# /stream-sentinel/src/ml/online_learning/model_registry.py

"""
Model Registry and Version Management System

This module implements a comprehensive model registry that handles versioning,
deployment, rollback, and lifecycle management of fraud detection models.
It provides enterprise-grade model governance and audit capabilities.

Key features:
- Semantic versioning with automated version bumping
- Model metadata and lineage tracking
- Automated rollback with performance monitoring
- Model deployment lifecycle management
- A/B testing integration and traffic routing
- Audit trails and compliance reporting
"""

import json
import time
import logging
import pickle
import hashlib
import shutil
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple, Union
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
import tempfile
import redis
from confluent_kafka import Producer
import numpy as np
import pandas as pd
from packaging import version
import joblib

from .config import OnlineLearningConfig, get_online_learning_config, MODEL_REGISTRY_CONFIG


class ModelStatus(Enum):
    """Model deployment status."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    ARCHIVED = "archived"
    DEPRECATED = "deprecated"
    ROLLBACK_CANDIDATE = "rollback_candidate"


class DeploymentStage(Enum):
    """Model deployment stages."""
    TRAINING = "training"
    VALIDATION = "validation"
    STAGING_DEPLOYMENT = "staging_deployment"
    PRODUCTION_DEPLOYMENT = "production_deployment"
    MONITORING = "monitoring"
    RETIRED = "retired"


@dataclass
class ModelMetadata:
    """Comprehensive metadata for model registry."""
    model_id: str
    version: str
    name: str
    description: str
    
    # Model information
    model_type: str
    algorithm: str
    framework: str
    training_data_hash: str
    
    # Performance metrics
    performance_metrics: Dict[str, float]
    validation_metrics: Dict[str, float]
    
    # Training information
    training_start_time: str
    training_end_time: str
    training_duration_minutes: float
    training_samples: int
    
    # Deployment information
    status: ModelStatus
    deployment_stage: DeploymentStage
    deployed_at: Optional[str] = None
    deployed_by: str = "system"
    
    # Lineage and dependencies
    parent_version: Optional[str] = None
    training_trigger: str = "manual"  # manual, drift, scheduled, feedback
    dataset_version: str = "unknown"
    feature_version: str = "unknown"
    
    # Resource requirements
    memory_requirements_mb: int = 0
    cpu_requirements: float = 0.0
    gpu_requirements: bool = False
    inference_latency_ms: float = 0.0
    
    # Governance
    approval_required: bool = False
    approved_by: Optional[str] = None
    approval_timestamp: Optional[str] = None
    rollback_policy: Dict[str, Any] = field(default_factory=dict)
    
    # Monitoring
    monitoring_alerts: List[str] = field(default_factory=list)
    performance_degradation_threshold: float = 0.05
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        model_dict = asdict(self)
        model_dict["status"] = self.status.value
        model_dict["deployment_stage"] = self.deployment_stage.value
        return model_dict


@dataclass
class ModelArtifact:
    """Model artifact storage information."""
    model_id: str
    version: str
    artifact_type: str  # "model", "scaler", "encoder", "config"
    
    # Storage information
    storage_path: str
    storage_backend: str  # "redis", "filesystem", "s3"
    checksum: str
    size_bytes: int
    compressed: bool = False
    
    # Access information
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_accessed: Optional[str] = None
    access_count: int = 0


@dataclass
class DeploymentRecord:
    """Record of model deployment."""
    deployment_id: str
    model_id: str
    version: str
    
    # Deployment details
    deployment_timestamp: str
    deployment_environment: str  # development, staging, production
    traffic_percentage: float  # For A/B testing
    deployment_strategy: str  # blue_green, canary, rolling
    
    # Success/failure information
    deployment_status: str  # pending, success, failed, rolled_back
    deployment_errors: List[str] = field(default_factory=list)
    rollback_reason: Optional[str] = None
    rollback_timestamp: Optional[str] = None
    
    # Performance tracking
    post_deployment_metrics: Dict[str, float] = field(default_factory=dict)
    monitoring_alerts_triggered: int = 0


class ModelRegistry:
    """
    Comprehensive model registry for fraud detection models.
    
    This class provides:
    1. Model versioning with semantic versioning
    2. Model artifact storage and retrieval
    3. Deployment lifecycle management
    4. Performance monitoring and rollback
    5. Audit trails and compliance reporting
    6. A/B testing integration
    """
    
    def __init__(self, config: Optional[OnlineLearningConfig] = None):
        self.config = config or get_online_learning_config()
        self.logger = logging.getLogger(__name__)
        
        # Initialize storage
        self._init_redis()
        self._init_kafka()
        self._init_filesystem_storage()
        
        # Registry state
        self.registered_models: Dict[str, ModelMetadata] = {}
        self.model_artifacts: Dict[str, List[ModelArtifact]] = {}
        self.deployment_history: List[DeploymentRecord] = []
        
        # Active deployments
        self.active_deployments = {
            "production": None,
            "staging": None,
            "development": None
        }
        
        # Load existing registry state
        self._load_registry_state()
        
        self.logger.info("ModelRegistry initialized successfully")
    
    def _init_redis(self) -> None:
        """Initialize Redis connections."""
        try:
            self.redis_models = redis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password,
                db=self.config.redis_db_models,
                decode_responses=True
            )
            
            # Separate connection for binary data
            self.redis_binary = redis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password,
                db=self.config.redis_db_models,
                decode_responses=False
            )
            
            self.redis_models.ping()
            self.redis_binary.ping()
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Redis: {e}")
            raise
    
    def _init_kafka(self) -> None:
        """Initialize Kafka producer for model events."""
        try:
            producer_config = {
                'bootstrap.servers': self.config.kafka_servers,
                'linger.ms': 5,
                'compression.type': 'lz4'
            }
            self.producer = Producer(producer_config)
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Kafka: {e}")
            raise
    
    def _init_filesystem_storage(self) -> None:
        """Initialize filesystem storage for model artifacts."""
        self.storage_path = Path(self.config.model_storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        (self.storage_path / "models").mkdir(exist_ok=True)
        (self.storage_path / "metadata").mkdir(exist_ok=True)
        (self.storage_path / "artifacts").mkdir(exist_ok=True)
        (self.storage_path / "backups").mkdir(exist_ok=True)
    
    def _load_registry_state(self) -> None:
        """Load existing registry state from storage."""
        try:
            # Load registered models
            registry_data = self.redis_models.get("model_registry_state")
            if registry_data:
                registry_dict = json.loads(registry_data)
                
                for model_id, metadata_dict in registry_dict.get("models", {}).items():
                    metadata = ModelMetadata(**metadata_dict)
                    metadata.status = ModelStatus(metadata_dict["status"])
                    metadata.deployment_stage = DeploymentStage(metadata_dict["deployment_stage"])
                    self.registered_models[model_id] = metadata
            
            # Load active deployments
            deployments_data = self.redis_models.get("active_deployments")
            if deployments_data:
                self.active_deployments = json.loads(deployments_data)
            
            self.logger.info(f"Loaded {len(self.registered_models)} models from registry")
            
        except Exception as e:
            self.logger.error(f"Failed to load registry state: {e}")
    
    def register_model(self, model: Any, metadata: ModelMetadata, 
                      artifacts: Optional[List[Tuple[str, Any]]] = None) -> bool:
        """
        Register a new model in the registry.
        
        Args:
            model: The trained model object
            metadata: Model metadata
            artifacts: Additional artifacts (scalers, encoders, etc.)
            
        Returns:
            True if registration successful
        """
        try:
            # Validate metadata
            if not self._validate_model_metadata(metadata):
                return False
            
            # Assign version if not specified
            if not metadata.version:
                metadata.version = self._generate_next_version(metadata.model_id, metadata.training_trigger)
            
            # Store model artifact
            model_artifact = self._store_model_artifact(model, metadata)
            if not model_artifact:
                return False
            
            # Store additional artifacts
            artifact_list = [model_artifact]
            if artifacts:
                for artifact_type, artifact_data in artifacts:
                    artifact = self._store_additional_artifact(
                        artifact_type, artifact_data, metadata
                    )
                    if artifact:
                        artifact_list.append(artifact)
            
            # Update registry
            self.registered_models[metadata.model_id] = metadata
            self.model_artifacts[metadata.model_id] = artifact_list
            
            # Save registry state
            self._save_registry_state()
            
            # Publish registration event
            self._publish_model_event("model_registered", metadata)
            
            self.logger.info(f"Registered model {metadata.model_id} v{metadata.version}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to register model: {e}")
            return False
    
    def deploy_model(self, model_id: str, environment: str = "production", 
                    traffic_percentage: float = 100.0,
                    deployment_strategy: str = "blue_green") -> bool:
        """
        Deploy a model to specified environment.
        
        Args:
            model_id: Model identifier
            environment: Target environment (development, staging, production)
            traffic_percentage: Percentage of traffic to route to new model
            deployment_strategy: Deployment strategy
            
        Returns:
            True if deployment successful
        """
        if model_id not in self.registered_models:
            self.logger.error(f"Model {model_id} not found in registry")
            return False
        
        metadata = self.registered_models[model_id]
        
        try:
            # Create deployment record
            deployment = DeploymentRecord(
                deployment_id=f"deploy_{model_id}_{int(time.time())}",
                model_id=model_id,
                version=metadata.version,
                deployment_timestamp=datetime.now().isoformat(),
                deployment_environment=environment,
                traffic_percentage=traffic_percentage,
                deployment_strategy=deployment_strategy,
                deployment_status="pending"
            )
            
            # Pre-deployment validation
            if not self._validate_deployment_readiness(metadata, environment):
                deployment.deployment_status = "failed"
                deployment.deployment_errors.append("Pre-deployment validation failed")
                return False
            
            # Load model for deployment
            model = self._load_model_artifact(model_id)
            if model is None:
                deployment.deployment_status = "failed"
                deployment.deployment_errors.append("Failed to load model artifact")
                return False
            
            # Execute deployment
            success = self._execute_deployment(model, metadata, environment, traffic_percentage)
            
            if success:
                # Update deployment record
                deployment.deployment_status = "success"
                
                # Update metadata
                metadata.status = ModelStatus.PRODUCTION if environment == "production" else ModelStatus.STAGING
                metadata.deployed_at = datetime.now().isoformat()
                metadata.deployment_stage = (DeploymentStage.PRODUCTION_DEPLOYMENT 
                                           if environment == "production" 
                                           else DeploymentStage.STAGING_DEPLOYMENT)
                
                # Update active deployments
                old_deployment = self.active_deployments.get(environment)
                self.active_deployments[environment] = {
                    "model_id": model_id,
                    "version": metadata.version,
                    "deployed_at": metadata.deployed_at,
                    "traffic_percentage": traffic_percentage
                }
                
                # Archive old deployment if exists
                if old_deployment:
                    self._archive_deployment(old_deployment, f"Replaced by {model_id} v{metadata.version}")
                
                # Publish deployment event
                self._publish_model_event("model_deployed", metadata, {
                    "environment": environment,
                    "traffic_percentage": traffic_percentage,
                    "deployment_strategy": deployment_strategy
                })
                
                self.logger.info(f"Successfully deployed {model_id} v{metadata.version} to {environment}")
                
            else:
                deployment.deployment_status = "failed"
                deployment.deployment_errors.append("Deployment execution failed")
            
            # Record deployment
            self.deployment_history.append(deployment)
            self._save_deployment_record(deployment)
            self._save_registry_state()
            
            return success
            
        except Exception as e:
            self.logger.error(f"Deployment failed: {e}")
            return False
    
    def rollback_model(self, environment: str = "production", 
                      reason: str = "Manual rollback") -> bool:
        """
        Rollback to previous model version.
        
        Args:
            environment: Environment to rollback
            reason: Reason for rollback
            
        Returns:
            True if rollback successful
        """
        try:
            current_deployment = self.active_deployments.get(environment)
            if not current_deployment:
                self.logger.error(f"No active deployment in {environment}")
                return False
            
            # Find previous version
            previous_deployment = self._find_previous_deployment(environment)
            if not previous_deployment:
                self.logger.error("No previous deployment found for rollback")
                return False
            
            # Execute rollback
            success = self.deploy_model(
                previous_deployment["model_id"],
                environment,
                previous_deployment["traffic_percentage"],
                "rollback"
            )
            
            if success:
                # Update current deployment as rolled back
                current_model_id = current_deployment["model_id"]
                if current_model_id in self.registered_models:
                    self.registered_models[current_model_id].status = ModelStatus.ROLLBACK_CANDIDATE
                
                # Record rollback reason
                rollback_record = {
                    "timestamp": datetime.now().isoformat(),
                    "environment": environment,
                    "rolled_back_from": current_deployment,
                    "rolled_back_to": previous_deployment,
                    "reason": reason
                }
                
                self.redis_models.lpush("rollback_history", json.dumps(rollback_record))
                
                # Publish rollback event
                self._publish_model_event("model_rolled_back", None, rollback_record)
                
                self.logger.info(f"Successfully rolled back {environment} deployment")
                
            return success
            
        except Exception as e:
            self.logger.error(f"Rollback failed: {e}")
            return False
    
    def get_model_metadata(self, model_id: str) -> Optional[ModelMetadata]:
        """Get metadata for a specific model."""
        return self.registered_models.get(model_id)
    
    def list_models(self, status: Optional[ModelStatus] = None) -> List[ModelMetadata]:
        """List all models, optionally filtered by status."""
        models = list(self.registered_models.values())
        
        if status:
            models = [m for m in models if m.status == status]
        
        # Sort by version (newest first)
        models.sort(key=lambda m: version.parse(m.version), reverse=True)
        
        return models
    
    def get_active_model(self, environment: str = "production") -> Optional[Any]:
        """Get the currently active model for an environment."""
        deployment = self.active_deployments.get(environment)
        if not deployment:
            return None
        
        return self._load_model_artifact(deployment["model_id"])
    
    def _validate_model_metadata(self, metadata: ModelMetadata) -> bool:
        """Validate model metadata before registration."""
        required_fields = ["model_id", "name", "model_type", "algorithm"]
        
        for field in required_fields:
            if not getattr(metadata, field):
                self.logger.error(f"Missing required field: {field}")
                return False
        
        # Validate version format if provided
        if metadata.version:
            try:
                version.parse(metadata.version)
            except Exception:
                self.logger.error(f"Invalid version format: {metadata.version}")
                return False
        
        return True
    
    def _generate_next_version(self, model_id: str, trigger: str) -> str:
        """Generate next version number based on existing versions."""
        existing_versions = []
        
        # Find existing versions for this model
        for mid, metadata in self.registered_models.items():
            if mid.startswith(model_id):
                try:
                    existing_versions.append(version.parse(metadata.version))
                except Exception:
                    continue
        
        if not existing_versions:
            return "1.0.0"
        
        # Get latest version
        latest = max(existing_versions)
        
        # Determine version bump based on trigger
        version_config = MODEL_REGISTRY_CONFIG["model_versioning"]
        
        if trigger in version_config["major_version_triggers"]:
            return f"{latest.major + 1}.0.0"
        elif trigger in version_config["minor_version_triggers"]:
            return f"{latest.major}.{latest.minor + 1}.0"
        else:  # patch version
            return f"{latest.major}.{latest.minor}.{latest.micro + 1}"
    
    def _store_model_artifact(self, model: Any, metadata: ModelMetadata) -> Optional[ModelArtifact]:
        """Store model artifact in storage backend."""
        try:
            # Serialize model
            model_bytes = pickle.dumps(model)
            checksum = hashlib.sha256(model_bytes).hexdigest()
            
            # Store in Redis
            storage_key = f"model_artifact:{metadata.model_id}:{metadata.version}"
            
            import base64
            model_b64 = base64.b64encode(model_bytes).decode()
            self.redis_models.setex(storage_key, 2592000, model_b64)  # 30 day TTL
            
            # Also store in filesystem as backup
            model_path = (self.storage_path / "models" / 
                         f"{metadata.model_id}_v{metadata.version}.pkl")
            
            with open(model_path, 'wb') as f:
                pickle.dump(model, f)
            
            # Create artifact record
            artifact = ModelArtifact(
                model_id=metadata.model_id,
                version=metadata.version,
                artifact_type="model",
                storage_path=str(model_path),
                storage_backend="redis",
                checksum=checksum,
                size_bytes=len(model_bytes)
            )
            
            return artifact
            
        except Exception as e:
            self.logger.error(f"Failed to store model artifact: {e}")
            return None
    
    def _store_additional_artifact(self, artifact_type: str, data: Any, 
                                  metadata: ModelMetadata) -> Optional[ModelArtifact]:
        """Store additional artifacts like scalers or encoders."""
        try:
            # Serialize artifact
            artifact_bytes = pickle.dumps(data)
            checksum = hashlib.sha256(artifact_bytes).hexdigest()
            
            # Store in filesystem
            artifact_path = (self.storage_path / "artifacts" / 
                           f"{metadata.model_id}_v{metadata.version}_{artifact_type}.pkl")
            
            with open(artifact_path, 'wb') as f:
                pickle.dump(data, f)
            
            # Create artifact record
            artifact = ModelArtifact(
                model_id=metadata.model_id,
                version=metadata.version,
                artifact_type=artifact_type,
                storage_path=str(artifact_path),
                storage_backend="filesystem",
                checksum=checksum,
                size_bytes=len(artifact_bytes)
            )
            
            return artifact
            
        except Exception as e:
            self.logger.error(f"Failed to store {artifact_type} artifact: {e}")
            return None
    
    def _load_model_artifact(self, model_id: str) -> Optional[Any]:
        """Load model artifact from storage."""
        if model_id not in self.registered_models:
            return None
        
        metadata = self.registered_models[model_id]
        
        try:
            # Try Redis first
            storage_key = f"model_artifact:{model_id}:{metadata.version}"
            model_data = self.redis_models.get(storage_key)
            
            if model_data:
                import base64
                model_bytes = base64.b64decode(model_data)
                model = pickle.loads(model_bytes)
                
                # Update access statistics
                self._update_access_stats(model_id, metadata.version)
                
                return model
            
            # Fallback to filesystem
            model_path = self.storage_path / "models" / f"{model_id}_v{metadata.version}.pkl"
            
            if model_path.exists():
                with open(model_path, 'rb') as f:
                    model = pickle.load(f)
                
                self._update_access_stats(model_id, metadata.version)
                return model
            
            self.logger.error(f"Model artifact not found: {model_id} v{metadata.version}")
            return None
            
        except Exception as e:
            self.logger.error(f"Failed to load model artifact: {e}")
            return None
    
    def _validate_deployment_readiness(self, metadata: ModelMetadata, environment: str) -> bool:
        """Validate that model is ready for deployment."""
        # Check approval requirements
        if metadata.approval_required and not metadata.approved_by:
            self.logger.error(f"Model {metadata.model_id} requires approval for deployment")
            return False
        
        # Check performance thresholds
        if "auc" in metadata.performance_metrics:
            min_auc = 0.7 if environment == "production" else 0.6
            if metadata.performance_metrics["auc"] < min_auc:
                self.logger.error(f"Model AUC {metadata.performance_metrics['auc']:.3f} below threshold")
                return False
        
        # Check deployment stage progression
        if environment == "production" and metadata.deployment_stage not in [
            DeploymentStage.STAGING_DEPLOYMENT, DeploymentStage.MONITORING
        ]:
            self.logger.error("Model must pass staging before production deployment")
            return False
        
        return True
    
    def _execute_deployment(self, model: Any, metadata: ModelMetadata, 
                          environment: str, traffic_percentage: float) -> bool:
        """Execute the actual model deployment."""
        try:
            # In a real implementation, this would:
            # 1. Update model serving infrastructure
            # 2. Configure traffic routing
            # 3. Update health checks
            # 4. Initialize monitoring
            
            # For this implementation, we'll simulate deployment
            deployment_key = f"deployed_model:{environment}"
            
            deployment_info = {
                "model_id": metadata.model_id,
                "version": metadata.version,
                "deployed_at": datetime.now().isoformat(),
                "traffic_percentage": traffic_percentage,
                "status": "active"
            }
            
            self.redis_models.set(deployment_key, json.dumps(deployment_info))
            
            # Store model for serving
            serving_key = f"serving_model:{environment}"
            model_bytes = pickle.dumps(model)
            
            import base64
            model_b64 = base64.b64encode(model_bytes).decode()
            self.redis_models.set(serving_key, model_b64)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to execute deployment: {e}")
            return False
    
    def _find_previous_deployment(self, environment: str) -> Optional[Dict[str, Any]]:
        """Find the previous deployment for rollback."""
        # This is a simplified implementation
        # In production, you'd maintain a proper deployment history
        
        rollback_history = self.redis_models.lrange("rollback_history", 0, -1)
        
        for record_json in rollback_history:
            record = json.loads(record_json)
            if record["environment"] == environment:
                return record.get("rolled_back_to")
        
        # Fallback: find any previous deployment
        deployment_records = [d for d in self.deployment_history 
                            if d.deployment_environment == environment 
                            and d.deployment_status == "success"]
        
        if len(deployment_records) >= 2:
            prev_deployment = deployment_records[-2]  # Second to last
            return {
                "model_id": prev_deployment.model_id,
                "version": prev_deployment.version,
                "traffic_percentage": prev_deployment.traffic_percentage
            }
        
        return None
    
    def _archive_deployment(self, deployment: Dict[str, Any], reason: str) -> None:
        """Archive a deployment that's being replaced."""
        archive_record = {
            "archived_at": datetime.now().isoformat(),
            "deployment": deployment,
            "reason": reason
        }
        
        self.redis_models.lpush("archived_deployments", json.dumps(archive_record))
    
    def _update_access_stats(self, model_id: str, version: str) -> None:
        """Update model access statistics."""
        try:
            stats_key = f"access_stats:{model_id}:{version}"
            stats = self.redis_models.get(stats_key)
            
            if stats:
                stats_dict = json.loads(stats)
            else:
                stats_dict = {"access_count": 0, "last_accessed": None}
            
            stats_dict["access_count"] += 1
            stats_dict["last_accessed"] = datetime.now().isoformat()
            
            self.redis_models.setex(stats_key, 2592000, json.dumps(stats_dict))  # 30 day TTL
            
        except Exception as e:
            self.logger.error(f"Failed to update access stats: {e}")
    
    def _save_registry_state(self) -> None:
        """Save current registry state to persistent storage."""
        try:
            registry_state = {
                "models": {mid: metadata.to_dict() 
                          for mid, metadata in self.registered_models.items()},
                "last_updated": datetime.now().isoformat()
            }
            
            self.redis_models.set("model_registry_state", json.dumps(registry_state))
            self.redis_models.set("active_deployments", json.dumps(self.active_deployments))
            
        except Exception as e:
            self.logger.error(f"Failed to save registry state: {e}")
    
    def _save_deployment_record(self, deployment: DeploymentRecord) -> None:
        """Save deployment record to persistent storage."""
        try:
            record_key = f"deployment_record:{deployment.deployment_id}"
            self.redis_models.setex(record_key, 2592000, json.dumps(asdict(deployment)))
            
        except Exception as e:
            self.logger.error(f"Failed to save deployment record: {e}")
    
    def _publish_model_event(self, event_type: str, metadata: Optional[ModelMetadata], 
                           additional_data: Optional[Dict[str, Any]] = None) -> None:
        """Publish model lifecycle events to Kafka."""
        try:
            event = {
                "event_type": event_type,
                "timestamp": datetime.now().isoformat(),
                "model_metadata": metadata.to_dict() if metadata else None,
                "additional_data": additional_data or {}
            }
            
            self.producer.produce(
                self.config.model_updates_topic,
                key=f"{event_type}_{int(time.time())}",
                value=json.dumps(event)
            )
            self.producer.flush()
            
        except Exception as e:
            self.logger.error(f"Failed to publish model event: {e}")
    
    def get_deployment_history(self, environment: Optional[str] = None, 
                             limit: int = 50) -> List[DeploymentRecord]:
        """Get deployment history, optionally filtered by environment."""
        history = self.deployment_history.copy()
        
        if environment:
            history = [d for d in history if d.deployment_environment == environment]
        
        # Sort by timestamp (newest first)
        history.sort(key=lambda d: d.deployment_timestamp, reverse=True)
        
        return history[:limit]
    
    def get_registry_statistics(self) -> Dict[str, Any]:
        """Get registry statistics and health metrics."""
        total_models = len(self.registered_models)
        status_counts = {}
        
        for metadata in self.registered_models.values():
            status = metadata.status.value
            status_counts[status] = status_counts.get(status, 0) + 1
        
        return {
            "total_models": total_models,
            "status_distribution": status_counts,
            "active_deployments": len([d for d in self.active_deployments.values() if d]),
            "deployment_history_size": len(self.deployment_history),
            "storage_backend": "redis + filesystem",
            "registry_health": "healthy"  # Add actual health checks
        }