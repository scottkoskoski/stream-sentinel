# Capacity Planning Runbook

## Current System Profile

| Parameter | Current Value | Source |
|-----------|--------------|--------|
| Target TPS | 10,000+ sustained | CLAUDE.md |
| Latency target | <100ms P99 | CLAUDE.md |
| Input topic partitions | 12 | src/kafka/config.py |
| Alert topic partitions | 6 | src/kafka/config.py |
| Kafka retention (transactions) | 7 days | Topic config |
| Kafka retention (alerts) | 30 days | Topic config |
| Redis maxmemory | 512 MB | docker-compose.yml |
| PostgreSQL max_connections | 200 | docker-compose.yml |
| PostgreSQL shared_buffers | 256 MB | docker-compose.yml |
| ClickHouse TTL (transactions) | 2 years | init script |
| ClickHouse TTL (features) | 1 year | init script |
| ClickHouse TTL (metrics) | 6 months | init script |
| ML model features | 200 | CLAUDE.md |
| Fraud threshold | 0.5 (default) | fraud_detector.py |

---

## Resource Estimation Formulas

### Kafka Storage

```
Daily Kafka storage (GB) = TPS * 86400 * avg_msg_bytes / 1e9 / compression_ratio * replication_factor

Variables:
  TPS = transactions per second
  avg_msg_bytes = average message size (typically 1-2 KB for transaction messages)
  compression_ratio = LZ4 typically achieves 2-3x
  replication_factor = 1 (dev), 3 (production)
```

**Example calculations:**

| TPS | Avg Msg | Compression | RF | Daily (GB) | 7-day (GB) | 30-day (GB) |
|-----|---------|-------------|-----|-----------|------------|-------------|
| 10,000 | 1 KB | 3x | 1 | 288 | 2,016 | N/A |
| 10,000 | 1 KB | 3x | 3 | 864 | 6,048 | N/A |
| 50,000 | 1 KB | 3x | 3 | 4,320 | 30,240 | N/A |

Add ~15% for topic overhead (indexes, segment metadata).

For all topics combined, multiply by ~1.3 (alerts, results, DLQ are lower volume).

### Redis Memory

```
Redis memory (MB) = user_profiles + blocked_users + drift_data + model_registry + overhead

Where:
  user_profiles = active_users * 500 bytes
  blocked_users = blocked_count * 50 bytes
  drift_data = 1 KB (baseline) + check_interval * 8 bytes (score buffer)
  model_registry = num_versions * 100 KB
  overhead = 20% of above (Redis data structure overhead)
```

**Example calculations:**

| Active Users | Blocked | Model Versions | Estimated Memory |
|-------------|---------|----------------|-----------------|
| 10,000 | 100 | 3 | ~7 MB |
| 100,000 | 1,000 | 5 | ~62 MB |
| 1,000,000 | 10,000 | 10 | ~610 MB |
| 10,000,000 | 100,000 | 10 | ~6 GB |

**Current limit:** 512 MB with allkeys-lru eviction. Sufficient for up to ~800K active users.

### PostgreSQL Storage

```
PostgreSQL daily growth (MB) = fraud_alerts + user_accounts + audit_log

Where:
  fraud_alerts = alerts_per_day * avg_row_size (estimated 2 KB per alert)
  user_accounts = new_users_per_day * 200 bytes (mostly updates, not inserts)
  audit_log = events_per_day * 500 bytes
```

**Example calculations (assuming 1% fraud rate):**

| TPS | Fraud Rate | Daily Alerts | Daily PG Growth | Annual Growth |
|-----|-----------|-------------|-----------------|---------------|
| 10,000 | 1% | 8,640 | ~17 MB | ~6.2 GB |
| 10,000 | 5% | 43,200 | ~85 MB | ~31 GB |
| 50,000 | 1% | 43,200 | ~85 MB | ~31 GB |
| 50,000 | 5% | 216,000 | ~425 MB | ~155 GB |

### ClickHouse Storage

```
ClickHouse daily growth (GB) = transaction_records + detection_results + fraud_features + performance_metrics

Where:
  transaction_records = TPS * 86400 * avg_row_bytes / 1e9 / clickhouse_compression
  detection_results = TPS * 86400 * avg_row_bytes / 1e9 / clickhouse_compression
  fraud_features = TPS * 86400 * num_features_stored * feature_row_bytes / 1e9 / clickhouse_compression
  clickhouse_compression = typically 5-10x for columnar data
```

**Example calculations:**

| TPS | Compression | Daily Raw | Daily Compressed | Annual |
|-----|-------------|----------|-----------------|--------|
| 10,000 | 5x | 10 GB | 2 GB | 730 GB |
| 10,000 | 10x | 10 GB | 1 GB | 365 GB |
| 50,000 | 5x | 50 GB | 10 GB | 3.6 TB |
| 50,000 | 10x | 50 GB | 5 GB | 1.8 TB |

ClickHouse TTLs enforce automatic cleanup: 2 years for transactions, 1 year for features, 6 months for metrics.

### Consumer CPU and Memory

```
Consumer instances = ceil(target_TPS / per_instance_TPS)

Per-instance resources:
  CPU: 0.5-1.0 cores (single-message), 1.0-2.0 cores (batch mode)
  Memory: 500 MB - 1 GB (includes model in memory, ~200 MB for XGBoost with 200 features)

Total consumer resources:
  CPU cores = instances * cores_per_instance
  Memory GB = instances * memory_per_instance
```

**Example calculations:**

| Target TPS | Mode | Instances | CPU Cores | Memory |
|-----------|------|-----------|-----------|--------|
| 10,000 | single | 4 | 4 | 4 GB |
| 10,000 | batch | 2 | 4 | 2 GB |
| 50,000 | batch | 7 | 14 | 7 GB |
| 100,000 | batch | 13 | 26 | 13 GB |

---

## Scaling Thresholds

These thresholds should trigger scaling actions when breached.

### Immediate Scaling Required

| Metric | Threshold | Action |
|--------|-----------|--------|
| `kafka_consumer_lag_messages` | > 100,000 for 5 min | Add consumer instances or switch to batch mode |
| `fraud_detection_duration_seconds` P99 | > 200ms for 5 min | Add consumer instances |
| Redis memory usage | > 80% of maxmemory (410 MB) | Increase maxmemory |
| PostgreSQL connections | > 160 (80% of 200) | Increase max_connections |
| Kafka disk usage | > 80% | Add disk or reduce retention |
| CPU usage on consumer host | > 80% sustained | Add consumer instances on new hosts |

### Plan Scaling Within 1 Week

| Metric | Threshold | Action |
|--------|-----------|--------|
| `kafka_consumer_lag_messages` | > 50,000 sustained | Evaluate horizontal scaling |
| `fraud_detection_duration_seconds` P99 | > 100ms sustained | Profile bottleneck, optimize |
| Redis memory usage | > 60% of maxmemory | Plan maxmemory increase |
| PostgreSQL size | > 50% of disk | Plan disk expansion |
| ClickHouse size | > 60% of disk | Review TTL settings or add storage |
| Topic partitions | All partitions assigned to consumers | Plan partition increase |

### Monitor Trend

| Metric | Check Frequency | Growth Concern |
|--------|----------------|----------------|
| Kafka storage | Weekly | > 5% week-over-week growth |
| PostgreSQL size | Weekly | Approaching disk limit in < 3 months |
| ClickHouse size | Weekly | Approaching disk limit in < 6 months |
| Redis key count | Weekly | > 10% week-over-week growth |
| Transaction volume | Daily | > 20% increase from baseline |

---

## Cost Projection Framework

### Infrastructure Sizing by TPS Tier

#### Tier 1: 10,000 TPS (Current Target)

| Component | Specification | Monthly Cost Estimate (Cloud) |
|-----------|-------------|-------------------------------|
| Kafka | 3 brokers, 2 CPU / 8 GB RAM / 500 GB SSD each | $450 |
| Redis | 1 instance, 1 CPU / 1 GB RAM | $30 |
| PostgreSQL | 1 instance, 2 CPU / 8 GB RAM / 100 GB SSD | $150 |
| ClickHouse | 1 instance, 4 CPU / 16 GB RAM / 1 TB SSD | $300 |
| Consumer hosts | 2 instances, 2 CPU / 4 GB RAM each | $200 |
| Monitoring | 1 instance, 2 CPU / 4 GB RAM / 50 GB SSD | $100 |
| **Total** | | **~$1,230/month** |

#### Tier 2: 50,000 TPS

| Component | Specification | Monthly Cost Estimate (Cloud) |
|-----------|-------------|-------------------------------|
| Kafka | 5 brokers, 4 CPU / 16 GB RAM / 2 TB SSD each | $2,000 |
| Redis | 1 instance, 2 CPU / 4 GB RAM (or Redis Cluster) | $120 |
| PostgreSQL | 1 primary + 1 replica, 4 CPU / 16 GB RAM each | $600 |
| ClickHouse | 2 instances (sharded), 8 CPU / 32 GB RAM each | $1,200 |
| Consumer hosts | 4 instances, 4 CPU / 8 GB RAM each | $800 |
| Monitoring | 1 instance, 4 CPU / 8 GB RAM / 200 GB SSD | $250 |
| **Total** | | **~$4,970/month** |

#### Tier 3: 100,000 TPS

| Component | Specification | Monthly Cost Estimate (Cloud) |
|-----------|-------------|-------------------------------|
| Kafka | 7 brokers, 8 CPU / 32 GB RAM / 4 TB NVMe each | $5,600 |
| Redis | Redis Cluster (3 primaries + 3 replicas) | $540 |
| PostgreSQL | 1 primary + 2 replicas, 8 CPU / 32 GB RAM each | $1,800 |
| ClickHouse | 4 instances (sharded + replicated), 16 CPU / 64 GB each | $5,000 |
| Consumer hosts | 7 instances, 4 CPU / 8 GB RAM each | $1,400 |
| Monitoring | 2 instances (HA), 4 CPU / 16 GB RAM each | $600 |
| **Total** | | **~$14,940/month** |

---

## Growth Planning Checklist

### Quarterly Review

1. **Measure current utilization:**
   ```bash
   # Kafka disk
   docker exec stream-sentinel-kafka df -h /var/lib/kafka/data

   # Redis memory
   redis-cli -p 6379 INFO memory | grep used_memory_human

   # PostgreSQL size
   docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel \
     -c "SELECT pg_size_pretty(pg_database_size('stream_sentinel'));"

   # ClickHouse size
   curl -s "http://localhost:8123/?query=SELECT+formatReadableSize(sum(bytes_on_disk))+FROM+system.parts+WHERE+database='stream_sentinel'"

   # Average TPS over the past week
   # Query Prometheus for avg rate of transactions_processed_total
   curl -s "http://localhost:9090/api/v1/query?query=avg_over_time(rate(transactions_processed_total[1h])[7d:])" | python3 -m json.tool
   ```

2. **Calculate runway:**
   - Time until Kafka disk is full at current growth rate
   - Time until Redis maxmemory is exhausted
   - Time until PostgreSQL disk is full
   - Time until ClickHouse TTL-adjusted storage is full

3. **Plan scaling actions** if any runway is < 3 months

4. **Review partition counts** -- are all consumer instances utilized, or are some idle?

5. **Review cost efficiency** -- is batch mode being used where throughput matters more than latency?
