# Troubleshooting Runbook

## Issue: Consumer Not Consuming

### Problem
A consumer is running but not processing messages. The `transactions_processed_total` counter is not incrementing.

### Diagnosis

```bash
# 1. Check if the consumer is subscribed and has partitions assigned
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group fraud-detection-group

# Look at the PARTITION and CURRENT-OFFSET columns
# If PARTITION shows no assignments, the consumer may be stuck in rebalance

# 2. Check if there are messages on the topic
docker exec stream-sentinel-kafka kafka-run-class.sh kafka.tools.GetOffsetShell \
  --broker-list localhost:9092 --topic synthetic-transactions --time -1

# 3. Check consumer logs for errors
journalctl -u fraud-detector --since "10 minutes ago" --no-pager | tail -50

# 4. Check if the consumer process is hung
ps aux | grep fraud_detector.py
# Check CPU usage -- 0% CPU may indicate the process is blocked

# 5. Check Kafka connectivity from the consumer host
python -c "
from confluent_kafka import Consumer
c = Consumer({'bootstrap.servers': 'localhost:9092', 'group.id': 'test-check'})
metadata = c.list_topics(timeout=5)
print(f'Connected. Topics: {len(metadata.topics)}')
c.close()
"

# 6. Check if Schema Registry is causing issues (if Avro is enabled)
curl -s http://localhost:8081/subjects || echo "Schema Registry unreachable"
```

### Fix

```bash
# If consumer is stuck in rebalance, kill and restart
kill -TERM $(pgrep -f fraud_detector.py)
sleep 5
python src/consumers/fraud_detector.py &

# If consumer offset is beyond the topic high watermark (offset reset issue)
# Reset offsets
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group fraud-detection-group \
  --reset-offsets --to-latest \
  --topic synthetic-transactions \
  --execute

# Restart consumer after offset reset
python src/consumers/fraud_detector.py &

# If Kafka is unreachable, check Kafka broker health
docker exec stream-sentinel-kafka kafka-broker-api-versions \
  --bootstrap-server localhost:9092
```

### Verify

```bash
# Watch transaction count increment
watch -n 5 "curl -s http://localhost:8000/metrics | grep 'transactions_processed_total'"

# Check lag is decreasing
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group fraud-detection-group
```

---

## Issue: High End-to-End Latency

### Problem
The `fraud_detection_duration_seconds` P99 exceeds 100ms. Transactions are being scored but too slowly.

### Diagnosis

```bash
# 1. Identify which stage is the bottleneck
# Check feature extraction time
curl -s http://localhost:8000/metrics | grep 'feature_extraction_duration_seconds'

# Check model inference time
curl -s http://localhost:8000/metrics | grep 'model_inference_duration_seconds'

# Check Redis operation time
curl -s http://localhost:8000/metrics | grep 'redis_operation_duration_seconds'

# Check overall fraud detection time
curl -s http://localhost:8000/metrics | grep 'fraud_detection_duration_seconds'

# 2. Check Redis latency
redis-cli -p 6379 --latency -c 100

# 3. Check system resources on the consumer host
top -b -n 1 | head -20
free -h
iostat -x 1 3
vmstat 1 3

# 4. Check if garbage collection is causing pauses (Python GC)
# Look for long pauses in the consumer logs

# 5. Check Kafka produce latency (publishing results/alerts)
curl -s http://localhost:8000/metrics | grep 'kafka_produce_duration_seconds'

# 6. Check if batch mode could help
# If currently in single-message mode, batch amortizes overhead
```

### Fix

```bash
# If Redis is the bottleneck (>5ms per operation):
# Check Redis slow log
redis-cli -p 6379 SLOWLOG GET 10
# Check memory pressure
redis-cli -p 6379 INFO memory | grep used_memory_human

# If model inference is the bottleneck (>10ms per prediction):
# Consider enabling C++ acceleration
python src/inference/export_model.py \
  --input models/synthetic_fraud_model_production.pkl \
  --output models/fraud_model_native.json

# Or switch to batch mode for amortized cost
kill -TERM $(pgrep -f fraud_detector.py)
sleep 5
python src/consumers/fraud_detector.py --batch --batch-size 32 --batch-timeout-ms 100 &

# If CPU-bound:
# Scale horizontally with additional consumer instances
python src/consumers/fraud_detector.py &

# If feature extraction is slow:
# Check if FeatureEngineer is doing expensive computations
# The unified feature engineer handles both batch and streaming modes
```

### Verify

```bash
# Monitor P99 latency
watch -n 10 "curl -s http://localhost:8000/metrics | grep 'fraud_detection_duration_seconds_bucket' | tail -5"
```

---

## Issue: False Positive Spike

### Problem
The `false_positive_rate` has increased significantly, or the alert processor is generating an unusually high number of alerts.

### Diagnosis

```bash
# 1. Check false positive rate metric
curl -s http://localhost:8000/metrics | grep 'false_positive_rate'

# 2. Check fraud score distribution (shifted toward higher scores?)
curl -s http://localhost:8000/metrics | grep 'fraud_score_distribution'

# 3. Check model status (rules_fallback produces different scoring)
curl -s http://localhost:8000/metrics | grep 'model_status_info'

# 4. Check for model drift
curl -s http://localhost:8000/metrics | grep 'fraud_model_drift_psi'

# 5. Check alert generation by severity
curl -s http://localhost:8001/metrics | grep 'alerts_generated_total'

# 6. Check input data characteristics
# Sample recent transactions
docker exec stream-sentinel-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic synthetic-transactions \
  --max-messages 5 --timeout-ms 5000

# 7. Check if the fraud threshold was changed
# Default threshold is 0.5, but FraudDetector init uses 0.3
# Verify in consumer logs
journalctl -u fraud-detector | grep "threshold"

# 8. Compare with recent ClickHouse data
curl -s "http://localhost:8123/?query=SELECT+toStartOfHour(timestamp)+AS+hour,+count()+AS+total,+sum(is_fraud)+AS+fraud,+round(sum(is_fraud)/count()*100,2)+AS+pct+FROM+stream_sentinel.transaction_records+WHERE+timestamp+>+now()-86400+GROUP+BY+hour+ORDER+BY+hour+DESC+LIMIT+24"
```

### Fix

```bash
# If rules_fallback is active and causing high false positives:
# Restore the ML model (see model-operations.md)

# If model drift is causing the issue:
# Reset drift baseline and monitor
redis-cli -p 6379 -n 4 DEL "drift_monitor:baseline"

# If input data distribution has changed legitimately:
# Trigger model retraining
python -m src.ml.online_learning.retraining_trigger &

# If threshold needs temporary adjustment:
kill -TERM $(pgrep -f fraud_detector.py)
sleep 5
# Higher threshold = fewer false positives (but may miss real fraud)
python src/consumers/fraud_detector.py --fraud-threshold 0.7 &

# If specific users are being incorrectly flagged:
# Check and potentially unblock
redis-cli -p 6379 SMEMBERS blocked_users
redis-cli -p 6379 SREM blocked_users "falsely_blocked_user_id"
```

### Verify

```bash
# Monitor false positive rate
watch -n 30 "curl -s http://localhost:8000/metrics | grep 'false_positive_rate'"

# Check alert rate is decreasing
watch -n 30 "curl -s http://localhost:8001/metrics | grep 'alerts_generated_total'"
```

---

## Issue: Memory Leak / High Memory Usage

### Problem
A consumer process is using increasing amounts of memory over time, eventually risking OOM kills.

### Diagnosis

```bash
# 1. Check process memory
ps aux | grep -E "fraud_detector|alert_processor|persistence_consumer" | awk '{print $1, $2, $4, $6, $11}'

# 2. Check system memory
free -h

# 3. Check for OOM kills
dmesg | grep -i "oom\|killed" | tail -10

# 4. Check Prometheus memory metric
curl -s http://localhost:8000/metrics | grep 'memory_usage_bytes'

# 5. Check Python-specific memory
python -c "
import psutil
for proc in psutil.process_iter(['pid', 'name', 'memory_info', 'cmdline']):
    if 'fraud_detector' in str(proc.info['cmdline']):
        mem = proc.info['memory_info']
        print(f'PID {proc.info[\"pid\"]}: RSS={mem.rss/1024/1024:.1f}MB, VMS={mem.vms/1024/1024:.1f}MB')
"

# 6. Check Redis memory (consumer fetches profiles)
redis-cli -p 6379 INFO memory | grep -E "used_memory_human|mem_fragmentation"

# 7. Check if batch_metrics is unbounded (it caps at 1000 samples by design)
# Check if score_buffer in LiveDriftMonitor is growing
# (capped at check_interval * 2 = 2000 by default)
```

### Fix

```bash
# If a consumer is leaking memory, restart it
kill -TERM $(pgrep -f fraud_detector.py)
sleep 5
python src/consumers/fraud_detector.py &

# If Redis is consuming too much memory on the consumer side:
# Check for large profile objects
redis-cli -p 6379 --bigkeys

# If the consumer heap keeps growing, set a memory limit (Linux cgroups)
# Or use systemd MemoryMax:
# systemctl edit fraud-detector
# [Service]
# MemoryMax=2G

# For Docker-based consumers:
# Add memory limits in docker-compose.yml
# deploy:
#   resources:
#     limits:
#       memory: 2G
```

### Verify

```bash
# Monitor memory usage over time
watch -n 30 "ps aux | grep fraud_detector | grep -v grep | awk '{print \$4, \$6}'"
```

---

## Issue: Kafka Connectivity Problems

### Problem
Consumers or producers cannot connect to Kafka. Connection timeouts or broker-not-available errors.

### Diagnosis

```bash
# 1. Check if Kafka container is running
docker ps | grep kafka

# 2. Check if port is reachable
ss -tlnp | grep 9092
nc -zv localhost 9092

# 3. Check Kafka logs for errors
docker logs stream-sentinel-kafka --tail 50

# 4. Check Zookeeper (Kafka dependency)
docker logs stream-sentinel-zookeeper --tail 30
docker exec stream-sentinel-zookeeper bash -c "echo ruok | nc localhost 2181"

# 5. Check advertised listeners configuration
docker exec stream-sentinel-kafka cat /etc/kafka/server.properties | grep listener

# 6. Test connectivity from a Python client
python -c "
from confluent_kafka.admin import AdminClient
admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
topics = admin.list_topics(timeout=5)
print(f'Connected. Brokers: {len(topics.brokers)}, Topics: {len(topics.topics)}')
"

# 7. Check network (Docker networking issues)
docker network ls
docker network inspect docker_default 2>/dev/null || docker network inspect stream-sentinel_default
```

### Fix

```bash
# If Kafka container is stopped
docker compose -f docker/docker-compose.yml up -d kafka

# If Zookeeper is down (Kafka depends on it)
docker compose -f docker/docker-compose.yml restart zookeeper
sleep 15
docker compose -f docker/docker-compose.yml restart kafka

# If Docker network issues
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml up -d

# If port conflict
ss -tlnp | grep 9092  # find what is using the port
# Stop the conflicting process or change Kafka port

# If listener configuration is wrong (e.g., container rebuilt)
# Verify docker-compose.yml has:
#   KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092,PLAINTEXT_HOST://localhost:9092
```

### Verify

```bash
docker exec stream-sentinel-kafka kafka-broker-api-versions \
  --bootstrap-server localhost:9092

docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 --list
```

---

## Issue: Redis Connectivity Problems

### Problem
Consumers cannot connect to Redis. Profile lookups fail, blocked user checks fail.

### Diagnosis

```bash
# 1. Check if Redis container is running
docker ps | grep redis

# 2. Test connectivity
redis-cli -p 6379 ping

# 3. Check if authentication is required
# docker-compose.yml conditionally enables requirepass based on REDIS_PASSWORD env var
redis-cli -p 6379 -a "${REDIS_PASSWORD}" ping

# 4. Check Redis logs
docker logs stream-sentinel-redis --tail 30

# 5. Check max connections
redis-cli -p 6379 INFO clients | grep -E "connected|blocked|rejected"

# 6. Check if Redis is in protected mode
redis-cli -p 6379 CONFIG GET protected-mode
```

### Fix

```bash
# If Redis container is stopped
docker compose -f docker/docker-compose.yml up -d redis

# If Redis is OOM (maxmemory reached, evicting keys aggressively)
redis-cli -p 6379 INFO memory | grep used_memory_human
# If near 512MB limit, increase maxmemory in docker-compose.yml

# If too many connections
redis-cli -p 6379 CLIENT LIST | wc -l
# Kill idle clients if needed
redis-cli -p 6379 CLIENT KILL ID <client-id>

# If authentication changed
# Ensure .env file has the correct REDIS_PASSWORD
# Restart consumers to pick up new credentials
```

### Verify

```bash
redis-cli -p 6379 ping
redis-cli -p 6379 DBSIZE
curl -s http://localhost:8000/metrics | grep 'redis_operations_total{.*status="success"}'
```

---

## Issue: Dead Letter Queue Filling Up

### Problem
The `dead-letter-queue` topic is accumulating messages, indicating processing failures.

### Diagnosis

```bash
# 1. Check DLQ message volume
docker exec stream-sentinel-kafka kafka-run-class.sh kafka.tools.GetOffsetShell \
  --broker-list localhost:9092 --topic dead-letter-queue --time -1

# 2. Sample messages to understand the failure pattern
docker exec stream-sentinel-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic dead-letter-queue \
  --from-beginning --max-messages 5 --timeout-ms 5000

# 3. Check error metrics
curl -s http://localhost:8000/metrics | grep 'kafka_errors_total'
curl -s http://localhost:8000/metrics | grep 'errors_total'

# 4. Check if DLQ consumer is running
ps aux | grep dlq_consumer.py

# 5. Check for schema/serialization issues
# DLQ messages typically contain the original message plus error metadata
```

### Fix

```bash
# Start DLQ consumer to process and classify failed messages
python src/consumers/dlq_consumer.py &

# If the root cause is deserialization errors from schema changes:
# Check Schema Registry for incompatible schema updates
curl -s http://localhost:8081/subjects/synthetic-transactions-value/versions/latest

# If the root cause is processing errors in the fraud detector:
# Check fraud detector logs for the specific error
journalctl -u fraud-detector --since "1 hour ago" | grep -i error | head -20

# If messages are permanently unprocessable, purge the DLQ
# WARNING: this discards failed messages permanently
docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --alter --topic dead-letter-queue \
  --config retention.ms=1000
# Wait a minute, then restore normal retention
sleep 60
docker exec stream-sentinel-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --alter --topic dead-letter-queue \
  --config retention.ms=2592000000
```

### Verify

```bash
# Confirm DLQ growth has stopped
docker exec stream-sentinel-kafka kafka-run-class.sh kafka.tools.GetOffsetShell \
  --broker-list localhost:9092 --topic dead-letter-queue --time -1
```

---

## Issue: Schema Registry Unavailable

### Problem
Schema Registry is unreachable. The system falls back to JSON serialization, so this is non-blocking.

### Diagnosis

```bash
curl -s http://localhost:8081/subjects || echo "Unreachable"
docker ps | grep schema-registry
docker logs stream-sentinel-schema-registry --tail 30
```

### Fix

```bash
docker compose -f docker/docker-compose.yml restart schema-registry
sleep 10
curl -s http://localhost:8081/subjects
```

### Verify

```bash
curl -s http://localhost:8081/subjects
# Should return a JSON array of subject names (may be empty if no schemas registered)
```

---

## Issue: Consumer Group Rebalance Storm

### Problem
Consumers are repeatedly joining and leaving the group, causing continuous rebalances. Processing stalls during each rebalance.

### Diagnosis

```bash
# 1. Check consumer group state
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group fraud-detection-group

# Look for STATE: Rebalancing or Empty

# 2. Check for consumer crashes/restarts
ps aux | grep fraud_detector.py
# If PID keeps changing, the consumer is crashing and restarting

# 3. Check session timeout
# Default session.timeout.ms in development = 10000 (10s)
# If processing takes longer, the consumer will be evicted

# 4. Check max.poll.interval.ms
# Default in development = 60000 (60s)
# If a poll cycle takes longer, the consumer is evicted

# 5. Check consumer logs for rebalance events
journalctl -u fraud-detector | grep -i "rebalance\|revoked\|assigned"
```

### Fix

```bash
# If processing is taking too long per batch:
# Reduce batch size or increase timeout
kill -TERM $(pgrep -f fraud_detector.py)
sleep 5
python src/consumers/fraud_detector.py --batch --batch-size 16 --batch-timeout-ms 50 &

# If consumer is crashing (OOM, unhandled exception):
# Check logs for the crash reason and fix the root cause
journalctl -u fraud-detector --since "30 minutes ago" | grep -E "error|exception|kill" -i

# If session timeout is too aggressive:
# Increase session.timeout.ms in src/kafka/config.py for development
# Default development config: session.timeout.ms=10000
```

### Verify

```bash
# Confirm stable partition assignment
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group fraud-detection-group

# State should be "Stable" and all partitions assigned
```

---

## Quick Diagnostic Commands

### Full System Health Check

```bash
echo "=== Kafka ==="
docker exec stream-sentinel-kafka kafka-broker-api-versions --bootstrap-server localhost:9092 2>&1 | head -3

echo "=== Redis ==="
redis-cli -p 6379 ping

echo "=== PostgreSQL ==="
docker exec stream-sentinel-postgres pg_isready -U stream_sentinel_user -d stream_sentinel

echo "=== ClickHouse ==="
curl -s "http://localhost:8123/ping"

echo "=== Schema Registry ==="
curl -s --max-time 3 http://localhost:8081/subjects | head -1 || echo "Unreachable"

echo "=== Consumer Metrics ==="
for port in 8000 8001 8002 8003; do
  status=$(curl -s --max-time 3 http://localhost:$port/metrics | head -1)
  echo "Port $port: ${status:+OK}"
  [ -z "$status" ] && echo "Port $port: DOWN"
done

echo "=== Consumer Groups ==="
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 --list

echo "=== Topic Offsets ==="
docker exec stream-sentinel-kafka kafka-run-class.sh kafka.tools.GetOffsetShell \
  --broker-list localhost:9092 --topic synthetic-transactions --time -1
```

### Model Health Check

```bash
echo "=== Model File ==="
ls -la models/synthetic_fraud_model_production.pkl

echo "=== Model Status Metric ==="
curl -s http://localhost:8000/metrics | grep 'model_status_info'

echo "=== Drift PSI ==="
curl -s http://localhost:8000/metrics | grep 'fraud_model_drift_psi'

echo "=== Inference Latency ==="
curl -s http://localhost:8000/metrics | grep 'model_inference_duration_seconds_count'
```
