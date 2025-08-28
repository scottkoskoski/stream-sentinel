# Fraud Detection System Implementation

Stream-Sentinel's fraud detection system demonstrates real-time transaction analysis using machine learning, statistical modeling, and behavioral analytics. This guide explains how modern fraud detection works and how it's implemented using stream processing technologies.

## Understanding Fraud Detection

### What is Financial Fraud Detection?

Financial fraud detection is the process of identifying suspicious or fraudulent transactions in real-time to prevent financial losses and protect customers. Modern fraud detection systems combine:

- **Statistical Analysis**: Pattern recognition in transaction data
- **Machine Learning**: Automated learning from historical fraud patterns
- **Behavioral Analytics**: Understanding normal vs abnormal user behavior
- **Real-time Processing**: Immediate analysis as transactions occur

### Types of Financial Fraud

**1. Transaction Fraud:**
```python
# Examples of transaction fraud patterns
fraud_patterns = {
    "card_testing": {
        "description": "Small transactions to test stolen card validity",
        "indicators": ["multiple_small_amounts", "rapid_succession", "different_merchants"]
    },
    "account_takeover": {
        "description": "Unauthorized access to legitimate accounts", 
        "indicators": ["unusual_location", "large_amount", "new_payee", "time_anomaly"]
    },
    "synthetic_identity": {
        "description": "Fake identities using real and fake information",
        "indicators": ["new_account", "rapid_credit_building", "bust_out_pattern"]
    }
}
```

**2. Behavioral Anomalies:**
- Unusual spending patterns for a user
- Transactions at odd hours
- Geographic anomalies (user in different locations)
- Velocity anomalies (too many transactions too quickly)

## Stream-Sentinel Fraud Detection Architecture

```
                    Real-Time Fraud Detection Pipeline
    
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Transaction   │    │   Feature       │    │   ML Scoring    │
│   Ingestion     │────▶   Engineering    │────▶   & Rules      │
│                 │    │                 │    │                 │
│ • Kafka Stream  │    │ • User Profile  │    │ • LightGBM      │
│ • Validation    │    │ • Behavioral    │    │ • Business Rules│
│ • Enrichment    │    │ • Temporal      │    │ • Ensemble      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Redis State   │    │   Feature       │    │   Alert         │
│   Management    │    │   Store         │    │   Generation    │
│                 │    │                 │    │                 │
│ • User Profiles │    │ • Real-time     │    │ • Risk Scoring  │
│ • Transaction   │    │ • Historical    │    │ • Action Rules  │
│ • History       │    │ • Aggregates    │    │ • Notifications │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## 🧮 Feature Engineering for Fraud Detection

Feature engineering transforms raw transaction data into meaningful signals that machine learning models can use to identify fraud.

### 1. User Behavioral Features

```python
class UserBehaviorFeatureExtractor:
    """Extract user behavior features for fraud detection"""
    
    def __init__(self, redis_client):
        self.redis_client = redis_client
    
    def extract_behavioral_features(self, transaction, user_profile):
        """Extract features related to user's normal behavior patterns"""
        
        # Amount-based features
        amount = float(transaction['amount'])
        avg_amount = user_profile.get('avg_transaction_amount', amount)
        max_amount = user_profile.get('max_single_transaction', amount)
        
        amount_features = {
            'amount': amount,
            'amount_log': np.log(amount + 1),  # Log transform for skewed data
            'amount_vs_avg_ratio': amount / max(avg_amount, 1.0),
            'amount_vs_max_ratio': amount / max(max_amount, 1.0),
            'is_high_amount': amount > avg_amount * 3,
            'is_small_amount': amount < 10.0,  # Potential card testing
            'amount_rounded': amount == round(amount),  # Round number bias
        }
        
        # Frequency-based features
        daily_count = user_profile.get('daily_transaction_count', 0)
        total_count = user_profile.get('total_transactions', 0)
        
        frequency_features = {
            'daily_transaction_count': daily_count,
            'total_transactions': total_count,
            'is_first_transaction': total_count == 0,
            'is_high_frequency_day': daily_count > 10,
            'transactions_per_day_avg': total_count / max(self.get_account_age_days(user_profile), 1)
        }
        
        # Combine all behavioral features
        return {**amount_features, **frequency_features}
    
    def get_account_age_days(self, user_profile):
        """Calculate account age in days"""
        created_at = user_profile.get('created_at')
        if not created_at:
            return 1
        
        created_date = datetime.fromisoformat(created_at)
        age_days = (datetime.utcnow() - created_date).days
        return max(age_days, 1)
```

### 2. Temporal Features

```python
class TemporalFeatureExtractor:
    """Extract time-based features for fraud detection"""
    
    def extract_temporal_features(self, transaction, user_profile):
        """Extract features related to timing patterns"""
        
        transaction_time = datetime.fromisoformat(transaction['timestamp'])
        last_transaction_time_str = user_profile.get('last_transaction_time')
        
        # Basic time features
        time_features = {
            'transaction_hour': transaction_time.hour,
            'transaction_day_of_week': transaction_time.weekday(),
            'is_weekend': transaction_time.weekday() >= 5,
            'is_business_hours': 9 <= transaction_time.hour <= 17,
            'is_late_night': transaction_time.hour <= 5 or transaction_time.hour >= 23,
        }
        
        # Velocity features (time between transactions)
        if last_transaction_time_str:
            last_transaction_time = datetime.fromisoformat(last_transaction_time_str)
            time_diff_seconds = (transaction_time - last_transaction_time).total_seconds()
            
            velocity_features = {
                'time_since_last_transaction': time_diff_seconds,
                'minutes_since_last': time_diff_seconds / 60,
                'hours_since_last': time_diff_seconds / 3600,
                'is_rapid_transaction': time_diff_seconds < 300,  # Less than 5 minutes
                'is_very_rapid': time_diff_seconds < 60,  # Less than 1 minute
                'is_same_minute': time_diff_seconds < 60,
            }
        else:
            velocity_features = {
                'time_since_last_transaction': float('inf'),
                'minutes_since_last': float('inf'),
                'hours_since_last': float('inf'),
                'is_rapid_transaction': False,
                'is_very_rapid': False,
                'is_same_minute': False,
            }
        
        return {**time_features, **velocity_features}
```

### 3. Transaction Context Features

```python
class TransactionContextExtractor:
    """Extract context features from transaction details"""
    
    def extract_context_features(self, transaction, user_profile):
        """Extract features from transaction context"""
        
        # Basic transaction features
        basic_features = {
            'transaction_id': transaction['transaction_id'],
            'merchant': transaction.get('merchant', 'unknown'),
            'category': transaction.get('category', 'unknown'),
            'payment_method': transaction.get('payment_method', 'unknown'),
        }
        
        # Merchant-based features
        merchant_features = self.extract_merchant_features(
            transaction.get('merchant'), user_profile['user_id']
        )
        
        # Device/Channel features (if available)
        device_features = self.extract_device_features(transaction)
        
        return {**basic_features, **merchant_features, **device_features}
    
    def extract_merchant_features(self, merchant, user_id):
        """Extract merchant-related features"""
        if not merchant:
            return {'is_new_merchant': True, 'merchant_frequency': 0}
        
        # Check if user has transacted with this merchant before
        merchant_key = f"user:{user_id}:merchants"
        is_new_merchant = self.redis_client.sadd(f"user:{user_id}:all_merchants", merchant) == 1
        
        # Get merchant transaction frequency for this user
        merchant_frequency = self.redis_client.hget(f"user:{user_id}:merchant_counts", merchant) or 0
        merchant_frequency = int(merchant_frequency)
        
        # Increment merchant count
        self.redis_client.hincrby(f"user:{user_id}:merchant_counts", merchant, 1)
        
        return {
            'is_new_merchant': is_new_merchant,
            'merchant_frequency': merchant_frequency,
            'is_frequent_merchant': merchant_frequency > 5
        }
    
    def extract_device_features(self, transaction):
        """Extract device/channel related features"""
        return {
            'channel': transaction.get('channel', 'online'),
            'device_type': transaction.get('device_type', 'unknown'),
            'is_mobile': transaction.get('device_type') == 'mobile',
            'is_web': transaction.get('device_type') == 'web',
        }
```

### 4. Aggregated Features

```python
class AggregatedFeatureExtractor:
    """Extract aggregated features across time windows"""
    
    def __init__(self, redis_client):
        self.redis_client = redis_client
    
    def extract_windowed_features(self, user_id, current_time):
        """Extract features across different time windows"""
        
        features = {}
        
        # Define time windows (in seconds)
        windows = {
            '1h': 3600,
            '6h': 6 * 3600,
            '24h': 24 * 3600,
            '7d': 7 * 24 * 3600
        }
        
        for window_name, window_seconds in windows.items():
            window_features = self.get_window_features(user_id, current_time, window_seconds)
            
            # Prefix features with window name
            for feature_name, value in window_features.items():
                features[f"{window_name}_{feature_name}"] = value
        
        return features
    
    def get_window_features(self, user_id, current_time, window_seconds):
        """Get aggregated features for a specific time window"""
        
        timeline_key = f"user:{user_id}:timeline"
        
        # Get transactions in the time window
        window_start = current_time.timestamp() - window_seconds
        window_end = current_time.timestamp()
        
        transactions = self.redis_client.zrangebyscore(
            timeline_key, window_start, window_end, withscores=True
        )
        
        if not transactions:
            return {
                'transaction_count': 0,
                'total_amount': 0.0,
                'avg_amount': 0.0,
                'unique_merchants': 0,
                'max_amount': 0.0
            }
        
        # Parse transaction data
        amounts = []
        merchants = set()
        
        for transaction_json, timestamp in transactions:
            try:
                transaction_data = json.loads(transaction_json)
                amounts.append(float(transaction_data['amount']))
                merchants.add(transaction_data.get('merchant', 'unknown'))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        
        return {
            'transaction_count': len(amounts),
            'total_amount': sum(amounts),
            'avg_amount': sum(amounts) / len(amounts) if amounts else 0,
            'max_amount': max(amounts) if amounts else 0,
            'min_amount': min(amounts) if amounts else 0,
            'amount_std': np.std(amounts) if len(amounts) > 1 else 0,
            'unique_merchants': len(merchants),
            'amount_velocity': sum(amounts) / (window_seconds / 3600)  # Amount per hour
        }
```

## 🤖 Machine Learning Model Integration

### Model Loading and Serving

```python
class FraudDetectionModel:
    """Serve machine learning model for real-time fraud scoring"""
    
    def __init__(self, model_path='models/ieee_fraud_model_production.pkl'):
        self.model = self.load_model(model_path)
        self.feature_names = self.load_feature_metadata()
        self.scaler = self.load_scaler() if self.needs_scaling() else None
    
    def load_model(self, model_path):
        """Load trained fraud detection model"""
        try:
            import pickle
            with open(model_path, 'rb') as f:
                model = pickle.load(f)
            print(f"Loaded fraud detection model: {type(model).__name__}")
            return model
        except Exception as e:
            print(f"Error loading model: {e}")
            return self.create_fallback_model()
    
    def load_feature_metadata(self):
        """Load feature names and metadata"""
        metadata_path = 'models/ieee_fraud_model_metadata.json'
        try:
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
            return metadata['feature_names']
        except Exception as e:
            print(f"Error loading feature metadata: {e}")
            return self.get_default_features()
    
    def predict_fraud_probability(self, features):
        """Predict fraud probability for a transaction"""
        
        # Convert features to model input format
        feature_vector = self.prepare_feature_vector(features)
        
        try:
            # Get prediction probability for fraud class (class 1)
            if hasattr(self.model, 'predict_proba'):
                probabilities = self.model.predict_proba([feature_vector])
                fraud_probability = probabilities[0][1]  # Probability of fraud (class 1)
            else:
                # Fallback for models without predict_proba
                prediction = self.model.predict([feature_vector])
                fraud_probability = float(prediction[0])
            
            return min(max(fraud_probability, 0.0), 1.0)  # Clamp between 0 and 1
            
        except Exception as e:
            print(f"Error in model prediction: {e}")
            return self.fallback_prediction(features)
    
    def prepare_feature_vector(self, features):
        """Convert feature dictionary to model input vector"""
        
        feature_vector = []
        
        for feature_name in self.feature_names:
            if feature_name in features:
                value = features[feature_name]
                
                # Handle different data types
                if isinstance(value, bool):
                    feature_vector.append(1.0 if value else 0.0)
                elif isinstance(value, (int, float)):
                    # Handle infinite values
                    if np.isinf(value) or np.isnan(value):
                        feature_vector.append(0.0)
                    else:
                        feature_vector.append(float(value))
                else:
                    # Handle categorical features (simplified)
                    feature_vector.append(self.encode_categorical(feature_name, value))
            else:
                # Missing feature - use default value
                feature_vector.append(0.0)
        
        # Apply scaling if needed
        if self.scaler:
            feature_vector = self.scaler.transform([feature_vector])[0]
        
        return feature_vector
    
    def encode_categorical(self, feature_name, value):
        """Simple categorical encoding"""
        # This would be more sophisticated in a real implementation
        # possibly using learned encodings from training
        if feature_name == 'transaction_hour':
            return float(value) / 24.0  # Normalize hour
        elif feature_name == 'transaction_day_of_week':
            return float(value) / 7.0   # Normalize day
        else:
            return hash(str(value)) % 100 / 100.0  # Simple hash encoding
    
    def fallback_prediction(self, features):
        """Simple rule-based fallback when ML model fails"""
        score = 0.0
        
        # Simple rules for fallback scoring
        if features.get('amount', 0) > 5000:
            score += 0.3
        if features.get('is_rapid_transaction', False):
            score += 0.2
        if features.get('is_late_night', False):
            score += 0.1
        if features.get('amount_vs_avg_ratio', 1) > 5:
            score += 0.4
        
        return min(score, 1.0)
    
    def create_fallback_model(self):
        """Create simple fallback model if main model fails to load"""
        class FallbackModel:
            def predict_proba(self, X):
                # Simple rule-based predictions
                predictions = []
                for features in X:
                    # Very basic rule: high amounts are more likely fraud
                    if len(features) > 0 and features[0] > 1000:  # Assuming first feature is amount
                        fraud_prob = 0.3
                    else:
                        fraud_prob = 0.1
                    predictions.append([1-fraud_prob, fraud_prob])
                return np.array(predictions)
        
        print("Using fallback rule-based model")
        return FallbackModel()
```

### Business Rules Integration

```python
class BusinessRulesEngine:
    """Apply business rules on top of ML model predictions"""
    
    def __init__(self):
        self.rules = self.load_business_rules()
    
    def load_business_rules(self):
        """Define business rules for fraud detection"""
        return [
            {
                'name': 'high_amount_rule',
                'condition': lambda features: features.get('amount', 0) > 10000,
                'action': lambda score: max(score, 0.8),
                'description': 'Transactions over $10k are high risk'
            },
            {
                'name': 'rapid_transaction_rule',
                'condition': lambda features: features.get('is_very_rapid', False),
                'action': lambda score: min(score + 0.3, 1.0),
                'description': 'Very rapid transactions (< 1 minute) boost fraud score'
            },
            {
                'name': 'new_merchant_high_amount',
                'condition': lambda features: (
                    features.get('is_new_merchant', False) and 
                    features.get('amount', 0) > 500
                ),
                'action': lambda score: min(score + 0.4, 1.0),
                'description': 'High amount transactions with new merchants are suspicious'
            },
            {
                'name': 'unusual_hour_rule',
                'condition': lambda features: features.get('is_late_night', False),
                'action': lambda score: min(score + 0.2, 1.0),
                'description': 'Late night transactions get score boost'
            },
            {
                'name': 'high_velocity_rule',
                'condition': lambda features: features.get('1h_transaction_count', 0) > 5,
                'action': lambda score: min(score + 0.25, 1.0),
                'description': 'More than 5 transactions in 1 hour is suspicious'
            },
            {
                'name': 'round_number_rule',
                'condition': lambda features: (
                    features.get('amount_rounded', False) and
                    features.get('amount', 0) >= 1000
                ),
                'action': lambda score: min(score + 0.1, 1.0),
                'description': 'Large round number amounts are slightly more suspicious'
            }
        ]
    
    def apply_business_rules(self, ml_score, features):
        """Apply business rules to modify ML model score"""
        
        final_score = ml_score
        applied_rules = []
        
        for rule in self.rules:
            try:
                if rule['condition'](features):
                    old_score = final_score
                    final_score = rule['action'](final_score)
                    
                    applied_rules.append({
                        'rule_name': rule['name'],
                        'description': rule['description'],
                        'score_change': final_score - old_score,
                        'new_score': final_score
                    })
                    
            except Exception as e:
                print(f"Error applying rule {rule['name']}: {e}")
                continue
        
        return {
            'final_score': final_score,
            'ml_score': ml_score,
            'applied_rules': applied_rules,
            'rules_triggered': len(applied_rules)
        }
```

## 🚨 Real-Time Fraud Detection Pipeline

### Complete Fraud Detection Consumer

```python
class RealTimeFraudDetector:
    """Complete real-time fraud detection consumer"""
    
    def __init__(self):
        # Initialize components
        self.kafka_config = get_kafka_config()
        self.consumer = self.create_consumer()
        self.producer = self.create_producer()
        self.redis_client = self.create_redis_client()
        
        # Feature extractors
        self.behavior_extractor = UserBehaviorFeatureExtractor(self.redis_client)
        self.temporal_extractor = TemporalFeatureExtractor()
        self.context_extractor = TransactionContextExtractor(self.redis_client)
        self.aggregated_extractor = AggregatedFeatureExtractor(self.redis_client)
        
        # ML and rules
        self.ml_model = FraudDetectionModel()
        self.business_rules = BusinessRulesEngine()
        
        # State management
        self.redis_manager = StreamSentinelRedisManager()
        
        # Performance tracking
        self.metrics = StreamProcessingMetrics()
        
        # Graceful shutdown
        self.running = False
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def create_consumer(self):
        """Create Kafka consumer for transactions"""
        config = self.kafka_config.get_consumer_config(
            'fraud-detection-group', 'fraud_detector'
        )
        return Consumer(config)
    
    def create_producer(self):
        """Create Kafka producer for alerts"""
        config = self.kafka_config.get_producer_config('alert')
        return Producer(config)
    
    def run(self):
        """Main fraud detection loop"""
        self.running = True
        self.consumer.subscribe(['synthetic-transactions'])
        
        print(" Starting real-time fraud detection...")
        print(" Processing transactions and detecting fraud patterns...")
        
        try:
            while self.running:
                msg = self.consumer.poll(timeout=1.0)
                
                if msg is None:
                    continue
                if msg.error():
                    self.handle_consumer_error(msg.error())
                    continue
                
                # Process transaction
                start_time = time.time()
                self.process_transaction_message(msg)
                end_time = time.time()
                
                # Record metrics
                self.metrics.record_processing_time(start_time, end_time)
                
                # Periodic status update
                if self.metrics.metrics['messages_processed'] % 1000 == 0:
                    self.print_status_update()
                
        except KeyboardInterrupt:
            print("\\n🛑 Received shutdown signal...")
        finally:
            self.shutdown()
    
    def process_transaction_message(self, msg):
        """Process a single transaction message"""
        try:
            # Parse transaction
            transaction = json.loads(msg.value().decode('utf-8'))
            
            # Extract all features
            features = self.extract_all_features(transaction)
            
            # Score transaction
            fraud_result = self.score_transaction(transaction, features)
            
            # Update user state
            self.update_user_state(transaction, fraud_result)
            
            # Generate alert if needed
            if fraud_result['is_fraud_alert']:
                self.generate_fraud_alert(transaction, fraud_result)
            
            # Commit message offset
            self.consumer.commit(msg)
            
        except Exception as e:
            print(f" Error processing transaction: {e}")
            # In production, send to dead letter queue
            self.handle_processing_error(msg, str(e))
    
    def extract_all_features(self, transaction):
        """Extract all fraud detection features"""
        user_id = transaction['user_id']
        transaction_time = datetime.fromisoformat(transaction['timestamp'])
        
        # Get user profile
        user_profile = self.redis_manager.get_fraud_features_for_user(user_id)
        
        # Extract features from different extractors
        behavioral_features = self.behavior_extractor.extract_behavioral_features(
            transaction, user_profile
        )
        
        temporal_features = self.temporal_extractor.extract_temporal_features(
            transaction, user_profile
        )
        
        context_features = self.context_extractor.extract_context_features(
            transaction, user_profile
        )
        
        aggregated_features = self.aggregated_extractor.extract_windowed_features(
            user_id, transaction_time
        )
        
        # Combine all features
        all_features = {
            **behavioral_features,
            **temporal_features,
            **context_features,
            **aggregated_features
        }
        
        return all_features
    
    def score_transaction(self, transaction, features):
        """Score transaction for fraud probability"""
        
        # Get ML model prediction
        ml_score = self.ml_model.predict_fraud_probability(features)
        
        # Apply business rules
        business_result = self.business_rules.apply_business_rules(ml_score, features)
        
        # Create fraud result
        final_score = business_result['final_score']
        fraud_threshold = 0.7  # Configurable threshold
        
        fraud_result = {
            'transaction_id': transaction['transaction_id'],
            'user_id': transaction['user_id'],
            'timestamp': datetime.utcnow().isoformat(),
            'ml_score': ml_score,
            'business_rules_score': final_score,
            'applied_rules': business_result['applied_rules'],
            'is_fraud_alert': final_score > fraud_threshold,
            'risk_level': self.calculate_risk_level(final_score),
            'features_used': features,
            'model_version': '1.0.0'
        }
        
        return fraud_result
    
    def calculate_risk_level(self, score):
        """Calculate risk level from fraud score"""
        if score >= 0.9:
            return 'CRITICAL'
        elif score >= 0.7:
            return 'HIGH'
        elif score >= 0.5:
            return 'MEDIUM'
        elif score >= 0.3:
            return 'LOW'
        else:
            return 'MINIMAL'
    
    def update_user_state(self, transaction, fraud_result):
        """Update user state with transaction and fraud information"""
        
        # Update basic user profile
        self.redis_manager.update_user_with_transaction(
            transaction['user_id'], transaction
        )
        
        # Record fraud alert if detected
        if fraud_result['is_fraud_alert']:
            self.redis_manager.record_fraud_alert(
                transaction['user_id'], fraud_result
            )
            self.metrics.metrics['fraud_detected'] += 1
    
    def generate_fraud_alert(self, transaction, fraud_result):
        """Generate and send fraud alert"""
        
        alert = {
            'alert_id': f"alert_{transaction['transaction_id']}_{int(time.time())}",
            'alert_type': 'fraud_detection',
            'severity': fraud_result['risk_level'],
            'user_id': transaction['user_id'],
            'transaction_id': transaction['transaction_id'],
            'fraud_score': fraud_result['business_rules_score'],
            'ml_confidence': fraud_result['ml_score'],
            'triggered_rules': [rule['rule_name'] for rule in fraud_result['applied_rules']],
            'transaction_details': {
                'amount': transaction['amount'],
                'merchant': transaction.get('merchant', 'unknown'),
                'timestamp': transaction['timestamp']
            },
            'detection_timestamp': fraud_result['timestamp'],
            'requires_action': fraud_result['risk_level'] in ['HIGH', 'CRITICAL']
        }
        
        # Send alert to Kafka topic
        self.producer.produce(
            topic='fraud-alerts',
            key=alert['user_id'],
            value=json.dumps(alert),
            callback=self.alert_delivery_callback
        )
        
        # Print alert for monitoring
        print(f"🚨 FRAUD ALERT: {alert['severity']} risk transaction detected")
        print(f"   User: {alert['user_id']}, Amount: ${transaction['amount']}")
        print(f"   Score: {fraud_result['business_rules_score']:.3f}, Rules: {len(fraud_result['applied_rules'])}")
    
    def alert_delivery_callback(self, err, msg):
        """Callback for alert delivery confirmation"""
        if err:
            print(f" Alert delivery failed: {err}")
        else:
            print(f" Alert delivered to {msg.topic()} [{msg.partition()}] @ {msg.offset()}")
    
    def print_status_update(self):
        """Print periodic status updates"""
        stats = self.metrics.get_performance_stats()
        redis_stats = self.redis_manager.get_performance_stats()
        
        print(f"\\n FRAUD DETECTION STATUS:")
        print(f"   Transactions processed: {stats['messages_processed']:,}")
        print(f"   Throughput: {stats['throughput_per_second']:.1f} TPS")
        print(f"   Avg processing time: {stats['avg_processing_time_ms']:.2f}ms")
        print(f"   Fraud detection rate: {stats['fraud_detection_rate']:.1%}")
        print(f"   Redis memory usage: {redis_stats['memory_usage_mb']}MB")
        print(f"   Redis ops/sec: {redis_stats['ops_per_second']}")
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        print(f"\\n🛑 Received signal {signum}, shutting down...")
        self.running = False
    
    def shutdown(self):
        """Clean shutdown of all components"""
        print(" Shutting down fraud detection system...")
        
        # Final stats
        final_stats = self.metrics.get_performance_stats()
        print(f"\\n FINAL STATISTICS:")
        print(f"   Total transactions processed: {final_stats['messages_processed']:,}")
        print(f"   Average throughput: {final_stats['throughput_per_second']:.1f} TPS")
        print(f"   Fraud alerts generated: {self.metrics.metrics['fraud_detected']}")
        
        # Close connections
        self.consumer.close()
        self.producer.flush(timeout=10)
        print(" Fraud detection system shutdown complete")


def main():
    """Main entry point for fraud detection consumer"""
    detector = RealTimeFraudDetector()
    detector.run()


if __name__ == "__main__":
    main()
```

## Model Performance and Evaluation

### Performance Metrics

```python
class FraudDetectionMetrics:
    """Track and evaluate fraud detection performance"""
    
    def __init__(self):
        self.true_positives = 0   # Correctly identified fraud
        self.true_negatives = 0   # Correctly identified legitimate  
        self.false_positives = 0  # False fraud alerts
        self.false_negatives = 0  # Missed fraud
        
        self.predictions = []
        self.ground_truth = []
    
    def record_prediction(self, predicted_fraud, actual_fraud):
        """Record a prediction for evaluation"""
        self.predictions.append(predicted_fraud)
        self.ground_truth.append(actual_fraud)
        
        if predicted_fraud and actual_fraud:
            self.true_positives += 1
        elif predicted_fraud and not actual_fraud:
            self.false_positives += 1
        elif not predicted_fraud and actual_fraud:
            self.false_negatives += 1
        else:
            self.true_negatives += 1
    
    def calculate_metrics(self):
        """Calculate comprehensive performance metrics"""
        
        # Basic metrics
        precision = self.true_positives / max(self.true_positives + self.false_positives, 1)
        recall = self.true_positives / max(self.true_positives + self.false_negatives, 1)
        f1_score = 2 * (precision * recall) / max(precision + recall, 1)
        accuracy = (self.true_positives + self.true_negatives) / max(len(self.predictions), 1)
        
        # Business-relevant metrics
        false_positive_rate = self.false_positives / max(self.false_positives + self.true_negatives, 1)
        fraud_detection_rate = recall  # Same as recall
        
        return {
            'precision': precision,
            'recall': recall, 
            'f1_score': f1_score,
            'accuracy': accuracy,
            'false_positive_rate': false_positive_rate,
            'fraud_detection_rate': fraud_detection_rate,
            'total_predictions': len(self.predictions),
            'fraud_cases': sum(self.ground_truth),
            'alerts_generated': sum(self.predictions)
        }
```

## Testing and Validation

### Synthetic Fraud Testing

```python
class FraudDetectionTester:
    """Test fraud detection system with synthetic fraud patterns"""
    
    def __init__(self, fraud_detector):
        self.fraud_detector = fraud_detector
        self.test_results = []
    
    def test_fraud_patterns(self):
        """Test detection of various fraud patterns"""
        
        test_cases = [
            self.test_high_amount_fraud(),
            self.test_rapid_transactions(),
            self.test_unusual_time_patterns(),
            self.test_new_merchant_fraud(),
            self.test_legitimate_transactions()
        ]
        
        overall_results = {
            'test_cases_passed': sum(1 for result in test_cases if result['passed']),
            'total_test_cases': len(test_cases),
            'test_details': test_cases
        }
        
        return overall_results
    
    def test_high_amount_fraud(self):
        """Test detection of high amount fraud"""
        
        # Create synthetic high-amount transaction
        transaction = {
            'transaction_id': 'test_high_amount_001',
            'user_id': 'test_user_001',
            'amount': 15000.00,  # Very high amount
            'timestamp': datetime.utcnow().isoformat(),
            'merchant': 'Online Store'
        }
        
        # Simulate normal user profile
        normal_profile = {
            'user_id': 'test_user_001',
            'avg_transaction_amount': 150.0,  # Normal: $150 average
            'total_transactions': 50,
            'daily_transaction_count': 2
        }
        
        # Mock Redis response
        # In real test, you'd mock Redis or use test database
        
        # Score transaction
        features = self.fraud_detector.extract_all_features(transaction)
        result = self.fraud_detector.score_transaction(transaction, features)
        
        # Validate detection
        passed = result['is_fraud_alert'] and result['business_rules_score'] > 0.8
        
        return {
            'test_name': 'high_amount_fraud',
            'passed': passed,
            'fraud_score': result['business_rules_score'],
            'details': f"High amount (${transaction['amount']}) should trigger fraud alert"
        }
```

## Advanced Concepts

### Online Learning Integration

```python
class OnlineLearningFraudDetector:
    """Fraud detector with online learning capabilities"""
    
    def update_model_with_feedback(self, transaction_id, actual_fraud_label):
        """Update model based on feedback from investigations"""
        
        # Get stored features for this transaction
        stored_features = self.get_stored_features(transaction_id)
        
        if stored_features:
            # Update model with new labeled example
            self.model.partial_fit([stored_features], [actual_fraud_label])
            
            # Update performance metrics
            predicted_fraud = stored_features.get('predicted_fraud', False)
            self.metrics.record_prediction(predicted_fraud, actual_fraud_label)
            
            print(f"Model updated with feedback for {transaction_id}: {actual_fraud_label}")
```

### Ensemble Methods

```python
class EnsembleFraudDetector:
    """Combine multiple models for better fraud detection"""
    
    def __init__(self):
        self.models = {
            'lightgbm': self.load_lightgbm_model(),
            'neural_network': self.load_nn_model(), 
            'isolation_forest': self.load_isolation_forest(),
        }
        
        self.model_weights = {
            'lightgbm': 0.5,
            'neural_network': 0.3,
            'isolation_forest': 0.2
        }
    
    def ensemble_predict(self, features):
        """Get ensemble prediction from multiple models"""
        
        predictions = {}
        
        for model_name, model in self.models.items():
            try:
                pred = model.predict_proba([features])[0][1]
                predictions[model_name] = pred
            except Exception as e:
                print(f"Model {model_name} failed: {e}")
                predictions[model_name] = 0.0
        
        # Weighted average
        ensemble_score = sum(
            predictions[model] * self.model_weights[model]
            for model in predictions
        )
        
        return {
            'ensemble_score': ensemble_score,
            'individual_predictions': predictions,
            'models_used': list(predictions.keys())
        }
```

## Next Steps and Extensions

**Advanced Fraud Detection Topics:**
1. **Graph-based Fraud Detection**: Network analysis for connected fraud
2. **Time Series Analysis**: Seasonal and trend-based detection
3. **Adversarial Machine Learning**: Defense against fraud model attacks
4. **Explainable AI**: Understanding why transactions are flagged

**Stream-Sentinel Integration:**
- [Alert Response System](../alert-response/README.md) - Automated actions on fraud detection
- [Machine Learning Pipeline](../machine-learning/README.md) - Model training and deployment
- [State Management](../state-management/README.md) - Advanced Redis patterns

---

The fraud detection system in Stream-Sentinel demonstrates how modern financial institutions detect fraud in real-time, combining statistical analysis, machine learning, and business rules to protect customers and prevent losses while maintaining high transaction throughput.