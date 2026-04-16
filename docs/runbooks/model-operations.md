# Model Operations Runbook

## Current Production Model

- **File:** `models/synthetic_fraud_model_production.pkl`
- **Algorithm:** XGBoost
- **Features:** 200 (full synthetic feature set)
- **AUC:** 99.42%
- **Threshold:** 0.5 (default)
- **Training:** GPU-accelerated (RTX 5070), 75 Optuna hyperparameter trials
- **Format:** Pickle dict with keys: `model`, `scaler`, `label_encoders`, `feature_names`

---

## Model Deployment

### Deploy a New Model

```bash
# 1. Validate the model file before deploying
python -c "
import pickle
import numpy as np

with open('models/new_model.pkl', 'rb') as f:
    data = pickle.load(f)

print('Type:', type(data))
if isinstance(data, dict):
    print('Keys:', list(data.keys()))
    model = data['model']
    print('Model type:', type(model))
    print('Features:', len(data.get('feature_names', [])))
    if hasattr(model, 'feature_names_in_'):
        print('Model features:', len(model.feature_names_in_))

    # Quick sanity check: predict on a zero vector
    n_features = len(data.get('feature_names', model.feature_names_in_))
    test_input = np.zeros((1, n_features))
    pred = model.predict_proba(test_input)
    print('Test prediction:', pred)
"

# 2. Back up current production model
cp models/synthetic_fraud_model_production.pkl \
   models/synthetic_fraud_model_production.pkl.bak.$(date +%Y%m%d%H%M)

# 3. Deploy new model
cp models/new_model.pkl models/synthetic_fraud_model_production.pkl

# 4. Restart fraud detector to load new model
# Send SIGTERM for graceful shutdown, then restart
kill -TERM $(pgrep -f fraud_detector.py)
sleep 5
python src/consumers/fraud_detector.py &

# 5. Verify model loaded
sleep 10
curl -s http://localhost:8000/metrics | grep 'model_status_info'
# Expected: model_status_info{status="ml_primary"} 1.0

curl -s http://localhost:8000/metrics | grep 'model_inference_duration_seconds_count'
```

### Deploy via Model Registry (Redis)

If the ModelRegistry is available, the fraud detector checks it before the filesystem.

```bash
# Register model in Redis
python -c "
from src.ml.online_learning.model_registry import ModelRegistry

registry = ModelRegistry()
registry.register_model(
    model_path='models/new_model.pkl',
    model_name='fraud_detector',
    version='2.0.0',
    stage='production',
    metrics={'auc': 0.9950, 'f2_score': 0.95}
)
print('Model registered successfully')
"

# The fraud detector will pick it up on next restart
# or if hot-reload is implemented, it will detect the registry update
```

---

## Model Rollback

### Rollback to Previous Model

```bash
# 1. Identify available backups
ls -la models/synthetic_fraud_model_production.pkl.bak.*

# 2. Restore the backup
cp models/synthetic_fraud_model_production.pkl.bak.YYYYMMDDHHM \
   models/synthetic_fraud_model_production.pkl

# 3. Restart fraud detector
kill -TERM $(pgrep -f fraud_detector.py)
sleep 5
python src/consumers/fraud_detector.py &

# 4. Verify
sleep 10
curl -s http://localhost:8000/metrics | grep 'model_status_info'
```

### Emergency Rollback to Rules-Based Scoring

If no valid model is available, the fraud detector automatically falls back to rules-based scoring. To force this:

```bash
# Option 1: Rename the model file so it is not found
mv models/synthetic_fraud_model_production.pkl models/synthetic_fraud_model_production.pkl.disabled

# Option 2: Restart the fraud detector with --no-ml flag (if supported)
# Otherwise, just removing the model file triggers rules_fallback

kill -TERM $(pgrep -f fraud_detector.py)
sleep 5
python src/consumers/fraud_detector.py &

# Verify rules fallback is active
curl -s http://localhost:8000/metrics | grep 'model_status_info'
# Expected: model_status_info{status="rules_fallback"} 1.0
```

---

## Retraining Trigger

The retraining pipeline is triggered by drift alerts. The `retraining_trigger.py` service consumes from `model-drift-alerts` and evaluates guard conditions before scheduling a retrain.

### Guard Conditions

1. **Minimum data volume:** At least 5,000 labeled samples since last retrain
2. **Cooldown period:** At most one retrain every 6 hours
3. **Severity threshold:** PSI >= 0.15 or severity >= "medium"

### Start Retraining Trigger

```bash
python -m src.ml.online_learning.retraining_trigger &
```

### Manual Retraining

```bash
# Trigger a retraining job manually by publishing to the jobs topic
docker exec stream-sentinel-kafka kafka-console-producer \
  --bootstrap-server localhost:9092 \
  --topic model-retraining-jobs <<EOF
{"trigger": "manual", "reason": "Operator-initiated retrain", "timestamp": "$(date -Iseconds)", "psi_score": 0.0}
EOF

# Run the training pipeline
python -c "
from src.ml.training.core.pipeline_orchestrator import PipelineOrchestrator
orchestrator = PipelineOrchestrator()
orchestrator.run()
"
```

### Check Retraining State

```bash
# Check retraining trigger state in Redis
redis-cli -p 6379 -n 4 KEYS "retrain_trigger:*"
redis-cli -p 6379 -n 4 GET "retrain_trigger:last_retrain_time"
redis-cli -p 6379 -n 4 GET "retrain_trigger:labeled_samples_count"

# Check for recent retraining jobs
docker exec stream-sentinel-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic model-retraining-jobs \
  --from-beginning --max-messages 5 --timeout-ms 5000

# Check drift alert history
docker exec stream-sentinel-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic model-drift-alerts \
  --from-beginning --max-messages 5 --timeout-ms 5000
```

---

## Model Validation Gate

After retraining, the new model must pass the validation gate before production deployment.

```bash
# Validate a retrained model against the current production model
python -c "
import pickle
import numpy as np

# Load current production model
with open('models/synthetic_fraud_model_production.pkl', 'rb') as f:
    prod_data = pickle.load(f)

# Load candidate model
with open('models/candidate_model.pkl', 'rb') as f:
    candidate_data = pickle.load(f)

prod_auc = prod_data.get('metrics', {}).get('auc', 0.9942)
candidate_auc = candidate_data.get('metrics', {}).get('auc', 0.0)

improvement_threshold = 0.005  # 0.5% improvement required
improvement = candidate_auc - prod_auc

print(f'Production AUC:  {prod_auc:.4f}')
print(f'Candidate AUC:   {candidate_auc:.4f}')
print(f'Improvement:     {improvement:.4f} (threshold: {improvement_threshold})')

if improvement >= improvement_threshold:
    print('PASS: Candidate model meets the improvement threshold')
else:
    print('FAIL: Candidate model does not meet the improvement threshold')
"
```

---

## A/B Testing

### Create a New A/B Test

```bash
python -c "
from src.ml.online_learning.ab_test_manager import ABTestManager, ModelVariant, VariantType

manager = ABTestManager()

# Define control (current production model)
control = ModelVariant(
    variant_id='control_v1',
    model_id='fraud_detector',
    model_version='1.0.0',
    variant_type=VariantType.CONTROL,
    traffic_allocation=0.9,  # 90% of traffic
)

# Define treatment (candidate model)
treatment = ModelVariant(
    variant_id='treatment_v2',
    model_id='fraud_detector',
    model_version='2.0.0',
    variant_type=VariantType.TREATMENT,
    traffic_allocation=0.1,  # 10% of traffic
)

experiment_id = manager.create_experiment(
    name='xgboost_v2_test',
    description='Testing retrained model with expanded features',
    variants=[control, treatment],
    min_sample_size=10000,
    significance_level=0.05,
)

print(f'Experiment created: {experiment_id}')
manager.start_experiment(experiment_id)
"
```

### Monitor A/B Test

```bash
# Check experiment status
python -c "
from src.ml.online_learning.ab_test_manager import ABTestManager
manager = ABTestManager()
status = manager.get_experiment_status('xgboost_v2_test')
print(status)
"

# Check from Redis directly
redis-cli -p 6379 -n 4 KEYS "ab_test:*"
redis-cli -p 6379 -n 4 HGETALL "ab_test:xgboost_v2_test"
```

### Stop and Evaluate A/B Test

```bash
python -c "
from src.ml.online_learning.ab_test_manager import ABTestManager
manager = ABTestManager()
result = manager.evaluate_experiment('xgboost_v2_test')
print(f'Decision: {result.decision}')
print(f'P-value: {result.p_value}')
print(f'Effect size: {result.effect_size}')
manager.stop_experiment('xgboost_v2_test')
"
```

---

## Drift Investigation

### Check Current Drift Status

```bash
# PSI metric
curl -s http://localhost:8000/metrics | grep 'fraud_model_drift_psi'

# Drift detection run counts
curl -s http://localhost:8000/metrics | grep 'drift_detection_runs_total'

# Score distribution
curl -s http://localhost:8000/metrics | grep 'fraud_score_distribution'

# Check drift monitor internal status
# (if the fraud detector exposes a health endpoint with drift info)
curl -s http://localhost:8000/health 2>/dev/null | python3 -m json.tool
```

### Investigate Drift Root Cause

```bash
# 1. Check if input data distribution has changed
# Compare recent fraud predictions vs historical
curl -s http://localhost:8000/metrics | grep 'fraud_predictions_total'

# 2. Check if a specific feature shifted
# Query ClickHouse for feature distribution changes
curl -s "http://localhost:8123/?query=SELECT+feature_name,+avg(feature_value),+stddevPop(feature_value)+FROM+stream_sentinel.fraud_features+WHERE+timestamp+>+now()-3600+GROUP+BY+feature_name+ORDER+BY+feature_name"

# 3. Check baseline distribution in Redis
redis-cli -p 6379 -n 4 GET "drift_monitor:baseline" | python3 -m json.tool

# 4. Check for producer changes (new merchant categories, payment methods, etc.)
docker exec stream-sentinel-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic synthetic-transactions \
  --max-messages 10 --timeout-ms 5000

# 5. If drift is from legitimate data evolution, reset baseline
redis-cli -p 6379 -n 4 DEL "drift_monitor:baseline"
# Monitor will recalibrate on next 1000 transactions
```

### Reset Drift Baseline

```bash
# Clear the Redis-stored baseline
redis-cli -p 6379 -n 4 DEL "drift_monitor:baseline"

# The LiveDriftMonitor will recalibrate after collecting check_interval (1000) scores
# Monitor for the new PSI reading
sleep 120  # wait for recalibration
curl -s http://localhost:8000/metrics | grep 'fraud_model_drift_psi'
```

---

## Model File Management

### Model File Locations

| File | Purpose |
|------|---------|
| `models/synthetic_fraud_model_production.pkl` | Active production model |
| `models/synthetic_fraud_model_production.pkl.bak.*` | Timestamped backups |
| Redis key `model_registry:active_model:production` | Registry-managed model reference |
| Redis db 4 `drift_monitor:baseline` | Drift detection baseline |

### Model Health Checks

```bash
# Verify model file exists and is readable
ls -la models/synthetic_fraud_model_production.pkl

# Verify model loads correctly
python -c "
import pickle
with open('models/synthetic_fraud_model_production.pkl', 'rb') as f:
    data = pickle.load(f)
print('OK - Model type:', type(data.get('model') if isinstance(data, dict) else data))
"

# Verify model scoring works
python -c "
import pickle, numpy as np
with open('models/synthetic_fraud_model_production.pkl', 'rb') as f:
    data = pickle.load(f)
model = data['model'] if isinstance(data, dict) else data
n = len(model.feature_names_in_) if hasattr(model, 'feature_names_in_') else 200
pred = model.predict_proba(np.zeros((1, n)))
print(f'OK - Prediction shape: {pred.shape}, value: {pred[0]}')
"

# Check Prometheus model status
curl -s http://localhost:8000/metrics | grep 'model_status_info'
curl -s http://localhost:8000/metrics | grep 'current_model_version'
curl -s http://localhost:8000/metrics | grep 'model_loads_total'
```

### Export for C++ Inference

```bash
# Convert pickle to native XGBoost JSON format for C++ acceleration
python src/inference/export_model.py \
  --input models/synthetic_fraud_model_production.pkl \
  --output models/fraud_model_native.json

# Build the C++ inference engine
cd src/inference/cpp && make

# The fraud detector will automatically try C++ inference if available
```
