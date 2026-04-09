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

# Enhanced fraud detector (with online learning)
python src/consumers/enhanced_fraud_detector.py

# Alert processor
python src/consumers/alert_processor.py

# Dead letter queue consumer
python src/consumers/dlq_consumer.py

# Kafka lag monitor (standalone)
python -m src.kafka.lag_monitor

# Retraining trigger (listens for drift alerts)
python -m src.ml.online_learning.retraining_trigger

# Online learning demo
python scripts/online_learning_demo.py
```

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
  - `fraud_detector.py` - Core consumer with ML-primary scoring, blocking enforcement, batch mode, drift monitoring
  - `enhanced_fraud_detector.py` - Extended variant with online learning integration
  - `alert_processor.py` - Severity classification, response actions, user blocking
  - `persistence_consumer.py` - Batch persistence to PostgreSQL/ClickHouse
  - `dlq_consumer.py` - Dead letter queue processing
- **kafka/** - `config.py` (centralized settings), `dlq.py` (DLQ publisher), `schema_utils.py` (optional Avro/Schema Registry), `lag_monitor.py` (backpressure monitoring)
- **ml/** - Machine learning subsystem:
  - `features/feature_engineer.py` - Unified feature engineering for both batch training and streaming inference
  - `training/core/` - Modular training pipeline with `pipeline_orchestrator.py` and `hyperparameter_optimizer.py` (Optuna)
  - `online_learning/` - Drift detection (`live_drift_monitor.py`), model registry, A/B testing, feedback collection, `retraining_trigger.py`
  - `serving/` - Model export (pickle, JSON, ONNX)
- **inference/** - Python/C++ hybrid inference. `export_model.py` converts pickle to native XGBoost JSON for C++ path.
- **persistence/** - PostgreSQL and ClickHouse database layer with schema definitions
- **monitoring/** - Prometheus metrics (counters, histograms, gauges). Each consumer exposes metrics on a dedicated port (8000-8003).
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
Monitoring: Prometheus (9090), Grafana (3000) with pre-built fraud detection dashboard.

### Configuration
Environment-based config via `.env` files (see `.env.example`). Kafka config is centralized in `src/kafka/config.py` with development/staging/production profiles. Producer data generation is configured in `src/producers/config.py`.

## Key Technical Details

- **Python 3.13+** required
- **Kafka**: confluent-kafka client, 12 partitions, LZ4 compression. Schema Registry integration is optional (falls back to JSON).
- **ML models**: XGBoost (99.42% production AUC, 200 features), trained on full-feature synthetic data with GPU (RTX 5070) + 75 Optuna trials. Model at `models/synthetic_fraud_model_production.pkl`. Loaded from ModelRegistry (Redis) or filesystem fallback. `model_status` tracks: `ml_primary` / `rules_fallback` / `loading`. Label encoders saved in pickle for proper categorical encoding at inference. Default fraud threshold is 0.5.
- **Feature engineering**: Velocity, merchant risk, amount z-score, temporal, interaction features. Unified module works for both training (DataFrame) and inference (dict).
- **Drift detection**: PSI-based live monitoring in fraud_detector, configurable check interval. Alerts trigger retraining evaluation with guard conditions (min samples, cooldown, severity threshold).
- **C++ inference**: Optional native XGBoost acceleration. Use `src/inference/export_model.py` to convert pickle to native format, build via `src/inference/cpp/Makefile`.
- **Batch mode**: `--batch` flag enables buffered inference (configurable batch_size and timeout). FlowController provides adaptive backpressure.
- **Structured logging**: All consumers use JSON logging via `src/utils/logging.py` with contextual fields (transaction_id, user_id, consumer_group).
- **Performance target**: 10k+ TPS sustained, <100ms P99 latency
