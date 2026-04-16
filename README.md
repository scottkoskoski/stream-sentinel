# Stream Sentinel

Real-time fraud detection system built on event-driven streaming architecture. Processes financial transactions through Kafka, applies ML-based fraud scoring (XGBoost), manages user behavioral profiles in Redis, and persists results to PostgreSQL and ClickHouse.

## Architecture Overview

### End-to-End Pipeline

```
 Synthetic          Kafka            Fraud             Alert            Persistence
 Producer    -->   Broker    -->   Detector    -->   Processor   -->   Consumer
                                                        |
  IEEE-CIS         12 parts        XGBoost ML           |              PostgreSQL
  patterns         LZ4 comp        (primary)         Severity          ClickHouse
  entity           Schema          Rules-based       classify
  tracking         Registry        (fallback)        User blocking
                   (optional)      Batch mode        SLA tracking
                                   Drift monitor
```

### Detailed System Architecture

```
                         +---------------------------+
                         |    Transaction Producer    |
                         |  (synthetic_transaction_   |
                         |   producer.py)             |
                         |                           |
                         |  - IEEE-CIS feature dists |
                         |  - Entity tracking (C/D/M)|
                         |  - Fraud correlations     |
                         |  - 10k+ TPS              |
                         +-------------+-------------+
                                       |
                                       | synthetic-transactions (12 partitions)
                                       v
+----------------------------------------------------------------------+
|                        Apache Kafka Broker                            |
|                                                                      |
|  Topics:                                                             |
|    synthetic-transactions    fraud-alerts         blocked-transactions|
|    fraud-detection-results   model-drift-alerts   dead-letter-queue   |
|    model-retraining-jobs                                             |
|                                                                      |
|  Optional: Confluent Schema Registry (Avro validation)               |
+------+-----------+-----------+-------------+----------+--------------+
       |           |           |             |          |
       v           |           |             |          v
+------+------+    |    +------+------+      |   +-----+--------+
|   Fraud     |    |    |   Alert     |      |   |  Persistence |
|  Detector   |    |    |  Processor  |      |   |   Consumer   |
|             |    |    |             |      |   |              |
| 1. Block    |    |    | - Severity  |      |   | - Batch      |
|    check    |    |    |   classify  |      |   |   inserts    |
| 2. Profile  |    |    | - Response  |      |   | - Multi-topic|
|    load     +--->+    |   actions   |      |   |   grouping   |
| 3. Feature  |  fraud  | - User      |      |   +-+----+-------+
|    extract  | alerts  |   blocking  |      |     |    |
| 4. ML score |    |    | - SLA track |      |     |    |
| 5. Drift    |    |    +------+------+      |     |    |
|    monitor  |    |           |             |     |    |
| 6. Publish  |    |           | Redis:      |     |    |
|    results  |    |           | blocked_    |     |    |
+------+------+    |           | users set   |     |    |
       |           |           +-------+     |     |    |
       |           |                   |     |     |    |
       v           v                   v     v     v    v
+------+-----------+---------+  +------+-----+--+  +---+----------+
|        Redis               |  |   PostgreSQL  |  |  ClickHouse  |
|                            |  |               |  |              |
| - User profiles (hash)    |  | - Fraud alerts|  | - Txn records|
| - Blocked users (set)     |  | - User accts  |  | - ML features|
| - Drift baseline          |  | - Audit logs  |  | - Perf metrics|
| - Model registry          |  | - Model perf  |  | - Detection  |
| - A/B test assignments    |  |               |  |   results    |
+----------------------------+  +---------------+  +--------------+
       |
       v
+------+-----------+------+------+-----------+------+
|                Online Learning System              |
|                                                    |
|  Drift Detector --> Retraining Trigger             |
|                       |                            |
|  Feedback       Model Registry    A/B Testing      |
|  Processor      (versioning)      (variant assign) |
|                                                    |
|  Incremental Learner (scheduled retraining)        |
+----------------------------------------------------+
       |
       v
+------+----------------------------------------------+
|              Observability                           |
|                                                      |
|  Prometheus (scrapes ports 8000-8003)                |
|      |                                               |
|  Grafana (port 3000)                                 |
|    - TPS throughput        - Fraud score distribution|
|    - Model inference P99   - Consumer lag            |
|    - Blocked transactions  - Alert severity breakdown|
|    - CPU/memory usage      - DLQ message count       |
+------------------------------------------------------+
```

### Fraud Scoring Pipeline (per transaction)

```
Transaction arrives
        |
        v
+-------+--------+     yes     +------------------+
| User blocked?   +----------->| Emit to           |
| (Redis SISMEMBER)|           | blocked-txns topic|
+-------+--------+            +------------------+
        | no
        v
+-------+--------+
| Load user       |
| profile (Redis) |
+-------+--------+
        |
        v
+-------+--------+
| Extract features|
| - Base: amount, |
|   card, time    |
| - Enriched:     |
|   velocity,     |
|   merchant risk,|
|   z-score,      |
|   temporal,     |
|   interactions  |
+-------+--------+
        |
        v
+-------+--------+     model     +------------------+
| ML model loaded?+---available-->| XGBoost          |
| (model_status)  |              | predict_proba()  |
+-------+--------+              | + apply scaler   |
        | unavailable            +--------+---------+
        v                                 |
+-------+--------+                        |
| Rule-based      |                       |
| fallback scoring|                       |
| (DEGRADED MODE) |                       |
+-------+--------+                        |
        |                                 |
        +<--------------------------------+
        |
        v
+-------+--------+
| Drift monitor   |
| (PSI check      |
|  every N txns)  |
+-------+--------+
        |
        v
+-------+--------+     score >= 0.7     +------------------+
| Threshold check +-------------------->| Publish to        |
|                 |                     | fraud-alerts topic|
+-------+--------+                     +------------------+
        |
        v
+------------------+
| Publish to        |
| detection-results |
| topic             |
+------------------+
```

### Kafka Topic Map

```
                    synthetic-transactions (12 parts, input)
                              |
                    +---------+---------+
                    |                   |
                    v                   v
            fraud-detector       persistence-consumer
                    |
         +----------+-----------+
         |          |           |
         v          v           v
   fraud-alerts  detection-  blocked-
                 results     transactions
         |
         v
   alert-processor
         |
    (blocks user
     in Redis)

   model-drift-alerts  -->  retraining-trigger  -->  model-retraining-jobs
   dead-letter-queue   -->  dlq-consumer (logs + persists for investigation)
```

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.13+
- 8GB+ RAM recommended

### Setup

```bash
# Clone and install
git clone <repository-url>
cd stream-sentinel
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Start infrastructure (Kafka, Redis, PostgreSQL, ClickHouse)
docker compose -f docker/docker-compose.yml up -d

# Verify connectivity
python src/kafka/test_connectivity.py
```

### Run the Pipeline

```bash
# Terminal 1: Generate synthetic transactions
python src/producers/synthetic_transaction_producer.py

# Terminal 2: Fraud detection (choose one)
python src/consumers/fraud_detector.py                    # single-message mode
python src/consumers/fraud_detector.py --batch            # batch mode (higher throughput)

# Terminal 3: Alert processing + user blocking
python src/consumers/alert_processor.py

# Terminal 4 (optional): Monitoring
docker compose -f docker/docker-compose.monitoring.yml up -d
# Grafana: http://localhost:3000  |  Prometheus: http://localhost:9090
# Kafka UI: http://localhost:8080 |  Redis Insight: http://localhost:8001
```

### Additional Components

```bash
# Dead letter queue consumer (process failed messages)
python src/consumers/dlq_consumer.py

# Persistence consumer (PostgreSQL + ClickHouse)
python src/consumers/persistence_consumer.py

# Drift-triggered retraining evaluator
python -m src.ml.online_learning.retraining_trigger

# Kafka consumer lag monitor
python -m src.kafka.lag_monitor

# Online learning demo
python scripts/online_learning_demo.py

# Model deployment CLI (ModelRegistry + A/B testing)
python scripts/deploy_model.py status
python scripts/deploy_model.py register --model-path models/new.pkl --version 2.0.0
python scripts/deploy_model.py promote --version 2.0.0 --strategy canary
python scripts/deploy_model.py ab-test --control 1.0.0 --treatment 2.0.0
```

### Deploy to Kubernetes

```bash
# Option A: Helm (recommended)
helm install stream-sentinel helm/stream-sentinel/

# Option B: Raw manifests
kubectl apply -f k8s/namespace.yaml -f k8s/serviceaccount.yaml
kubectl apply -f k8s/config/ -f k8s/consumers/ -f k8s/hpa/ -f k8s/monitoring/
```

See `docs/runbooks/` for on-call procedures (incident response, scaling, disaster recovery, model operations, troubleshooting).

## Key Features

### Fraud Detection
- **ML-Primary Scoring**: XGBoost model (99.42% production AUC on 200 features) is the primary scorer; rule-based fallback activates only when model is unavailable, with `model_status` tracking (`ml_primary` / `rules_fallback` / `loading`). Default alerting threshold is 0.3, tunable via `--threshold`.
- **Runtime Model Deployment**: fraud_detector loads from the Redis `ModelRegistry` first and falls back to filesystem pickle. A background refresh thread (60s) hot-swaps new versions under a threading lock without dropping in-flight messages.
- **Live A/B Testing**: When an experiment is active, users are assigned to control/treatment variants via consistent MD5 hashing, each variant is scored with its own model, and results are recorded for two-proportion z-test analysis.
- **Input Validation**: Transactions are validated at the Kafka ingestion point (`src/validation/transaction_validator.py`); malformed messages are rejected to the DLQ before feature extraction.
- **Distributed Tracing**: Correlation IDs flow across every Kafka topic hop (`src/tracing/`) so a single transaction can be traced from producer -> fraud detector -> alert processor -> persistence.
- **Blocking Enforcement**: Transactions from blocked users are rejected at the top of the pipeline (Redis `SISMEMBER` check) before any scoring occurs.
- **Enriched Features**: Velocity (txns/hour, txns/day), merchant risk score, amount z-score vs user history, temporal features (time_since_last, is_weekend), interaction features.
- **Batch Inference**: `--batch` flag buffers N messages for batch `predict_proba()`, with adaptive backpressure via FlowController.

### Observability
- **Prometheus Metrics**: Each consumer exposes metrics on dedicated ports -- fraud_detector=8000, alert_processor=8001, persistence_consumer=8002, enhanced_fraud_detector=8003, dlq_consumer=8004 -- including TPS, fraud score distribution, model inference latency, consumer lag, blocked transactions, model version, drift PSI.
- **Prometheus Alerting**: Production alert rules at `docker/prometheus/alert_rules.yml` cover consumer availability, lag, latency, fraud rate anomalies, model drift, and error rates.
- **Health Endpoints**: Every consumer exposes `/health` (liveness), `/health/ready` (readiness), and `/health/details` (verbose dependency status) via `src/monitoring/health.py` -- used by Kubernetes probes.
- **Grafana Dashboard**: Pre-built 12-panel dashboard at `docker/grafana/dashboards/fraud-detection.json`.
- **Structured Logging**: All consumers use JSON logging via `src/utils/logging.py` with contextual fields (transaction_id, user_id, correlation_id, consumer_group).
- **Dead Letter Queue**: Failed messages published to `dead-letter-queue` topic with error metadata; `dlq_consumer.py` persists for investigation.
- **Schema Registry**: Optional Avro validation via Confluent Schema Registry (falls back to JSON).

### ML Pipeline
- **Unified Feature Engineering**: `src/ml/features/feature_engineer.py` works for both batch training (DataFrame) and streaming inference (dict).
- **F2-Score Training Objective**: Training optimizes F2-score with cost-sensitive learning (replaces the prior ROC-AUC objective) so the pipeline weights recall over precision -- appropriate for fraud detection where false negatives are costlier than false positives.
- **Live Drift Detection**: PSI-based monitoring in fraud_detector, publishes to `model-drift-alerts` on drift.
- **Model Registry**: Redis-backed model versioning with filesystem fallback; training pipeline auto-registers new models.
- **Runtime Deployment CLI**: `scripts/deploy_model.py` provides `register`, `promote` (full/canary/blue-green/rolling), `rollback`, `ab-test`, and `status` commands for operating the registry without restarts.
- **Automated Retraining**: Drift alerts trigger retraining evaluation with guard conditions (min samples, cooldown, severity threshold).
- **A/B Testing**: Consistent hashing for stable variant assignment, two-proportion z-test for statistical analysis.

### Alert Response
- **Severity Classification**: CRITICAL (>= 0.9), HIGH (>= 0.7 + risk factors), MEDIUM (>= 0.4 + indicators), LOW
- **Automated Actions**: IMMEDIATE_BLOCK, AUTO_INVESTIGATE, MANUAL_REVIEW, NOTIFY_TEAM, LOG_ONLY
- **User Blocking**: Adds to Redis `blocked_users` set with 24h TTL; fraud_detector enforces on next transaction
- **SLA Tracking**: CRITICAL 1s, HIGH 5s, MEDIUM 30s, LOW 5min

### Production Deployment
- **Kubernetes Manifests** (`k8s/`): namespace, service account, ConfigMap, Secrets, 4 consumer Deployments with liveness/readiness probes, HPA (min=2/max=12 on CPU >70%), Prometheus + Grafana.
- **Helm Chart** (`helm/stream-sentinel/`): templated manifests with configurable `values.yaml` for all infrastructure endpoints.
- **Consumer Image**: `docker/Dockerfile.consumer` -- multi-stage build, non-root runtime user.
- **CI/CD**: `.github/workflows/{ci,performance,security}.yml` -- automated test, lint, performance, and dependency/secret scanning on every PR.
- **Operational Runbooks** (`docs/runbooks/`): 8 runbooks for on-call -- incident response, per-alert diagnosis, disaster recovery, scaling, model operations, troubleshooting, capacity planning.

### Synthetic Data Generation
- **IEEE-CIS Compliant**: 200+ features matching real dataset distributions (loaded from `data/processed/ieee_cis_analysis.json`)
- **Entity Tracking**: C-features (card/address counts), D-features (time deltas from entity events), M-features (match indicators)
- **Fraud Correlations**: Fraudulent transactions show correlated anomalies across amount, velocity, and time -- not independent random flags
- **Configurable**: All generation parameters centralized in `src/producers/config.py`

## Performance

| Metric | Measured |
|--------|----------|
| Transaction throughput (producer) | ~3,700 TPS 2-worker / 7,500+ TPS 4-worker (synthetic generation) |
| Fraud detector throughput per consumer | ~3,100 txn/sec on single-message path (with C++ inference) |
| C++ XGBoost inference latency | 0.15 ms / prediction (native `libxgboost.so` via pybind11 wrapper) |
| Feature extraction latency | 0.06 ms / call (200 features, precomputed encoder lookups) |
| Full scoring path latency (end-to-end) | 0.32 ms / message, P99 under 1 ms |
| ML model accuracy | 99.42% AUC in production (99.59% training) |
| Precision / Recall | 0.62 / 0.91 at threshold=0.5 |
| Alert response | < 5 ms routing with SLA tracking |

> Latency numbers reflect the built C++ inference extension (see
> `src/inference/cpp/`) with precomputed categorical encoder lookup
> tables and a cached StandardScaler. See
> [Model Performance](docs/model-performance-report.md) and
> [System Benchmarks](docs/system-benchmarks-report.md) for historical
> baselines.

## Project Structure

```
stream-sentinel/
+-- src/
|   +-- producers/
|   |   +-- synthetic_transaction_producer.py   # Transaction generation
|   |   +-- config.py                           # Generation parameters
|   +-- consumers/
|   |   +-- fraud_detector.py        # Core: ML scoring, blocking, batch mode, drift
|   |   +-- enhanced_fraud_detector.py  # Extended: online learning integration
|   |   +-- alert_processor.py       # Severity classification, user blocking
|   |   +-- persistence_consumer.py  # Batch persistence to PostgreSQL/ClickHouse
|   |   +-- dlq_consumer.py          # Dead letter queue processing
|   +-- kafka/
|   |   +-- config.py          # Centralized Kafka settings (env-aware)
|   |   +-- dlq.py             # Dead letter queue publisher
|   |   +-- schema_utils.py    # Optional Avro/Schema Registry
|   |   +-- lag_monitor.py     # Consumer lag + backpressure
|   +-- ml/
|   |   +-- features/
|   |   |   +-- feature_engineer.py   # Unified batch + streaming features
|   |   +-- training/core/
|   |   |   +-- pipeline_orchestrator.py     # Training coordination
|   |   |   +-- hyperparameter_optimizer.py  # Optuna integration
|   |   |   +-- data_processor.py            # Data preprocessing
|   |   |   +-- checkpoint_manager.py        # Training state
|   |   +-- online_learning/
|   |   |   +-- live_drift_monitor.py   # PSI-based drift in fraud_detector
|   |   |   +-- drift_detector.py       # Full statistical drift (KS, PSI, Chi-sq)
|   |   |   +-- model_registry.py       # Versioned model storage
|   |   |   +-- retraining_trigger.py   # Drift -> retrain evaluation
|   |   |   +-- ab_test_manager.py      # A/B testing framework
|   |   |   +-- feedback_processor.py   # Investigation feedback
|   |   +-- serving/
|   |       +-- model_export.py         # Pickle/JSON/ONNX export
|   +-- inference/
|   |   +-- fast_inference.py    # Python/C++ inference bridge
|   |   +-- export_model.py     # Pickle -> native XGBoost JSON
|   |   +-- cpp/                # Optional C++ acceleration
|   +-- persistence/
|   |   +-- database.py         # PostgreSQL + ClickHouse operations
|   |   +-- schemas.py          # Table definitions + migration
|   +-- monitoring/
|   |   +-- metrics.py          # Prometheus counters/histograms/gauges
|   |   +-- health.py           # /health, /health/ready, /health/details endpoints
|   +-- tracing/                # Distributed tracing with correlation IDs
|   +-- validation/             # Kafka-ingestion input validation
|   +-- utils/
|       +-- logging.py          # Structured JSON logging
+-- docker/
|   +-- docker-compose.yml              # Core infrastructure (8 services)
|   +-- docker-compose.monitoring.yml   # Prometheus + Grafana
|   +-- docker-compose.secure.yml       # Production variant (TLS, SASL)
|   +-- Dockerfile.consumer             # Multi-stage, non-root consumer image
|   +-- prometheus/
|   |   +-- prometheus.yml             # Scrape configs
|   |   +-- alert_rules.yml            # Production alert rules (16+ alerts)
|   +-- grafana/dashboards/             # Pre-built dashboards
+-- k8s/                # Kubernetes manifests (namespace, consumers, HPA, monitoring)
+-- helm/stream-sentinel/   # Helm chart for production deployment
+-- scripts/
|   +-- deploy_model.py         # ModelRegistry CLI (register/promote/rollback/ab-test)
|   +-- online_learning_demo.py
|   +-- scale_test_runner.py
|   +-- train_synthetic_model.py
+-- .github/workflows/  # CI/CD pipelines (ci, performance, security)
+-- tests/
|   +-- unit/           # Component tests (fraud scoring, features, alerts, profiles)
|   +-- integration/    # Kafka, Redis, PostgreSQL, ClickHouse
|   +-- e2e/            # Full pipeline workflows
|   +-- contract/       # Producer-consumer field compatibility
|   +-- chaos/          # Resilience (Redis down, model corrupt, Kafka backpressure)
|   +-- performance/    # 10k+ TPS sustained throughput validation
+-- models/             # Trained model artifacts (pickle, JSON, ONNX)
+-- schemas/            # Avro schema definitions (.avsc)
+-- data/               # IEEE-CIS dataset + analysis results
+-- docs/
|   +-- runbooks/       # Production operational runbooks (8 documents)
|   +-- project-logs/   # Phase-by-phase implementation history
|   +-- learning/       # Kafka and Redis learning resources
|   +-- (component READMEs)
+-- benchmarks/         # Performance profiling infrastructure
```

## Configuration

Environment-based config via `.env` files (see `.env.example`).

```bash
STREAM_SENTINEL_ENV=development    # development | staging | production
KAFKA_SERVERS=localhost:9092
SCHEMA_REGISTRY_URL=http://localhost:8081
REDIS_HOST=localhost
REDIS_PORT=6379
```

Kafka config is centralized in `src/kafka/config.py` with per-environment profiles. Producer data generation is configured in `src/producers/config.py`.

## Testing

```bash
# Full orchestrator
python tests/run_tests.py

# By category
pytest tests/unit/ -m unit
pytest tests/integration/ -m integration
pytest tests/e2e/ -m e2e
pytest tests/contract/
pytest tests/chaos/
pytest tests/performance/ -m performance

# Skip infrastructure-dependent tests
pytest -m "not requires_infrastructure"
```

See `tests/pytest.ini` for all markers. Tests requiring live services are marked `requires_infrastructure`.

## Documentation

- **[CLAUDE.md](CLAUDE.md)** -- Concise reference for AI coding assistants
- **[docs/](docs/README.md)** -- Architecture guides and learning resources
  - [Infrastructure](docs/infrastructure/README.md) -- Docker, Kafka, Redis, Kubernetes/Helm
  - [Fraud Detection](docs/fraud-detection/README.md) -- ML models, features, scoring, A/B testing
  - [Machine Learning](docs/machine-learning/README.md) -- Training pipeline, F2-score optimization, hyperparameter tuning
  - [Alert Response](docs/alert-response/README.md) -- Severity classification, automated actions
  - [Stream Processing](docs/stream-processing/README.md) -- Kafka patterns, consumer design
  - [State Management](docs/state-management/README.md) -- Redis patterns, user profiling
  - [Data Analysis](docs/data-analysis/README.md) -- IEEE-CIS analysis, synthetic generation
  - [Data Persistence](docs/data-persistence/README.md) -- PostgreSQL + ClickHouse hybrid
- **[Operational Runbooks](docs/runbooks/README.md)** -- Production on-call procedures (incident response, scaling, model operations, disaster recovery, troubleshooting, capacity planning)
- **[Online Learning System](src/ml/online_learning/README.md)** -- Drift detection, model registry, A/B testing
- **Performance Reports**: [Model Performance](docs/model-performance-report.md) | [System Benchmarks](docs/system-benchmarks-report.md) | [Synthetic Data Validation](data/SYNTHETIC_DATA_VALIDATION.md)
- **Learning Resources**: [Kafka](docs/learning/kafka.md) | [Redis](docs/learning/redis.md)
- **[Development Journal](docs/project-logs/README.md)** -- Phase-by-phase implementation history

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
