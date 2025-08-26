# /stream-sentinel/src/ml/online_learning/incremental_learner.py

"""
Incremental Learning System for Online Model Updates

This module implements incremental learning capabilities that allow fraud detection
models to be updated with new data without full retraining. It supports various
incremental learning strategies and maintains model performance during updates.

Key features:
- Incremental updates for tree-based models (LightGBM, XGBoost)
- Memory-efficient batch processing
- Model performance validation during updates
- Catastrophic forgetting prevention
- Adaptive learning rate scheduling
"""

import json
import time
import logging
import pickle
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple, Union
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
import tempfile
import shutil
from collections import deque
import redis
from confluent_kafka import Consumer, Producer

# ML libraries
import lightgbm as lgb
import xgboost as xgb
from sklearn.metrics import roc_auc_score, precision_recall_curve, average_precision_score
from sklearn.model_selection import train_test_split
import joblib

from .config import OnlineLearningConfig, get_online_learning_config
from .feedback_processor import ProcessedFeedback, FeedbackLabel


class UpdateStrategy(Enum):
    """Strategies for incremental model updates."""
    CONTINUOUS = "continuous"        # Update after each batch
    SCHEDULED = "scheduled"          # Update at fixed intervals
    DRIFT_TRIGGERED = "drift_triggered"  # Update when drift detected
    PERFORMANCE_BASED = "performance_based"  # Update when performance degrades


class ModelState(Enum):
    """Model training states."""
    IDLE = "idle"
    TRAINING = "training"
    VALIDATING = "validating"
    UPDATING = "updating"
    FAILED = "failed"


@dataclass
class TrainingBatch:
    """Batch of training data for incremental learning."""
    batch_id: str
    features: pd.DataFrame
    labels: np.ndarray
    sample_weights: Optional[np.ndarray]
    timestamp: str
    source: str  # "feedback", "synthetic", "historical"
    
    # Metadata
    fraud_rate: float
    total_samples: int
    quality_score: float = 1.0
    
    def __post_init__(self):
        """Validate batch after initialization."""
        if len(self.features) != len(self.labels):
            raise ValueError("Features and labels must have same length")
        
        if self.sample_weights is not None and len(self.sample_weights) != len(self.labels):
            raise ValueError("Sample weights must have same length as labels")


@dataclass
class UpdateResult:
    """Result of an incremental model update."""
    update_id: str
    success: bool
    timestamp: str
    
    # Performance metrics
    old_performance: Dict[str, float]
    new_performance: Dict[str, float]
    performance_change: Dict[str, float]
    
    # Update details
    samples_used: int
    training_time_seconds: float
    model_size_change: int  # bytes
    
    # Validation results
    validation_passed: bool
    validation_errors: List[str] = field(default_factory=list)
    
    # Model metadata
    model_version: str
    previous_version: str


class IncrementalLearner:
    """
    Implements incremental learning for fraud detection models.
    
    This class handles:
    1. Batch collection and preprocessing from feedback
    2. Incremental model updates with various strategies
    3. Performance monitoring and validation
    4. Catastrophic forgetting prevention
    5. Model versioning and rollback capabilities
    """
    
    def __init__(self, config: Optional[OnlineLearningConfig] = None):
        self.config = config or get_online_learning_config()
        self.logger = logging.getLogger(__name__)
        
        # Initialize connections
        self._init_redis()
        self._init_kafka()
        
        # Model management
        self.current_model = None
        self.model_version = "1.0.0"
        self.model_state = ModelState.IDLE
        self.model_path = Path(self.config.model_storage_path)
        self.model_path.mkdir(parents=True, exist_ok=True)
        
        # Training data management
        self.training_queue = deque(maxlen=100)
        self.validation_data = None
        self.performance_history = deque(maxlen=1000)
        
        # Incremental learning configuration
        self.update_strategy = UpdateStrategy.CONTINUOUS
        self.min_batch_size = self.config.model.min_samples_for_update
        self.max_memory_samples = self.config.model.max_samples_in_memory
        
        # Performance tracking
        self.update_statistics = {
            "total_updates": 0,
            "successful_updates": 0,
            "failed_updates": 0,
            "rollbacks": 0,
            "total_samples_processed": 0
        }
        
        # Load existing model if available
        self._load_current_model()
        
        self.logger.info("IncrementalLearner initialized successfully")
    
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
            self.redis_models.ping()
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Redis connections: {e}")
            raise
    
    def _init_kafka(self) -> None:
        """Initialize Kafka consumer and producer."""
        try:
            # Consumer for model update triggers
            consumer_config = {
                'bootstrap.servers': self.config.kafka_servers,
                'group.id': 'incremental-learner',
                'auto.offset.reset': 'latest',
                'enable.auto.commit': True
            }
            self.consumer = Consumer(consumer_config)
            self.consumer.subscribe([self.config.model_updates_topic])
            
            # Producer for update notifications
            producer_config = {
                'bootstrap.servers': self.config.kafka_servers,
                'linger.ms': 5,
                'compression.type': 'lz4'
            }
            self.producer = Producer(producer_config)
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Kafka: {e}")
            raise
    
    def _load_current_model(self) -> bool:
        """Load the current production model."""
        try:
            # Try to load from Redis first
            model_key = "current_fraud_model"
            model_data = self.redis_models.get(model_key)
            
            if model_data:
                # Model is stored as base64 in Redis
                import base64
                model_bytes = base64.b64decode(model_data)
                
                # Create temporary file and load
                with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                    temp_file.write(model_bytes)
                    temp_path = temp_file.name
                
                self.current_model = joblib.load(temp_path)
                Path(temp_path).unlink()  # Clean up
                
                # Load metadata
                metadata_key = "current_model_metadata"
                metadata = self.redis_models.get(metadata_key)
                if metadata:
                    metadata_dict = json.loads(metadata)
                    self.model_version = metadata_dict.get("version", "1.0.0")
                
                self.logger.info(f"Loaded model version {self.model_version} from Redis")
                return True
            
            # Fallback to filesystem
            model_file = self.model_path / "ieee_fraud_model_production.pkl"
            if model_file.exists():
                with open(model_file, 'rb') as f:
                    self.current_model = pickle.load(f)
                
                self.logger.info("Loaded model from filesystem")
                return True
            
            self.logger.warning("No existing model found")
            return False
            
        except Exception as e:
            self.logger.error(f"Failed to load current model: {e}")
            return False
    
    def add_training_batch(self, processed_feedback: List[ProcessedFeedback]) -> bool:
        """
        Add processed feedback as a training batch.
        
        Args:
            processed_feedback: List of validated feedback records
            
        Returns:
            True if batch added successfully
        """
        if not processed_feedback:
            return False
        
        try:
            # Convert feedback to training format
            features_list = []
            labels_list = []
            weights_list = []
            
            for feedback in processed_feedback:
                if not feedback.should_include_in_training:
                    continue
                
                # Get original features
                if feedback.feedback_records:
                    original_features = feedback.feedback_records[0].original_features
                    features_list.append(original_features)
                    
                    # Convert label to binary
                    label = 1 if feedback.final_label == FeedbackLabel.FRAUD else 0
                    labels_list.append(label)
                    
                    # Use feedback weight
                    weights_list.append(feedback.feedback_weight)
            
            if not features_list:
                self.logger.warning("No valid training samples in feedback batch")
                return False
            
            # Create DataFrame
            features_df = pd.DataFrame(features_list)
            labels_array = np.array(labels_list)
            weights_array = np.array(weights_list)
            
            # Create training batch
            batch = TrainingBatch(
                batch_id=f"feedback_batch_{int(time.time())}",
                features=features_df,
                labels=labels_array,
                sample_weights=weights_array,
                timestamp=datetime.now().isoformat(),
                source="feedback",
                fraud_rate=float(labels_array.mean()),
                total_samples=len(labels_array),
                quality_score=np.mean([fb.quality_score for fb in processed_feedback])
            )
            
            # Add to queue
            self.training_queue.append(batch)
            
            # Store in Redis for persistence
            self._store_training_batch(batch)
            
            self.logger.info(f"Added training batch: {len(labels_array)} samples, {batch.fraud_rate:.3f} fraud rate")
            
            # Trigger update if conditions met
            if self._should_trigger_update():
                self._schedule_model_update()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to add training batch: {e}")
            return False
    
    def _store_training_batch(self, batch: TrainingBatch) -> None:
        """Store training batch in Redis for persistence."""
        try:
            batch_key = f"training_batch:{batch.batch_id}"
            
            # Store batch metadata and features separately due to size limits
            batch_metadata = {
                'batch_id': batch.batch_id,
                'timestamp': batch.timestamp,
                'source': batch.source,
                'fraud_rate': batch.fraud_rate,
                'total_samples': batch.total_samples,
                'quality_score': batch.quality_score,
                'feature_columns': list(batch.features.columns)
            }
            
            self.redis_models.setex(f"{batch_key}:metadata", 86400, json.dumps(batch_metadata))
            
            # Store features and labels as compressed numpy arrays
            import base64
            import gzip
            
            features_bytes = gzip.compress(batch.features.to_numpy().tobytes())
            labels_bytes = gzip.compress(batch.labels.tobytes())
            
            self.redis_models.setex(f"{batch_key}:features", 86400, base64.b64encode(features_bytes).decode())
            self.redis_models.setex(f"{batch_key}:labels", 86400, base64.b64encode(labels_bytes).decode())
            
            if batch.sample_weights is not None:
                weights_bytes = gzip.compress(batch.sample_weights.tobytes())
                self.redis_models.setex(f"{batch_key}:weights", 86400, base64.b64encode(weights_bytes).decode())
        
        except Exception as e:
            self.logger.error(f"Failed to store training batch: {e}")
    
    def _should_trigger_update(self) -> bool:
        """Determine if model update should be triggered."""
        if self.model_state != ModelState.IDLE:
            return False
        
        total_samples = sum(batch.total_samples for batch in self.training_queue)
        
        if self.update_strategy == UpdateStrategy.CONTINUOUS:
            return total_samples >= self.min_batch_size
        
        elif self.update_strategy == UpdateStrategy.SCHEDULED:
            # Check if enough time has passed (implement based on schedule)
            return total_samples >= self.min_batch_size
        
        elif self.update_strategy == UpdateStrategy.DRIFT_TRIGGERED:
            # This would be triggered externally by drift detection
            return False
        
        elif self.update_strategy == UpdateStrategy.PERFORMANCE_BASED:
            # Check if performance has degraded
            return self._has_performance_degraded() and total_samples >= self.min_batch_size
        
        return False
    
    def _has_performance_degraded(self) -> bool:
        """Check if model performance has significantly degraded."""
        if len(self.performance_history) < 10:
            return False
        
        recent_performance = list(self.performance_history)[-5:]
        baseline_performance = list(self.performance_history)[:10]
        
        recent_auc = np.mean([perf['auc'] for perf in recent_performance])
        baseline_auc = np.mean([perf['auc'] for perf in baseline_performance])
        
        degradation = baseline_auc - recent_auc
        return degradation > 0.05  # 5% degradation threshold
    
    def _schedule_model_update(self) -> None:
        """Schedule a model update to run."""
        try:
            update_message = {
                'trigger': 'incremental_update',
                'timestamp': datetime.now().isoformat(),
                'batch_count': len(self.training_queue),
                'total_samples': sum(batch.total_samples for batch in self.training_queue),
                'update_strategy': self.update_strategy.value
            }
            
            self.producer.produce(
                self.config.model_updates_topic,
                key=f"incremental_update_{int(time.time())}",
                value=json.dumps(update_message)
            )
            self.producer.flush()
            
            self.logger.info("Scheduled incremental model update")
            
        except Exception as e:
            self.logger.error(f"Failed to schedule model update: {e}")
    
    def perform_incremental_update(self, force_update: bool = False) -> Optional[UpdateResult]:
        """
        Perform incremental model update with current training batches.
        
        Args:
            force_update: Force update even if conditions not met
            
        Returns:
            UpdateResult if update performed, None otherwise
        """
        if not force_update and not self._should_trigger_update():
            return None
        
        if self.model_state != ModelState.IDLE:
            self.logger.warning("Model update already in progress")
            return None
        
        self.model_state = ModelState.TRAINING
        update_id = f"incremental_update_{int(time.time())}"
        start_time = time.time()
        
        try:
            # Collect all training data
            combined_batch = self._combine_training_batches()
            if combined_batch is None:
                self.model_state = ModelState.IDLE
                return None
            
            # Backup current model
            model_backup = self._backup_current_model()
            old_performance = self._evaluate_current_model()
            
            # Perform incremental update
            new_model = self._update_model_incrementally(combined_batch)
            
            if new_model is None:
                self.model_state = ModelState.FAILED
                return self._create_failed_update_result(update_id, "Model update failed")
            
            self.model_state = ModelState.VALIDATING
            
            # Validate new model
            validation_result = self._validate_updated_model(new_model, combined_batch)
            
            if not validation_result['passed']:
                # Rollback to previous model
                self.current_model = model_backup
                self.model_state = ModelState.IDLE
                self.update_statistics["rollbacks"] += 1
                
                return self._create_failed_update_result(
                    update_id, 
                    "Validation failed", 
                    validation_result['errors']
                )
            
            # Update was successful
            self.current_model = new_model
            self.model_state = ModelState.IDLE
            
            # Save updated model
            self._save_updated_model()
            
            # Evaluate new performance
            new_performance = self._evaluate_current_model()
            
            # Clear training queue
            self.training_queue.clear()
            
            # Update statistics
            self.update_statistics["total_updates"] += 1
            self.update_statistics["successful_updates"] += 1
            self.update_statistics["total_samples_processed"] += combined_batch.total_samples
            
            # Create update result
            update_result = UpdateResult(
                update_id=update_id,
                success=True,
                timestamp=datetime.now().isoformat(),
                old_performance=old_performance,
                new_performance=new_performance,
                performance_change=self._calculate_performance_change(old_performance, new_performance),
                samples_used=combined_batch.total_samples,
                training_time_seconds=time.time() - start_time,
                model_size_change=self._calculate_model_size_change(model_backup, new_model),
                validation_passed=True,
                model_version=self.model_version,
                previous_version=self.model_version  # Would increment in production
            )
            
            self.logger.info(f"Incremental update completed successfully: {update_id}")
            return update_result
            
        except Exception as e:
            self.model_state = ModelState.FAILED
            self.update_statistics["failed_updates"] += 1
            self.logger.error(f"Incremental update failed: {e}")
            
            return self._create_failed_update_result(update_id, str(e))
    
    def _combine_training_batches(self) -> Optional[TrainingBatch]:
        """Combine all training batches into a single batch."""
        if not self.training_queue:
            return None
        
        try:
            all_features = []
            all_labels = []
            all_weights = []
            total_samples = 0
            quality_scores = []
            
            for batch in self.training_queue:
                all_features.append(batch.features)
                all_labels.append(batch.labels)
                
                if batch.sample_weights is not None:
                    all_weights.append(batch.sample_weights)
                
                total_samples += batch.total_samples
                quality_scores.append(batch.quality_score)
            
            # Combine all data
            combined_features = pd.concat(all_features, ignore_index=True)
            combined_labels = np.concatenate(all_labels)
            combined_weights = np.concatenate(all_weights) if all_weights else None
            
            # Create combined batch
            combined_batch = TrainingBatch(
                batch_id=f"combined_batch_{int(time.time())}",
                features=combined_features,
                labels=combined_labels,
                sample_weights=combined_weights,
                timestamp=datetime.now().isoformat(),
                source="combined_feedback",
                fraud_rate=float(combined_labels.mean()),
                total_samples=total_samples,
                quality_score=np.mean(quality_scores)
            )
            
            return combined_batch
            
        except Exception as e:
            self.logger.error(f"Failed to combine training batches: {e}")
            return None
    
    def _backup_current_model(self):
        """Create backup of current model."""
        if self.current_model is None:
            return None
        
        try:
            # Create deep copy for backup
            import copy
            return copy.deepcopy(self.current_model)
        except Exception as e:
            self.logger.error(f"Failed to backup model: {e}")
            return None
    
    def _update_model_incrementally(self, training_batch: TrainingBatch):
        """Perform the actual incremental model update."""
        if self.current_model is None:
            self.logger.error("No current model to update")
            return None
        
        try:
            # Prepare training data
            X_train = training_batch.features
            y_train = training_batch.labels
            sample_weights = training_batch.sample_weights
            
            # Handle different model types
            if hasattr(self.current_model, 'classes_'):
                # Scikit-learn style model
                if hasattr(self.current_model, 'partial_fit'):
                    # Models that support partial_fit
                    self.current_model.partial_fit(X_train, y_train, sample_weight=sample_weights)
                else:
                    # Models that don't support incremental learning
                    # We need to retrain with both old and new data
                    self.logger.warning("Model doesn't support incremental learning, falling back to retraining")
                    return self._retrain_model_with_memory(training_batch)
            
            elif hasattr(self.current_model, 'booster'):
                # LightGBM model
                return self._update_lightgbm_model(training_batch)
            
            elif hasattr(self.current_model, 'get_booster'):
                # XGBoost model
                return self._update_xgboost_model(training_batch)
            
            else:
                self.logger.error("Unsupported model type for incremental learning")
                return None
            
            return self.current_model
            
        except Exception as e:
            self.logger.error(f"Failed to update model incrementally: {e}")
            return None
    
    def _update_lightgbm_model(self, training_batch: TrainingBatch):
        """Update LightGBM model incrementally."""
        try:
            # LightGBM doesn't support true incremental learning
            # But we can continue training from the existing model
            
            X_train = training_batch.features
            y_train = training_batch.labels
            sample_weights = training_batch.sample_weights
            
            # Create LightGBM dataset
            train_data = lgb.Dataset(
                X_train, 
                label=y_train, 
                weight=sample_weights,
                free_raw_data=False
            )
            
            # Get current model parameters
            current_params = self.current_model.params.copy()
            current_params.update({
                'num_boost_round': 50,  # Additional rounds
                'init_model': self.current_model,  # Continue from existing model
                'verbose': -1
            })
            
            # Train additional boosting rounds
            updated_model = lgb.train(
                current_params,
                train_data,
                init_model=self.current_model,
                num_boost_round=50,
                valid_sets=[train_data],
                callbacks=[lgb.early_stopping(10), lgb.log_evaluation(0)]
            )
            
            return updated_model
            
        except Exception as e:
            self.logger.error(f"Failed to update LightGBM model: {e}")
            return None
    
    def _update_xgboost_model(self, training_batch: TrainingBatch):
        """Update XGBoost model incrementally."""
        try:
            X_train = training_batch.features
            y_train = training_batch.labels
            sample_weights = training_batch.sample_weights
            
            # Create DMatrix
            dtrain = xgb.DMatrix(X_train, label=y_train, weight=sample_weights)
            
            # Get current booster
            current_booster = self.current_model.get_booster()
            
            # Continue training
            updated_booster = xgb.train(
                params=self.current_model.get_params(),
                dtrain=dtrain,
                num_boost_round=50,
                xgb_model=current_booster,
                verbose_eval=False
            )
            
            # Create new model with updated booster
            updated_model = xgb.XGBClassifier()
            updated_model._Booster = updated_booster
            updated_model._le = self.current_model._le  # Preserve label encoder
            
            return updated_model
            
        except Exception as e:
            self.logger.error(f"Failed to update XGBoost model: {e}")
            return None
    
    def _retrain_model_with_memory(self, training_batch: TrainingBatch):
        """Retrain model with both historical and new data to prevent forgetting."""
        try:
            # This is a simplified implementation
            # In production, you'd want to maintain a memory buffer of historical data
            
            # For now, just use the new training batch
            X_train = training_batch.features
            y_train = training_batch.labels
            sample_weights = training_batch.sample_weights
            
            # Create new model with same parameters
            if hasattr(self.current_model, 'get_params'):
                new_model = type(self.current_model)(**self.current_model.get_params())
                new_model.fit(X_train, y_train, sample_weight=sample_weights)
                return new_model
            
            return None
            
        except Exception as e:
            self.logger.error(f"Failed to retrain model with memory: {e}")
            return None
    
    def _validate_updated_model(self, new_model, training_batch: TrainingBatch) -> Dict[str, Any]:
        """Validate the updated model before deployment."""
        validation_result = {
            'passed': True,
            'errors': [],
            'warnings': []
        }
        
        try:
            # 1. Basic functionality test
            X_test = training_batch.features[:min(100, len(training_batch.features))]
            
            try:
                predictions = new_model.predict_proba(X_test)[:, 1]
                
                # Check prediction range
                if not (0 <= predictions.min() <= predictions.max() <= 1):
                    validation_result['errors'].append("Predictions outside [0,1] range")
                    validation_result['passed'] = False
                
                # Check for NaN predictions
                if np.isnan(predictions).any():
                    validation_result['errors'].append("Model produces NaN predictions")
                    validation_result['passed'] = False
            
            except Exception as e:
                validation_result['errors'].append(f"Prediction failed: {str(e)}")
                validation_result['passed'] = False
            
            # 2. Performance validation
            if validation_result['passed'] and len(training_batch.labels) > 50:
                try:
                    predictions = new_model.predict_proba(training_batch.features)[:, 1]
                    auc_score = roc_auc_score(training_batch.labels, predictions)
                    
                    if auc_score < 0.6:  # Minimum acceptable AUC
                        validation_result['errors'].append(f"AUC too low: {auc_score:.3f}")
                        validation_result['passed'] = False
                    elif auc_score < 0.7:
                        validation_result['warnings'].append(f"Low AUC: {auc_score:.3f}")
                
                except Exception as e:
                    validation_result['warnings'].append(f"Performance validation failed: {str(e)}")
            
            # 3. Model comparison with previous version
            if validation_result['passed'] and self.current_model is not None:
                try:
                    old_predictions = self.current_model.predict_proba(X_test)[:, 1]
                    new_predictions = new_model.predict_proba(X_test)[:, 1]
                    
                    # Check if predictions are reasonable compared to old model
                    prediction_diff = np.abs(new_predictions - old_predictions).mean()
                    
                    if prediction_diff > 0.5:  # Predictions too different
                        validation_result['warnings'].append(f"Large prediction changes: {prediction_diff:.3f}")
                
                except Exception as e:
                    validation_result['warnings'].append(f"Model comparison failed: {str(e)}")
        
        except Exception as e:
            validation_result['errors'].append(f"Validation process failed: {str(e)}")
            validation_result['passed'] = False
        
        return validation_result
    
    def _evaluate_current_model(self) -> Dict[str, float]:
        """Evaluate current model performance."""
        if self.current_model is None:
            return {}
        
        try:
            # Use validation data if available
            if self.validation_data is not None:
                X_val, y_val = self.validation_data
                predictions = self.current_model.predict_proba(X_val)[:, 1]
                
                auc_score = roc_auc_score(y_val, predictions)
                ap_score = average_precision_score(y_val, predictions)
                
                # Binary predictions for precision/recall
                binary_preds = (predictions > 0.5).astype(int)
                precision = np.sum((binary_preds == 1) & (y_val == 1)) / max(1, np.sum(binary_preds == 1))
                recall = np.sum((binary_preds == 1) & (y_val == 1)) / max(1, np.sum(y_val == 1))
                f1 = 2 * precision * recall / max(1e-8, precision + recall)
                
                return {
                    'auc': float(auc_score),
                    'average_precision': float(ap_score),
                    'precision': float(precision),
                    'recall': float(recall),
                    'f1': float(f1)
                }
        
        except Exception as e:
            self.logger.error(f"Failed to evaluate model: {e}")
        
        return {'auc': 0.0, 'average_precision': 0.0, 'precision': 0.0, 'recall': 0.0, 'f1': 0.0}
    
    def _calculate_performance_change(self, old_perf: Dict[str, float], new_perf: Dict[str, float]) -> Dict[str, float]:
        """Calculate change in performance metrics."""
        changes = {}
        
        for metric in old_perf.keys():
            if metric in new_perf:
                changes[metric] = new_perf[metric] - old_perf[metric]
            else:
                changes[metric] = 0.0
        
        return changes
    
    def _calculate_model_size_change(self, old_model, new_model) -> int:
        """Calculate change in model size (bytes)."""
        try:
            import sys
            old_size = sys.getsizeof(pickle.dumps(old_model)) if old_model else 0
            new_size = sys.getsizeof(pickle.dumps(new_model)) if new_model else 0
            return new_size - old_size
        except Exception:
            return 0
    
    def _save_updated_model(self) -> None:
        """Save the updated model to storage."""
        try:
            # Save to Redis
            import base64
            
            model_bytes = pickle.dumps(self.current_model)
            model_b64 = base64.b64encode(model_bytes).decode()
            
            self.redis_models.set("current_fraud_model", model_b64)
            
            # Save metadata
            metadata = {
                'version': self.model_version,
                'update_timestamp': datetime.now().isoformat(),
                'model_type': type(self.current_model).__name__,
                'size_bytes': len(model_bytes)
            }
            
            self.redis_models.set("current_model_metadata", json.dumps(metadata))
            
            # Also save to filesystem as backup
            model_file = self.model_path / f"model_v{self.model_version}.pkl"
            with open(model_file, 'wb') as f:
                pickle.dump(self.current_model, f)
            
            self.logger.info(f"Saved updated model version {self.model_version}")
            
        except Exception as e:
            self.logger.error(f"Failed to save updated model: {e}")
    
    def _create_failed_update_result(self, update_id: str, error_message: str, 
                                   validation_errors: Optional[List[str]] = None) -> UpdateResult:
        """Create update result for failed update."""
        return UpdateResult(
            update_id=update_id,
            success=False,
            timestamp=datetime.now().isoformat(),
            old_performance={},
            new_performance={},
            performance_change={},
            samples_used=0,
            training_time_seconds=0.0,
            model_size_change=0,
            validation_passed=False,
            validation_errors=[error_message] + (validation_errors or []),
            model_version=self.model_version,
            previous_version=self.model_version
        )
    
    def set_validation_data(self, X_val: pd.DataFrame, y_val: np.ndarray) -> None:
        """Set validation data for model evaluation."""
        self.validation_data = (X_val, y_val)
        self.logger.info(f"Set validation data: {len(X_val)} samples")
    
    def get_update_statistics(self) -> Dict[str, Any]:
        """Get incremental learning statistics."""
        stats = self.update_statistics.copy()
        stats.update({
            'current_model_version': self.model_version,
            'model_state': self.model_state.value,
            'training_queue_size': len(self.training_queue),
            'total_queued_samples': sum(batch.total_samples for batch in self.training_queue),
            'update_strategy': self.update_strategy.value,
            'has_validation_data': self.validation_data is not None,
            'performance_history_size': len(self.performance_history)
        })
        return stats
    
    def set_update_strategy(self, strategy: UpdateStrategy) -> None:
        """Set the update strategy for incremental learning."""
        self.update_strategy = strategy
        self.logger.info(f"Update strategy set to: {strategy.value}")
    
    def clear_training_queue(self) -> int:
        """Clear the training queue and return number of batches cleared."""
        count = len(self.training_queue)
        self.training_queue.clear()
        self.logger.info(f"Cleared {count} training batches from queue")
        return count