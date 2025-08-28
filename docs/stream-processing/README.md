# Stream Processing Architecture

Stream processing is the heart of Stream-Sentinel's real-time fraud detection capabilities. This guide explores how continuous data streams are processed, transformed, and analyzed to detect fraudulent transactions with sub-100ms latency.

## What is Stream Processing?

Stream processing is a data processing paradigm that handles continuous, unbounded data streams in real-time. Unlike batch processing that works on fixed datasets, stream processing analyzes data as it flows through the system.

**Batch vs Stream Processing:**

```
Batch Processing (Traditional):
Collect Data → Store → Process → Results
[1 hour delay] → [Analytics Reports]

Stream Processing (Real-time):
Data → Process → Results (continuous)
[<100ms delay] → [Immediate Actions]
```

**Why Stream Processing for Fraud Detection?**
- **Real-time Detection**: Identify fraud as transactions occur
- **Immediate Response**: Block accounts or transactions instantly  
- **Continuous Learning**: Update user behavior models in real-time
- **Cost Efficiency**: Process only relevant data, not entire datasets

## Stream Processing Architecture

```
                    Stream Processing Pipeline
    
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Source    │    │   Process   │    │   Enrich    │    │    Sink     │
│             │    │             │    │             │    │             │
│ Synthetic   │────┤ Fraud       │────┤ User        │────┤ Alert       │
│ Transaction │    │ Detection   │    │ Profile     │    │ Response    │
│ Producer    │    │ Consumer    │    │ Lookup      │    │ System      │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
       │                   │                   │                   │
       ▼                   ▼                   ▼                   ▼
 Kafka Topic         Stream Processor    Redis State Store    Action Queue
```

### Data Flow in Stream-Sentinel

**1. Transaction Ingestion:**
```python
# Producer sends transaction to Kafka
transaction = {
    "transaction_id": "tx_12345",
    "user_id": "user_001",
    "amount": 250.00,
    "timestamp": "2025-08-26T14:30:00Z",
    "merchant": "Amazon"
}

producer.produce(
    topic='synthetic-transactions',
    key=transaction['user_id'],  # Partition by user for ordering
    value=json.dumps(transaction)
)
```

**2. Real-time Processing:**
```python
# Consumer processes stream in real-time
for message in consumer:
    transaction = json.loads(message.value())
    
    # Stream processing steps:
    # 1. Validate transaction format
    # 2. Enrich with user profile data
    # 3. Calculate fraud features
    # 4. Score fraud probability
    # 5. Generate alert if needed
    # 6. Update user profile state
    
    fraud_score = process_transaction(transaction)
```

**3. State Management:**
```python
# Maintain user state in Redis for fast lookups
user_profile = {
    "user_id": "user_001",
    "avg_transaction_amount": 127.50,
    "transaction_count": 42,
    "last_transaction_time": "2025-08-26T14:25:00Z",
    "daily_count": 5,
    "fraud_score_history": [0.1, 0.2, 0.15, 0.3]
}

redis_client.hset(f"user:{user_id}", mapping=user_profile)
```

## Stream Processing Patterns

### 1. Stateless Processing

**Definition:** Each message is processed independently without considering previous messages.

**Example - Transaction Validation:**
```python
def validate_transaction(transaction):
    """Stateless validation - no external state needed"""
    errors = []
    
    # Format validation
    if not transaction.get('transaction_id'):
        errors.append("Missing transaction ID")
    
    # Business rule validation
    if transaction.get('amount', 0) <= 0:
        errors.append("Invalid transaction amount")
        
    if transaction.get('amount', 0) > 10000:
        errors.append("Amount exceeds single transaction limit")
    
    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "transaction": transaction
    }
```

**Benefits:**
- Simple to implement and test
- Easy to scale horizontally
- No state synchronization issues
- Perfect for data validation and transformation

### 2. Stateful Processing

**Definition:** Processing requires maintaining state across multiple messages.

**Example - User Behavior Analysis:**
```python
class UserBehaviorProcessor:
    def __init__(self):
        self.redis_client = redis.Redis(host='localhost', port=6379)
    
    def process_transaction(self, transaction):
        """Stateful processing - uses historical user data"""
        user_id = transaction['user_id']
        amount = transaction['amount']
        timestamp = transaction['timestamp']
        
        # Get user's historical data (state)
        user_profile = self.get_user_profile(user_id)
        
        # Calculate features using historical state
        features = self.calculate_features(transaction, user_profile)
        
        # Update state with new transaction
        self.update_user_profile(user_id, transaction)
        
        return features
    
    def calculate_features(self, transaction, user_profile):
        """Calculate fraud features using historical state"""
        amount = transaction['amount']
        avg_amount = user_profile.get('avg_transaction_amount', 0)
        
        return {
            'amount_vs_avg_ratio': amount / max(avg_amount, 1),
            'is_unusual_amount': amount > avg_amount * 3,
            'daily_transaction_count': user_profile.get('daily_count', 0) + 1,
            'time_since_last_transaction': self.calculate_time_diff(
                transaction['timestamp'], 
                user_profile.get('last_transaction_time')
            )
        }
```

**Challenges:**
- State must be maintained and synchronized
- Fault tolerance requires state recovery
- Scaling requires state partitioning

### 3. Windowed Processing

**Definition:** Group events by time windows for aggregation or analysis.

**Example - Hourly Fraud Statistics:**
```python
class WindowedFraudAnalyzer:
    def __init__(self, window_size_minutes=60):
        self.window_size = window_size_minutes * 60  # Convert to seconds
        self.windows = {}  # In-memory window storage
    
    def process_transaction(self, transaction):
        """Process transaction with windowed aggregation"""
        timestamp = datetime.fromisoformat(transaction['timestamp'])
        window_start = self.get_window_start(timestamp)
        
        # Initialize window if doesn't exist
        if window_start not in self.windows:
            self.windows[window_start] = {
                'total_transactions': 0,
                'fraud_transactions': 0,
                'total_amount': 0.0,
                'unique_users': set()
            }
        
        # Update window statistics
        window = self.windows[window_start]
        window['total_transactions'] += 1
        window['total_amount'] += transaction['amount']
        window['unique_users'].add(transaction['user_id'])
        
        if transaction.get('is_fraud', False):
            window['fraud_transactions'] += 1
        
        # Calculate window statistics
        fraud_rate = window['fraud_transactions'] / window['total_transactions']
        
        # Alert if fraud rate is unusually high
        if fraud_rate > 0.10:  # 10% fraud rate threshold
            self.send_fraud_spike_alert(window_start, fraud_rate)
        
        return {
            'window_start': window_start,
            'fraud_rate': fraud_rate,
            'transaction_volume': window['total_transactions']
        }
    
    def get_window_start(self, timestamp):
        """Get the start of the time window for a timestamp"""
        epoch_seconds = int(timestamp.timestamp())
        window_seconds = (epoch_seconds // self.window_size) * self.window_size
        return datetime.fromtimestamp(window_seconds)
```

**Common Window Types:**
- **Tumbling Windows**: Fixed-size, non-overlapping (hourly reports)
- **Sliding Windows**: Fixed-size, overlapping (rolling averages)
- **Session Windows**: Variable-size based on activity gaps

## 🧮 Feature Engineering Pipeline

Feature engineering transforms raw transaction data into ML-ready features for fraud detection.

### Real-time Feature Calculation

```python
class RealTimeFraudFeatures:
    def __init__(self):
        self.redis_client = redis.Redis(host='localhost', port=6379)
    
    def engineer_features(self, transaction):
        """Extract fraud detection features in real-time"""
        user_id = transaction['user_id']
        amount = transaction['amount']
        timestamp = datetime.fromisoformat(transaction['timestamp'])
        
        # Get user profile for historical features
        user_profile = self.get_user_profile(user_id)
        
        # Basic transaction features
        basic_features = {
            'amount': amount,
            'transaction_hour': timestamp.hour,
            'transaction_day': timestamp.weekday(),
            'amount_log': np.log(amount + 1)  # Handle zero amounts
        }
        
        # User behavior features
        behavior_features = self.calculate_behavior_features(
            transaction, user_profile
        )
        
        # Temporal features  
        temporal_features = self.calculate_temporal_features(
            transaction, user_profile
        )
        
        # Risk indicators
        risk_features = self.calculate_risk_indicators(
            transaction, user_profile
        )
        
        return {
            **basic_features,
            **behavior_features, 
            **temporal_features,
            **risk_features
        }
    
    def calculate_behavior_features(self, transaction, user_profile):
        """Calculate user behavior deviation features"""
        amount = transaction['amount']
        avg_amount = user_profile.get('avg_transaction_amount', amount)
        
        return {
            'amount_vs_avg_ratio': amount / max(avg_amount, 1),
            'is_high_amount': amount > avg_amount * 5,
            'daily_transaction_count': user_profile.get('daily_count', 0) + 1,
            'total_transaction_count': user_profile.get('total_transactions', 0) + 1
        }
    
    def calculate_temporal_features(self, transaction, user_profile):
        """Calculate time-based features"""
        current_time = datetime.fromisoformat(transaction['timestamp'])
        last_transaction_time = user_profile.get('last_transaction_time')
        
        if last_transaction_time:
            last_time = datetime.fromisoformat(last_transaction_time)
            time_diff = (current_time - last_time).total_seconds()
        else:
            time_diff = float('inf')
        
        return {
            'time_since_last_transaction': time_diff,
            'is_rapid_transaction': time_diff < 300,  # Less than 5 minutes
            'is_unusual_hour': current_time.hour < 6 or current_time.hour > 22,
            'is_weekend': current_time.weekday() >= 5
        }
    
    def calculate_risk_indicators(self, transaction, user_profile):
        """Calculate high-level risk indicators"""
        amount = transaction['amount']
        avg_amount = user_profile.get('avg_transaction_amount', amount)
        daily_count = user_profile.get('daily_count', 0)
        
        # Velocity scoring (transactions per hour)
        velocity_score = daily_count / 24.0  # Simple daily velocity
        
        return {
            'velocity_score': velocity_score,
            'is_high_velocity': velocity_score > 2.0,
            'amount_vs_last_ratio': amount / max(
                user_profile.get('last_transaction_amount', amount), 1
            )
        }
```

### Feature Store Integration

```python
class FeatureStore:
    """Manage features for both real-time serving and batch training"""
    
    def __init__(self):
        self.redis_client = redis.Redis(host='localhost', port=6379)
        self.feature_ttl = 3600 * 24 * 7  # 7 days
    
    def store_features(self, user_id, transaction_id, features):
        """Store features for model serving and training"""
        feature_key = f"features:{user_id}:{transaction_id}"
        
        # Store individual features  
        self.redis_client.hset(feature_key, mapping=features)
        self.redis_client.expire(feature_key, self.feature_ttl)
        
        # Update user feature aggregates
        self.update_user_aggregates(user_id, features)
    
    def get_user_features(self, user_id):
        """Get real-time features for a user"""
        return self.redis_client.hgetall(f"user_features:{user_id}")
    
    def update_user_aggregates(self, user_id, features):
        """Update rolling user feature aggregates"""
        aggregate_key = f"user_features:{user_id}"
        
        # Update rolling averages, counts, etc.
        pipe = self.redis_client.pipeline()
        pipe.hset(aggregate_key, "last_amount", features['amount'])
        pipe.hset(aggregate_key, "last_transaction_time", 
                  features.get('timestamp', time.time()))
        pipe.expire(aggregate_key, self.feature_ttl)
        pipe.execute()
```

## Fraud Detection Scoring

### Real-time Scoring Pipeline

```python
class RealTimeFraudScorer:
    def __init__(self):
        self.model = self.load_fraud_model()
        self.feature_engineer = RealTimeFraudFeatures()
        self.alert_producer = FraudAlertProducer()
    
    def score_transaction(self, transaction):
        """Score transaction for fraud probability"""
        
        # 1. Engineer features from transaction and user state
        features = self.feature_engineer.engineer_features(transaction)
        
        # 2. Create feature vector for ML model
        feature_vector = self.create_feature_vector(features)
        
        # 3. Score with trained model
        fraud_probability = self.model.predict_proba([feature_vector])[0][1]
        
        # 4. Apply business rules
        final_score = self.apply_business_rules(features, fraud_probability)
        
        # 5. Create fraud result
        fraud_result = {
            'transaction_id': transaction['transaction_id'],
            'user_id': transaction['user_id'],
            'fraud_score': final_score,
            'fraud_probability': fraud_probability,
            'is_fraud': final_score > 0.7,  # Configurable threshold
            'features_used': features,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        # 6. Send alert if fraud detected
        if fraud_result['is_fraud']:
            self.alert_producer.send_fraud_alert(fraud_result)
        
        return fraud_result
    
    def apply_business_rules(self, features, ml_score):
        """Apply business rules on top of ML score"""
        score = ml_score
        
        # Rule 1: High amount transactions are always suspicious
        if features['amount'] > 5000:
            score = max(score, 0.8)
        
        # Rule 2: Rapid transactions are suspicious  
        if features['is_rapid_transaction']:
            score = max(score, 0.6)
        
        # Rule 3: Unusual hour transactions get score boost
        if features['is_unusual_hour']:
            score = min(score + 0.2, 1.0)
        
        # Rule 4: High velocity users get score boost
        if features['velocity_score'] > 5.0:
            score = min(score + 0.3, 1.0)
        
        return score
    
    def create_feature_vector(self, features):
        """Convert feature dictionary to ML model input vector"""
        # This would map to your trained model's feature order
        feature_order = [
            'amount', 'amount_vs_avg_ratio', 'daily_transaction_count',
            'time_since_last_transaction', 'velocity_score', 'transaction_hour'
        ]
        
        return [features.get(feature, 0) for feature in feature_order]
```

### Multi-Model Ensemble

```python
class EnsembleFraudScorer:
    def __init__(self):
        self.models = {
            'lightgbm': self.load_lgb_model(),
            'random_forest': self.load_rf_model(),
            'neural_network': self.load_nn_model()
        }
        self.model_weights = {
            'lightgbm': 0.5,
            'random_forest': 0.3, 
            'neural_network': 0.2
        }
    
    def ensemble_score(self, features):
        """Combine multiple model predictions"""
        scores = {}
        
        for model_name, model in self.models.items():
            try:
                feature_vector = self.prepare_features(features, model_name)
                scores[model_name] = model.predict_proba([feature_vector])[0][1]
            except Exception as e:
                print(f"Model {model_name} failed: {e}")
                scores[model_name] = 0.0
        
        # Weighted ensemble
        ensemble_score = sum(
            scores[model] * self.model_weights[model] 
            for model in scores
        )
        
        return {
            'ensemble_score': ensemble_score,
            'individual_scores': scores,
            'models_used': list(scores.keys())
        }
```

## Performance Optimization

### Stream Processing Optimization

**1. Batch Processing:**
```python
class BatchedFraudDetector:
    def __init__(self, batch_size=100, batch_timeout=1.0):
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.current_batch = []
        self.last_batch_time = time.time()
    
    def process_message(self, message):
        """Add message to batch for processing"""
        self.current_batch.append(message)
        
        # Process batch when size or timeout reached
        if (len(self.current_batch) >= self.batch_size or 
            time.time() - self.last_batch_time > self.batch_timeout):
            self.process_batch()
    
    def process_batch(self):
        """Process entire batch for better throughput"""
        if not self.current_batch:
            return
        
        # Batch feature engineering
        all_features = self.batch_feature_engineering(self.current_batch)
        
        # Batch model scoring
        scores = self.batch_model_scoring(all_features)
        
        # Process results
        for message, features, score in zip(self.current_batch, all_features, scores):
            self.handle_fraud_result(message, features, score)
        
        # Reset batch
        self.current_batch = []
        self.last_batch_time = time.time()
```

**2. Connection Pooling:**
```python
class OptimizedRedisClient:
    def __init__(self, pool_size=10):
        self.pool = redis.ConnectionPool(
            host='localhost', 
            port=6379,
            max_connections=pool_size,
            socket_connect_timeout=1,
            socket_timeout=1,
            retry_on_timeout=True
        )
        self.client = redis.Redis(connection_pool=self.pool)
    
    def pipeline_operations(self, operations):
        """Use Redis pipeline for multiple operations"""
        pipe = self.client.pipeline()
        for op in operations:
            getattr(pipe, op['method'])(*op['args'], **op['kwargs'])
        return pipe.execute()
```

**3. Memory Management:**
```python
class MemoryEfficientProcessor:
    def __init__(self, max_memory_mb=512):
        self.max_memory = max_memory_mb * 1024 * 1024  # Convert to bytes
        self.user_profiles = {}
        
    def get_user_profile(self, user_id):
        """Get user profile with memory management"""
        # Check memory usage
        if self.get_memory_usage() > self.max_memory:
            self.evict_oldest_profiles()
        
        if user_id not in self.user_profiles:
            self.user_profiles[user_id] = self.load_from_redis(user_id)
            
        return self.user_profiles[user_id]
    
    def evict_oldest_profiles(self):
        """LRU eviction of user profiles"""
        # Remove 20% of profiles, keeping most recently used
        sorted_profiles = sorted(
            self.user_profiles.items(),
            key=lambda x: x[1].get('last_access', 0)
        )
        
        evict_count = len(sorted_profiles) // 5
        for user_id, _ in sorted_profiles[:evict_count]:
            del self.user_profiles[user_id]
```

## Monitoring and Debugging

### Stream Processing Metrics

```python
class StreamProcessingMetrics:
    def __init__(self):
        self.metrics = {
            'messages_processed': 0,
            'processing_time_ms': [],
            'fraud_detected': 0,
            'errors': 0,
            'throughput_per_second': 0
        }
        self.start_time = time.time()
    
    def record_processing_time(self, start_time, end_time):
        """Record processing time for a message"""
        processing_time = (end_time - start_time) * 1000  # Convert to ms
        self.metrics['processing_time_ms'].append(processing_time)
        self.metrics['messages_processed'] += 1
        
        # Calculate throughput
        elapsed_seconds = time.time() - self.start_time
        self.metrics['throughput_per_second'] = (
            self.metrics['messages_processed'] / elapsed_seconds
        )
    
    def get_performance_stats(self):
        """Get current performance statistics"""
        processing_times = self.metrics['processing_time_ms']
        
        if processing_times:
            avg_processing_time = sum(processing_times) / len(processing_times)
            p95_processing_time = sorted(processing_times)[int(len(processing_times) * 0.95)]
        else:
            avg_processing_time = p95_processing_time = 0
        
        return {
            'messages_processed': self.metrics['messages_processed'],
            'throughput_per_second': round(self.metrics['throughput_per_second'], 2),
            'avg_processing_time_ms': round(avg_processing_time, 2),
            'p95_processing_time_ms': round(p95_processing_time, 2),
            'fraud_detection_rate': (
                self.metrics['fraud_detected'] / max(self.metrics['messages_processed'], 1)
            ),
            'error_rate': (
                self.metrics['errors'] / max(self.metrics['messages_processed'], 1)
            )
        }
```

### Error Handling Patterns

```python
class RobustStreamProcessor:
    def __init__(self):
        self.dead_letter_producer = Producer({'bootstrap.servers': 'localhost:9092'})
        self.max_retries = 3
        self.retry_delay = 1.0
    
    def process_with_error_handling(self, message):
        """Process message with comprehensive error handling"""
        retry_count = 0
        
        while retry_count < self.max_retries:
            try:
                result = self.process_transaction(message)
                return result
                
            except ValidationError as e:
                # Don't retry validation errors
                self.send_to_dead_letter_queue(message, str(e))
                break
                
            except RedisConnectionError as e:
                # Retry Redis connection errors
                retry_count += 1
                if retry_count >= self.max_retries:
                    self.send_to_dead_letter_queue(message, str(e))
                    break
                time.sleep(self.retry_delay * retry_count)  # Exponential backoff
                
            except Exception as e:
                # Unknown errors - retry with caution
                retry_count += 1
                if retry_count >= self.max_retries:
                    self.send_to_dead_letter_queue(message, str(e))
                    break
                time.sleep(self.retry_delay)
    
    def send_to_dead_letter_queue(self, message, error_reason):
        """Send failed messages to dead letter queue for analysis"""
        dead_letter_message = {
            'original_message': message,
            'error_reason': error_reason,
            'failed_at': datetime.utcnow().isoformat(),
            'retry_count': self.max_retries
        }
        
        self.dead_letter_producer.produce(
            topic='fraud-detection-dlq',
            value=json.dumps(dead_letter_message)
        )
```

## Next Steps

**Advanced Topics to Explore:**
1. **Kafka Streams**: Higher-level stream processing library
2. **Apache Flink**: Advanced stream processing with state management
3. **Real-time Machine Learning**: Online learning and model updates
4. **Complex Event Processing**: Pattern matching across event streams

**Stream-Sentinel Extensions:**
- [State Management Patterns](../state-management/README.md)
- [Machine Learning Integration](../machine-learning/README.md)
- [Alert Response Systems](../alert-response/README.md)

---

Stream processing enables Stream-Sentinel to detect fraud in real-time, demonstrating how continuous data analysis can provide immediate business value while maintaining high performance and reliability.