#!/usr/bin/env python3

"""
Python-Only Performance Baseline

Establishes current Python XGBoost performance baseline while
we wait for C++ wrapper compilation. This provides the "before"
metrics for our optimization comparison.
"""

import time
import statistics
import pickle
import numpy as np
from typing import List, Dict, Any
import json
from datetime import datetime

def load_model():
    """Load the production XGBoost model."""
    model_path = "models/ieee_fraud_model_production.pkl"
    
    with open(model_path, 'rb') as f:
        model_data = pickle.load(f)
    
    if isinstance(model_data, dict):
        return model_data['model'], model_data.get('feature_names', [])
    else:
        return model_data, []

def generate_realistic_features(count: int = 1000) -> List[List[float]]:
    """Generate realistic fraud detection features."""
    np.random.seed(42)  # Reproducible
    
    features = []
    for _ in range(count):
        # Generate 200 features matching our model requirements
        feature_vector = []
        
        # Add some realistic transaction features
        feature_vector.extend([
            np.random.lognormal(2.0, 1.5),    # transaction amounts
            np.random.poisson(0.8),           # velocity features  
            np.random.poisson(2.1),           # daily counts
            np.random.exponential(0.3),       # time-based features
            np.random.beta(2, 8),             # risk scores
            np.random.gamma(2, 2),            # behavioral scores
            np.random.uniform(0, 1),          # normalized scores
            np.random.normal(0, 1),           # z-scores
        ])
        
        # Fill remaining features with reasonable random values
        while len(feature_vector) < 200:
            feature_vector.append(np.random.normal(0, 1))
        
        features.append(feature_vector[:200])  # Ensure exactly 200
    
    return features

def benchmark_python_inference(model, test_features: List[List[float]], iterations: int = 5000) -> Dict[str, Any]:
    """Comprehensive Python XGBoost inference benchmark."""
    
    print(f"Benchmarking Python XGBoost ({iterations} inferences)...")
    
    # Warmup
    for i in range(100):
        features = test_features[i % len(test_features)]
        model.predict_proba([features])
    
    # Actual benchmark
    latencies = []
    predictions = []
    
    for i in range(iterations):
        features = test_features[i % len(test_features)]
        
        start_time = time.perf_counter()
        prediction = model.predict_proba([features])[0][1]  # Fraud probability
        end_time = time.perf_counter()
        
        latency_ms = (end_time - start_time) * 1000
        latencies.append(latency_ms)
        predictions.append(prediction)
    
    # Calculate statistics
    return {
        'iterations': iterations,
        'total_time_s': sum(latencies) / 1000,
        'mean_latency_ms': statistics.mean(latencies),
        'median_latency_ms': statistics.median(latencies),
        'p95_latency_ms': np.percentile(latencies, 95),
        'p99_latency_ms': np.percentile(latencies, 99),
        'min_latency_ms': min(latencies),
        'max_latency_ms': max(latencies),
        'std_latency_ms': statistics.stdev(latencies),
        'throughput_ips': iterations / (sum(latencies) / 1000),
        'prediction_stats': {
            'mean': statistics.mean(predictions),
            'std': statistics.stdev(predictions),
            'min': min(predictions),
            'max': max(predictions)
        }
    }

def main():
    """Run comprehensive Python baseline benchmark."""
    
    print("Python XGBoost Performance Baseline")
    print("=" * 40)
    
    try:
        # Load model
        model, feature_names = load_model()
        print(f"✓ Loaded model with {len(feature_names)} features")
        
        # Generate test data
        test_features = generate_realistic_features(10000)
        print(f"✓ Generated {len(test_features)} test samples")
        
        # Run benchmark
        results = benchmark_python_inference(model, test_features)
        
        # Display results
        print(f"\n📊 PYTHON XGBOOST PERFORMANCE BASELINE")
        print(f"{'='*50}")
        print(f"Iterations:          {results['iterations']:,}")
        print(f"Total Time:          {results['total_time_s']:.2f}s")
        print(f"Mean Latency:        {results['mean_latency_ms']:.3f}ms")
        print(f"Median Latency:      {results['median_latency_ms']:.3f}ms") 
        print(f"95th Percentile:     {results['p95_latency_ms']:.3f}ms")
        print(f"99th Percentile:     {results['p99_latency_ms']:.3f}ms")
        print(f"Min Latency:         {results['min_latency_ms']:.3f}ms")
        print(f"Max Latency:         {results['max_latency_ms']:.3f}ms")
        print(f"Std Dev:             {results['std_latency_ms']:.3f}ms")
        print(f"Throughput:          {results['throughput_ips']:.0f} inferences/sec")
        
        print(f"\n🎯 C++ OPTIMIZATION TARGETS:")
        target_improvements = [2, 5, 10]
        for improvement in target_improvements:
            target_latency = results['mean_latency_ms'] / improvement
            target_throughput = results['throughput_ips'] * improvement
            print(f"{improvement:2}x improvement: {target_latency:.3f}ms latency, {target_throughput:.0f} TPS")
        
        print(f"\n📈 Prediction Statistics:")
        pred_stats = results['prediction_stats']
        print(f"Mean fraud prob:     {pred_stats['mean']:.6f}")
        print(f"Std dev:             {pred_stats['std']:.6f}")
        print(f"Range:               [{pred_stats['min']:.6f}, {pred_stats['max']:.6f}]")
        
        # Save baseline for comparison
        baseline_data = {
            'timestamp': datetime.now().isoformat(),
            'engine': 'python_xgboost',
            'model_file': 'ieee_fraud_model_production.pkl',
            'performance': results,
            'test_config': {
                'iterations': results['iterations'],
                'feature_count': len(feature_names),
                'warmup_iterations': 100
            }
        }
        
        baseline_file = f"python_baseline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(baseline_file, 'w') as f:
            json.dump(baseline_data, f, indent=2, default=str)
        
        print(f"\n✓ Baseline saved to {baseline_file}")
        print(f"✓ Ready for C++ performance comparison")
        
        return 0
        
    except Exception as e:
        print(f"❌ Baseline benchmark failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(main())