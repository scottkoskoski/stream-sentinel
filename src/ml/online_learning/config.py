# /stream-sentinel/src/ml/online_learning/config.py

"""
Online Learning Configuration System

This module provides configuration management for the online learning pipeline,
including model parameters, drift detection thresholds, feedback processing
settings, and A/B testing configurations.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from pathlib import Path


@dataclass
class ModelConfig:
    """Configuration for model training and inference."""
    model_type: str = "lightgbm_gpu"
    max_depth: int = 9
    learning_rate: float = 0.1
    n_estimators: int = 100
    subsample: float = 0.8
    colsample_bytree: float = 0.6
    reg_alpha: float = 1.0
    reg_lambda: float = 1.0
    num_leaves: int = 231
    device: str = "gpu"
    metric: str = "auc"
    is_unbalance: bool = True
    
    # Online learning specific
    incremental_update_batch_size: int = 1000
    min_samples_for_update: int = 100
    max_samples_in_memory: int = 50000
    

@dataclass
class DriftDetectionConfig:
    """Configuration for model drift detection."""
    # Statistical drift detection
    statistical_test: str = "ks_test"  # ks_test, chi2_test, psi_test
    drift_threshold: float = 0.05
    min_reference_samples: int = 1000
    min_current_samples: int = 500
    
    # Performance-based drift detection
    performance_metric: str = "auc"
    performance_threshold: float = 0.05  # 5% degradation triggers drift
    performance_window_size: int = 1000
    
    # Feature drift detection
    feature_drift_threshold: float = 0.1
    categorical_drift_threshold: float = 0.15
    
    # Drift response
    auto_retrain_on_drift: bool = True
    drift_alert_threshold: float = 0.8  # Confidence level for drift alert


@dataclass
class FeedbackConfig:
    """Configuration for feedback processing and validation."""
    # Feedback collection
    feedback_batch_size: int = 100
    feedback_validation_threshold: float = 0.8
    max_feedback_age_hours: int = 72
    
    # Feedback quality control
    min_investigator_confidence: float = 0.7
    require_multiple_confirmations: bool = True
    min_confirmations: int = 2
    
    # Feedback weighting
    recent_feedback_weight: float = 1.0
    older_feedback_weight: float = 0.5
    weight_decay_hours: int = 24


@dataclass
class ABTestingConfig:
    """Configuration for A/B testing framework."""
    # Test setup
    min_samples_per_variant: int = 1000
    max_concurrent_tests: int = 3
    test_duration_hours: int = 48
    
    # Statistical significance
    significance_level: float = 0.05
    minimum_effect_size: float = 0.01  # 1% improvement
    power: float = 0.8
    
    # Traffic allocation
    control_traffic_percentage: float = 0.5
    challenger_traffic_percentage: float = 0.3
    champion_traffic_percentage: float = 0.2


@dataclass
class OnlineLearningConfig:
    """Main configuration for the online learning system."""
    # Environment
    environment: str = field(default_factory=lambda: os.getenv("STREAM_SENTINEL_ENV", "development"))
    
    # Model configurations
    model: ModelConfig = field(default_factory=ModelConfig)
    drift_detection: DriftDetectionConfig = field(default_factory=DriftDetectionConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    ab_testing: ABTestingConfig = field(default_factory=ABTestingConfig)
    
    # Storage paths
    model_storage_path: str = "models/online_learning"
    feedback_storage_path: str = "data/feedback" 
    drift_reports_path: str = "data/drift_reports"
    ab_test_results_path: str = "data/ab_test_results"
    
    # Redis configuration
    redis_host: str = field(default_factory=lambda: os.getenv("REDIS_HOST", "localhost"))
    redis_port: int = field(default_factory=lambda: int(os.getenv("REDIS_PORT", "6379")))
    redis_password: Optional[str] = field(default_factory=lambda: os.getenv("REDIS_PASSWORD"))
    redis_db_feedback: int = 2
    redis_db_models: int = 3
    redis_db_drift: int = 4
    redis_db_ab_tests: int = 5
    
    # Kafka configuration
    kafka_servers: str = field(default_factory=lambda: os.getenv("KAFKA_SERVERS", "localhost:9092"))
    feedback_topic: str = "fraud-feedback"
    model_updates_topic: str = "model-updates"
    drift_alerts_topic: str = "drift-alerts"
    ab_test_assignments_topic: str = "ab-test-assignments"
    
    # Monitoring
    enable_detailed_logging: bool = True
    log_predictions: bool = True
    log_feedback_processing: bool = True
    metrics_collection_interval: int = 60  # seconds
    
    # Performance thresholds
    max_prediction_latency_ms: float = 50.0
    max_training_time_minutes: int = 30
    max_memory_usage_gb: float = 8.0


def get_online_learning_config() -> OnlineLearningConfig:
    """Get online learning configuration based on environment."""
    config = OnlineLearningConfig()
    
    # Environment-specific adjustments
    if config.environment == "production":
        config.model.incremental_update_batch_size = 5000
        config.feedback.feedback_batch_size = 500
        config.drift_detection.auto_retrain_on_drift = True
        config.enable_detailed_logging = False
        config.log_predictions = False
        
    elif config.environment == "staging":
        config.model.incremental_update_batch_size = 2000
        config.feedback.feedback_batch_size = 200
        config.drift_detection.auto_retrain_on_drift = True
        
    # Development environment keeps defaults
    
    return config


# Model registry configuration
MODEL_REGISTRY_CONFIG = {
    "supported_models": [
        "lightgbm_gpu",
        "lightgbm_cpu", 
        "xgboost_gpu",
        "xgboost_cpu",
        "ensemble"
    ],
    "default_model": "lightgbm_gpu",
    "model_versioning": {
        "major_version_triggers": ["architecture_change", "feature_schema_change"],
        "minor_version_triggers": ["hyperparameter_change", "training_data_update"],
        "patch_version_triggers": ["incremental_update", "drift_correction"]
    },
    "rollback_policy": {
        "max_rollback_versions": 5,
        "auto_rollback_conditions": [
            "performance_degradation > 10%",
            "error_rate > 5%", 
            "drift_score > 0.9"
        ]
    }
}


# Feature engineering configuration
FEATURE_CONFIG = {
    "base_features": [
        "amount", "transaction_hour", "transaction_day", "amount_vs_avg_ratio",
        "daily_transaction_count", "daily_amount_total", "time_since_last_transaction",
        "amount_vs_last_ratio", "is_high_amount", "is_unusual_hour", 
        "is_rapid_transaction", "velocity_score"
    ],
    "derived_features": [
        "amount_percentile", "hour_risk_score", "user_risk_score", 
        "velocity_percentile", "amount_zscore", "temporal_anomaly_score"
    ],
    "feature_windows": {
        "short_term": "1h",
        "medium_term": "24h", 
        "long_term": "7d"
    }
}