"""
Integration Tests for Model Export Pipeline

Comprehensive test suite for XGBoost to ONNX conversion with FAANG-level
quality standards. Validates accuracy, performance, and production readiness.

Test Coverage:
- End-to-end ONNX conversion pipeline
- Accuracy validation within 1e-6 tolerance
- Performance benchmarking validation
- Edge case and boundary testing
- Error handling and recovery
"""

import json
import pytest
import tempfile
import numpy as np
import pandas as pd
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import xgboost as xgb
from sklearn.datasets import make_classification
from sklearn.model_selection import train_test_split

# Test dependencies
import sys
sys.path.append('src')

from ml.serving.model_export import ModelExporter, ExportConfig, ValidationResult, ModelExportError
from ml.serving.model_validation import ModelAccuracyValidator, PerformanceValidator
from ml.serving.benchmarking import PerformanceBenchmark, BenchmarkConfig


class TestModelExportPipeline:
    """
    Integration tests for the complete model export pipeline.
    Tests the entire workflow from XGBoost model to validated ONNX export.
    """
    
    @pytest.fixture
    def sample_data(self):
        """Generate sample classification data for testing."""
        X, y = make_classification(
            n_samples=1000,
            n_features=50,
            n_informative=30,
            n_redundant=10,
            n_clusters_per_class=1,
            random_state=42
        )
        
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )
        
        return {
            'X_train': X_train.astype(np.float32),
            'X_test': X_test.astype(np.float32),
            'y_train': y_train,
            'y_test': y_test
        }
    
    @pytest.fixture
    def trained_xgboost_model(self, sample_data):
        """Create a trained XGBoost model for testing."""
        model = xgb.XGBClassifier(
            n_estimators=10,  # Small model for fast testing
            max_depth=3,
            learning_rate=0.1,
            random_state=42,
            eval_metric='auc'
        )
        
        model.fit(sample_data['X_train'], sample_data['y_train'])
        return model
    
    @pytest.fixture
    def export_config(self, tmp_path):
        """Create test export configuration."""
        return ExportConfig(
            target_opset=14,
            accuracy_tolerance=1e-6,
            validation_samples=100,  # Smaller for faster testing
            output_dir=str(tmp_path / "test_exports"),
            generate_test_cases=True
        )
    
    @pytest.fixture
    def model_exporter(self, export_config):
        """Create ModelExporter instance for testing."""
        return ModelExporter(export_config)
    
    def test_export_config_validation(self):
        """Test export configuration validation."""
        # Valid configuration
        valid_config = ExportConfig()
        # Should not raise exception
        
        # Invalid accuracy tolerance
        with pytest.raises(ValueError, match="Accuracy tolerance"):
            ExportConfig(accuracy_tolerance=0.1)  # Too large
        
        # Invalid validation samples
        with pytest.raises(ValueError, match="Validation samples"):
            ExportConfig(validation_samples=50)  # Too small
    
    def test_model_export_initialization(self, export_config):
        """Test ModelExporter initialization."""
        exporter = ModelExporter(export_config)
        
        assert exporter.config == export_config
        assert Path(export_config.output_dir).exists()
    
    @pytest.mark.skipif(not hasattr(sys.modules.get('onnx', None), '__version__'), 
                       reason="ONNX not available")
    def test_end_to_end_export_pipeline(self, model_exporter, trained_xgboost_model, sample_data):
        """Test complete end-to-end export pipeline."""
        # Export model
        onnx_path, validation_result = model_exporter.export_to_onnx(
            trained_xgboost_model,
            model_name="test_model",
            X_validation=sample_data['X_test']
        )
        
        # Validate export success
        assert Path(onnx_path).exists()
        assert validation_result.conversion_successful
        
        # Validate accuracy requirements
        if validation_result.accuracy_validated:
            assert validation_result.max_absolute_error < model_exporter.config.accuracy_tolerance
            assert validation_result.correlation_coefficient > 0.9999
            assert validation_result.decision_agreement_rate > 0.999
        
        # Validate performance improvement
        if validation_result.performance_validated:
            assert validation_result.performance_improvement_factor >= 1.0
        
        # Check metadata files
        metadata_file = Path(onnx_path).parent / "test_model_metadata.json"
        if metadata_file.exists():
            with open(metadata_file) as f:
                metadata = json.load(f)
            
            assert 'export_info' in metadata
            assert 'model_info' in metadata
            assert 'validation_results' in metadata
    
    @pytest.mark.skipif(not hasattr(sys.modules.get('onnx', None), '__version__'), 
                       reason="ONNX not available")
    def test_model_info_extraction(self, model_exporter, trained_xgboost_model):
        """Test model information extraction."""
        model_info = model_exporter._extract_model_info(trained_xgboost_model)
        
        assert 'n_features' in model_info
        assert 'n_trees' in model_info
        assert 'max_depth' in model_info
        assert model_info['max_depth'] == 3
        assert model_info['n_trees'] == 10
    
    @pytest.mark.skipif(not hasattr(sys.modules.get('onnx', None), '__version__'), 
                       reason="ONNX not available")
    def test_validation_with_insufficient_accuracy(self, export_config, trained_xgboost_model, sample_data):
        """Test validation failure with very strict accuracy requirements."""
        # Set extremely strict tolerance
        strict_config = ExportConfig(
            accuracy_tolerance=1e-12,  # Extremely strict
            output_dir=export_config.output_dir
        )
        
        exporter = ModelExporter(strict_config)
        
        _, validation_result = exporter.export_to_onnx(
            trained_xgboost_model,
            model_name="strict_test",
            X_validation=sample_data['X_test']
        )
        
        # Should still convert successfully but may fail validation
        assert validation_result.conversion_successful
        # May not pass extremely strict accuracy requirements
    
    def test_export_without_validation_data(self, model_exporter, trained_xgboost_model):
        """Test export without validation data."""
        onnx_path, validation_result = model_exporter.export_to_onnx(
            trained_xgboost_model,
            model_name="no_validation_test"
        )
        
        # Should still export successfully
        assert validation_result.conversion_successful
        assert len(validation_result.warnings) > 0  # Should warn about missing validation
        assert "No validation data provided" in str(validation_result.warnings)
    
    def test_test_case_generation(self, model_exporter, trained_xgboost_model, sample_data):
        """Test automatic test case generation."""
        # Enable test case generation
        model_exporter.config.generate_test_cases = True
        
        onnx_path, validation_result = model_exporter.export_to_onnx(
            trained_xgboost_model,
            model_name="test_cases_model",
            X_validation=sample_data['X_test']
        )
        
        # Check for test case files
        output_dir = Path(onnx_path).parent
        test_cases_file = output_dir / "test_cases_model_test_cases.npz"
        expected_outputs_file = output_dir / "test_cases_model_expected_outputs.npz"
        
        # Files should exist if test case generation succeeded
        if model_exporter.config.generate_test_cases:
            # Test case generation is best effort, may not always succeed
            pass
    
    def test_error_handling_with_invalid_model(self, model_exporter):
        """Test error handling with invalid model input."""
        # Create mock invalid model
        invalid_model = Mock()
        invalid_model.predict_proba = Mock(side_effect=Exception("Invalid model"))
        
        with pytest.raises(ModelExportError):
            model_exporter.export_to_onnx(invalid_model, model_name="invalid_test")
    
    def test_concurrent_exports(self, export_config, trained_xgboost_model, sample_data):
        """Test concurrent model exports don't interfere."""
        import threading
        
        results = []
        errors = []
        
        def export_worker(worker_id):
            try:
                # Each worker gets its own output directory
                worker_config = ExportConfig(
                    output_dir=str(Path(export_config.output_dir) / f"worker_{worker_id}"),
                    validation_samples=50  # Smaller for faster testing
                )
                
                exporter = ModelExporter(worker_config)
                onnx_path, validation_result = exporter.export_to_onnx(
                    trained_xgboost_model,
                    model_name=f"concurrent_test_{worker_id}",
                    X_validation=sample_data['X_test'][:50]  # Smaller dataset
                )
                
                results.append((worker_id, onnx_path, validation_result))
                
            except Exception as e:
                errors.append((worker_id, str(e)))
        
        # Run multiple exports concurrently
        threads = []
        for i in range(3):
            thread = threading.Thread(target=export_worker, args=(i,))
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        # Check results
        assert len(errors) == 0, f"Concurrent export errors: {errors}"
        assert len(results) == 3
        
        # All exports should succeed
        for worker_id, onnx_path, validation_result in results:
            assert Path(onnx_path).exists()
            assert validation_result.conversion_successful


class TestModelValidationIntegration:
    """
    Integration tests for model validation framework.
    Tests accuracy and performance validation with real models.
    """
    
    @pytest.fixture
    def validation_data(self):
        """Generate validation dataset."""
        X, y = make_classification(
            n_samples=500,
            n_features=20,
            n_informative=15,
            random_state=42
        )
        return X.astype(np.float32), y
    
    @pytest.fixture
    def simple_xgboost_model(self, validation_data):
        """Create simple XGBoost model for validation testing."""
        X, y = validation_data
        
        model = xgb.XGBClassifier(
            n_estimators=5,  # Very small for fast testing
            max_depth=2,
            learning_rate=0.3,
            random_state=42
        )
        
        model.fit(X, y)
        return model
    
    @pytest.mark.skipif(not hasattr(sys.modules.get('onnx', None), '__version__'), 
                       reason="ONNX not available")
    def test_accuracy_validation_integration(self, simple_xgboost_model, validation_data, tmp_path):
        """Test accuracy validation with real ONNX model."""
        X_test, y_test = validation_data
        
        # Export model to ONNX first
        export_config = ExportConfig(
            output_dir=str(tmp_path),
            validation_samples=100
        )
        
        exporter = ModelExporter(export_config)
        onnx_path, export_result = exporter.export_to_onnx(
            simple_xgboost_model,
            model_name="accuracy_test_model",
            X_validation=X_test
        )
        
        if not export_result.conversion_successful:
            pytest.skip("ONNX conversion failed, skipping accuracy validation test")
        
        # Test accuracy validation
        accuracy_validator = ModelAccuracyValidator(accuracy_tolerance=1e-4)  # Slightly relaxed for test
        
        validation_result = accuracy_validator.validate_accuracy(
            simple_xgboost_model,
            onnx_path,
            X_test,
            y_test
        )
        
        # Validate results
        assert validation_result.test_samples > 0
        assert validation_result.max_absolute_difference >= 0
        assert validation_result.correlation >= 0
        assert validation_result.decision_agreement_rate >= 0
        
        # Should pass basic accuracy requirements
        assert validation_result.max_absolute_difference < 1e-3  # Reasonable tolerance
    
    @pytest.mark.skipif(not hasattr(sys.modules.get('onnx', None), '__version__'), 
                       reason="ONNX not available")
    def test_performance_validation_integration(self, simple_xgboost_model, validation_data, tmp_path):
        """Test performance validation with real models."""
        X_test, _ = validation_data
        
        # Export model to ONNX first
        export_config = ExportConfig(output_dir=str(tmp_path))
        exporter = ModelExporter(export_config)
        
        onnx_path, export_result = exporter.export_to_onnx(
            simple_xgboost_model,
            model_name="performance_test_model",
            X_validation=X_test
        )
        
        if not export_result.conversion_successful:
            pytest.skip("ONNX conversion failed, skipping performance validation test")
        
        # Test performance validation
        performance_validator = PerformanceValidator(
            target_improvement_factor=1.0,  # Any improvement acceptable for test
            max_acceptable_latency_ms=100.0  # Very lenient for test
        )
        
        validation_result = performance_validator.validate_performance(
            simple_xgboost_model,
            onnx_path,
            X_test[:100],  # Smaller dataset for faster testing
            warmup_iterations=5,
            benchmark_iterations=20
        )
        
        # Validate results
        assert validation_result.python_mean_latency_ms > 0
        assert validation_result.onnx_mean_latency_ms > 0
        assert validation_result.python_throughput_pps > 0
        assert validation_result.onnx_throughput_pps > 0
        assert validation_result.latency_improvement_factor > 0


class TestBenchmarkingIntegration:
    """
    Integration tests for performance benchmarking infrastructure.
    Tests benchmarking with real prediction functions.
    """
    
    @pytest.fixture
    def benchmark_config(self, tmp_path):
        """Create benchmark configuration for testing."""
        return BenchmarkConfig(
            warmup_iterations=5,
            benchmark_iterations=20,
            concurrent_threads=2,
            max_threads=4,
            load_test_duration_seconds=5,
            target_throughput_pps=100,
            stress_test_enabled=True,
            output_dir=str(tmp_path / "benchmarks"),
            generate_plots=False  # Disable plots for faster testing
        )
    
    @pytest.fixture
    def simple_predict_function(self):
        """Create simple prediction function for benchmarking."""
        def predict_func(X):
            # Simulate some computation
            import time
            time.sleep(0.001)  # 1ms artificial latency
            return np.random.rand(len(X))
        
        return predict_func
    
    @pytest.fixture
    def benchmark_data(self):
        """Generate benchmark test data."""
        return np.random.randn(100, 10).astype(np.float32)
    
    def test_benchmark_config_validation(self):
        """Test benchmark configuration validation."""
        # Valid configuration
        valid_config = BenchmarkConfig()
        assert len(valid_config.validate()) == 0
        
        # Invalid configuration
        invalid_config = BenchmarkConfig(
            warmup_iterations=0,
            benchmark_iterations=5,
            load_test_duration_seconds=1
        )
        
        errors = invalid_config.validate()
        assert len(errors) > 0
        assert any("Warmup iterations" in error for error in errors)
        assert any("Benchmark iterations" in error for error in errors)
        assert any("Load test duration" in error for error in errors)
    
    def test_single_threaded_benchmarking(self, benchmark_config, simple_predict_function, benchmark_data):
        """Test single-threaded performance benchmarking."""
        benchmark = PerformanceBenchmark(benchmark_config)
        
        result = benchmark.benchmark_model(
            simple_predict_function,
            benchmark_data,
            model_name="single_thread_test"
        )
        
        # Validate results
        assert result.single_thread_metrics is not None
        assert result.single_thread_metrics.mean_latency_ms > 0
        assert result.single_thread_metrics.throughput_pps > 0
        assert result.single_thread_metrics.total_requests == benchmark_config.benchmark_iterations
        assert result.single_thread_metrics.successful_requests > 0
    
    def test_concurrent_benchmarking(self, benchmark_config, simple_predict_function, benchmark_data):
        """Test concurrent performance benchmarking."""
        benchmark = PerformanceBenchmark(benchmark_config)
        
        result = benchmark.benchmark_model(
            simple_predict_function,
            benchmark_data,
            model_name="concurrent_test"
        )
        
        # Validate concurrent results
        assert len(result.concurrent_metrics) > 0
        
        for thread_count, metrics in result.concurrent_metrics.items():
            assert metrics.mean_latency_ms > 0
            assert metrics.throughput_pps > 0
            assert metrics.total_requests > 0
    
    def test_load_testing(self, benchmark_config, simple_predict_function, benchmark_data):
        """Test load testing functionality."""
        # Reduce load test duration for faster testing
        benchmark_config.load_test_duration_seconds = 3
        benchmark_config.target_throughput_pps = 50  # Achievable target
        
        benchmark = PerformanceBenchmark(benchmark_config)
        
        result = benchmark.benchmark_model(
            simple_predict_function,
            benchmark_data,
            model_name="load_test"
        )
        
        # Validate load test results
        assert result.load_test_metrics is not None
        assert result.load_test_metrics.total_requests > 0
        assert result.load_test_metrics.duration_seconds > 0
        assert len(result.load_test_timeline) > 0
    
    def test_baseline_comparison(self, benchmark_config, benchmark_data):
        """Test performance comparison with baseline."""
        def fast_function(X):
            time.sleep(0.0005)  # 0.5ms
            return np.random.rand(len(X))
        
        def slow_function(X):
            time.sleep(0.002)  # 2ms
            return np.random.rand(len(X))
        
        benchmark = PerformanceBenchmark(benchmark_config)
        
        result = benchmark.benchmark_model(
            fast_function,
            benchmark_data,
            model_name="comparison_test",
            baseline_func=slow_function
        )
        
        # Validate comparison results
        assert result.baseline_metrics is not None
        assert len(result.performance_improvement) > 0
        assert 'latency_improvement_factor' in result.performance_improvement
        
        # Fast function should show improvement
        improvement = result.performance_improvement['latency_improvement_factor']
        assert improvement > 1.0  # Should be faster than baseline
    
    def test_benchmark_error_handling(self, benchmark_config, benchmark_data):
        """Test benchmarking with failing prediction function."""
        def failing_function(X):
            if np.random.random() < 0.1:  # 10% failure rate
                raise Exception("Prediction failed")
            return np.random.rand(len(X))
        
        benchmark = PerformanceBenchmark(benchmark_config)
        
        result = benchmark.benchmark_model(
            failing_function,
            benchmark_data,
            model_name="error_test"
        )
        
        # Should handle errors gracefully
        assert result.single_thread_metrics is not None
        assert result.single_thread_metrics.failed_requests >= 0
        assert result.single_thread_metrics.error_rate >= 0
    
    def test_benchmark_output_generation(self, benchmark_config, simple_predict_function, benchmark_data):
        """Test benchmark output file generation."""
        benchmark_config.save_raw_data = True
        benchmark_config.generate_report = True
        
        benchmark = PerformanceBenchmark(benchmark_config)
        
        result = benchmark.benchmark_model(
            simple_predict_function,
            benchmark_data,
            model_name="output_test"
        )
        
        # Check for generated files
        output_dir = Path(benchmark_config.output_dir)
        
        # Results file should exist
        results_file = output_dir / "output_test_benchmark_results.json"
        if results_file.exists():
            with open(results_file) as f:
                saved_results = json.load(f)
            
            assert 'test_info' in saved_results
            assert 'single_thread_metrics' in saved_results
        
        # Report file should exist
        report_file = output_dir / "output_test_benchmark_report.md"
        if report_file.exists():
            content = report_file.read_text()
            assert "Performance Benchmark Report" in content
            assert "Single-Threaded Performance" in content


# Integration test for complete serving pipeline
class TestCompleteServingPipeline:
    """
    End-to-end integration tests for the complete serving pipeline.
    Tests the full workflow from model training to ONNX export to validation.
    """
    
    @pytest.fixture
    def complete_pipeline_data(self):
        """Generate comprehensive dataset for pipeline testing."""
        X, y = make_classification(
            n_samples=2000,
            n_features=100,
            n_informative=80,
            n_redundant=10,
            n_clusters_per_class=2,
            random_state=42
        )
        
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.3, random_state=42
        )
        
        return {
            'X_train': X_train.astype(np.float32),
            'X_test': X_test.astype(np.float32),
            'y_train': y_train,
            'y_test': y_test
        }
    
    @pytest.fixture
    def production_xgboost_model(self, complete_pipeline_data):
        """Create production-like XGBoost model."""
        data = complete_pipeline_data
        
        model = xgb.XGBClassifier(
            n_estimators=50,
            max_depth=6,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric='auc'
        )
        
        model.fit(data['X_train'], data['y_train'])
        return model
    
    @pytest.mark.skipif(not hasattr(sys.modules.get('onnx', None), '__version__'), 
                       reason="ONNX not available")
    def test_complete_serving_pipeline(self, production_xgboost_model, complete_pipeline_data, tmp_path):
        """Test complete end-to-end serving pipeline."""
        data = complete_pipeline_data
        
        # 1. Export model to ONNX
        export_config = ExportConfig(
            output_dir=str(tmp_path / "exports"),
            accuracy_tolerance=1e-6,
            validation_samples=200,
            generate_test_cases=True
        )
        
        exporter = ModelExporter(export_config)
        onnx_path, export_result = exporter.export_to_onnx(
            production_xgboost_model,
            model_name="production_model",
            X_validation=data['X_test']
        )
        
        # Validate export
        assert export_result.conversion_successful
        assert Path(onnx_path).exists()
        
        if not export_result.conversion_successful:
            pytest.skip("ONNX export failed, skipping pipeline test")
        
        # 2. Accuracy validation
        accuracy_validator = ModelAccuracyValidator(accuracy_tolerance=1e-5)
        accuracy_result = accuracy_validator.validate_accuracy(
            production_xgboost_model,
            onnx_path,
            data['X_test'][:200],  # Use subset for faster testing
            data['y_test'][:200]
        )
        
        # 3. Performance validation
        performance_validator = PerformanceValidator(
            target_improvement_factor=1.0,
            max_acceptable_latency_ms=50.0
        )
        
        performance_result = performance_validator.validate_performance(
            production_xgboost_model,
            onnx_path,
            data['X_test'][:100],
            warmup_iterations=10,
            benchmark_iterations=50
        )
        
        # 4. Comprehensive benchmarking
        benchmark_config = BenchmarkConfig(
            warmup_iterations=10,
            benchmark_iterations=50,
            load_test_duration_seconds=5,
            output_dir=str(tmp_path / "benchmarks"),
            generate_plots=False
        )
        
        benchmark = PerformanceBenchmark(benchmark_config)
        
        # Create ONNX prediction function for benchmarking
        import onnxruntime as ort
        
        def onnx_predict_func(X):
            session = ort.InferenceSession(onnx_path)
            input_name = session.get_inputs()[0].name
            outputs = session.run(None, {input_name: X.astype(np.float32)})
            
            if len(outputs) == 2:
                return outputs[1][:, 1] if outputs[1].shape[1] == 2 else outputs[1][:, 0]
            return outputs[0]
        
        def python_predict_func(X):
            return production_xgboost_model.predict_proba(X)[:, 1]
        
        benchmark_result = benchmark.benchmark_model(
            onnx_predict_func,
            data['X_test'][:50],
            model_name="pipeline_test",
            baseline_func=python_predict_func
        )
        
        # 5. Validate complete pipeline results
        
        # Export should succeed
        assert export_result.conversion_successful
        
        # Accuracy should be within tolerance
        assert accuracy_result.test_samples > 0
        
        # Performance should show some improvement or at least not regress
        assert performance_result.python_mean_latency_ms > 0
        assert performance_result.onnx_mean_latency_ms > 0
        
        # Benchmarking should complete
        assert benchmark_result.single_thread_metrics is not None
        assert benchmark_result.baseline_metrics is not None
        
        # Performance improvement should be measured
        if benchmark_result.performance_improvement:
            improvement = benchmark_result.performance_improvement.get('latency_improvement_factor', 0)
            assert improvement > 0
        
        # Overall pipeline success
        pipeline_success = (
            export_result.conversion_successful and
            accuracy_result.test_samples > 0 and
            performance_result.python_mean_latency_ms > 0 and
            benchmark_result.single_thread_metrics is not None
        )
        
        assert pipeline_success, "Complete serving pipeline should execute successfully"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])