#!/usr/bin/env python3

"""Quick Python XGBoost performance baseline"""

import time
import statistics
import pickle
import numpy as np

def main():
    # Load model
    with open("models/ieee_fraud_model_production.pkl", 'rb') as f:
        model_data = pickle.load(f)
    model = model_data['model']
    
    # Quick test with 1000 inferences
    test_features = [[1.0] * 200]  # Single test case
    latencies = []
    
    print("Running quick baseline (1000 inferences)...")
    
    # Warmup
    for _ in range(10):
        model.predict_proba(test_features)
    
    # Benchmark
    for i in range(1000):
        start = time.perf_counter()
        pred = model.predict_proba(test_features)[0][1]
        end = time.perf_counter()
        latencies.append((end - start) * 1000)
    
    print(f"Mean latency: {statistics.mean(latencies):.3f}ms")
    print(f"P95 latency:  {np.percentile(latencies, 95):.3f}ms")
    print(f"P99 latency:  {np.percentile(latencies, 99):.3f}ms")
    print(f"Throughput:   {1000/(sum(latencies)/1000):.0f} inferences/sec")
    
    print("\n✅ Quick baseline complete!")

if __name__ == "__main__":
    main()