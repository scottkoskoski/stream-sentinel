# /stream-sentinel/src/consumers/fraud_detector.py

"""
Real-Time Fraud Detection Consumer for Stream-Sentinel

This module implements the core fraud detection consumer that processes
transaction streams in real-time, performs feature engineering, and
publishes fraud alerts. It demonstrates advanced stream processing patterns
with state management using Redis and Kafka.

Key distributed systems concepts:
- Real-time stream processing with Kafka consumers
- Stateful processing with Redis-backed state management
- Feature engineering pipeline for ML-ready data
- Alert publishing with configurable fraud thresholds
- Graceful error handling and recovery mechanisms
"""

import json
import time
import signal
import sys
import pickle
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from confluent_kafka import Consumer, Producer, KafkaError, KafkaException
import redis
import logging
from pathlib import Path

# Import our configuration system
sys.path.append(str(Path(__file__).parent.parent))
from kafka.config import get_kafka_config


@dataclass
class UserProfile:
    """User profile for fraud detection state management."""
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
    
    def update_daily_stats(self, amount: float, timestamp: str) -> None:
        """Update daily statistics, resetting if new day."""
        current_date = datetime.fromisoformat(timestamp).date().isoformat()
        
        if self.last_reset_date != current_date:
            self.daily_transaction_count = 0
            self.daily_amount = 0.0
            self.last_reset_date = current_date
            
        self.daily_transaction_count += 1
        self.daily_amount += amount
        
    def update_transaction_stats(self, amount: float, timestamp: str) -> None:
        """Update overall transaction statistics."""
        self.total_transactions += 1
        self.total_amount += amount
        self.avg_transaction_amount = self.total_amount / self.total_transactions
        self.last_transaction_time = timestamp
        self.last_transaction_amount = amount


@dataclass 
class FraudFeatures:
    """Engineered features for fraud detection."""
    user_id: str
    transaction_id: str
    
    # Basic transaction features
    amount: float
    transaction_hour: int
    transaction_day: int
    
    # User behavior features
    amount_vs_avg_ratio: float
    daily_transaction_count: int
    daily_amount_total: float
    time_since_last_transaction: float  # seconds
    amount_vs_last_ratio: float
    
    # Risk indicators
    is_high_amount: bool
    is_unusual_hour: bool
    is_rapid_transaction: bool
    velocity_score: float
    
    # Fraud score
    fraud_score: float
    is_fraud_alert: bool
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


class FraudDetector:
    """
    Real-time fraud detection consumer with Redis state management.
    
    This consumer processes transaction streams, maintains user profiles in Redis,
    performs feature engineering, and publishes fraud alerts for suspicious
    transactions.
    """
    
    def __init__(self, 
                 consumer_group: str = "fraud-detection-group",
                 fraud_threshold: float = 0.7,
                 use_ml_model: bool = True,
                 model_path: str = "models/ieee_fraud_model_production.pkl"):
        """
        Initialize fraud detection consumer.
        
        Args:
            consumer_group: Kafka consumer group for parallel processing
            fraud_threshold: Fraud score threshold for alert generation
            use_ml_model: Whether to use ML model or rule-based scoring
            model_path: Path to the trained ML model
        """
        # Initialize Kafka configuration
        self.kafka_config = get_kafka_config()
        self.logger = self._setup_logging()
        self.fraud_threshold = fraud_threshold
        self.consumer_group = consumer_group
        self.use_ml_model = use_ml_model
        
        # Load ML model if enabled
        self.ml_model = None
        self.model_features = None
        if use_ml_model:
            self._load_ml_model(model_path)
        
        # Topics
        self.input_topic = "synthetic-transactions"
        self.output_topic = "fraud-alerts"
        
        # Initialize Kafka consumer and producer
        self.consumer = self._create_consumer()
        self.producer = self._create_producer()
        
        # Initialize Redis for state management
        self.redis_client = self._create_redis_client()
        
        # Processing statistics
        self.processed_count = 0
        self.fraud_alerts_count = 0
        self.start_time = time.time()
        
        # Graceful shutdown
        self.running = True
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info(
            f"FraudDetector initialized - group: {consumer_group}, "
            f"threshold: {fraud_threshold}"
        )
    
    def _setup_logging(self) -> logging.Logger:
        """Setup logging for fraud detection operations."""
        logger = logging.getLogger("stream_sentinel.fraud_detector")
        
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)  # Enable debug logging
            
        return logger
    
    def _create_consumer(self) -> Consumer:
        """Create Kafka consumer for transaction processing."""
        consumer_config = self.kafka_config.get_consumer_config(
            self.consumer_group, "fraud_detector"
        )
        consumer = Consumer(consumer_config)
        
        # Subscribe to transactions topic
        consumer.subscribe([self.input_topic])
        self.logger.info(f"Consumer subscribed to {self.input_topic}")
        
        return consumer
    
    def _create_producer(self) -> Producer:
        """Create Kafka producer for fraud alerts."""
        producer_config = self.kafka_config.get_producer_config("transaction")
        producer = Producer(producer_config)
        
        self.logger.info("Producer created for fraud alerts")
        return producer
    
    def _create_redis_client(self) -> redis.Redis:
        """Create Redis client for state management."""
        try:
            client = redis.Redis(
                host='localhost',
                port=6379,
                db=0,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            
            # Test connection
            client.ping()
            self.logger.info("Redis client connected successfully")
            return client
            
        except redis.ConnectionError as e:
            self.logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    def _load_ml_model(self, model_path: str) -> None:
        """Load the trained ML model for fraud detection."""
        try:
            # Load the pickled model
            with open(model_path, 'rb') as f:
                model_data = pickle.load(f)
                
            # Extract the actual model and preprocessing components
            if isinstance(model_data, dict):
                self.ml_model = model_data.get('model')
                self.scaler = model_data.get('scaler')
                self.model_features = model_data.get('feature_names', [])
                self.logger.info(f"Loaded model components: {list(model_data.keys())}")
            else:
                # Fallback for simple model pickle
                self.ml_model = model_data
            
            # Load model metadata
            metadata_path = model_path.replace('.pkl', '_metadata.json')
            if not Path(metadata_path).exists():
                # Try alternative path (metadata might be in same directory)
                metadata_path = 'models/ieee_fraud_model_metadata.json'
            
            if Path(metadata_path).exists():
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)
                    # Only use metadata features if not already loaded from pickle
                    if not self.model_features:
                        self.model_features = metadata.get('feature_names', [])
                    model_metrics = metadata.get('model_metrics', {})
                    self.logger.info(f"Loaded ML model: {metadata.get('model_type', 'unknown')}")
                    self.logger.info(f"Model AUC: {model_metrics.get('val_auc', 'unknown'):.4f}")
            else:
                self.logger.warning("Model metadata not found, using pickle feature names")
                
            self.logger.info(f"Expected features: {len(self.model_features) if self.model_features else 0}")
                
        except Exception as e:
            self.logger.error(f"Failed to load ML model from {model_path}: {e}")
            self.logger.info("Falling back to rule-based fraud detection")
            self.use_ml_model = False
    
    def _signal_handler(self, signum: int, frame) -> None:
        """Handle graceful shutdown signals."""
        self.logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.running = False
    
    def get_user_profile(self, user_id: str) -> UserProfile:
        """
        Retrieve or create user profile from Redis.
        
        Args:
            user_id: User identifier
            
        Returns:
            UserProfile object with current state
        """
        try:
            profile_data = self.redis_client.hgetall(f"user_profile:{user_id}")
            
            if profile_data:
                # Convert Redis strings back to appropriate types
                return UserProfile(
                    user_id=profile_data['user_id'],
                    total_transactions=int(profile_data.get('total_transactions', 0)),
                    total_amount=float(profile_data.get('total_amount', 0.0)),
                    avg_transaction_amount=float(profile_data.get('avg_transaction_amount', 0.0)),
                    last_transaction_time=profile_data.get('last_transaction_time'),
                    last_transaction_amount=float(profile_data.get('last_transaction_amount', 0.0)),
                    daily_transaction_count=int(profile_data.get('daily_transaction_count', 0)),
                    daily_amount=float(profile_data.get('daily_amount', 0.0)),
                    last_reset_date=profile_data.get('last_reset_date'),
                    suspicious_activity_count=int(profile_data.get('suspicious_activity_count', 0))
                )
            else:
                # Create new profile for first-time user
                return UserProfile(user_id=user_id)
                
        except Exception as e:
            self.logger.error(f"Error retrieving user profile for {user_id}: {e}")
            return UserProfile(user_id=user_id)
    
    def save_user_profile(self, profile: UserProfile) -> None:
        """
        Save user profile to Redis.
        
        Args:
            profile: UserProfile to save
        """
        try:
            profile_dict = asdict(profile)
            # Remove None values for cleaner Redis storage
            profile_dict = {k: v for k, v in profile_dict.items() if v is not None}
            
            self.redis_client.hset(
                f"user_profile:{profile.user_id}",
                mapping=profile_dict
            )
            
            # Set TTL for user profiles (30 days)
            self.redis_client.expire(f"user_profile:{profile.user_id}", 2592000)
            
        except Exception as e:
            self.logger.error(f"Error saving user profile for {profile.user_id}: {e}")
    
    def extract_features(self, transaction: Dict[str, Any], 
                        user_profile: UserProfile) -> FraudFeatures:
        """
        Extract fraud detection features from transaction and user state.
        
        Args:
            transaction: Raw transaction data
            user_profile: Current user profile state
            
        Returns:
            FraudFeatures object with engineered features
        """
        # Parse transaction data
        amount = float(transaction['transaction_amt'])
        timestamp = transaction['generated_timestamp']  # Use generated timestamp instead
        user_id = str(transaction['card1'])  # Using card1 as user identifier, convert to string
        transaction_id = transaction.get('transaction_id', 'unknown')
        
        # Parse timestamp for temporal features
        dt = datetime.fromisoformat(timestamp)
        transaction_hour = dt.hour
        transaction_day = dt.weekday()
        
        # Calculate behavioral features
        amount_vs_avg_ratio = (
            amount / user_profile.avg_transaction_amount 
            if user_profile.avg_transaction_amount > 0 else 1.0
        )
        
        # Time since last transaction (in seconds)
        time_since_last = 0.0
        if user_profile.last_transaction_time:
            last_dt = datetime.fromisoformat(user_profile.last_transaction_time)
            time_since_last = (dt - last_dt).total_seconds()
        
        # Amount comparison with last transaction
        amount_vs_last_ratio = (
            amount / user_profile.last_transaction_amount
            if user_profile.last_transaction_amount > 0 else 1.0
        )
        
        # Risk indicators
        is_high_amount = amount > 1000.0  # High amount threshold
        is_unusual_hour = transaction_hour < 6 or transaction_hour > 22  # Night hours
        is_rapid_transaction = time_since_last < 300  # Less than 5 minutes
        
        # Calculate velocity score (transactions per hour)
        velocity_score = (
            user_profile.daily_transaction_count / 24.0
            if user_profile.daily_transaction_count > 0 else 0.0
        )
        
        # Calculate fraud score using ML model or rule-based approach
        if self.use_ml_model and self.ml_model:
            fraud_score = self._calculate_ml_fraud_score(transaction, user_profile)
        else:
            fraud_score = self._calculate_fraud_score(
                amount_vs_avg_ratio,
                is_high_amount,
                is_unusual_hour,
                is_rapid_transaction,
                velocity_score,
                user_profile.daily_transaction_count
            )
        
        return FraudFeatures(
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
            fraud_score=fraud_score,
            is_fraud_alert=fraud_score >= self.fraud_threshold
        )
    
    def _calculate_fraud_score(self, 
                              amount_vs_avg_ratio: float,
                              is_high_amount: bool,
                              is_unusual_hour: bool,
                              is_rapid_transaction: bool,
                              velocity_score: float,
                              daily_count: int) -> float:
        """
        Calculate fraud score using rule-based approach.
        
        Args:
            amount_vs_avg_ratio: Transaction amount vs user average
            is_high_amount: Whether transaction is high amount
            is_unusual_hour: Whether transaction is at unusual hour
            is_rapid_transaction: Whether transaction is rapid
            velocity_score: User transaction velocity
            daily_count: Daily transaction count
            
        Returns:
            Fraud score between 0.0 and 1.0
        """
        score = 0.0
        
        # Amount-based scoring
        if amount_vs_avg_ratio > 5.0:
            score += 0.3
        elif amount_vs_avg_ratio > 3.0:
            score += 0.2
        elif amount_vs_avg_ratio > 2.0:
            score += 0.1
        
        # High amount transactions
        if is_high_amount:
            score += 0.2
        
        # Unusual hour transactions
        if is_unusual_hour:
            score += 0.15
        
        # Rapid transactions (potential velocity fraud)
        if is_rapid_transaction:
            score += 0.25
        
        # High velocity users
        if velocity_score > 10:  # More than 10 transactions per hour average
            score += 0.2
        elif velocity_score > 5:
            score += 0.1
        
        # Excessive daily transactions
        if daily_count > 50:
            score += 0.15
        elif daily_count > 25:
            score += 0.1
        
        # Ensure score is between 0 and 1
        return min(score, 1.0)
    
    def _calculate_ml_fraud_score(self, transaction: Dict[str, Any], 
                                 user_profile: UserProfile) -> float:
        """
        Calculate fraud score using trained ML model.
        
        Args:
            transaction: Transaction data
            user_profile: User profile for behavioral features
            
        Returns:
            Fraud probability between 0.0 and 1.0
        """
        try:
            # Extract features compatible with the trained model
            features = self._extract_ml_features(transaction, user_profile)
            
            # Predict fraud probability
            fraud_probability = self.ml_model.predict_proba([features])[0][1]
            
            return float(fraud_probability)
            
        except Exception as e:
            self.logger.warning(f"ML fraud scoring failed: {e}, falling back to rule-based")
            # Fallback to rule-based scoring
            amount = float(transaction['transaction_amt'])
            timestamp = transaction['generated_timestamp']
            dt = datetime.fromisoformat(timestamp)
            
            amount_vs_avg_ratio = (
                amount / user_profile.avg_transaction_amount 
                if user_profile.avg_transaction_amount > 0 else 1.0
            )
            is_high_amount = amount > 1000.0
            is_unusual_hour = dt.hour < 6 or dt.hour > 22
            
            time_since_last = 0.0
            if user_profile.last_transaction_time:
                last_dt = datetime.fromisoformat(user_profile.last_transaction_time)
                time_since_last = (dt - last_dt).total_seconds()
            is_rapid_transaction = time_since_last < 300
            
            velocity_score = user_profile.daily_transaction_count / 24.0
            
            return self._calculate_fraud_score(
                amount_vs_avg_ratio, is_high_amount, is_unusual_hour,
                is_rapid_transaction, velocity_score, user_profile.daily_transaction_count
            )
    
    def _extract_ml_features(self, transaction: Dict[str, Any], 
                            user_profile: UserProfile) -> List[float]:
        """
        Extract features compatible with the trained ML model.
        
        Args:
            transaction: Transaction data
            user_profile: User profile for behavioral features
            
        Returns:
            List of feature values matching model expectations
        """
        features = []
        
        # Create a mapping of available transaction features with safe None handling
        def safe_float(value, default=0.0):
            """Safely convert value to float, handling None and empty values."""
            if value is None or value == '':
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default
                
        available_features = {
            'TransactionAmt': safe_float(transaction.get('transaction_amt')),
            'ProductCD': transaction.get('product_cd', 'W') or 'W',
            'card1': safe_float(transaction.get('card1')),
            'card2': safe_float(transaction.get('card2')),
            'card3': safe_float(transaction.get('card3')),
            'card5': safe_float(transaction.get('card5')),
            'card6': transaction.get('card6', 'debit') or 'debit',
            'addr1': safe_float(transaction.get('addr1')),
            'addr2': safe_float(transaction.get('addr2')),
            'R_emaildomain': transaction.get('r_emaildomain', 'unknown') or 'unknown',
        }
        
        # Add engineered features
        amount = available_features['TransactionAmt']
        available_features['TransactionAmt_log'] = np.log1p(amount) if amount > 0 else 0.0
        available_features['TransactionAmt_decimal'] = amount - int(amount) if amount > 0 else 0.0
        
        # Add behavioral features from user profile
        available_features['user_avg_amount'] = user_profile.avg_transaction_amount
        available_features['user_total_transactions'] = float(user_profile.total_transactions)
        available_features['user_daily_count'] = float(user_profile.daily_transaction_count)
        
        # For each expected feature, use available value or sensible default
        for feature_name in self.model_features:
            if feature_name in available_features:
                value = available_features[feature_name]
                # Handle categorical features
                if isinstance(value, str):
                    # Simple categorical encoding (more sophisticated than needed but safe)
                    features.append(float(hash(value) % 1000))
                else:
                    features.append(float(value))
            else:
                # Default values for missing features
                if feature_name.startswith('V') or feature_name.startswith('C') or feature_name.startswith('D'):
                    features.append(0.0)  # Numerical features default to 0
                elif feature_name.startswith('id_'):
                    features.append(0.0)  # Identity features default to 0
                elif 'email' in feature_name.lower():
                    features.append(0.0)  # Email features default to 0
                else:
                    features.append(0.0)  # All other features default to 0
        
        return features
    
    def publish_fraud_alert(self, features: FraudFeatures, 
                           original_transaction: Dict[str, Any]) -> None:
        """
        Publish fraud alert to Kafka topic.
        
        Args:
            features: Fraud features for the transaction
            original_transaction: Original transaction data
        """
        try:
            alert = {
                "alert_id": f"alert_{features.transaction_id}_{int(time.time())}",
                "timestamp": datetime.now().isoformat(),
                "user_id": features.user_id,
                "transaction_id": features.transaction_id,
                "fraud_score": features.fraud_score,
                "risk_factors": {
                    "is_high_amount": features.is_high_amount,
                    "is_unusual_hour": features.is_unusual_hour,
                    "is_rapid_transaction": features.is_rapid_transaction,
                    "amount_vs_avg_ratio": features.amount_vs_avg_ratio,
                    "velocity_score": features.velocity_score,
                    "daily_transaction_count": features.daily_transaction_count
                },
                "transaction_details": {
                    "amount": features.amount,
                    "hour": features.transaction_hour,
                    "day": features.transaction_day
                },
                "original_transaction": original_transaction
            }
            
            # Publish to fraud alerts topic
            self.producer.produce(
                self.output_topic,
                key=features.user_id,
                value=json.dumps(alert),
                callback=self._delivery_callback
            )
            
            # Poll for delivery callbacks
            self.producer.poll(0)
            
            self.fraud_alerts_count += 1
            self.logger.warning(
                f"FRAUD ALERT: User {features.user_id}, Score: {features.fraud_score:.3f}, "
                f"Amount: ${features.amount:.2f}"
            )
            
        except Exception as e:
            self.logger.error(f"Error publishing fraud alert: {e}")
    
    def _delivery_callback(self, err, msg):
        """Callback for message delivery confirmation."""
        if err is not None:
            self.logger.error(f"Failed to deliver fraud alert: {err}")
        else:
            self.logger.debug(
                f"Fraud alert delivered to {msg.topic()} [partition {msg.partition()}]"
            )
    
    def process_transaction(self, transaction: Dict[str, Any]) -> None:
        """
        Process a single transaction for fraud detection.
        
        Args:
            transaction: Transaction data from Kafka message
        """
        try:
            user_id = transaction['card1']  # Using card1 as user identifier
            
            # Get current user profile
            user_profile = self.get_user_profile(user_id)
            
            # Extract features for fraud detection
            features = self.extract_features(transaction, user_profile)
            
            # Update user profile with new transaction
            user_profile.update_daily_stats(features.amount, transaction['generated_timestamp'])
            user_profile.update_transaction_stats(features.amount, transaction['generated_timestamp'])
            
            # Update suspicious activity count if fraud detected
            if features.is_fraud_alert:
                user_profile.suspicious_activity_count += 1
            
            # Save updated profile
            self.save_user_profile(user_profile)
            
            # Publish fraud alert if threshold exceeded
            if features.is_fraud_alert:
                self.publish_fraud_alert(features, transaction)
                
            # Debug logging for high fraud scores (even if not alerting)
            if features.fraud_score > 0.2:
                self.logger.debug(
                    f"High fraud score: {features.fraud_score:.3f} for user {user_id}, "
                    f"amount: ${features.amount:.2f}, threshold: {self.fraud_threshold}"
                )
            
            self.processed_count += 1
            
            # Log processing statistics every 1000 transactions
            if self.processed_count % 1000 == 0:
                elapsed = time.time() - self.start_time
                tps = self.processed_count / elapsed
                fraud_rate = self.fraud_alerts_count / self.processed_count * 100
                
                self.logger.info(
                    f"Processed: {self.processed_count}, "
                    f"Fraud alerts: {self.fraud_alerts_count} ({fraud_rate:.2f}%), "
                    f"TPS: {tps:.1f}"
                )
            
        except Exception as e:
            self.logger.error(f"Error processing transaction: {e}")
            self.logger.error(f"Transaction data: {transaction}")
    
    def run(self) -> None:
        """
        Main processing loop for fraud detection consumer.
        """
        self.logger.info("Starting fraud detection consumer...")
        
        try:
            while self.running:
                # Poll for messages with timeout
                msg = self.consumer.poll(timeout=1.0)
                
                if msg is None:
                    continue
                
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        # End of partition - continue
                        continue
                    else:
                        self.logger.error(f"Kafka error: {msg.error()}")
                        break
                
                try:
                    # Parse transaction from message
                    transaction = json.loads(msg.value().decode('utf-8'))
                    
                    # Process transaction for fraud detection
                    self.process_transaction(transaction)
                    
                    # Manually commit offset after successful processing
                    self.consumer.commit(msg)
                    
                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse transaction JSON: {e}")
                    self.consumer.commit(msg)  # Skip bad message
                    
                except Exception as e:
                    self.logger.error(f"Error processing message: {e}")
                    # Don't commit - will retry message
                    
        except KafkaException as e:
            self.logger.error(f"Kafka exception: {e}")
            
        finally:
            self._cleanup()
    
    def _cleanup(self) -> None:
        """Cleanup resources during shutdown."""
        self.logger.info("Shutting down fraud detection consumer...")
        
        # Final statistics
        elapsed = time.time() - self.start_time
        tps = self.processed_count / elapsed if elapsed > 0 else 0
        fraud_rate = self.fraud_alerts_count / self.processed_count * 100 if self.processed_count > 0 else 0
        
        self.logger.info(
            f"Final statistics - Processed: {self.processed_count}, "
            f"Fraud alerts: {self.fraud_alerts_count} ({fraud_rate:.2f}%), "
            f"Average TPS: {tps:.1f}"
        )
        
        # Flush remaining messages
        if self.producer:
            self.producer.flush(timeout=10)
            
        # Close Kafka connections
        if self.consumer:
            self.consumer.close()
            
        # Close Redis connection
        if self.redis_client:
            self.redis_client.close()
        
        self.logger.info("Fraud detection consumer shutdown complete")


def main():
    """Main entry point for fraud detection consumer."""
    try:
        # Create and run fraud detector
        detector = FraudDetector(
            consumer_group="fraud-detection-group",
            fraud_threshold=0.3  # Lower threshold for testing
        )
        
        detector.run()
        
    except KeyboardInterrupt:
        print("\nShutdown requested by user")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()