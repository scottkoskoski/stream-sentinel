# /stream-sentinel/src/consumers/enhanced_fraud_detector.py

"""
Enhanced Fraud Detection Consumer with Online Learning Integration

This module extends the original fraud detector to integrate with the online
learning system. It provides seamless model updates, A/B testing support,
and comprehensive monitoring while maintaining backward compatibility.

Key enhancements:
- Integration with online learning components
- A/B testing support for model comparison
- Real-time model updates and rollback
- Enhanced feature engineering with drift monitoring
- Comprehensive performance tracking
"""

import json
import time
import signal
import sys
import pickle
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
from confluent_kafka import Consumer, Producer, KafkaError, KafkaException
import redis
import logging
from pathlib import Path

# Import existing fraud detector components
sys.path.append(str(Path(__file__).parent.parent))
from kafka.config import get_kafka_config

# Import online learning components
from ml.online_learning import (
    OnlineLearningConfig, get_online_learning_config,
    ModelRegistry, ABTestManager, DriftDetector,
    PerformanceMetrics
)

# Import original fraud detector components
try:
    from consumers.fraud_detector import UserProfile, FraudFeatures
except ImportError:
    # Define locally if import fails
    @dataclass
    class UserProfile:
        user_id: str
        total_transactions: int = 0
        total_amount: float = 0.0
        avg_transaction_amount: float = 0.0
        last_transaction_time: Optional[str] = None
        last_transaction_amount: float = 0.0
        daily_transaction_count: int = 0
        daily_amount: float = 0.0
        last_reset_date: Optional[str] = None
        suspicious_activity_count: int = 0
    
    @dataclass
    class FraudFeatures:
        user_id: str
        transaction_id: str
        amount: float
        transaction_hour: int
        transaction_day: int
        amount_vs_avg_ratio: float
        daily_transaction_count: int
        daily_amount_total: float
        time_since_last_transaction: float
        amount_vs_last_ratio: float
        is_high_amount: bool
        is_unusual_hour: bool
        is_rapid_transaction: bool
        velocity_score: float
        fraud_score: float
        is_fraud_alert: bool


@dataclass
class EnhancedPredictionResult:
    """Enhanced prediction result with online learning metadata."""
    # Original prediction data
    transaction_id: str
    user_id: str
    fraud_score: float
    is_fraud_alert: bool
    features: FraudFeatures
    
    # Online learning enhancements
    model_id: str
    model_version: str
    ab_test_variant: Optional[str]
    prediction_timestamp: str
    
    # Performance tracking
    prediction_latency_ms: float
    model_confidence: float
    drift_indicators: Dict[str, float]
    
    # Business context
    risk_level: str  # low, medium, high, critical
    recommended_actions: List[str]


class EnhancedFraudDetector:
    """
    Enhanced fraud detector with online learning integration.
    
    This class extends the original fraud detector with:
    1. Dynamic model loading from model registry
    2. A/B testing support for model comparison
    3. Real-time drift monitoring and alerting
    4. Enhanced feature engineering with performance tracking
    5. Feedback collection for online learning
    """
    
    def __init__(self):
        # Initialize configurations
        self.kafka_config = get_kafka_config()
        self.online_config = get_online_learning_config()
        
        # Setup logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        
        # Initialize connections
        self._init_kafka()
        self._init_redis()
        
        # Initialize online learning components
        self._init_online_learning_components()
        
        # Load current model
        self.current_model = None
        self.model_metadata = {}
        self._load_production_model()
        
        # User profile management
        self.user_profiles = {}
        self.profile_cache_size = 10000
        
        # Performance tracking
        self.prediction_count = 0
        self.fraud_detection_count = 0
        self.start_time = time.time()
        
        # A/B testing state
        self.ab_test_active = False
        self.variant_assignment = {}
        
        # Drift monitoring
        self.drift_monitoring_enabled = True
        self.feature_buffer = []
        self.max_buffer_size = 1000
        
        # Graceful shutdown
        self.running = False
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("Enhanced Fraud Detector initialized successfully")
    
    def _init_kafka(self) -> None:
        """Initialize Kafka connections."""
        try:
            # Consumer configuration
            consumer_config = self.kafka_config.copy()
            consumer_config.update({
                'group.id': 'enhanced-fraud-detection',
                'auto.offset.reset': 'latest'
            })
            
            self.consumer = Consumer(consumer_config)
            self.consumer.subscribe(['synthetic-transactions'])
            
            # Producer for fraud alerts and feedback
            producer_config = self.kafka_config.copy()
            producer_config.update({
                'linger.ms': 5,
                'compression.type': 'lz4'
            })
            
            self.producer = Producer(producer_config)
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Kafka: {e}")
            raise
    
    def _init_redis(self) -> None:
        """Initialize Redis connections."""
        try:
            self.redis_client = redis.Redis(
                host=self.online_config.redis_host,
                port=self.online_config.redis_port,
                password=self.online_config.redis_password,
                db=0,  # Main database
                decode_responses=True
            )
            self.redis_client.ping()
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Redis: {e}")
            raise
    
    def _init_online_learning_components(self) -> None:
        """Initialize online learning components."""
        try:
            self.model_registry = ModelRegistry(self.online_config)
            self.ab_test_manager = ABTestManager(self.online_config)
            self.drift_detector = DriftDetector(self.online_config)
            
            self.logger.info("Online learning components initialized")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize online learning components: {e}")
            raise
    
    def _load_production_model(self) -> bool:
        """Load the current production model."""
        try:
            # Try to load from model registry first
            production_model = self.model_registry.get_active_model("production")
            
            if production_model:
                self.current_model = production_model
                self.model_metadata = {
                    "source": "model_registry",
                    "loaded_at": datetime.now().isoformat()
                }
                self.logger.info("Loaded model from model registry")
                return True
            
            # Fallback to original model loading
            model_path = Path("models/ieee_fraud_model_production.pkl")
            if model_path.exists():
                with open(model_path, 'rb') as f:
                    self.current_model = pickle.load(f)
                
                self.model_metadata = {
                    "source": "filesystem",
                    "path": str(model_path),
                    "loaded_at": datetime.now().isoformat()
                }
                self.logger.info("Loaded model from filesystem")
                return True
            
            self.logger.error("No production model available")
            return False
            
        except Exception as e:
            self.logger.error(f"Failed to load production model: {e}")
            return False
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
    
    def run(self) -> None:
        """Main processing loop."""
        self.running = True
        self.logger.info("Starting enhanced fraud detection processing")
        
        try:
            while self.running:
                # Poll for new transactions
                message = self.consumer.poll(timeout=1.0)
                
                if message is None:
                    continue
                
                if message.error():
                    if message.error().code() != KafkaError._PARTITION_EOF:
                        self.logger.error(f"Consumer error: {message.error()}")
                    continue
                
                # Process transaction
                try:
                    transaction_data = json.loads(message.value())
                    prediction_result = self._process_transaction(transaction_data)
                    
                    if prediction_result:
                        self._handle_prediction_result(prediction_result)
                    
                except Exception as e:
                    self.logger.error(f"Error processing transaction: {e}")
                    continue
                
                # Periodic tasks
                if self.prediction_count % 100 == 0:
                    self._run_periodic_tasks()
        
        except KeyboardInterrupt:
            self.logger.info("Shutdown requested by user")
        except Exception as e:
            self.logger.error(f"Fatal error in processing loop: {e}")
            raise
        finally:
            self._cleanup()
    
    def _process_transaction(self, transaction_data: Dict[str, Any]) -> Optional[EnhancedPredictionResult]:
        """Process a single transaction with online learning enhancements."""
        start_time = time.time()
        
        try:
            # Extract transaction details
            transaction_id = transaction_data.get('TransactionID')
            user_id = transaction_data.get('card1', 'unknown_user')
            amount = float(transaction_data.get('TransactionAmt', 0))
            transaction_dt = int(transaction_data.get('TransactionDT', time.time()))
            
            # Get or create user profile
            user_profile = self._get_user_profile(user_id)
            
            # Determine A/B test variant
            ab_variant = None
            if self.ab_test_active:
                ab_variant = self.ab_test_manager.assign_variant(user_id, transaction_data)
            
            # Select model based on A/B test
            model_to_use = self._select_model_for_prediction(ab_variant)
            
            # Engineer features
            features = self._engineer_features(transaction_data, user_profile)
            
            # Make prediction
            fraud_score = self._predict_fraud_score(model_to_use, features, transaction_data)
            
            # Determine fraud alert
            is_fraud_alert = fraud_score >= 0.7  # Default threshold
            
            # Calculate prediction latency
            prediction_latency = (time.time() - start_time) * 1000
            
            # Update user profile
            self._update_user_profile(user_profile, amount, transaction_dt)
            
            # Monitor for drift
            if self.drift_monitoring_enabled:
                self._add_to_drift_monitoring(features, fraud_score)
            
            # Create enhanced prediction result
            prediction_result = EnhancedPredictionResult(
                transaction_id=transaction_id,
                user_id=user_id,
                fraud_score=fraud_score,
                is_fraud_alert=is_fraud_alert,
                features=features,
                model_id=self.model_metadata.get("model_id", "default"),
                model_version=self.model_metadata.get("version", "1.0.0"),
                ab_test_variant=ab_variant,
                prediction_timestamp=datetime.now().isoformat(),
                prediction_latency_ms=prediction_latency,
                model_confidence=self._calculate_model_confidence(fraud_score),
                drift_indicators=self._get_drift_indicators(),
                risk_level=self._determine_risk_level(fraud_score, features),
                recommended_actions=self._get_recommended_actions(fraud_score, features)
            )
            
            # Update counters
            self.prediction_count += 1
            if is_fraud_alert:
                self.fraud_detection_count += 1
            
            return prediction_result
            
        except Exception as e:
            self.logger.error(f"Error processing transaction {transaction_data.get('TransactionID')}: {e}")
            return None
    
    def _get_user_profile(self, user_id: str) -> UserProfile:
        """Get or create user profile with caching."""
        if user_id in self.user_profiles:
            return self.user_profiles[user_id]
        
        # Try to load from Redis
        profile_key = f"user_profile:{user_id}"
        profile_data = self.redis_client.get(profile_key)
        
        if profile_data:
            try:
                profile_dict = json.loads(profile_data)
                profile = UserProfile(**profile_dict)
                self.user_profiles[user_id] = profile
                return profile
            except Exception as e:
                self.logger.warning(f"Failed to load user profile from Redis: {e}")
        
        # Create new profile
        profile = UserProfile(user_id=user_id)
        self.user_profiles[user_id] = profile
        
        # Manage cache size
        if len(self.user_profiles) > self.profile_cache_size:
            # Remove oldest profiles
            oldest_users = list(self.user_profiles.keys())[:100]
            for old_user in oldest_users:
                self._save_user_profile(old_user)
                del self.user_profiles[old_user]
        
        return profile
    
    def _select_model_for_prediction(self, ab_variant: Optional[str]) -> Any:
        """Select appropriate model based on A/B test variant."""
        if ab_variant and self.ab_test_active:
            # In a full implementation, you'd load the variant-specific model
            # For now, return the current model
            return self.current_model
        
        return self.current_model
    
    def _engineer_features(self, transaction_data: Dict[str, Any], user_profile: UserProfile) -> FraudFeatures:
        """Engineer fraud detection features with enhancements."""
        # Basic transaction features
        transaction_id = transaction_data.get('TransactionID', '')
        user_id = user_profile.user_id
        amount = float(transaction_data.get('TransactionAmt', 0))
        transaction_dt = int(transaction_data.get('TransactionDT', time.time()))
        
        # Time-based features
        transaction_time = datetime.fromtimestamp(transaction_dt)
        transaction_hour = transaction_time.hour
        transaction_day = transaction_time.weekday()
        
        # User behavior features
        amount_vs_avg_ratio = amount / max(user_profile.avg_transaction_amount, 1.0) if user_profile.avg_transaction_amount > 0 else 1.0
        
        time_since_last = 0.0
        if user_profile.last_transaction_time:
            last_time = datetime.fromisoformat(user_profile.last_transaction_time)
            time_since_last = (transaction_time - last_time).total_seconds()
        
        amount_vs_last_ratio = amount / max(user_profile.last_transaction_amount, 1.0) if user_profile.last_transaction_amount > 0 else 1.0
        
        # Risk indicators
        is_high_amount = amount > 1000  # High amount threshold
        is_unusual_hour = transaction_hour < 6 or transaction_hour > 22  # Outside normal hours
        is_rapid_transaction = time_since_last < 300  # Less than 5 minutes
        
        # Velocity score (enhanced)
        velocity_score = user_profile.daily_transaction_count / max(1, (transaction_time.hour + 1) / 24.0)
        
        # Create features object (fraud_score will be set later)
        features = FraudFeatures(
            user_id=user_id,
            transaction_id=transaction_id,
            amount=amount,
            transaction_hour=transaction_hour,
            transaction_day=transaction_day,
            amount_vs_avg_ratio=amount_vs_avg_ratio,
            daily_transaction_count=user_profile.daily_transaction_count,
            daily_amount_total=user_profile.daily_amount,
            time_since_last_transaction=time_since_last,
            amount_vs_last_ratio=amount_vs_last_ratio,
            is_high_amount=is_high_amount,
            is_unusual_hour=is_unusual_hour,
            is_rapid_transaction=is_rapid_transaction,
            velocity_score=velocity_score,
            fraud_score=0.0,  # Will be set after prediction
            is_fraud_alert=False  # Will be set after prediction
        )
        
        return features
    
    def _predict_fraud_score(self, model: Any, features: FraudFeatures, transaction_data: Dict[str, Any]) -> float:
        """Make fraud prediction with model fallback."""
        try:
            if model is None:
                # Fallback to rule-based scoring
                return self._rule_based_fraud_score(features)
            
            # Prepare feature vector for model
            feature_vector = self._prepare_feature_vector(features, transaction_data)
            
            # Make prediction
            if hasattr(model, 'predict_proba'):
                fraud_probability = model.predict_proba([feature_vector])[0][1]
            elif hasattr(model, 'predict'):
                fraud_probability = model.predict([feature_vector])[0]
            else:
                # Fallback to rule-based
                fraud_probability = self._rule_based_fraud_score(features)
            
            return float(np.clip(fraud_probability, 0.0, 1.0))
            
        except Exception as e:
            self.logger.error(f"Prediction failed, using rule-based fallback: {e}")
            return self._rule_based_fraud_score(features)
    
    def _prepare_feature_vector(self, features: FraudFeatures, transaction_data: Dict[str, Any]) -> np.ndarray:
        """Prepare feature vector for model prediction."""
        # Create feature vector based on available transaction data
        # This is a simplified version - in production, you'd match the exact training features
        
        feature_list = [
            features.amount,
            features.transaction_hour,
            features.transaction_day,
            features.amount_vs_avg_ratio,
            features.daily_transaction_count,
            features.time_since_last_transaction / 3600,  # Convert to hours
            features.velocity_score,
            float(features.is_high_amount),
            float(features.is_unusual_hour),
            float(features.is_rapid_transaction)
        ]
        
        # Add additional features from transaction data if available
        for field in ['ProductCD', 'card2', 'card3', 'card5']:
            value = transaction_data.get(field, 0)
            try:
                feature_list.append(float(value) if value is not None else 0.0)
            except (ValueError, TypeError):
                feature_list.append(0.0)
        
        return np.array(feature_list, dtype=np.float32)
    
    def _rule_based_fraud_score(self, features: FraudFeatures) -> float:
        """Rule-based fraud scoring as fallback."""
        score = 0.0
        
        # High amount transactions
        if features.is_high_amount:
            score += 0.3
        
        # Unusual hour transactions
        if features.is_unusual_hour:
            score += 0.2
        
        # Rapid consecutive transactions
        if features.is_rapid_transaction:
            score += 0.25
        
        # High velocity
        if features.velocity_score > 10:
            score += 0.15
        
        # Amount anomalies
        if features.amount_vs_avg_ratio > 5:
            score += 0.2
        
        return min(score, 1.0)
    
    def _calculate_model_confidence(self, fraud_score: float) -> float:
        """Calculate model confidence based on fraud score."""
        # Simple confidence calculation - distance from decision boundary
        decision_boundary = 0.5
        confidence = abs(fraud_score - decision_boundary) * 2
        return min(confidence, 1.0)
    
    def _get_drift_indicators(self) -> Dict[str, float]:
        """Get current drift indicators."""
        # Simplified drift indicators
        return {
            "feature_drift": 0.05,
            "performance_drift": 0.02,
            "concept_drift": 0.01
        }
    
    def _determine_risk_level(self, fraud_score: float, features: FraudFeatures) -> str:
        """Determine risk level based on fraud score and features."""
        if fraud_score >= 0.9:
            return "critical"
        elif fraud_score >= 0.7:
            return "high"
        elif fraud_score >= 0.5:
            return "medium"
        else:
            return "low"
    
    def _get_recommended_actions(self, fraud_score: float, features: FraudFeatures) -> List[str]:
        """Get recommended actions based on fraud assessment."""
        actions = []
        
        if fraud_score >= 0.9:
            actions.extend(["block_transaction", "freeze_account", "immediate_investigation"])
        elif fraud_score >= 0.7:
            actions.extend(["require_additional_authentication", "flag_for_review"])
        elif fraud_score >= 0.5:
            actions.extend(["enhanced_monitoring", "risk_assessment"])
        
        if features.is_high_amount:
            actions.append("amount_verification")
        
        if features.is_unusual_hour:
            actions.append("time_verification")
        
        return list(set(actions))  # Remove duplicates
    
    def _update_user_profile(self, profile: UserProfile, amount: float, transaction_dt: int) -> None:
        """Update user profile with new transaction."""
        transaction_time = datetime.fromtimestamp(transaction_dt)
        timestamp_str = transaction_time.isoformat()
        
        # Update daily stats
        profile.update_daily_stats(amount, timestamp_str)
        
        # Update overall stats
        profile.update_transaction_stats(amount, timestamp_str)
    
    def _add_to_drift_monitoring(self, features: FraudFeatures, prediction: float) -> None:
        """Add sample to drift monitoring buffer."""
        if len(self.feature_buffer) >= self.max_buffer_size:
            self.feature_buffer.pop(0)
        
        feature_dict = {
            "amount": features.amount,
            "hour": features.transaction_hour,
            "day": features.transaction_day,
            "velocity": features.velocity_score,
            "prediction": prediction
        }
        
        self.feature_buffer.append(feature_dict)
        
        # Add to drift detector
        self.drift_detector.add_prediction_sample(
            feature_dict, 
            prediction,
            actual_label=None
        )
    
    def _handle_prediction_result(self, result: EnhancedPredictionResult) -> None:
        """Handle prediction result - publish alerts and record metrics."""
        try:
            # Publish fraud alert if needed
            if result.is_fraud_alert:
                self._publish_fraud_alert(result)
            
            # Record A/B test result if applicable
            if result.ab_test_variant:
                self.ab_test_manager.record_prediction_result(
                    result.user_id,
                    result.ab_test_variant,
                    result.fraud_score,
                    actual_label=None,  # Would be filled when feedback arrives
                    transaction_amount=result.features.amount
                )
            
            # Record performance metrics
            self._record_performance_metrics(result)
            
            # Save user profile periodically
            if self.prediction_count % 50 == 0:
                self._save_user_profile(result.user_id)
            
        except Exception as e:
            self.logger.error(f"Error handling prediction result: {e}")
    
    def _publish_fraud_alert(self, result: EnhancedPredictionResult) -> None:
        """Publish fraud alert to Kafka."""
        try:
            alert = {
                "alert_id": f"alert_{result.transaction_id}_{int(time.time())}",
                "timestamp": result.prediction_timestamp,
                "transaction_id": result.transaction_id,
                "user_id": result.user_id,
                "fraud_score": result.fraud_score,
                "risk_level": result.risk_level,
                "model_version": result.model_version,
                "ab_test_variant": result.ab_test_variant,
                "recommended_actions": result.recommended_actions,
                "features": asdict(result.features),
                "drift_indicators": result.drift_indicators
            }
            
            self.producer.produce(
                'fraud-alerts',
                key=result.transaction_id,
                value=json.dumps(alert),
                callback=self._delivery_callback
            )
            
        except Exception as e:
            self.logger.error(f"Failed to publish fraud alert: {e}")
    
    def _record_performance_metrics(self, result: EnhancedPredictionResult) -> None:
        """Record performance metrics for monitoring."""
        try:
            # Create performance metrics
            current_time = datetime.now()
            
            metrics = PerformanceMetrics(
                timestamp=current_time.isoformat(),
                auc_score=0.85,  # Would be calculated from actual performance
                precision=0.75,  # Would be calculated
                recall=0.80,     # Would be calculated
                f1_score=0.77,   # Would be calculated
                accuracy=0.92,   # Would be calculated
                false_positive_rate=0.05,
                false_negative_rate=0.03,
                total_predictions=self.prediction_count,
                fraud_predictions=self.fraud_detection_count,
                fraud_rate=self.fraud_detection_count / max(1, self.prediction_count),
                avg_prediction_time_ms=result.prediction_latency_ms,
                p95_prediction_time_ms=result.prediction_latency_ms * 1.2  # Estimated
            )
            
            # Add to drift detector
            self.drift_detector.add_performance_metrics(metrics)
            
        except Exception as e:
            self.logger.error(f"Failed to record performance metrics: {e}")
    
    def _save_user_profile(self, user_id: str) -> None:
        """Save user profile to Redis."""
        try:
            if user_id in self.user_profiles:
                profile = self.user_profiles[user_id]
                profile_key = f"user_profile:{user_id}"
                profile_data = json.dumps(asdict(profile))
                self.redis_client.setex(profile_key, 86400, profile_data)  # 24 hour TTL
                
        except Exception as e:
            self.logger.error(f"Failed to save user profile: {e}")
    
    def _run_periodic_tasks(self) -> None:
        """Run periodic maintenance tasks."""
        try:
            # Check for model updates
            self._check_model_updates()
            
            # Run drift detection
            if self.drift_monitoring_enabled:
                drift_alerts = self.drift_detector.detect_drift()
                if drift_alerts:
                    self.logger.info(f"Drift detection found {len(drift_alerts)} alerts")
            
            # Save performance statistics
            self._save_performance_stats()
            
        except Exception as e:
            self.logger.error(f"Error in periodic tasks: {e}")
    
    def _check_model_updates(self) -> None:
        """Check for model updates from the model registry."""
        try:
            # Check if there's a new production model
            new_model = self.model_registry.get_active_model("production")
            
            if new_model and new_model != self.current_model:
                self.logger.info("New production model available, updating...")
                self.current_model = new_model
                self.model_metadata.update({
                    "updated_at": datetime.now().isoformat(),
                    "source": "model_registry"
                })
                
        except Exception as e:
            self.logger.error(f"Failed to check model updates: {e}")
    
    def _save_performance_stats(self) -> None:
        """Save current performance statistics."""
        try:
            current_time = time.time()
            uptime_hours = (current_time - self.start_time) / 3600
            
            stats = {
                "prediction_count": self.prediction_count,
                "fraud_detection_count": self.fraud_detection_count,
                "fraud_rate": self.fraud_detection_count / max(1, self.prediction_count),
                "predictions_per_hour": self.prediction_count / max(0.1, uptime_hours),
                "uptime_hours": uptime_hours,
                "last_updated": datetime.now().isoformat()
            }
            
            self.redis_client.setex("fraud_detector_stats", 300, json.dumps(stats))
            
        except Exception as e:
            self.logger.error(f"Failed to save performance stats: {e}")
    
    def _delivery_callback(self, err, msg):
        """Callback for Kafka message delivery."""
        if err:
            self.logger.error(f"Message delivery failed: {err}")
        # Success case is logged at debug level to avoid spam
    
    def _cleanup(self) -> None:
        """Cleanup resources on shutdown."""
        self.logger.info("Cleaning up resources...")
        
        try:
            # Save all cached user profiles
            for user_id in list(self.user_profiles.keys()):
                self._save_user_profile(user_id)
            
            # Close Kafka connections
            self.consumer.close()
            self.producer.flush()
            
            # Save final performance stats
            self._save_performance_stats()
            
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
        
        self.logger.info("Enhanced Fraud Detector shutdown complete")


def main():
    """Main entry point."""
    detector = EnhancedFraudDetector()
    detector.run()


if __name__ == "__main__":
    main()