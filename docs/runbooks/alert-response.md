# Alert Response Runbook

This runbook covers diagnosis and remediation for all alerts in the Stream Sentinel monitoring stack. Since alert_rules.yml has not yet been created, this runbook defines the recommended alerts based on the actual metrics exposed by the system, and provides response procedures for each.

---

## Recommended Alert Definitions

The alerts below should be defined in `docker/prometheus/alert_rules.yml` and loaded by Prometheus. Each section includes the recommended rule, followed by the full Problem / Diagnosis / Fix / Verify procedure.

---

## Alert: FraudDetectorDown

**Recommended rule:**
```yaml
- alert: FraudDetectorDown
  expr: up{job="fraud-detector"} == 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Fraud detector is unreachable"
```

**Severity:** SEV1

### Problem
The fraud detector consumer is not responding to Prometheus scrapes. No transactions are being scored.

### Diagnosis

```bash
# Check if the process is running
ps aux | grep fraud_detector.py

# Check the metrics endpoint directly
curl -s --max-time 3 http://localhost:8000/metrics || echo "UNREACHABLE"

# Check recent logs
journalctl -u fraud-detector --since "10 minutes ago" --no-pager | tail -50

# Or if running in a terminal / screen session, check the output directly
# Check for OOM kills
dmesg | grep -i "oom\|killed" | tail -10

# Check system resources
free -h
df -h /
```

### Fix

```bash
# Restart the fraud detector (single-message mode)
python src/consumers/fraud_detector.py &

# Or restart in batch mode for higher throughput
python src/consumers/fraud_detector.py --batch --batch-size 32 --batch-timeout-ms 100 &

# If OOM was the cause, check memory and increase limits before restart
ulimit -v  # check virtual memory limit
```

### Verify

```bash
# Confirm the metrics endpoint is responding
curl -s http://localhost:8000/metrics | head -5

# Check that messages are being consumed
curl -s http://localhost:8000/metrics | grep 'transactions_processed_total'

# Check consumer group lag is decreasing
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group fraud-detection-group
```

---

## Alert: AlertProcessorDown

**Recommended rule:**
```yaml
- alert: AlertProcessorDown
  expr: up{job="alert-processor"} == 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Alert processor is unreachable"
```

**Severity:** SEV2

### Problem
The alert processor is not running. Fraud alerts from `fraud-alerts` topic are not being classified, and user blocking is not happening.

### Diagnosis

```bash
ps aux | grep alert_processor.py
curl -s --max-time 3 http://localhost:8001/metrics || echo "UNREACHABLE"

# Check lag on fraud-alerts topic
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group alert-processor-group
```

### Fix

```bash
python src/consumers/alert_processor.py &
```

### Verify

```bash
curl -s http://localhost:8001/metrics | grep 'alerts_processed_total'

docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group alert-processor-group
```

---

## Alert: PersistenceConsumerDown

**Recommended rule:**
```yaml
- alert: PersistenceConsumerDown
  expr: up{job="persistence-consumer"} == 0
  for: 5m
  labels:
    severity: high
  annotations:
    summary: "Persistence consumer is unreachable"
```

**Severity:** SEV2

### Problem
Detection results are not being persisted to PostgreSQL/ClickHouse. The `fraud-detection-results` topic will accumulate unprocessed messages. Core fraud detection continues working, but audit trail and analytics are impacted.

### Diagnosis

```bash
ps aux | grep persistence_consumer.py
curl -s --max-time 3 http://localhost:8002/metrics || echo "UNREACHABLE"

# Check if databases are reachable
docker exec stream-sentinel-postgres pg_isready -U stream_sentinel_user -d stream_sentinel
curl -s "http://localhost:8123/ping"

# Check lag
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group stream-sentinel-persistence
```

### Fix

```bash
# If databases are healthy, restart the consumer
python src/consumers/persistence_consumer.py &

# If PostgreSQL is down, see Disaster Recovery runbook
# If ClickHouse is down, see Disaster Recovery runbook
```

### Verify

```bash
curl -s http://localhost:8002/metrics | grep 'transactions_processed_total'

# Verify data is flowing to PostgreSQL
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel \
  -c "SELECT COUNT(*) FROM fraud_alerts WHERE created_at > NOW() - INTERVAL '5 minutes';"

# Verify data is flowing to ClickHouse
curl -s "http://localhost:8123/?query=SELECT+count()+FROM+stream_sentinel.transaction_records+WHERE+timestamp+>+now()-300"
```

---

## Alert: HighConsumerLag

**Recommended rule:**
```yaml
- alert: HighConsumerLag
  expr: kafka_consumer_lag_messages > 50000
  for: 5m
  labels:
    severity: high
  annotations:
    summary: "Kafka consumer lag exceeds 50,000 messages"
```

**Severity:** SEV2

### Problem
Consumer is falling behind the producer rate. Messages are queuing in Kafka. If this continues, the scoring pipeline will have significant latency, and Kafka retention may cause data loss.

### Diagnosis

```bash
# Check lag per partition
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group fraud-detection-group

# Check consumer throughput from metrics
curl -s http://localhost:8000/metrics | grep 'kafka_messages_consumed_total'

# Check if processing latency has increased
curl -s http://localhost:8000/metrics | grep 'fraud_detection_duration_seconds'

# Check producer rate
docker exec stream-sentinel-kafka kafka-run-class.sh kafka.tools.GetOffsetShell \
  --broker-list localhost:9092 --topic synthetic-transactions --time -1

# Check system resources on the consumer host
top -b -n 1 | head -20
free -h
```

### Fix

```bash
# Option 1: Scale horizontally -- launch additional consumer instances
# Each instance in the same consumer group will take some partitions
python src/consumers/fraud_detector.py &  # instance 2
python src/consumers/fraud_detector.py &  # instance 3

# Option 2: Switch to batch mode for higher throughput
# Kill existing consumer first, then restart in batch mode
python src/consumers/fraud_detector.py --batch --batch-size 64 --batch-timeout-ms 50 &

# Option 3: Temporarily reduce producer rate if lag is from a burst
# Stop the synthetic transaction producer or reduce its rate in src/producers/config.py
```

### Verify

```bash
# Watch lag decreasing over time
watch -n 10 "docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group fraud-detection-group 2>/dev/null | tail -15"
```

---

## Alert: HighFraudDetectionLatency

**Recommended rule:**
```yaml
- alert: HighFraudDetectionLatency
  expr: histogram_quantile(0.99, rate(fraud_detection_duration_seconds_bucket[5m])) > 0.1
  for: 5m
  labels:
    severity: high
  annotations:
    summary: "Fraud detection P99 latency exceeds 100ms"
```

**Severity:** SEV2

### Problem
End-to-end fraud detection latency has exceeded the 100ms P99 target. Transactions are being scored but too slowly.

### Diagnosis

```bash
# Check overall latency
curl -s http://localhost:8000/metrics | grep 'fraud_detection_duration_seconds'

# Check which stage is slow
curl -s http://localhost:8000/metrics | grep 'feature_extraction_duration_seconds'
curl -s http://localhost:8000/metrics | grep 'model_inference_duration_seconds'
curl -s http://localhost:8000/metrics | grep 'redis_operation_duration_seconds'

# Check Redis latency
redis-cli -p 6379 --latency -c 100

# Check system resources
top -b -n 1 | head -20
iostat -x 1 3
```

### Fix

```bash
# If Redis is slow:
# Check Redis memory usage
redis-cli -p 6379 INFO memory | grep used_memory_human
# If near max (512mb configured), consider increasing maxmemory in docker-compose.yml

# If model inference is slow:
# Enable batch mode for amortized inference cost
python src/consumers/fraud_detector.py --batch --batch-size 32 --batch-timeout-ms 100 &

# If feature extraction is slow:
# Check if Redis profile lookups are timing out
redis-cli -p 6379 SLOWLOG GET 10

# If CPU-bound:
# Scale horizontally with additional consumer instances
python src/consumers/fraud_detector.py &
```

### Verify

```bash
# Monitor P99 latency over time
watch -n 10 "curl -s http://localhost:8000/metrics | grep 'fraud_detection_duration_seconds_bucket' | tail -5"
```

---

## Alert: ModelScoringDegraded

**Recommended rule:**
```yaml
- alert: ModelScoringDegraded
  expr: model_status_info{status="rules_fallback"} == 1
  for: 1m
  labels:
    severity: high
  annotations:
    summary: "Fraud detector using rules-based fallback instead of ML model"
```

**Severity:** SEV2

### Problem
The ML model failed to load or is unavailable. The fraud detector is operating in rules-based fallback mode, which has lower accuracy than the production XGBoost model (99.42% AUC).

### Diagnosis

```bash
# Check model status metric
curl -s http://localhost:8000/metrics | grep 'model_status_info'

# Check if the model file exists
ls -la models/synthetic_fraud_model_production.pkl

# Check model file integrity
python -c "
import pickle
with open('models/synthetic_fraud_model_production.pkl', 'rb') as f:
    data = pickle.load(f)
print('Model type:', type(data))
if isinstance(data, dict):
    print('Keys:', list(data.keys()))
    print('Model:', type(data.get('model')))
"

# Check Redis model registry
redis-cli -p 6379 -n 4 KEYS "model_registry:*"

# Check fraud detector logs for model loading errors
journalctl -u fraud-detector --since "30 minutes ago" | grep -i "model\|fallback\|error"
```

### Fix

```bash
# Option 1: Restart the fraud detector (will retry model load)
# Kill existing instance and restart
python src/consumers/fraud_detector.py &

# Option 2: If model file is corrupt, restore from backup
cp models/synthetic_fraud_model_production.pkl.bak models/synthetic_fraud_model_production.pkl

# Option 3: If Redis model registry is the issue, clear and restart
redis-cli -p 6379 -n 4 DEL "model_registry:active_model:production"
python src/consumers/fraud_detector.py &
```

### Verify

```bash
# Confirm model status is ml_primary
curl -s http://localhost:8000/metrics | grep 'model_status_info'
# Expected: model_status_info{status="ml_primary"} 1.0

# Confirm ML inference is happening
curl -s http://localhost:8000/metrics | grep 'model_inference_duration_seconds_count'
```

---

## Alert: ModelDriftDetected

**Recommended rule:**
```yaml
- alert: ModelDriftDetected
  expr: fraud_model_drift_psi > 0.15
  for: 0m
  labels:
    severity: warning
  annotations:
    summary: "Model drift detected (PSI > 0.15)"

- alert: ModelDriftCritical
  expr: fraud_model_drift_psi > 0.5
  for: 0m
  labels:
    severity: critical
  annotations:
    summary: "Critical model drift (PSI > 0.5)"
```

**Severity:** SEV3 (medium drift, PSI 0.15-0.25), SEV2 (high drift, PSI 0.25-0.5), SEV1 (critical drift, PSI > 0.5)

### Problem
The fraud score distribution has shifted significantly from the baseline. The model may be making inaccurate predictions due to changes in input data patterns.

### Diagnosis

```bash
# Check current PSI value
curl -s http://localhost:8000/metrics | grep 'fraud_model_drift_psi'

# Check drift alerts topic for details
docker exec stream-sentinel-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic model-drift-alerts \
  --from-beginning --max-messages 5 --timeout-ms 5000

# Check fraud score distribution
curl -s http://localhost:8000/metrics | grep 'fraud_score_distribution'

# Check if input data patterns have changed
curl -s http://localhost:8000/metrics | grep 'fraud_predictions_total'

# Check Redis baseline
redis-cli -p 6379 -n 4 GET "drift_monitor:baseline"

# Check false positive rate
curl -s http://localhost:8000/metrics | grep 'false_positive_rate'
```

### Fix

```bash
# For medium drift (PSI 0.15-0.25): Monitor closely, check if transient
# The retraining trigger will evaluate whether retraining is warranted

# For high/critical drift:
# Option 1: Trigger manual retraining evaluation
python -m src.ml.online_learning.retraining_trigger &

# Option 2: Reset the baseline if the drift is from a legitimate data shift
redis-cli -p 6379 -n 4 DEL "drift_monitor:baseline"
# The monitor will recalibrate on the next check_interval (1000 transactions)

# Option 3: Roll back to a known-good model version
# See model-operations.md for rollback procedure
```

### Verify

```bash
# After baseline reset, wait for recalibration
sleep 60
curl -s http://localhost:8000/metrics | grep 'fraud_model_drift_psi'

# After retraining, confirm new model is loaded
curl -s http://localhost:8000/metrics | grep 'model_status_info'
```

---

## Alert: HighErrorRate

**Recommended rule:**
```yaml
- alert: HighErrorRate
  expr: rate(errors_total[5m]) > 1
  for: 5m
  labels:
    severity: high
  annotations:
    summary: "Error rate exceeds 1/sec on {{ $labels.component }}"
```

**Severity:** SEV2

### Problem
A component is generating errors at a sustained rate exceeding 1 per second.

### Diagnosis

```bash
# Check which component and error type
curl -s http://localhost:8000/metrics | grep 'errors_total'
curl -s http://localhost:8001/metrics | grep 'errors_total'
curl -s http://localhost:8002/metrics | grep 'errors_total'

# Check Kafka-specific errors
curl -s http://localhost:8000/metrics | grep 'kafka_errors_total'

# Check Redis errors
curl -s http://localhost:8000/metrics | grep 'redis_operations_total{.*status="error"}'

# Check consumer logs for error details
journalctl -u fraud-detector --since "10 minutes ago" --priority err --no-pager
```

### Fix

Fix depends on the error type identified in diagnosis:

- **Kafka errors:** Check broker health, see HighConsumerLag section
- **Redis errors:** Check Redis connectivity, see RedisUnavailable section
- **Deserialization errors:** Check for schema changes, inspect DLQ messages
- **Model errors:** Check model file integrity, see ModelScoringDegraded section

### Verify

```bash
# Confirm error rate is decreasing
watch -n 10 "curl -s http://localhost:8000/metrics | grep 'errors_total'"
```

---

## Alert: KafkaBrokerDown

**Recommended rule:**
```yaml
- alert: KafkaBrokerDown
  expr: up{job="kafka"} == 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Kafka broker is unreachable"
```

**Severity:** SEV1

### Problem
The Kafka broker is unreachable. All message production and consumption will stop.

### Diagnosis

```bash
# Check Docker container status
docker ps -a | grep kafka

# Check Kafka logs
docker logs stream-sentinel-kafka --tail 100

# Check Zookeeper (Kafka dependency)
docker logs stream-sentinel-zookeeper --tail 50

# Check disk space (Kafka is disk-intensive)
docker exec stream-sentinel-kafka df -h /var/lib/kafka/data

# Check if port is listening
ss -tlnp | grep 9092
```

### Fix

```bash
# Restart Kafka (will rejoin the cluster)
docker compose -f docker/docker-compose.yml restart kafka

# If Zookeeper is also down, restart both
docker compose -f docker/docker-compose.yml restart zookeeper
sleep 10
docker compose -f docker/docker-compose.yml restart kafka

# If disk is full, clean old segments
docker exec stream-sentinel-kafka kafka-log-dirs \
  --bootstrap-server localhost:9092 --describe --broker-list 1

# Wait for broker to be ready
docker exec stream-sentinel-kafka kafka-broker-api-versions \
  --bootstrap-server localhost:9092 2>&1 | head -3
```

### Verify

```bash
# Confirm broker is healthy
docker exec stream-sentinel-kafka kafka-broker-api-versions \
  --bootstrap-server localhost:9092

# Confirm topics are accessible
docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 --list

# Confirm consumers can reconnect
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 --list
```

---

## Alert: RedisUnavailable

**Recommended rule:**
```yaml
- alert: RedisUnavailable
  expr: component_health_status{component_name="redis", check_type="general"} == 0
  for: 30s
  labels:
    severity: critical
  annotations:
    summary: "Redis is unreachable"
```

**Severity:** SEV1

### Problem
Redis is unreachable. User profiles cannot be loaded, blocked user checks will fail, and drift baselines are unavailable.

### Diagnosis

```bash
# Check Redis container
docker ps -a | grep redis

# Try to connect
redis-cli -p 6379 ping

# Check Redis logs
docker logs stream-sentinel-redis --tail 50

# Check memory
redis-cli -p 6379 INFO memory 2>/dev/null || echo "Cannot connect"

# Check if port is bound
ss -tlnp | grep 6379
```

### Fix

```bash
# Restart Redis
docker compose -f docker/docker-compose.yml restart redis

# If Redis is OOM, the container may have been killed
# Check and restart
docker compose -f docker/docker-compose.yml up -d redis

# Verify data persistence (AOF enabled)
docker exec stream-sentinel-redis ls -la /data/appendonly.aof
```

### Verify

```bash
redis-cli -p 6379 ping
# Expected: PONG

redis-cli -p 6379 INFO keyspace

# Confirm consumers have reconnected
curl -s http://localhost:8000/metrics | grep 'redis_operations_total{.*status="success"}'
```

---

## Alert: PostgreSQLDown

**Recommended rule:**
```yaml
- alert: PostgreSQLDown
  expr: component_health_status{component_name="postgresql", check_type="general"} == 0
  for: 1m
  labels:
    severity: high
  annotations:
    summary: "PostgreSQL is unreachable"
```

**Severity:** SEV2

### Problem
PostgreSQL is unreachable. Fraud alerts and audit logs are not being persisted. Core fraud scoring continues but compliance data is at risk.

### Diagnosis

```bash
docker exec stream-sentinel-postgres pg_isready -U stream_sentinel_user -d stream_sentinel
docker logs stream-sentinel-postgres --tail 50
docker ps -a | grep postgres
```

### Fix

```bash
docker compose -f docker/docker-compose.yml restart postgres
sleep 10
docker exec stream-sentinel-postgres pg_isready -U stream_sentinel_user -d stream_sentinel
```

### Verify

```bash
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel \
  -c "SELECT COUNT(*) FROM fraud_alerts WHERE created_at > NOW() - INTERVAL '5 minutes';"
```

---

## Alert: ClickHouseDown

**Recommended rule:**
```yaml
- alert: ClickHouseDown
  expr: component_health_status{component_name="clickhouse", check_type="general"} == 0
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "ClickHouse is unreachable"
```

**Severity:** SEV3

### Problem
ClickHouse is unreachable. Analytics queries and materialized views are not being updated. Core fraud scoring is unaffected.

### Diagnosis

```bash
curl -s "http://localhost:8123/ping"
docker logs stream-sentinel-clickhouse --tail 50
docker ps -a | grep clickhouse
```

### Fix

```bash
docker compose -f docker/docker-compose.yml restart clickhouse
sleep 15
curl -s "http://localhost:8123/ping"
```

### Verify

```bash
curl -s "http://localhost:8123/?query=SELECT+count()+FROM+stream_sentinel.transaction_records"
```

---

## Alert: HighDLQVolume

**Recommended rule:**
```yaml
- alert: HighDLQVolume
  expr: rate(kafka_messages_produced_total{topic="dead-letter-queue"}[5m]) > 0.1
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Dead letter queue receiving messages at sustained rate"
```

**Severity:** SEV3

### Problem
Messages are failing processing and being routed to the dead letter queue. This may indicate data quality issues, schema changes, or processing bugs.

### Diagnosis

```bash
# Check DLQ message count
docker exec stream-sentinel-kafka kafka-run-class.sh kafka.tools.GetOffsetShell \
  --broker-list localhost:9092 --topic dead-letter-queue --time -1

# Sample DLQ messages to understand the failures
docker exec stream-sentinel-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic dead-letter-queue \
  --from-beginning --max-messages 3 --timeout-ms 5000

# Check for deserialization errors
curl -s http://localhost:8000/metrics | grep 'kafka_errors_total{.*error_type="deserialization"}'
```

### Fix

```bash
# Start the DLQ consumer to process failed messages
python src/consumers/dlq_consumer.py &

# If schema issues, check Schema Registry
curl -s http://localhost:8081/subjects

# If producer is sending malformed data, fix the producer
```

### Verify

```bash
# Confirm DLQ is being drained
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group dlq-processor-group
```

---

## Alert: HighRedisLatency

**Recommended rule:**
```yaml
- alert: HighRedisLatency
  expr: histogram_quantile(0.99, rate(redis_operation_duration_seconds_bucket[5m])) > 0.01
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Redis P99 latency exceeds 10ms"
```

**Severity:** SEV3

### Problem
Redis operations are taking longer than expected. This will impact fraud detection latency since user profile lookups and blocked-user checks depend on Redis.

### Diagnosis

```bash
# Check Redis latency
redis-cli -p 6379 --latency -c 100

# Check slow log
redis-cli -p 6379 SLOWLOG GET 10

# Check memory usage (near max triggers eviction overhead)
redis-cli -p 6379 INFO memory | grep -E "used_memory_human|maxmemory_human|mem_fragmentation"

# Check connected clients
redis-cli -p 6379 INFO clients | grep connected_clients

# Check if AOF rewrite is running
redis-cli -p 6379 INFO persistence | grep -E "aof_rewrite|rdb_bgsave"
```

### Fix

```bash
# If memory fragmentation is high (ratio > 1.5)
redis-cli -p 6379 MEMORY DOCTOR

# If too many keys, check for stale data
redis-cli -p 6379 DBSIZE

# If AOF is causing latency, consider adjusting fsync policy
# In docker-compose.yml, change command to include: --appendfsync everysec
```

### Verify

```bash
redis-cli -p 6379 --latency -c 50
# P99 should be under 1ms for normal operations
```

---

## Alert: HighFraudRate

**Recommended rule:**
```yaml
- alert: HighFraudRate
  expr: fraud_rate{time_window="1h"} > 10
  for: 15m
  labels:
    severity: warning
  annotations:
    summary: "Fraud rate exceeds 10% over the last hour"
```

**Severity:** SEV3

### Problem
An abnormally high percentage of transactions are being flagged as fraudulent. This could indicate a genuine attack wave, or it could be a false positive spike due to model or data issues.

### Diagnosis

```bash
# Check current fraud rate
curl -s http://localhost:8000/metrics | grep 'fraud_rate'

# Check fraud score distribution
curl -s http://localhost:8000/metrics | grep 'fraud_score_distribution'

# Check if model drift is also elevated
curl -s http://localhost:8000/metrics | grep 'fraud_model_drift_psi'

# Check model status
curl -s http://localhost:8000/metrics | grep 'model_status_info'

# Check false positive rate
curl -s http://localhost:8000/metrics | grep 'false_positive_rate'

# Check recent alerts by severity
curl -s http://localhost:8001/metrics | grep 'alerts_generated_total'
```

### Fix

```bash
# If rules_fallback is active and causing high fraud rate:
# See ModelScoringDegraded section to restore ML model

# If genuine attack: this is expected behavior, ensure alert processor
# is blocking malicious users
redis-cli -p 6379 SMEMBERS blocked_users

# If false positive spike due to data shift:
# Temporarily increase fraud threshold
# Restart fraud detector with higher threshold
python src/consumers/fraud_detector.py --threshold 0.7 &
```

### Verify

```bash
# Monitor fraud rate decreasing
watch -n 30 "curl -s http://localhost:8000/metrics | grep 'fraud_rate'"
```

---

## Alert: PrometheusTargetDown

**Recommended rule:**
```yaml
- alert: PrometheusTargetDown
  expr: up == 0
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Prometheus target {{ $labels.job }} is down"
```

**Severity:** SEV3 (creates a monitoring blind spot)

### Problem
One or more Prometheus scrape targets are unreachable. Metrics and alerting for that component are unavailable.

### Diagnosis

```bash
# Check which targets are down
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool | grep -A5 '"health": "down"'

# Check individual endpoints
for port in 8000 8001 8002 8003; do
  echo "Port $port: $(curl -s --max-time 3 http://localhost:$port/metrics | head -1 || echo 'DOWN')"
done
```

### Fix

Restart the affected consumer. See the relevant consumer-specific alert section above.

### Verify

```bash
# Confirm all targets are up
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool | grep '"health"'
```

---

## Alert: HighBlockedTransactionRate

**Recommended rule:**
```yaml
- alert: HighBlockedTransactionRate
  expr: rate(transactions_blocked_total[5m]) > 10
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Blocked transaction rate exceeds 10/sec"
```

**Severity:** SEV3

### Problem
A large number of transactions are being blocked because users are on the `blocked_users` Redis set. This may indicate a coordinated attack, or users may have been incorrectly blocked.

### Diagnosis

```bash
# Check blocked users count
redis-cli -p 6379 SCARD blocked_users

# Check recent blocked transaction metric
curl -s http://localhost:8000/metrics | grep 'transactions_blocked_total'

# List blocked users
redis-cli -p 6379 SMEMBERS blocked_users

# Check who blocked them (via PostgreSQL audit)
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel \
  -c "SELECT user_id, blocked_at, blocked_reason FROM user_accounts WHERE status = 'BLOCKED' ORDER BY blocked_at DESC LIMIT 20;"
```

### Fix

```bash
# If users were incorrectly blocked, unblock them
redis-cli -p 6379 SREM blocked_users "user_id_to_unblock"

# Update PostgreSQL as well
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel \
  -c "UPDATE user_accounts SET status = 'ACTIVE', blocked_at = NULL, blocked_reason = NULL WHERE user_id = 'user_id_to_unblock';"
```

### Verify

```bash
redis-cli -p 6379 SCARD blocked_users
curl -s http://localhost:8000/metrics | grep 'transactions_blocked_total'
```
