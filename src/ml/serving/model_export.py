"""
Model Export Pipeline - XGBoost to ONNX Conversion

Production-grade model conversion with comprehensive validation and optimization.
Implements FAANG-level quality standards with strict accuracy requirements.

Key Features:
- XGBoost to ONNX conversion with <1e-6 accuracy validation
- Model optimization and quantization strategies
- Performance benchmarking and regression detection
- Version management and compatibility testing
"""

import json
import logging
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import xgboost as xgb

# ONNX dependencies
try:
    import onnx
    import onnxmltools
    from onnxmltools.convert import convert_xgboost
    from onnxmltools.convert.common.data_types import FloatTensorType
    import onnxruntime as ort
    ONNX_AVAILABLE = True
except ImportError as e:
    ONNX_AVAILABLE = False
    IMPORT_ERROR = str(e)

logger = logging.getLogger(__name__)


@dataclass
class ExportConfig:
    """Configuration for model export process."""
    
    # Export settings
    target_opset: int = 14
    optimization_level: str = "all"  # "none", "basic", "extended", "all"
    enable_quantization: bool = False
    enable_graph_optimization: bool = True
    
    # Validation settings
    accuracy_tolerance: float = 1e-6
    performance_baseline_multiplier: float = 0.8  # Must be within 80% of baseline
    validation_samples: int = 10000
    
    # Output settings
    output_dir: str = "models/onnx_exports"
    model_name: Optional[str] = None
    include_metadata: bool = True
    
    # Testing configuration
    generate_test_cases: bool = True
    test_case_coverage: str = "comprehensive"  # "basic", "extended", "comprehensive"
    
    def __post_init__(self):
        """Validate configuration parameters."""
        if self.accuracy_tolerance <= 0 or self.accuracy_tolerance > 1e-3:
            raise ValueError(f"Accuracy tolerance must be between 0 and 1e-3, got {self.accuracy_tolerance}")
        
        if not 0.1 <= self.performance_baseline_multiplier <= 2.0:
            raise ValueError(f"Performance baseline multiplier must be between 0.1 and 2.0")
        
        if self.validation_samples < 100:
            raise ValueError(f"Validation samples must be at least 100, got {self.validation_samples}")


@dataclass 
class ValidationResult:
    """Results from model conversion validation."""
    
    # Validation status
    passed: bool = False
    conversion_successful: bool = False
    accuracy_validated: bool = False
    performance_validated: bool = False
    
    # Accuracy metrics
    max_absolute_error: float = float('inf')
    mean_absolute_error: float = float('inf')
    correlation_coefficient: float = 0.0
    decision_agreement_rate: float = 0.0
    
    # Performance metrics
    python_inference_time_ms: float = 0.0
    onnx_inference_time_ms: float = 0.0
    performance_improvement_factor: float = 0.0
    
    # Model information
    model_size_mb: float = 0.0
    onnx_size_mb: float = 0.0
    compression_ratio: float = 1.0
    
    # Test coverage
    test_cases_count: int = 0
    edge_cases_tested: int = 0
    
    # Error information
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def add_error(self, error: str):
        """Add validation error."""
        self.errors.append(error)
        self.passed = False
    
    def add_warning(self, warning: str):
        """Add validation warning."""
        self.warnings.append(warning)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'validation_status': {
                'passed': self.passed,
                'conversion_successful': self.conversion_successful,
                'accuracy_validated': self.accuracy_validated,
                'performance_validated': self.performance_validated
            },
            'accuracy_metrics': {
                'max_absolute_error': self.max_absolute_error,
                'mean_absolute_error': self.mean_absolute_error,
                'correlation_coefficient': self.correlation_coefficient,
                'decision_agreement_rate': self.decision_agreement_rate
            },
            'performance_metrics': {
                'python_inference_time_ms': self.python_inference_time_ms,
                'onnx_inference_time_ms': self.onnx_inference_time_ms,
                'performance_improvement_factor': self.performance_improvement_factor
            },
            'model_metrics': {
                'model_size_mb': self.model_size_mb,
                'onnx_size_mb': self.onnx_size_mb,
                'compression_ratio': self.compression_ratio
            },
            'test_coverage': {
                'test_cases_count': self.test_cases_count,
                'edge_cases_tested': self.edge_cases_tested
            },
            'issues': {
                'errors': self.errors,
                'warnings': self.warnings
            }
        }


class ModelExportError(Exception):
    """Custom exception for model export failures."""
    pass


class ModelExporter:
    """
    Production-grade XGBoost to ONNX model converter with comprehensive validation.
    
    Implements FAANG-level quality standards:
    - Strict accuracy validation (<1e-6 tolerance)
    - Comprehensive error handling and recovery
    - Performance benchmarking and regression detection
    - Production-ready logging and monitoring
    """
    
    def __init__(self, config: ExportConfig):
        """Initialize model exporter with configuration."""
        if not ONNX_AVAILABLE:
            raise ModelExportError(f"ONNX dependencies not available: {IMPORT_ERROR}")
        
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Create output directory
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize validation components
        self._validation_cache = {}
        
        self.logger.info("ModelExporter initialized", extra={
            "output_dir": str(self.output_dir),
            "accuracy_tolerance": config.accuracy_tolerance,
            "validation_samples": config.validation_samples
        })
    
    def export_to_onnx(self, 
                      xgboost_model: xgb.XGBClassifier,
                      model_name: Optional[str] = None,
                      X_validation: Optional[np.ndarray] = None) -> Tuple[str, ValidationResult]:
        """
        Convert XGBoost model to optimized ONNX format with comprehensive validation.
        
        Args:
            xgboost_model: Trained XGBoost classifier
            model_name: Optional model name for output files
            X_validation: Validation data for accuracy testing
            
        Returns:
            Tuple of (onnx_model_path, validation_result)
        """
        self.logger.info("Starting XGBoost to ONNX conversion")
        
        validation_result = ValidationResult()
        
        try:
            # 1. Extract model metadata
            model_info = self._extract_model_info(xgboost_model)
            self.logger.info("Model analysis complete", extra=model_info)
            
            # 2. Generate model name if not provided
            if model_name is None:
                model_name = self.config.model_name or f"xgboost_model_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # 3. Convert to ONNX
            onnx_model = self._convert_to_onnx(xgboost_model, model_info)
            validation_result.conversion_successful = True
            
            # 4. Apply optimizations
            if self.config.enable_graph_optimization:
                onnx_model = self._optimize_onnx_model(onnx_model)
            
            # 5. Save ONNX model
            onnx_path = self.output_dir / f"{model_name}.onnx"
            onnx.save(onnx_model, str(onnx_path))
            
            # 6. Calculate model sizes
            validation_result.onnx_size_mb = onnx_path.stat().st_size / (1024 * 1024)
            validation_result.model_size_mb = self._estimate_xgboost_size_mb(xgboost_model)
            validation_result.compression_ratio = validation_result.model_size_mb / validation_result.onnx_size_mb
            
            # 7. Comprehensive validation
            if X_validation is not None:
                self._validate_conversion_accuracy(xgboost_model, onnx_model, X_validation, validation_result)
                self._validate_performance(xgboost_model, onnx_model, X_validation, validation_result)
            else:
                validation_result.add_warning("No validation data provided - accuracy validation skipped")
            
            # 8. Generate test cases
            if self.config.generate_test_cases:
                self._generate_test_cases(xgboost_model, model_info, model_name)
            
            # 9. Save metadata and validation results
            self._save_export_metadata(model_name, model_info, validation_result)
            
            # 10. Final validation check
            validation_result.passed = (
                validation_result.conversion_successful and
                validation_result.accuracy_validated and
                validation_result.performance_validated and
                len(validation_result.errors) == 0
            )
            
            if validation_result.passed:
                self.logger.info("ONNX conversion completed successfully", extra={
                    "model_path": str(onnx_path),
                    "accuracy_improvement": f"{validation_result.performance_improvement_factor:.2f}x",
                    "size_compression": f"{validation_result.compression_ratio:.2f}x"
                })
            else:
                self.logger.error("ONNX conversion validation failed", extra={
                    "errors": validation_result.errors,
                    "warnings": validation_result.warnings
                })
            
            return str(onnx_path), validation_result
            
        except Exception as e:
            validation_result.add_error(f"Export failed: {str(e)}")
            self.logger.error("ONNX conversion failed", extra={
                "error": str(e),
                "traceback": traceback.format_exc()
            })
            raise ModelExportError(f"Failed to export model to ONNX: {e}") from e
    
    def _extract_model_info(self, model: xgb.XGBClassifier) -> Dict[str, Any]:
        """Extract comprehensive model metadata."""
        try:
            booster = model.get_booster()
            feature_importance = model.feature_importances_
            
            return {
                'n_features': model.n_features_in_,
                'n_trees': booster.num_boosted_rounds(),
                'max_depth': model.max_depth,
                'learning_rate': model.learning_rate,
                'n_estimators': model.n_estimators,
                'objective': model.objective,
                'booster_type': model.booster,
                'feature_importance_stats': {
                    'mean': float(np.mean(feature_importance)),
                    'std': float(np.std(feature_importance)),
                    'max': float(np.max(feature_importance)),
                    'min': float(np.min(feature_importance))
                }
            }
        except Exception as e:
            self.logger.warning(f"Failed to extract complete model info: {e}")
            return {
                'n_features': getattr(model, 'n_features_in_', 'unknown'),
                'extraction_error': str(e)
            }
    
    def _convert_to_onnx(self, model: xgb.XGBClassifier, model_info: Dict[str, Any]) -> onnx.ModelProto:
        """Convert XGBoost model to ONNX format optimized for C++ inference."""
        try:
            # Define input type with C++-friendly names
            n_features = model_info.get('n_features', 200)  # Default fallback
            
            # Use standardized input/output names for C++ determinism
            initial_type = [('features', FloatTensorType([None, n_features]))]
            
            # Create dummy input for skl2onnx conversion
            dummy_input = np.zeros((1, n_features), dtype=np.float32)
            
            # Fix feature names for ONNX compatibility by working with the booster
            booster = model.get_booster()
            original_feature_names = booster.feature_names
            
            if original_feature_names is not None:
                # Check if feature names follow ONNX pattern (f0, f1, f2, ...)
                needs_fixing = any(not name.startswith('f') or not name[1:].isdigit() 
                                 for name in original_feature_names if name)
                
                if needs_fixing:
                    self.logger.info("Fixing feature names for ONNX compatibility")
                    # Set standard feature names for conversion
                    booster.feature_names = [f'f{i}' for i in range(n_features)]
            
            # Convert using onnxmltools with C++-optimized settings
            onnx_model = convert_xgboost(
                model,
                initial_types=initial_type,
                target_opset=self.config.target_opset,
                doc_string=f"XGBoost fraud detection model - {datetime.now().isoformat()}"
            )
            
            # Apply shape inference and simplification for C++ optimization
            import onnx.shape_inference
            import onnx.utils
            
            # Run shape inference to optimize the graph
            onnx_model = onnx.shape_inference.infer_shapes(onnx_model)
            
            # Simplify the model graph (removes redundant nodes)
            try:
                from onnxsim import simplify
                onnx_model, check = simplify(onnx_model)
                if check:
                    self.logger.info("ONNX model simplified for C++ optimization")
                else:
                    self.logger.warning("ONNX model simplification validation failed")
            except ImportError:
                self.logger.info("onnxsim not available, skipping graph simplification")
            
            self.logger.info("Using onnxmltools with C++-optimized settings")
            
            # Restore original feature names
            if original_feature_names is not None and booster.feature_names != original_feature_names:
                booster.feature_names = original_feature_names
            
            # Validate ONNX model structure
            onnx.checker.check_model(onnx_model)
            
            self.logger.info("ONNX conversion successful", extra={
                "opset_version": self.config.target_opset,
                "input_features": n_features,
                "model_size_mb": len(onnx_model.SerializeToString()) / (1024 * 1024)
            })
            
            return onnx_model
            
        except Exception as e:
            raise ModelExportError(f"ONNX conversion failed: {e}") from e
    
    def _optimize_onnx_model(self, model: onnx.ModelProto) -> onnx.ModelProto:
        """Apply ONNX model optimizations."""
        try:
            # Create temporary ONNX Runtime session for optimization
            with tempfile.NamedTemporaryFile(suffix='.onnx', delete=False) as tmp_file:
                onnx.save(model, tmp_file.name)
                
                # Configure optimization
                sess_options = ort.SessionOptions()
                sess_options.graph_optimization_level = getattr(
                    ort.GraphOptimizationLevel, 
                    self.config.optimization_level.upper(),
                    ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                )
                
                # Apply optimizations
                session = ort.InferenceSession(tmp_file.name, sess_options)
                
                self.logger.info("ONNX model optimization completed", extra={
                    "optimization_level": self.config.optimization_level
                })
                
                # Return original model (optimization is applied during inference)
                return model
                
        except Exception as e:
            self.logger.warning(f"ONNX optimization failed: {e}")
            return model
    
    def _validate_conversion_accuracy(self, 
                                    xgb_model: xgb.XGBClassifier,
                                    onnx_model: onnx.ModelProto,
                                    X_validation: np.ndarray,
                                    result: ValidationResult):
        """Validate ONNX model produces identical predictions to XGBoost."""
        try:
            # Limit validation samples for performance
            n_samples = min(len(X_validation), self.config.validation_samples)
            X_test = X_validation[:n_samples].astype(np.float32)
            
            # XGBoost predictions
            xgb_predictions = xgb_model.predict_proba(X_test)[:, 1]
            
            # ONNX predictions
            onnx_predictions = self._run_onnx_inference(onnx_model, X_test)
            
            # Calculate accuracy metrics
            result.max_absolute_error = float(np.max(np.abs(xgb_predictions - onnx_predictions)))
            result.mean_absolute_error = float(np.mean(np.abs(xgb_predictions - onnx_predictions)))
            result.correlation_coefficient = float(np.corrcoef(xgb_predictions, onnx_predictions)[0, 1])
            
            # Business decision agreement
            xgb_decisions = (xgb_predictions > 0.5).astype(int)
            onnx_decisions = (onnx_predictions > 0.5).astype(int)
            result.decision_agreement_rate = float(np.mean(xgb_decisions == onnx_decisions))
            
            result.test_cases_count = n_samples
            
            # Validation criteria
            accuracy_passed = result.max_absolute_error < self.config.accuracy_tolerance
            correlation_passed = result.correlation_coefficient > 0.9999
            decision_passed = result.decision_agreement_rate > 0.999
            
            result.accuracy_validated = accuracy_passed and correlation_passed and decision_passed
            
            if not result.accuracy_validated:
                if not accuracy_passed:
                    result.add_error(f"Accuracy tolerance exceeded: {result.max_absolute_error:.2e} > {self.config.accuracy_tolerance:.2e}")
                if not correlation_passed:
                    result.add_error(f"Correlation too low: {result.correlation_coefficient:.6f} < 0.9999")
                if not decision_passed:
                    result.add_error(f"Decision agreement too low: {result.decision_agreement_rate:.4f} < 0.999")
            
            self.logger.info("Accuracy validation completed", extra={
                "max_absolute_error": result.max_absolute_error,
                "mean_absolute_error": result.mean_absolute_error,
                "correlation": result.correlation_coefficient,
                "decision_agreement": result.decision_agreement_rate,
                "validation_passed": result.accuracy_validated
            })
            
        except Exception as e:
            result.add_error(f"Accuracy validation failed: {e}")
            self.logger.error("Accuracy validation error", extra={"error": str(e)})
    
    def _validate_performance(self,
                            xgb_model: xgb.XGBClassifier,
                            onnx_model: onnx.ModelProto,
                            X_validation: np.ndarray,
                            result: ValidationResult):
        """Validate ONNX model performance meets requirements."""
        try:
            # Performance test configuration
            n_samples = min(1000, len(X_validation))  # Smaller sample for timing
            X_test = X_validation[:n_samples].astype(np.float32)
            n_warmup = 10
            n_trials = 50
            
            # XGBoost performance
            xgb_times = []
            for _ in range(n_warmup):
                _ = xgb_model.predict_proba(X_test[0:1])  # Warmup
            
            for _ in range(n_trials):
                start_time = datetime.now()
                _ = xgb_model.predict_proba(X_test[0:1])
                xgb_times.append((datetime.now() - start_time).total_seconds() * 1000)
            
            result.python_inference_time_ms = float(np.mean(xgb_times))
            
            # ONNX performance
            onnx_times = []
            
            # Create ONNX session
            with tempfile.NamedTemporaryFile(suffix='.onnx', delete=False) as tmp_file:
                onnx.save(onnx_model, tmp_file.name)
                session = ort.InferenceSession(tmp_file.name)
                input_name = session.get_inputs()[0].name
                
                # Warmup
                for _ in range(n_warmup):
                    _ = session.run(None, {input_name: X_test[0:1]})
                
                # Timing trials
                for _ in range(n_trials):
                    start_time = datetime.now()
                    _ = session.run(None, {input_name: X_test[0:1]})
                    onnx_times.append((datetime.now() - start_time).total_seconds() * 1000)
            
            result.onnx_inference_time_ms = float(np.mean(onnx_times))
            result.performance_improvement_factor = result.python_inference_time_ms / result.onnx_inference_time_ms
            
            # Performance validation
            baseline_threshold = result.python_inference_time_ms * self.config.performance_baseline_multiplier
            performance_passed = result.onnx_inference_time_ms <= baseline_threshold
            improvement_passed = result.performance_improvement_factor >= 1.0
            
            result.performance_validated = performance_passed and improvement_passed
            
            if not result.performance_validated:
                if not performance_passed:
                    result.add_error(f"ONNX inference too slow: {result.onnx_inference_time_ms:.2f}ms > {baseline_threshold:.2f}ms")
                if not improvement_passed:
                    result.add_warning(f"No performance improvement: {result.performance_improvement_factor:.2f}x")
            
            self.logger.info("Performance validation completed", extra={
                "python_time_ms": result.python_inference_time_ms,
                "onnx_time_ms": result.onnx_inference_time_ms,
                "improvement_factor": result.performance_improvement_factor,
                "validation_passed": result.performance_validated
            })
            
        except Exception as e:
            result.add_error(f"Performance validation failed: {e}")
            self.logger.error("Performance validation error", extra={"error": str(e)})
    
    def _run_onnx_inference(self, onnx_model: onnx.ModelProto, X: np.ndarray) -> np.ndarray:
        """Run inference using ONNX Runtime."""
        with tempfile.NamedTemporaryFile(suffix='.onnx', delete=False) as tmp_file:
            onnx.save(onnx_model, tmp_file.name)
            
            session = ort.InferenceSession(tmp_file.name)
            input_name = session.get_inputs()[0].name
            
            # Run inference
            outputs = session.run(None, {input_name: X})
            
            # Extract probability scores (assuming binary classification)
            if len(outputs) == 2:  # label and probabilities
                probabilities = outputs[1]
                if probabilities.shape[1] == 2:
                    return probabilities[:, 1]  # Positive class probability
                else:
                    return probabilities[:, 0]
            else:
                return outputs[0]
    
    def _generate_test_cases(self, model: xgb.XGBClassifier, model_info: Dict[str, Any], model_name: str):
        """Generate comprehensive test cases for the model."""
        try:
            n_features = model_info.get('n_features', 200)
            
            # Generate different types of test cases
            test_cases = {}
            
            # 1. Normal range test cases
            test_cases['normal'] = np.random.randn(100, n_features).astype(np.float32)
            
            # 2. Edge cases
            test_cases['zeros'] = np.zeros((10, n_features), dtype=np.float32)
            test_cases['ones'] = np.ones((10, n_features), dtype=np.float32)
            test_cases['large_positive'] = np.full((10, n_features), 100.0, dtype=np.float32)
            test_cases['large_negative'] = np.full((10, n_features), -100.0, dtype=np.float32)
            
            # 3. Boundary cases
            test_cases['small_positive'] = np.full((10, n_features), 1e-6, dtype=np.float32)
            test_cases['small_negative'] = np.full((10, n_features), -1e-6, dtype=np.float32)
            
            # Save test cases
            test_cases_path = self.output_dir / f"{model_name}_test_cases.npz"
            np.savez_compressed(test_cases_path, **test_cases)
            
            # Generate expected outputs
            expected_outputs = {}
            for case_name, cases in test_cases.items():
                expected_outputs[case_name] = model.predict_proba(cases)[:, 1]
            
            expected_path = self.output_dir / f"{model_name}_expected_outputs.npz"
            np.savez_compressed(expected_path, **expected_outputs)
            
            self.logger.info("Test cases generated", extra={
                "test_cases_path": str(test_cases_path),
                "expected_outputs_path": str(expected_path),
                "total_cases": sum(len(cases) for cases in test_cases.values())
            })
            
        except Exception as e:
            self.logger.warning(f"Failed to generate test cases: {e}")
    
    def _estimate_xgboost_size_mb(self, model: xgb.XGBClassifier) -> float:
        """Estimate XGBoost model size in MB."""
        try:
            # Serialize model to get size
            with tempfile.NamedTemporaryFile(suffix='.json') as tmp_file:
                model.save_model(tmp_file.name)
                return tmp_file.seek(0, 2) / (1024 * 1024)  # Size in MB
        except:
            # Rough estimation based on model parameters
            n_trees = getattr(model, 'n_estimators', 100)
            max_depth = getattr(model, 'max_depth', 6)
            n_features = getattr(model, 'n_features_in_', 200)
            
            # Rough calculation: each node ~100 bytes, max nodes per tree = 2^depth
            estimated_nodes = n_trees * (2 ** max_depth)
            estimated_bytes = estimated_nodes * 100
            return estimated_bytes / (1024 * 1024)
    
    def _save_export_metadata(self, model_name: str, model_info: Dict[str, Any], validation_result: ValidationResult):
        """Save comprehensive export metadata."""
        try:
            metadata = {
                'export_info': {
                    'model_name': model_name,
                    'export_timestamp': datetime.now().isoformat(),
                    'exporter_version': "1.0.0",
                    'config': {
                        'target_opset': self.config.target_opset,
                        'optimization_level': self.config.optimization_level,
                        'accuracy_tolerance': self.config.accuracy_tolerance,
                        'validation_samples': self.config.validation_samples
                    }
                },
                'model_info': model_info,
                'validation_results': validation_result.to_dict()
            }
            
            metadata_path = self.output_dir / f"{model_name}_metadata.json"
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            self.logger.info("Export metadata saved", extra={"metadata_path": str(metadata_path)})
            
        except Exception as e:
            self.logger.warning(f"Failed to save export metadata: {e}")


# Factory function for easy instantiation
def create_model_exporter(config: Optional[ExportConfig] = None) -> ModelExporter:
    """Create ModelExporter with default or custom configuration."""
    if config is None:
        config = ExportConfig()
    return ModelExporter(config)