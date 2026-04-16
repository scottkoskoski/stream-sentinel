# Scaling Runbook

## Horizontal Scaling: Fraud Detection Consumers

### How Kafka Consumer Scaling Works

Kafka partitions are the unit of parallelism. The `synthetic-transactions` topic has 12 partitions. Each consumer instance in the same consumer group (`fraud-detection-group`) is assigned a subset of partitions. Maximum useful consumer instances = number of partitions.

**Current capacity per instance:** ~2,500-3,000 TPS (single-message mode), ~5,000-8,000 TPS (batch mode)

### Scale Up

```bash
# Launch additional consumer instances (same consumer group)
# Instance 2
python src/consumers/fraud_detector.py &

# Instance 3
python src/consumers/fraud_detector.py &

# Instance 4 (batch mode for higher throughput)
python src/consumers/fraud_detector.py --batch --batch-size 64 --batch-timeout-ms 50 &

# Verify partition assignment
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group fraud-detection-group
```

Each new instance triggers a consumer group rebalance. During rebalance (typically 5-10 seconds), consumption pauses briefly.

### Scale Down

```bash
# Send SIGTERM to the instance to shut down gracefully
kill -TERM <pid>

# The consumer will commit offsets and leave the group
# Remaining consumers will pick up the orphaned partitions
```

### Scaling Limits

| Instances | Partitions Each | Approx TPS (single) | Approx TPS (batch) |
|-----------|----------------|---------------------|---------------------|
| 1 | 12 | 3,000 | 8,000 |
| 2 | 6 | 6,000 | 16,000 |
| 3 | 4 | 9,000 | 24,000 |
| 4 | 3 | 12,000 | 32,000 |
| 6 | 2 | 18,000 | 48,000 |
| 12 | 1 | 36,000 | 96,000 |
| >12 | idle | No benefit | No benefit |

To scale beyond 12 instances, increase topic partitions first.

---

## Horizontal Scaling: Alert Processor

The `fraud-alerts` topic has 6 partitions. Maximum useful instances = 6.

```bash
# Scale to 2 instances
python src/consumers/alert_processor.py &
python src/consumers/alert_processor.py &

# Verify
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group alert-processor-group
```

---

## Horizontal Scaling: Persistence Consumer

The `fraud-detection-results` topic has 6 partitions. Maximum useful instances = 6.

```bash
python src/consumers/persistence_consumer.py &

# Verify
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group stream-sentinel-persistence
```

---

## Increasing Kafka Partitions

When consumer scaling hits the partition ceiling, increase partition count. This is a non-destructive, online operation.

```bash
# Increase synthetic-transactions from 12 to 24 partitions
docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --alter --topic synthetic-transactions \
  --partitions 24

# Increase fraud-alerts from 6 to 12
docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --alter --topic fraud-alerts \
  --partitions 12

# Verify
docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --describe --topic synthetic-transactions

# Note: Existing data in old partitions stays there.
# New messages will be distributed across all partitions.
# Consumer rebalance will happen automatically.
```

**Warning:** Increasing partitions is irreversible. Reducing partitions requires recreating the topic.

---

## Vertical Scaling: Kafka

### Memory

Kafka JVM heap is the primary tunable. Adjust in docker-compose.yml:

```yaml
kafka:
  environment:
    KAFKA_HEAP_OPTS: "-Xmx2g -Xms2g"  # default is usually 1g
```

### Disk

Kafka is I/O intensive. For production:
- Use SSD/NVMe for `kafka-data` volume
- Monitor disk usage: `docker exec stream-sentinel-kafka df -h /var/lib/kafka/data`
- Retention is configured per-topic (7 days for transactions, 30 days for alerts)

### Network

Kafka uses LZ4 compression (configured in docker-compose.yml: `KAFKA_COMPRESSION_TYPE: 'lz4'`). This reduces network bandwidth at the cost of CPU.

Estimated bandwidth per 10k TPS:
- Uncompressed: ~50 MB/s
- LZ4 compressed: ~15-20 MB/s

---

## Vertical Scaling: Redis

### Memory

Redis `maxmemory` is configured to 512MB in docker-compose.yml with `allkeys-lru` eviction.

```bash
# Check current memory usage
redis-cli -p 6379 INFO memory | grep -E "used_memory_human|maxmemory_human"

# To increase, modify docker-compose.yml command:
# redis-server --appendonly yes --maxmemory 1gb --maxmemory-policy allkeys-lru
```

**Memory estimation formula:**
```
User profiles: ~500 bytes/user * num_active_users
Blocked users set: ~50 bytes/user * num_blocked_users
Drift baseline: ~1 KB
Model registry: ~100 KB per model version

Example: 1M active users = ~500 MB for profiles alone
```

### Connections

Each consumer instance creates 1 Redis connection. Default max connections = 10,000.

```bash
redis-cli -p 6379 INFO clients | grep connected_clients
redis-cli -p 6379 CONFIG GET maxclients
```

---

## Vertical Scaling: PostgreSQL

### Connection Pool

PostgreSQL is configured with `max_connections=200` in docker-compose.yml.

```bash
# Check active connections
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel \
  -c "SELECT count(*) FROM pg_stat_activity WHERE state = 'active';"
```

### Memory

Key parameters from docker-compose.yml:
- `shared_buffers=256MB`
- `effective_cache_size=1GB`
- `maintenance_work_mem=64MB`

To increase, modify the postgres command in docker-compose.yml:
```
-c shared_buffers=1GB
-c effective_cache_size=4GB
-c maintenance_work_mem=256MB
-c work_mem=16MB
```

### Disk

```bash
# Check database size
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel \
  -c "SELECT pg_size_pretty(pg_database_size('stream_sentinel'));"

# Check table sizes
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel \
  -c "SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
      FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC;"
```

---

## Vertical Scaling: ClickHouse

### Memory

ClickHouse is configured with default memory settings. For heavy analytics workloads:

```bash
# Check current memory usage
curl -s "http://localhost:8123/?query=SELECT+formatReadableSize(sum(memory_usage))+FROM+system.processes"

# Increase max memory for queries
# Add to ClickHouse config or set per-query:
curl -s "http://localhost:8123/?max_memory_usage=10000000000&query=SELECT+..."
```

### Disk

Transaction records use MergeTree with TTL (2 years for transactions, 1 year for features, 6 months for metrics).

```bash
# Check table sizes
curl -s "http://localhost:8123/?query=SELECT+table,+formatReadableSize(sum(bytes_on_disk))+FROM+system.parts+WHERE+database='stream_sentinel'+GROUP+BY+table+ORDER+BY+sum(bytes_on_disk)+DESC"
```

---

## Scaling Decision Matrix

| Symptom | Metric | First Action | Second Action |
|---------|--------|-------------|---------------|
| Consumer lag growing | `kafka_consumer_lag_messages > 50000` | Add consumer instance | Switch to batch mode |
| P99 latency > 100ms | `fraud_detection_duration_seconds` P99 | Check Redis latency | Add consumer instance |
| Redis OOM | `memory_usage_bytes` near maxmemory | Increase maxmemory | Add Redis cluster |
| PostgreSQL slow queries | Connection count near max | Increase max_connections | Add read replicas |
| Kafka disk full | Disk utilization > 80% | Reduce retention | Add storage volume |
| Consumer idle (>12 instances) | Some consumers have 0 partitions | Increase topic partitions | Remove idle instances |

---

## Capacity Planning Formula

### Throughput

```
Required consumer instances = ceil(target_TPS / per_instance_TPS)

Where per_instance_TPS:
  - Single-message mode: ~3,000 TPS
  - Batch mode (batch_size=32): ~8,000 TPS
  - Batch mode (batch_size=64): ~12,000 TPS

Required partitions = max(required_consumer_instances, current_partitions)
```

### Storage

```
Kafka daily storage = TPS * 86400 * avg_message_size_bytes * replication_factor / compression_ratio
  Example: 10000 * 86400 * 1024 * 1 / 3 = ~296 GB/day (uncompressed)
  With LZ4: ~99 GB/day
  With 7-day retention: ~693 GB

PostgreSQL growth = alerts_per_day * avg_row_size
  Example: 500 alerts/day * 2 KB = 1 MB/day, 365 MB/year

ClickHouse growth = TPS * 86400 * avg_row_size
  Example: 10000 * 86400 * 512 bytes = ~442 GB/day
  With compression: ~88 GB/day (ClickHouse typically 5:1 compression)
  With 2-year TTL: ~64 TB

Redis memory = active_users * profile_size + blocked_users * 50B + overhead
  Example: 100000 * 500B + 1000 * 50B + 10MB = ~60 MB
```
