"""
Model Validation Framework

Comprehensive validation framework for Python vs ONNX model accuracy and performance.
Implements FAANG-level testing standards with statistical analysis and business impact assessment.

Key Features:
- Statistical accuracy validation with significance testing
- Business decision impact analysis  
- Performance regression detection
- Load testing and stress testing capabilities
- Production deployment validation
"""

import json
import logging
import statistics
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import xgboost as xgb

# Scientific computing
try:
    from scipy import stats
    from scipy.stats import ttest_rel, kstest
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

# ONNX dependencies
try:
    import onnx
    import onnxruntime as ort
    ONNX_AVAILABLE = True
except ImportError:
    ONNX_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class AccuracyValidationResult:
    """Results from comprehensive accuracy validation."""
    
    # Statistical metrics
    max_absolute_difference: float = float('inf')
    mean_absolute_difference: float = float('inf')
    std_absolute_difference: float = float('inf')
    correlation: float = 0.0
    r_squared: float = 0.0
    
    # Business impact metrics
    decision_agreement_rate: float = 0.0
    false_positive_difference: float = 0.0
    false_negative_difference: float = 0.0
    
    # Statistical significance
    statistical_significance_p: float = 1.0
    distribution_similarity_p: float = 1.0
    
    # Test coverage
    test_samples: int = 0
    edge_cases_tested: int = 0
    boundary_cases_tested: int = 0
    
    # Validation status
    passed: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def add_error(self, error: str):
        self.errors.append(error)
        self.passed = False
    
    def add_warning(self, warning: str):
        self.warnings.append(warning)


@dataclass 
class PerformanceValidationResult:
    """Results from performance validation and benchmarking."""
    
    # Latency metrics (milliseconds)
    python_mean_latency_ms: float = 0.0
    python_p50_latency_ms: float = 0.0
    python_p95_latency_ms: float = 0.0
    python_p99_latency_ms: float = 0.0
    
    onnx_mean_latency_ms: float = 0.0
    onnx_p50_latency_ms: float = 0.0
    onnx_p95_latency_ms: float = 0.0
    onnx_p99_latency_ms: float = 0.0
    
    # Performance improvements
    latency_improvement_factor: float = 1.0
    throughput_improvement_factor: float = 1.0
    
    # Throughput metrics (predictions per second)
    python_throughput_pps: float = 0.0
    onnx_throughput_pps: float = 0.0
    
    # Memory metrics (MB)
    python_memory_mb: float = 0.0
    onnx_memory_mb: float = 0.0
    memory_efficiency_ratio: float = 1.0
    
    # Load testing results
    max_sustained_throughput_pps: float = 0.0
    latency_at_max_throughput_ms: float = 0.0
    stability_under_load: bool = False
    
    # Validation status
    meets_performance_target: bool = False
    performance_regression_detected: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def add_error(self, error: str):
        self.errors.append(error)
        self.meets_performance_target = False
    
    def add_warning(self, warning: str):
        self.warnings.append(warning)


class ModelAccuracyValidator:
    """
    Comprehensive validation framework for Python vs ONNX model accuracy.
    
    Implements statistical analysis, business impact assessment, and edge case testing
    with FAANG-level quality standards.
    """
    
    def __init__(self, 
                 accuracy_tolerance: float = 1e-6,
                 correlation_threshold: float = 0.9999,
                 decision_agreement_threshold: float = 0.999):
        """
        Initialize accuracy validator.
        
        Args:
            accuracy_tolerance: Maximum allowed absolute prediction difference
            correlation_threshold: Minimum correlation between predictions
            decision_agreement_threshold: Minimum business decision agreement rate
        """
        self.accuracy_tolerance = accuracy_tolerance
        self.correlation_threshold = correlation_threshold
        self.decision_agreement_threshold = decision_agreement_threshold
        
        self.logger = logging.getLogger(__name__)
        
        if not ONNX_AVAILABLE:
            raise ImportError("ONNX dependencies required for model validation")
    
    def validate_accuracy(self, 
                         python_model: xgb.XGBClassifier,
                         onnx_model_path: str,
                         test_data: np.ndarray,
                         test_labels: Optional[np.ndarray] = None) -> AccuracyValidationResult:
        """
        Comprehensive accuracy validation with statistical analysis.
        
        Args:
            python_model: Original XGBoost model
            onnx_model_path: Path to ONNX model file
            test_data: Test dataset for validation
            test_labels: Optional true labels for additional validation
            
        Returns:
            AccuracyValidationResult with comprehensive metrics
        """
        result = AccuracyValidationResult()
        result.test_samples = len(test_data)
        
        try:
            self.logger.info("Starting comprehensive accuracy validation", extra={
                "test_samples": result.test_samples,
                "accuracy_tolerance": self.accuracy_tolerance
            })
            
            # 1. Get predictions from both models
            python_preds = self._get_python_predictions(python_model, test_data)
            onnx_preds = self._get_onnx_predictions(onnx_model_path, test_data)
            
            # 2. Statistical analysis
            self._compute_statistical_metrics(python_preds, onnx_preds, result)
            
            # 3. Business impact analysis
            self._compute_business_metrics(python_preds, onnx_preds, result)
            
            # 4. Statistical significance testing
            if SCIPY_AVAILABLE:
                self._compute_significance_tests(python_preds, onnx_preds, result)
            
            # 5. Edge case testing
            self._test_edge_cases(python_model, onnx_model_path, result)
            
            # 6. Boundary case testing  
            self._test_boundary_cases(python_model, onnx_model_path, result)
            
            # 7. Validate against thresholds
            self._validate_accuracy_thresholds(result)
            
            # 8. Additional validation with true labels
            if test_labels is not None:
                self._validate_against_ground_truth(python_preds, onnx_preds, test_labels, result)
            
            self.logger.info("Accuracy validation completed", extra={
                "max_absolute_difference": result.max_absolute_difference,
                "correlation": result.correlation,
                "decision_agreement": result.decision_agreement_rate,
                "validation_passed": result.passed
            })
            
            return result
            
        except Exception as e:
            result.add_error(f"Accuracy validation failed: {e}")
            self.logger.error("Accuracy validation error", extra={"error": str(e)})
            return result
    
    def _get_python_predictions(self, model: xgb.XGBClassifier, X: np.ndarray) -> np.ndarray:
        """Get predictions from Python XGBoost model."""
        return model.predict_proba(X.astype(np.float32))[:, 1]
    
    def _get_onnx_predictions(self, model_path: str, X: np.ndarray) -> np.ndarray:
        """Get predictions from ONNX model."""
        session = ort.InferenceSession(model_path)
        input_name = session.get_inputs()[0].name
        
        outputs = session.run(None, {input_name: X.astype(np.float32)})
        
        # Handle different output formats
        if len(outputs) == 2:  # label and probabilities
            probabilities = outputs[1]
            if probabilities.shape[1] == 2:
                return probabilities[:, 1]  # Positive class probability
            else:
                return probabilities[:, 0]
        else:
            return outputs[0]
    
    def _compute_statistical_metrics(self, 
                                   python_preds: np.ndarray, 
                                   onnx_preds: np.ndarray,
                                   result: AccuracyValidationResult):
        """Compute comprehensive statistical accuracy metrics."""
        differences = np.abs(python_preds - onnx_preds)
        
        result.max_absolute_difference = float(np.max(differences))
        result.mean_absolute_difference = float(np.mean(differences))
        result.std_absolute_difference = float(np.std(differences))
        
        # Correlation analysis
        if len(python_preds) > 1:
            correlation_matrix = np.corrcoef(python_preds, onnx_preds)
            result.correlation = float(correlation_matrix[0, 1])
            result.r_squared = result.correlation ** 2
        
        self.logger.debug("Statistical metrics computed", extra={
            "max_diff": result.max_absolute_difference,
            "mean_diff": result.mean_absolute_difference,
            "correlation": result.correlation
        })
    
    def _compute_business_metrics(self,
                                python_preds: np.ndarray,
                                onnx_preds: np.ndarray, 
                                result: AccuracyValidationResult):
        """Compute business impact metrics."""
        # Decision agreement (using 0.5 threshold)
        python_decisions = (python_preds > 0.5).astype(int)
        onnx_decisions = (onnx_preds > 0.5).astype(int)
        
        result.decision_agreement_rate = float(np.mean(python_decisions == onnx_decisions))
        
        # False positive/negative analysis
        disagreements = python_decisions != onnx_decisions
        if np.any(disagreements):
            # Python says fraud, ONNX says not fraud
            python_fraud_onnx_not = (python_decisions == 1) & (onnx_decisions == 0)
            # Python says not fraud, ONNX says fraud  
            python_not_onnx_fraud = (python_decisions == 0) & (onnx_decisions == 1)
            
            result.false_positive_difference = float(np.sum(python_not_onnx_fraud))
            result.false_negative_difference = float(np.sum(python_fraud_onnx_not))
        
        self.logger.debug("Business metrics computed", extra={
            "decision_agreement": result.decision_agreement_rate,
            "fp_difference": result.false_positive_difference,
            "fn_difference": result.false_negative_difference
        })
    
    def _compute_significance_tests(self,
                                  python_preds: np.ndarray,
                                  onnx_preds: np.ndarray,
                                  result: AccuracyValidationResult):
        """Compute statistical significance tests."""
        try:
            # Paired t-test for mean difference
            t_stat, p_value = ttest_rel(python_preds, onnx_preds)
            result.statistical_significance_p = float(p_value)
            
            # Kolmogorov-Smirnov test for distribution similarity
            ks_stat, ks_p = kstest(python_preds - onnx_preds, 'norm')
            result.distribution_similarity_p = float(ks_p)
            
            self.logger.debug("Significance tests completed", extra={
                "t_test_p": result.statistical_significance_p,
                "ks_test_p": result.distribution_similarity_p
            })
            
        except Exception as e:
            result.add_warning(f"Statistical significance tests failed: {e}")
    
    def _test_edge_cases(self,
                        python_model: xgb.XGBClassifier,
                        onnx_model_path: str,
                        result: AccuracyValidationResult):
        """Test model behavior on edge cases."""
        try:
            n_features = python_model.n_features_in_
            edge_cases = []
            
            # Generate edge cases
            edge_cases.append(np.zeros((1, n_features), dtype=np.float32))  # All zeros
            edge_cases.append(np.ones((1, n_features), dtype=np.float32))   # All ones
            edge_cases.append(np.full((1, n_features), 1000.0, dtype=np.float32))  # Large positive
            edge_cases.append(np.full((1, n_features), -1000.0, dtype=np.float32)) # Large negative
            edge_cases.append(np.full((1, n_features), 1e-10, dtype=np.float32))   # Very small positive
            edge_cases.append(np.full((1, n_features), -1e-10, dtype=np.float32))  # Very small negative
            
            edge_case_errors = []
            
            for i, case in enumerate(edge_cases):
                try:
                    python_pred = self._get_python_predictions(python_model, case)
                    onnx_pred = self._get_onnx_predictions(onnx_model_path, case)
                    
                    diff = abs(python_pred[0] - onnx_pred[0])
                    if diff > self.accuracy_tolerance:
                        edge_case_errors.append(f"Edge case {i}: difference {diff:.2e}")
                        
                except Exception as e:
                    edge_case_errors.append(f"Edge case {i} failed: {e}")
            
            result.edge_cases_tested = len(edge_cases)
            
            if edge_case_errors:
                result.add_warning(f"Edge case issues: {edge_case_errors}")
            
            self.logger.debug("Edge case testing completed", extra={
                "cases_tested": result.edge_cases_tested,
                "errors": len(edge_case_errors)
            })
            
        except Exception as e:
            result.add_warning(f"Edge case testing failed: {e}")
    
    def _test_boundary_cases(self,
                           python_model: xgb.XGBClassifier,
                           onnx_model_path: str,
                           result: AccuracyValidationResult):
        """Test model behavior on boundary cases."""
        try:
            n_features = python_model.n_features_in_
            boundary_cases = []
            
            # Generate boundary cases around common decision boundaries
            np.random.seed(42)  # Reproducible tests
            
            # Cases around 0.5 probability (decision boundary)
            for _ in range(10):
                # Generate cases that might produce predictions near 0.5
                case = np.random.randn(1, n_features).astype(np.float32) * 0.1
                boundary_cases.append(case)
            
            boundary_errors = []
            
            for i, case in enumerate(boundary_cases):
                try:
                    python_pred = self._get_python_predictions(python_model, case)
                    onnx_pred = self._get_onnx_predictions(onnx_model_path, case)
                    
                    diff = abs(python_pred[0] - onnx_pred[0])
                    if diff > self.accuracy_tolerance:
                        boundary_errors.append(f"Boundary case {i}: difference {diff:.2e}")
                        
                except Exception as e:
                    boundary_errors.append(f"Boundary case {i} failed: {e}")
            
            result.boundary_cases_tested = len(boundary_cases)
            
            if boundary_errors:
                result.add_warning(f"Boundary case issues: {boundary_errors}")
            
            self.logger.debug("Boundary case testing completed", extra={
                "cases_tested": result.boundary_cases_tested,
                "errors": len(boundary_errors)
            })
            
        except Exception as e:
            result.add_warning(f"Boundary case testing failed: {e}")
    
    def _validate_accuracy_thresholds(self, result: AccuracyValidationResult):
        """Validate results against accuracy thresholds."""
        checks_passed = 0
        total_checks = 0
        
        # Check accuracy tolerance
        total_checks += 1
        if result.max_absolute_difference < self.accuracy_tolerance:
            checks_passed += 1
        else:
            result.add_error(f"Max absolute difference {result.max_absolute_difference:.2e} exceeds tolerance {self.accuracy_tolerance:.2e}")
        
        # Check correlation
        total_checks += 1
        if result.correlation > self.correlation_threshold:
            checks_passed += 1
        else:
            result.add_error(f"Correlation {result.correlation:.6f} below threshold {self.correlation_threshold:.6f}")
        
        # Check decision agreement
        total_checks += 1
        if result.decision_agreement_rate > self.decision_agreement_threshold:
            checks_passed += 1
        else:
            result.add_error(f"Decision agreement {result.decision_agreement_rate:.4f} below threshold {self.decision_agreement_threshold:.4f}")
        
        # Check statistical significance (no significant difference expected)
        if SCIPY_AVAILABLE and result.statistical_significance_p > 0:
            total_checks += 1
            if result.statistical_significance_p > 0.05:  # No significant difference
                checks_passed += 1
            else:
                result.add_warning(f"Statistical difference detected (p={result.statistical_significance_p:.6f})")
        
        result.passed = (checks_passed == total_checks) and len(result.errors) == 0
        
        self.logger.info("Accuracy threshold validation completed", extra={
            "checks_passed": checks_passed,
            "total_checks": total_checks,
            "validation_passed": result.passed
        })
    
    def _validate_against_ground_truth(self,
                                     python_preds: np.ndarray,
                                     onnx_preds: np.ndarray,
                                     true_labels: np.ndarray,
                                     result: AccuracyValidationResult):
        """Additional validation against ground truth labels."""
        try:
            from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
            
            # Convert predictions to decisions
            python_decisions = (python_preds > 0.5).astype(int)
            onnx_decisions = (onnx_preds > 0.5).astype(int)
            
            # Calculate metrics for both models
            python_accuracy = accuracy_score(true_labels, python_decisions)
            onnx_accuracy = accuracy_score(true_labels, onnx_decisions)
            
            accuracy_diff = abs(python_accuracy - onnx_accuracy)
            
            if accuracy_diff > 0.001:  # 0.1% tolerance
                result.add_warning(f"Ground truth accuracy difference: {accuracy_diff:.4f}")
            
            self.logger.debug("Ground truth validation completed", extra={
                "python_accuracy": python_accuracy,
                "onnx_accuracy": onnx_accuracy,
                "accuracy_difference": accuracy_diff
            })
            
        except Exception as e:
            result.add_warning(f"Ground truth validation failed: {e}")


class PerformanceValidator:
    """
    Performance validation and benchmarking for model inference.
    
    Validates performance improvements and detects regressions with comprehensive
    load testing and stress testing capabilities.
    """
    
    def __init__(self, 
                 target_improvement_factor: float = 5.0,
                 max_acceptable_latency_ms: float = 5.0):
        """
        Initialize performance validator.
        
        Args:
            target_improvement_factor: Minimum expected performance improvement
            max_acceptable_latency_ms: Maximum acceptable inference latency
        """
        self.target_improvement_factor = target_improvement_factor
        self.max_acceptable_latency_ms = max_acceptable_latency_ms
        
        self.logger = logging.getLogger(__name__)
        
        if not ONNX_AVAILABLE:
            raise ImportError("ONNX dependencies required for performance validation")
    
    def validate_performance(self,
                           python_model: xgb.XGBClassifier,
                           onnx_model_path: str,
                           test_data: np.ndarray,
                           warmup_iterations: int = 20,
                           benchmark_iterations: int = 100) -> PerformanceValidationResult:
        """
        Comprehensive performance validation and benchmarking.
        
        Args:
            python_model: Original XGBoost model
            onnx_model_path: Path to ONNX model file
            test_data: Test dataset for benchmarking
            warmup_iterations: Number of warmup iterations
            benchmark_iterations: Number of benchmark iterations
            
        Returns:
            PerformanceValidationResult with comprehensive metrics
        """
        result = PerformanceValidationResult()
        
        try:
            self.logger.info("Starting performance validation", extra={
                "test_samples": len(test_data),
                "warmup_iterations": warmup_iterations,
                "benchmark_iterations": benchmark_iterations
            })
            
            # 1. Single prediction latency benchmarking
            self._benchmark_single_prediction_latency(python_model, onnx_model_path, 
                                                    test_data, result, warmup_iterations, benchmark_iterations)
            
            # 2. Batch prediction throughput benchmarking
            self._benchmark_batch_throughput(python_model, onnx_model_path, test_data, result)
            
            # 3. Memory usage analysis
            self._analyze_memory_usage(python_model, onnx_model_path, result)
            
            # 4. Load testing
            self._perform_load_testing(python_model, onnx_model_path, test_data, result)
            
            # 5. Validate performance targets
            self._validate_performance_targets(result)
            
            self.logger.info("Performance validation completed", extra={
                "latency_improvement": result.latency_improvement_factor,
                "throughput_improvement": result.throughput_improvement_factor,
                "meets_targets": result.meets_performance_target
            })
            
            return result
            
        except Exception as e:
            result.add_error(f"Performance validation failed: {e}")
            self.logger.error("Performance validation error", extra={"error": str(e)})
            return result
    
    def _benchmark_single_prediction_latency(self,
                                           python_model: xgb.XGBClassifier,
                                           onnx_model_path: str,
                                           test_data: np.ndarray,
                                           result: PerformanceValidationResult,
                                           warmup_iterations: int,
                                           benchmark_iterations: int):
        """Benchmark single prediction latency."""
        # Use single sample for latency testing
        single_sample = test_data[0:1].astype(np.float32)
        
        # Python model benchmarking
        python_times = self._benchmark_model_latency(
            lambda x: python_model.predict_proba(x)[:, 1],
            single_sample, warmup_iterations, benchmark_iterations
        )
        
        result.python_mean_latency_ms = float(np.mean(python_times))
        result.python_p50_latency_ms = float(np.percentile(python_times, 50))
        result.python_p95_latency_ms = float(np.percentile(python_times, 95))
        result.python_p99_latency_ms = float(np.percentile(python_times, 99))
        
        # ONNX model benchmarking
        session = ort.InferenceSession(onnx_model_path)
        input_name = session.get_inputs()[0].name
        
        def onnx_predict(x):
            outputs = session.run(None, {input_name: x})
            if len(outputs) == 2:
                return outputs[1][:, 1] if outputs[1].shape[1] == 2 else outputs[1][:, 0]
            return outputs[0]
        
        onnx_times = self._benchmark_model_latency(
            onnx_predict, single_sample, warmup_iterations, benchmark_iterations
        )
        
        result.onnx_mean_latency_ms = float(np.mean(onnx_times))
        result.onnx_p50_latency_ms = float(np.percentile(onnx_times, 50))
        result.onnx_p95_latency_ms = float(np.percentile(onnx_times, 95))
        result.onnx_p99_latency_ms = float(np.percentile(onnx_times, 99))
        
        # Calculate improvement factor
        if result.onnx_mean_latency_ms > 0:
            result.latency_improvement_factor = result.python_mean_latency_ms / result.onnx_mean_latency_ms
        
        self.logger.debug("Latency benchmarking completed", extra={
            "python_mean_ms": result.python_mean_latency_ms,
            "onnx_mean_ms": result.onnx_mean_latency_ms,
            "improvement_factor": result.latency_improvement_factor
        })
    
    def _benchmark_model_latency(self, predict_func, test_sample: np.ndarray, 
                               warmup_iterations: int, benchmark_iterations: int) -> List[float]:
        """Benchmark model prediction latency."""
        # Warmup
        for _ in range(warmup_iterations):
            predict_func(test_sample)
        
        # Benchmark
        times = []
        for _ in range(benchmark_iterations):
            start_time = time.perf_counter()
            predict_func(test_sample)
            end_time = time.perf_counter()
            times.append((end_time - start_time) * 1000)  # Convert to milliseconds
        
        return times
    
    def _benchmark_batch_throughput(self,
                                  python_model: xgb.XGBClassifier,
                                  onnx_model_path: str,
                                  test_data: np.ndarray,
                                  result: PerformanceValidationResult):
        """Benchmark batch prediction throughput."""
        batch_size = min(1000, len(test_data))
        test_batch = test_data[:batch_size].astype(np.float32)
        
        # Python throughput
        start_time = time.perf_counter()
        python_model.predict_proba(test_batch)
        python_duration = time.perf_counter() - start_time
        result.python_throughput_pps = batch_size / python_duration
        
        # ONNX throughput
        session = ort.InferenceSession(onnx_model_path)
        input_name = session.get_inputs()[0].name
        
        start_time = time.perf_counter()
        session.run(None, {input_name: test_batch})
        onnx_duration = time.perf_counter() - start_time
        result.onnx_throughput_pps = batch_size / onnx_duration
        
        # Calculate improvement
        if result.python_throughput_pps > 0:
            result.throughput_improvement_factor = result.onnx_throughput_pps / result.python_throughput_pps
        
        self.logger.debug("Throughput benchmarking completed", extra={
            "python_pps": result.python_throughput_pps,
            "onnx_pps": result.onnx_throughput_pps,
            "improvement_factor": result.throughput_improvement_factor
        })
    
    def _analyze_memory_usage(self,
                            python_model: xgb.XGBClassifier,
                            onnx_model_path: str,
                            result: PerformanceValidationResult):
        """Analyze memory usage of both models."""
        try:
            import psutil
            import os
            
            # Python model memory estimation
            with tempfile.NamedTemporaryFile(suffix='.json') as tmp_file:
                python_model.save_model(tmp_file.name)
                result.python_memory_mb = os.path.getsize(tmp_file.name) / (1024 * 1024)
            
            # ONNX model memory
            result.onnx_memory_mb = os.path.getsize(onnx_model_path) / (1024 * 1024)
            
            # Memory efficiency ratio
            if result.onnx_memory_mb > 0:
                result.memory_efficiency_ratio = result.python_memory_mb / result.onnx_memory_mb
            
            self.logger.debug("Memory analysis completed", extra={
                "python_memory_mb": result.python_memory_mb,
                "onnx_memory_mb": result.onnx_memory_mb,
                "efficiency_ratio": result.memory_efficiency_ratio
            })
            
        except Exception as e:
            result.add_warning(f"Memory analysis failed: {e}")
    
    def _perform_load_testing(self,
                            python_model: xgb.XGBClassifier,
                            onnx_model_path: str,
                            test_data: np.ndarray,
                            result: PerformanceValidationResult):
        """Perform load testing to find maximum sustained throughput."""
        try:
            session = ort.InferenceSession(onnx_model_path)
            input_name = session.get_inputs()[0].name
            
            # Test different batch sizes to find optimal throughput
            batch_sizes = [1, 10, 50, 100, 500]
            max_throughput = 0
            best_latency = float('inf')
            
            for batch_size in batch_sizes:
                if batch_size > len(test_data):
                    continue
                
                test_batch = test_data[:batch_size].astype(np.float32)
                
                # Run multiple iterations for stability
                throughputs = []
                latencies = []
                
                for _ in range(10):
                    start_time = time.perf_counter()
                    session.run(None, {input_name: test_batch})
                    duration = time.perf_counter() - start_time
                    
                    throughput = batch_size / duration
                    latency = (duration / batch_size) * 1000  # ms per prediction
                    
                    throughputs.append(throughput)
                    latencies.append(latency)
                
                avg_throughput = np.mean(throughputs)
                avg_latency = np.mean(latencies)
                
                if avg_throughput > max_throughput:
                    max_throughput = avg_throughput
                    best_latency = avg_latency
            
            result.max_sustained_throughput_pps = max_throughput
            result.latency_at_max_throughput_ms = best_latency
            result.stability_under_load = best_latency < self.max_acceptable_latency_ms
            
            self.logger.debug("Load testing completed", extra={
                "max_throughput_pps": result.max_sustained_throughput_pps,
                "latency_at_max_ms": result.latency_at_max_throughput_ms,
                "stable_under_load": result.stability_under_load
            })
            
        except Exception as e:
            result.add_warning(f"Load testing failed: {e}")
    
    def _validate_performance_targets(self, result: PerformanceValidationResult):
        """Validate performance results against targets."""
        checks_passed = 0
        total_checks = 0
        
        # Check latency improvement
        total_checks += 1
        if result.latency_improvement_factor >= self.target_improvement_factor:
            checks_passed += 1
        else:
            result.add_error(f"Latency improvement {result.latency_improvement_factor:.2f}x below target {self.target_improvement_factor:.2f}x")
        
        # Check absolute latency
        total_checks += 1
        if result.onnx_mean_latency_ms <= self.max_acceptable_latency_ms:
            checks_passed += 1
        else:
            result.add_error(f"ONNX latency {result.onnx_mean_latency_ms:.2f}ms exceeds maximum {self.max_acceptable_latency_ms:.2f}ms")
        
        # Check for performance regression
        total_checks += 1
        if result.latency_improvement_factor >= 1.0:
            checks_passed += 1
        else:
            result.performance_regression_detected = True
            result.add_error(f"Performance regression detected: {result.latency_improvement_factor:.2f}x improvement")
        
        result.meets_performance_target = (checks_passed == total_checks) and len(result.errors) == 0
        
        self.logger.info("Performance target validation completed", extra={
            "checks_passed": checks_passed,
            "total_checks": total_checks,
            "meets_targets": result.meets_performance_target
        })


# Factory functions for easy instantiation
def create_accuracy_validator(accuracy_tolerance: float = 1e-6) -> ModelAccuracyValidator:
    """Create ModelAccuracyValidator with custom tolerance."""
    return ModelAccuracyValidator(accuracy_tolerance=accuracy_tolerance)


def create_performance_validator(target_improvement_factor: float = 5.0) -> PerformanceValidator:
    """Create PerformanceValidator with custom target."""
    return PerformanceValidator(target_improvement_factor=target_improvement_factor)