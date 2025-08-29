# /stream-sentinel/src/ml/training/core/__init__.py

"""
Core Training Components

This module contains the fundamental components of the modular training pipeline:
- CheckpointManager: Atomic persistence and recovery
- DataProcessor: Data loading, preprocessing, and caching  
- HyperparameterOptimizer: Optuna integration with immediate model persistence
- PipelineOrchestrator: Component coordination and error handling

Each component is designed for independent testing, development, and deployment
while maintaining clean interfaces for integration.
"""

from .checkpoint_manager import CheckpointManager, ModelCheckpoint
from .data_processor import DataProcessor, ProcessedDataset, ValidationResult
from .hyperparameter_optimizer import HyperparameterOptimizer, OptimizationResult, StudyHandle  
from .pipeline_orchestrator import PipelineOrchestrator

__all__ = [
    "CheckpointManager",
    "ModelCheckpoint",
    "DataProcessor", 
    "ProcessedDataset",
    "ValidationResult",
    "HyperparameterOptimizer",
    "OptimizationResult", 
    "StudyHandle",
    "PipelineOrchestrator"
]