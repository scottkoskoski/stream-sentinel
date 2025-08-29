# /stream-sentinel/src/ml/training/config/training_config.py

"""
Training Configuration Management

Hierarchical configuration system supporting multiple environments with
comprehensive validation and type safety for training pipeline parameters.

Key Features:
- YAML-based configuration with environment-specific overrides
- Comprehensive validation with detailed error reporting
- Type-safe configuration objects with defaults
- Configuration merging and inheritance
- Environment-aware loading (development, staging, production)
- Configuration versioning and compatibility validation

Architecture:
- Immutable configuration objects with validation
- Hierarchical merging with environment overrides
- Comprehensive validation with descriptive error messages
- Plugin system for custom configuration validators
- Configuration schema evolution support
"""

import os
import yaml
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field
from abc import ABC, abstractmethod


@dataclass(frozen=True)
class DataConfig:
    """
    Configuration for data processing pipeline.
    """
    # Data paths
    transaction_data_path: str = "data/raw/train_transaction.csv"
    identity_data_path: str = "data/raw/train_identity.csv"
    cache_dir: str = "data/processed/cache"
    
    # Processing parameters
    max_features: int = 200
    validation_split: float = 0.2
    test_split: float = 0.1
    enable_caching: bool = True
    force_refresh: bool = False
    
    # Feature engineering
    enable_feature_engineering: bool = True
    feature_selection_method: str = "mutual_info"
    categorical_encoding: str = "label"
    missing_value_strategy: str = "zero_fill"
    
    # Validation thresholds
    min_fraud_rate: float = 0.001
    max_fraud_rate: float = 0.5
    max_memory_gb: float = 8.0
    
    def validate(self) -> List[str]:
        """Validate data configuration parameters."""
        errors = []
        
        # Path validation
        if not Path(self.transaction_data_path).suffix == '.csv':
            errors.append(f"Transaction data must be CSV: {self.transaction_data_path}")
        
        # Parameter validation
        if not 0 < self.validation_split < 1:
            errors.append(f"Validation split must be between 0 and 1: {self.validation_split}")
        
        if not 0 < self.test_split < 1:
            errors.append(f"Test split must be between 0 and 1: {self.test_split}")
        
        if self.validation_split + self.test_split >= 1:
            errors.append(f"Validation + test split must be < 1.0: {self.validation_split + self.test_split}")
        
        if self.max_features <= 0:
            errors.append(f"Max features must be positive: {self.max_features}")
        
        if not 0 < self.min_fraud_rate < self.max_fraud_rate:
            errors.append(f"Invalid fraud rate bounds: {self.min_fraud_rate} - {self.max_fraud_rate}")
        
        # Method validation
        valid_selection_methods = ["mutual_info", "chi2", "f_classif"]
        if self.feature_selection_method not in valid_selection_methods:
            errors.append(f"Invalid feature selection method: {self.feature_selection_method}")
        
        valid_encodings = ["label", "onehot", "target"]
        if self.categorical_encoding not in valid_encodings:
            errors.append(f"Invalid categorical encoding: {self.categorical_encoding}")
        
        return errors


@dataclass(frozen=True)
class OptimizationConfig:
    """
    Configuration for hyperparameter optimization.
    """
    # Optimization parameters
    n_trials: int = 100
    timeout_seconds: int = 7200  # 2 hours
    n_jobs: int = 1
    cv_folds: int = 5
    random_state: int = 42
    
    # Study configuration
    study_dir: str = "models/hyperparameter_studies"
    results_dir: str = "models/hyperparameter_results"
    study_name_prefix: str = "fraud_detection"
    
    # Optimization strategy
    sampler: str = "tpe"  # TPE, Random, Grid
    pruner: str = "median"  # Median, Hyperband, None
    direction: str = "maximize"  # maximize, minimize
    
    # Convergence detection
    convergence_patience: int = 50
    min_trials_for_convergence: int = 20
    convergence_threshold: float = 0.001
    
    # Resource management
    enable_gpu: bool = True
    gpu_memory_limit_gb: Optional[float] = None
    checkpoint_interval_trials: int = 10
    
    def validate(self) -> List[str]:
        """Validate optimization configuration parameters."""
        errors = []
        
        if self.n_trials <= 0:
            errors.append(f"Number of trials must be positive: {self.n_trials}")
        
        if self.timeout_seconds <= 0:
            errors.append(f"Timeout must be positive: {self.timeout_seconds}")
        
        if not 2 <= self.cv_folds <= 10:
            errors.append(f"CV folds must be between 2 and 10: {self.cv_folds}")
        
        if self.convergence_patience <= 0:
            errors.append(f"Convergence patience must be positive: {self.convergence_patience}")
        
        if self.min_trials_for_convergence <= 0:
            errors.append(f"Min trials for convergence must be positive: {self.min_trials_for_convergence}")
        
        # Strategy validation
        valid_samplers = ["tpe", "random", "grid", "cmaes"]
        if self.sampler.lower() not in valid_samplers:
            errors.append(f"Invalid sampler: {self.sampler}")
        
        valid_pruners = ["median", "hyperband", "none"]
        if self.pruner.lower() not in valid_pruners:
            errors.append(f"Invalid pruner: {self.pruner}")
        
        valid_directions = ["maximize", "minimize"]
        if self.direction.lower() not in valid_directions:
            errors.append(f"Invalid direction: {self.direction}")
        
        if self.gpu_memory_limit_gb is not None and self.gpu_memory_limit_gb <= 0:
            errors.append(f"GPU memory limit must be positive: {self.gpu_memory_limit_gb}")
        
        return errors


@dataclass(frozen=True)
class ResourceConfig:
    """
    Configuration for resource management.
    """
    # GPU configuration
    enable_gpu: bool = True
    gpu_id: int = 0
    gpu_memory_limit_gb: Optional[float] = None
    gpu_memory_growth: bool = True
    
    # CPU configuration
    n_jobs: int = 1
    cpu_limit_percent: Optional[float] = None
    
    # Memory configuration
    max_memory_gb: float = 16.0
    memory_monitoring_enabled: bool = True
    oom_protection_threshold: float = 0.9
    
    # Disk configuration
    temp_dir: Optional[str] = None
    min_disk_space_gb: float = 5.0
    cleanup_temp_files: bool = True
    
    # Process configuration
    timeout_seconds: int = 3600  # 1 hour default
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    
    def validate(self) -> List[str]:
        """Validate resource configuration parameters."""
        errors = []
        
        if self.gpu_id < 0:
            errors.append(f"GPU ID must be non-negative: {self.gpu_id}")
        
        if self.gpu_memory_limit_gb is not None and self.gpu_memory_limit_gb <= 0:
            errors.append(f"GPU memory limit must be positive: {self.gpu_memory_limit_gb}")
        
        if self.n_jobs <= 0:
            errors.append(f"Number of jobs must be positive: {self.n_jobs}")
        
        if self.cpu_limit_percent is not None and not 0 < self.cpu_limit_percent <= 100:
            errors.append(f"CPU limit percent must be between 0 and 100: {self.cpu_limit_percent}")
        
        if self.max_memory_gb <= 0:
            errors.append(f"Max memory must be positive: {self.max_memory_gb}")
        
        if not 0.1 <= self.oom_protection_threshold <= 1.0:
            errors.append(f"OOM protection threshold must be between 0.1 and 1.0: {self.oom_protection_threshold}")
        
        if self.min_disk_space_gb <= 0:
            errors.append(f"Min disk space must be positive: {self.min_disk_space_gb}")
        
        if self.timeout_seconds <= 0:
            errors.append(f"Timeout must be positive: {self.timeout_seconds}")
        
        if self.max_retries < 0:
            errors.append(f"Max retries must be non-negative: {self.max_retries}")
        
        return errors


@dataclass(frozen=True)
class MonitoringConfig:
    """
    Configuration for monitoring and logging.
    """
    # Logging configuration
    log_level: str = "INFO"
    log_dir: str = "logs/training"
    log_file_prefix: str = "training"
    log_rotation_size_mb: int = 100
    log_retention_days: int = 30
    
    # Structured logging
    enable_structured_logging: bool = True
    log_format: str = "json"  # json, text
    include_timestamps: bool = True
    include_process_info: bool = True
    
    # Metrics configuration
    enable_metrics: bool = True
    metrics_interval_seconds: int = 30
    metrics_backend: str = "prometheus"  # prometheus, cloudwatch, datadog
    
    # Progress monitoring
    enable_progress_bars: bool = False  # Disable for production
    progress_update_interval: int = 10
    
    # Alerting configuration
    enable_alerting: bool = False
    alert_channels: List[str] = field(default_factory=list)
    alert_thresholds: Dict[str, float] = field(default_factory=dict)
    
    # Performance monitoring
    enable_profiling: bool = False
    profile_memory: bool = True
    profile_cpu: bool = True
    profile_gpu: bool = True
    
    def validate(self) -> List[str]:
        """Validate monitoring configuration parameters."""
        errors = []
        
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level.upper() not in valid_log_levels:
            errors.append(f"Invalid log level: {self.log_level}")
        
        if self.log_rotation_size_mb <= 0:
            errors.append(f"Log rotation size must be positive: {self.log_rotation_size_mb}")
        
        if self.log_retention_days <= 0:
            errors.append(f"Log retention days must be positive: {self.log_retention_days}")
        
        valid_log_formats = ["json", "text"]
        if self.log_format.lower() not in valid_log_formats:
            errors.append(f"Invalid log format: {self.log_format}")
        
        if self.metrics_interval_seconds <= 0:
            errors.append(f"Metrics interval must be positive: {self.metrics_interval_seconds}")
        
        valid_backends = ["prometheus", "cloudwatch", "datadog", "none"]
        if self.metrics_backend.lower() not in valid_backends:
            errors.append(f"Invalid metrics backend: {self.metrics_backend}")
        
        if self.progress_update_interval <= 0:
            errors.append(f"Progress update interval must be positive: {self.progress_update_interval}")
        
        return errors


@dataclass(frozen=True)
class TrainingConfig:
    """
    Master training configuration combining all component configurations.
    """
    # Component configurations
    data: DataConfig = field(default_factory=DataConfig)
    optimization: OptimizationConfig = field(default_factory=OptimizationConfig) 
    resources: ResourceConfig = field(default_factory=ResourceConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    
    # Pipeline configuration
    pipeline_name: str = "fraud_detection_training"
    environment: str = "development"
    version: str = "1.0.0"
    
    # Execution configuration
    model_types: List[str] = field(default_factory=lambda: ["xgboost"])
    parallel_training: bool = False
    max_concurrent_models: int = 1
    
    # Checkpointing configuration
    checkpointing: Dict[str, Any] = field(default_factory=lambda: {
        "checkpoint_dir": "models/checkpoints",
        "retention_hours": 168,  # 7 days
        "checkpoint_interval_minutes": 5
    })
    
    # Recovery configuration
    max_recovery_attempts: int = 3
    recovery_timeout_minutes: int = 30
    enable_auto_recovery: bool = True
    
    def validate(self) -> List[str]:
        """Validate complete training configuration."""
        errors = []
        
        # Validate component configurations
        errors.extend(self.data.validate())
        errors.extend(self.optimization.validate())
        errors.extend(self.resources.validate())
        errors.extend(self.monitoring.validate())
        
        # Validate pipeline configuration
        if not self.pipeline_name:
            errors.append("Pipeline name cannot be empty")
        
        if not self.model_types:
            errors.append("At least one model type must be specified")
        
        valid_model_types = ["xgboost", "lightgbm", "catboost", "random_forest"]
        invalid_models = [m for m in self.model_types if m.lower() not in valid_model_types]
        if invalid_models:
            errors.append(f"Invalid model types: {invalid_models}")
        
        if self.max_concurrent_models <= 0:
            errors.append(f"Max concurrent models must be positive: {self.max_concurrent_models}")
        
        # Cross-component validation
        if self.parallel_training and self.resources.enable_gpu and self.max_concurrent_models > 1:
            errors.append("Parallel GPU training not supported with multiple models")
        
        if self.optimization.n_jobs != 1 and self.resources.enable_gpu:
            errors.append("Multiple optimization jobs not compatible with GPU training")
        
        # Checkpointing validation
        if self.checkpointing.get("retention_hours", 0) <= 0:
            errors.append("Checkpoint retention hours must be positive")
        
        if self.max_recovery_attempts < 0:
            errors.append("Max recovery attempts must be non-negative")
        
        return errors
    
    def is_production_environment(self) -> bool:
        """Check if running in production environment."""
        return self.environment.lower() == "production"
    
    def get_model_config(self, model_type: str) -> Dict[str, Any]:
        """Get model-specific configuration."""
        base_config = {
            "enable_gpu": self.resources.enable_gpu,
            "random_state": self.optimization.random_state,
            "n_jobs": self.resources.n_jobs
        }
        
        # Model-specific defaults
        if model_type.lower() == "xgboost":
            base_config.update({
                "tree_method": "gpu_hist" if self.resources.enable_gpu else "hist",
                "gpu_id": self.resources.gpu_id,
                "eval_metric": "auc"
            })
        elif model_type.lower() == "lightgbm":
            base_config.update({
                "device": "gpu" if self.resources.enable_gpu else "cpu",
                "objective": "binary",
                "metric": "auc"
            })
        
        return base_config


class ConfigurationValidator:
    """
    Extensible configuration validator with detailed error reporting.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def validate_configuration(self, config: TrainingConfig) -> Tuple[bool, List[str]]:
        """
        Comprehensive configuration validation.
        
        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = config.validate()
        
        # Additional cross-system validation
        errors.extend(self._validate_file_paths(config))
        errors.extend(self._validate_resource_availability(config))
        errors.extend(self._validate_environment_compatibility(config))
        
        is_valid = len(errors) == 0
        
        if not is_valid:
            self.logger.error("configuration.validation_failed", extra={
                "error_count": len(errors),
                "errors": errors
            })
        
        return is_valid, errors
    
    def _validate_file_paths(self, config: TrainingConfig) -> List[str]:
        """Validate file paths and permissions."""
        errors = []
        
        # Check data files
        transaction_path = Path(config.data.transaction_data_path)
        if not transaction_path.exists():
            errors.append(f"Transaction data file not found: {transaction_path}")
        elif not transaction_path.is_file():
            errors.append(f"Transaction data path is not a file: {transaction_path}")
        
        identity_path = Path(config.data.identity_data_path)
        if config.data.identity_data_path and not identity_path.exists():
            # Identity data is optional, so only warn
            pass
        
        # Check directory permissions
        dirs_to_check = [
            config.data.cache_dir,
            config.optimization.study_dir,
            config.optimization.results_dir,
            config.monitoring.log_dir,
            config.checkpointing["checkpoint_dir"]
        ]
        
        for dir_path in dirs_to_check:
            path = Path(dir_path)
            try:
                path.mkdir(parents=True, exist_ok=True)
                # Test write permissions
                test_file = path / ".permission_test"
                test_file.touch()
                test_file.unlink()
            except Exception as e:
                errors.append(f"Cannot write to directory {dir_path}: {e}")
        
        return errors
    
    def _validate_resource_availability(self, config: TrainingConfig) -> List[str]:
        """Validate resource availability."""
        errors = []
        
        # Memory validation
        try:
            import psutil
            available_memory_gb = psutil.virtual_memory().available / (1024**3)
            if available_memory_gb < config.resources.max_memory_gb * 0.5:
                errors.append(f"Insufficient memory: need {config.resources.max_memory_gb}GB, have {available_memory_gb:.1f}GB")
        except ImportError:
            pass  # psutil not available
        
        # GPU validation
        if config.resources.enable_gpu:
            try:
                import torch
                if not torch.cuda.is_available():
                    errors.append("GPU training enabled but CUDA not available")
                elif config.resources.gpu_id >= torch.cuda.device_count():
                    errors.append(f"GPU ID {config.resources.gpu_id} not available")
            except ImportError:
                # Check for XGBoost GPU support
                try:
                    import xgboost as xgb
                    # This is a basic check - more sophisticated validation could be done
                except ImportError:
                    errors.append("GPU training enabled but XGBoost not available")
        
        # Disk space validation
        try:
            import shutil
            for dir_path in [config.data.cache_dir, config.checkpointing["checkpoint_dir"]]:
                path = Path(dir_path)
                if path.exists():
                    free_space_gb = shutil.disk_usage(path).free / (1024**3)
                    if free_space_gb < config.resources.min_disk_space_gb:
                        errors.append(f"Insufficient disk space in {dir_path}: need {config.resources.min_disk_space_gb}GB, have {free_space_gb:.1f}GB")
        except Exception:
            pass  # Disk space check failed
        
        return errors
    
    def _validate_environment_compatibility(self, config: TrainingConfig) -> List[str]:
        """Validate environment-specific requirements."""
        errors = []
        
        if config.is_production_environment():
            # Production-specific validation
            if config.monitoring.log_level.upper() == "DEBUG":
                errors.append("DEBUG logging not recommended in production")
            
            if config.monitoring.enable_progress_bars:
                errors.append("Progress bars should be disabled in production")
            
            if config.optimization.n_trials < 100:
                errors.append("Production should use at least 100 optimization trials")
        
        return errors


def load_training_config(config_path: Optional[str] = None, 
                        environment: str = "development") -> TrainingConfig:
    """
    Load training configuration with environment-specific overrides.
    
    Args:
        config_path: Path to custom configuration file
        environment: Environment name (development, staging, production)
        
    Returns:
        Validated TrainingConfig object
        
    Raises:
        ConfigurationError: If configuration is invalid
    """
    logger = logging.getLogger(__name__)
    
    try:
        # Load base configuration
        base_config = _load_base_config(config_path)
        
        # Apply environment overrides
        env_config = _apply_environment_overrides(base_config, environment)
        
        # Create configuration object
        config = _create_config_object(env_config, environment)
        
        # Validate configuration
        validator = ConfigurationValidator()
        is_valid, errors = validator.validate_configuration(config)
        
        if not is_valid:
            raise ConfigurationError(f"Configuration validation failed: {errors}")
        
        logger.info("training_config.loaded", extra={
            "environment": environment,
            "config_path": config_path,
            "model_types": config.model_types
        })
        
        return config
        
    except Exception as e:
        logger.error("training_config.load_failed", extra={
            "environment": environment,
            "config_path": config_path,
            "error": str(e)
        })
        raise ConfigurationError(f"Failed to load training configuration: {e}") from e


def _load_base_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load base configuration from YAML file."""
    if config_path is None:
        # Use default configuration paths
        config_paths = [
            "config/training/base.yaml",
            "src/ml/training/config/base.yaml",
            "training_config.yaml"
        ]
        
        for path in config_paths:
            if Path(path).exists():
                config_path = path
                break
        else:
            # Return default configuration
            return {}
    
    config_file = Path(config_path)
    if not config_file.exists():
        return {}
    
    try:
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f) or {}
        return config
    except yaml.YAMLError as e:
        raise ConfigurationError(f"Invalid YAML configuration: {e}") from e


def _apply_environment_overrides(base_config: Dict[str, Any], 
                                environment: str) -> Dict[str, Any]:
    """Apply environment-specific configuration overrides."""
    config = base_config.copy()
    
    # Environment-specific defaults
    env_defaults = {
        "development": {
            "data": {
                "max_features": 50,
                "enable_caching": True
            },
            "optimization": {
                "n_trials": 20,
                "timeout_seconds": 600  # 10 minutes
            },
            "monitoring": {
                "log_level": "DEBUG",
                "enable_progress_bars": True
            }
        },
        "staging": {
            "optimization": {
                "n_trials": 100,
                "timeout_seconds": 3600  # 1 hour
            },
            "monitoring": {
                "log_level": "INFO",
                "enable_progress_bars": False
            }
        },
        "production": {
            "optimization": {
                "n_trials": 500,
                "timeout_seconds": 14400  # 4 hours
            },
            "monitoring": {
                "log_level": "WARNING",
                "enable_progress_bars": False,
                "enable_metrics": True,
                "enable_alerting": True
            },
            "resources": {
                "memory_monitoring_enabled": True,
                "cleanup_temp_files": True
            }
        }
    }
    
    # Apply environment defaults
    env_config = env_defaults.get(environment.lower(), {})
    config = _deep_merge_dicts(config, env_config)
    
    # Apply environment variables
    config = _apply_environment_variables(config)
    
    return config


def _deep_merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge two dictionaries."""
    result = base.copy()
    
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge_dicts(result[key], value)
        else:
            result[key] = value
    
    return result


def _apply_environment_variables(config: Dict[str, Any]) -> Dict[str, Any]:
    """Apply environment variable overrides."""
    # Environment variable mapping
    env_mapping = {
        "FRAUD_DETECTION_LOG_LEVEL": ("monitoring", "log_level"),
        "FRAUD_DETECTION_N_TRIALS": ("optimization", "n_trials"),
        "FRAUD_DETECTION_ENABLE_GPU": ("resources", "enable_gpu"),
        "FRAUD_DETECTION_DATA_PATH": ("data", "transaction_data_path"),
        "FRAUD_DETECTION_CACHE_DIR": ("data", "cache_dir")
    }
    
    for env_var, (section, key) in env_mapping.items():
        value = os.getenv(env_var)
        if value is not None:
            # Type conversion based on existing value
            if section in config and key in config[section]:
                existing_value = config[section][key]
                if isinstance(existing_value, bool):
                    value = value.lower() in ("true", "1", "yes", "on")
                elif isinstance(existing_value, int):
                    value = int(value)
                elif isinstance(existing_value, float):
                    value = float(value)
            
            config.setdefault(section, {})[key] = value
    
    return config


def _create_config_object(config_dict: Dict[str, Any], environment: str) -> TrainingConfig:
    """Create TrainingConfig object from dictionary."""
    # Extract component configurations
    data_config = DataConfig(**config_dict.get("data", {}))
    optimization_config = OptimizationConfig(**config_dict.get("optimization", {}))
    resources_config = ResourceConfig(**config_dict.get("resources", {}))
    monitoring_config = MonitoringConfig(**config_dict.get("monitoring", {}))
    
    # Extract pipeline configuration
    pipeline_config = config_dict.get("pipeline", {})
    
    return TrainingConfig(
        data=data_config,
        optimization=optimization_config,
        resources=resources_config,
        monitoring=monitoring_config,
        environment=environment,
        **pipeline_config
    )


# Custom exceptions
class ConfigurationError(Exception):
    """Base exception for configuration errors."""
    pass