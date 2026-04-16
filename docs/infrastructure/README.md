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
| enhanced_fraud_detector | 8003 |
| dlq_consumer | 8004 |

Prometheus scrapes these endpoints and stores time-series metrics (counters, histograms, gauges) defined in `src/monitoring/metrics.py`. Production alert rules live in `docker/prometheus/alert_rules.yml` (16+ alerts covering consumer availability, lag, latency, model drift, error rates, and A/B experiment health). Alertmanager routing is configured via `docker/prometheus/alertmanager.yml`.

In addition to metrics, every consumer exposes health probes via `src/monitoring/health.py`:

| Endpoint | Purpose |
|----------|---------|
| `/health` | Liveness probe (returns 503 during startup grace, then 200 while healthy) |
| `/health/ready` | Readiness probe (checks Kafka/Redis/model dependencies) |
| `/health/details` | Verbose dependency status for debugging (toggleable via `HEALTH_DETAILS_ENABLED`) |

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

Prometheus alert rules at `docker/prometheus/alert_rules.yml` cover:
- Consumer availability (FraudDetectorDown, AlertProcessorDown, PersistenceConsumerDown)
- Consumer lag exceeding thresholds (HighConsumerLag)
- Inference latency degradation (HighFraudDetectionLatency)
- Model health (ModelScoringDegraded, ModelDriftDetected)
- Error rate spikes, fraud rate anomalies, and A/B experiment statistical signals

## Kubernetes Deployment

The `k8s/` directory contains raw Kubernetes manifests and `helm/stream-sentinel/` provides a Helm chart for production deployment.

```bash
# Option A: Helm (recommended for production)
helm install stream-sentinel helm/stream-sentinel/ -f helm/stream-sentinel/values.yaml

# Option B: Raw manifests
kubectl apply -f k8s/namespace.yaml -f k8s/serviceaccount.yaml
kubectl apply -f k8s/config/ -f k8s/consumers/ -f k8s/hpa/ -f k8s/monitoring/
```

Contents:

| Resource | File(s) |
|----------|---------|
| Namespace + RBAC | `k8s/namespace.yaml`, `k8s/serviceaccount.yaml` |
| Configuration | `k8s/config/` (ConfigMap, Secrets) |
| Consumer Deployments | `k8s/consumers/{fraud-detector,alert-processor,persistence-consumer,dlq-consumer}.yaml` |
| Autoscaling | `k8s/hpa/` (min=2, max=12, CPU >70% target) |
| Monitoring | `k8s/monitoring/` (Prometheus + Grafana) |

Consumer pods use the `docker/Dockerfile.consumer` image -- a multi-stage, non-root build that installs the slim runtime dependencies and bakes in the production model. Runtime model updates go through the Redis `ModelRegistry` rather than a mounted volume (see [Model Operations Runbook](../runbooks/model-operations.md)).

Liveness probe: `/health` on port 8080. Readiness probe: `/health/ready` on port 8080.

## CI/CD

GitHub Actions workflows live under `.github/workflows/`:

| Workflow | Purpose |
|----------|---------|
| `ci.yml` | Lint (black/isort/flake8), unit + integration tests, image build |
| `performance.yml` | Throughput and latency regression checks |
| `security.yml` | Dependency and secret scanning |

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

**Navigation:** [Documentation Index](../README.md) | [Docker Compose](../../docker/docker-compose.yml) | [Kafka Config](../../src/kafka/config.py) | [Runbooks](../runbooks/README.md) | [Helm Chart](../../helm/stream-sentinel/)
