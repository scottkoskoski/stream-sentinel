# Stream-Sentinel Integration Testing Framework

## Enterprise-Grade Distributed Systems Testing

A production-ready integration testing framework designed for distributed fraud detection systems, demonstrating FAANG-level testing sophistication with comprehensive multi-service validation, realistic fraud scenarios, and advanced distributed systems testing patterns.

### 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Integration Testing Framework                            │
├─────────────────┬─────────────────┬─────────────────┬─────────────────────────┤
│ Service         │ Test Data       │ Cross-Service   │ Eventually Consistent   │
│ Orchestrator    │ Factory         │ Validator       │ Assertions              │
│                 │                 │                 │                         │
│ • Docker        │ • IEEE-CIS      │ • Redis State   │ • Exponential Backoff   │
│   Compose       │   Patterns      │ • PostgreSQL    │ • Jitter Prevention     │
│ • Dependency    │ • Realistic     │   Consistency   │ • Performance SLA       │
│   Resolution    │   User Behavior │ • ClickHouse    │ • Message Flow          │
│ • Health        │ • Fraud         │   Analytics     │ • Chaos Recovery        │
│   Checking      │   Scenarios     │ • Data Flow     │                         │
└─────────────────┴─────────────────┴─────────────────┴─────────────────────────┘
```

### ✨ Key Features

#### 🚀 **Production-Grade Service Orchestration**
- **Docker Compose Integration**: Full service lifecycle management with automatic cleanup
- **Dependency-Aware Startup**: Topological sort with parallel execution groups
- **Health Checking**: Protocol-aware validation with exponential backoff and jitter
- **Resource Management**: Memory, CPU, and network resource allocation and monitoring

#### 🎯 **IEEE-CIS Based Test Data Generation**
- **Statistical Accuracy**: Leverages real-world fraud patterns from IEEE-CIS dataset analysis
- **Behavioral Modeling**: Sophisticated user behavior profiles with temporal preferences
- **Fraud Scenarios**: Velocity attacks, amount anomalies, mixed populations, stress testing
- **Temporal Patterns**: Hour-of-day fraud rate variations matching real-world data

#### 🔍 **Cross-Service State Validation**
- **Multi-Service Consistency**: Validates state across Redis, PostgreSQL, and ClickHouse
- **Data Flow Integrity**: End-to-end transaction tracing with detailed diagnostics
- **Performance Impact Monitoring**: Measures validation overhead and optimization opportunities
- **Referential Integrity**: Comprehensive constraint and relationship validation

#### ⚡ **Eventually Consistent Assertions**
- **Distributed Systems Patterns**: Handles timing issues and eventual consistency
- **Exponential Backoff with Jitter**: Prevents thundering herd problems
- **Performance SLA Validation**: Latency and throughput compliance checking
- **Comprehensive Diagnostics**: Detailed failure analysis and debugging support

### 🛠️ Quick Start

#### Prerequisites
```bash
# Docker and Docker Compose
docker --version  # >= 20.0
docker-compose --version  # >= 1.29

# Python dependencies
pip install pytest pytest-asyncio redis asyncpg confluent-kafka
```

#### Basic Usage
```python
import pytest
from src.testing import (
    ServiceOrchestrator, 
    TestDataFactory, 
    IntegrationTestConfig,
    EventuallyConsistentAssertions
)

@pytest.mark.asyncio
async def test_fraud_detection_pipeline():
    # Initialize configuration
    config = IntegrationTestConfig(environment="ci_cd")
    
    # Create test data factory
    data_factory = TestDataFactory(random_seed=42)
    
    # Generate realistic fraud scenario
    scenario = data_factory.create_mixed_population_scenario(
        total_users=50,
        duration_hours=1.0,
        target_fraud_rate=0.03
    )
    
    # Start services with orchestrator
    orchestrator = ServiceOrchestrator(config)
    async with orchestrator.service_environment(ServiceProfile.FULL_INTEGRATION):
        
        # Initialize eventually consistent assertions
        assertions = EventuallyConsistentAssertions()
        
        # Validate end-to-end processing
        result = await assertions.assert_cross_service_state(
            checks={
                'redis_profiles': lambda: check_user_profiles_exist(scenario),
                'postgres_alerts': lambda: check_fraud_alerts_generated(scenario),
                'clickhouse_data': lambda: check_analytics_persisted(scenario)
            },
            assertion_name="end_to_end_validation",
            timeout=120
        )
        
        assert result.success
```

### 📊 Test Scenarios

#### 1. **Legitimate Baseline**
```python
scenario = data_factory.create_legitimate_baseline_scenario(
    transaction_count=1000,
    user_count=50,
    duration_hours=24.0
)
# Expected: 0% fraud rate, no false positives
```

#### 2. **Velocity Attack Detection**
```python
scenario = data_factory.create_velocity_attack_scenario(
    attack_user_count=3,
    legitimate_user_count=20,
    attack_intensity=15.0,  # 15x normal velocity
    duration_hours=2.0
)
# Expected: High-frequency transactions flagged as fraud
```

#### 3. **Amount Anomaly Detection**
```python
scenario = data_factory.create_amount_anomaly_scenario(
    anomaly_user_count=5,
    legitimate_user_count=25,
    anomaly_multiplier=10.0,  # 10x typical amounts
    duration_hours=12.0
)
# Expected: Large transactions flagged for manual review
```

#### 4. **Realistic Mixed Population**
```python
scenario = data_factory.create_mixed_population_scenario(
    total_users=100,
    duration_hours=24.0,
    target_fraud_rate=0.03  # IEEE-CIS realistic rate
)
# Expected: 97% legitimate, 3% fraud matching real-world patterns
```

#### 5. **Stress Testing**
```python
scenario = data_factory.create_stress_test_scenario(
    target_tps=1000.0,
    duration_minutes=10.0,
    concurrent_users=500
)
# Expected: System maintains SLA under high load
```

### 🔧 Advanced Configuration

#### Environment-Specific Settings
```python
# Development environment - fast iteration
config = IntegrationTestConfig(
    environment=TestEnvironment.DEVELOPMENT,
    config_overrides={
        'resource_limits': {
            'max_memory_mb': 1024,
            'service_startup_timeout_seconds': 60
        },
        'test_data': {
            'base_transaction_count': 100,
            'user_count': 10
        }
    }
)

# CI/CD environment - balanced performance
config = IntegrationTestConfig(
    environment=TestEnvironment.CI_CD,
    config_overrides={
        'test_execution': {
            'parallel_workers': 4,
            'test_timeout_minutes': 15
        }
    }
)

# Performance testing - maximum resources
config = IntegrationTestConfig(
    environment=TestEnvironment.PERFORMANCE,
    config_overrides={
        'resource_limits': {
            'max_memory_mb': 4096,
            'max_cpu_cores': 8
        },
        'test_data': {
            'base_transaction_count': 10000,
            'user_count': 500
        }
    }
)
```

#### Custom Fraud Scenarios
```python
class CustomTestDataFactory(TestDataFactory):
    def create_insider_threat_scenario(self, insider_count: int = 2) -> FraudScenario:
        """Create scenario simulating insider threat patterns."""
        # Trusted users suddenly exhibiting fraudulent behavior
        user_profiles = []
        
        for i in range(insider_count):
            profile = UserBehaviorProfile(
                user_id=f"insider_{i}",
                avg_transaction_amount=self.amount_statistics['p90'],
                transaction_frequency_per_day=20.0,  # High frequency
                preferred_transaction_hours=[22, 23, 0, 1, 2],  # Off-hours
                risk_score=0.9  # High risk despite trusted status
            )
            user_profiles.append(profile)
        
        return FraudScenario(
            scenario_id=f"insider_threat_{int(time.time())}",
            scenario_type=ScenarioType.BEHAVIORAL_DRIFT,
            name="Insider Threat Scenario",
            description="Trusted users exhibiting sudden fraudulent behavior",
            user_profiles=user_profiles,
            # ... additional configuration
        )
```

### 🎭 Service Failure Scenarios

#### Redis Failure Recovery
```python
@pytest.mark.asyncio
async def test_redis_failure_recovery(service_orchestrator, test_data_factory):
    """Test system resilience during Redis unavailability."""
    
    # Establish baseline
    scenario = test_data_factory.create_legitimate_baseline_scenario()
    baseline_latency = await measure_processing_latency(scenario)
    
    # Simulate Redis failure
    await service_orchestrator.stop_service("redis")
    
    # Continue processing during failure
    failure_scenario = test_data_factory.create_legitimate_baseline_scenario()
    
    # Validate graceful degradation
    degraded_latency = await measure_processing_latency(failure_scenario)
    assert degraded_latency < baseline_latency * 3.0  # Max 3x degradation
    
    # Restart Redis and validate recovery
    await service_orchestrator.start_service("redis")
    
    recovery_scenario = test_data_factory.create_legitimate_baseline_scenario()
    recovery_latency = await measure_processing_latency(recovery_scenario)
    assert recovery_latency < baseline_latency * 1.5  # Near-baseline performance
```

#### Kafka Partition Failure
```python
@pytest.mark.asyncio
async def test_kafka_partition_resilience(service_orchestrator):
    """Test system behavior during Kafka partition unavailability."""
    
    # Simulate partition failure
    await simulate_kafka_partition_failure(partition_id=0)
    
    # Validate message routing to healthy partitions
    assertions = EventuallyConsistentAssertions()
    
    result = await assertions.assert_message_flow(
        producer_func=lambda: produce_test_transaction(),
        consumer_func=lambda msg_id: check_transaction_processed(msg_id),
        assertion_name="kafka_partition_resilience",
        timeout=60
    )
    
    assert result.success
```

### 📈 Performance Validation

#### Latency SLA Compliance
```python
@pytest.mark.asyncio
async def test_fraud_detection_latency_sla():
    """Validate fraud detection meets latency SLA requirements."""
    
    assertions = EventuallyConsistentAssertions()
    
    # Test under normal load
    normal_load_result = await assertions.assert_performance_sla(
        operation_func=lambda: simulate_fraud_detection_operation(),
        max_latency_ms=100.0,  # Sub-100ms requirement
        min_throughput_ops_per_sec=1000.0,  # 1000 TPS minimum
        assertion_name="normal_load_sla",
        sample_count=50
    )
    
    assert normal_load_result.success
    
    # Test under stress conditions
    stress_result = await assertions.assert_performance_sla(
        operation_func=lambda: simulate_fraud_detection_under_stress(),
        max_latency_ms=200.0,  # Relaxed SLA under stress
        min_throughput_ops_per_sec=500.0,  # Reduced throughput acceptable
        assertion_name="stress_load_sla",
        sample_count=100
    )
    
    assert stress_result.success
```

#### Memory Usage Monitoring
```python
@pytest.mark.asyncio
async def test_memory_usage_compliance(service_orchestrator):
    """Validate system memory usage stays within limits."""
    
    stress_scenario = test_data_factory.create_stress_test_scenario(
        target_tps=2000.0,
        duration_minutes=5.0,
        concurrent_users=1000
    )
    
    # Monitor memory usage during stress test
    memory_monitor = SystemResourceMonitor()
    await memory_monitor.start_monitoring()
    
    # Execute stress scenario
    await execute_fraud_detection_scenario(stress_scenario)
    
    memory_stats = await memory_monitor.get_statistics()
    
    # Validate memory constraints
    assert memory_stats['max_memory_mb'] < 2048  # 2GB limit
    assert memory_stats['memory_growth_rate'] < 0.1  # <10% growth per minute
    
    await memory_monitor.stop_monitoring()
```

### 🧪 Advanced Testing Patterns

#### Data Consistency Validation
```python
@pytest.mark.asyncio
async def test_multi_service_data_consistency():
    """Validate data consistency across all persistence layers."""
    
    scenario = test_data_factory.create_mixed_population_scenario()
    
    # Execute scenario
    transaction_ids = await execute_scenario(scenario)
    
    # Cross-service validation
    validator = CrossServiceValidator(config)
    
    async with validator:
        result = await validator.validate_transaction_flow(
            transaction_ids=transaction_ids,
            scenario_id=scenario.scenario_id,
            timeout_seconds=120
        )
    
    # Validate consistency across all services
    assert result.is_consistent
    assert result.consistency_checks['user_profile_consistency']
    assert result.consistency_checks['transaction_count_consistency']
    assert result.consistency_checks['fraud_alert_consistency']
    
    # Validate data flow integrity
    assert result.data_flow_validation['integrity_validated']
    assert result.data_flow_validation['flow_success_rate'] >= 0.95
```

#### Chaos Engineering Integration
```python
@pytest.mark.asyncio
async def test_chaos_engineering_resilience():
    """Test system resilience under various failure conditions."""
    
    chaos_scenarios = [
        ('network_partition', lambda: simulate_network_partition(duration=30)),
        ('high_cpu_load', lambda: simulate_cpu_stress(utilization=90, duration=60)),
        ('memory_pressure', lambda: simulate_memory_pressure(pressure=80, duration=45)),
        ('disk_full', lambda: simulate_disk_full(fill_percentage=95, duration=30))
    ]
    
    baseline_scenario = test_data_factory.create_legitimate_baseline_scenario()
    
    for failure_name, failure_func in chaos_scenarios:
        logging.info(f"Testing resilience under {failure_name}")
        
        # Inject failure
        await failure_func()
        
        # Validate system continues operating
        assertions = EventuallyConsistentAssertions()
        
        resilience_result = await assertions.eventually_assert(
            assertion_func=lambda: check_system_operational(baseline_scenario),
            assertion_name=f"resilience_under_{failure_name}",
            timeout=90
        )
        
        assert resilience_result.success, f"System failed under {failure_name}"
```

### 🎯 Best Practices

#### 1. **Test Isolation**
```python
@pytest.fixture(scope="function")
async def isolated_test_environment():
    """Ensure complete test isolation."""
    
    # Use unique namespaces
    test_id = f"test_{int(time.time())}_{random.randint(1000, 9999)}"
    
    config = IntegrationTestConfig(
        config_overrides={
            'kafka': {'topic_prefix': f"test-{test_id}-"},
            'redis': {'namespace': f"test:{test_id}"},
            'postgres': {'schema': f"test_{test_id}"}
        }
    )
    
    yield config
    
    # Cleanup after test
    await cleanup_test_data(test_id)
```

#### 2. **Resource Management**
```python
@pytest.fixture(scope="session")
async def resource_manager():
    """Manage shared resources across test session."""
    
    manager = TestResourceManager()
    await manager.initialize()
    
    try:
        yield manager
    finally:
        await manager.cleanup()
```

#### 3. **Error Diagnostics**
```python
@pytest.fixture(autouse=True)
async def test_diagnostics(request):
    """Automatic diagnostic collection on test failure."""
    
    yield
    
    if request.node.rep_call.failed:
        # Collect service logs
        await collect_service_logs(request.node.name)
        
        # Collect system metrics
        await collect_system_metrics(request.node.name)
        
        # Generate diagnostic report
        await generate_failure_report(request.node.name)
```

### 📊 Metrics and Reporting

The framework automatically collects comprehensive metrics:

- **Service Startup Times**: Track orchestration performance
- **Test Execution Duration**: Monitor test efficiency
- **Assertion Success Rates**: Validate framework reliability
- **Cross-Service Validation Performance**: Optimize validation overhead
- **Resource Utilization**: Track memory, CPU, and network usage
- **Error Rates and Failure Patterns**: Identify system weaknesses

### 🎓 Educational Value

This framework demonstrates advanced distributed systems concepts:

1. **Eventual Consistency Patterns**: Proper handling of distributed timing issues
2. **Service Mesh Validation**: Cross-service communication verification
3. **Chaos Engineering Principles**: Proactive failure injection and recovery testing
4. **Performance Engineering**: SLA compliance and optimization strategies
5. **Data Consistency Models**: Multi-service state management validation
6. **Production Readiness**: Real-world operational concerns and monitoring

Perfect for demonstrating senior-level distributed systems engineering capabilities in technical interviews and portfolio presentations.

---

**Built with production-grade quality and FAANG-level engineering standards.**