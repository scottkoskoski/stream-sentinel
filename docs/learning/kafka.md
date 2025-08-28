# Apache Kafka Fundamentals

This guide provides a comprehensive introduction to Apache Kafka concepts through the lens of the Stream-Sentinel fraud detection system. Learn both the theory and practical application of distributed event streaming.

## What is Apache Kafka?

Apache Kafka is a distributed event streaming platform that can handle trillions of events per day. Think of it as a high-performance messaging system that acts as the "nervous system" of real-time applications.

**Real-World Analogy**: Kafka is like a digital mail system for applications. Instead of sending individual emails, applications publish "events" to topics (like mailing lists), and other applications subscribe to topics they're interested in. Unlike email, messages are stored durably and can be replayed.

## 🧠 Core Concepts

### 1. Events and Messages

**What is an Event?**
An event represents something that happened in your system. In fraud detection, events might be:

```json
{
  "event_type": "transaction_created",
  "transaction_id": "tx_12345",
  "user_id": "user_001", 
  "amount": 250.00,
  "timestamp": "2025-08-26T14:30:00Z",
  "merchant": "Amazon",
  "is_fraud": false
}
```

**Message Structure:**
- **Key**: Used for partitioning (e.g., user_id)
- **Value**: The actual event data (JSON, Avro, etc.)
- **Timestamp**: When the event occurred
- **Headers**: Additional metadata

### 2. Topics - Event Categories

**What are Topics?**
Topics are categories or feeds where events are published. Think of them as channels in Slack or folders in your email.

**Stream-Sentinel Topics:**
```
synthetic-transactions   → New transaction events
fraud-alerts            → Detected fraud cases  
user-actions           → Account blocks, investigations
system-metrics         → Performance monitoring events
```

**Topic Configuration:**
```python
# Creating a topic optimized for fraud detection
topic_config = {
    "name": "synthetic-transactions",
    "num_partitions": 12,        # High parallelism
    "replication_factor": 3,     # Data safety (production)
    "retention_ms": 604800000,   # 7 days retention
    "compression_type": "lz4"    # Fast compression
}
```

### 3. Partitions - Parallel Processing

**What are Partitions?**
Partitions split a topic into parallel lanes. Each partition is an ordered, append-only log of events.

```
Topic: synthetic-transactions (12 partitions)

Partition 0: [tx1] → [tx5] → [tx9]  → [tx13] (user_001 transactions)
Partition 1: [tx2] → [tx6] → [tx10] → [tx14] (user_002 transactions)  
Partition 2: [tx3] → [tx7] → [tx11] → [tx15] (user_003 transactions)
...
Partition 11:[tx4] → [tx8] → [tx12] → [tx16] (user_012 transactions)
```

**Why Partitions Matter:**
- **Parallelism**: Process multiple partitions simultaneously
- **Ordering**: Events within a partition maintain order
- **Scalability**: Add partitions to handle more throughput
- **Load Distribution**: Spread load across multiple consumers

**Partitioning Strategy in Stream-Sentinel:**
```python
# Partition by user_id to maintain user transaction order
def get_partition_key(transaction):
    return transaction["user_id"]  # Same user always goes to same partition

# This ensures all transactions for user_001 are processed in order
producer.produce(
    topic="synthetic-transactions",
    key=user_id,                    # Kafka uses this for partitioning
    value=json.dumps(transaction)
)
```

### 4. Producers - Event Publishers

**What are Producers?**
Producers are applications that publish events to Kafka topics. In Stream-Sentinel, the synthetic transaction generator is a producer.

**Producer Workflow:**
```python
from confluent_kafka import Producer

# 1. Create producer with configuration
producer = Producer({
    'bootstrap.servers': 'localhost:9092',
    'linger.ms': 5,              # Wait 5ms to batch messages
    'compression.type': 'lz4',    # Compress for efficiency
    'acks': 'all'                # Wait for all replicas to confirm
})

# 2. Produce a message
def on_delivery(err, msg):
    if err:
        print(f"Failed to deliver message: {err}")
    else:
        print(f"Message delivered to {msg.topic()} [{msg.partition()}] @ offset {msg.offset()}")

# 3. Send message asynchronously
producer.produce(
    topic='synthetic-transactions',
    key=str(user_id),
    value=json.dumps(transaction_data),
    callback=on_delivery
)

# 4. Ensure message is sent
producer.flush()
```

**Producer Configurations Explained:**

| Setting | Purpose | Stream-Sentinel Value |
|---------|---------|----------------------|
| `linger.ms` | How long to wait for batching | 5ms (balance latency/throughput) |
| `acks` | Delivery guarantee | "all" (wait for all replicas) |
| `compression.type` | Message compression | "lz4" (fast compression) |
| `retries` | Retry failed sends | 10 (development) / unlimited (prod) |
| `batch.size` | Maximum batch size | 16KB (optimal for transaction data) |

### 5. Consumers - Event Processors

**What are Consumers?**
Consumers read events from Kafka topics and process them. In Stream-Sentinel, the fraud detector is a consumer.

**Consumer Workflow:**
```python
from confluent_kafka import Consumer

# 1. Create consumer with configuration
consumer = Consumer({
    'bootstrap.servers': 'localhost:9092',
    'group.id': 'fraud-detection-group',    # Consumer group for scaling
    'auto.offset.reset': 'latest',          # Start from newest messages
    'enable.auto.commit': False             # Manual offset management
})

# 2. Subscribe to topics
consumer.subscribe(['synthetic-transactions'])

# 3. Process messages
try:
    while True:
        msg = consumer.poll(timeout=1.0)    # Wait up to 1 second
        
        if msg is None:
            continue
        if msg.error():
            print(f"Consumer error: {msg.error()}")
            continue
            
        # 4. Process the transaction
        transaction = json.loads(msg.value().decode('utf-8'))
        fraud_score = detect_fraud(transaction)
        
        # 5. Manually commit offset after successful processing
        consumer.commit(msg)
        
except KeyboardInterrupt:
    pass
finally:
    consumer.close()
```

### 6. Consumer Groups - Scaling Processing

**What are Consumer Groups?**
Consumer groups allow multiple consumer instances to work together, automatically distributing partitions among group members.

```
Topic: synthetic-transactions (12 partitions)
Consumer Group: fraud-detection-group

Consumer 1: Processes partitions 0, 1, 2, 3
Consumer 2: Processes partitions 4, 5, 6, 7  
Consumer 3: Processes partitions 8, 9, 10, 11

If Consumer 2 fails:
Consumer 1: Processes partitions 0, 1, 2, 3, 4, 5
Consumer 3: Processes partitions 6, 7, 8, 9, 10, 11
```

**Benefits:**
- **Automatic Load Balancing**: Kafka distributes partitions automatically
- **Fault Tolerance**: If a consumer fails, others take over its partitions
- **Horizontal Scaling**: Add more consumers to handle higher throughput
- **At-Least-Once Processing**: Messages are redelivered if processing fails

## Kafka in Stream-Sentinel

### Topic Design for Fraud Detection

```python
# Topic configurations optimized for different data types
TOPICS = {
    "synthetic-transactions": {
        "partitions": 12,           # High concurrency for transaction processing
        "retention_hours": 168,     # 7 days for fraud analysis
        "compression": "lz4",       # Fast processing
        "use_case": "Real-time transaction processing"
    },
    
    "fraud-alerts": {
        "partitions": 6,            # Medium concurrency for alerts
        "retention_hours": 720,     # 30 days for compliance
        "compression": "gzip",      # Better compression for long-term storage
        "cleanup_policy": "compact", # Keep latest alert per user
        "use_case": "Fraud detection results"
    },
    
    "user-actions": {
        "partitions": 3,            # Lower volume data
        "retention_hours": 2160,    # 90 days for audit trail
        "compression": "gzip",      # Optimize for storage
        "use_case": "Account actions and responses"
    }
}
```

### Producer Patterns

**1. High-Throughput Transaction Producer:**
```python
class SyntheticTransactionProducer:
    def __init__(self):
        self.producer = Producer({
            'bootstrap.servers': 'localhost:9092',
            'linger.ms': 5,              # Batch for efficiency
            'compression.type': 'lz4',    # Fast compression
            'batch.size': 16384,         # Optimize batch size
            'buffer.memory': 33554432    # 32MB buffer
        })
    
    def produce_transaction(self, user_id, transaction_data):
        # Use user_id as partition key for ordered processing
        self.producer.produce(
            topic='synthetic-transactions',
            key=str(user_id),
            value=json.dumps(transaction_data),
            callback=self.on_delivery
        )
        
        # Non-blocking flush every 100 messages
        if self.message_count % 100 == 0:
            self.producer.poll(0)  # Handle delivery reports
```

**2. Alert Producer with Guaranteed Delivery:**
```python
class FraudAlertProducer:
    def __init__(self):
        self.producer = Producer({
            'bootstrap.servers': 'localhost:9092',
            'acks': 'all',              # Wait for all replicas
            'retries': 2147483647,      # Retry indefinitely
            'max.in.flight.requests.per.connection': 1,  # Strict ordering
            'enable.idempotence': True   # Exactly-once semantics
        })
    
    def send_fraud_alert(self, alert_data):
        try:
            # Synchronous send for critical alerts
            future = self.producer.produce(
                topic='fraud-alerts',
                key=alert_data['user_id'],
                value=json.dumps(alert_data)
            )
            self.producer.flush(timeout=10)  # Wait up to 10 seconds
            print(f"Alert sent successfully: {alert_data['alert_id']}")
        except Exception as e:
            print(f"Failed to send alert: {e}")
            # Implement retry logic or dead letter queue
```

### Consumer Patterns

**1. Fraud Detection Consumer:**
```python
class FraudDetectionConsumer:
    def __init__(self):
        self.consumer = Consumer({
            'bootstrap.servers': 'localhost:9092',
            'group.id': 'fraud-detection-group',
            'auto.offset.reset': 'latest',      # Process only new transactions
            'enable.auto.commit': False,        # Manual offset management
            'max.poll.records': 1000,          # Process in batches
            'fetch.min.bytes': 1024,           # Reduce network overhead
            'session.timeout.ms': 30000        # 30s timeout for rebalancing
        })
        
        self.redis_client = redis.Redis(host='localhost', port=6379, db=0)
        
    def run(self):
        self.consumer.subscribe(['synthetic-transactions'])
        
        try:
            while True:
                msg = self.consumer.poll(timeout=1.0)
                
                if msg is None:
                    continue
                if msg.error():
                    self.handle_error(msg.error())
                    continue
                
                # Process transaction
                transaction = json.loads(msg.value().decode('utf-8'))
                fraud_result = self.detect_fraud(transaction)
                
                # Update Redis state
                self.update_user_profile(transaction['user_id'], transaction)
                
                # Send alert if fraud detected
                if fraud_result['is_fraud']:
                    self.send_alert(fraud_result)
                
                # Commit offset only after successful processing
                self.consumer.commit(msg)
                
        except KeyboardInterrupt:
            print("Shutting down consumer...")
        finally:
            self.consumer.close()
```

**2. Batch Processing Consumer:**
```python
class BatchAnalyticsConsumer:
    def __init__(self):
        self.consumer = Consumer({
            'bootstrap.servers': 'localhost:9092',
            'group.id': 'analytics-group',
            'auto.offset.reset': 'earliest',    # Process historical data
            'enable.auto.commit': False,
            'max.poll.records': 10000,         # Large batches for analytics
            'fetch.max.wait.ms': 5000          # Wait longer for larger batches
        })
        
    def process_batch(self, messages):
        """Process messages in batches for better performance"""
        transactions = []
        for msg in messages:
            transaction = json.loads(msg.value().decode('utf-8'))
            transactions.append(transaction)
        
        # Batch processing logic
        fraud_statistics = self.analyze_fraud_patterns(transactions)
        self.update_analytics_database(fraud_statistics)
        
        # Commit all offsets at once
        self.consumer.commit()
```

## Performance and Tuning

### Understanding Kafka Performance

**Throughput Factors:**
1. **Batch Size**: Larger batches = higher throughput, higher latency
2. **Compression**: Reduces network/disk usage but increases CPU
3. **Partitions**: More partitions = better parallelism
4. **Replication**: More replicas = better durability, lower performance

**Latency Factors:**
1. **linger.ms**: How long producer waits to batch messages
2. **acks**: How many replicas must confirm before success
3. **Network**: Distance between producers, brokers, and consumers

### Performance Monitoring

**Key Metrics to Track:**
```python
# Producer metrics
producer_metrics = {
    "record-send-rate": "Messages per second",
    "record-send-total": "Total messages sent", 
    "batch-size-avg": "Average batch size",
    "compression-rate-avg": "Compression effectiveness",
    "record-error-rate": "Error rate"
}

# Consumer metrics  
consumer_metrics = {
    "records-consumed-rate": "Messages per second consumed",
    "records-lag-max": "Maximum consumer lag",
    "fetch-rate": "Fetch requests per second",
    "fetch-latency-avg": "Average fetch latency"
}

# Topic metrics (via JMX)
topic_metrics = {
    "messages-in-per-sec": "Incoming message rate",
    "bytes-in-per-sec": "Incoming data rate",
    "log-size": "Total log size on disk",
    "under-replicated-partitions": "Partitions missing replicas"
}
```

### Tuning for Fraud Detection

**Low-Latency Configuration:**
```python
# For real-time fraud detection (minimize latency)
low_latency_config = {
    "producer": {
        "linger.ms": 0,              # Send immediately
        "acks": "1",                 # Wait for leader only
        "compression.type": "lz4",    # Fast compression
        "batch.size": 1000           # Smaller batches
    },
    "consumer": {
        "fetch.min.bytes": 1,        # Don't wait for large batches
        "fetch.max.wait.ms": 10,     # Short wait time
        "max.poll.records": 100      # Process smaller batches
    }
}
```

**High-Throughput Configuration:**
```python
# For analytics processing (maximize throughput)
high_throughput_config = {
    "producer": {
        "linger.ms": 100,            # Wait to batch more messages
        "acks": "all",               # Ensure durability
        "compression.type": "gzip",   # Better compression ratio
        "batch.size": 32768          # Larger batches
    },
    "consumer": {
        "fetch.min.bytes": 32768,    # Wait for larger batches
        "fetch.max.wait.ms": 5000,   # Longer wait time
        "max.poll.records": 10000    # Process large batches
    }
}
```

## Troubleshooting Common Issues

### Consumer Lag

**Problem**: Consumer can't keep up with producer
**Symptoms**: Increasing consumer lag, delayed fraud detection

**Solutions:**
```bash
# 1. Check consumer lag
kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group fraud-detection-group

# 2. Add more consumer instances
# Start additional consumers with same group.id

# 3. Increase consumer throughput
max.poll.records=5000    # Process more messages per poll
fetch.min.bytes=32768    # Wait for larger batches
```

### Producer Timeouts

**Problem**: Producer messages timing out
**Symptoms**: "Expiring 1 record(s)" errors

**Solutions:**
```python
# Increase timeout settings
producer_config = {
    "request.timeout.ms": 60000,     # 60 second timeout
    "delivery.timeout.ms": 120000,   # 2 minute total timeout
    "retries": 10,                   # Retry failed sends
    "retry.backoff.ms": 1000        # Wait 1s between retries
}
```

### Partition Imbalance

**Problem**: Some consumers idle while others overloaded
**Symptoms**: Uneven partition distribution

**Solutions:**
```bash
# 1. Check partition assignment
kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group fraud-detection-group

# 2. Trigger rebalance (restart consumers)
# 3. Consider custom partition assignment strategy

# 4. Review partitioning key strategy
# Ensure keys are evenly distributed across partitions
```

## Best Practices for Fraud Detection

### 1. Topic Design
- Use user_id as partition key to maintain order per user
- Set retention based on compliance requirements (7-90 days)
- Choose partition count based on peak throughput needs

### 2. Producer Patterns
- Use async production with callbacks for performance
- Implement proper error handling and retry logic
- Monitor producer metrics for bottlenecks

### 3. Consumer Patterns  
- Process messages idempotently (handle duplicates gracefully)
- Commit offsets only after successful processing
- Implement proper error handling and dead letter queues

### 4. Error Handling
```python
def process_message_with_retry(message):
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            result = detect_fraud(message)
            return result
        except Exception as e:
            retry_count += 1
            if retry_count >= max_retries:
                # Send to dead letter queue
                send_to_dlq(message, str(e))
                raise
            time.sleep(retry_count * 1000)  # Exponential backoff
```

### 5. Monitoring and Alerting
```python
# Set up alerts for critical metrics
alerts = {
    "consumer_lag > 10000": "High consumer lag detected",
    "error_rate > 0.01": "High error rate in fraud detection",
    "throughput < 1000": "Throughput below minimum threshold"
}
```

## Additional Resources

**Official Documentation:**
- [Apache Kafka Documentation](https://kafka.apache.org/documentation/)
- [Confluent Platform Documentation](https://docs.confluent.io/)

**Stream-Sentinel Specific:**
- [Infrastructure Setup](../infrastructure/README.md)
- [Stream Processing Patterns](../stream-processing/README.md)
- [Project Implementation Logs](../project-logs/README.md)

**Advanced Topics:**
- Kafka Streams for stream processing
- Schema Registry for data governance
- Kafka Connect for data integration
- KSQL for stream analytics

---

Understanding Kafka is crucial for building real-time applications. Stream-Sentinel demonstrates these concepts in practice, showing how theoretical knowledge translates to production-ready fraud detection systems.