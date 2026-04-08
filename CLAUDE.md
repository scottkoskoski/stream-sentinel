# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Stream Sentinel is a real-time fraud detection system built on event-driven streaming architecture. It processes financial transactions through Kafka, applies ML-based fraud scoring (XGBoost/LightGBM), manages user behavioral profiles in Redis, and persists results to PostgreSQL and ClickHouse.

## Development Commands

### Infrastructure
```bash
# Start all services (Kafka, Redis, PostgreSQL, ClickHouse, etc.)
docker compose -f docker/docker-compose.yml up -d

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

# Run a single test file
pytest tests/unit/test_fraud_scoring.py -v

# Run a single test
pytest tests/unit/test_fraud_scoring.py::TestClassName::test_name -v

# Run by marker (see tests/pytest.ini for all 27 markers)
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

# Fraud detection consumer
python src/consumers/fraud_detector.py

# Enhanced fraud detector (with online learning)
python src/consumers/enhanced_fraud_detector.py

# Alert processor
python src/consumers/alert_processor.py

# Online learning demo
python scripts/online_learning_demo.py
```

## Architecture

### Data Flow
```
Producers -> Kafka -> Fraud Detection Consumers -> Alerts/Persistence
                          |
                    ML Inference (XGBoost)
                          |
                  Redis (user profiles, state)
                          |
              PostgreSQL (alerts) + ClickHouse (analytics)
```

### Source Layout (`src/`)
- **producers/** - Transaction data generation (synthetic + IEEE-CIS dataset)
- **consumers/** - Stream consumers: `fraud_detector.py` (core), `enhanced_fraud_detector.py` (with online learning), `alert_processor.py`
- **kafka/** - Kafka client configuration (`config.py` centralizes all Kafka settings with environment awareness)
- **ml/** - Machine learning subsystem:
  - `training/core/` - Modular training pipeline with `pipeline_orchestrator.py` and `hyperparameter_optimizer.py` (Optuna)
  - `online_learning/` - Adaptive ML: drift detection, feedback collection, A/B testing, model registry
  - `serving/` - Model export (pickle, JSON, ONNX)
- **inference/** - Python/C++ hybrid inference engine. `cpp/simple_xgboost_wrapper.cpp` provides native acceleration
- **persistence/** - PostgreSQL and ClickHouse database layer
- **monitoring/** - System metrics and observability
- **data/** - Dataset analysis and feature engineering

### Infrastructure (Docker Compose)
8 services: Zookeeper, Kafka (port 9092), Schema Registry, Kafka UI (port 8080), Redis (port 6379), Redis Insight (port 8001), PostgreSQL, ClickHouse.

### Configuration
Environment-based config via `.env` files (see `.env.example`). Kafka config is centralized in `src/kafka/config.py` with development/staging/production profiles.

## Key Technical Details

- **Python 3.13+** required
- **Kafka**: confluent-kafka client, 12 partitions, LZ4 compression
- **ML models**: XGBoost primary (97.05% CV AUC), LightGBM and CatBoost also available
- **C++ inference**: 630x speedup over pure Python (~0.2ms latency), built via `src/inference/cpp/`
- **Online learning**: Incremental model updates with statistical drift detection (KS, PSI, Chi-square tests)
- **Performance target**: 10k+ TPS sustained, <100ms P99 latency
