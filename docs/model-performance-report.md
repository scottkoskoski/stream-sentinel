# Model Performance Report

## Production Model (Synthetic Data)

| Property | Value |
|----------|-------|
| Algorithm | XGBoost (Booster, binary:logistic) |
| Training Date | 2026-04-08 |
| Training Duration | ~6.6 minutes (GPU-accelerated) |
| GPU | NVIDIA RTX 5070 (CUDA) |
| Training Samples | 150,000 synthetic transactions (full 200 IEEE-CIS features) |
| Features | 200 (all populated in synthetic data) |
| Categorical Features | 31 (label-encoded with saved LabelEncoders) |
| Hyperparameter Optimization | Optuna, 75 trials (GPU) |

## Training Performance

### Accuracy Metrics

| Metric | Value |
|--------|-------|
| Training AUC | 0.9959 |
| Production AUC (measured) | 0.9942 |
| Precision (threshold=0.5) | 0.6204 |
| Recall (threshold=0.5) | 0.9054 |
| Baseline Fraud Rate | 2.71% |

The model was trained on synthetic data with all 200 IEEE-CIS features populated (V-features, id-features, device features, derived amounts). GPU-accelerated Optuna explored 75 hyperparameter configurations. Production AUC matches training AUC within 0.0017.

### Feature Importance (Top 20 by Gain)

| Rank | Feature | Gain | Category |
|------|---------|------|----------|
| 1 | V258 | 717.91 | Vesta engineered |
| 2 | V201 | 76.18 | Vesta engineered |
| 3 | V246 | 76.03 | Vesta engineered |
| 4 | V12 | 74.52 | Vesta engineered |
| 5 | V264 | 73.18 | Vesta engineered |
| 6 | V244 | 69.18 | Vesta engineered |
| 7 | V189 | 67.03 | Vesta engineered |
| 8 | V314 | 65.89 | Vesta engineered |
| 9 | V281 | 60.73 | Vesta engineered |
| 10 | V283 | 56.70 | Vesta engineered |
| 11 | V294 | 56.01 | Vesta engineered |
| 12 | V243 | 53.72 | Vesta engineered |
| 13 | addr2 | 51.44 | Address |
| 14 | V245 | 50.88 | Vesta engineered |
| 15 | V282 | 50.49 | Vesta engineered |
| 16 | V313 | 50.42 | Vesta engineered |
| 17 | card3 | 48.07 | Card |
| 18 | V310 | 47.75 | Vesta engineered |
| 19 | V315 | 45.07 | Vesta engineered |
| 20 | V285 | 41.63 | Vesta engineered |

V258 is the dominant feature at 717.91 gain -- over 9x the next most important feature. Vesta-engineered features (V-series) dominate the top 20, accounting for 18 of the 20 most important features. Only `addr2` and `card3` are raw transaction fields.

### Feature Composition

| Category | Count | In Top 20 |
|----------|-------|-----------|
| V-features (Vesta engineered) | 147 | 18 |
| Card features (card1-6) | 6 | 1 |
| Address features (addr1-2) | 2 | 1 |
| C-features (counting) | 14 | 0 |
| D-features (time delta) | 15 | 0 |
| M-features (match) | 9 | 0 |
| Identity features | 0 | 0 |
| Other (TransactionAmt, etc.) | 7 | 0 |

## Production Performance

### Training vs Production Gap -- RESOLVED

The synthetic data producer now generates all 200 features the model expects. The model was retrained on this full-feature synthetic data. Training and production AUC match within 0.0017.

| Environment | Features Populated | Measured AUC |
|------------|-------------------|-------------|
| Training (synthetic, full features) | 200/200 (100%) | 0.9959 |
| Production (synthetic, full features) | 200/200 (100%) | 0.9942 |

XGBoost handles missing features natively via its sparsity-aware split finding, so the model still produces meaningful scores -- but accuracy is significantly degraded from the training AUC. The system falls back to rule-based scoring as a safety net when the model is unavailable.

### Fraud Score Distribution

On synthetic data, model predictions cluster in a narrow range:

| Statistic | Value |
|-----------|-------|
| Minimum score | 0.0015 |
| Maximum score | 0.3676 |
| Threshold (alerts) | 0.30 |

The fraud threshold is set to 0.30 (not 0.70) specifically because the model's output distribution on synthetic data rarely exceeds 0.37. This threshold was calibrated during the fraud expert review phase.

### Categorical Encoding Limitation

The production pipeline uses `hash(value) % 1000` for categorical feature encoding at inference time. The model was trained with label encoders (31 features). This mismatch means categorical features are encoded differently between training and inference, further impacting accuracy on those features.

## Inference Benchmarks

### Single Prediction Latency (Python XGBoost, 1000 iterations)

| Percentile | Latency |
|-----------|---------|
| P50 | 20.9 ms |
| P95 | 34.9 ms |
| P99 | 40.2 ms |
| Throughput | ~44 predictions/sec |

### Batch Prediction Latency (Python XGBoost, 200 iterations each)

| Batch Size | P50 | P95 | Throughput |
|-----------|-----|-----|-----------|
| 32 | 6.0 ms | 12.0 ms | 4,553 pred/s |
| 64 | 6.0 ms | 12.0 ms | 8,603 pred/s |
| 128 | 6.0 ms | 13.0 ms | 16,979 pred/s |

Batch mode delivers ~386x throughput improvement over single predictions. At batch-128, the system achieves ~17,000 predictions/second with P95 well within the <100ms target.

### FastInferenceEngine Wrapper

The `FastInferenceEngine` wrapper adds negligible overhead (-4.4%, within measurement noise). It correctly auto-detects and falls back to Python XGBoost when the C++ extension is not compiled.

### C++ Inference

The C++ wrapper builds successfully but provides no latency benefit for Python consumers -- both paths call the same `libxgboost.so` C library underneath. The C++ wrapper is relevant only for non-Python deployment scenarios (e.g., C++ microservice).

### Model Export Verification

| Check | Result |
|-------|--------|
| Pickle-to-JSON prediction match | 100% (0.00 max difference) |
| Feature count preserved | 200 |
| Tree count preserved | 2,050 |
| Export format | Native XGBoost JSON (~100 MB) |

## Recommendations

1. **Use batch mode in production** (`--batch --batch-size 128`): Single most impactful optimization, delivering 17k pred/s.
2. **Retrain on real data** when available: The 172-feature gap renders the model partially effective on synthetic data.
3. **Integrate feedback loop**: Wire `feedback_processor.py` to track precision/recall on live predictions, not just drift (PSI).
4. **Fix categorical encoding**: Replace `hash() % 1000` with the stored label encoders from the model pickle.
5. **Monitor score distribution**: If scores exceed 0.37, the data distribution has changed and the threshold may need recalibration.
