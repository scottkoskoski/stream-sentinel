# Redis for Stream Processing

Redis plays a crucial role in Stream-Sentinel as the high-performance state store for user profiles, feature caching, and real-time lookups. This guide explains Redis fundamentals and how it enables sub-millisecond fraud detection.

## 🚀 What is Redis?

Redis (Remote Dictionary Server) is an in-memory data structure store that functions as a database, cache, and message broker. It's designed for speed, storing data in RAM for microsecond-level response times.

**Why Redis for Fraud Detection?**
- **Speed**: Sub-millisecond read/write operations
- **Rich Data Types**: Hashes, sets, lists, sorted sets beyond simple key-value
- **Atomic Operations**: Race-condition-free updates for financial data
- **Persistence**: Optional disk persistence for durability
- **Scalability**: Built-in clustering and replication

**Redis vs Traditional Databases:**

| Feature | Traditional DB | Redis |
|---------|---------------|-------|
| Storage | Disk-based | Memory-based |
| Query Time | 10-100ms | <1ms |
| Data Types | Tables/Rows | Rich data structures |
| Transactions | ACID | Atomic commands |
| Scaling | Vertical | Horizontal clustering |

## 🗂️ Redis Data Types in Fraud Detection

### 1. Hashes - User Profiles

**What are Hashes?**
Hashes are field-value pairs, perfect for storing structured data like user profiles.

```redis
# Redis hash structure
HSET user:001 
  total_transactions 42
  avg_amount 127.50
  last_transaction_time "2025-08-26T14:30:00Z"
  daily_count 5
  fraud_alerts 0
```

**Python Implementation:**
```python
import redis
import json
from datetime import datetime

class UserProfileManager:
    def __init__(self):
        self.redis_client = redis.Redis(
            host='localhost', 
            port=6379, 
            db=0,
            decode_responses=True  # Automatically decode bytes to strings
        )
    
    def get_user_profile(self, user_id):
        """Get user profile as dictionary"""
        profile = self.redis_client.hgetall(f"user:{user_id}")
        
        if not profile:
            # Create new user profile
            profile = self.create_new_user_profile(user_id)
        else:
            # Convert string values to appropriate types
            profile = self.parse_user_profile(profile)
        
        return profile
    
    def create_new_user_profile(self, user_id):
        """Initialize new user profile"""
        profile = {
            'user_id': user_id,
            'total_transactions': 0,
            'total_amount': 0.0,
            'avg_transaction_amount': 0.0,
            'last_transaction_time': None,
            'daily_count': 0,
            'daily_amount': 0.0,
            'last_reset_date': None,
            'fraud_alerts': 0
        }
        
        # Store in Redis
        self.redis_client.hset(f"user:{user_id}", mapping=profile)
        self.redis_client.expire(f"user:{user_id}", 3600 * 24 * 7)  # 7 day TTL
        
        return profile
    
    def update_user_profile(self, user_id, transaction):
        """Atomically update user profile with new transaction"""
        key = f"user:{user_id}"
        amount = float(transaction['amount'])
        timestamp = transaction['timestamp']
        
        # Use Redis pipeline for atomic updates
        pipe = self.redis_client.pipeline()
        
        # Increment counters
        pipe.hincrby(key, 'total_transactions', 1)
        pipe.hincrbyfloat(key, 'total_amount', amount)
        pipe.hincrby(key, 'daily_count', 1)
        pipe.hincrbyfloat(key, 'daily_amount', amount)
        
        # Update last transaction info
        pipe.hset(key, 'last_transaction_time', timestamp)
        pipe.hset(key, 'last_transaction_amount', amount)
        
        # Execute all commands atomically
        results = pipe.execute()
        
        # Calculate and update average (requires separate transaction)
        total_transactions = results[0]  # Result from hincrby
        total_amount = results[1]        # Result from hincrbyfloat
        avg_amount = total_amount / total_transactions
        
        self.redis_client.hset(key, 'avg_transaction_amount', avg_amount)
        
        return {
            'total_transactions': total_transactions,
            'total_amount': total_amount,
            'avg_transaction_amount': avg_amount
        }
```

### 2. Sets - Unique Collections

**Use Case:** Track unique merchants, IP addresses, or device IDs per user.

```python
class UserActivityTracker:
    def __init__(self):
        self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    
    def track_merchant_activity(self, user_id, merchant):
        """Track unique merchants per user"""
        key = f"user:{user_id}:merchants"
        
        # Add merchant to user's set
        self.redis_client.sadd(key, merchant)
        
        # Set expiration (reset daily)
        self.redis_client.expire(key, 3600 * 24)
        
        # Get merchant count for fraud scoring
        merchant_count = self.redis_client.scard(key)
        
        return {
            'unique_merchants_today': merchant_count,
            'is_new_merchant': self.redis_client.sadd(f"user:{user_id}:all_merchants", merchant) == 1
        }
    
    def get_merchant_diversity_score(self, user_id):
        """Calculate merchant diversity for fraud detection"""
        daily_merchants = self.redis_client.scard(f"user:{user_id}:merchants")
        all_merchants = self.redis_client.scard(f"user:{user_id}:all_merchants")
        
        if all_merchants == 0:
            return 0.0
        
        return daily_merchants / all_merchants  # High score = unusual diversity
```

### 3. Sorted Sets - Time-Series Data

**Use Case:** Maintain transaction history with timestamps for pattern analysis.

```python
class TransactionTimelineManager:
    def __init__(self):
        self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    
    def add_transaction_to_timeline(self, user_id, transaction):
        """Add transaction to user's timeline"""
        key = f"user:{user_id}:timeline"
        timestamp = datetime.fromisoformat(transaction['timestamp']).timestamp()
        
        # Store transaction as JSON with timestamp as score
        transaction_data = json.dumps({
            'transaction_id': transaction['transaction_id'],
            'amount': transaction['amount'],
            'merchant': transaction.get('merchant', 'unknown')
        })
        
        # Add to sorted set (automatically sorted by timestamp)
        self.redis_client.zadd(key, {transaction_data: timestamp})
        
        # Keep only last 100 transactions
        self.redis_client.zremrangebyrank(key, 0, -101)
        
        return True
    
    def get_recent_transactions(self, user_id, count=10):
        """Get user's most recent transactions"""
        key = f"user:{user_id}:timeline"
        
        # Get last N transactions (highest scores = most recent)
        recent = self.redis_client.zrevrange(key, 0, count-1, withscores=True)
        
        transactions = []
        for transaction_json, timestamp in recent:
            transaction = json.loads(transaction_json)
            transaction['timestamp'] = datetime.fromtimestamp(timestamp).isoformat()
            transactions.append(transaction)
        
        return transactions
    
    def calculate_transaction_velocity(self, user_id, time_window_minutes=60):
        """Calculate transaction velocity in given time window"""
        key = f"user:{user_id}:timeline"
        
        # Get current time and window start
        now = datetime.now().timestamp()
        window_start = now - (time_window_minutes * 60)
        
        # Count transactions in time window
        count = self.redis_client.zcount(key, window_start, now)
        
        return {
            'transactions_in_window': count,
            'transactions_per_hour': (count / time_window_minutes) * 60,
            'is_high_velocity': count > 10  # More than 10 transactions in window
        }
```

### 4. Lists - Transaction Queues

**Use Case:** Maintain FIFO queues for processing or alerting.

```python
class FraudAlertQueue:
    def __init__(self):
        self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    
    def queue_fraud_alert(self, alert_data):
        """Add fraud alert to processing queue"""
        alert_json = json.dumps(alert_data)
        
        # Push to right side of list (FIFO queue)
        self.redis_client.rpush('fraud_alerts:pending', alert_json)
        
        # Also add to user-specific alert history
        user_id = alert_data['user_id']
        self.redis_client.lpush(f'user:{user_id}:alerts', alert_json)
        
        # Keep only last 50 alerts per user
        self.redis_client.ltrim(f'user:{user_id}:alerts', 0, 49)
        
        return True
    
    def process_next_alert(self):
        """Get next alert from queue for processing"""
        # Pop from left side (FIFO)
        alert_json = self.redis_client.lpop('fraud_alerts:pending')
        
        if alert_json:
            return json.loads(alert_json)
        return None
    
    def get_queue_size(self):
        """Get current queue size"""
        return self.redis_client.llen('fraud_alerts:pending')
```

## ⚡ Performance Optimization

### Connection Pooling

**Problem:** Creating new Redis connections is expensive.
**Solution:** Use connection pools for better performance.

```python
import redis.connection

class OptimizedRedisManager:
    def __init__(self, max_connections=20):
        # Create connection pool
        self.pool = redis.ConnectionPool(
            host='localhost',
            port=6379,
            max_connections=max_connections,
            socket_connect_timeout=5,
            socket_timeout=5,
            socket_keepalive=True,
            socket_keepalive_options={},
            retry_on_timeout=True
        )
        
        # Create Redis client using pool
        self.redis_client = redis.Redis(
            connection_pool=self.pool,
            decode_responses=True
        )
    
    def health_check(self):
        """Check Redis connection health"""
        try:
            return self.redis_client.ping()
        except redis.ConnectionError:
            return False
```

### Pipelining for Batch Operations

**Problem:** Multiple Redis commands create network round-trips.
**Solution:** Use pipelines to batch commands.

```python
class BatchUserUpdater:
    def __init__(self):
        self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    
    def batch_update_users(self, user_transactions):
        """Update multiple users in a single pipeline"""
        pipe = self.redis_client.pipeline()
        
        for user_id, transactions in user_transactions.items():
            key = f"user:{user_id}"
            
            # Add all operations to pipeline
            for transaction in transactions:
                amount = float(transaction['amount'])
                pipe.hincrby(key, 'total_transactions', 1)
                pipe.hincrbyfloat(key, 'total_amount', amount)
                pipe.hset(key, 'last_transaction_time', transaction['timestamp'])
        
        # Execute all operations in one network round-trip
        results = pipe.execute()
        
        return len(results)
    
    def parallel_user_lookups(self, user_ids):
        """Look up multiple users in parallel"""
        pipe = self.redis_client.pipeline()
        
        # Queue all lookups
        for user_id in user_ids:
            pipe.hgetall(f"user:{user_id}")
        
        # Execute all lookups
        results = pipe.execute()
        
        # Combine results with user IDs
        user_profiles = {}
        for user_id, profile_data in zip(user_ids, results):
            user_profiles[user_id] = profile_data
        
        return user_profiles
```

### Memory Optimization

**Problem:** Redis memory usage can grow large with user profiles.
**Solution:** Implement TTL and memory-efficient data structures.

```python
class MemoryOptimizedProfileManager:
    def __init__(self):
        self.redis_client = redis.Redis(host='localhost', port=6379)
    
    def create_user_profile_with_ttl(self, user_id, ttl_days=7):
        """Create user profile with automatic expiration"""
        profile = {
            'created': datetime.now().isoformat(),
            'total_transactions': 0,
            'total_amount': 0.0
        }
        
        key = f"user:{user_id}"
        
        # Set profile data
        self.redis_client.hset(key, mapping=profile)
        
        # Set TTL (time to live) 
        self.redis_client.expire(key, ttl_days * 24 * 3600)
        
        return profile
    
    def compress_old_transaction_data(self, user_id):
        """Compress old transaction data to save memory"""
        timeline_key = f"user:{user_id}:timeline"
        
        # Keep only last 50 transactions
        self.redis_client.zremrangebyrank(timeline_key, 0, -51)
        
        # Compress merchant data (keep only top 10)
        merchants_key = f"user:{user_id}:merchants"
        merchant_count = self.redis_client.scard(merchants_key)
        
        if merchant_count > 10:
            # Convert to sorted set by frequency, keep top 10
            merchants = self.redis_client.smembers(merchants_key)
            # This would require additional logic to track merchant frequency
            
        return True
    
    def get_memory_usage_info(self):
        """Get Redis memory usage information"""
        info = self.redis_client.info('memory')
        
        return {
            'used_memory_human': info['used_memory_human'],
            'used_memory_peak_human': info['used_memory_peak_human'],
            'mem_fragmentation_ratio': info['mem_fragmentation_ratio'],
            'keyspace_hits': info.get('keyspace_hits', 0),
            'keyspace_misses': info.get('keyspace_misses', 0)
        }
```

## 🔐 Redis Security and Reliability

### Authentication and Security

```python
class SecureRedisManager:
    def __init__(self, password=None):
        self.redis_client = redis.Redis(
            host='localhost',
            port=6379,
            password=password,  # Set Redis AUTH password
            ssl=False,          # Enable SSL for production
            decode_responses=True
        )
    
    def setup_security(self):
        """Configure Redis security settings"""
        # In production, these would be set in redis.conf
        security_config = {
            'requirepass': 'strong-password-here',
            'bind': '127.0.0.1',  # Only local connections
            'protected-mode': 'yes',
            'port': 0,            # Disable standard port
            'unixsocket': '/tmp/redis.sock',  # Use Unix socket
            'unixsocketperm': 700
        }
        
        return security_config
```

### Persistence and Backup

```python
class RedisBackupManager:
    def __init__(self):
        self.redis_client = redis.Redis(host='localhost', port=6379)
    
    def configure_persistence(self):
        """Configure Redis persistence options"""
        # AOF (Append Only File) for better durability
        self.redis_client.config_set('appendonly', 'yes')
        self.redis_client.config_set('appendfsync', 'everysec')  # Sync every second
        
        # RDB snapshots for point-in-time backups
        self.redis_client.config_set('save', '900 1 300 10 60 10000')  # Save conditions
        
        return True
    
    def create_backup(self):
        """Create manual backup"""
        # Trigger RDB snapshot
        self.redis_client.bgsave()
        
        # Check backup status
        last_save = self.redis_client.lastsave()
        return {
            'backup_triggered': True,
            'last_save_time': last_save
        }
    
    def get_persistence_info(self):
        """Get current persistence settings"""
        info = self.redis_client.info('persistence')
        
        return {
            'aof_enabled': info.get('aof_enabled', 0) == 1,
            'rdb_bgsave_in_progress': info.get('rdb_bgsave_in_progress', 0) == 1,
            'last_save_time': info.get('rdb_last_save_time', 0),
            'changes_since_last_save': info.get('rdb_changes_since_last_save', 0)
        }
```

## 📊 Monitoring and Debugging

### Redis Monitoring

```python
class RedisMonitor:
    def __init__(self):
        self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    
    def get_performance_metrics(self):
        """Get comprehensive Redis performance metrics"""
        info = self.redis_client.info()
        
        return {
            # Connection metrics
            'connected_clients': info['connected_clients'],
            'total_connections_received': info['total_connections_received'],
            
            # Command metrics
            'total_commands_processed': info['total_commands_processed'],
            'instantaneous_ops_per_sec': info['instantaneous_ops_per_sec'],
            
            # Memory metrics
            'used_memory': info['used_memory'],
            'used_memory_human': info['used_memory_human'],
            'mem_fragmentation_ratio': info['mem_fragmentation_ratio'],
            
            # Keyspace metrics
            'keyspace_hits': info.get('keyspace_hits', 0),
            'keyspace_misses': info.get('keyspace_misses', 0),
            'keyspace_hit_rate': self.calculate_hit_rate(info),
            
            # Replication metrics (if using replication)
            'role': info['role'],
            'connected_slaves': info.get('connected_slaves', 0)
        }
    
    def calculate_hit_rate(self, info):
        """Calculate cache hit rate"""
        hits = info.get('keyspace_hits', 0)
        misses = info.get('keyspace_misses', 0)
        total = hits + misses
        
        if total == 0:
            return 0.0
        
        return (hits / total) * 100
    
    def get_slow_log(self, count=10):
        """Get slow query log"""
        return self.redis_client.slowlog_get(count)
    
    def monitor_commands(self, duration=10):
        """Monitor Redis commands for debugging"""
        # This would typically be done with redis-cli monitor
        # Here's a Python approximation
        pubsub = self.redis_client.pubsub()
        
        # Note: Real monitoring would use MONITOR command
        # This is a simplified version for demonstration
        print(f"Monitoring Redis for {duration} seconds...")
        
        import time
        start_time = time.time()
        commands = []
        
        while time.time() - start_time < duration:
            # In real implementation, capture MONITOR output
            time.sleep(0.1)
        
        return commands
```

### Debugging Common Issues

```python
class RedisDebugger:
    def __init__(self):
        self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    
    def diagnose_memory_issues(self):
        """Diagnose memory-related problems"""
        info = self.redis_client.info('memory')
        
        issues = []
        
        # Check memory usage
        if info['used_memory'] > 500 * 1024 * 1024:  # 500MB threshold
            issues.append("High memory usage detected")
        
        # Check fragmentation
        if info['mem_fragmentation_ratio'] > 1.5:
            issues.append("High memory fragmentation")
        
        # Check for memory peaks
        if info['used_memory_peak'] > info['used_memory'] * 2:
            issues.append("Memory usage has large peaks")
        
        return {
            'issues': issues,
            'memory_info': info,
            'recommendations': self.get_memory_recommendations(issues)
        }
    
    def get_memory_recommendations(self, issues):
        """Get recommendations for memory issues"""
        recommendations = []
        
        if "High memory usage detected" in issues:
            recommendations.append("Consider setting maxmemory and eviction policy")
            recommendations.append("Review data TTL settings")
        
        if "High memory fragmentation" in issues:
            recommendations.append("Consider restarting Redis to defragment memory")
            recommendations.append("Review data structure usage patterns")
        
        return recommendations
    
    def check_key_patterns(self, pattern="*", sample_size=100):
        """Analyze key patterns for optimization opportunities"""
        keys = self.redis_client.keys(pattern)
        
        if len(keys) > sample_size:
            # Sample random keys to avoid performance issues
            import random
            keys = random.sample(keys, sample_size)
        
        key_analysis = {
            'total_keys': len(keys),
            'key_types': {},
            'memory_usage': {},
            'ttl_analysis': {'with_ttl': 0, 'without_ttl': 0}
        }
        
        for key in keys:
            # Get key type
            key_type = self.redis_client.type(key)
            key_analysis['key_types'][key_type] = key_analysis['key_types'].get(key_type, 0) + 1
            
            # Get memory usage (approximate)
            try:
                memory = self.redis_client.memory_usage(key)
                key_analysis['memory_usage'][key] = memory
            except:
                pass  # Not all Redis versions support MEMORY USAGE
            
            # Check TTL
            ttl = self.redis_client.ttl(key)
            if ttl > 0:
                key_analysis['ttl_analysis']['with_ttl'] += 1
            else:
                key_analysis['ttl_analysis']['without_ttl'] += 1
        
        return key_analysis
```

## 🚀 Redis in Stream-Sentinel Implementation

### Complete User Profile Management

```python
class StreamSentinelRedisManager:
    """Complete Redis management for Stream-Sentinel fraud detection"""
    
    def __init__(self, redis_config=None):
        config = redis_config or {
            'host': 'localhost',
            'port': 6379,
            'db': 0,
            'max_connections': 20,
            'socket_timeout': 5,
            'socket_connect_timeout': 5,
            'retry_on_timeout': True
        }
        
        # Create connection pool
        self.pool = redis.ConnectionPool(**config)
        self.redis_client = redis.Redis(
            connection_pool=self.pool,
            decode_responses=True
        )
        
        # Key prefixes for organization
        self.USER_PREFIX = "user"
        self.ALERT_PREFIX = "alert"
        self.TIMELINE_PREFIX = "timeline"
        self.MERCHANT_PREFIX = "merchant"
    
    def initialize_user_profile(self, user_id):
        """Initialize new user profile with all required fields"""
        profile = {
            'user_id': user_id,
            'created_at': datetime.utcnow().isoformat(),
            'total_transactions': 0,
            'total_amount': 0.0,
            'avg_transaction_amount': 0.0,
            'last_transaction_time': None,
            'last_transaction_amount': 0.0,
            'daily_transaction_count': 0,
            'daily_amount': 0.0,
            'last_reset_date': None,
            'suspicious_activity_count': 0,
            'fraud_score_avg': 0.0,
            'max_single_transaction': 0.0
        }
        
        key = f"{self.USER_PREFIX}:{user_id}"
        
        # Use pipeline for atomic creation
        pipe = self.redis_client.pipeline()
        pipe.hset(key, mapping=profile)
        pipe.expire(key, 7 * 24 * 3600)  # 7 day TTL
        pipe.execute()
        
        return profile
    
    def update_user_with_transaction(self, user_id, transaction):
        """Comprehensive user profile update with transaction"""
        key = f"{self.USER_PREFIX}:{user_id}"
        amount = float(transaction['amount'])
        timestamp = transaction['timestamp']
        
        # Check if daily reset is needed
        current_date = datetime.fromisoformat(timestamp).date().isoformat()
        
        pipe = self.redis_client.pipeline()
        
        # Get current profile for calculations
        current_profile = self.redis_client.hgetall(key) or {}
        last_reset_date = current_profile.get('last_reset_date')
        
        if last_reset_date != current_date:
            # Reset daily counters
            pipe.hset(key, 'daily_transaction_count', 0)
            pipe.hset(key, 'daily_amount', 0.0)
            pipe.hset(key, 'last_reset_date', current_date)
        
        # Update transaction counters
        pipe.hincrby(key, 'total_transactions', 1)
        pipe.hincrbyfloat(key, 'total_amount', amount)
        pipe.hincrby(key, 'daily_transaction_count', 1)
        pipe.hincrbyfloat(key, 'daily_amount', amount)
        
        # Update transaction details
        pipe.hset(key, 'last_transaction_time', timestamp)
        pipe.hset(key, 'last_transaction_amount', amount)
        
        # Update max transaction if needed
        current_max = float(current_profile.get('max_single_transaction', 0))
        if amount > current_max:
            pipe.hset(key, 'max_single_transaction', amount)
        
        # Execute updates
        results = pipe.execute()
        
        # Calculate average amount (requires separate command after increments)
        total_transactions = int(current_profile.get('total_transactions', 0)) + 1
        total_amount = float(current_profile.get('total_amount', 0)) + amount
        avg_amount = total_amount / total_transactions
        
        self.redis_client.hset(key, 'avg_transaction_amount', avg_amount)
        
        # Add to transaction timeline
        self.add_to_timeline(user_id, transaction)
        
        return {
            'updated': True,
            'total_transactions': total_transactions,
            'avg_amount': avg_amount,
            'daily_count': int(current_profile.get('daily_transaction_count', 0)) + 1
        }
    
    def add_to_timeline(self, user_id, transaction):
        """Add transaction to user timeline"""
        timeline_key = f"{self.USER_PREFIX}:{user_id}:{self.TIMELINE_PREFIX}"
        timestamp = datetime.fromisoformat(transaction['timestamp']).timestamp()
        
        transaction_data = json.dumps({
            'transaction_id': transaction['transaction_id'],
            'amount': transaction['amount'],
            'merchant': transaction.get('merchant', 'unknown'),
            'fraud_score': transaction.get('fraud_score', 0.0)
        })
        
        pipe = self.redis_client.pipeline()
        pipe.zadd(timeline_key, {transaction_data: timestamp})
        pipe.zremrangebyrank(timeline_key, 0, -101)  # Keep last 100
        pipe.expire(timeline_key, 7 * 24 * 3600)  # 7 day TTL
        pipe.execute()
    
    def get_fraud_features_for_user(self, user_id):
        """Get all fraud detection features for a user"""
        key = f"{self.USER_PREFIX}:{user_id}"
        profile = self.redis_client.hgetall(key)
        
        if not profile:
            return self.initialize_user_profile(user_id)
        
        # Convert string values to appropriate types
        return {
            'user_id': profile['user_id'],
            'total_transactions': int(profile.get('total_transactions', 0)),
            'avg_transaction_amount': float(profile.get('avg_transaction_amount', 0)),
            'daily_transaction_count': int(profile.get('daily_transaction_count', 0)),
            'daily_amount': float(profile.get('daily_amount', 0)),
            'last_transaction_amount': float(profile.get('last_transaction_amount', 0)),
            'suspicious_activity_count': int(profile.get('suspicious_activity_count', 0)),
            'max_single_transaction': float(profile.get('max_single_transaction', 0)),
            'last_transaction_time': profile.get('last_transaction_time')
        }
    
    def record_fraud_alert(self, user_id, fraud_data):
        """Record fraud alert for user"""
        user_key = f"{self.USER_PREFIX}:{user_id}"
        alert_key = f"{self.ALERT_PREFIX}:{fraud_data['alert_id']}"
        
        pipe = self.redis_client.pipeline()
        
        # Increment user's suspicious activity count
        pipe.hincrby(user_key, 'suspicious_activity_count', 1)
        
        # Store full alert data
        pipe.hset(alert_key, mapping=fraud_data)
        pipe.expire(alert_key, 30 * 24 * 3600)  # 30 day retention
        
        # Add to user's alert timeline
        user_alerts_key = f"{self.USER_PREFIX}:{user_id}:alerts"
        alert_timestamp = datetime.utcnow().timestamp()
        pipe.zadd(user_alerts_key, {fraud_data['alert_id']: alert_timestamp})
        pipe.zremrangebyrank(user_alerts_key, 0, -51)  # Keep last 50
        
        pipe.execute()
        
        return True
    
    def get_performance_stats(self):
        """Get Redis performance statistics for monitoring"""
        info = self.redis_client.info()
        
        return {
            'memory_usage_mb': round(info['used_memory'] / (1024 * 1024), 2),
            'ops_per_second': info['instantaneous_ops_per_sec'],
            'connected_clients': info['connected_clients'],
            'hit_rate': self.calculate_hit_rate(),
            'key_count': info.get('db0', {}).get('keys', 0) if 'db0' in info else 0,
            'uptime_seconds': info['uptime_in_seconds']
        }
    
    def calculate_hit_rate(self):
        """Calculate cache hit rate percentage"""
        info = self.redis_client.info('stats')
        hits = info.get('keyspace_hits', 0)
        misses = info.get('keyspace_misses', 0)
        total = hits + misses
        
        return round((hits / total * 100), 2) if total > 0 else 0
```

## 📚 Best Practices Summary

1. **Use Connection Pools**: Avoid connection overhead
2. **Implement TTLs**: Prevent memory bloat with expiration
3. **Use Pipelines**: Batch operations for better performance
4. **Choose Right Data Types**: Use hashes for objects, sets for uniqueness
5. **Monitor Memory Usage**: Track fragmentation and usage patterns
6. **Implement Proper Error Handling**: Handle connection failures gracefully
7. **Use Atomic Operations**: Prevent race conditions with MULTI/EXEC
8. **Plan for Persistence**: Configure AOF and RDB based on durability needs

## 📚 Next Steps

**Advanced Redis Topics:**
- Redis Clustering for horizontal scaling
- Redis Sentinel for high availability  
- Lua scripting for complex atomic operations
- Redis modules for specialized functionality

**Stream-Sentinel Integration:**
- [Stream Processing Patterns](../stream-processing/README.md)
- [Fraud Detection Implementation](../fraud-detection/README.md)
- [Infrastructure Overview](../infrastructure/README.md)

---

Redis provides the high-performance state management foundation that makes Stream-Sentinel's real-time fraud detection possible, demonstrating how in-memory data stores enable microsecond-latency financial applications.