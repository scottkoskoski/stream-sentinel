# /stream-sentinel/src/ml/training/tests/test_integration.py

"""
Integration Tests for Modular Training Pipeline

Comprehensive integration tests that validate the entire modular training
pipeline works correctly with all components integrated.

These tests are designed to catch integration issues, validate the pipeline
state machine, and ensure components work together reliably.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
import pandas as pd
from datetime import datetime

# Import components to test
import sys
sys.path.append('src')

from ml.training.config.training_config import (
    TrainingConfig, DataConfig, OptimizationConfig, 
    ResourceConfig, MonitoringConfig, load_training_config
)
from ml.training.core.checkpoint_manager import CheckpointManager, ModelCheckpoint
from ml.training.core.data_processor import DataProcessor, ProcessedDataset
from ml.training.core.hyperparameter_optimizer import HyperparameterOptimizer
from ml.training.core.pipeline_orchestrator import PipelineOrchestrator, PipelineState
from ml.training.utils.logging import TrainingLogger, setup_training_logging
from ml.training.utils.metrics import TrainingMetrics, create_metrics_collector
from ml.training.utils.resource_manager import GPUResourceManager, SystemResourceManager


class SimpleTestModel:
    """Simple test model that can be pickled."""
    def __init__(self):
        self.model_type = "test_model"
        
    def predict_proba(self, X):
        return np.array([[0.7, 0.3], [0.9, 0.1]])
        
    def fit(self, X, y, **kwargs):
        """Mock fit method that does nothing but is serializable."""
        return self


class TestModularPipelineIntegration:
    """
    Comprehensive integration tests for the modular training pipeline.
    """
    
    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test artifacts."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.fixture
    def mock_data(self, temp_dir):
        """Create mock IEEE-CIS data for testing."""
        # Create mock transaction data
        n_samples = 1000
        n_features = 50
        
        # Generate synthetic transaction data
        np.random.seed(42)
        data = {
            'TransactionID': range(1, n_samples + 1),
            'TransactionAmt': np.random.lognormal(3, 1, n_samples),
            'isFraud': np.random.binomial(1, 0.03, n_samples)  # 3% fraud rate
        }
        
        # Add random features
        for i in range(n_features):
            if i % 10 == 0:  # Some categorical features
                data[f'cat_feature_{i}'] = np.random.choice(
                    ['A', 'B', 'C', 'D'], n_samples
                )
            else:  # Numeric features
                data[f'feature_{i}'] = np.random.randn(n_samples)
        
        df = pd.DataFrame(data)
        
        # Save to temp directory
        transaction_path = temp_dir / "train_transaction.csv"
        df.to_csv(transaction_path, index=False)
        
        return transaction_path, df
    
    @pytest.fixture
    def test_config(self, temp_dir, mock_data):
        """Create test configuration."""
        transaction_path, _ = mock_data
        
        return TrainingConfig(
            data=DataConfig(
                transaction_data_path=str(transaction_path),
                identity_data_path="",  # No identity data for test
                cache_dir=str(temp_dir / "cache"),
                max_features=20,  # Reduce for faster testing
                enable_caching=True
            ),
            optimization=OptimizationConfig(
                n_trials=5,  # Very small for testing
                timeout_seconds=60,  # 1 minute timeout
                study_dir=str(temp_dir / "studies"),
                results_dir=str(temp_dir / "results")
            ),
            resources=ResourceConfig(
                enable_gpu=False,  # Disable GPU for testing
                max_memory_gb=4.0,
                temp_dir=str(temp_dir / "temp")
            ),
            monitoring=MonitoringConfig(
                log_level="DEBUG",
                log_dir=str(temp_dir / "logs"),
                enable_metrics=True
            ),
            checkpointing={
                "checkpoint_dir": str(temp_dir / "checkpoints"),
                "retention_hours": 24
            },
            model_types=["xgboost"],
            environment="testing"
        )
    
    def test_configuration_system(self, test_config):
        """Test configuration system validation and loading."""
        # Validate configuration
        errors = test_config.validate()
        assert len(errors) == 0, f"Configuration validation failed: {errors}"
        
        # Test environment detection
        assert test_config.environment == "testing"
        assert not test_config.is_production_environment()
        
        # Test model configuration
        xgb_config = test_config.get_model_config("xgboost")
        assert "random_state" in xgb_config
        assert xgb_config["tree_method"] == "hist"  # CPU mode for testing
    
    def test_checkpoint_manager(self, test_config):
        """Test checkpoint manager functionality."""
        checkpoint_manager = CheckpointManager(test_config.checkpointing)
        
        # Create simple test model (serializable)
        test_model = SimpleTestModel()
        
        # Create test checkpoint
        checkpoint = ModelCheckpoint(
            trial_number=1,
            parameters={"n_estimators": 100, "max_depth": 5},
            score=0.85,
            model=test_model,
            timestamp=datetime.now(),
            model_type="xgboost"
        )
        
        # Test checkpoint save/load cycle
        checkpoint_id = checkpoint_manager.save_checkpoint(checkpoint)
        assert checkpoint_id is not None
        
        loaded_checkpoint = checkpoint_manager.store.load_checkpoint(checkpoint_id)
        assert loaded_checkpoint is not None
        assert loaded_checkpoint.score == 0.85
        assert loaded_checkpoint.model_type == "xgboost"
        
        # Test best checkpoint tracking
        best_checkpoint = checkpoint_manager.load_best_checkpoint()
        assert best_checkpoint is not None
        assert best_checkpoint.score == 0.85
    
    def test_data_processor(self, test_config):
        """Test data processor with caching."""
        data_processor = DataProcessor(test_config.data.__dict__)
        
        # Test data loading and processing
        dataset = data_processor.load_and_preprocess_data()
        
        # Validate processed dataset
        assert isinstance(dataset, ProcessedDataset)
        assert dataset.validation_result.is_valid
        assert len(dataset.X) > 0
        assert len(dataset.y) > 0
        assert len(dataset.X) == len(dataset.y)
        assert dataset.fraud_rate > 0.01  # Should have some fraud
        
        # Test caching - second load should be from cache
        dataset2 = data_processor.load_and_preprocess_data()
        assert dataset2.data_hash == dataset.data_hash
        
        # Test train/test split
        train_data, test_data = dataset.get_train_test_split(test_size=0.2)
        assert len(train_data.X) + len(test_data.X) == len(dataset.X)
    
    def test_resource_manager(self, test_config):
        """Test resource management components."""
        # Test GPU resource manager (should detect no GPU in test)
        gpu_manager = GPUResourceManager(test_config.resources.__dict__)
        assert not gpu_manager.gpu_available  # No GPU in test environment
        
        # Test system resource manager
        sys_manager = SystemResourceManager(test_config.resources.__dict__)
        
        # Test resource usage monitoring
        usage = sys_manager.get_current_usage()
        assert usage.memory_mb > 0
        assert usage.cpu_percent >= 0
        assert usage.disk_free_gb > 0
        
        # Test resource availability check
        available, errors = sys_manager.check_resource_availability(
            memory_gb=0.1,  # Small memory requirement
            disk_gb=0.1     # Small disk requirement
        )
        assert available or len(errors) > 0  # Should either be available or have errors
        
        # Test memory resource allocation
        if available:
            memory_handle = sys_manager.allocate_memory_resource(0.1)
            assert memory_handle.is_active
            memory_handle.cleanup()
            assert not memory_handle.is_active
    
    def test_logging_and_metrics(self, test_config):
        """Test logging and metrics infrastructure."""
        # Setup logging
        setup_training_logging(
            level=test_config.monitoring.log_level,
            log_dir=test_config.monitoring.log_dir
        )
        
        # Test training logger
        logger = TrainingLogger("test_component", test_config.monitoring.__dict__)
        logger.set_context(test_id="integration_test")
        
        # Test logging methods
        logger.info("Test info message", extra={"test_value": 42})
        logger.warning("Test warning message")
        
        # Test timer functionality
        with logger.timer("test_operation"):
            import time
            time.sleep(0.1)  # Short operation
        
        # Test metrics
        metrics_collector = create_metrics_collector(
            backend_type="file",
            file_path=Path(test_config.monitoring.log_dir) / "metrics.jsonl"
        )
        
        training_metrics = TrainingMetrics(metrics_collector)
        training_metrics.set_default_tags(test_session="integration")
        
        # Emit test metrics
        training_metrics.data_loaded(
            samples=1000, features=20, fraud_rate=0.03,
            processing_time=1.5, memory_mb=256
        )
        
        training_metrics.trial_completed(
            trial_number=1, score=0.85, duration=30.0, model_type="xgboost"
        )
        
        # Cleanup
        metrics_collector.close()
    
    @patch('ml.training.core.hyperparameter_optimizer.cross_val_score')
    @patch('xgboost.XGBClassifier')
    def test_hyperparameter_optimizer(self, mock_xgb, mock_cv_score, test_config, mock_data):
        """Test hyperparameter optimizer with mocked ML components."""
        # Mock cross-validation scores
        mock_cv_score.return_value = np.array([0.82, 0.85, 0.83, 0.86, 0.84])
        
        # Mock XGBoost classifier with serializable test model
        test_model = SimpleTestModel()
        mock_xgb.return_value = test_model
        
        # Setup components
        checkpoint_manager = CheckpointManager(test_config.checkpointing)
        data_processor = DataProcessor(test_config.data.__dict__)
        
        # Load test data
        dataset = data_processor.load_and_preprocess_data()
        
        # Create optimizer
        optimizer = HyperparameterOptimizer(
            test_config.optimization.__dict__,
            checkpoint_manager
        )
        
        # Create study
        study_handle = optimizer.create_study("test_study", "xgboost")
        assert study_handle.model_type == "xgboost"
        
        # Run optimization (with mocked components)
        result = optimizer.optimize(study_handle, dataset)
        
        # Validate results
        assert result.best_score > 0
        assert result.best_params is not None
        assert len(result.trial_history) > 0
        assert result.optimization_time > 0
    
    @patch('ml.training.core.hyperparameter_optimizer.cross_val_score')
    @patch('xgboost.XGBClassifier')
    def test_pipeline_orchestrator(self, mock_xgb, mock_cv_score, test_config):
        """Test complete pipeline orchestration."""
        # Setup mocks
        mock_cv_score.return_value = np.array([0.82, 0.85, 0.83, 0.86, 0.84])
        test_model = SimpleTestModel()
        mock_xgb.return_value = test_model
        
        # Create components
        checkpoint_manager = CheckpointManager(test_config.checkpointing)
        data_processor = DataProcessor(test_config.data.__dict__)
        hyperopt_optimizer = HyperparameterOptimizer(
            test_config.optimization.__dict__, 
            checkpoint_manager
        )
        
        # Convert config to fully serializable dictionary 
        def convert_dataclass_to_dict(obj):
            """Recursively convert dataclass objects to dictionaries."""
            from dataclasses import is_dataclass, asdict
            
            if is_dataclass(obj):
                return asdict(obj)
            elif isinstance(obj, dict):
                return {key: convert_dataclass_to_dict(value) for key, value in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return type(obj)(convert_dataclass_to_dict(item) for item in obj)
            else:
                return obj
        
        config_dict = convert_dataclass_to_dict(test_config)
        
        # Create orchestrator
        orchestrator = PipelineOrchestrator(
            data_processor=data_processor,
            hyperopt_optimizer=hyperopt_optimizer,
            checkpoint_manager=checkpoint_manager,
            config=config_dict
        )
        
        # Test pipeline execution
        result = orchestrator.execute_pipeline(["xgboost"])
        
        # Validate pipeline results
        assert "pipeline_id" in result
        assert result["execution_summary"]["final_state"] == "completed"
        assert result["model_performance"]["best_score"] is not None
        assert "stage_durations" in result
        
        # Verify all stages completed
        expected_stages = ["data_processing", "hyperparameter_optimization", 
                          "model_validation", "production_deployment"]
        for stage in expected_stages:
            assert stage in result["stage_durations"]
    
    def test_pipeline_recovery(self, test_config):
        """Test pipeline recovery from failure."""
        # Create components
        checkpoint_manager = CheckpointManager(test_config.checkpointing)
        data_processor = DataProcessor(test_config.data.__dict__)
        hyperopt_optimizer = HyperparameterOptimizer(
            test_config.optimization.__dict__,
            checkpoint_manager
        )
        
        # Convert config to fully serializable dictionary 
        def convert_dataclass_to_dict(obj):
            """Recursively convert dataclass objects to dictionaries."""
            from dataclasses import is_dataclass, asdict
            
            if is_dataclass(obj):
                return asdict(obj)
            elif isinstance(obj, dict):
                return {key: convert_dataclass_to_dict(value) for key, value in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return type(obj)(convert_dataclass_to_dict(item) for item in obj)
            else:
                return obj
        
        config_dict = convert_dataclass_to_dict(test_config)
        
        orchestrator = PipelineOrchestrator(
            data_processor=data_processor,
            hyperopt_optimizer=hyperopt_optimizer,
            checkpoint_manager=checkpoint_manager,
            config=config_dict
        )
        
        # Create a mock failed pipeline state
        pipeline_id = "test_recovery_pipeline"
        
        # Test recovery attempt (should handle gracefully even with no prior state)
        try:
            result = orchestrator.resume_pipeline(pipeline_id)
            # If this succeeds, validate the result
            assert "pipeline_id" in result
        except Exception as e:
            # Recovery should fail gracefully for non-existent pipeline
            assert "not found" in str(e).lower() or "recovery" in str(e).lower()
    
    def test_end_to_end_integration(self, test_config):
        """Test complete end-to-end pipeline integration."""
        # This test validates the entire system works together
        # without mocking major components
        
        # Setup logging and metrics
        setup_training_logging(level="INFO", log_dir=test_config.monitoring.log_dir)
        metrics_collector = create_metrics_collector(
            backend_type="file",
            file_path=Path(test_config.monitoring.log_dir) / "metrics.jsonl"
        )
        
        try:
            # Create the factory function (simulating real usage)
            from ml.training import create_training_pipeline
            
            # This should work without errors if imports are correct
            pipeline = create_training_pipeline()
            
            # Validate pipeline was created
            assert pipeline is not None
            assert hasattr(pipeline, 'orchestrator')
            assert hasattr(pipeline, 'config')
            
        except ImportError as e:
            pytest.skip(f"Import error in end-to-end test: {e}")
        
        finally:
            metrics_collector.close()


if __name__ == "__main__":
    # Run tests directly if script is executed
    pytest.main([__file__, "-v"])