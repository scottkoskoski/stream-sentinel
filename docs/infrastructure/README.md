# Infrastructure

Stream Sentinel runs on containerized services orchestrated through Docker Compose. Three compose files cover core services, monitoring, and secure deployment.

## Docker Compose Stacks

### Core Stack (`docker/docker-compose.yml`)

Start with:
```bash
docker compose -f docker/docker-compose.yml up -d
```

| Service | Port(s) | Purpose |
|---------|---------|---------|
| Zookeeper | 2181 | Kafka coordination and metadata |
| Kafka | 9092 | Distributed event streaming |
| Schema Registry | 8081 | Avro schema management (optional) |
| Kafka UI | 8080 | Web UI for topic/consumer monitoring |
| Redis | 6379 | User profiles, blocked users, model registry, drift baseline |
| Redis Insight | 8001 | Redis web UI for inspection and debugging |
| PostgreSQL | 5432 | OLTP storage for alerts and user management |
| ClickHouse | 8123 (HTTP), 9000 (native) | OLAP storage for analytics and time-series data |

### Monitoring Stack (`docker/docker-compose.monitoring.yml`)

Start with:
```bash
docker compose -f docker/docker-compose.monitoring.yml up -d
```

| Service | Port | Purpose |
|---------|------|---------|
| Prometheus | 9090 | Metrics collection and alerting |
| Grafana | 3000 | Dashboards and visualization |

Grafana ships with a pre-built fraud detection dashboard. Each consumer exposes Prometheus metrics on a dedicated port:

| Consumer | Metrics Port |
|----------|-------------|
| fraud_detector | 8000 |
| alert_processor | 8001 |
| persistence_consumer | 8002 |
| dlq_consumer | 8003 |

Prometheus scrapes these endpoints and stores time-series metrics (counters, histograms, gauges) defined in `src/monitoring/`.

### Secure Stack (`docker/docker-compose.secure.yml`)

Start with:
```bash
docker compose -f docker/docker-compose.secure.yml up -d
```

Production-oriented variant with TLS encryption and SASL/SCRAM authentication for Kafka.

## Kafka Configuration

**Client:** confluent-kafka (Python)
**Partitions:** 12 per topic
**Compression:** LZ4
**Retention:** 7 days (default)

Configuration is centralized in `src/kafka/config.py` with environment profiles:

| Environment | Key Settings |
|-------------|-------------|
| Development | Single broker (localhost:9092), acks=1, 10 retries |
| Staging | Multi-broker, acks=all, idempotent producer |
| Production | Multi-broker, acks=all, SASL_SSL, idempotent producer |

Environment-based configuration is managed through `.env` files (see `.env.example`).

## Kafka Topics

All 7 topics used by the system:

| Topic | Purpose |
|-------|---------|
| `synthetic-transactions` | Input transaction stream from producers |
| `fraud-alerts` | High-severity fraud detections for alert processor |
| `fraud-detection-results` | Full scoring results for persistence layer |
| `blocked-transactions` | Transactions from blocked users (skipped scoring) |
| `model-drift-alerts` | PSI drift detection alerts from live monitoring |
| `model-retraining-jobs` | Retraining trigger messages for model pipeline |
| `dead-letter-queue` | Failed message processing for retry/investigation |

### Data Flow

```
Producers -> synthetic-transactions -> Fraud Detector -> fraud-alerts -> Alert Processor
                                                      -> fraud-detection-results -> Persistence Consumer
                                                      -> blocked-transactions (blocked users)
                                    -> dead-letter-queue (failures)

Live Drift Monitor -> model-drift-alerts -> Retraining Trigger -> model-retraining-jobs
```

## Schema Registry

Schema Registry (port 8081) provides optional Avro schema management for Kafka messages. When available, producers serialize with Avro and consumers deserialize using registered schemas, enabling safe schema evolution.

When Schema Registry is unavailable, the system falls back to JSON serialization. This is controlled in `src/kafka/schema_utils.py`.

## Service Details

### Redis

Redis serves multiple roles:
- **User profiles:** behavioral data (transaction counts, amounts, timestamps) keyed by `user_profile:{user_id}`
- **Blocked users:** set of user IDs blocked by the alert processor
- **Model registry:** model versions, deployment state, metadata
- **Drift baseline:** reference distributions for PSI drift detection

Configuration: 512MB memory limit, LRU eviction, AOF persistence enabled.

### PostgreSQL

OLTP database for:
- Fraud alerts requiring investigation
- User account status and blocking actions
- Audit logs

Connection: `postgresql://stream_sentinel_user:stream_sentinel_password@localhost:5432/stream_sentinel`

Schema definitions are in `src/persistence/`.

### ClickHouse

OLAP database for:
- All processed transactions with fraud scores
- Feature data and detection results
- Analytics and time-series queries

Connection: `http://stream_sentinel_user:stream_sentinel_password@localhost:8123/stream_sentinel`

## Prometheus and Grafana

### Metrics

Consumers expose metrics via the `src/monitoring/` module:
- **Counters:** transactions processed, alerts generated, errors
- **Histograms:** inference latency, end-to-end processing time
- **Gauges:** consumer lag, model status, active connections

### Grafana Dashboard

The pre-built dashboard (available at http://localhost:3000) displays:
- Transaction throughput and processing latency
- Fraud detection rates and alert volumes
- Consumer lag across all topics
- Model inference performance

### Alerting

Prometheus alerting can be configured for:
- Consumer lag exceeding thresholds
- Inference latency degradation
- Error rate spikes
- Service health failures

## Operations

### Starting the Full Stack

```bash
# Start core services
docker compose -f docker/docker-compose.yml up -d

# Start monitoring
docker compose -f docker/docker-compose.monitoring.yml up -d

# Verify services
docker compose -f docker/docker-compose.yml ps
```

### Verification

```bash
# Test Kafka connectivity
python src/kafka/test_connectivity.py

# Test PostgreSQL
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel -c "SELECT version();"

# Test ClickHouse
docker exec stream-sentinel-clickhouse clickhouse-client --query "SELECT version()"

# Test Redis
redis-cli -h localhost -p 6379 ping
```

### Health Checks

All core services include Docker health checks (30s interval, 10s timeout, 3 retries). Kafka and databases have extended start periods (30-60s) to account for initialization time.

### Structured Logging

All consumers use JSON-formatted structured logging via `src/utils/logging.py`, with contextual fields including `transaction_id`, `user_id`, and `consumer_group`.

---

**Navigation:** [Documentation Index](../README.md) | [Docker Compose](../../docker/docker-compose.yml) | [Kafka Config](../../src/kafka/config.py)
