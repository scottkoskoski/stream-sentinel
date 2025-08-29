# /stream-sentinel/src/ml/training/__init__.py

"""
Modular ML Training Pipeline for Stream-Sentinel

This package implements a production-grade, fault-tolerant ML training pipeline
that addresses critical reliability issues in monolithic training approaches.

Key Features:
- Immediate persistence to prevent model loss
- Component-based architecture with fault isolation  
- Comprehensive error handling and recovery mechanisms
- Resource management for GPU/memory optimization
- Observable operations with structured logging and metrics
- Configuration management for multiple environments

Core Components:
- CheckpointManager: Atomic state persistence and recovery
- DataProcessor: IEEE-CIS data loading with caching and validation
- HyperparameterOptimizer: Optuna integration with immediate model persistence
- PipelineOrchestrator: Component coordination with error handling

Architecture:
- Modular design prevents single points of failure
- Each component can be tested and developed independently
- Hybrid approach maintains fallback to monolithic system during migration
- Enterprise-grade reliability and monitoring capabilities
"""

from .core.checkpoint_manager import CheckpointManager, ModelCheckpoint
from .core.data_processor import DataProcessor, ProcessedDataset, ValidationResult  
from .core.hyperparameter_optimizer import HyperparameterOptimizer, OptimizationResult, StudyHandle
from .core.pipeline_orchestrator import PipelineOrchestrator, TrainingPipeline

from .config.training_config import (
    TrainingConfig,
    DataConfig, 
    OptimizationConfig,
    ResourceConfig,
    MonitoringConfig,
    load_training_config
)

from .utils.logging import TrainingLogger, setup_training_logging
from .utils.metrics import TrainingMetrics, emit_training_metrics
from .utils.resource_manager import GPUResourceManager, ResourceHandle

__version__ = "1.0.0"
__author__ = "Stream-Sentinel Development Team"

__all__ = [
    # Core components
    "CheckpointManager",
    "ModelCheckpoint", 
    "DataProcessor",
    "ProcessedDataset",
    "ValidationResult",
    "HyperparameterOptimizer",
    "OptimizationResult",
    "StudyHandle",
    "PipelineOrchestrator", 
    "TrainingPipeline",
    
    # Configuration
    "TrainingConfig",
    "DataConfig",
    "OptimizationConfig", 
    "ResourceConfig",
    "MonitoringConfig",
    "load_training_config",
    
    # Utilities
    "TrainingLogger",
    "setup_training_logging",
    "TrainingMetrics",
    "emit_training_metrics",
    "GPUResourceManager",
    "ResourceHandle"
]


def create_training_pipeline(config_path: str = None, environment: str = "development") -> TrainingPipeline:
    """
    Factory function to create a fully configured training pipeline.
    
    Args:
        config_path: Path to custom configuration file
        environment: Environment name (development, production, etc.)
        
    Returns:
        Configured TrainingPipeline ready for execution
        
    Examples:
        # Development pipeline with defaults
        pipeline = create_training_pipeline()
        
        # Production pipeline with custom config
        pipeline = create_training_pipeline("config/custom.yaml", "production")
    """
    config = load_training_config(config_path, environment)
    
    # Initialize core components - convert config objects to dicts
    checkpoint_manager = CheckpointManager(config.checkpointing)
    data_processor = DataProcessor(config.data.__dict__)
    hyperopt_optimizer = HyperparameterOptimizer(config.optimization.__dict__, checkpoint_manager)
    
    # Create orchestrator
    orchestrator = PipelineOrchestrator(
        data_processor=data_processor,
        hyperopt_optimizer=hyperopt_optimizer,
        checkpoint_manager=checkpoint_manager,
        config=config.__dict__
    )
    
    return TrainingPipeline(orchestrator, config.__dict__)


# Package-level logging setup
import logging

def setup_package_logging(level: str = "INFO") -> None:
    """Setup logging for the training package."""
    setup_training_logging(level)
    
    logger = logging.getLogger(__name__)
    logger.info("Stream-Sentinel modular training pipeline initialized", 
               extra={"version": __version__, "components": len(__all__)})

# Initialize logging
setup_package_logging()