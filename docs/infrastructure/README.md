# Infrastructure Architecture

Stream-Sentinel's infrastructure demonstrates production-grade distributed systems patterns using containerized services orchestrated through Docker Compose. This guide explains both the core technologies and the advanced online learning infrastructure that work together to create a resilient, adaptive fraud detection platform.

## Enhanced Architecture Overview

```
                         Enhanced Stream-Sentinel Infrastructure
    
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │                              Docker Host                                        │
    │                                                                                 │
    │  ┌─────────────────────────────────────────────────────────────────────────────┐ │
    │  │                           Docker Network                                    │ │
    │  │                                                                             │ │
    │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │ │
    │  │  │  Zookeeper   │  │    Kafka     │  │Schema Registry│  │  Kafka UI    │     │ │
    │  │  │    :2181     │  │   :9092      │  │    :8081      │  │    :8080     │     │ │
    │  │  │              │  │              │  │              │  │              │     │ │
    │  │  │ Coordination │◄─┤ Message      │◄─┤ Data Format  │  │ Monitoring   │     │ │
    │  │  │ & Metadata   │  │ Streaming    │  │ Evolution    │  │ & Debug      │     │ │
    │  │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘     │ │
    │  │                                                                             │ │
    │  │  ┌─────────────────────────────────────────────────────────────────────┐   │ │
    │  │  │                         Redis Cluster                               │   │ │
    │  │  │                          :6379                                      │   │ │
    │  │  │                                                                     │   │ │
    │  │  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐      │   │ │
    │  │  │  │DB0: User│ │DB1: Main│ │DB2: Feed│ │DB3: Model│ │DB4: Drift│      │   │ │
    │  │  │  │Profiles │ │ State   │ │ back   │ │Registry │ │Detection│      │   │ │
    │  │  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘      │   │ │
    │  │  │                                                                     │   │ │
    │  │  │  ┌─────────────────┐                                               │   │ │
    │  │  │  │ DB5: A/B Tests  │          Redis Insight :8001                  │   │ │
    │  │  │  │ & Experiments   │          Cache Monitor & Debug                │   │ │
    │  │  │  └─────────────────┘                                               │   │ │
    │  │  └─────────────────────────────────────────────────────────────────────┘   │ │
    │  │                                                                             │ │
    │  └─────────────────────────────────────────────────────────────────────────────┘ │
    └─────────────────────────────────────────────────────────────────────────────────┘
```

## 🧰 Technology Stack Deep Dive

### Apache Kafka - Distributed Event Streaming

**What is Kafka?**
Apache Kafka is a distributed event streaming platform that handles real-time data feeds. Think of it as a high-performance messaging system that can handle millions of events per second across multiple servers.

**Why Kafka for Fraud Detection?**
- **High Throughput**: Can process 10k+ transactions per second
- **Durability**: Messages are persisted to disk and replicated
- **Real-time**: Sub-millisecond message delivery
- **Scalability**: Horizontal scaling through partitioning
- **Fault Tolerance**: Automatic failover and data recovery

**How Kafka Works in Stream-Sentinel:**
```python
# Producer (Transaction Generator)
producer.produce(
    topic='synthetic-transactions',
    key=user_id,
    value=transaction_data,
    partition=hash(user_id) % 12  # Distribute across 12 partitions
)

# Consumer (Fraud Detector)
consumer.subscribe(['synthetic-transactions'])
for message in consumer:
    transaction = json.loads(message.value())
    fraud_score = detect_fraud(transaction)
```

**Kafka Configuration in Stream-Sentinel:**
```yaml
# docker-compose.yml excerpt
kafka:
  environment:
    KAFKA_NUM_PARTITIONS: 12        # High parallelism
    KAFKA_COMPRESSION_TYPE: 'lz4'   # Fast compression
    KAFKA_LOG_RETENTION_HOURS: 168  # 7 days retention
    KAFKA_DEFAULT_REPLICATION_FACTOR: 1  # Development setting
```

### Redis - High-Performance State Management

**What is Redis?**
Redis is an in-memory data structure store used as a database, cache, and message broker. It's extremely fast because data lives in RAM rather than on disk.

**Why Redis for Fraud Detection?**
- **Speed**: Sub-millisecond read/write operations
- **Data Structures**: Built-in support for complex data types (hashes, sets, lists)
- **Persistence**: Can save data to disk for durability
- **Memory Efficiency**: Optimized storage with LRU eviction

**How Redis Works in Stream-Sentinel:**
```python
# User Profile Storage
redis_client.hset(
    f"user:{user_id}",
    mapping={
        "total_transactions": 42,
        "avg_amount": 127.50,
        "last_transaction_time": "2025-08-26T14:30:00Z",
        "daily_count": 5
    }
)

# Fast Lookups During Fraud Detection
user_profile = redis_client.hgetall(f"user:{user_id}")
if float(current_amount) > float(user_profile['avg_amount']) * 5:
    fraud_indicators.append("high_amount_vs_average")
```

**Redis Configuration:**
```bash
# Optimized for fraud detection workload
redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru --appendonly yes
```

### Zookeeper - Coordination Service

**What is Zookeeper?**
Apache Zookeeper is a coordination service for distributed applications. It provides configuration management, synchronization, and group services.

**Why Zookeeper for Kafka?**
- **Metadata Management**: Stores topic configurations and broker information
- **Leader Election**: Manages which Kafka broker is the controller
- **Configuration Storage**: Centralized configuration management
- **Service Discovery**: Helps services find and connect to each other

**How Zookeeper Works:**
```
Kafka Broker Registration:
/brokers/ids/1 -> {"host":"kafka","port":9092}

Topic Metadata:
/brokers/topics/synthetic-transactions -> {"partitions":12}

Consumer Groups:
/consumers/fraud-detection-group/offsets/synthetic-transactions/0 -> 1847
```

### Schema Registry - Data Format Evolution

**What is Schema Registry?**
Confluent Schema Registry provides a centralized repository for managing and validating schemas for Kafka messages. It ensures data compatibility as your schemas evolve.

**Why Schema Registry?**
- **Data Quality**: Ensures all messages conform to expected format
- **Evolution**: Safely change message formats without breaking consumers
- **Validation**: Reject malformed messages at production time
- **Documentation**: Self-documenting data contracts

**Schema Evolution Example:**
```json
// Version 1: Basic Transaction
{
  "type": "record",
  "name": "Transaction",
  "fields": [
    {"name": "transaction_id", "type": "string"},
    {"name": "amount", "type": "double"},
    {"name": "timestamp", "type": "long"}
  ]
}

// Version 2: Added User Context (Backward Compatible)
{
  "type": "record", 
  "name": "Transaction",
  "fields": [
    {"name": "transaction_id", "type": "string"},
    {"name": "amount", "type": "double"},
    {"name": "timestamp", "type": "long"},
    {"name": "user_id", "type": ["null", "string"], "default": null}
  ]
}
```

## Docker Compose Orchestration

**Why Docker Compose?**
Docker Compose allows us to define and run multi-container applications with a single configuration file. Perfect for development and testing of distributed systems.

**Key Benefits:**
- **Reproducible Environment**: Same setup across all development machines
- **Service Discovery**: Containers can find each other by name
- **Network Isolation**: Services communicate on private networks
- **Volume Management**: Persistent data storage
- **Health Checks**: Automatic service monitoring

**Service Dependencies:**
```yaml
services:
  zookeeper:
    # Base coordination service
    
  kafka:
    depends_on:
      - zookeeper  # Kafka needs Zookeeper for metadata
      
  schema-registry:
    depends_on:
      - kafka      # Schema Registry stores schemas in Kafka
      
  kafka-ui:
    depends_on:
      - kafka
      - schema-registry  # UI shows both Kafka and Schema Registry data
```

## Monitoring & Observability

### Kafka UI - Stream Processing Dashboard

**Access**: http://localhost:8080

**Key Features:**
- **Topic Management**: Create, configure, and monitor Kafka topics
- **Message Browser**: Inspect individual messages and their content
- **Consumer Groups**: Monitor lag and partition assignment
- **Schema Registry Integration**: View and manage message schemas
- **Performance Metrics**: Throughput, error rates, and partition distribution

**Fraud Detection Monitoring:**
```
Topics to Monitor:
├── synthetic-transactions (12 partitions)
├── fraud-alerts (6 partitions) 
└── user-actions (3 partitions)

Key Metrics:
├── Messages per second (target: 10k+)
├── Consumer lag (target: <1s)
└── Error rate (target: <0.1%)
```

### Redis Insight - State Management Dashboard

**Access**: http://localhost:8001

**Key Features:**
- **Memory Usage**: Track Redis memory consumption and key distribution
- **Command Execution**: Run Redis commands directly in the web interface
- **Key Browser**: Inspect user profiles and cached data
- **Performance Monitoring**: Track operations per second and latency

**User Profile Monitoring:**
```
Key Patterns:
├── user:{user_id} -> Hash with transaction statistics
├── daily:{user_id}:{date} -> Daily transaction counts
└── alerts:{user_id} -> List of recent fraud alerts

Memory Usage:
├── Total: ~100MB (500 users * ~200KB each)
├── Eviction: LRU policy when memory limit reached
└── Persistence: AOF for crash recovery
```

## Configuration Management

### Environment-Aware Configuration

Stream-Sentinel uses a sophisticated configuration system that adapts to different environments:

```python
# src/kafka/config.py
class Environment(Enum):
    DEVELOPMENT = "development"  # Local Docker setup
    STAGING = "staging"          # Cloud testing environment  
    PRODUCTION = "production"    # Live trading environment

def get_kafka_config(environment):
    if environment == Environment.DEVELOPMENT:
        return {
            "bootstrap.servers": "localhost:9092",
            "acks": "1",  # Faster development feedback
            "retries": 10
        }
    elif environment == Environment.PRODUCTION:
        return {
            "bootstrap.servers": "kafka-1:9092,kafka-2:9092,kafka-3:9092",
            "acks": "all",  # Maximum durability
            "retries": 2147483647,
            "security.protocol": "SASL_SSL"
        }
```

### Performance Tuning

**Kafka Optimizations:**
```yaml
# High throughput producer settings
KAFKA_LINGER_MS: 5                    # Batch messages for efficiency
KAFKA_COMPRESSION_TYPE: 'lz4'         # Fast compression algorithm
KAFKA_BATCH_SIZE: 16384               # Optimal batch size for fraud data

# Consumer optimization
KAFKA_FETCH_MIN_BYTES: 1024          # Minimize network overhead
KAFKA_MAX_POLL_RECORDS: 1000         # Process messages in batches
```

**Redis Optimizations:**
```bash
# Memory management
maxmemory 512mb                       # Limit memory usage
maxmemory-policy allkeys-lru          # Evict oldest keys when full

# Persistence  
appendonly yes                        # Enable AOF for durability
auto-aof-rewrite-percentage 100       # Rewrite AOF when it doubles
```

## Health Monitoring & Diagnostics

### Service Health Checks

Each service includes health checks to ensure proper operation:

```yaml
# Kafka health check
healthcheck:
  test: ["CMD", "kafka-broker-api-versions", "--bootstrap-server", "localhost:9092"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 60s

# Redis health check  
healthcheck:
  test: ["CMD", "redis-cli", "ping"]
  interval: 30s
  timeout: 10s
  retries: 3
```

### Troubleshooting Common Issues

**Kafka Connection Issues:**
```bash
# Check if Kafka is accessible
kafka-broker-api-versions --bootstrap-server localhost:9092

# Verify topic exists
kafka-topics --bootstrap-server localhost:9092 --list

# Check consumer group status
kafka-consumer-groups --bootstrap-server localhost:9092 --describe --group fraud-detection-group
```

**Redis Connection Issues:**
```bash
# Test Redis connectivity
redis-cli -h localhost -p 6379 ping

# Check memory usage
redis-cli -h localhost -p 6379 info memory

# Monitor commands in real-time
redis-cli -h localhost -p 6379 monitor
```

## Getting Started

### Prerequisites Verification

```bash
# Check Docker installation
docker --version          # Should be 20.0+
docker-compose --version  # Should be 1.25+

# Check available resources
docker system df          # Available disk space
free -h                   # Available memory (8GB+ recommended)
```

### Infrastructure Startup

```bash
# Start all services
cd docker && docker-compose up -d

# Verify all services are healthy
docker-compose ps

# Check logs if any service fails
docker-compose logs kafka
docker-compose logs redis
```

### Validation Steps

```bash
# Test Kafka connectivity
python src/kafka/test_connectivity.py

# Expected output:
# All Kafka connectivity tests PASSED!
# Stream-Sentinel is ready for fraud detection pipeline development.
```

## Performance Characteristics

### Throughput Benchmarks

| Component | Development | Production Target |
|-----------|-------------|-------------------|
| Kafka Messages/sec | 10,000+ | 100,000+ |
| Redis Operations/sec | 100,000+ | 1,000,000+ |
| Total Latency | <100ms | <10ms |
| Memory Usage | ~1GB | ~8GB |

### Scaling Considerations

**Horizontal Scaling:**
- Add more Kafka partitions for higher parallelism
- Deploy multiple Redis instances with sharding
- Use multiple consumer instances for load distribution

**Vertical Scaling:**
- Increase memory for larger user profile cache
- Add CPU cores for higher message throughput
- Use SSD storage for faster Kafka log writes

---

This infrastructure forms the foundation for Stream-Sentinel's fraud detection capabilities, providing the reliability, performance, and scalability needed for production financial transaction processing.