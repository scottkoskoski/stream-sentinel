# Real-Time Fraud Detection Pipeline

The fraud detection consumer (`src/consumers/fraud_detector.py`) is the core runtime component of Stream Sentinel. It consumes transactions from Kafka, enforces user blocking, extracts enriched features, scores via ML model (with rules-based fallback), monitors for model drift, and publishes alerts.

## Pipeline Overview

```
synthetic-transactions (Kafka)
        |
        v
 +--- Blocked user check (Redis SISMEMBER) ---+
 |                                             |
 | blocked                              not blocked
 v                                             v
blocked-transactions topic              Load user profile (Redis)
                                               |
                                               v
                                        Feature engineering
                                        (FeatureEngineer)
                                               |
                                               v
                                        ML scoring (XGBoost primary)
                                        Rules-based (fallback only)
                                               |
                                               v
                                        Apply scaler (if loaded)
                                               |
                                               v
                                        Live drift monitor (PSI)
                                               |
                                               v
                                  +--- score >= threshold (0.7)? ---+
                                  |                                 |
                                  v                                 v
                           fraud-alerts topic              fraud-detection-results
                                  |
                                  v
                           fraud-detection-results
```

Failed messages at any stage are routed to the dead letter queue via `src/kafka/dlq.py`.

## Blocking Enforcement

Before any scoring occurs, the consumer checks whether the user is in the Redis `blocked_users` set using SISMEMBER. If the user is blocked:

- The transaction is emitted to the `blocked-transactions` Kafka topic.
- Scoring is skipped entirely -- no features are extracted, no model inference runs.
- The consumer moves on to the next message.

Users are added to `blocked_users` by the alert processor (see [Alert Response](../alert-response/README.md)) with a 24-hour TTL. This creates a closed feedback loop: the alert processor blocks, the fraud detector enforces.

## Feature Engineering

Feature extraction is handled by `FeatureEngineer` (`src/ml/features/feature_engineer.py`), a unified module that works for both batch training (DataFrame input) and streaming inference (dict input).

### Base Features

- `transaction_amt`, `transaction_hour`, `transaction_day`
- Card attributes (`card1` through `card6`), address fields, email domains
- IEEE-CIS dataset fields mapped to model input

### Enriched Features

The enriched feature set provides behavioral context from the user's Redis profile:

| Category | Features | Description |
|----------|----------|-------------|
| **Velocity** | transactions per hour, per day | Rate of activity over sliding windows |
| **Merchant risk** | merchant risk score | Risk level associated with the merchant category |
| **Amount z-score** | z-score vs user history | How far the current amount deviates from the user's mean |
| **Temporal** | time_since_last, is_weekend | Time gap from previous transaction, weekend flag |
| **Interaction** | combined feature crosses | Multiplicative interactions between risk signals |

These enriched features are computed from the user profile stored in Redis (30-day TTL, automatic daily counter resets).

## Scoring

### ML-Primary Scoring

The primary scoring path uses an XGBoost model (97.05% CV AUC). The `model_status` field tracks the current state:

- `ml_primary` -- ML model loaded and serving predictions
- `rules_fallback` -- ML model unavailable, using rule-based scoring
- `loading` -- Model is being loaded

The model is loaded from the ModelRegistry (Redis) with filesystem fallback. If a scaler was saved alongside the model, it is applied to the feature vector before inference.

### Rules-Based Fallback

Rule-based scoring activates only when the ML model is unavailable (load failure, missing model file, inference error). It is not a separate scoring mode -- it is a fallback. The rules evaluate:

- Amount vs average ratio (thresholds at 2x, 3x, 5x)
- High amount flag (>$1000)
- Unusual hour flag (before 6 AM or after 10 PM)
- Rapid transaction flag (<5 min since last)
- Velocity score and daily transaction count

Peak fraud hours in the synthetic data are 2-4 AM (card-not-present fraud pattern).

## Drift Monitoring

The fraud detector includes a live drift monitor that uses Population Stability Index (PSI) to detect distribution shifts in model predictions. After every N transactions (configurable), the monitor compares the current score distribution against a baseline stored in Redis.

When drift exceeds the configured threshold, the consumer publishes an alert to the `model-drift-alerts` topic. Downstream, the `retraining_trigger.py` service evaluates whether retraining is warranted based on guard conditions (minimum sample count, cooldown period, severity threshold).

## Batch Mode

The consumer supports batch inference via the `--batch` flag:

```bash
python src/consumers/fraud_detector.py --batch --batch-size 32 --batch-timeout-ms 100
```

In batch mode, the consumer buffers messages and runs inference on batches for higher throughput. A FlowController provides adaptive backpressure to prevent memory exhaustion under load.

## Kafka Topics

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `synthetic-transactions` | Input | Raw transactions (12 partitions) |
| `fraud-alerts` | Output | Alerts for score >= threshold |
| `fraud-detection-results` | Output | Full results for persistence layer |
| `blocked-transactions` | Output | Transactions from blocked users |
| `model-drift-alerts` | Output | PSI drift detection alerts |
| `dead-letter-queue` | Output | Failed message processing |

## Observability

### Prometheus Metrics

Each consumer instance exposes Prometheus metrics on a dedicated port (fraud detector uses port 8000). Metrics include processing latency histograms, throughput counters, fraud alert counters, and model status gauges.

### Structured Logging

All logging uses `src/utils/logging.py` which emits structured JSON with contextual fields: `transaction_id`, `user_id`, `consumer_group`, `model_status`, and processing timestamps.

## Running

```bash
# Single-message mode (default)
python src/consumers/fraud_detector.py

# Batch mode
python src/consumers/fraud_detector.py --batch --batch-size 32 --batch-timeout-ms 100

# Enhanced variant with online learning integration
python src/consumers/enhanced_fraud_detector.py
```

### Configuration

- `consumer_group` -- Kafka consumer group (default: `fraud-detection-group`)
- `fraud_threshold` -- Alert threshold, 0.0-1.0 (default: 0.7)
- `model_path` -- Path to model pickle file
- Kafka and Redis connection settings via environment variables (see `.env.example`)

C++ inference acceleration is available as an optional optimization. Use `src/inference/export_model.py` to convert the model to native XGBoost JSON format and build via `src/inference/cpp/Makefile`.

## Performance Targets

- **Throughput**: 10,000+ TPS sustained
- **Latency**: <100ms P99
- **Model AUC**: 97.05% (XGBoost, validated on IEEE-CIS dataset)
- **Graceful degradation**: Automatic fallback to rules-based scoring if ML model fails

## Related Documentation

- [Alert Response System](../alert-response/README.md) -- Downstream alert processing and user blocking
- [Machine Learning Pipeline](../machine-learning/README.md) -- Model training and hyperparameter optimization
- [State Management](../state-management/README.md) -- Redis patterns for user profiles and blocking
