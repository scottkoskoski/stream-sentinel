#!/usr/bin/env python3
"""
C++ Model Serving Pipeline Verification Script

Verifies model export integrity, benchmarks Python XGBoost inference,
tests FastInferenceEngine wrapper, and assesses C++ build readiness.
"""

import os
import pickle
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

# Ensure src is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

MODELS_DIR = PROJECT_ROOT / "models"
PKL_PATH = MODELS_DIR / "ieee_fraud_model_production.pkl"
JSON_PATH = MODELS_DIR / "ieee_fraud_model_cpp.json"
# The export_model.py default naming uses _production_cpp.json
JSON_PATH_ALT = MODELS_DIR / "ieee_fraud_model_production_cpp.json"

results = {}


def section(title):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def find_json_model():
    """Find the exported JSON model, exporting if needed."""
    for p in [JSON_PATH, JSON_PATH_ALT]:
        if p.exists():
            return p
    # Try to export
    print("JSON model not found, exporting from pickle...")
    import xgboost as xgb

    with open(PKL_PATH, "rb") as f:
        model_data = pickle.load(f)
    estimator = model_data["model"] if isinstance(model_data, dict) else model_data
    booster = estimator.get_booster() if hasattr(estimator, "get_booster") else estimator
    out = JSON_PATH_ALT
    booster.save_model(str(out))
    print(f"Exported to {out} ({out.stat().st_size / 1024:.1f} KB)")
    return out


# ============================================================
# Task 1: Model Export Integrity
# ============================================================
section("1. Model Export Integrity Verification")

with open(PKL_PATH, "rb") as f:
    model_data = pickle.load(f)
estimator = model_data["model"] if isinstance(model_data, dict) else model_data
print(f"Pickle model type: {type(estimator).__name__}")

# Extract info about features
import xgboost as xgb

booster = estimator.get_booster() if hasattr(estimator, "get_booster") else estimator
num_features = booster.num_features()
print(f"Number of features: {num_features}")

json_path = find_json_model()
print(f"JSON model path: {json_path}")

# Load JSON model as a fresh booster
json_booster = xgb.Booster()
json_booster.load_model(str(json_path))
print(f"JSON model loaded, features: {json_booster.num_features()}")

# Generate test data
np.random.seed(42)
N_SAMPLES = 100
test_data = np.random.randn(N_SAMPLES, num_features).astype(np.float32)
# Make values more realistic (scale to reasonable transaction feature ranges)
test_data = np.clip(test_data * 2 + 1, -10, 10)

# Get feature names from the model
feature_names = booster.feature_names
if feature_names:
    print(f"Feature names: {len(feature_names)} names (first 5: {feature_names[:5]})")
    dmat = xgb.DMatrix(test_data, feature_names=feature_names)
else:
    dmat = xgb.DMatrix(test_data)

# Predictions from pickle model (via booster)
pkl_preds = booster.predict(dmat)

# Predictions from JSON model
json_preds = json_booster.predict(dmat)

max_diff = np.max(np.abs(pkl_preds - json_preds))
mean_diff = np.mean(np.abs(pkl_preds - json_preds))
match_pct = np.mean(np.abs(pkl_preds - json_preds) < 1e-6) * 100

print(f"\nPrediction comparison ({N_SAMPLES} samples):")
print(f"  Max absolute difference:  {max_diff:.2e}")
print(f"  Mean absolute difference: {mean_diff:.2e}")
print(f"  Match within 1e-6:        {match_pct:.1f}%")
print(f"  Prediction range (pkl):   [{pkl_preds.min():.4f}, {pkl_preds.max():.4f}]")
print(f"  Prediction range (json):  [{json_preds.min():.4f}, {json_preds.max():.4f}]")

results["export"] = {
    "max_diff": float(max_diff),
    "mean_diff": float(mean_diff),
    "match_pct": float(match_pct),
    "num_features": int(num_features),
    "json_path": str(json_path),
}

# ============================================================
# Task 2: Python XGBoost Inference Benchmarks
# ============================================================
section("2. Python XGBoost Inference Benchmarks")

# Single prediction latency
N_ITERS = 1000
single_times = []
single_feature = test_data[0:1]

for _ in range(N_ITERS):
    dm = xgb.DMatrix(single_feature, feature_names=feature_names)
    t0 = time.perf_counter()
    _ = booster.predict(dm)
    single_times.append((time.perf_counter() - t0) * 1e6)  # microseconds

single_times.sort()
p50 = single_times[int(N_ITERS * 0.50)]
p95 = single_times[int(N_ITERS * 0.95)]
p99 = single_times[int(N_ITERS * 0.99)]

print(f"Single prediction latency ({N_ITERS} iterations):")
print(f"  P50: {p50:.1f} us")
print(f"  P95: {p95:.1f} us")
print(f"  P99: {p99:.1f} us")
print(f"  Throughput: {1e6 / np.mean(single_times):.0f} predictions/sec")

results["single_latency"] = {"p50_us": p50, "p95_us": p95, "p99_us": p99, "throughput": 1e6 / np.mean(single_times)}

# Batch prediction latency
print(f"\nBatch prediction latency:")
batch_results = {}
for batch_size in [32, 64, 128]:
    batch_data = np.random.randn(batch_size, num_features).astype(np.float32)
    dm = xgb.DMatrix(batch_data, feature_names=feature_names)
    times = []
    for _ in range(200):
        t0 = time.perf_counter()
        _ = booster.predict(dm)
        times.append((time.perf_counter() - t0) * 1e6)
    times.sort()
    bp50 = times[int(len(times) * 0.50)]
    bp95 = times[int(len(times) * 0.95)]
    throughput = batch_size * 1e6 / np.mean(times)
    print(f"  Batch {batch_size:>3d}: P50={bp50:.1f}us  P95={bp95:.1f}us  " f"Throughput={throughput:.0f} pred/s")
    batch_results[batch_size] = {"p50_us": bp50, "p95_us": bp95, "throughput": throughput}

results["batch_latency"] = batch_results

# ============================================================
# Task 3: FastInferenceEngine Verification
# ============================================================
section("3. FastInferenceEngine Verification")

from inference.fast_inference import FastInferenceEngine

# Instantiate with C++ disabled to test Python fallback
engine = FastInferenceEngine(str(PKL_PATH), enable_cpp=False)
status = engine.get_status()
print(f"Engine status (cpp disabled): {status}")
assert not status["using_cpp"], "Should not be using C++ when disabled"
assert status["python_available"], "Python model should be available"

# Instantiate with C++ enabled (will fall back since not compiled)
engine_auto = FastInferenceEngine(str(PKL_PATH), enable_cpp=True)
status_auto = engine_auto.get_status()
print(f"Engine status (cpp enabled):  {status_auto}")
print(f"  Fallback to Python: {not status_auto['using_cpp']}")

# Test predictions match
test_features = test_data[0].tolist()
prob_engine, info = engine.predict_fraud_probability(test_features)
print(f"\nWrapper prediction: {prob_engine:.6f} (engine={info['engine']}, " f"time={info['inference_time_ms']:.3f}ms)")

# Compare with direct XGBoost
prob_direct = estimator.predict_proba([test_features])[0][1]
print(f"Direct prediction:  {prob_direct:.6f}")
diff = abs(prob_engine - prob_direct)
print(f"Difference:         {diff:.2e}")

# Benchmark wrapper overhead
wrapper_times = []
direct_times = []
for i in range(500):
    feats = test_data[i % N_SAMPLES].tolist()
    t0 = time.perf_counter()
    engine.predict_fraud_probability(feats)
    wrapper_times.append((time.perf_counter() - t0) * 1e6)

    t0 = time.perf_counter()
    estimator.predict_proba([feats])
    direct_times.append((time.perf_counter() - t0) * 1e6)

wrapper_times.sort()
direct_times.sort()
w_p50 = wrapper_times[int(len(wrapper_times) * 0.50)]
d_p50 = direct_times[int(len(direct_times) * 0.50)]
overhead_pct = (w_p50 / d_p50 - 1) * 100

print(f"\nWrapper vs Direct (P50):")
print(f"  Wrapper:  {w_p50:.1f} us")
print(f"  Direct:   {d_p50:.1f} us")
print(f"  Overhead: {overhead_pct:+.1f}%")

results["wrapper"] = {
    "wrapper_p50_us": w_p50,
    "direct_p50_us": d_p50,
    "overhead_pct": overhead_pct,
    "prediction_diff": float(diff),
    "fallback_works": not status_auto["using_cpp"],
}

# ============================================================
# Task 4: C++ Build Readiness
# ============================================================
section("4. C++ Build Readiness Assessment")

cpp_dir = PROJECT_ROOT / "src" / "inference" / "cpp"
c_api_header = cpp_dir / "xgboost_headers" / "c_api.h"
print(f"c_api.h present: {c_api_header.exists()}")

# Check system dependencies
checks = {}
for cmd, label in [("g++ --version", "g++"), ("python3 -m pybind11 --includes", "pybind11")]:
    try:
        r = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=10)
        checks[label] = r.returncode == 0
        if r.returncode == 0:
            print(f"  {label}: available ({r.stdout.splitlines()[0][:60]})")
        else:
            print(f"  {label}: NOT available")
    except Exception as e:
        checks[label] = False
        print(f"  {label}: NOT available ({e})")

# Check for XGBoost shared library
try:
    import xgboost as _xgb

    xgb_lib = Path(_xgb.__file__).parent / "lib" / "libxgboost.so"
    checks["libxgboost.so"] = xgb_lib.exists()
    print(f"  libxgboost.so: {'found' if xgb_lib.exists() else 'NOT found'} at {xgb_lib}")
except Exception:
    checks["libxgboost.so"] = False
    print(f"  libxgboost.so: NOT found")

# Attempt build
cpp_build_success = False
cpp_build_output = ""
if all(checks.values()):
    print("\nAttempting C++ build...")
    try:
        r = subprocess.run(["make", "-C", str(cpp_dir), "clean"], capture_output=True, text=True, timeout=30)
        r = subprocess.run(
            ["make", "-C", str(cpp_dir)],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")},
        )
        cpp_build_output = r.stdout + r.stderr
        cpp_build_success = r.returncode == 0
        if cpp_build_success:
            print("  BUILD SUCCEEDED")
        else:
            print(f"  BUILD FAILED (rc={r.returncode})")
            print(f"  {cpp_build_output[-500:]}")
    except Exception as e:
        cpp_build_output = str(e)
        print(f"  Build error: {e}")
else:
    missing = [k for k, v in checks.items() if not v]
    print(f"\nSkipping build - missing dependencies: {', '.join(missing)}")
    cpp_build_output = f"Missing: {', '.join(missing)}"

results["cpp_build"] = {
    "c_api_header": c_api_header.exists(),
    "dependencies": checks,
    "build_success": cpp_build_success,
    "build_output_tail": cpp_build_output[-300:] if cpp_build_output else "",
}

# If build succeeded, benchmark C++ vs Python
if cpp_build_success:
    section("4b. C++ vs Python Benchmark")
    engine_cpp = FastInferenceEngine(str(PKL_PATH), enable_cpp=True)
    if engine_cpp.using_cpp:
        cpp_times = []
        for i in range(500):
            feats = test_data[i % N_SAMPLES].tolist()
            t0 = time.perf_counter()
            engine_cpp.predict_fraud_probability(feats)
            cpp_times.append((time.perf_counter() - t0) * 1e6)
        cpp_times.sort()
        cpp_p50 = cpp_times[int(len(cpp_times) * 0.50)]
        speedup = d_p50 / cpp_p50
        print(f"  C++ P50:    {cpp_p50:.1f} us")
        print(f"  Python P50: {d_p50:.1f} us")
        print(f"  Speedup:    {speedup:.2f}x")
        results["cpp_benchmark"] = {"cpp_p50_us": cpp_p50, "speedup": speedup}
    else:
        print("  C++ engine did not load despite successful build")

# ============================================================
# Summary
# ============================================================
section("Summary")
print(f"Export match:     {results['export']['match_pct']:.1f}%")
print(f"Single P99:       {results['single_latency']['p99_us']:.1f} us")
print(f"Throughput (1x):  {results['single_latency']['throughput']:.0f} pred/s")
print(f"Wrapper overhead: {results['wrapper']['overhead_pct']:+.1f}%")
print(f"C++ build:        {'SUCCESS' if results['cpp_build']['build_success'] else 'NOT BUILT'}")

# Save results for report generation
import json

results_path = MODELS_DIR / "serving_verification_results.json"
with open(results_path, "w") as f:
    json.dump(results, f, indent=2)
print(f"\nResults saved to {results_path}")
