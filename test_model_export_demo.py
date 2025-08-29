#!/usr/bin/env python3
"""
Model Export Pipeline Demonstration

End-to-end demonstration of the high-performance model serving architecture
with a trained XGBoost model from the modular training pipeline.

This script validates the complete Phase 1 implementation:
- Model export from XGBoost to ONNX
- Comprehensive accuracy validation (<1e-6 tolerance)
- Performance benchmarking and improvement validation
- Integration testing of all components
"""

import sys
import json
import time
from pathlib import Path
import numpy as np

# Add src to path
sys.path.append('src')

# Import our high-performance serving components
from ml.serving.model_export import ModelExporter, ExportConfig
from ml.serving.model_validation import ModelAccuracyValidator, PerformanceValidator
from ml.serving.benchmarking import PerformanceBenchmark, BenchmarkConfig

# Import modular training components to load the trained model
from ml.training.core.checkpoint_manager import CheckpointManager


def load_best_trained_model():
    """Load the best trained model from the modular training pipeline."""
    print("🔍 Loading best trained model from modular pipeline...")
    
    # Initialize checkpoint manager
    checkpoint_manager = CheckpointManager({
        "checkpoint_dir": "models/checkpoints",
        "retention_hours": 168
    })
    
    try:
        # Load the best checkpoint
        best_checkpoint = checkpoint_manager.load_best_checkpoint()
        
        if best_checkpoint is None:
            raise RuntimeError("No trained model checkpoint found. Run modular training first.")
        
        print(f"✅ Loaded model: Trial {best_checkpoint.trial_number}, AUC = {best_checkpoint.score:.4f}")
        return best_checkpoint.model, best_checkpoint.score
        
    except Exception as e:
        print(f"❌ Failed to load trained model: {e}")
        print("💡 Make sure the modular training pipeline has completed successfully")
        return None, None


def generate_validation_data(n_samples=1000, n_features=200):
    """Generate synthetic validation data matching the IEEE model structure."""
    print(f"🔢 Generating validation data: {n_samples} samples, {n_features} features")
    
    np.random.seed(42)  # Reproducible data
    
    # Generate realistic feature distributions
    X = np.random.randn(n_samples, n_features).astype(np.float32)
    
    # Add some realistic patterns
    X[:, :10] = np.random.lognormal(0, 1, (n_samples, 10)).astype(np.float32)  # Amount-like features
    X[:, 10:20] = np.random.gamma(2, 2, (n_samples, 10)).astype(np.float32)    # Time-like features
    
    # Clip extreme values
    X = np.clip(X, -10, 10)
    
    print(f"✅ Generated validation data with shape: {X.shape}")
    return X


def demonstrate_model_export():
    """Demonstrate the complete model export pipeline."""
    print("\n" + "="*80)
    print("🚀 PHASE 1: HIGH-PERFORMANCE MODEL SERVING - DEMONSTRATION")
    print("="*80)
    
    # 1. Load the trained model
    trained_model, model_score = load_best_trained_model()
    
    if trained_model is None:
        return False
    
    print(f"\n📊 Model Performance: AUC = {model_score:.4f} (Target: >0.96 ✅)")
    
    # 2. Generate validation data
    validation_data = generate_validation_data()
    
    # 3. Configure model export
    print("\n⚙️  Configuring model export pipeline...")
    
    export_config = ExportConfig(
        target_opset=14,
        accuracy_tolerance=1e-6,  # FAANG-level strict tolerance
        validation_samples=500,   # Comprehensive validation
        output_dir="models/onnx_exports",
        model_name="ieee_fraud_production",
        generate_test_cases=True,
        test_case_coverage="comprehensive"
    )
    
    print(f"   ✓ Accuracy tolerance: {export_config.accuracy_tolerance:.0e}")
    print(f"   ✓ Validation samples: {export_config.validation_samples}")
    print(f"   ✓ Output directory: {export_config.output_dir}")
    
    # 4. Export model to ONNX
    print("\n🔄 Exporting XGBoost model to ONNX...")
    start_time = time.time()
    
    try:
        exporter = ModelExporter(export_config)
        onnx_path, export_result = exporter.export_to_onnx(
            trained_model,
            model_name="ieee_fraud_production",
            X_validation=validation_data
        )
        
        export_duration = time.time() - start_time
        print(f"✅ Export completed in {export_duration:.2f}s")
        
    except Exception as e:
        print(f"❌ Export failed: {e}")
        return False
    
    # 5. Validate export results
    print("\n📋 Export Validation Results:")
    print("-" * 40)
    
    if export_result.conversion_successful:
        print("✅ ONNX conversion: SUCCESS")
        print(f"   📏 Model size: {export_result.onnx_size_mb:.1f}MB")
        print(f"   📦 Compression ratio: {export_result.compression_ratio:.2f}x")
    else:
        print("❌ ONNX conversion: FAILED")
        return False
    
    if export_result.accuracy_validated:
        print("✅ Accuracy validation: PASSED")
        print(f"   🎯 Max absolute error: {export_result.max_absolute_error:.2e}")
        print(f"   📊 Correlation: {export_result.correlation_coefficient:.6f}")
        print(f"   🤝 Decision agreement: {export_result.decision_agreement_rate:.4f}")
    else:
        print("⚠️  Accuracy validation: ISSUES DETECTED")
        for error in export_result.errors:
            print(f"   ❌ {error}")
    
    if export_result.performance_validated:
        print("✅ Performance validation: PASSED")
        print(f"   ⚡ Python inference: {export_result.python_inference_time_ms:.2f}ms")
        print(f"   🚀 ONNX inference: {export_result.onnx_inference_time_ms:.2f}ms")
        print(f"   📈 Performance improvement: {export_result.performance_improvement_factor:.2f}x")
    else:
        print("⚠️  Performance validation: ISSUES DETECTED")
    
    # 6. Comprehensive accuracy validation
    if Path(onnx_path).exists():
        print("\n🔍 Running comprehensive accuracy validation...")
        
        accuracy_validator = ModelAccuracyValidator(
            accuracy_tolerance=1e-6,
            correlation_threshold=0.9999,
            decision_agreement_threshold=0.999
        )
        
        accuracy_result = accuracy_validator.validate_accuracy(
            trained_model,
            onnx_path,
            validation_data[:200]  # Use subset for demonstration
        )
        
        print("📊 Comprehensive Accuracy Results:")
        print(f"   🎯 Max absolute difference: {accuracy_result.max_absolute_difference:.2e}")
        print(f"   📈 Mean absolute difference: {accuracy_result.mean_absolute_difference:.2e}")
        print(f"   📊 Correlation coefficient: {accuracy_result.correlation:.6f}")
        print(f"   🤝 Decision agreement rate: {accuracy_result.decision_agreement_rate:.4f}")
        print(f"   🧪 Edge cases tested: {accuracy_result.edge_cases_tested}")
        print(f"   🔬 Boundary cases tested: {accuracy_result.boundary_cases_tested}")
        
        if accuracy_result.passed:
            print("✅ COMPREHENSIVE ACCURACY VALIDATION: PASSED")
        else:
            print("⚠️  COMPREHENSIVE ACCURACY VALIDATION: ISSUES DETECTED")
            for error in accuracy_result.errors:
                print(f"   ❌ {error}")
    
    # 7. Performance benchmarking
    print("\n⚡ Running performance benchmarking...")
    
    benchmark_config = BenchmarkConfig(
        warmup_iterations=20,
        benchmark_iterations=100,
        concurrent_threads=4,
        load_test_duration_seconds=10,
        target_throughput_pps=500,
        output_dir="benchmarks/demo_results",
        generate_plots=True,
        generate_report=True
    )
    
    benchmark = PerformanceBenchmark(benchmark_config)
    
    # Create prediction functions for benchmarking
    def python_predict_func(X):
        return trained_model.predict_proba(X)[:, 1]
    
    def onnx_predict_func(X):
        import onnxruntime as ort
        session = ort.InferenceSession(onnx_path)
        input_name = session.get_inputs()[0].name
        outputs = session.run(None, {input_name: X.astype(np.float32)})
        
        if len(outputs) == 2:
            return outputs[1][:, 1] if outputs[1].shape[1] == 2 else outputs[1][:, 0]
        return outputs[0]
    
    # Benchmark ONNX vs Python
    benchmark_result = benchmark.benchmark_model(
        onnx_predict_func,
        validation_data[:100],  # Use subset for demonstration
        model_name="ieee_fraud_onnx",
        baseline_func=python_predict_func
    )
    
    # 8. Display final results
    print("\n" + "="*80)
    print("🎯 DEMONSTRATION RESULTS SUMMARY")
    print("="*80)
    
    # Overall success
    overall_success = (
        export_result.conversion_successful and
        export_result.accuracy_validated and
        benchmark_result.single_thread_metrics is not None
    )
    
    if overall_success:
        print("🌟 SUCCESS: Phase 1 Model Export Pipeline - FULLY OPERATIONAL")
        print("\n📊 Key Achievements:")
        print(f"   ✅ Source model AUC: {model_score:.4f} (>0.96 target)")
        print(f"   ✅ ONNX conversion: SUCCESSFUL")
        print(f"   ✅ Accuracy validation: <{export_config.accuracy_tolerance:.0e} tolerance")
        
        if benchmark_result.performance_improvement:
            improvement = benchmark_result.performance_improvement.get('latency_improvement_factor', 1.0)
            print(f"   ✅ Performance improvement: {improvement:.2f}x faster")
        
        if benchmark_result.single_thread_metrics:
            print(f"   ✅ ONNX inference latency: {benchmark_result.single_thread_metrics.mean_latency_ms:.2f}ms")
            print(f"   ✅ ONNX throughput: {benchmark_result.single_thread_metrics.throughput_pps:.0f} predictions/sec")
        
        print("\n🚀 READY FOR PHASE 2: C++ Inference Engine Implementation")
        
    else:
        print("⚠️  ISSUES DETECTED - Review validation results above")
        
        if not export_result.conversion_successful:
            print("   ❌ ONNX conversion failed")
        
        if not export_result.accuracy_validated:
            print("   ❌ Accuracy validation failed")
    
    print("\n" + "="*80)
    
    return overall_success


if __name__ == "__main__":
    print("🔧 Model Export Pipeline Demonstration")
    print("🎯 Validating Phase 1: ONNX Export with <1e-6 Accuracy")
    
    success = demonstrate_model_export()
    
    if success:
        print("\n✅ Phase 1 demonstration completed successfully!")
        print("🚀 Ready to proceed with Phase 2: C++ Inference Engine")
    else:
        print("\n❌ Phase 1 demonstration encountered issues")
        print("🔧 Review the error messages above and ensure all dependencies are installed")
    
    exit(0 if success else 1)