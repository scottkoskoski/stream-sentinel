# Disaster Recovery Runbook

## RTO/RPO Targets

| Component | RPO (Recovery Point Objective) | RTO (Recovery Time Objective) | Data Criticality |
|-----------|------|------|------------------|
| Kafka | 0 (no data loss with replication) | 5 minutes | Critical -- message pipeline |
| Redis | 1 second (AOF appendfsync everysec) | 2 minutes | High -- user profiles, blocked users |
| PostgreSQL | 0 (WAL-based recovery) | 10 minutes | Critical -- fraud alerts, compliance |
| ClickHouse | 5 minutes (batch writes) | 15 minutes | Medium -- analytics only |

---

## Kafka Recovery

### Scenario: Single Broker Restart

**Impact:** Brief consumer rebalance. No data loss if replication factor >= 2 (production: RF=3).

```bash
# Check broker status
docker exec stream-sentinel-kafka kafka-broker-api-versions \
  --bootstrap-server localhost:9092

# Restart broker
docker compose -f docker/docker-compose.yml restart kafka

# Wait for broker to rejoin
sleep 30

# Verify broker is in-sync
docker exec stream-sentinel-kafka kafka-broker-api-versions \
  --bootstrap-server localhost:9092

# Check that all partitions have ISR (in-sync replicas)
docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --describe --topic synthetic-transactions
```

### Scenario: Kafka Data Loss (Volume Corruption)

**Impact:** Messages in the affected segments are lost. Consumers may need offset reset.

```bash
# Stop Kafka
docker compose -f docker/docker-compose.yml stop kafka

# Remove corrupted volume
docker volume rm docker_kafka-data

# Restart Kafka (will create fresh data directory)
docker compose -f docker/docker-compose.yml up -d kafka
sleep 60

# Recreate topics with proper configuration
docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --create --topic synthetic-transactions \
  --partitions 12 --replication-factor 1 \
  --config compression.type=lz4 \
  --config retention.ms=604800000

docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --create --topic fraud-alerts \
  --partitions 6 --replication-factor 1 \
  --config retention.ms=2592000000

docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --create --topic fraud-detection-results \
  --partitions 6 --replication-factor 1 \
  --config retention.ms=604800000

docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --create --topic blocked-transactions \
  --partitions 6 --replication-factor 1 \
  --config retention.ms=2592000000

docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --create --topic model-drift-alerts \
  --partitions 3 --replication-factor 1 \
  --config retention.ms=2592000000

docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --create --topic model-retraining-jobs \
  --partitions 3 --replication-factor 1 \
  --config retention.ms=2592000000

docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --create --topic dead-letter-queue \
  --partitions 3 --replication-factor 1 \
  --config retention.ms=2592000000

# Reset consumer group offsets to latest (skip the gap)
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group fraud-detection-group \
  --reset-offsets --to-latest \
  --topic synthetic-transactions \
  --execute

# Restart all consumers
# They will start from the latest offset after the reset
```

### Scenario: Zookeeper Failure

**Impact:** Kafka cannot elect leaders or manage metadata. Full pipeline halt.

```bash
# Check Zookeeper health
docker exec stream-sentinel-zookeeper bash -c \
  "echo ruok | nc localhost 2181"

# Restart Zookeeper
docker compose -f docker/docker-compose.yml restart zookeeper
sleep 15

# Restart Kafka (needs to reconnect to Zookeeper)
docker compose -f docker/docker-compose.yml restart kafka
sleep 30

# Verify full recovery
docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 --list
```

### Kafka Backup Strategy (Production)

```bash
# Periodic topic configuration backup
docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --describe > /backups/kafka/topic-config-$(date +%Y%m%d).txt

# Consumer group offset backup
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --all-groups --describe > /backups/kafka/offsets-$(date +%Y%m%d).txt
```

---

## Redis Recovery

### Scenario: Redis Restart (AOF Intact)

**Impact:** Brief unavailability (seconds). Data restored from AOF on startup.

```bash
# Redis is configured with appendonly=yes in docker-compose.yml
# AOF ensures durability on restart

docker compose -f docker/docker-compose.yml restart redis
sleep 5

# Verify data is intact
redis-cli -p 6379 ping
redis-cli -p 6379 DBSIZE
redis-cli -p 6379 SCARD blocked_users
redis-cli -p 6379 INFO keyspace
```

### Scenario: Redis Data Loss (Volume Corruption)

**Impact:** All user profiles, blocked users, drift baselines, and model registry entries are lost.

```bash
# Stop Redis
docker compose -f docker/docker-compose.yml stop redis

# Remove corrupted volume
docker volume rm docker_redis-data

# Restart Redis (empty state)
docker compose -f docker/docker-compose.yml up -d redis
sleep 5

redis-cli -p 6379 ping

# Restore blocked users from PostgreSQL
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel \
  -c "COPY (SELECT user_id FROM user_accounts WHERE status = 'BLOCKED') TO STDOUT;" \
  | while read user_id; do
    redis-cli -p 6379 SADD blocked_users "$user_id"
  done

# User profiles will be rebuilt as transactions are processed
# The fraud detector creates profiles on first encounter

# Drift baselines will recalibrate after check_interval (1000) scores

# Restart consumers to reconnect to fresh Redis
```

### Redis Backup Procedure

```bash
# Trigger RDB snapshot (non-blocking)
redis-cli -p 6379 BGSAVE

# Wait for completion
redis-cli -p 6379 LASTSAVE

# Copy the dump
docker cp stream-sentinel-redis:/data/dump.rdb /backups/redis/dump-$(date +%Y%m%d%H%M).rdb

# Also backup AOF
docker cp stream-sentinel-redis:/data/appendonly.aof /backups/redis/aof-$(date +%Y%m%d%H%M).aof
```

### Redis Restore from Backup

```bash
docker compose -f docker/docker-compose.yml stop redis

# Copy backup into the volume
docker cp /backups/redis/dump-YYYYMMDD.rdb stream-sentinel-redis:/data/dump.rdb
docker cp /backups/redis/aof-YYYYMMDD.aof stream-sentinel-redis:/data/appendonly.aof

docker compose -f docker/docker-compose.yml start redis
sleep 5
redis-cli -p 6379 DBSIZE
```

---

## PostgreSQL Recovery

### Scenario: PostgreSQL Restart

**Impact:** Brief unavailability. Data intact on persistent volume.

```bash
docker compose -f docker/docker-compose.yml restart postgres
sleep 15

docker exec stream-sentinel-postgres pg_isready -U stream_sentinel_user -d stream_sentinel

# Verify data
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel \
  -c "SELECT COUNT(*) FROM fraud_alerts;"
```

### Scenario: PostgreSQL Data Loss (Volume Corruption)

**Impact:** All fraud alerts, user accounts, model performance records, and audit logs are lost. This is a compliance-critical event.

```bash
# Stop PostgreSQL
docker compose -f docker/docker-compose.yml stop postgres

# Remove corrupted volume
docker volume rm docker_postgres-data

# Restart (init scripts will recreate schema)
docker compose -f docker/docker-compose.yml up -d postgres
sleep 20

# Verify schema was created
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel \
  -c "\dt"

# Expected tables: fraud_alerts, user_accounts, model_performance, system_audit_log
```

### PostgreSQL Backup Procedure

```bash
# Logical backup (pg_dump)
docker exec stream-sentinel-postgres pg_dump \
  -U stream_sentinel_user -d stream_sentinel \
  --format=custom \
  --file=/tmp/stream_sentinel_backup.dump

docker cp stream-sentinel-postgres:/tmp/stream_sentinel_backup.dump \
  /backups/postgresql/stream_sentinel-$(date +%Y%m%d%H%M).dump

# Verify backup integrity
docker exec stream-sentinel-postgres pg_restore \
  --list /tmp/stream_sentinel_backup.dump | head -20
```

### PostgreSQL Restore from Backup

```bash
docker compose -f docker/docker-compose.yml stop postgres
docker volume rm docker_postgres-data
docker compose -f docker/docker-compose.yml up -d postgres
sleep 20

# Restore from backup
docker cp /backups/postgresql/stream_sentinel-YYYYMMDD.dump \
  stream-sentinel-postgres:/tmp/restore.dump

docker exec stream-sentinel-postgres pg_restore \
  -U stream_sentinel_user -d stream_sentinel \
  --clean --if-exists \
  /tmp/restore.dump

# Verify
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel \
  -c "SELECT COUNT(*) FROM fraud_alerts;"
```

### PostgreSQL Point-in-Time Recovery (Production)

For production, enable WAL archiving by adding to the postgres command in docker-compose.yml:

```
-c archive_mode=on
-c archive_command='cp %p /var/lib/postgresql/wal_archive/%f'
-c wal_level=replica
```

---

## ClickHouse Recovery

### Scenario: ClickHouse Restart

**Impact:** Brief unavailability of analytics. Core fraud detection is unaffected.

```bash
docker compose -f docker/docker-compose.yml restart clickhouse
sleep 15
curl -s "http://localhost:8123/ping"
```

### Scenario: ClickHouse Data Loss (Volume Corruption)

**Impact:** All analytics data, materialized views, and performance metrics history are lost. No compliance impact (PostgreSQL is the system of record for alerts).

```bash
# Stop ClickHouse
docker compose -f docker/docker-compose.yml stop clickhouse

# Remove corrupted volumes
docker volume rm docker_clickhouse-data docker_clickhouse-logs

# Restart (init scripts will recreate schema)
docker compose -f docker/docker-compose.yml up -d clickhouse
sleep 20

# Verify schema
curl -s "http://localhost:8123/?query=SHOW+TABLES+FROM+stream_sentinel"

# Expected: transaction_records, fraud_features, detection_results,
#           performance_metrics, fraud_rate_hourly, user_activity_daily,
#           model_accuracy_hourly
```

### ClickHouse Backup Procedure

```bash
# Create backup using clickhouse-backup or native commands
curl -s "http://localhost:8123/?query=SELECT+count()+FROM+stream_sentinel.transaction_records"

# Export critical tables
for table in transaction_records detection_results fraud_features; do
  curl -s "http://localhost:8123/?query=SELECT+*+FROM+stream_sentinel.${table}+FORMAT+Native" \
    > /backups/clickhouse/${table}-$(date +%Y%m%d).native
done
```

---

## Full System Recovery (Complete Infrastructure Rebuild)

Use this procedure when the entire Docker environment needs to be rebuilt from scratch.

```bash
# 1. Stop everything
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.monitoring.yml down

# 2. Remove all volumes (WARNING: this destroys all data)
docker volume rm docker_zookeeper-data docker_zookeeper-logs \
  docker_kafka-data docker_redis-data docker_redis-insight-data \
  docker_postgres-data docker_clickhouse-data docker_clickhouse-logs \
  docker_prometheus-data docker_grafana-data

# 3. Rebuild infrastructure
docker compose -f docker/docker-compose.yml up -d
sleep 60  # Wait for all services to initialize

# 4. Verify all services
docker exec stream-sentinel-kafka kafka-topics --bootstrap-server localhost:9092 --list
redis-cli -p 6379 ping
docker exec stream-sentinel-postgres pg_isready -U stream_sentinel_user -d stream_sentinel
curl -s "http://localhost:8123/ping"

# 5. Create Kafka topics (12 partitions for transactions, 6 for alerts/results, 3 for others)
docker exec stream-sentinel-kafka kafka-topics --bootstrap-server localhost:9092 \
  --create --topic synthetic-transactions --partitions 12 --replication-factor 1 \
  --config compression.type=lz4 --config retention.ms=604800000

docker exec stream-sentinel-kafka kafka-topics --bootstrap-server localhost:9092 \
  --create --topic fraud-alerts --partitions 6 --replication-factor 1 \
  --config retention.ms=2592000000

docker exec stream-sentinel-kafka kafka-topics --bootstrap-server localhost:9092 \
  --create --topic fraud-detection-results --partitions 6 --replication-factor 1 \
  --config retention.ms=604800000

docker exec stream-sentinel-kafka kafka-topics --bootstrap-server localhost:9092 \
  --create --topic blocked-transactions --partitions 6 --replication-factor 1 \
  --config retention.ms=2592000000

docker exec stream-sentinel-kafka kafka-topics --bootstrap-server localhost:9092 \
  --create --topic model-drift-alerts --partitions 3 --replication-factor 1 \
  --config retention.ms=2592000000

docker exec stream-sentinel-kafka kafka-topics --bootstrap-server localhost:9092 \
  --create --topic model-retraining-jobs --partitions 3 --replication-factor 1 \
  --config retention.ms=2592000000

docker exec stream-sentinel-kafka kafka-topics --bootstrap-server localhost:9092 \
  --create --topic dead-letter-queue --partitions 3 --replication-factor 1 \
  --config retention.ms=2592000000

# 6. Start monitoring stack
docker compose -f docker/docker-compose.monitoring.yml up -d

# 7. Restore from backups if available (see component-specific restore sections above)

# 8. Start consumers
python src/consumers/fraud_detector.py &
python src/consumers/alert_processor.py &
python src/consumers/persistence_consumer.py &
python src/consumers/dlq_consumer.py &

# 9. Start producer
python src/producers/synthetic_transaction_producer.py &

# 10. Verify end-to-end flow
sleep 30
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 --describe --group fraud-detection-group
curl -s http://localhost:8000/metrics | grep 'transactions_processed_total'
```

---

## Backup Schedule (Production Recommendation)

| Component | Backup Type | Frequency | Retention |
|-----------|------------|-----------|-----------|
| PostgreSQL | pg_dump (logical) | Every 6 hours | 30 days |
| PostgreSQL | WAL archiving | Continuous | 7 days |
| Redis | RDB snapshot | Every 1 hour | 7 days |
| Redis | AOF | Continuous (built-in) | Until compaction |
| ClickHouse | Native export | Daily | 14 days |
| Kafka | Topic config + offsets | Daily | 30 days |
| Model files | File copy | On each deploy | Indefinite |
