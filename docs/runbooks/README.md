# Stream Sentinel -- Operational Runbooks

Production runbooks for the Stream Sentinel real-time fraud detection system.

## Runbook Index

| Runbook | Purpose | Primary Audience |
|---------|---------|-----------------|
| [Incident Response](incident-response.md) | SEV1-SEV4 classification, escalation paths, post-mortem template | On-call, incident commanders |
| [Alert Response](alert-response.md) | Per-alert diagnosis and remediation for every Prometheus alert | On-call engineers |
| [Disaster Recovery](disaster-recovery.md) | Recovery procedures for Kafka, Redis, PostgreSQL, ClickHouse | SRE / Platform |
| [Scaling](scaling.md) | Horizontal and vertical scaling for all components | SRE / Platform |
| [Model Operations](model-operations.md) | Model deployment, rollback, retraining, A/B testing, drift | ML Engineering |
| [Troubleshooting](troubleshooting.md) | Common issues: consumer stalls, high latency, false positives | On-call engineers |
| [Capacity Planning](capacity-planning.md) | Resource estimation, scaling thresholds, growth projections | SRE / Engineering leads |

## System Quick Reference

### Service Ports

| Service | Port | Protocol |
|---------|------|----------|
| Kafka broker | 9092 | TCP (PLAINTEXT) |
| Kafka JMX | 9101 | JMX |
| Schema Registry | 8081 | HTTP |
| Kafka UI | 8080 | HTTP |
| Redis | 6379 | TCP |
| Redis Insight | 8001 | HTTP |
| PostgreSQL | 5432 | TCP |
| ClickHouse HTTP | 8123 | HTTP |
| ClickHouse native | 9000 | TCP |
| Prometheus | 9090 | HTTP |
| Grafana | 3000 | HTTP |
| Fraud Detector metrics | 8000 | HTTP |
| Alert Processor metrics | 8001 | HTTP |
| Persistence Consumer metrics | 8002 | HTTP |
| Enhanced Fraud Detector metrics | 8003 | HTTP |
| DLQ Consumer metrics | 8004 | HTTP |

### Kafka Topics

| Topic | Partitions | Retention | Purpose |
|-------|-----------|-----------|---------|
| `synthetic-transactions` | 12 | 7 days | Inbound transactions |
| `fraud-alerts` | 6 | 30 days | Fraud alerts for alert processor |
| `fraud-detection-results` | 6 | 7 days | Full results for persistence |
| `blocked-transactions` | 6 | 30 days | Transactions from blocked users |
| `model-drift-alerts` | 3 | 30 days | PSI drift detection alerts |
| `model-retraining-jobs` | 3 | 30 days | Retraining trigger messages |
| `dead-letter-queue` | 3 | 30 days | Failed message processing |

### Key Metrics

| Metric | Type | Labels |
|--------|------|--------|
| `transactions_processed_total` | Counter | component, status |
| `fraud_detection_duration_seconds` | Histogram | component |
| `model_inference_duration_seconds` | Histogram | model_version, model_type |
| `kafka_consumer_lag_messages` | Gauge | topic, partition, consumer_group |
| `kafka_messages_consumed_total` | Counter | topic, component, consumer_group |
| `kafka_errors_total` | Counter | operation, error_type, component |
| `redis_operation_duration_seconds` | Histogram | operation, database |
| `alerts_generated_total` | Counter | severity, alert_type |
| `model_status_info` | Gauge | status |
| `fraud_model_drift_psi` | Gauge | (none) |
| `errors_total` | Counter | component, error_type, severity |
| `component_health_status` | Gauge | component_name, check_type |

### Docker Compose Files

```bash
# Core infrastructure
docker compose -f docker/docker-compose.yml up -d

# Monitoring stack
docker compose -f docker/docker-compose.monitoring.yml up -d

# Secure/production variant
docker compose -f docker/docker-compose.secure.yml up -d
```

### Consumer Groups

| Consumer Group | Consumer | Input Topic |
|---------------|----------|-------------|
| `fraud-detection-group` | fraud_detector.py | synthetic-transactions |
| `alert-processor-group` | alert_processor.py | fraud-alerts |
| `stream-sentinel-persistence` | persistence_consumer.py | fraud-detection-results |
| `dlq-processor-group` | dlq_consumer.py | dead-letter-queue |
| `retraining-trigger-group` | retraining_trigger.py | model-drift-alerts |
