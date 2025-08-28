# Stream-Sentinel Comprehensive Testing Framework

This directory contains a complete, production-grade testing framework for the Stream-Sentinel fraud detection system. The testing strategy emphasizes **comprehensive coverage**, **real service integration**, and **full performance validation** at production scale.

## Testing Philosophy

### Core Principles
- **Complete System Coverage**: Test every critical component with realistic integration scenarios
- **Real Service Integration**: Use actual Kafka, Redis, PostgreSQL, and ClickHouse services
- **Production-Level Performance**: Validate full 10k+ TPS capabilities under sustained load
- **Comprehensive Failure Testing**: Chaos engineering and resilience validation
- **Deterministic Reproducibility**: Consistent test results across environments

### Testing Pyramid

```
                    Chaos Engineering (5%)
                    ├─ Network partitions, service crashes
                    └─ Resource exhaustion, cascading failures
                
                Performance Tests (5%)
                ├─ 10k+ TPS sustained throughput
                ├─ Sub-100ms latency validation
                └─ Memory/CPU profiling
            
        End-to-End Tests (20%)
        ├─ Complete fraud detection workflows
        ├─ Alert processing automation
        └─ Cross-service integration
    
    Integration Tests (35%)
    ├─ Kafka message flow validation
    ├─ Redis state management
    ├─ Database persistence operations
    └─ ML pipeline integration
    
Unit Tests (40%)
├─ Feature engineering (25+ functions)
├─ Fraud scoring algorithms
├─ User profile management
└─ Alert classification logic
```

## Test Architecture

### Infrastructure Requirements

**Required Services:**
- **Kafka Cluster**: 12-partition topics, LZ4 compression, exactly-once processing
- **Redis**: State management with LRU eviction, persistence enabled
- **PostgreSQL**: ACID-compliant alert and audit storage
- **ClickHouse**: High-performance analytics and metrics storage

**Resource Requirements:**
- **CPU**: Minimum 8 cores for performance testing
- **Memory**: 16GB RAM for full test suite
- **Storage**: 50GB for test data and results
- **Network**: Localhost/containerized services

### Test Categories

| Category | Duration | Infrastructure | Description |
|----------|----------|----------------|-------------|
| **Unit** | 2-5 min | No | Individual component validation |
| **Integration** | 10-20 min | Yes | Real service interactions |
| **End-to-End** | 20-30 min | Yes | Complete workflow validation |
| **Performance** | 30-60 min | Yes | 10k+ TPS throughput validation |
| **Chaos** | 45-90 min | Yes | Failure modes and resilience |

## Quick Start

### Prerequisites

```bash
# Install Python dependencies
pip install pytest pytest-asyncio pytest-benchmark pytest-cov pytest-xdist

# Install system dependencies
sudo apt-get update
sudo apt-get install docker.io docker-compose

# Verify Docker
docker --version
docker-compose --version
```

### Running Tests

```bash
# Quick smoke tests (30 seconds)
python tests/run_tests.py --smoke

# Unit tests only (fast feedback)
python tests/run_tests.py --category unit

# Full integration validation
python tests/run_tests.py --category integration --start-infrastructure

# Performance validation (10k+ TPS)
python tests/run_tests.py --category performance --start-infrastructure --verbose

# Complete test suite (2-3 hours)
python tests/run_tests.py --all --start-infrastructure --generate-report
```

### Manual Infrastructure Management

```bash
# Start test infrastructure
cd tests
docker-compose -f docker-compose.test.yml up -d

# Check service health
docker-compose -f docker-compose.test.yml ps

# Stop and cleanup
docker-compose -f docker-compose.test.yml down -v
```

## Test Categories Detail

### Unit Tests (`tests/unit/`)

**Comprehensive component testing with 90%+ coverage:**

- **Feature Engineering** (`test_feature_engineering.py`)
  - 25+ real-time features validation
  - Edge case handling (missing data, invalid timestamps)
  - Temporal pattern detection (8 AM fraud peak)
  - Amount categorization (small <$10, large >$1000)
  - User behavioral analysis

- **Fraud Scoring** (`test_fraud_scoring.py`)
  - ML model integration (LightGBM 83.6% AUC)
  - Ensemble scoring (ML + business rules)
  - Score interpretation and thresholds
  - Feature importance analysis
  - Fallback mechanisms when ML unavailable

- **Alert Processing** (`test_alert_processing.py`)
  - Multi-tier severity classification (MINIMAL → CRITICAL)
  - Automated action routing
  - User blocking mechanisms
  - SLA compliance (sub-1ms processing)
  - Investigation queue management

- **User Profile Management** (`test_user_profile_management.py`)
  - Redis CRUD operations
  - Concurrent access handling
  - Daily statistics reset logic
  - Data validation and recovery
  - Memory management and TTL

**Execution:**
```bash
# Run all unit tests
pytest tests/unit/ -v

# Run specific component
pytest tests/unit/test_feature_engineering.py -v

# With coverage report
pytest tests/unit/ --cov=src --cov-report=html
```

### Integration Tests (`tests/integration/`)

**Real service integration validation:**

- **Kafka Integration** (`test_kafka_integration.py`)
  - End-to-end message flow (produce → consume)
  - Message ordering within partitions
  - Consumer group load balancing
  - Exactly-once processing semantics
  - High-throughput production (1k+ TPS)
  - Consumer lag and catch-up behavior
  - Partition rebalancing
  - Error handling and recovery

- **Redis Integration** (`test_redis_integration.py`)
  - User profile CRUD operations
  - Concurrent access and consistency
  - Transaction velocity tracking
  - User blocking and status management
  - Enhanced monitoring configuration
  - Alert deduplication
  - Memory management and eviction
  - Connection failure recovery
  - High-frequency operations (1k+ ops/sec)

**Key Integration Scenarios:**
```python
# Test concurrent profile updates
def test_concurrent_user_profile_updates():
    # 100 concurrent transactions for same user
    # Verify atomic updates and consistency
    assert final_profile.total_transactions == 100
    assert final_profile.total_amount == 100 * 50.0

# Test high-throughput Kafka processing  
def test_high_throughput_production():
    # Produce 10k messages in <10 seconds
    # Verify delivery confirmations
    assert actual_tps >= 1000
    assert production_errors < 1%
```

### End-to-End Tests (`tests/e2e/`)

**Complete workflow validation with realistic volumes:**

- **Fraud Detection Workflows** (`test_fraud_workflows.py`)
  - Normal transaction processing (low fraud score, minimal actions)
  - High-risk fraud detection (velocity patterns, user blocking)
  - New user onboarding (profile creation, risk assessment)
  - Bulk processing (1k transactions, performance validation)
  - Cross-service integration (Kafka → Redis → Database)

**Complete Workflow Example:**
```python
def test_high_risk_fraud_transaction_workflow():
    # 1. Produce high-velocity transactions to Kafka
    # 2. Process with fraud detector (Redis state updates)
    # 3. Generate high fraud score (>0.8)
    # 4. Trigger CRITICAL alert with user blocking
    # 5. Verify Redis blocking status
    # 6. Validate database audit logging
    assert fraud_score.final_score > 0.8
    assert redis_client.exists(f"blocked_users:{user_id}")
```

### Performance Tests (`tests/performance/`)

**Production-level performance validation:**

- **Throughput Benchmarks** (`test_throughput_benchmarks.py`)
  - **10k+ TPS Sustained Processing**: 5-minute continuous load test
  - **Fraud Detection Processing**: 5k TPS with ML scoring
  - **Concurrent User Processing**: 1k users simultaneously
  - **Memory Usage Monitoring**: Sustained load without leaks
  - **Redis Performance**: 5k+ operations/second
  
**Performance Targets:**
- **Throughput**: 10,000+ transactions per second
- **Latency**: Sub-100ms fraud detection (P99 < 100ms)
- **Memory**: <2GB growth under sustained load
- **CPU**: <80% utilization at target throughput
- **Accuracy**: Maintain >80% AUC under load

**Performance Test Execution:**
```bash
# Full performance validation
python tests/run_tests.py --category performance --verbose

# Memory profiling
pytest tests/performance/test_throughput_benchmarks.py::TestThroughputBenchmarks::test_memory_usage_under_sustained_load -v

# 10k TPS validation
pytest tests/performance/test_throughput_benchmarks.py::TestThroughputBenchmarks::test_sustained_10k_tps_processing -v --tb=short
```

### Chaos Engineering Tests (`tests/chaos/`)

**System resilience and failure mode validation:**

- **System Resilience** (`test_system_resilience.py`)
  - **Network Partitions**: Kafka broker isolation, recovery behavior
  - **Service Failures**: Redis connection loss, graceful degradation
  - **Resource Exhaustion**: Memory pressure, performance degradation
  - **Data Corruption**: ML model failures, invalid data handling
  - **Cascading Failure Prevention**: Component isolation validation
  - **Gradual Degradation**: Performance under increasing load

**Chaos Engineering Scenarios:**
```python
def test_kafka_broker_network_partition():
    # Phase 1: Normal operation baseline
    # Phase 2: Simulate network partition (connection errors)
    # Phase 3: Recovery validation
    assert recovery_rate >= baseline_rate * 0.8

def test_redis_connection_failure_handling():
    # Mock Redis failures
    # Verify graceful fallback to defaults  
    # Validate system continues processing
    assert features["is_new_user"] == 1  # Default fallback
```

## Test Data Management

### Synthetic Data Generation

**Realistic test data with IEEE-CIS statistical patterns:**

```python
from tests.fixtures.synthetic_data_generator import SyntheticDataGenerator

# Generate realistic transactions
generator = SyntheticDataGenerator(seed=42)

# Performance test data (10k+ TPS)
perf_data = generator.generate_performance_test_data(
    target_tps=10000,
    duration_seconds=300,
    num_users=10000
)

# Fraud scenarios
fraud_scenarios = generator.generate_fraud_scenarios()
high_velocity_fraud = fraud_scenarios["high_velocity"]
```

**Data Characteristics:**
- **User Consistency**: 1000+ users with persistent behavioral patterns
- **Fraud Rate**: 2.7% baseline matching IEEE-CIS analysis
- **Temporal Patterns**: 8 AM fraud peak, realistic hourly distributions
- **Amount Distributions**: Log-normal with realistic bounds
- **Fraud Injection**: Multi-factor determination (velocity, amount, time)

### Test Fixtures (`tests/fixtures/`)

**Comprehensive test data and utilities:**
- **User Profiles**: 1000+ consistent user behavioral patterns
- **Fraud Scenarios**: High-velocity, large-amount, temporal anomalies
- **Performance Data**: 100k+ transactions for load testing
- **Edge Cases**: Invalid data, boundary conditions, error scenarios

## Test Execution Automation

### Test Runner (`tests/run_tests.py`)

**Comprehensive test automation with infrastructure management:**

```bash
# Quick validation
python tests/run_tests.py --smoke

# Category-specific execution
python tests/run_tests.py --category unit
python tests/run_tests.py --category integration --start-infrastructure
python tests/run_tests.py --category performance --start-infrastructure --verbose

# Full test suite
python tests/run_tests.py --all --start-infrastructure --generate-report

# Performance regression check
python tests/run_tests.py --regression
```

**Features:**
- **Infrastructure Management**: Automatic Docker Compose orchestration
- **Service Health Checking**: Port scanning and readiness validation
- **Parallel Execution**: Safe parallelization for unit/integration tests
- **Performance Benchmarking**: Automated baseline comparison
- **Comprehensive Reporting**: Detailed execution reports with metrics

### CI/CD Integration

**GitHub Actions / Jenkins Integration:**

```yaml
# .github/workflows/test.yml
name: Stream-Sentinel Tests
on: [push, pull_request]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Run Unit Tests
        run: python tests/run_tests.py --category unit
  
  integration-tests:
    runs-on: ubuntu-latest
    services:
      kafka:
        image: confluentinc/cp-kafka:7.4.0
      redis:
        image: redis:7.2-alpine
    steps:
      - name: Run Integration Tests
        run: python tests/run_tests.py --category integration
  
  performance-tests:
    runs-on: [self-hosted, performance]
    steps:
      - name: Run Performance Validation
        run: python tests/run_tests.py --category performance --generate-report
```

## Test Reporting and Metrics

### Automated Reporting

**Comprehensive test execution reports:**

```bash
# Generate detailed report
python tests/run_tests.py --all --generate-report

# Output: tests/results/test_execution_report.md
```

**Report Contents:**
- **Executive Summary**: Pass/fail rates, execution times
- **Category Breakdown**: Detailed results per test category  
- **Performance Metrics**: Throughput, latency, resource usage
- **System Information**: Platform, resources, configuration
- **Historical Trends**: Performance regression analysis

### Metrics Collection

**Key Performance Indicators:**
- **Test Coverage**: >90% line coverage for critical components
- **Execution Time**: <5 minutes unit, <60 minutes full suite
- **Throughput Validation**: 10k+ TPS sustained processing
- **Reliability**: >99% test pass rate in CI/CD
- **Performance Regression**: <5% degradation tolerance

## Development Workflow

### Test-Driven Development

**Recommended workflow for new features:**

```bash
# 1. Write failing tests first
pytest tests/unit/test_new_feature.py -v  # Should fail

# 2. Implement feature
# ... code development ...

# 3. Validate implementation
pytest tests/unit/test_new_feature.py -v  # Should pass

# 4. Integration testing
pytest tests/integration/test_new_feature_integration.py -v

# 5. End-to-end validation
pytest tests/e2e/test_new_feature_workflow.py -v

# 6. Performance validation
python tests/run_tests.py --category performance
```

### Pre-Commit Testing

```bash
# Quick validation before commit
python tests/run_tests.py --smoke

# Full validation before push
python tests/run_tests.py --category unit --category integration
```

## Troubleshooting

### Common Issues

**Infrastructure Services Not Starting:**
```bash
# Check Docker daemon
sudo systemctl status docker

# Check port conflicts
sudo netstat -tlnp | grep :9092  # Kafka
sudo netstat -tlnp | grep :6379  # Redis

# Force cleanup
docker-compose -f tests/docker-compose.test.yml down -v --remove-orphans
```

**Performance Tests Failing:**
```bash
# Check system resources
htop  # Monitor CPU/memory during tests

# Increase Docker memory limits
# Edit docker-compose.test.yml and increase memory limits

# Run performance tests with profiling
pytest tests/performance/ -v --profile-svg
```

**Redis Connection Issues:**
```bash
# Test Redis connectivity
redis-cli -h localhost -p 6379 ping

# Check Redis logs
docker-compose -f tests/docker-compose.test.yml logs redis-test
```

**Test Data Generation Issues:**
```bash
# Regenerate test data
python -c "from tests.fixtures.synthetic_data_generator import *; 
           gen = SyntheticDataGenerator(); 
           data = gen.generate_bulk_transactions(1000, 100); 
           gen.save_generated_data(data, 'tests/temp/test_data.json')"
```

## Success Criteria

### Technical Validation
- **Unit Tests**: >90% code coverage, <5 minutes execution
- **Integration Tests**: All services validated, <20 minutes execution  
- **End-to-End**: Complete workflows validated, <30 minutes execution
- **Performance**: 10k+ TPS sustained, <100ms P99 latency
- **Chaos**: All failure modes handled gracefully

### Business Validation  
- **Fraud Detection Accuracy**: >80% AUC maintained under load
- **Alert Processing**: Sub-1ms SLA compliance
- **System Reliability**: 99.9% uptime simulation
- **Scalability**: Linear performance scaling to 10k+ TPS

### Portfolio Demonstration
- **Engineering Maturity**: Production-grade testing strategy
- **Technical Depth**: Comprehensive system understanding
- **Performance Engineering**: Validated high-throughput capabilities
- **Operational Excellence**: Complete failure mode coverage

---

**This testing framework demonstrates senior-level distributed systems engineering capabilities with production-ready validation of a high-performance fraud detection system.**