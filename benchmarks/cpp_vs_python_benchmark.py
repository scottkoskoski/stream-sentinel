#!/usr/bin/env python3

"""
C++ vs Python XGBoost Performance Benchmark

Comprehensive comparison of C++ wrapper vs Python XGBoost implementation
for fraud detection inference. Provides evidence-based performance validation
following FAANG engineering practices.
"""

import os
import sys
import time
import json
import pickle
import statistics
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Any
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime

# Add src directory for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from inference.fast_inference import FastInferenceEngine
    CPP_AVAILABLE = True
except ImportError:
    CPP_AVAILABLE = False


class InferenceComparator:
    """
    Comprehensive performance comparison between C++ and Python XGBoost implementations.
    
    Measures latency, throughput, memory usage, and accuracy consistency across
    different workload patterns to provide definitive performance analysis.
    """
    
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.python_model = None
        self.fast_engine = None
        self.test_features = []
        self.results = {
            'metadata': {
                'timestamp': datetime.now().isoformat(),
                'model_path': model_path,
                'cpp_available': CPP_AVAILABLE
            },
            'python_results': {},
            'cpp_results': {},
            'comparison': {}
        }
        
        self._initialize_models()
        self._generate_test_data()
    
    def _initialize_models(self) -> None:
        """Initialize both Python and C++ inference engines."""
        
        # Load Python model
        try:
            with open(self.model_path, 'rb') as f:
                model_data = pickle.load(f)
                
            if isinstance(model_data, dict):
                self.python_model = model_data.get('model')
            else:
                self.python_model = model_data
                
            print("✓ Python XGBoost model loaded")
            
        except Exception as e:
            print(f"✗ Failed to load Python model: {e}")
            raise
        
        # Load C++ accelerated engine if available
        if CPP_AVAILABLE:
            try:
                self.fast_engine = FastInferenceEngine(self.model_path, enable_cpp=True)
                status = self.fast_engine.get_status()
                
                if status['using_cpp']:
                    print("✓ C++ accelerated inference engine loaded")
                else:
                    print("! C++ wrapper not available, FastInferenceEngine using Python fallback")
                    
            except Exception as e:
                print(f"✗ Failed to load FastInferenceEngine: {e}")
                self.fast_engine = None
        else:
            print("! C++ inference module not available")
    
    def _generate_test_data(self, num_samples: int = 10000) -> None:
        """Generate realistic test features for benchmarking."""
        print(f"Generating {num_samples} test samples...")
        
        np.random.seed(42)  # Reproducible results
        
        # Generate realistic fraud detection features
        for _ in range(num_samples):
            features = [
                np.random.lognormal(2.0, 1.5),    # transaction_amount
                np.random.poisson(0.8),           # velocity_1h  
                np.random.poisson(2.1),           # velocity_24h
                np.random.exponential(0.3),       # time_since_last
                np.random.beta(2, 8),             # amount_zscore
                np.random.gamma(2, 2),            # frequency_score
                np.random.uniform(0, 1),          # merchant_risk
                np.random.normal(0, 1),           # user_behavior
                np.random.exponential(1),         # account_age_days
                np.random.poisson(1.2),           # daily_txn_count
                np.random.lognormal(1.5, 0.8),   # avg_txn_amount
                np.random.uniform(0, 1),          # merchant_category_risk
                np.random.beta(1.5, 3),           # time_of_day_risk
                np.random.gamma(1.5, 1),          # location_risk
                np.random.normal(0.5, 0.2),       # device_risk
                np.random.exponential(0.5),       # payment_method_risk
                np.random.uniform(0, 10),         # transaction_sequence
                np.random.beta(2, 5),             # peer_comparison
                np.random.gamma(1.8, 1.2),        # seasonal_factor
                np.random.normal(0, 1)            # normalized_feature
            ]
            self.test_features.append(features)
        
        print(f"✓ Generated {len(self.test_features)} test samples")
    
    def benchmark_python_inference(self, iterations: int = 5000) -> Dict[str, Any]:
        """Benchmark Python XGBoost inference performance."""
        print(f"Benchmarking Python inference ({iterations} iterations)...")
        
        if not self.python_model:
            return {'error': 'Python model not loaded'}
        
        latencies = []
        predictions = []
        
        # Warmup
        for i in range(100):
            features = self.test_features[i]
            self.python_model.predict_proba([features])
        
        # Actual benchmark
        for i in range(iterations):
            features = self.test_features[i % len(self.test_features)]
            
            start_time = time.perf_counter()
            prediction = self.python_model.predict_proba([features])[0][1]
            end_time = time.perf_counter()
            
            latency_ms = (end_time - start_time) * 1000
            latencies.append(latency_ms)
            predictions.append(prediction)
        
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
            'predictions_sample': predictions[:10]
        }
    
    def benchmark_cpp_inference(self, iterations: int = 5000) -> Dict[str, Any]:
        """Benchmark C++ accelerated inference performance."""
        print(f"Benchmarking C++ inference ({iterations} iterations)...")
        
        if not self.fast_engine:
            return {'error': 'FastInferenceEngine not available'}
        
        latencies = []
        predictions = []
        performance_infos = []
        
        # Warmup
        for i in range(100):
            features = self.test_features[i]
            self.fast_engine.predict_fraud_probability(features)
        
        # Actual benchmark  
        for i in range(iterations):
            features = self.test_features[i % len(self.test_features)]
            
            start_time = time.perf_counter()
            prediction, perf_info = self.fast_engine.predict_fraud_probability(features)
            end_time = time.perf_counter()
            
            latency_ms = (end_time - start_time) * 1000
            latencies.append(latency_ms)
            predictions.append(prediction)
            performance_infos.append(perf_info)
        
        # Analyze which engine was actually used
        cpp_count = sum(1 for info in performance_infos if info.get('engine') == 'cpp')
        python_count = sum(1 for info in performance_infos if info.get('engine') == 'python')
        
        return {
            'iterations': iterations,
            'cpp_engine_used': cpp_count,
            'python_fallback_used': python_count,
            'actual_cpp_ratio': cpp_count / iterations,
            'total_time_s': sum(latencies) / 1000,
            'mean_latency_ms': statistics.mean(latencies),
            'median_latency_ms': statistics.median(latencies),
            'p95_latency_ms': np.percentile(latencies, 95),
            'p99_latency_ms': np.percentile(latencies, 99),
            'min_latency_ms': min(latencies),
            'max_latency_ms': max(latencies),
            'std_latency_ms': statistics.stdev(latencies),
            'throughput_ips': iterations / (sum(latencies) / 1000),
            'predictions_sample': predictions[:10]
        }
    
    def validate_accuracy_consistency(self, sample_size: int = 1000) -> Dict[str, Any]:
        """Validate that C++ and Python implementations produce identical results."""
        print(f"Validating accuracy consistency ({sample_size} samples)...")
        
        if not self.python_model or not self.fast_engine:
            return {'error': 'Both models not available for comparison'}
        
        python_predictions = []
        cpp_predictions = []
        differences = []
        
        for i in range(sample_size):
            features = self.test_features[i]
            
            # Python prediction
            python_pred = self.python_model.predict_proba([features])[0][1]
            python_predictions.append(python_pred)
            
            # C++ prediction (FastInferenceEngine handles fallback internally)
            cpp_pred, _ = self.fast_engine.predict_fraud_probability(features)
            cpp_predictions.append(cpp_pred)
            
            # Calculate absolute difference
            diff = abs(python_pred - cpp_pred)
            differences.append(diff)
        
        return {
            'sample_size': sample_size,
            'max_difference': max(differences),
            'mean_difference': statistics.mean(differences),
            'median_difference': statistics.median(differences),
            'std_difference': statistics.stdev(differences),
            'identical_predictions': sum(1 for d in differences if d < 1e-10),
            'accuracy_match_rate': (sum(1 for d in differences if d < 1e-6) / sample_size),
            'python_pred_stats': {
                'mean': statistics.mean(python_predictions),
                'std': statistics.stdev(python_predictions)
            },
            'cpp_pred_stats': {
                'mean': statistics.mean(cpp_predictions),
                'std': statistics.stdev(cpp_predictions)
            }
        }
    
    def run_comprehensive_benchmark(self) -> Dict[str, Any]:
        """Run complete performance comparison."""
        print("\n" + "="*60)
        print("C++ VS PYTHON XGBOOST PERFORMANCE BENCHMARK")
        print("="*60)
        
        # Python benchmark
        self.results['python_results'] = self.benchmark_python_inference()
        
        # C++ benchmark
        self.results['cpp_results'] = self.benchmark_cpp_inference()
        
        # Accuracy validation
        self.results['accuracy_validation'] = self.validate_accuracy_consistency()
        
        # Generate comparison analysis
        self._generate_comparison_analysis()
        
        return self.results
    
    def _generate_comparison_analysis(self) -> None:
        """Generate detailed performance comparison analysis."""
        
        if 'error' in self.results['python_results'] or 'error' in self.results['cpp_results']:
            self.results['comparison'] = {'error': 'Incomplete benchmark data'}
            return
        
        py_results = self.results['python_results']
        cpp_results = self.results['cpp_results']
        
        # Performance improvements
        latency_improvement = py_results['mean_latency_ms'] / cpp_results['mean_latency_ms']
        throughput_improvement = cpp_results['throughput_ips'] / py_results['throughput_ips']
        p99_improvement = py_results['p99_latency_ms'] / cpp_results['p99_latency_ms']
        
        self.results['comparison'] = {
            'latency_improvement_factor': latency_improvement,
            'throughput_improvement_factor': throughput_improvement,
            'p99_latency_improvement_factor': p99_improvement,
            'python_mean_latency_ms': py_results['mean_latency_ms'],
            'cpp_mean_latency_ms': cpp_results['mean_latency_ms'],
            'python_throughput_ips': py_results['throughput_ips'],
            'cpp_throughput_ips': cpp_results['throughput_ips'],
            'python_p99_latency_ms': py_results['p99_latency_ms'],
            'cpp_p99_latency_ms': cpp_results['p99_latency_ms'],
            'performance_summary': {
                'significant_improvement': latency_improvement > 2.0,
                'improvement_category': self._categorize_improvement(latency_improvement)
            }
        }
    
    def _categorize_improvement(self, improvement_factor: float) -> str:
        """Categorize performance improvement."""
        if improvement_factor >= 10:
            return "Exceptional (10x+)"
        elif improvement_factor >= 5:
            return "Excellent (5x+)"  
        elif improvement_factor >= 2:
            return "Significant (2x+)"
        elif improvement_factor >= 1.5:
            return "Moderate (1.5x+)"
        elif improvement_factor >= 1.1:
            return "Minor (1.1x+)"
        else:
            return "Negligible (<1.1x)"
    
    def save_results(self, filename: str = "cpp_vs_python_benchmark.json") -> None:
        """Save benchmark results to JSON file."""
        with open(filename, 'w') as f:
            json.dump(self.results, f, indent=2, default=str)
        print(f"✓ Results saved to {filename}")
    
    def print_summary(self) -> None:
        """Print benchmark summary."""
        if 'comparison' not in self.results or 'error' in self.results['comparison']:
            print("⚠ Incomplete benchmark results")
            return
        
        comp = self.results['comparison']
        
        print("\n" + "="*60)
        print("PERFORMANCE COMPARISON SUMMARY")
        print("="*60)
        
        print(f"Python Mean Latency:     {comp['python_mean_latency_ms']:.2f}ms")
        print(f"C++ Mean Latency:        {comp['cpp_mean_latency_ms']:.2f}ms")
        print(f"Latency Improvement:     {comp['latency_improvement_factor']:.1f}x")
        print()
        print(f"Python Throughput:       {comp['python_throughput_ips']:.0f} inferences/sec")
        print(f"C++ Throughput:          {comp['cpp_throughput_ips']:.0f} inferences/sec") 
        print(f"Throughput Improvement:  {comp['throughput_improvement_factor']:.1f}x")
        print()
        print(f"Python P99 Latency:      {comp['python_p99_latency_ms']:.2f}ms")
        print(f"C++ P99 Latency:         {comp['cpp_p99_latency_ms']:.2f}ms")
        print(f"P99 Improvement:         {comp['p99_latency_improvement_factor']:.1f}x")
        print()
        print(f"Performance Category:    {comp['performance_summary']['improvement_category']}")
        print(f"Significant Improvement: {comp['performance_summary']['significant_improvement']}")
        
        # Accuracy validation summary
        if 'accuracy_validation' in self.results:
            acc = self.results['accuracy_validation']
            print(f"\nAccuracy Match Rate:     {acc['accuracy_match_rate']*100:.2f}%")
            print(f"Max Prediction Diff:     {acc['max_difference']:.2e}")


def main():
    """Run comprehensive C++ vs Python XGBoost benchmark."""
    
    # Model path
    model_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), 
        "models", 
        "ieee_fraud_model_production.pkl"
    )
    
    if not os.path.exists(model_path):
        print(f"Error: Model file not found at {model_path}")
        return 1
    
    print(f"Using model: {model_path}")
    print(f"C++ inference available: {CPP_AVAILABLE}")
    
    try:
        # Run benchmark
        comparator = InferenceComparator(model_path)
        results = comparator.run_comprehensive_benchmark()
        
        # Print results
        comparator.print_summary()
        
        # Save results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"cpp_vs_python_benchmark_{timestamp}.json"
        comparator.save_results(filename)
        
        print(f"\n✓ Comprehensive benchmark completed successfully")
        print(f"✓ Results saved to {filename}")
        
        return 0
        
    except Exception as e:
        print(f"Benchmark failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())