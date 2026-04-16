# Real-Time Fraud Detection Pipeline

The fraud detection consumer (`src/consumers/fraud_detector.py`) is the core runtime component of Stream Sentinel. It validates incoming transactions, enforces user blocking, extracts enriched features, scores via an ML model loaded from the Redis `ModelRegistry` (with filesystem fallback and optional A/B variant routing), emits distributed-tracing correlation IDs downstream, monitors for model drift, and publishes alerts. A background thread hot-swaps new registry versions without restart.

## Pipeline Overview

```
synthetic-transactions (Kafka)
        |
        v
 Input validation (src/validation/transaction_validator.py)
        |
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
                                        A/B variant assignment
                                        (if experiment active)
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
                                  +--- score >= threshold (0.3)? ---+
                                  |                                 |
                                  v                                 v
                           fraud-alerts topic              fraud-detection-results
```

Correlation IDs (via `src/tracing/`) are stamped on every output message so a transaction can be traced end-to-end across Kafka hops. Failed messages at any stage are routed to the dead letter queue via `src/kafka/dlq.py`.

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

The primary scoring path uses an XGBoost model (99.42% production AUC on 200 features, trained with F2-score + cost-sensitive learning). The `model_status` field tracks the current state:

- `ml_primary` -- ML model loaded and serving predictions
- `rules_fallback` -- ML model unavailable, using rule-based scoring
- `loading` -- Model is being loaded

### Model Loading and Hot-Swap

On startup, the detector tries the Redis-backed `ModelRegistry` first and falls back to `models/synthetic_fraud_model_production.pkl` if Redis is unavailable. A background thread refreshes the registry every 60 seconds; when it finds a newer production version, it hot-swaps the model, scaler, encoders, and feature names under a `threading.Lock` so in-flight scoring does not observe a partial state. If a scaler was saved alongside the model, it is applied to the feature vector before inference.

### A/B Test Variant Routing

When `ABTestManager` reports an active experiment, users are assigned to control or treatment variants via consistent MD5 hashing of `user_id`. Each variant is scored against its own pickled model (cached in-process); the emitted result includes `ab_test.variant_id` and `ab_test.experiment_id` for downstream statistical analysis. If the treatment model cannot be loaded, the path falls back to the control (production) model transparently. Experiments are created via `python scripts/deploy_model.py ab-test`.

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
- `fraud_threshold` -- Alert threshold, 0.0-1.0 (default: 0.3; CLI: `--threshold`)
- `model_path` -- Path to model pickle file (default: `models/synthetic_fraud_model_production.pkl`)
- Kafka and Redis connection settings via environment variables (see `.env.example`)

C++ inference acceleration is active by default (`enable_cpp_acceleration=True`). The pybind11 wrapper links against `libxgboost.so` via a baked-in RPATH so no `LD_LIBRARY_PATH` is needed at runtime. Use `src/inference/export_model.py` to convert the pickle to the native `_cpp.json` format the wrapper loads; see `src/inference/cpp/README.md` for local build instructions.

## Health and Metrics

- **Health endpoints** (via `src/monitoring/health.py`): `/health` (liveness), `/health/ready` (readiness -- returns 503 during startup grace period), `/health/details` (verbose dependency status). Used as Kubernetes liveness/readiness probes.
- **Prometheus metrics** on port 8000: processing latency histograms, throughput counters, fraud alert counters, model status gauge (1=ml_primary, 0=rules_fallback), model version info, drift PSI, A/B variant counts.

## Performance Targets

- **Throughput**: ~3,100 txn/sec per consumer on the single-message path with C++ inference; scales linearly with consumer replicas (HPA min=2, max=12).
- **Latency**: ~0.32 ms/message end-to-end scoring (C++ inference path + precomputed encoder/scaler lookups); P99 well under 1 ms on steady-state single-message mode.
- **Model AUC**: 99.42% (XGBoost, 200 features, production test set).
- **Graceful degradation**: Automatic fallback to Python `Booster.inplace_predict` if the C++ wrapper can't load; then rule-based scoring if the Python model is unavailable.

## Related Documentation

- [Alert Response System](../alert-response/README.md) -- Downstream alert processing and user blocking
- [Machine Learning Pipeline](../machine-learning/README.md) -- Model training and hyperparameter optimization
- [State Management](../state-management/README.md) -- Redis patterns for user profiles and blocking
- [Model Operations Runbook](../runbooks/model-operations.md) -- Deployment, rollback, A/B test setup
