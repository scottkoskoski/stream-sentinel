"""
Comprehensive Test Configuration and Fixtures for Stream-Sentinel

This module provides pytest fixtures and configuration for testing the entire
fraud detection system with real service integration and production-level
data volumes.

Key features:
- Real service integration (Kafka, Redis, PostgreSQL, ClickHouse)
- Production-scale test data generation
- Comprehensive error handling and cleanup
- Performance monitoring during tests
"""

import json
import time
import pytest
import redis
import psycopg
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Any, Generator
from datetime import datetime, timedelta
from confluent_kafka import Producer, Consumer
from confluent_kafka.admin import AdminClient, ConfigResource, ResourceType, NewTopic

import sys
sys.path.append(str(Path(__file__).parent.parent / "src"))

from kafka.config import get_kafka_config
from persistence.config import PersistenceConfigManager
from persistence.database import PostgreSQLManager, ClickHouseManager, get_persistence_layer


@pytest.fixture(scope="session")
def kafka_config():
    """Kafka configuration for testing with real cluster."""
    return get_kafka_config()


@pytest.fixture(scope="session") 
def redis_client(kafka_config):
    """Redis client connected to test instance."""
    redis_config = {
        'host': 'localhost',
        'port': 6379,
        'db': 1,  # Use separate DB for tests
        'decode_responses': True,
        'socket_timeout': 30,
        'socket_connect_timeout': 10,
        'retry_on_timeout': True
    }
    
    client = redis.Redis(**redis_config)
    
    # Verify connection
    try:
        client.ping()
        yield client
    finally:
        # Cleanup test data
        client.flushdb()


@pytest.fixture(scope="session")
def database_manager():
    """Database manager for PostgreSQL and ClickHouse testing."""
    try:
        # Use the persistence layer instead of a specific DatabaseManager
        persistence_layer = get_persistence_layer()
        yield persistence_layer
    finally:
        # Cleanup will be handled by the persistence layer's context manager
        pass


@pytest.fixture(scope="session")
def kafka_admin_client(kafka_config):
    """Kafka admin client for topic management."""
    admin_config = {"bootstrap.servers": kafka_config.bootstrap_servers}
    return AdminClient(admin_config)


@pytest.fixture(scope="function")
def kafka_producer(kafka_config):
    """Kafka producer for test message generation."""
    producer_config = kafka_config.get_producer_config("transaction")
    producer = Producer(producer_config)
    
    yield producer
    
    # Ensure all messages are delivered
    producer.flush(timeout=10)


@pytest.fixture(scope="function") 
def kafka_consumer(kafka_config):
    """Kafka consumer for test message consumption."""
    consumer_config = kafka_config.get_consumer_config(
        consumer_group="test-group", 
        consumer_type="fraud_detector"
    )
    consumer_config["auto.offset.reset"] = "earliest"
    consumer_config["enable.auto.commit"] = False
    
    consumer = Consumer(consumer_config)
    
    yield consumer
    
    consumer.close()


@pytest.fixture(scope="function")
def test_topics(kafka_admin_client):
    """Create and manage test Kafka topics."""
    test_topic_names = [
        "test-transactions",
        "test-fraud-alerts", 
        "test-model-updates",
        "test-user-events"
    ]
    
    # Delete existing test topics
    try:
        delete_result = kafka_admin_client.delete_topics(test_topic_names)
        for topic, future in delete_result.items():
            try:
                future.result(timeout=10)
            except Exception:
                pass  # Topic might not exist
        time.sleep(2)  # Wait for deletion
    except Exception:
        pass
    
    # Create test topics with production-like configuration
    topics = []
    for topic_name in test_topic_names:
        topic = NewTopic(
            topic=topic_name,
            num_partitions=12,  # Same as production
            replication_factor=1,  # Single broker in test
            config={
                "cleanup.policy": "delete",
                "retention.ms": str(300000),  # 5 minutes for tests
                "segment.ms": str(60000),     # 1 minute segments
                "compression.type": "lz4"
            }
        )
        topics.append(topic)
    
    # Create topics
    creation_result = kafka_admin_client.create_topics(topics)
    for topic_name, future in creation_result.items():
        future.result(timeout=10)
    
    time.sleep(2)  # Wait for topic creation
    
    yield test_topic_names
    
    # Cleanup
    delete_result = kafka_admin_client.delete_topics(test_topic_names)
    for topic, future in delete_result.items():
        try:
            future.result(timeout=10)
        except Exception:
            pass


@pytest.fixture
def synthetic_transactions() -> List[Dict[str, Any]]:
    """Generate realistic synthetic transactions for testing."""
    np.random.seed(42)  # Deterministic for testing
    
    transactions = []
    user_ids = [f"user_{i:04d}" for i in range(1000)]  # 1000 test users
    
    # Generate 10,000 transactions with realistic patterns
    for i in range(10000):
        user_id = np.random.choice(user_ids)
        
        # Log-normal amount distribution (matches IEEE-CIS analysis)
        amount = np.random.lognormal(mean=3.0, sigma=1.5)
        amount = max(1.0, min(amount, 5000.0))  # Reasonable bounds
        
        # Temporal patterns - higher fraud rates at certain hours
        hour = np.random.randint(0, 24)
        is_peak_fraud_hour = hour == 8  # 8 AM peak from analysis
        
        # Fraud injection based on multiple factors
        fraud_probability = 0.027  # 2.7% baseline
        if is_peak_fraud_hour:
            fraud_probability *= 2.3  # Peak fraud multiplier
        if amount < 10:
            fraud_probability *= 1.9  # Small amount multiplier
        if i % 100 < 5:  # Velocity-based fraud
            fraud_probability *= 3.0
            
        is_fraud = np.random.random() < fraud_probability
        
        transaction = {
            "transaction_id": f"txn_{i:06d}",
            "user_id": user_id,
            "amount": round(amount, 2),
            "timestamp": datetime.now().isoformat(),
            "hour": hour,
            "merchant_category": np.random.choice(["grocery", "gas", "restaurant", "online", "retail"]),
            "card_type": np.random.choice(["credit", "debit"]),
            "is_fraud": 1 if is_fraud else 0,
            "test_metadata": {
                "generated_at": datetime.now().isoformat(),
                "fraud_factors": {
                    "peak_hour": is_peak_fraud_hour,
                    "small_amount": amount < 10,
                    "velocity_flag": i % 100 < 5
                }
            }
        }
        transactions.append(transaction)
    
    return transactions


@pytest.fixture
def fraud_scenarios() -> Dict[str, List[Dict[str, Any]]]:
    """Predefined fraud scenarios for comprehensive testing."""
    scenarios = {}
    
    # High-velocity fraud - rapid successive transactions
    scenarios["high_velocity"] = []
    base_time = datetime.now()
    for i in range(20):
        scenarios["high_velocity"].append({
            "transaction_id": f"velocity_{i}",
            "user_id": "user_velocity_test",
            "amount": 50.0,
            "timestamp": (base_time + timedelta(seconds=i*2)).isoformat(),
            "merchant_category": "online",
            "is_fraud": 1
        })
    
    # Large amount fraud
    scenarios["large_amount"] = [{
        "transaction_id": "large_amount_1",
        "user_id": "user_large_test", 
        "amount": 4500.0,
        "timestamp": datetime.now().isoformat(),
        "merchant_category": "online",
        "is_fraud": 1
    }]
    
    # Time-based fraud (unusual hours)
    scenarios["unusual_time"] = [{
        "transaction_id": "unusual_time_1",
        "user_id": "user_time_test",
        "amount": 200.0,
        "timestamp": datetime.now().replace(hour=3, minute=15).isoformat(),
        "merchant_category": "gas",
        "is_fraud": 1
    }]
    
    # Geographic anomaly simulation
    scenarios["geographic_anomaly"] = [{
        "transaction_id": "geo_anomaly_1", 
        "user_id": "user_geo_test",
        "amount": 75.0,
        "timestamp": datetime.now().isoformat(),
        "merchant_category": "restaurant",
        "location_anomaly": True,
        "is_fraud": 1
    }]
    
    return scenarios


@pytest.fixture
def user_profiles() -> Dict[str, Dict[str, Any]]:
    """Generate realistic user profiles for state management testing."""
    profiles = {}
    
    # Normal user profile
    profiles["normal_user"] = {
        "user_id": "user_normal",
        "total_transactions": 150,
        "total_amount": 3250.50,
        "avg_transaction_amount": 21.67,
        "daily_transaction_count": 3,
        "daily_amount": 85.25,
        "last_transaction_time": (datetime.now() - timedelta(hours=2)).isoformat(),
        "last_transaction_amount": 32.50,
        "suspicious_activity_count": 0
    }
    
    # High-risk user profile
    profiles["high_risk_user"] = {
        "user_id": "user_high_risk",
        "total_transactions": 50,
        "total_amount": 12000.00,
        "avg_transaction_amount": 240.00,
        "daily_transaction_count": 8,
        "daily_amount": 1200.00,
        "last_transaction_time": (datetime.now() - timedelta(minutes=5)).isoformat(),
        "last_transaction_amount": 500.00,
        "suspicious_activity_count": 5
    }
    
    # New user profile
    profiles["new_user"] = {
        "user_id": "user_new",
        "total_transactions": 0,
        "total_amount": 0.0,
        "avg_transaction_amount": 0.0,
        "daily_transaction_count": 0,
        "daily_amount": 0.0,
        "last_transaction_time": None,
        "last_transaction_amount": 0.0,
        "suspicious_activity_count": 0
    }
    
    return profiles


@pytest.fixture
def ml_model_metadata():
    """ML model metadata for testing model operations."""
    return {
        "model_version": "test_v1.0.0",
        "model_type": "lightgbm",
        "training_date": datetime.now().isoformat(),
        "performance_metrics": {
            "auc_score": 0.836,
            "precision": 0.742,
            "recall": 0.698,
            "f1_score": 0.719
        },
        "feature_count": 25,
        "training_samples": 100000,
        "validation_samples": 25000
    }


@pytest.fixture
def performance_benchmarks():
    """Performance benchmark targets for validation."""
    return {
        "max_latency_ms": 100,
        "target_throughput_tps": 10000,
        "min_accuracy_auc": 0.80,
        "max_memory_mb": 2048,
        "max_cpu_percent": 80,
        "alert_processing_ms": 1
    }


@pytest.fixture(scope="function")
def clean_test_environment(redis_client, database_manager):
    """Ensure clean test environment before each test."""
    # Clean Redis test database
    redis_client.flushdb()
    
    # For now, skip database cleanup as the interface is different
    # TODO: Implement proper cleanup when database tests are needed
    
    yield
    
    # Cleanup after test
    redis_client.flushdb()


def pytest_configure(config):
    """Configure pytest with custom markers and settings."""
    config.addinivalue_line(
        "markers", "requires_infrastructure: mark test as needing full Docker infrastructure"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers based on test names."""
    for item in items:
        # Add integration marker to integration tests
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            
        # Add performance marker to performance tests
        if "performance" in str(item.fspath):
            item.add_marker(pytest.mark.performance)
            item.add_marker(pytest.mark.slow)
            
        # Add requires_infrastructure to tests that need full stack
        if any(keyword in str(item.fspath) for keyword in ["e2e", "performance", "chaos"]):
            item.add_marker(pytest.mark.requires_infrastructure)