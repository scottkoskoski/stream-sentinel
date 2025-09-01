# Infrastructure Architecture

Stream-Sentinel's infrastructure demonstrates production-grade distributed systems patterns using containerized services orchestrated through Docker Compose. The system provides a complete data processing pipeline with message streaming, real-time state management, and dual-database persistence for both transactional integrity and analytical queries.

## Architecture Overview

```
                         Stream-Sentinel Infrastructure Architecture
    
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │                              Docker Host Network                                │
    │                                                                                 │
    │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
    │  │  Zookeeper   │  │    Kafka     │  │Schema Registry│  │  Kafka UI    │       │
    │  │    :2181     │  │   :9092      │  │    :8081      │  │    :8080     │       │
    │  │              │  │              │  │              │  │              │       │
    │  │ Coordination │◄─┤ Message      │◄─┤ Data Format  │  │ Monitoring   │       │
    │  │ & Metadata   │  │ Streaming    │  │ Evolution    │  │ & Debug      │       │
    │  │              │  │ 6 Partitions │  │              │  │              │       │
    │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘       │
    │                                                                                 │
    │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
    │  │    Redis     │  │ Redis Insight│  │ PostgreSQL   │  │  ClickHouse  │       │
    │  │    :6379     │  │    :8001     │  │    :5432     │  │ :8123/:9000  │       │
    │  │              │  │              │  │              │  │              │       │
    │  │ User State   │  │ Cache        │  │ OLTP         │  │ OLAP         │       │
    │  │ Management   │  │ Monitoring   │  │ Fraud Alerts │  │ Analytics    │       │
    │  │ 512MB LRU    │  │ & Debug      │  │ User Mgmt    │  │ Time-Series  │       │
    │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘       │
    └─────────────────────────────────────────────────────────────────────────────────┘
```

## Core Technology Stack

### Apache Kafka - Distributed Event Streaming

**What is Kafka?**
Apache Kafka is a distributed event streaming platform that handles real-time data feeds. It acts as a high-performance messaging system capable of processing thousands of events per second with fault tolerance and durability.

**Why Kafka for Fraud Detection?**
- **High Throughput**: Processes 1,000+ transactions per second validated
- **Durability**: Messages are persisted to disk and replicated
- **Real-time**: Sub-millisecond message delivery within the cluster
- **Scalability**: Horizontal scaling through partitioning (6 partitions configured)
- **Fault Tolerance**: Automatic failover and data recovery

**Stream-Sentinel Kafka Configuration:**
```yaml
# Core Kafka settings from docker-compose.yml
KAFKA_NUM_PARTITIONS: 6                 # Optimized for development
KAFKA_COMPRESSION_TYPE: 'lz4'           # Fast compression
KAFKA_LOG_RETENTION_HOURS: 168          # 7 days retention
KAFKA_DEFAULT_REPLICATION_FACTOR: 1     # Single-node development
KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
```

**Topic Architecture:**
```python
# Primary data flow topics
topics = {
    'synthetic-transactions': {
        'partitions': 6,
        'purpose': 'IEEE-CIS format transaction data',
        'retention': '7 days'
    },
    'fraud-alerts': {
        'partitions': 6, 
        'purpose': 'High-priority fraud detections',
        'retention': '7 days'
    },
    'fraud-detection-results': {
        'partitions': 6,
        'purpose': 'Complete detection results for persistence',
        'retention': '7 days'
    },
    'performance-metrics': {
        'partitions': 3,
        'purpose': 'System performance monitoring',
        'retention': '3 days'
    }
}
```

### Redis - High-Performance State Management

**What is Redis?**
Redis is an in-memory data structure store used for real-time state management. Stream-Sentinel uses Redis to maintain user behavioral profiles that are updated with each transaction for velocity-based fraud detection.

**Why Redis for Fraud Detection?**
- **Speed**: Sub-millisecond read/write operations
- **Data Structures**: Built-in support for hashes, perfect for user profiles  
- **Persistence**: AOF (Append Only File) enabled for crash recovery
- **Memory Management**: LRU eviction with 512MB memory limit
- **Atomic Operations**: Safe concurrent updates from multiple consumers

**User Profile Storage Pattern:**
```python
# User profile structure in Redis
user_profile = {
    f"user_profile:{user_id}": {
        "user_id": str(user_id),
        "total_transactions": 42,
        "total_amount": 5247.50,
        "avg_transaction_amount": 125.0,
        "last_transaction_time": "2025-01-15T14:30:00Z",
        "last_transaction_amount": 89.99,
        "daily_transaction_count": 3,
        "daily_amount": 275.48,
        "last_reset_date": "2025-01-15",
        "suspicious_activity_count": 0
    }
}

# Redis configuration
redis_config = {
    "maxmemory": "512mb",
    "maxmemory-policy": "allkeys-lru",
    "appendonly": "yes",  # Persistence enabled
    "auto-aof-rewrite-percentage": 100
}
```

### PostgreSQL - ACID-Compliant Transactional Storage

**What is PostgreSQL?**
PostgreSQL serves as the OLTP (Online Transaction Processing) database for Stream-Sentinel, storing critical data that requires ACID compliance and immediate consistency.

**PostgreSQL Configuration:**
```yaml
# Production-optimized PostgreSQL settings
POSTGRES_DB: stream_sentinel
POSTGRES_USER: stream_sentinel_user
max_connections: 200
shared_buffers: 256MB
effective_cache_size: 1GB
maintenance_work_mem: 64MB
```

**OLTP Data Storage:**
- **Fraud Alerts**: High-severity fraud detections requiring investigation
- **User Accounts**: Account status, blocking actions, investigation history
- **Audit Logs**: Compliance-ready audit trails for regulatory requirements
- **Model Performance**: ML model accuracy tracking and performance metrics

### ClickHouse - High-Performance Analytics Database

**What is ClickHouse?**
ClickHouse serves as the OLAP (Online Analytical Processing) database, optimized for time-series analytics and high-volume transaction data storage.

**ClickHouse Configuration:**
```yaml
# Analytics-optimized ClickHouse settings
CLICKHOUSE_DB: stream_sentinel
CLICKHOUSE_USER: stream_sentinel_user
ulimits:
  nofile: 262144  # Handle high connection counts
```

**OLAP Data Storage:**
- **Transaction Records**: All processed transactions with fraud scores
- **Feature Data**: ML features and engineered attributes
- **Detection Results**: Complete fraud detection results and metadata
- **Performance Metrics**: System throughput and latency measurements

### Schema Registry - Data Format Evolution

**What is Schema Registry?**
Confluent Schema Registry provides centralized schema management for Kafka messages, ensuring data quality and enabling safe schema evolution without breaking consumers.

**Schema Evolution Example:**
```json
// Current Transaction Schema (Version 1)
{
  "type": "record",
  "name": "Transaction", 
  "fields": [
    {"name": "transaction_id", "type": "string"},
    {"name": "card1", "type": "long"},
    {"name": "transaction_amt", "type": "double"},
    {"name": "generated_timestamp", "type": "string"},
    {"name": "product_cd", "type": ["null", "string"], "default": null}
  ]
}
```

### Zookeeper - Coordination Service

**What is Zookeeper?**
Apache Zookeeper coordinates the Kafka cluster, managing broker metadata, topic configurations, and consumer group coordination.

**Zookeeper Responsibilities:**
- **Broker Registration**: Maintains list of available Kafka brokers
- **Topic Metadata**: Stores partition assignments and configurations
- **Consumer Coordination**: Manages consumer group membership and offset tracking
- **Leader Election**: Elects partition leaders for high availability

## Docker Compose Service Architecture

### Service Dependencies

```yaml
# Service startup order and dependencies
services:
  zookeeper:          # Foundation service
    # No dependencies
    
  kafka:              # Core messaging
    depends_on:
      - zookeeper
      
  schema-registry:    # Schema management  
    depends_on:
      - kafka
      
  kafka-ui:           # Monitoring dashboard
    depends_on:
      - kafka
      - schema-registry
      
  redis:              # State management
    # No dependencies - independent service
    
  redis-insight:      # Redis monitoring
    depends_on:
      - redis
      
  postgres:           # OLTP database
    # No dependencies - independent service
    
  clickhouse:         # OLAP database  
    # No dependencies - independent service
```

### Health Monitoring

Each service includes comprehensive health checks:

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

# PostgreSQL health check  
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U stream_sentinel_user -d stream_sentinel"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 30s

# ClickHouse health check
healthcheck:
  test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost:8123/ping"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 30s
```

## Monitoring & Observability

### Kafka UI - Stream Processing Dashboard

**Access**: http://localhost:8080

**Key Features:**
- **Topic Management**: Monitor message throughput and partition distribution
- **Message Browser**: Inspect transaction content and fraud detection results  
- **Consumer Groups**: Track processing lag and partition assignments
- **Schema Registry**: View message format evolution and compatibility
- **Performance Metrics**: Real-time throughput and error rate monitoring

**Fraud Detection Monitoring:**
```bash
# Key topics to monitor
synthetic-transactions     # Input transaction stream
fraud-alerts               # High-priority fraud detections  
fraud-detection-results    # Complete results for persistence
performance-metrics        # System health and performance

# Key metrics
- Messages/second: Target 1,000+ sustained
- Consumer lag: Target <5 seconds  
- Error rate: Target <0.1%
- Partition distribution: Balanced load across 6 partitions
```

### Redis Insight - State Management Dashboard

**Access**: http://localhost:8001

**Key Features:**
- **Memory Usage**: Track Redis memory consumption and key distribution
- **Key Browser**: Inspect user profiles and behavioral state
- **Command Interface**: Execute Redis commands directly in web UI
- **Real-time Monitoring**: Operations per second and latency tracking

**User Profile Monitoring:**
```bash
# Key patterns in Redis
user_profile:{user_id}     # User behavioral profiles (TTL: 30 days)

# Memory monitoring
Total Memory: ~100MB (estimated for 10,000 active users)
Key Count: ~10,000 user profiles  
Eviction Policy: LRU when memory limit reached
Persistence: AOF enabled for crash recovery
```

## Configuration Management

### Environment-Aware Configuration

Stream-Sentinel uses sophisticated configuration management that adapts to different deployment environments:

```python
# src/kafka/config.py
class Environment(Enum):
    DEVELOPMENT = "development"  # Local Docker setup
    STAGING = "staging"          # Cloud testing environment
    PRODUCTION = "production"    # Live deployment

def get_kafka_config(environment):
    if environment == Environment.DEVELOPMENT:
        return {
            "bootstrap.servers": "localhost:9092",
            "acks": "1",             # Faster development feedback
            "retries": 10,
            "linger.ms": 5,          # Small batching for low latency
            "compression.type": "lz4"
        }
    elif environment == Environment.PRODUCTION:
        return {
            "bootstrap.servers": "kafka-1:9092,kafka-2:9092,kafka-3:9092", 
            "acks": "all",           # Maximum durability
            "retries": 2147483647,   # Retry indefinitely
            "enable.idempotence": True,
            "security.protocol": "SASL_SSL"
        }
```

### Performance Tuning

**Kafka Optimizations:**
```yaml
# Producer performance tuning
KAFKA_LINGER_MS: 5                    # Small batch latency
KAFKA_COMPRESSION_TYPE: 'lz4'         # Fast compression
KAFKA_BATCH_SIZE: 16384               # Optimal for fraud detection data

# Consumer performance tuning  
KAFKA_FETCH_MIN_BYTES: 1024          # Minimize network overhead
KAFKA_MAX_POLL_RECORDS: 500          # Balanced throughput/latency
```

**Redis Optimizations:**
```bash
# Memory management for user profiles
maxmemory 512mb                       # Sufficient for development workload
maxmemory-policy allkeys-lru          # Evict least recently used profiles

# Persistence for state recovery
appendonly yes                        # Enable AOF persistence
auto-aof-rewrite-percentage 100       # Rewrite when AOF doubles in size
```

## Deployment and Operations

### Infrastructure Startup

```bash
# Complete infrastructure startup
cd docker && docker-compose up -d

# Verify all services are healthy  
docker-compose ps

# Expected output: All services should show "Up" status
stream-sentinel-zookeeper     Up (healthy)
stream-sentinel-kafka         Up (healthy)  
stream-sentinel-schema-registry Up (healthy)
stream-sentinel-kafka-ui      Up (healthy)
stream-sentinel-redis         Up (healthy)
stream-sentinel-redis-insight Up
stream-sentinel-postgres      Up (healthy)
stream-sentinel-clickhouse    Up (healthy)
```

### Validation and Testing

```bash
# Test Kafka connectivity and topic operations
cd src/kafka && python test_connectivity.py

# Expected output:
# ✅ All Kafka connectivity tests PASSED!
# ✅ Schema Registry connectivity verified
# ✅ Topic creation and message handling verified
# Stream-Sentinel infrastructure ready for fraud detection

# Test database connectivity
docker exec stream-sentinel-postgres psql -U stream_sentinel_user -d stream_sentinel -c "SELECT version();"
docker exec stream-sentinel-clickhouse clickhouse-client --query "SELECT version()"

# Test Redis state management
redis-cli -h localhost -p 6379 ping
# Expected: PONG
```

### Troubleshooting

**Common Issues and Solutions:**

```bash
# Kafka connection issues
kafka-broker-api-versions --bootstrap-server localhost:9092

# Redis memory issues  
redis-cli -h localhost -p 6379 info memory

# PostgreSQL connection issues
docker exec stream-sentinel-postgres pg_isready -U stream_sentinel_user -d stream_sentinel

# ClickHouse connection issues
curl http://localhost:8123/ping
```

## Performance Characteristics

### Measured Performance Benchmarks

| Component | Development | Production Target |
|-----------|-------------|-------------------|
| Kafka Messages/sec | 1,000+ (validated) | 10,000+ |
| Redis Operations/sec | 10,000+ | 100,000+ |
| Transaction Processing | <100ms (measured) | <50ms |
| Total System Memory | ~2GB | ~8GB |

### Scaling Recommendations

**Horizontal Scaling:**
- **Kafka**: Increase partitions from 6 to 12+ for higher parallelism
- **Consumer Instances**: Deploy multiple fraud detection consumers
- **Database Sharding**: Distribute user profiles across Redis instances
- **Load Balancing**: Use multiple Kafka brokers for production workloads

**Vertical Scaling:**
- **Memory**: Increase Redis memory limit for larger user profile cache  
- **CPU**: Add cores for higher message processing throughput
- **Storage**: Use SSD for Kafka log persistence and database performance

### Resource Requirements

**Development Environment:**
```bash
# Minimum system requirements
CPU: 4 cores minimum, 8 cores recommended
Memory: 8GB minimum, 16GB recommended  
Storage: 10GB for containers and data
Network: Localhost (no external network required)

# Container resource allocation
Kafka + Zookeeper: ~1GB memory
Redis: 512MB memory (configured limit)
Databases: ~1GB memory combined  
Monitoring: ~500MB memory
```

**Production Environment:**
```bash
# Recommended production resources
CPU: 16+ cores for high-throughput processing
Memory: 32GB+ for larger state cache and buffering
Storage: SSD with 100GB+ for data persistence
Network: Gigabit for inter-service communication

# Production scaling targets
Throughput: 10,000+ TPS sustained
Latency: <50ms end-to-end processing
Availability: 99.9% uptime with failover
Data Retention: 30 days Kafka, unlimited databases
```

## Integration Points

### Data Flow Architecture

```bash
# Complete data processing pipeline
Synthetic Producer → Kafka Topics → Fraud Detector → State Management (Redis)
                                  ↓
                  Alert Processor → Database Persistence (PostgreSQL + ClickHouse)
                                  ↓  
                  Performance Monitoring → Metrics Topics → Monitoring Dashboards
```

### External Integration

**Database Connections:**
- **PostgreSQL**: `postgresql://stream_sentinel_user:stream_sentinel_password@localhost:5432/stream_sentinel`
- **ClickHouse**: `http://stream_sentinel_user:stream_sentinel_password@localhost:8123/stream_sentinel`
- **Redis**: `redis://localhost:6379/0` (no authentication in development)

**Monitoring Endpoints:**
- **Kafka UI**: http://localhost:8080 (topic management and monitoring)
- **Redis Insight**: http://localhost:8001 (state management monitoring)
- **Schema Registry**: http://localhost:8081 (schema management API)

---

**Navigation:** [← Documentation Index](../README.md) | [Configuration →](../../src/kafka/config.py) | [Docker Compose →](../../docker/docker-compose.yml)

*This infrastructure provides the robust, scalable foundation for Stream-Sentinel's production-grade fraud detection system, demonstrating modern distributed systems patterns with containerized microservices architecture.*