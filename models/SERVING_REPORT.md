# C++ Model Serving Pipeline Verification Report

**Date:** 2026-04-08
**Model:** ieee_fraud_model_production.pkl (XGBClassifier, 200 features, 97.05% CV AUC on IEEE-CIS)

> **Note (2026-04-08):** 97.05% AUC is the cross-validation score on the original IEEE-CIS training run. The subsequent production model (`synthetic_fraud_model_production.pkl`, retrained on the full synthetic 200-feature dataset with F2-score optimization) scores 99.42% AUC on its held-out test set. See `models/TRAINING_REPORT.md` for the current production model details.

> **Update (2026-04-16):** The "C++ wrapper provides no speedup for single
> predictions" conclusion below (Section 4, Recommendations) reflects
> measurements taken when the XGBoost call in the hot path was the sole
> cost. Subsequent work (see commit history for
> `fraud_detector.py`) replaced the sklearn `StandardScaler.transform`
> and 31 sequential `LabelEncoder.transform` calls with precomputed
> lookup tables, so per-message scoring is now dominated by
> `libxgboost.so` again. In that regime the C++ path measures
> ~0.15 ms / prediction end-to-end vs ~21 ms via the prior pickle-loaded
> Python path (which was silently returning `AttributeError` on the
> bare `Booster` before the `inplace_predict` fallback was added). The
> current production numbers are reported in `README.md` and
> `docs/fraud-detection/README.md`.

## 1. Model Export Verification

| Metric | Result |
|--------|--------|
| Samples tested | 100 |
| Max absolute difference | 0.00e+00 |
| Mean absolute difference | 0.00e+00 |
| Predictions matching within 1e-6 | **100.0%** |
| Pickle prediction range | [0.0015, 0.3676] |
| JSON prediction range | [0.0015, 0.3676] |

The exported native XGBoost JSON model (`ieee_fraud_model_production_cpp.json`, ~100 MB)
produces bit-identical predictions to the original pickle model. The export is lossless.

## 2. Python XGBoost Inference Benchmarks

### Single Prediction Latency (1000 iterations)

| Percentile | Latency |
|-----------|---------|
| P50 | 20.9 ms |
| P95 | 34.9 ms |
| P99 | 40.2 ms |
| Throughput | ~44 predictions/sec |

### Batch Prediction Latency (200 iterations each)

| Batch Size | P50 | P95 | Throughput |
|-----------|-----|-----|-----------|
| 32 | 6.0 ms | 12.0 ms | 4,553 pred/s |
| 64 | 6.0 ms | 12.0 ms | 8,603 pred/s |
| 128 | 6.0 ms | 13.0 ms | 16,979 pred/s |

Batch inference provides massive throughput gains (up to ~386x over single prediction)
due to amortized DMatrix construction and vectorized tree traversal.

## 3. FastInferenceEngine Wrapper

| Metric | Result |
|--------|--------|
| Python fallback | Working correctly |
| C++ auto-detection | Working (loads when compiled) |
| Prediction accuracy | Exact match with direct XGBoost |
| Wrapper overhead (P50) | **-4.4%** (negligible, within noise) |

The FastInferenceEngine adds no measurable overhead. It correctly:
- Falls back to Python when C++ is unavailable
- Auto-detects and uses C++ when the extension is compiled
- Produces identical predictions through both paths

## 4. C++ Build Status

| Component | Status |
|-----------|--------|
| `xgboost_headers/c_api.h` | Present |
| g++ (C++17) | Available (GCC 15.2.1) |
| pybind11 | Available (pip install) |
| `libxgboost.so` | Found in xgboost package |
| **Build result** | **SUCCESS** |

### Build Requirements
- `g++` with C++17 support
- `pybind11` Python package (`pip install pybind11`)
- `xgboost` Python package (provides `libxgboost.so`)
- Run `make` in `src/inference/cpp/`

### C++ vs Python Performance

| Engine | P50 Latency | Speedup |
|--------|-------------|---------|
| Python (direct) | 20.9 ms | 1.00x |
| C++ wrapper | 21.0 ms | ~1.00x |

The C++ pybind11 wrapper calls the same underlying `libxgboost.so` C library as the
Python XGBoost package. The single-prediction latency is dominated by tree traversal
in the shared library, so the wrapper provides no speedup for single predictions.
The primary benefit of the C++ path is for embedded/non-Python deployments.

## 5. Recommendations for Production Deployment

1. **Use batch mode** (`--batch --batch-size 128`). Batching provides ~386x throughput
   improvement over single predictions (16,979 vs 44 pred/s). This is by far the most
   impactful optimization available.

2. **The C++ wrapper is production-ready** but provides no latency benefit for the
   Python consumer since both paths use the same `libxgboost.so`. It is useful if
   deploying inference in a pure C++ microservice (no Python overhead).

3. **Model export is verified lossless** -- the JSON model can be safely used wherever
   the C API is needed (C++ services, ONNX Runtime, edge deployments).

4. **Single-prediction P99 (~40 ms)** is well within the <100 ms target. With batch
   mode at batch_size=128, per-prediction latency drops to ~47 us.

5. **Consider ONNX export** for alternative serving backends (TensorRT, ONNX Runtime)
   if sub-millisecond single-prediction latency becomes a requirement.
