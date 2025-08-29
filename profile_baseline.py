#!/usr/bin/env python3
"""
Accurate Baseline Performance Profiling

Separate feature preparation from prediction to identify real bottlenecks.
"""

import sys
import time
import numpy as np
import psutil
import os
from pathlib import Path

sys.path.append('src')

from ml.training.core.checkpoint_manager import CheckpointManager

def profile_xgboost_baseline():
    """Profile actual XGBoost performance separating loading, feature prep, and prediction."""
    print("🔍 Accurate XGBoost Baseline Profiling")
    print("=" * 60)
    
    # Load model once (measure one-time cost)
    print("📦 Loading model (one-time cost)...")
    start = time.perf_counter()
    
    checkpoint_manager = CheckpointManager({
        "checkpoint_dir": "models/checkpoints",
        "retention_hours": 168
    })
    
    best_checkpoint = checkpoint_manager.load_best_checkpoint()
    model = best_checkpoint.model
    
    load_time = time.perf_counter() - start
    print(f"   Model loading: {load_time*1000:.2f}ms (one-time per process)")
    
    # Memory after model load
    process = psutil.Process(os.getpid())
    memory_mb = process.memory_info().rss / 1024 / 1024
    print(f"   Process memory: {memory_mb:.1f}MB")
    
    # Generate test data
    print("\n🔢 Generating test features...")
    np.random.seed(42)
    
    # Single-row inference
    single_row = np.random.randn(1, 200).astype(np.float32)
    
    # Batch inference  
    batch_sizes = [1, 10, 50, 100, 500]
    batches = {size: np.random.randn(size, 200).astype(np.float32) for size in batch_sizes}
    
    # Warmup (page in model, fill caches)
    print("\n🔥 Warmup (10 predictions)...")
    for _ in range(10):
        _ = model.predict_proba(single_row)
    
    # Profile single-row prediction
    print("\n⚡ Single-Row Performance:")
    times = []
    for i in range(1000):
        # Feature preparation (minimal for synthetic data)
        start_prep = time.perf_counter()
        features_ready = single_row  # In real system: validation, imputation, scaling
        prep_time = time.perf_counter() - start_prep
        
        # XGBoost prediction
        start_pred = time.perf_counter()
        probs = model.predict_proba(features_ready)[:, 1]
        pred_time = time.perf_counter() - start_pred
        
        total_time = prep_time + pred_time
        times.append((prep_time * 1000, pred_time * 1000, total_time * 1000))
    
    prep_times = [t[0] for t in times]
    pred_times = [t[1] for t in times]
    total_times = [t[2] for t in times]
    
    print(f"   Feature prep: {np.mean(prep_times):.3f}ms mean, {np.percentile(prep_times, 99):.3f}ms p99")
    print(f"   XGBoost pred: {np.mean(pred_times):.3f}ms mean, {np.percentile(pred_times, 99):.3f}ms p99")
    print(f"   Total E2E:    {np.mean(total_times):.3f}ms mean, {np.percentile(total_times, 99):.3f}ms p99")
    print(f"   Theoretical RPS/thread: {1000/np.mean(total_times):.0f}")
    
    # Profile batch inference
    print("\n📊 Batch Performance:")
    for batch_size in batch_sizes:
        batch_data = batches[batch_size]
        
        # Time batch prediction
        times = []
        for i in range(100):
            start = time.perf_counter()
            probs = model.predict_proba(batch_data)[:, 1]
            times.append(time.perf_counter() - start)
        
        batch_mean = np.mean(times) * 1000
        batch_p99 = np.percentile(times, 99) * 1000
        per_row = batch_mean / batch_size
        
        print(f"   Batch {batch_size:3d}: {batch_mean:6.2f}ms total, {per_row:5.3f}ms/row, {batch_size/(batch_mean/1000):.0f} rps")
    
    # Memory after warmup/inference
    final_memory_mb = process.memory_info().rss / 1024 / 1024
    print(f"\nMemory usage: {final_memory_mb:.1f}MB total process")
    
    return {
        'model_load_ms': load_time * 1000,
        'single_prep_mean_ms': np.mean(prep_times),
        'single_pred_mean_ms': np.mean(pred_times),
        'single_total_mean_ms': np.mean(total_times),
        'single_total_p99_ms': np.percentile(total_times, 99),
        'memory_mb': final_memory_mb,
        'rps_per_thread': 1000/np.mean(total_times)
    }

if __name__ == "__main__":
    results = profile_xgboost_baseline()
    
    print(f"\n🎯 Corrected Baseline Summary:")
    print(f"   Model loading: {results['model_load_ms']:.1f}ms (one-time)")  
    print(f"   Single prediction: {results['single_total_mean_ms']:.3f}ms mean, {results['single_total_p99_ms']:.3f}ms p99")
    print(f"   Process memory: {results['memory_mb']:.1f}MB")
    print(f"   RPS/thread: {results['rps_per_thread']:.0f}")