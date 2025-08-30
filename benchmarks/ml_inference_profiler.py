#!/usr/bin/env python3

import time
import statistics
import psutil
import os
import sys
import pickle
import numpy as np
from pathlib import Path
from contextlib import contextmanager
from typing import Dict, List, Tuple, Optional
import json

# Focus on XGBoost model inference only, avoid full system dependencies


class MLInferenceProfiler:
    """
    Performance profiler specifically for ML inference bottleneck analysis.
    
    Measures Python XGBoost inference performance to establish baseline
    before C++ wrapper implementation. Follows FAANG engineering practice
    of measurement-driven optimization.
    """
    
    def __init__(self, model_path: str = None):
        # Use absolute path from current working directory
        if model_path is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(current_dir)
            self.model_path = os.path.join(project_root, "models", "ieee_fraud_model_production.pkl")
        else:
            self.model_path = model_path
        self.measurements: List[Dict] = []
        self.process = psutil.Process(os.getpid())
        
        # Load model directly for isolated testing
        try:
            with open(self.model_path, 'rb') as f:
                self.model = pickle.load(f)
            print(f"Loaded XGBoost model from {self.model_path}")
        except Exception as e:
            print(f"Error loading model: {e}")
            self.model = None

    @contextmanager
    def measure_inference(self):
        """Context manager to measure single inference performance."""
        # Memory before inference
        memory_before = self.process.memory_info().rss / (1024 * 1024)  # MB
        
        # CPU time before
        cpu_before = time.process_time()
        
        # Wall clock time
        start_time = time.perf_counter()
        
        yield
        
        # Measurements after inference
        end_time = time.perf_counter()
        cpu_after = time.process_time()
        memory_after = self.process.memory_info().rss / (1024 * 1024)  # MB
        
        measurement = {
            'wall_time_ms': (end_time - start_time) * 1000,
            'cpu_time_ms': (cpu_after - cpu_before) * 1000,
            'memory_before_mb': memory_before,
            'memory_after_mb': memory_after,
            'memory_delta_mb': memory_after - memory_before
        }
        
        self.measurements.append(measurement)

    def generate_test_features(self, count: int = 1000) -> List[List[float]]:
        """Generate realistic test features for ML inference profiling."""
        np.random.seed(42)  # Reproducible results
        
        # Based on fraud detection feature engineering patterns
        features = []
        for _ in range(count):
            # Simulate 20 features typical of fraud detection
            feature_vector = [
                np.random.lognormal(2.0, 1.5),  # transaction_amount (log-normal)
                np.random.poisson(0.8),         # velocity_1h
                np.random.poisson(2.1),         # velocity_24h
                np.random.exponential(0.3),     # time_since_last_transaction
                np.random.beta(2, 8),           # amount_zscore
                np.random.gamma(2, 2),          # frequency_score
                np.random.uniform(0, 1),        # merchant_risk_score
                np.random.normal(0, 1),         # user_behavior_score
                np.random.exponential(1),       # account_age_days
                np.random.poisson(1.2),         # daily_transaction_count
                np.random.lognormal(1.5, 0.8),  # avg_transaction_amount
                np.random.uniform(0, 1),        # merchant_category_risk
                np.random.beta(1.5, 3),         # time_of_day_risk
                np.random.gamma(1.5, 1),        # location_risk_score
                np.random.normal(0.5, 0.2),     # device_risk_score
                np.random.exponential(0.5),     # payment_method_risk
                np.random.uniform(0, 10),       # transaction_sequence
                np.random.beta(2, 5),           # peer_comparison_score
                np.random.gamma(1.8, 1.2),      # seasonal_factor
                np.random.normal(0, 1)          # normalized_feature
            ]
            features.append(feature_vector)
        
        return features

    def profile_single_inference(self, features: List[float]) -> Dict:
        """Profile a single ML inference call."""
        if self.model is None:
            return {'error': 'Model not loaded'}
            
        with self.measure_inference():
            # This mirrors the exact call in _calculate_ml_fraud_score()
            prediction = self.model.predict_proba([features])[0][1]
            
        return {
            'prediction': float(prediction),
            'measurement': self.measurements[-1]
        }

    def profile_batch_inference(self, batch_size: int = 100, iterations: int = 10) -> Dict:
        """Profile batch ML inference performance."""
        print(f"Profiling {iterations} batches of {batch_size} inferences each...")
        
        # Generate test features
        test_features = self.generate_test_features(batch_size * iterations)
        
        batch_results = []
        self.measurements.clear()
        
        for i in range(iterations):
            batch_start = i * batch_size
            batch_end = batch_start + batch_size
            batch_features = test_features[batch_start:batch_end]
            
            batch_start_time = time.perf_counter()
            
            for features in batch_features:
                result = self.profile_single_inference(features)
                if 'error' in result:
                    print(f"Error in batch {i}: {result['error']}")
                    continue
                    
            batch_end_time = time.perf_counter()
            batch_time = (batch_end_time - batch_start_time) * 1000  # ms
            
            batch_results.append({
                'batch_id': i,
                'batch_size': batch_size,
                'total_batch_time_ms': batch_time,
                'avg_inference_time_ms': batch_time / batch_size
            })
            
            if i % 2 == 0:
                print(f"Batch {i+1}/{iterations} complete - Avg: {batch_time/batch_size:.2f}ms per inference")
        
        return {
            'batch_results': batch_results,
            'individual_measurements': self.measurements[-batch_size*iterations:]
        }

    def calculate_statistics(self) -> Dict:
        """Calculate performance statistics from measurements."""
        if not self.measurements:
            return {'error': 'No measurements available'}
            
        wall_times = [m['wall_time_ms'] for m in self.measurements]
        cpu_times = [m['cpu_time_ms'] for m in self.measurements]
        memory_deltas = [m['memory_delta_mb'] for m in self.measurements]
        
        return {
            'total_inferences': len(self.measurements),
            'wall_time_stats': {
                'mean_ms': statistics.mean(wall_times),
                'median_ms': statistics.median(wall_times),
                'stdev_ms': statistics.stdev(wall_times) if len(wall_times) > 1 else 0,
                'min_ms': min(wall_times),
                'max_ms': max(wall_times),
                'p95_ms': np.percentile(wall_times, 95),
                'p99_ms': np.percentile(wall_times, 99)
            },
            'cpu_time_stats': {
                'mean_ms': statistics.mean(cpu_times),
                'median_ms': statistics.median(cpu_times)
            },
            'memory_stats': {
                'avg_delta_mb': statistics.mean(memory_deltas),
                'max_delta_mb': max(memory_deltas),
                'total_measurements': len(self.measurements)
            },
            'throughput': {
                'inferences_per_second': 1000 / statistics.mean(wall_times) if wall_times else 0,
                'theoretical_max_tps': 1000 / min(wall_times) if wall_times else 0
            }
        }

    def generate_baseline_report(self) -> Dict:
        """Generate comprehensive baseline performance report."""
        print("\n" + "="*60)
        print("ML INFERENCE PERFORMANCE BASELINE REPORT")
        print("="*60)
        
        # Run comprehensive profiling
        batch_results = self.profile_batch_inference(batch_size=1000, iterations=5)
        stats = self.calculate_statistics()
        
        if 'error' in stats:
            return {'error': stats['error']}
            
        report = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'model_path': self.model_path,
            'system_info': {
                'cpu_count': psutil.cpu_count(),
                'memory_total_gb': psutil.virtual_memory().total / (1024**3),
                'python_version': sys.version,
                'platform': sys.platform
            },
            'performance_baseline': stats,
            'batch_performance': batch_results
        }
        
        # Print summary
        print(f"\nBASELINE PERFORMANCE SUMMARY:")
        print(f"  Mean Inference Time: {stats['wall_time_stats']['mean_ms']:.2f}ms")
        print(f"  Median Inference Time: {stats['wall_time_stats']['median_ms']:.2f}ms")
        print(f"  95th Percentile: {stats['wall_time_stats']['p95_ms']:.2f}ms")
        print(f"  99th Percentile: {stats['wall_time_stats']['p99_ms']:.2f}ms")
        print(f"  Standard Deviation: {stats['wall_time_stats']['stdev_ms']:.2f}ms")
        print(f"  Theoretical Max TPS: {stats['throughput']['theoretical_max_tps']:.1f}")
        print(f"  Sustainable TPS: {stats['throughput']['inferences_per_second']:.1f}")
        print(f"  Memory per Inference: {stats['memory_stats']['avg_delta_mb']:.3f}MB")
        
        print(f"\nC++ OPTIMIZATION TARGET:")
        target_improvement = 10  # Conservative 10x improvement target
        target_latency = stats['wall_time_stats']['mean_ms'] / target_improvement
        target_throughput = stats['throughput']['inferences_per_second'] * target_improvement
        
        print(f"  Target Latency: <{target_latency:.2f}ms (10x improvement)")
        print(f"  Target Throughput: {target_throughput:.1f} TPS")
        print(f"  Current Bottleneck: Python XGBoost predict_proba() call")
        print(f"  Integration Point: fraud_detector.py:_calculate_ml_fraud_score()")
        
        return report

    def save_baseline_report(self, filename: str = "ml_inference_baseline.json"):
        """Save baseline report for C++ comparison."""
        report = self.generate_baseline_report()
        
        if 'error' not in report:
            with open(filename, 'w') as f:
                json.dump(report, f, indent=2)
            print(f"\nBaseline report saved to {filename}")
        
        return report


def main():
    """Main profiling execution."""
    print("ML Inference Profiler - Establishing Python XGBoost Baseline")
    print("Purpose: Measure current performance before C++ wrapper implementation")
    
    # Initialize profiler
    profiler = MLInferenceProfiler()
    
    if profiler.model is None:
        print("ERROR: Could not load XGBoost model. Please ensure the model file exists.")
        return
    
    # Generate and save comprehensive baseline report
    baseline_file = os.path.join(os.path.dirname(__file__), "ml_inference_baseline.json")
    report = profiler.save_baseline_report(baseline_file)
    
    if 'error' in report:
        print(f"Error generating baseline: {report['error']}")
        return
    
    print(f"\n✓ Baseline established successfully")
    print(f"✓ Report saved to {baseline_file}")
    print(f"✓ Ready for C++ wrapper implementation and comparison")


if __name__ == "__main__":
    main()