"""
Performance Benchmarking Infrastructure

Comprehensive benchmarking system for model inference performance with
load testing, stress testing, and regression detection capabilities.

Features:
- Multi-threaded performance benchmarking
- Load testing with concurrent requests
- Memory usage and resource monitoring
- Performance regression detection
- Comprehensive reporting and visualization
"""

import json
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, Callable
from dataclasses import dataclass, field
from queue import Queue
import statistics

import numpy as np
import pandas as pd

# System monitoring
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Plotting (optional)
try:
    import matplotlib.pyplot as plt
    import seaborn as sns
    PLOTTING_AVAILABLE = True
except ImportError:
    PLOTTING_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkConfig:
    """Configuration for performance benchmarking."""
    
    # Test configuration
    warmup_iterations: int = 50
    benchmark_iterations: int = 200
    concurrent_threads: int = 1
    max_threads: int = 8
    
    # Load testing
    load_test_duration_seconds: int = 60
    target_throughput_pps: float = 1000.0
    ramp_up_duration_seconds: int = 10
    
    # Stress testing
    stress_test_enabled: bool = True
    stress_test_multiplier: float = 2.0
    memory_limit_gb: Optional[float] = None
    
    # Monitoring
    monitor_system_resources: bool = True
    monitoring_interval_seconds: float = 1.0
    
    # Output configuration
    output_dir: str = "benchmarks/results"
    save_raw_data: bool = True
    generate_plots: bool = True
    generate_report: bool = True
    
    def validate(self) -> List[str]:
        """Validate benchmark configuration."""
        errors = []
        
        if self.warmup_iterations < 1:
            errors.append("Warmup iterations must be at least 1")
        
        if self.benchmark_iterations < 10:
            errors.append("Benchmark iterations must be at least 10")
        
        if self.concurrent_threads < 1:
            errors.append("Concurrent threads must be at least 1")
        
        if self.load_test_duration_seconds < 5:
            errors.append("Load test duration must be at least 5 seconds")
        
        if self.target_throughput_pps <= 0:
            errors.append("Target throughput must be positive")
        
        return errors


@dataclass
class BenchmarkMetrics:
    """Individual benchmark run metrics."""
    
    # Latency metrics (milliseconds)
    latencies: List[float] = field(default_factory=list)
    mean_latency_ms: float = 0.0
    median_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    std_latency_ms: float = 0.0
    
    # Throughput metrics
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    throughput_pps: float = 0.0
    error_rate: float = 0.0
    
    # Resource metrics
    peak_memory_mb: float = 0.0
    avg_cpu_percent: float = 0.0
    peak_cpu_percent: float = 0.0
    
    # Timing
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime = field(default_factory=datetime.now)
    duration_seconds: float = 0.0
    
    def calculate_derived_metrics(self):
        """Calculate derived metrics from raw data."""
        if self.latencies:
            self.mean_latency_ms = float(np.mean(self.latencies))
            self.median_latency_ms = float(np.median(self.latencies))
            self.p95_latency_ms = float(np.percentile(self.latencies, 95))
            self.p99_latency_ms = float(np.percentile(self.latencies, 99))
            self.min_latency_ms = float(np.min(self.latencies))
            self.max_latency_ms = float(np.max(self.latencies))
            self.std_latency_ms = float(np.std(self.latencies))
        
        if self.total_requests > 0:
            self.error_rate = self.failed_requests / self.total_requests
        
        self.duration_seconds = (self.end_time - self.start_time).total_seconds()
        
        if self.duration_seconds > 0:
            self.throughput_pps = self.successful_requests / self.duration_seconds


@dataclass
class BenchmarkResult:
    """Comprehensive benchmark results."""
    
    # Test identification
    test_name: str = ""
    model_name: str = ""
    test_timestamp: datetime = field(default_factory=datetime.now)
    config: Optional[BenchmarkConfig] = None
    
    # Single-threaded metrics
    single_thread_metrics: Optional[BenchmarkMetrics] = None
    
    # Multi-threaded metrics
    concurrent_metrics: Dict[int, BenchmarkMetrics] = field(default_factory=dict)
    
    # Load test results
    load_test_metrics: Optional[BenchmarkMetrics] = None
    load_test_timeline: List[Tuple[float, float, float]] = field(default_factory=list)  # (time, latency, throughput)
    
    # Stress test results
    stress_test_metrics: Optional[BenchmarkMetrics] = None
    stress_test_passed: bool = False
    
    # Comparison results
    baseline_metrics: Optional[BenchmarkMetrics] = None
    performance_improvement: Dict[str, float] = field(default_factory=dict)
    regression_detected: bool = False
    
    # System information
    system_info: Dict[str, Any] = field(default_factory=dict)
    
    # Errors and warnings
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def add_error(self, error: str):
        """Add error message."""
        self.errors.append(f"{datetime.now().isoformat()}: {error}")
    
    def add_warning(self, warning: str):
        """Add warning message."""
        self.warnings.append(f"{datetime.now().isoformat()}: {warning}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'test_info': {
                'test_name': self.test_name,
                'model_name': self.model_name,
                'test_timestamp': self.test_timestamp.isoformat(),
                'system_info': self.system_info
            },
            'single_thread_metrics': self._metrics_to_dict(self.single_thread_metrics),
            'concurrent_metrics': {
                str(threads): self._metrics_to_dict(metrics)
                for threads, metrics in self.concurrent_metrics.items()
            },
            'load_test_metrics': self._metrics_to_dict(self.load_test_metrics),
            'stress_test_metrics': self._metrics_to_dict(self.stress_test_metrics),
            'performance_comparison': {
                'baseline_metrics': self._metrics_to_dict(self.baseline_metrics),
                'performance_improvement': self.performance_improvement,
                'regression_detected': self.regression_detected
            },
            'issues': {
                'errors': self.errors,
                'warnings': self.warnings
            }
        }
    
    def _metrics_to_dict(self, metrics: Optional[BenchmarkMetrics]) -> Optional[Dict[str, Any]]:
        """Convert metrics to dictionary."""
        if metrics is None:
            return None
        
        return {
            'latency_metrics': {
                'mean_ms': metrics.mean_latency_ms,
                'median_ms': metrics.median_latency_ms,
                'p95_ms': metrics.p95_latency_ms,
                'p99_ms': metrics.p99_latency_ms,
                'min_ms': metrics.min_latency_ms,
                'max_ms': metrics.max_latency_ms,
                'std_ms': metrics.std_latency_ms
            },
            'throughput_metrics': {
                'total_requests': metrics.total_requests,
                'successful_requests': metrics.successful_requests,
                'failed_requests': metrics.failed_requests,
                'throughput_pps': metrics.throughput_pps,
                'error_rate': metrics.error_rate
            },
            'resource_metrics': {
                'peak_memory_mb': metrics.peak_memory_mb,
                'avg_cpu_percent': metrics.avg_cpu_percent,
                'peak_cpu_percent': metrics.peak_cpu_percent
            },
            'timing': {
                'start_time': metrics.start_time.isoformat(),
                'end_time': metrics.end_time.isoformat(),
                'duration_seconds': metrics.duration_seconds
            }
        }


class SystemMonitor:
    """System resource monitoring during benchmarks."""
    
    def __init__(self, monitoring_interval: float = 1.0):
        self.monitoring_interval = monitoring_interval
        self.is_monitoring = False
        self._monitor_thread = None
        self._metrics_queue = Queue()
        
        self.memory_samples = []
        self.cpu_samples = []
        
    def start_monitoring(self):
        """Start system monitoring."""
        if not PSUTIL_AVAILABLE:
            logger.warning("psutil not available, system monitoring disabled")
            return
        
        self.is_monitoring = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
    
    def stop_monitoring(self) -> Tuple[List[float], List[float]]:
        """Stop monitoring and return collected metrics."""
        self.is_monitoring = False
        
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
        
        return self.memory_samples.copy(), self.cpu_samples.copy()
    
    def _monitor_loop(self):
        """Main monitoring loop."""
        process = psutil.Process()
        
        while self.is_monitoring:
            try:
                # Memory usage in MB
                memory_mb = process.memory_info().rss / (1024 * 1024)
                self.memory_samples.append(memory_mb)
                
                # CPU usage percentage
                cpu_percent = process.cpu_percent()
                self.cpu_samples.append(cpu_percent)
                
                time.sleep(self.monitoring_interval)
                
            except Exception as e:
                logger.warning(f"System monitoring error: {e}")
                break


class PerformanceBenchmark:
    """
    Comprehensive performance benchmarking system.
    
    Implements FAANG-level benchmarking with load testing, stress testing,
    and comprehensive performance analysis.
    """
    
    def __init__(self, config: BenchmarkConfig):
        """Initialize performance benchmark."""
        validation_errors = config.validate()
        if validation_errors:
            raise ValueError(f"Invalid benchmark configuration: {validation_errors}")
        
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Create output directory
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize system monitor
        self.system_monitor = SystemMonitor(config.monitoring_interval_seconds)
        
        self.logger.info("PerformanceBenchmark initialized", extra={
            "output_dir": str(self.output_dir),
            "benchmark_iterations": config.benchmark_iterations,
            "max_threads": config.max_threads
        })
    
    def benchmark_model(self, 
                       predict_func: Callable[[np.ndarray], np.ndarray],
                       test_data: np.ndarray,
                       model_name: str = "model",
                       baseline_func: Optional[Callable[[np.ndarray], np.ndarray]] = None) -> BenchmarkResult:
        """
        Comprehensive model benchmarking.
        
        Args:
            predict_func: Model prediction function
            test_data: Test data for benchmarking
            model_name: Name of the model being benchmarked
            baseline_func: Optional baseline model for comparison
            
        Returns:
            BenchmarkResult with comprehensive metrics
        """
        result = BenchmarkResult(
            test_name=f"{model_name}_benchmark",
            model_name=model_name,
            config=self.config
        )
        
        # Collect system information
        result.system_info = self._collect_system_info()
        
        try:
            self.logger.info(f"Starting comprehensive benchmark for {model_name}")
            
            # 1. Single-threaded performance
            result.single_thread_metrics = self._benchmark_single_threaded(predict_func, test_data)
            
            # 2. Multi-threaded performance
            if self.config.max_threads > 1:
                result.concurrent_metrics = self._benchmark_concurrent(predict_func, test_data)
            
            # 3. Load testing
            result.load_test_metrics, result.load_test_timeline = self._run_load_test(predict_func, test_data)
            
            # 4. Stress testing
            if self.config.stress_test_enabled:
                result.stress_test_metrics, result.stress_test_passed = self._run_stress_test(predict_func, test_data)
            
            # 5. Baseline comparison
            if baseline_func is not None:
                result.baseline_metrics = self._benchmark_single_threaded(baseline_func, test_data)
                result.performance_improvement = self._calculate_performance_improvement(
                    result.single_thread_metrics, result.baseline_metrics
                )
                result.regression_detected = self._detect_regression(
                    result.single_thread_metrics, result.baseline_metrics
                )
            
            # 6. Generate outputs
            self._save_results(result)
            
            if self.config.generate_report:
                self._generate_report(result)
            
            if self.config.generate_plots and PLOTTING_AVAILABLE:
                self._generate_plots(result)
            
            self.logger.info(f"Benchmark completed for {model_name}", extra={
                "mean_latency_ms": result.single_thread_metrics.mean_latency_ms,
                "throughput_pps": result.single_thread_metrics.throughput_pps
            })
            
            return result
            
        except Exception as e:
            result.add_error(f"Benchmark failed: {e}")
            self.logger.error(f"Benchmark error for {model_name}", extra={"error": str(e)})
            return result
    
    def _benchmark_single_threaded(self, 
                                  predict_func: Callable[[np.ndarray], np.ndarray],
                                  test_data: np.ndarray) -> BenchmarkMetrics:
        """Benchmark single-threaded performance."""
        metrics = BenchmarkMetrics()
        metrics.start_time = datetime.now()
        
        # Start system monitoring
        if self.config.monitor_system_resources:
            self.system_monitor.start_monitoring()
        
        try:
            # Use single sample for latency testing
            test_sample = test_data[0:1].astype(np.float32)
            
            # Warmup
            for _ in range(self.config.warmup_iterations):
                predict_func(test_sample)
            
            # Benchmark
            latencies = []
            successful = 0
            failed = 0
            
            for i in range(self.config.benchmark_iterations):
                try:
                    start_time = time.perf_counter()
                    _ = predict_func(test_sample)
                    end_time = time.perf_counter()
                    
                    latency_ms = (end_time - start_time) * 1000
                    latencies.append(latency_ms)
                    successful += 1
                    
                except Exception as e:
                    failed += 1
                    if failed == 1:  # Log first error
                        self.logger.warning(f"Prediction error: {e}")
            
            metrics.latencies = latencies
            metrics.total_requests = successful + failed
            metrics.successful_requests = successful
            metrics.failed_requests = failed
            
        finally:
            metrics.end_time = datetime.now()
            
            # Stop monitoring and collect metrics
            if self.config.monitor_system_resources:
                memory_samples, cpu_samples = self.system_monitor.stop_monitoring()
                if memory_samples:
                    metrics.peak_memory_mb = max(memory_samples)
                if cpu_samples:
                    metrics.avg_cpu_percent = statistics.mean(cpu_samples)
                    metrics.peak_cpu_percent = max(cpu_samples)
        
        metrics.calculate_derived_metrics()
        return metrics
    
    def _benchmark_concurrent(self, 
                             predict_func: Callable[[np.ndarray], np.ndarray],
                             test_data: np.ndarray) -> Dict[int, BenchmarkMetrics]:
        """Benchmark concurrent performance with varying thread counts."""
        concurrent_results = {}
        test_sample = test_data[0:1].astype(np.float32)
        
        # Test different thread counts
        thread_counts = [2, 4, 8] if self.config.max_threads >= 8 else [2, self.config.max_threads]
        thread_counts = [t for t in thread_counts if t <= self.config.max_threads]
        
        for num_threads in thread_counts:
            self.logger.info(f"Benchmarking with {num_threads} threads")
            
            metrics = BenchmarkMetrics()
            metrics.start_time = datetime.now()
            
            # Start monitoring
            if self.config.monitor_system_resources:
                self.system_monitor.start_monitoring()
            
            try:
                latencies = []
                successful = 0
                failed = 0
                
                def worker_task():
                    """Individual worker task."""
                    worker_latencies = []
                    worker_successful = 0
                    worker_failed = 0
                    
                    iterations_per_thread = self.config.benchmark_iterations // num_threads
                    
                    for _ in range(iterations_per_thread):
                        try:
                            start_time = time.perf_counter()
                            _ = predict_func(test_sample)
                            end_time = time.perf_counter()
                            
                            latency_ms = (end_time - start_time) * 1000
                            worker_latencies.append(latency_ms)
                            worker_successful += 1
                            
                        except Exception:
                            worker_failed += 1
                    
                    return worker_latencies, worker_successful, worker_failed
                
                # Execute concurrent workers
                with ThreadPoolExecutor(max_workers=num_threads) as executor:
                    futures = [executor.submit(worker_task) for _ in range(num_threads)]
                    
                    for future in as_completed(futures):
                        try:
                            worker_latencies, worker_successful, worker_failed = future.result()
                            latencies.extend(worker_latencies)
                            successful += worker_successful
                            failed += worker_failed
                        except Exception as e:
                            failed += self.config.benchmark_iterations // num_threads
                            self.logger.warning(f"Worker thread failed: {e}")
                
                metrics.latencies = latencies
                metrics.total_requests = successful + failed
                metrics.successful_requests = successful
                metrics.failed_requests = failed
                
            finally:
                metrics.end_time = datetime.now()
                
                # Stop monitoring
                if self.config.monitor_system_resources:
                    memory_samples, cpu_samples = self.system_monitor.stop_monitoring()
                    if memory_samples:
                        metrics.peak_memory_mb = max(memory_samples)
                    if cpu_samples:
                        metrics.avg_cpu_percent = statistics.mean(cpu_samples)
                        metrics.peak_cpu_percent = max(cpu_samples)
            
            metrics.calculate_derived_metrics()
            concurrent_results[num_threads] = metrics
        
        return concurrent_results
    
    def _run_load_test(self, 
                      predict_func: Callable[[np.ndarray], np.ndarray],
                      test_data: np.ndarray) -> Tuple[BenchmarkMetrics, List[Tuple[float, float, float]]]:
        """Run sustained load test."""
        self.logger.info(f"Running load test for {self.config.load_test_duration_seconds}s")
        
        metrics = BenchmarkMetrics()
        timeline = []
        test_sample = test_data[0:1].astype(np.float32)
        
        # Calculate target request rate
        target_interval = 1.0 / self.config.target_throughput_pps
        
        # Shared state for workers
        start_time = time.time()
        end_time = start_time + self.config.load_test_duration_seconds
        ramp_end_time = start_time + self.config.ramp_up_duration_seconds
        
        latencies = []
        successful = 0
        failed = 0
        
        latency_lock = threading.Lock()
        timeline_lock = threading.Lock()
        
        def load_worker():
            """Load test worker."""
            nonlocal successful, failed
            
            worker_successful = 0
            worker_failed = 0
            worker_latencies = []
            
            next_request_time = time.time()
            
            while time.time() < end_time:
                current_time = time.time()
                
                # Ramp up logic
                if current_time < ramp_end_time:
                    ramp_progress = (current_time - start_time) / self.config.ramp_up_duration_seconds
                    current_interval = target_interval / ramp_progress if ramp_progress > 0.1 else target_interval * 10
                else:
                    current_interval = target_interval
                
                # Wait for next request time
                if current_time < next_request_time:
                    time.sleep(next_request_time - current_time)
                
                try:
                    request_start = time.perf_counter()
                    _ = predict_func(test_sample)
                    request_end = time.perf_counter()
                    
                    latency_ms = (request_end - request_start) * 1000
                    worker_latencies.append(latency_ms)
                    worker_successful += 1
                    
                    # Record timeline data every second
                    if len(worker_latencies) % max(1, int(1.0 / current_interval)) == 0:
                        elapsed = time.time() - start_time
                        current_throughput = worker_successful / elapsed if elapsed > 0 else 0
                        
                        with timeline_lock:
                            timeline.append((elapsed, latency_ms, current_throughput))
                    
                except Exception:
                    worker_failed += 1
                
                next_request_time += current_interval
            
            # Update shared state
            with latency_lock:
                latencies.extend(worker_latencies)
                successful += worker_successful
                failed += worker_failed
        
        # Start load test
        metrics.start_time = datetime.now()
        
        if self.config.monitor_system_resources:
            self.system_monitor.start_monitoring()
        
        try:
            # Run load test with multiple workers
            num_workers = min(4, self.config.max_threads)
            
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = [executor.submit(load_worker) for _ in range(num_workers)]
                
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        self.logger.warning(f"Load test worker failed: {e}")
            
            metrics.latencies = latencies
            metrics.total_requests = successful + failed
            metrics.successful_requests = successful
            metrics.failed_requests = failed
            
        finally:
            metrics.end_time = datetime.now()
            
            if self.config.monitor_system_resources:
                memory_samples, cpu_samples = self.system_monitor.stop_monitoring()
                if memory_samples:
                    metrics.peak_memory_mb = max(memory_samples)
                if cpu_samples:
                    metrics.avg_cpu_percent = statistics.mean(cpu_samples)
                    metrics.peak_cpu_percent = max(cpu_samples)
        
        metrics.calculate_derived_metrics()
        return metrics, timeline
    
    def _run_stress_test(self, 
                        predict_func: Callable[[np.ndarray], np.ndarray],
                        test_data: np.ndarray) -> Tuple[BenchmarkMetrics, bool]:
        """Run stress test at elevated load."""
        stress_target = self.config.target_throughput_pps * self.config.stress_test_multiplier
        
        self.logger.info(f"Running stress test at {stress_target:.0f} RPS")
        
        # Temporarily increase target throughput
        original_target = self.config.target_throughput_pps
        original_duration = self.config.load_test_duration_seconds
        
        self.config.target_throughput_pps = stress_target
        self.config.load_test_duration_seconds = min(30, self.config.load_test_duration_seconds)  # Shorter stress test
        
        try:
            stress_metrics, _ = self._run_load_test(predict_func, test_data)
            
            # Determine if stress test passed
            stress_passed = (
                stress_metrics.error_rate < 0.01 and  # Less than 1% errors
                stress_metrics.mean_latency_ms < 10.0 and  # Less than 10ms average latency
                stress_metrics.throughput_pps > stress_target * 0.8  # Achieved at least 80% of target
            )
            
            return stress_metrics, stress_passed
            
        finally:
            # Restore original configuration
            self.config.target_throughput_pps = original_target
            self.config.load_test_duration_seconds = original_duration
    
    def _calculate_performance_improvement(self, 
                                         current_metrics: BenchmarkMetrics,
                                         baseline_metrics: BenchmarkMetrics) -> Dict[str, float]:
        """Calculate performance improvements over baseline."""
        improvements = {}
        
        if baseline_metrics.mean_latency_ms > 0:
            improvements['latency_improvement_factor'] = baseline_metrics.mean_latency_ms / current_metrics.mean_latency_ms
        
        if baseline_metrics.throughput_pps > 0:
            improvements['throughput_improvement_factor'] = current_metrics.throughput_pps / baseline_metrics.throughput_pps
        
        if baseline_metrics.peak_memory_mb > 0:
            improvements['memory_efficiency_ratio'] = baseline_metrics.peak_memory_mb / current_metrics.peak_memory_mb
        
        improvements['error_rate_change'] = current_metrics.error_rate - baseline_metrics.error_rate
        
        return improvements
    
    def _detect_regression(self, 
                          current_metrics: BenchmarkMetrics,
                          baseline_metrics: BenchmarkMetrics) -> bool:
        """Detect performance regression."""
        # Regression if current performance is significantly worse
        latency_regression = current_metrics.mean_latency_ms > baseline_metrics.mean_latency_ms * 1.1
        throughput_regression = current_metrics.throughput_pps < baseline_metrics.throughput_pps * 0.9
        error_rate_regression = current_metrics.error_rate > baseline_metrics.error_rate + 0.01
        
        return latency_regression or throughput_regression or error_rate_regression
    
    def _collect_system_info(self) -> Dict[str, Any]:
        """Collect system information."""
        system_info = {
            'timestamp': datetime.now().isoformat(),
            'python_version': f"{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}"
        }
        
        if PSUTIL_AVAILABLE:
            system_info.update({
                'cpu_count': psutil.cpu_count(),
                'memory_total_gb': psutil.virtual_memory().total / (1024**3),
                'platform': __import__('platform').platform()
            })
        
        return system_info
    
    def _save_results(self, result: BenchmarkResult):
        """Save benchmark results to files."""
        if not self.config.save_raw_data:
            return
        
        try:
            # Save complete results as JSON
            results_file = self.output_dir / f"{result.model_name}_benchmark_results.json"
            with open(results_file, 'w') as f:
                json.dump(result.to_dict(), f, indent=2, default=str)
            
            # Save raw latency data
            if result.single_thread_metrics and result.single_thread_metrics.latencies:
                latency_file = self.output_dir / f"{result.model_name}_latencies.csv"
                pd.DataFrame({
                    'latency_ms': result.single_thread_metrics.latencies
                }).to_csv(latency_file, index=False)
            
            # Save load test timeline
            if result.load_test_timeline:
                timeline_file = self.output_dir / f"{result.model_name}_load_test_timeline.csv"
                pd.DataFrame(result.load_test_timeline, 
                           columns=['elapsed_seconds', 'latency_ms', 'throughput_pps']).to_csv(timeline_file, index=False)
            
            self.logger.info("Benchmark results saved", extra={
                "results_file": str(results_file)
            })
            
        except Exception as e:
            self.logger.warning(f"Failed to save benchmark results: {e}")
    
    def _generate_report(self, result: BenchmarkResult):
        """Generate comprehensive benchmark report."""
        try:
            report_file = self.output_dir / f"{result.model_name}_benchmark_report.md"
            
            with open(report_file, 'w') as f:
                f.write(f"# Performance Benchmark Report: {result.model_name}\n\n")
                f.write(f"**Test Date**: {result.test_timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                
                # Single-threaded results
                if result.single_thread_metrics:
                    f.write("## Single-Threaded Performance\n\n")
                    m = result.single_thread_metrics
                    f.write(f"- **Mean Latency**: {m.mean_latency_ms:.2f}ms\n")
                    f.write(f"- **P95 Latency**: {m.p95_latency_ms:.2f}ms\n")
                    f.write(f"- **P99 Latency**: {m.p99_latency_ms:.2f}ms\n")
                    f.write(f"- **Throughput**: {m.throughput_pps:.1f} predictions/second\n")
                    f.write(f"- **Error Rate**: {m.error_rate:.4f}\n\n")
                
                # Concurrent results
                if result.concurrent_metrics:
                    f.write("## Concurrent Performance\n\n")
                    for threads, metrics in result.concurrent_metrics.items():
                        f.write(f"### {threads} Threads\n")
                        f.write(f"- **Mean Latency**: {metrics.mean_latency_ms:.2f}ms\n")
                        f.write(f"- **Throughput**: {metrics.throughput_pps:.1f} predictions/second\n")
                        f.write(f"- **Error Rate**: {metrics.error_rate:.4f}\n\n")
                
                # Load test results
                if result.load_test_metrics:
                    f.write("## Load Test Results\n\n")
                    m = result.load_test_metrics
                    f.write(f"- **Target Throughput**: {self.config.target_throughput_pps:.0f} RPS\n")
                    f.write(f"- **Achieved Throughput**: {m.throughput_pps:.1f} RPS\n")
                    f.write(f"- **Mean Latency**: {m.mean_latency_ms:.2f}ms\n")
                    f.write(f"- **P95 Latency**: {m.p95_latency_ms:.2f}ms\n")
                    f.write(f"- **Error Rate**: {m.error_rate:.4f}\n\n")
                
                # Stress test results
                if result.stress_test_metrics:
                    f.write("## Stress Test Results\n\n")
                    m = result.stress_test_metrics
                    f.write(f"- **Stress Test Passed**: {'✅ Yes' if result.stress_test_passed else '❌ No'}\n")
                    f.write(f"- **Achieved Throughput**: {m.throughput_pps:.1f} RPS\n")
                    f.write(f"- **Mean Latency**: {m.mean_latency_ms:.2f}ms\n")
                    f.write(f"- **Error Rate**: {m.error_rate:.4f}\n\n")
                
                # Performance comparison
                if result.baseline_metrics:
                    f.write("## Performance Comparison\n\n")
                    improvements = result.performance_improvement
                    f.write(f"- **Latency Improvement**: {improvements.get('latency_improvement_factor', 1.0):.2f}x\n")
                    f.write(f"- **Throughput Improvement**: {improvements.get('throughput_improvement_factor', 1.0):.2f}x\n")
                    f.write(f"- **Memory Efficiency**: {improvements.get('memory_efficiency_ratio', 1.0):.2f}x\n")
                    f.write(f"- **Regression Detected**: {'❌ Yes' if result.regression_detected else '✅ No'}\n\n")
                
                # Issues
                if result.errors or result.warnings:
                    f.write("## Issues\n\n")
                    if result.errors:
                        f.write("### Errors\n")
                        for error in result.errors:
                            f.write(f"- {error}\n")
                        f.write("\n")
                    if result.warnings:
                        f.write("### Warnings\n")
                        for warning in result.warnings:
                            f.write(f"- {warning}\n")
                        f.write("\n")
            
            self.logger.info("Benchmark report generated", extra={
                "report_file": str(report_file)
            })
            
        except Exception as e:
            self.logger.warning(f"Failed to generate benchmark report: {e}")
    
    def _generate_plots(self, result: BenchmarkResult):
        """Generate performance visualization plots."""
        try:
            import matplotlib
            matplotlib.use('Agg')  # Non-interactive backend
            
            # Latency distribution plot
            if result.single_thread_metrics and result.single_thread_metrics.latencies:
                plt.figure(figsize=(10, 6))
                plt.hist(result.single_thread_metrics.latencies, bins=50, alpha=0.7, edgecolor='black')
                plt.xlabel('Latency (ms)')
                plt.ylabel('Frequency')
                plt.title(f'Latency Distribution - {result.model_name}')
                plt.grid(True, alpha=0.3)
                
                plot_file = self.output_dir / f"{result.model_name}_latency_distribution.png"
                plt.savefig(plot_file, dpi=150, bbox_inches='tight')
                plt.close()
            
            # Load test timeline plot
            if result.load_test_timeline:
                timeline_df = pd.DataFrame(result.load_test_timeline, 
                                         columns=['elapsed_seconds', 'latency_ms', 'throughput_pps'])
                
                fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
                
                # Latency over time
                ax1.plot(timeline_df['elapsed_seconds'], timeline_df['latency_ms'])
                ax1.set_xlabel('Time (seconds)')
                ax1.set_ylabel('Latency (ms)')
                ax1.set_title('Latency Over Time')
                ax1.grid(True, alpha=0.3)
                
                # Throughput over time
                ax2.plot(timeline_df['elapsed_seconds'], timeline_df['throughput_pps'])
                ax2.set_xlabel('Time (seconds)')
                ax2.set_ylabel('Throughput (RPS)')
                ax2.set_title('Throughput Over Time')
                ax2.grid(True, alpha=0.3)
                
                plt.tight_layout()
                
                timeline_plot_file = self.output_dir / f"{result.model_name}_load_test_timeline.png"
                plt.savefig(timeline_plot_file, dpi=150, bbox_inches='tight')
                plt.close()
            
            # Concurrent performance comparison
            if result.concurrent_metrics:
                threads = list(result.concurrent_metrics.keys())
                latencies = [result.concurrent_metrics[t].mean_latency_ms for t in threads]
                throughputs = [result.concurrent_metrics[t].throughput_pps for t in threads]
                
                fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
                
                ax1.plot(threads, latencies, 'o-')
                ax1.set_xlabel('Number of Threads')
                ax1.set_ylabel('Mean Latency (ms)')
                ax1.set_title('Latency vs Thread Count')
                ax1.grid(True, alpha=0.3)
                
                ax2.plot(threads, throughputs, 'o-')
                ax2.set_xlabel('Number of Threads')
                ax2.set_ylabel('Throughput (RPS)')
                ax2.set_title('Throughput vs Thread Count')
                ax2.grid(True, alpha=0.3)
                
                plt.tight_layout()
                
                concurrent_plot_file = self.output_dir / f"{result.model_name}_concurrent_performance.png"
                plt.savefig(concurrent_plot_file, dpi=150, bbox_inches='tight')
                plt.close()
            
            self.logger.info("Performance plots generated", extra={
                "output_dir": str(self.output_dir)
            })
            
        except Exception as e:
            self.logger.warning(f"Failed to generate performance plots: {e}")


# Factory function for easy instantiation
def create_performance_benchmark(config: Optional[BenchmarkConfig] = None) -> PerformanceBenchmark:
    """Create PerformanceBenchmark with default or custom configuration."""
    if config is None:
        config = BenchmarkConfig()
    return PerformanceBenchmark(config)