# Data Persistence Architecture

Stream-Sentinel implements a sophisticated hybrid data persistence architecture that separates OLTP (Online Transaction Processing) and OLAP (Online Analytical Processing) workloads for optimal performance and analytical capabilities.

## Architecture Overview

The system uses a two-database approach designed for different data access patterns:

- **PostgreSQL**: ACID-compliant transactional storage for critical fraud detection data
- **ClickHouse**: High-performance columnar storage optimized for time-series analytics

This hybrid approach provides both immediate transactional integrity and long-term analytical capabilities without compromising real-time fraud detection performance.

## Design Principles

### Separation of Concerns
- **OLTP Workloads**: User accounts, fraud alerts, audit logs, model performance tracking
- **OLAP Workloads**: Transaction records, feature data, detection results, performance metrics
- **Asynchronous Processing**: Zero impact on real-time fraud detection latency

### Performance Optimization
- **Connection Pooling**: Efficient database connectivity with automatic resource management
- **Batch Processing**: Optimized bulk operations for high-throughput scenarios
- **Partitioning**: Time-based data organization for optimal query performance
- **TTL Policies**: Automated data lifecycle management

### Operational Excellence  
- **Health Monitoring**: Built-in database health checks and performance metrics
- **Error Handling**: Comprehensive error recovery and graceful degradation
- **Audit Trails**: Complete compliance-ready logging for investigations
- **Scalability**: Designed for horizontal scaling and high-throughput workloads

## Database Schemas

### PostgreSQL (OLTP)

**Critical Transactional Data:**

```sql
-- Fraud alerts requiring investigation
fraud_alerts (
    alert_id UUID PRIMARY KEY,
    transaction_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    severity VARCHAR(20) CHECK (severity IN ('MINIMAL', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    fraud_score DECIMAL(5,4) CHECK (fraud_score >= 0 AND fraud_score <= 1),
    business_rules_triggered TEXT[],
    explanation JSONB,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- User account management
user_accounts (
    user_id VARCHAR(255) PRIMARY KEY,
    status VARCHAR(20) DEFAULT 'ACTIVE',
    total_alerts INTEGER DEFAULT 0,
    high_severity_alerts INTEGER DEFAULT 0,
    last_alert_at TIMESTAMP WITH TIME ZONE,
    blocked_at TIMESTAMP WITH TIME ZONE
);

-- Compliance audit logging
system_audit_log (
    event_type VARCHAR(100) NOT NULL,
    entity_type VARCHAR(100) NOT NULL,
    entity_id VARCHAR(255) NOT NULL,
    action VARCHAR(100) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    details JSONB
);
```

### ClickHouse (OLAP)

**High-Volume Analytics Data:**

```sql
-- All processed transactions for analysis
transaction_records (
    transaction_id String,
    user_id String,
    timestamp DateTime64(3),
    amount Decimal64(4),
    fraud_score Float64,
    is_fraud UInt8,
    processing_time_ms UInt32
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (user_id, timestamp)
TTL timestamp + INTERVAL 2 YEAR;

-- Real-time engineered features
fraud_features (
    transaction_id String,
    feature_name String,
    feature_value Float64,
    feature_category String,
    timestamp DateTime64(3)
) ENGINE = MergeTree()
PARTITION BY (toYYYYMM(timestamp), feature_category)
TTL timestamp + INTERVAL 1 YEAR;

-- ML model predictions and business rules
detection_results (
    transaction_id String,
    ml_prediction Float64,
    ml_confidence Float64,
    business_rules_score Float64,
    final_fraud_score Float64,
    business_rules_triggered Array(String)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
TTL timestamp + INTERVAL 2 YEAR;
```

## Data Flow Architecture

```
Real-Time Processing Pipeline → Asynchronous Persistence Pipeline

┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│ Fraud Detection │    │   Kafka Topics   │    │   Persistence   │
│    Consumer     │───▶│                  │───▶│    Consumer     │
│                 │    │ • fraud-results  │    │                 │
│ (Redis State)   │    │ • performance-   │    │ (Batch Proc.)   │
│ (Sub-100ms)     │    │   metrics        │    │                 │
└─────────────────┘    │ • audit-events   │    └─────────────────┘
                       └──────────────────┘             │
                                                         ▼
┌─────────────────┐                           ┌─────────────────┐
│   PostgreSQL    │◀──────────────────────────┤  Dual Database  │
│                 │                           │   Write Logic   │
│ • ACID Integrity│                           │                 │
│ • Investigations│                           │ • Route OLTP    │
│ • User Accounts │                           │ • Route OLAP    │
│ • Audit Trails  │                           │ • Error Handle  │
└─────────────────┘                           └─────────────────┘
                                                         │
┌─────────────────┐                                      │
│   ClickHouse    │◀─────────────────────────────────────┘
│                 │
│ • Time Series   │
│ • Analytics     │  
│ • ML Features   │
│ • Bulk Inserts  │
└─────────────────┘
```

## Persistence Consumer Implementation

The persistence consumer handles asynchronous data ingestion without impacting real-time performance:

### Batch Processing Strategy
```python
class PersistenceConsumer:
    def __init__(self):
        self.batch_size = 100
        self.batch_timeout_ms = 1000
        self.current_batch = []
    
    def _process_batch(self):
        # Group messages by topic for efficient processing
        grouped_messages = {}
        for msg_data in self.current_batch:
            topic = msg_data['topic']
            if topic not in grouped_messages:
                grouped_messages[topic] = []
            grouped_messages[topic].append(msg_data)
        
        # Process each topic group
        for topic, messages in grouped_messages.items():
            self._process_topic_batch(topic, messages)
```

### Database Routing Logic
```python
def _process_fraud_detection_batch(self, messages):
    for msg_data in messages:
        # Parse transaction and fraud detection data
        transaction = TransactionRecord(...)
        alert = FraudAlert(...) if high_severity else None
        
        # Route to appropriate storage
        if alert:
            # High-severity → Both databases
            self.persistence_layer.persist_fraud_detection_result(
                transaction=transaction,
                alert=alert,
                features=features,
                detection_metadata=metadata
            )
        else:
            # Low-severity → ClickHouse only
            self.persistence_layer.clickhouse.insert_transaction_record(
                transaction
            )
```

## Performance Characteristics

### PostgreSQL Performance
- **Connection Pooling**: 5-20 connections with automatic cleanup
- **Query Optimization**: Comprehensive indexing on all query paths
- **Transaction Management**: ACID compliance with automatic rollback
- **Typical Latency**: 1-5ms for single operations

### ClickHouse Performance
- **Batch Processing**: 1000+ records per batch operation
- **Compression**: LZ4 compression for storage efficiency
- **Partitioning**: Monthly partitions for time-series optimization
- **Typical Throughput**: 100k+ inserts per second

### Overall System Impact
- **Real-Time Latency**: Zero impact on fraud detection (asynchronous)
- **Data Durability**: ACID transactions for critical alerts
- **Analytics Performance**: Sub-second queries on millions of records
- **Storage Efficiency**: Optimized compression and TTL policies

## Monitoring and Observability

### Database Health Monitoring
```python
def health_check(self) -> Dict[str, bool]:
    return {
        'postgresql': self.postgres.health_check(),
        'clickhouse': self.clickhouse.health_check()
    }

def get_performance_metrics(self) -> Dict[str, Any]:
    return {
        'postgresql': {
            'query_count': self._query_count,
            'average_query_time': self._avg_query_time,
            'connection_pool_stats': self.get_connection_pool_stats()
        },
        'clickhouse': {
            'total_rows_inserted': self._total_rows_inserted,
            'batch_processing_rate': self._batch_rate
        }
    }
```

### Key Metrics
- **PostgreSQL**: Query latency, connection pool utilization, transaction throughput
- **ClickHouse**: Insert throughput, compression ratios, query performance
- **Consumer**: Batch processing rates, error rates, lag metrics
- **System**: Memory usage, disk I/O, network throughput

## Operational Procedures

### Database Maintenance
- **PostgreSQL**: Automatic VACUUM, index maintenance, connection pool monitoring
- **ClickHouse**: Automatic merges, TTL cleanup, partition optimization
- **Backup Strategy**: Point-in-time recovery for PostgreSQL, distributed backups for ClickHouse

### Troubleshooting
- **Connection Issues**: Pool exhaustion detection and automatic recovery
- **Performance Degradation**: Query plan analysis and index optimization
- **Data Consistency**: Cross-database validation and reconciliation procedures

### Scaling Considerations
- **Horizontal Scaling**: Multiple persistence consumers with partition awareness
- **Vertical Scaling**: Connection pool sizing and memory optimization
- **Storage Scaling**: Partition management and data archival strategies

## Integration with Fraud Detection

### Data Publishing from Fraud Detector
```python
def publish_fraud_detection_result(self, features, transaction, processing_start_time):
    detection_result = {
        "transaction": self._extract_transaction_data(transaction),
        "is_fraud": features.is_fraud_alert,
        "fraud_score": features.fraud_score,
        "severity": self._calculate_severity(features.fraud_score),
        "business_rules_triggered": self._get_triggered_rules(features),
        "features": features.to_dict(),
        "processing_time_ms": processing_time_ms,
        "detection_metadata": self._build_metadata(features)
    }
    
    self.producer.produce(
        "fraud-detection-results",
        value=json.dumps(detection_result)
    )
```

### Severity-Based Routing
- **CRITICAL/HIGH**: Immediate PostgreSQL alert creation + ClickHouse analytics
- **MEDIUM**: PostgreSQL alert creation + ClickHouse analytics  
- **LOW/MINIMAL**: ClickHouse analytics only (no investigation required)

## Data Analytics Capabilities

### Materialized Views
ClickHouse provides pre-computed analytics views for common queries:

```sql
-- Hourly fraud rate aggregation
CREATE MATERIALIZED VIEW fraud_rate_hourly AS
SELECT
    toStartOfHour(timestamp) as hour,
    user_id,
    countState() as transaction_count,
    sumState(is_fraud) as fraud_count,
    avgState(fraud_score) as avg_fraud_score
FROM transaction_records
GROUP BY hour, user_id;

-- Model performance tracking  
CREATE MATERIALIZED VIEW model_accuracy_hourly AS
SELECT
    toStartOfHour(timestamp) as hour,
    ml_model_version,
    avgState(ml_prediction) as avg_prediction,
    sumState(true_positives) as tp_count,
    sumState(false_positives) as fp_count
FROM detection_results
GROUP BY hour, ml_model_version;
```

### Business Intelligence Queries
- **Fraud Trends**: Time-series analysis of fraud patterns
- **User Behavior**: Cohort analysis and behavioral segmentation  
- **Model Performance**: Accuracy metrics and drift detection
- **Operational Metrics**: System throughput and error analysis

## Security and Compliance

### Data Protection
- **Encryption**: TLS for all database connections
- **Access Control**: Role-based permissions and connection limits
- **Audit Logging**: Complete audit trail for all data access

### Compliance Features
- **Data Retention**: Configurable TTL policies for regulatory compliance
- **Audit Trails**: Immutable logs for fraud investigations
- **Data Anonymization**: PII handling for analytics workloads
- **Backup/Recovery**: Point-in-time recovery for critical data

## Future Enhancements

### Planned Improvements
- **Stream Processing**: Real-time materialized views with Kafka Connect
- **Data Lake Integration**: Long-term storage with Apache Parquet
- **Advanced Analytics**: Machine learning feature stores and model serving
- **Multi-Region**: Geographic distribution for global deployment

### Scalability Roadmap
- **Read Replicas**: Query load distribution for PostgreSQL
- **Sharding**: Horizontal partitioning for massive scale
- **Caching**: Redis integration for frequently accessed data
- **API Gateway**: RESTful access to persistence layer

This persistence architecture provides Stream-Sentinel with enterprise-grade data management capabilities while maintaining the high-performance real-time fraud detection that is core to the system's value proposition.