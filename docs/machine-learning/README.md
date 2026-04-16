# Machine Learning Pipeline

Stream Sentinel uses XGBoost for real-time fraud detection. The current production model scores **99.42% AUC on 200 features** with training optimized for **F2-score** (recall-weighted) using cost-sensitive learning. The ML subsystem covers training, feature engineering, model export, online learning, runtime deployment, and production inference.

## Training Pipeline

Training is orchestrated by `src/ml/training/core/pipeline_orchestrator.py`, which coordinates data processing, hyperparameter optimization, model evaluation, checkpointing, and auto-registration to the model registry.

```
src/ml/training/
├── core/
│   ├── pipeline_orchestrator.py      # End-to-end training workflow
│   ├── data_processor.py             # Data loading and preprocessing
│   ├── hyperparameter_optimizer.py   # Optuna-based optimization
│   └── checkpoint_manager.py         # Model persistence and recovery
├── config/
│   └── training_config.py            # Training configuration
└── utils/
    ├── metrics.py                    # Evaluation metrics
    ├── logging.py                    # Training logging
    └── resource_manager.py           # Resource management
```

### Data Processing

`data_processor.py` handles data loading, cleaning, and train/validation splitting. It prepares DataFrames for the feature engineering step and manages class imbalance through stratified sampling.

### Hyperparameter Optimization

`hyperparameter_optimizer.py` uses Optuna with TPE sampling and MedianPruner for efficient search. The optimizer targets **F2-score** (β=2, weights recall 4x over precision) as the objective rather than ROC-AUC -- a deliberate choice for fraud detection where missed fraud is costlier than false positives. Cost-sensitive learning is applied via `scale_pos_weight` to handle the extreme class imbalance of fraud data.

Key search spaces include:

- `n_estimators`: 500-3000
- `max_depth`: 3-15
- `learning_rate`: 0.005-0.3 (log scale)
- `subsample`, `colsample_bytree`: 0.4-1.0
- `reg_alpha`, `reg_lambda`: 0-50 (log scale)
- `min_child_weight`: 0.1-20
- `scale_pos_weight`: 1-10 (class imbalance handling)

Validation uses StratifiedKFold cross-validation with early stopping. The optimizer supports convergence detection and trial pruning; failed trials are pruned from the study before final model selection.

### Checkpointing

`checkpoint_manager.py` saves intermediate training state (model, parameters, trial results) to enable recovery from interruptions. Checkpoints are written after each completed Optuna trial.

### Model Registration

After training completes, `pipeline_orchestrator.py` auto-registers the model to the Redis-backed model registry with semantic versioning and deployment metadata. The registry falls back to filesystem storage when Redis is unavailable.

## Model Performance

**Production XGBoost Model** (`models/synthetic_fraud_model_production.pkl`):
- Training AUC: 99.59%
- **Production AUC: 99.42%** (held-out test set)
- Precision/Recall at threshold=0.5: 0.62 / 0.91
- Features: 200 (full IEEE-CIS compatible feature set)
- Optimization objective: F2-score with cost-sensitive learning
- Hardware: GPU-accelerated XGBoost (RTX 5070) + 75 Optuna trials
- Validation: StratifiedKFold cross-validation

See `models/TRAINING_REPORT.md` and `docs/model-performance-report.md` for the full performance breakdown and feature importance rankings.

## Feature Engineering

Feature engineering is handled by `src/ml/features/feature_engineer.py`, a unified module that works for both batch training (DataFrame input) and streaming inference (dict input). This eliminates training/serving skew by using the same code path for both contexts.

### Feature Categories

**Velocity features:**
- `txns_per_hour`: transaction count in the last hour
- `txns_per_day`: transaction count in the last 24 hours

**Merchant risk:**
- `merchant_risk_score`: risk score based on merchant category

**Amount z-score:**
- `amount_zscore`: deviation of transaction amount from user average

**Temporal features:**
- `time_since_last`: seconds since user's last transaction
- `is_weekend`: weekend indicator
- `day_of_week`: day of week (0-6)
- `is_business_hours`: business hours indicator

**Interaction features:**
- `amount_x_hour_risk`: transaction amount multiplied by hour-of-day risk factor
- `velocity_x_amount_deviation`: velocity score multiplied by amount deviation

### Usage

```python
from ml.features.feature_engineer import FeatureEngineer

engineer = FeatureEngineer()

# Batch training (DataFrame)
features_df = engineer.extract_features(transactions_df)

# Streaming inference (dict)
features = engineer.extract_features_dict(transaction, user_profile)
```

## Model Export

`src/ml/serving/model_export.py` supports three export formats:

| Format | File | Use Case |
|--------|------|----------|
| Pickle | `.pkl` | Standard Python model loading, includes scaler and metadata |
| XGBoost JSON | `.json` | Native format for C++ inference path |
| ONNX | `.onnx` | Cross-platform interoperability |

### C++ Inference Path

For optional native acceleration, `src/inference/export_model.py` converts a pickle model to native XGBoost JSON format. The C++ inference engine is built via `src/inference/cpp/Makefile` and loaded at runtime when available.

```bash
# Convert model for C++ inference
python src/inference/export_model.py

# Build C++ inference engine
cd src/inference/cpp && make
```

The fraud detector (`src/consumers/fraud_detector.py`) loads the ML model at startup. If the model or its dependencies are unavailable, it falls back to rule-based scoring. The `model_status` field tracks the current mode: `ml_primary`, `rules_fallback`, or `loading`.

## Scoring Pipeline

The fraud detector processes each transaction through these steps:

1. Check `blocked_users` Redis set -- skip scoring if blocked, emit to `blocked-transactions` topic
2. Load user profile from Redis
3. Extract features using the unified feature engineer (velocity, merchant risk, z-score, temporal, interactions)
4. Score via ML model (primary) or rule-based engine (fallback)
5. Apply scaler if one was loaded with the model
6. Feed score to live drift monitor (PSI check every N transactions)
7. Publish alerts to `fraud-alerts`, results to `fraud-detection-results`
8. Failed messages go to the dead letter queue

Batch mode (`--batch` flag) buffers transactions for batched inference, configurable via `--batch-size` and `--batch-timeout-ms`.

## Online Learning

The online learning subsystem lives in `src/ml/online_learning/` and has its own detailed documentation at `src/ml/online_learning/README.md`. This section provides a brief overview.

```
src/ml/online_learning/
├── drift_detector.py               # KS, PSI, Chi-square drift tests
├── live_drift_monitor.py           # In-consumer PSI monitoring
├── feedback_processor.py           # Human feedback integration
├── incremental_learner.py          # Incremental model updates
├── ab_test_manager.py              # A/B testing framework
├── model_registry.py               # Redis-backed model versioning
├── retraining_trigger.py           # Drift-triggered retraining
└── online_learning_orchestrator.py # Coordination
```

### Drift Detection

Two layers of drift detection operate in the system:

**Live drift monitoring** (`live_drift_monitor.py`): Runs inside `fraud_detector.py`, performing PSI (Population Stability Index) checks every N transactions. When drift exceeds the configured threshold, an alert is published to the `model-drift-alerts` Kafka topic.

**Drift detector** (`drift_detector.py`): Supports multiple statistical tests -- Kolmogorov-Smirnov, PSI, and Chi-square -- for comprehensive distribution comparison between baseline and current data.

### Retraining Trigger

`retraining_trigger.py` consumes from `model-drift-alerts` and evaluates whether retraining is warranted based on guard conditions:

- Minimum sample count since last training
- Cooldown period between retraining runs
- Drift severity threshold

When conditions are met, it publishes a job to the `model-retraining-jobs` topic.

### Model Registry

`model_registry.py` provides Redis-backed model versioning with:

- Semantic versioning (major.minor.patch)
- Deployment state tracking (staging, production, retired)
- Filesystem fallback when Redis is unavailable
- Rollback to previous versions

### A/B Testing

`ab_test_manager.py` supports champion/challenger model comparison with configurable traffic splitting, statistical significance testing, and automatic promotion based on performance criteria.

### Feedback Processing

`feedback_processor.py` integrates human investigation outcomes back into the learning loop, enabling supervised corrections to model predictions.

## Running Components

```bash
# Train a model
python -m src.ml.training.core.pipeline_orchestrator

# Export model for C++ inference
python src/inference/export_model.py

# Run fraud detector (single-message mode)
python src/consumers/fraud_detector.py

# Run fraud detector (batch mode)
python src/consumers/fraud_detector.py --batch --batch-size 32 --batch-timeout-ms 100

# Run enhanced fraud detector with online learning
python src/consumers/enhanced_fraud_detector.py

# Run retraining trigger
python -m src.ml.online_learning.retraining_trigger

# Run online learning demo
python scripts/online_learning_demo.py

# Deploy a model via the ModelRegistry CLI
python scripts/deploy_model.py register --model-path models/new.pkl --version 2.0.0
python scripts/deploy_model.py promote --version 2.0.0 --strategy canary
python scripts/deploy_model.py rollback --version 1.0.0
python scripts/deploy_model.py ab-test --control 1.0.0 --treatment 2.0.0
python scripts/deploy_model.py status
```

---

**Navigation:** [Documentation Index](../README.md) | [Online Learning Details](../../src/ml/online_learning/README.md) | [Model Operations Runbook](../runbooks/model-operations.md)
