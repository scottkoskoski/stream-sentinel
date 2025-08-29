# /stream-sentinel/src/ml/online_learning/__init__.py

"""
Online Learning Pipeline for Stream-Sentinel

This package implements a production-grade online learning system for fraud detection
with the following key capabilities:

1. Real-time model drift detection and alerting
2. Feedback-driven incremental learning
3. A/B testing framework for model comparison
4. Model versioning and rollback capabilities
5. Performance monitoring and degradation alerts

Key Components:
- FeedbackProcessor: Collects and validates fraud investigation feedback
- DriftDetector: Monitors model performance and data distribution changes
- IncrementalLearner: Updates models with new validated feedback
- ModelRegistry: Manages model versions and deployment
- ABTestManager: Coordinates model comparison experiments

Architecture:
- Event-driven design using Kafka for communication
- Redis for state management and caching
- Modular design for easy testing and maintenance
- Production-ready error handling and monitoring
"""

from .config import (
    OnlineLearningConfig,
    ModelConfig, 
    DriftDetectionConfig,
    FeedbackConfig,
    ABTestingConfig,
    get_online_learning_config,
    MODEL_REGISTRY_CONFIG,
    FEATURE_CONFIG
)

from .feedback_processor import FeedbackProcessor
from .drift_detector import DriftDetector, PerformanceMetrics
from .incremental_learner import IncrementalLearner
from .model_registry import ModelRegistry
from .ab_test_manager import ABTestManager

__version__ = "1.0.0"
__author__ = "Stream-Sentinel Development Team"

__all__ = [
    # Configuration
    "OnlineLearningConfig",
    "ModelConfig",
    "DriftDetectionConfig", 
    "FeedbackConfig",
    "ABTestingConfig",
    "get_online_learning_config",
    "MODEL_REGISTRY_CONFIG",
    "FEATURE_CONFIG",
    
    # Core components
    "FeedbackProcessor",
    "DriftDetector",
    "PerformanceMetrics",
    "IncrementalLearner",
    "ModelRegistry",
    "ABTestManager"
]


# Package-level logging configuration
import logging

def setup_logging(level: str = "INFO") -> None:
    """Setup logging for the online learning package."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('online_learning.log')
        ]
    )

# Initialize logging
setup_logging()