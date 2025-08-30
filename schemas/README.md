# Stream-Sentinel Schema Management

This directory contains the Avro schema definitions for Stream-Sentinel's data contracts, ensuring type safety and compatibility across all system components.

## Schema Architecture

### Core Data Contracts

- **`transaction.avsc`**: Raw financial transaction records
- **`fraud_score.avsc`**: ML model fraud predictions and risk assessments  
- **`fraud_alert.avsc`**: High-severity alerts requiring immediate action

### Schema Evolution Strategy

Stream-Sentinel follows **BACKWARD compatibility** for all schema evolution:

- ✅ **Allowed Changes**: Add optional fields, remove optional fields, add enum values
- ❌ **Forbidden Changes**: Remove required fields, change field types, rename fields

## Schema Registry Integration

### Configuration

```bash
# Schema Registry URL
SCHEMA_REGISTRY_URL=http://localhost:8081

# Compatibility mode for all subjects
COMPATIBILITY_MODE=BACKWARD
```

### Topic-Schema Mapping

| Kafka Topic | Schema File | Schema Subject |
|-------------|-------------|----------------|
| `synthetic-transactions` | `transaction.avsc` | `synthetic-transactions-value` |
| `fraud-scores` | `fraud_score.avsc` | `fraud-scores-value` |  
| `fraud-alerts` | `fraud_alert.avsc` | `fraud-alerts-value` |

## Schema Validation

### Automated Validation Rules

- **Documentation**: All fields must include `doc` attributes
- **Naming**: Use `snake_case` for field names
- **Defaults**: Optional fields must have default values
- **Namespacing**: All schemas use `com.streamsent.fraud` namespace

### CI/CD Integration

```bash
# Validate schema syntax
python scripts/validate_schemas.py

# Check compatibility with existing versions
python scripts/check_compatibility.py

# Register new schema versions
python scripts/register_schemas.py
```

## Producer/Consumer Integration

### Python Producer Example

```python
from confluent_kafka import avro
from confluent_kafka.avro import AvroProducer

# Configure Avro producer
avro_producer = AvroProducer({
    'bootstrap.servers': 'localhost:9092',
    'schema.registry.url': 'http://localhost:8081'
}, default_value_schema=load_schema('transaction.avsc'))

# Send transaction with schema validation
transaction = {
    'transaction_id': 'txn_12345',
    'user_id': 'user_001', 
    'amount': 150.75,
    'timestamp': int(time.time() * 1000),
    'card_type': 'CREDIT',
    'transaction_type': 'PURCHASE'
}

avro_producer.produce('synthetic-transactions', transaction)
```

### Python Consumer Example

```python
from confluent_kafka.avro import AvroConsumer

# Configure Avro consumer  
avro_consumer = AvroConsumer({
    'bootstrap.servers': 'localhost:9092',
    'schema.registry.url': 'http://localhost:8081',
    'group.id': 'fraud-detection-group',
    'auto.offset.reset': 'earliest'
})

avro_consumer.subscribe(['synthetic-transactions'])

# Consume with automatic schema validation
for message in avro_consumer:
    transaction = message.value()  # Automatically deserialized
    print(f"Processing transaction {transaction['transaction_id']}")
```

## Schema Design Principles

### 1. Forward/Backward Compatibility

All schemas support evolution without breaking existing producers/consumers:

```json
{
  "name": "new_optional_field",
  "type": ["null", "string"], 
  "default": null,
  "doc": "New field added in v1.1 - optional for compatibility"
}
```

### 2. Rich Type System

Leverage Avro's type system for data validation:

```json
{
  "name": "risk_level",
  "type": {
    "type": "enum",
    "name": "RiskLevel",
    "symbols": ["MINIMAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
  },
  "doc": "Enum ensures only valid risk levels"
}
```

### 3. Logical Types

Use logical types for semantic clarity:

```json
{
  "name": "timestamp",
  "type": "long",
  "logicalType": "timestamp-millis",
  "doc": "Timestamp with millisecond precision"
}
```

### 4. Comprehensive Documentation

Every field includes documentation for maintainability:

```json
{
  "name": "fraud_score", 
  "type": "double",
  "doc": "ML model fraud probability score (0.0-1.0)"
}
```

## Performance Considerations

### Schema Registry Caching

- **Producer**: Cache schemas in memory to avoid repeated registry calls
- **Consumer**: Enable schema caching for high-throughput scenarios
- **Registry**: Configure appropriate cache TTL settings

### Binary Encoding Benefits

Avro binary encoding provides:

- **Compact Size**: ~40% smaller than JSON for typical fraud detection payloads
- **Fast Serialization**: Pre-compiled schemas eliminate parsing overhead
- **Type Safety**: Runtime type validation prevents data corruption

## Development Workflow

### 1. Schema Development

```bash
# Create/modify schema files
edit schemas/fraud_score.avsc

# Validate syntax
python scripts/validate_schemas.py fraud_score.avsc
```

### 2. Compatibility Testing  

```bash
# Test against existing data
python scripts/test_compatibility.py fraud_score.avsc v1.0.0

# Run regression tests
python -m pytest tests/schema/test_fraud_score_compatibility.py
```

### 3. Schema Registration

```bash
# Register new version
python scripts/register_schemas.py fraud_score.avsc

# Verify registration
curl http://localhost:8081/subjects/fraud-scores-value/versions
```

## Monitoring & Observability

### Schema Registry Metrics

- **Registration Rate**: New schema versions registered per hour
- **Compatibility Violations**: Failed compatibility checks  
- **Subject Growth**: Number of schema subjects over time

### Application Metrics

- **Serialization Latency**: Time to serialize/deserialize messages
- **Schema Cache Hit Rate**: Registry cache performance
- **Validation Errors**: Schema validation failures per topic

## Troubleshooting

### Common Issues

**Schema Not Found**
```bash
# Check subject registration
curl http://localhost:8081/subjects

# Verify schema file exists
ls -la schemas/transaction.avsc
```

**Compatibility Violations**
```bash
# Check compatibility manually
python scripts/check_compatibility.py transaction.avsc v1.0.0

# Review compatibility mode
curl http://localhost:8081/config/synthetic-transactions-value
```

**Serialization Errors**
```bash
# Validate data against schema
python scripts/validate_data.py --schema transaction.avsc --data sample_transaction.json

# Enable debug logging
export KAFKA_DEBUG=all
```

## Best Practices

### Schema Design

1. **Use explicit defaults** for all optional fields
2. **Document business logic** in field descriptions
3. **Validate enums** represent complete value sets
4. **Version schemas** semantically (major.minor.patch)

### Evolution Management

1. **Test compatibility** before deploying schema changes
2. **Coordinate deployments** between producers and consumers
3. **Monitor metrics** during schema rollouts
4. **Maintain rollback plans** for compatibility issues

### Performance Optimization

1. **Cache compiled schemas** in application memory
2. **Batch schema registry calls** during initialization
3. **Use connection pooling** for registry HTTP clients
4. **Monitor serialization latency** in production

---

**Schema Management Philosophy**: "Evolve data contracts safely while maintaining high performance and operational excellence."