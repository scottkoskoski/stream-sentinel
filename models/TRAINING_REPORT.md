# Fraud Detection Model Training Report

**Report Date:** 2026-04-08
**Model Version:** pipeline_20250829_143814 (checkpoint 64d37e733cd958de)

## Model Architecture

- **Algorithm:** XGBoost (XGBClassifier)
- **Objective:** binary:logistic
- **Booster:** gbtree (histogram-based tree method)
- **Number of Boosted Rounds:** 2,050
- **Number of Features:** 200

## Training Data

- **Dataset:** IEEE-CIS Fraud Detection (train_transaction.csv + train_identity.csv)
- **Training Samples:** 590,540
- **Hyperparameter Optimization:** 60 Optuna trials
- **Total Pipeline Duration:** ~4.2 hours (15,172 seconds)
- **Training Date:** 2025-08-29

## Performance Metrics

| Metric | Value |
|--------|-------|
| Validation AUC | 0.9707 |
| Cross-Validation AUC (reported) | 0.9705 |

## Feature Importance (Top 20 by Gain)

| Rank | Feature | Gain |
|------|---------|------|
| 1 | V258 | 717.91 |
| 2 | V70 | 66.55 |
| 3 | V201 | 66.45 |
| 4 | V91 | 63.99 |
| 5 | V198 | 63.47 |
| 6 | V189 | 49.03 |
| 7 | V194 | 34.59 |
| 8 | V257 | 31.66 |
| 9 | V252 | 29.01 |
| 10 | addr2 | 20.45 |
| 11 | V200 | 20.22 |
| 12 | V197 | 18.91 |
| 13 | V283 | 16.67 |
| 14 | V249 | 15.56 |
| 15 | V262 | 13.76 |
| 16 | V45 | 13.20 |
| 17 | V254 | 13.17 |
| 18 | V187 | 12.78 |
| 19 | V217 | 12.13 |
| 20 | card3 | 12.13 |

V258 dominates with 10x the gain of the next feature. The Vesta-engineered V-features account for 18 of the top 20, with addr2 and card3 being the only raw transaction fields in the list.

## Export Formats

| Format | File | Size | Use Case |
|--------|------|------|----------|
| Python pickle | `ieee_fraud_model_production.pkl` | ~50 MB | Python inference (fraud_detector.py) |
| Native XGBoost JSON | `ieee_fraud_model_cpp.json` | ~100 MB | C++ inference via XGBoost C API |
| Metadata JSON | `ieee_fraud_model_metadata.json` | ~8 KB | Feature names, params, metrics |
| Training results | `training_results.json` | ~10 KB | Full analysis with importance scores |

## Label Encoders

31 categorical features are label-encoded, including: ProductCD, card4, card6, P_emaildomain, R_emaildomain, M1-M9, id_12-id_38, DeviceType, DeviceInfo.

## How to Retrain

1. Place IEEE-CIS CSVs in `data/raw/` (train_transaction.csv, train_identity.csv)
2. Run the training pipeline:
   ```bash
   PYTHONPATH=src python -c "
   from ml.training.core.data_processor import DataProcessor
   from ml.training.core.hyperparameter_optimizer import HyperparameterOptimizer
   from ml.training.core.checkpoint_manager import CheckpointManager
   from ml.training.core.pipeline_orchestrator import PipelineOrchestrator, TrainingPipeline

   config = {'data_dir': 'data/raw', 'n_trials': 60}
   dp = DataProcessor(config)
   ho = HyperparameterOptimizer(config)
   cm = CheckpointManager(config)
   orch = PipelineOrchestrator(dp, ho, cm, config)
   pipeline = TrainingPipeline(orchestrator=orch, config=config)
   results = pipeline.run(['xgboost'])
   ```
3. Export for C++ serving:
   ```bash
   python src/inference/export_model.py \
     --input models/ieee_fraud_model_production.pkl \
     --output models/ieee_fraud_model_cpp.json
   ```
4. The pipeline automatically saves the model to `models/ieee_fraud_model_production.pkl` and registers it in the ModelRegistry (Redis) if available.

## Online Learning

The production system supports live drift detection (PSI-based) and online learning via `enhanced_fraud_detector.py`. When drift is detected, `retraining_trigger.py` evaluates whether a full retrain is warranted based on guard conditions (minimum samples, cooldown period, severity threshold).
