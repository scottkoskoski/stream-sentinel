# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Stream Sentinel is a real-time fraud detection system built on event-driven streaming architecture. It processes financial transactions through Kafka, applies ML-based fraud scoring (XGBoost/LightGBM), manages user behavioral profiles in Redis, and persists results to PostgreSQL and ClickHouse.

## Development Commands

### Infrastructure
```bash
# Start all services (Kafka, Redis, PostgreSQL, ClickHouse, etc.)
docker compose -f docker/docker-compose.yml up -d

# Start monitoring stack (Prometheus + Grafana)
docker compose -f docker/docker-compose.monitoring.yml up -d

# Start secure/production variant
docker compose -f docker/docker-compose.secure.yml up -d

# Verify Kafka connectivity
python src/kafka/test_connectivity.py
```

### Running Tests
```bash
# Full test orchestrator (preferred entry point)
python tests/run_tests.py

# Run by category
pytest tests/unit/ -m unit
pytest tests/integration/ -m integration
pytest tests/e2e/ -m e2e
pytest tests/performance/ -m performance
pytest tests/chaos/ -m chaos
pytest tests/contract/

# Run a single test file
pytest tests/unit/test_fraud_scoring.py -v

# Run a single test
pytest tests/unit/test_fraud_scoring.py::TestClassName::test_name -v

# Run by marker (see tests/pytest.ini for all markers)
pytest -m "kafka"
pytest -m "ml"
pytest -m "online_learning"
pytest -m "not requires_infrastructure"  # skip tests needing Docker stack
```

pytest config is in `tests/pytest.ini`. Tests requiring live services are marked `requires_infrastructure`.

### Code Quality
```bash
black src/ tests/          # formatting
isort src/ tests/          # import sorting
flake8 src/ tests/         # linting
```

### Running Components
```bash
# Transaction producer (generates synthetic data at 10k+ TPS)
python src/producers/synthetic_transaction_producer.py

# Fraud detection consumer (single-message mode, default)
python src/consumers/fraud_detector.py

# Fraud detection consumer (batch mode for higher throughput)
python src/consumers/fraud_detector.py --batch --batch-size 32 --batch-timeout-ms 100

# Override default fraud threshold (default: 0.3)
python src/consumers/fraud_detector.py --threshold 0.5

# Enhanced fraud detector (alternate variant with online learning integration)
python src/consumers/enhanced_fraud_detector.py

# Alert processor
python src/consumers/alert_processor.py

# Dead letter queue consumer
python src/consumers/dlq_consumer.py

# Kafka lag monitor (standalone)
python -m src.kafka.lag_monitor

# Retraining trigger (listens for drift alerts)
python -m src.ml.online_learning.retraining_trigger

# Model deployment CLI (register / promote / rollback / ab-test / status)
python scripts/deploy_model.py register --model-path models/new.pkl --version 2.0.0
python scripts/deploy_model.py promote --version 2.0.0 --strategy canary
python scripts/deploy_model.py ab-test --control 1.0.0 --treatment 2.0.0

# Online learning demo
python scripts/online_learning_demo.py
```

### Production Deployment

```bash
# Deploy to Kubernetes via Helm (k8s/ + helm/stream-sentinel/)
helm install stream-sentinel helm/stream-sentinel/

# Or apply raw manifests
kubectl apply -f k8s/namespace.yaml -f k8s/serviceaccount.yaml
kubectl apply -f k8s/config/ -f k8s/consumers/ -f k8s/hpa/ -f k8s/monitoring/
```

Consumer image is built from `docker/Dockerfile.consumer` (multi-stage, non-root). CI/CD pipelines live in `.github/workflows/` (ci.yml, performance.yml, security.yml). Operational runbooks are under `docs/runbooks/`.

## Architecture

### Data Flow
```
Producers -> Kafka -> Fraud Detection Consumers -> Alerts/Persistence
                          |
                    ML Inference (XGBoost, primary)
                    Rules-based (fallback)
                          |
                  Redis (user profiles, blocked users, drift baseline)
                          |
              PostgreSQL (alerts) + ClickHouse (analytics)
                          |
              Prometheus (metrics) -> Grafana (dashboards)
```

### Scoring Pipeline (fraud_detector.py)
1. Check `blocked_users` Redis set -- skip scoring if blocked, emit to `blocked-transactions` topic
2. Load user profile from Redis
3. Extract features + enriched features (velocity, merchant risk, z-score, temporal, interactions)
4. Score via ML model (primary) or rule-based (fallback if model unavailable)
5. Apply scaler if one was loaded with model
6. Feed score to live drift monitor (PSI check every N transactions)
7. Publish alerts to `fraud-alerts`, results to `fraud-detection-results`
8. Failed messages go to dead letter queue

### Source Layout (`src/`)
- **producers/** - Transaction data generation. `config.py` centralizes all generation parameters (fraud rates, feature distributions, entity tracking).
- **consumers/** - Stream consumers:
  - `fraud_detector.py` - Core consumer with ML-primary scoring (ModelRegistry + filesystem fallback), blocking enforcement, batch mode, drift monitoring, live A/B testing
  - `enhanced_fraud_detector.py` - Alternate variant with extended online learning integration
  - `alert_processor.py` - Severity classification, response actions, user blocking
  - `persistence_consumer.py` - Batch persistence to PostgreSQL/ClickHouse
  - `dlq_consumer.py` - Dead letter queue processing
- **kafka/** - `config.py` (centralized settings), `dlq.py` (DLQ publisher), `schema_utils.py` (optional Avro/Schema Registry), `lag_monitor.py` (backpressure monitoring)
- **ml/** - Machine learning subsystem:
  - `features/feature_engineer.py` - Unified feature engineering for both batch training and streaming inference
  - `training/core/` - Modular training pipeline with `pipeline_orchestrator.py` and `hyperparameter_optimizer.py` (Optuna, F2-score with cost-sensitive learning)
  - `online_learning/` - Drift detection (`live_drift_monitor.py`), model registry, A/B testing, feedback collection, `retraining_trigger.py`
  - `serving/` - Model export (pickle, JSON, ONNX)
- **inference/** - Python/C++ hybrid inference. `export_model.py` converts pickle to native XGBoost JSON for C++ path.
- **persistence/** - PostgreSQL and ClickHouse database layer with schema definitions
- **monitoring/** - Prometheus metrics (`metrics.py`) and health endpoints (`health.py` serves `/health`, `/health/ready`, `/health/details`). Each consumer exposes metrics on a dedicated port: fraud_detector=8000, alert_processor=8001, persistence_consumer=8002, enhanced_fraud_detector=8003, dlq_consumer=8004.
- **tracing/** - Distributed tracing with correlation IDs across the Kafka topic chain (`correlation.py`, `traced_consume`, `traced_produce`).
- **validation/** - Transaction input validation at Kafka consumer ingestion (`transaction_validator.py`); invalid messages are rejected to DLQ before scoring.
- **utils/** - `logging.py` provides structured JSON logging across all consumers

### Kafka Topics
- `synthetic-transactions` - Input transactions (12 partitions)
- `fraud-alerts` - Fraud alerts for alert processor
- `fraud-detection-results` - Full results for persistence
- `blocked-transactions` - Transactions from blocked users
- `model-drift-alerts` - PSI drift detection alerts
- `model-retraining-jobs` - Retraining trigger messages
- `dead-letter-queue` - Failed message processing

### Infrastructure (Docker Compose)
Core: Zookeeper, Kafka (9092), Schema Registry, Kafka UI (8080), Redis (6379), Redis Insight (8001), PostgreSQL, ClickHouse.
Monitoring: Prometheus (9090), Grafana (3000) with pre-built fraud detection dashboard. Prometheus alert rules live at `docker/prometheus/alert_rules.yml`.

### Kubernetes / Helm
- `k8s/` - raw manifests (namespace, service account, ConfigMap, Secrets, 4 consumer Deployments with health probes, HPA min=2/max=12, Prometheus+Grafana)
- `helm/stream-sentinel/` - Helm chart with templated manifests and configurable `values.yaml` for all infrastructure endpoints
- `docker/Dockerfile.consumer` - multi-stage, non-root runtime image for all consumers

### CI/CD
GitHub Actions at `.github/workflows/`: `ci.yml` (lint/test/build), `performance.yml` (throughput checks), `security.yml` (dependency/secret scanning).

### Operational Runbooks
Production on-call references live under `docs/runbooks/`:
- `incident-response.md`, `alert-response.md`, `disaster-recovery.md`
- `scaling.md`, `model-operations.md`, `troubleshooting.md`, `capacity-planning.md`

### Configuration
Environment-based config via `.env` files (see `.env.example`). Kafka config is centralized in `src/kafka/config.py` with development/staging/production profiles. Producer data generation is configured in `src/producers/config.py`.

## Key Technical Details

- **Python 3.13+** required
- **Kafka**: confluent-kafka client, 12 partitions, LZ4 compression. Schema Registry integration is optional (falls back to JSON).
- **ML models**: XGBoost (99.42% production AUC, 200 features), trained on full-feature synthetic data with GPU (RTX 5070) + 75 Optuna trials. Training pipeline optimizes **F2-score** with cost-sensitive learning (replaces prior ROC-AUC objective) to weight recall over precision. Model at `models/synthetic_fraud_model_production.pkl`. Loaded from ModelRegistry (Redis) first, with filesystem fallback. A background refresh thread hot-swaps new registry versions every 60s under a threading lock. `model_status` tracks: `ml_primary` / `rules_fallback` / `loading`. Label encoders saved in pickle for proper categorical encoding at inference. Default fraud threshold is 0.3 (tunable via `--threshold`).
- **A/B testing**: Wired into the streaming fraud detector via `ABTestManager`. Users are assigned to control/treatment variants by consistent MD5 hashing when an experiment is active. Use `scripts/deploy_model.py` to register/promote/rollback models and create experiments.
- **Feature engineering**: Velocity, merchant risk, amount z-score, temporal, interaction features. Unified module works for both training (DataFrame) and inference (dict).
- **Drift detection**: PSI-based live monitoring in fraud_detector, configurable check interval. Alerts trigger retraining evaluation with guard conditions (min samples, cooldown, severity threshold).
- **C++ inference**: Optional native XGBoost acceleration. Use `src/inference/export_model.py` to convert pickle to native format, build via `src/inference/cpp/Makefile`.
- **Batch mode**: `--batch` flag enables buffered inference (configurable batch_size and timeout). FlowController provides adaptive backpressure.
- **Structured logging**: All consumers use JSON logging via `src/utils/logging.py` with contextual fields (transaction_id, user_id, consumer_group).
- **Performance target**: 10k+ TPS sustained, <100ms P99 latency
