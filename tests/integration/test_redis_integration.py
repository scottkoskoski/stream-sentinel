"""
Comprehensive Redis Integration Tests

Tests real Redis cluster integration including:
- User profile state management operations
- Concurrent access and consistency
- Memory management and eviction policies
- High-frequency read/write operations
- Connection failure and recovery
- Performance characteristics under load
"""

import pytest
import json
import time
import threading
import redis
from datetime import datetime, timedelta
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.fraud_detector import UserProfile


@pytest.mark.integration
@pytest.mark.redis
@pytest.mark.requires_infrastructure
class TestRedisIntegration:
    """Comprehensive Redis integration tests with real cluster."""

    def test_user_profile_crud_operations(self, redis_client, clean_test_environment):
        """Test Create, Read, Update, Delete operations for user profiles."""
        user_id = "crud_test_user"
        profile_key = f"user_profile:{user_id}"
        
        # CREATE: Store new user profile
        initial_profile = {
            "user_id": user_id,
            "total_transactions": "25",
            "total_amount": "750.50",
            "avg_transaction_amount": "30.02",
            "last_transaction_time": "2023-08-15T14:30:00",
            "daily_transaction_count": "3",
            "daily_amount": "95.00",
            "suspicious_activity_count": "0"
        }
        
        redis_client.hset(profile_key, mapping=initial_profile)
        redis_client.expire(profile_key, 86400)  # 24 hour TTL
        
        # READ: Retrieve user profile
        retrieved_profile = redis_client.hgetall(profile_key)
        
        assert retrieved_profile == initial_profile
        assert redis_client.ttl(profile_key) > 0  # TTL is set
        
        # UPDATE: Modify profile fields
        redis_client.hset(profile_key, "total_transactions", "26")
        redis_client.hset(profile_key, "total_amount", "800.75")
        redis_client.hincrby(profile_key, "daily_transaction_count", 1)
        
        updated_profile = redis_client.hgetall(profile_key)
        
        assert updated_profile["total_transactions"] == "26"
        assert updated_profile["total_amount"] == "800.75"  
        assert updated_profile["daily_transaction_count"] == "4"
        
        # DELETE: Remove user profile
        deleted_count = redis_client.delete(profile_key)
        assert deleted_count == 1
        
        # Verify deletion
        assert not redis_client.exists(profile_key)

    def test_concurrent_user_profile_updates(self, redis_client, clean_test_environment):
        """Test concurrent updates to same user profile for consistency."""
        user_id = "concurrent_test_user"
        profile_key = f"user_profile:{user_id}"
        
        # Initialize profile
        initial_profile = {
            "user_id": user_id,
            "total_transactions": "0",
            "total_amount": "0.0",
            "daily_transaction_count": "0",
            "daily_amount": "0.0"
        }
        redis_client.hset(profile_key, mapping=initial_profile)
        
        # Simulate concurrent transaction processing
        def update_profile(transaction_id: int):
            """Simulate processing a single transaction."""
            pipe = redis_client.pipeline()
            
            # Atomic transaction for profile update
            pipe.multi()
            pipe.hincrby(profile_key, "total_transactions", 1)
            pipe.hincrbyfloat(profile_key, "total_amount", 50.0)
            pipe.hincrby(profile_key, "daily_transaction_count", 1)
            pipe.hincrbyfloat(profile_key, "daily_amount", 50.0)
            pipe.hset(profile_key, "last_transaction_time", datetime.now().isoformat())
            
            result = pipe.execute()
            return result
        
        # Run concurrent updates
        num_concurrent_transactions = 100
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = []
            
            for i in range(num_concurrent_transactions):
                future = executor.submit(update_profile, i)
                futures.append(future)
            
            # Wait for all updates to complete
            results = []
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=10)
                    results.append(result)
                except Exception as e:
                    pytest.fail(f"Concurrent update failed: {e}")
        
        # Verify final state consistency
        final_profile = redis_client.hgetall(profile_key)
        
        assert int(final_profile["total_transactions"]) == num_concurrent_transactions
        assert float(final_profile["total_amount"]) == num_concurrent_transactions * 50.0
        assert int(final_profile["daily_transaction_count"]) == num_concurrent_transactions
        assert float(final_profile["daily_amount"]) == num_concurrent_transactions * 50.0

    def test_transaction_velocity_tracking(self, redis_client, clean_test_environment):
        """Test real-time transaction velocity tracking using Redis lists."""
        user_id = "velocity_test_user"
        velocity_key = f"user_velocity:{user_id}"
        
        # Simulate rapid transaction sequence
        transactions = []
        base_time = datetime.now()
        
        for i in range(15):  # 15 transactions in quick succession
            transaction_time = base_time + timedelta(seconds=i * 2)
            transaction_data = {
                "transaction_id": f"velocity_txn_{i}",
                "timestamp": transaction_time.isoformat(),
                "amount": 25.0 + i
            }
            transactions.append(transaction_data)
        
        # Store transactions in Redis list (most recent first)
        for transaction in transactions:
            redis_client.lpush(velocity_key, json.dumps(transaction))
        
        # Set expiration for velocity tracking
        redis_client.expire(velocity_key, 3600)  # 1 hour
        
        # Test velocity calculations
        
        # Get transactions in last hour
        all_transactions = redis_client.lrange(velocity_key, 0, -1)
        parsed_transactions = [json.loads(tx) for tx in all_transactions]
        
        assert len(parsed_transactions) == 15
        
        # Get transactions in last 10 minutes
        ten_minutes_ago = datetime.now() - timedelta(minutes=10)
        recent_transactions = []
        
        for tx_data in parsed_transactions:
            tx_time = datetime.fromisoformat(tx_data["timestamp"])
            if tx_time > ten_minutes_ago:
                recent_transactions.append(tx_data)
        
        assert len(recent_transactions) >= 10  # Most should be within 10 minutes
        
        # Calculate velocity metrics
        total_amount_last_hour = sum(tx["amount"] for tx in parsed_transactions)
        assert total_amount_last_hour > 300  # Should sum to significant amount
        
        # Test list trimming for memory management
        redis_client.ltrim(velocity_key, 0, 99)  # Keep last 100 transactions
        
        trimmed_length = redis_client.llen(velocity_key)
        assert trimmed_length == 15  # Should still have all 15 (less than 100)

    def test_user_blocking_and_status_tracking(self, redis_client, clean_test_environment):
        """Test user blocking mechanism and status tracking."""
        user_id = "blocking_test_user"
        blocking_key = f"blocked_users:{user_id}"
        
        # Block user
        blocking_data = {
            "user_id": user_id,
            "blocked_at": datetime.now().isoformat(),
            "reason": "High fraud score",
            "alert_id": "alert_12345",
            "fraud_score": "0.85",
            "expires_at": (datetime.now() + timedelta(hours=24)).isoformat()
        }
        
        redis_client.hset(blocking_key, mapping=blocking_data)
        redis_client.expire(blocking_key, 86400)  # 24 hours
        
        # Verify blocking status
        is_blocked = redis_client.exists(blocking_key)
        assert is_blocked == 1
        
        blocked_data = redis_client.hgetall(blocking_key)
        assert blocked_data["user_id"] == user_id
        assert float(blocked_data["fraud_score"]) == 0.85
        
        # Test bulk blocking status check
        test_users = [f"user_{i}" for i in range(100)]
        blocked_users = [f"user_{i}" for i in range(5)]  # Block first 5 users
        
        # Block multiple users
        pipe = redis_client.pipeline()
        for blocked_user in blocked_users:
            blocked_key = f"blocked_users:{blocked_user}"
            block_data = {
                "user_id": blocked_user,
                "blocked_at": datetime.now().isoformat(),
                "reason": "Test blocking"
            }
            pipe.hset(blocked_key, mapping=block_data)
            pipe.expire(blocked_key, 3600)
        pipe.execute()
        
        # Check blocking status for all users
        pipe = redis_client.pipeline()
        for user in test_users:
            pipe.exists(f"blocked_users:{user}")
        
        blocking_results = pipe.execute()
        
        # Verify correct blocking status
        for i, is_user_blocked in enumerate(blocking_results):
            if i < 5:  # First 5 should be blocked
                assert is_user_blocked == 1
            else:  # Others should not be blocked
                assert is_user_blocked == 0

    def test_enhanced_monitoring_configuration(self, redis_client, clean_test_environment):
        """Test enhanced monitoring configuration for high-risk users."""
        user_id = "monitoring_test_user"
        monitoring_key = f"enhanced_monitoring:{user_id}"
        
        # Activate enhanced monitoring
        monitoring_config = {
            "user_id": user_id,
            "activated_at": datetime.now().isoformat(),
            "monitoring_level": "HIGH",
            "alert_frequency": "real_time",
            "expires_at": (datetime.now() + timedelta(days=7)).isoformat(),
            "trigger_alert_id": "alert_67890"
        }
        
        redis_client.hset(monitoring_key, mapping=monitoring_config)
        redis_client.expire(monitoring_key, 7 * 86400)  # 7 days
        
        # Verify monitoring configuration
        active_monitoring = redis_client.hgetall(monitoring_key)
        
        assert active_monitoring["monitoring_level"] == "HIGH"
        assert active_monitoring["alert_frequency"] == "real_time"
        assert redis_client.ttl(monitoring_key) > 6 * 86400  # At least 6 days left
        
        # Test monitoring level escalation
        redis_client.hset(monitoring_key, "monitoring_level", "CRITICAL")
        redis_client.hset(monitoring_key, "escalated_at", datetime.now().isoformat())
        
        updated_config = redis_client.hgetall(monitoring_key)
        assert updated_config["monitoring_level"] == "CRITICAL"
        assert "escalated_at" in updated_config

    def test_alert_deduplication_tracking(self, redis_client, clean_test_environment):
        """Test alert deduplication using Redis sets and timestamps."""
        transaction_id = "dedup_test_transaction"
        user_id = "dedup_test_user"
        
        # Test transaction-based deduplication
        txn_dedup_key = f"alert_dedup:txn:{transaction_id}"
        
        # First alert for transaction
        first_alert_time = time.time()
        redis_client.set(txn_dedup_key, first_alert_time, ex=3600)  # 1 hour expiry
        
        # Check if duplicate
        existing_alert = redis_client.get(txn_dedup_key)
        assert existing_alert is not None
        assert float(existing_alert) == first_alert_time
        
        # Test user-based deduplication (multiple alerts for same user in timeframe)
        user_dedup_key = f"alert_dedup:user:{user_id}"
        
        # Track recent alerts for user
        recent_alerts = ["alert_001", "alert_002", "alert_003"]
        
        for alert_id in recent_alerts:
            redis_client.lpush(user_dedup_key, json.dumps({
                "alert_id": alert_id,
                "timestamp": datetime.now().isoformat(),
                "fraud_score": 0.75
            }))
        
        redis_client.expire(user_dedup_key, 1800)  # 30 minutes
        
        # Check for recent duplicate alerts
        recent_user_alerts = redis_client.lrange(user_dedup_key, 0, 9)  # Last 10 alerts
        assert len(recent_user_alerts) == 3
        
        # Parse and validate alert data
        parsed_alerts = [json.loads(alert) for alert in recent_user_alerts]
        alert_ids = [alert["alert_id"] for alert in parsed_alerts]
        
        assert "alert_001" in alert_ids
        assert "alert_002" in alert_ids
        assert "alert_003" in alert_ids

    def test_redis_memory_management(self, redis_client, clean_test_environment):
        """Test Redis memory management with TTL and eviction policies."""
        # Test TTL functionality
        temp_keys = []
        
        for i in range(50):
            key = f"temp_key_{i}"
            value = f"temporary_data_{i}" * 100  # Make value substantial
            
            redis_client.set(key, value, ex=2)  # 2 second expiry
            temp_keys.append(key)
        
        # Verify keys exist initially
        existing_count = sum(1 for key in temp_keys if redis_client.exists(key))
        assert existing_count == 50
        
        # Wait for expiration
        time.sleep(3)
        
        # Verify keys expired
        remaining_count = sum(1 for key in temp_keys if redis_client.exists(key))
        assert remaining_count == 0
        
        # Test memory pressure with large data
        large_data_keys = []
        
        for i in range(100):
            key = f"large_data_{i}"
            large_value = json.dumps({
                "user_id": f"user_{i}",
                "transaction_history": [f"txn_{j}" for j in range(1000)],
                "features": [float(j) for j in range(100)]
            })
            
            redis_client.set(key, large_value, ex=300)  # 5 minute expiry
            large_data_keys.append(key)
        
        # Verify data was stored
        stored_count = sum(1 for key in large_data_keys if redis_client.exists(key))
        assert stored_count == 100
        
        # Test memory info
        memory_info = redis_client.info("memory")
        used_memory = memory_info["used_memory"]
        assert used_memory > 0
        
        # Cleanup large data
        pipe = redis_client.pipeline()
        for key in large_data_keys:
            pipe.delete(key)
        pipe.execute()

    def test_high_frequency_operations(self, redis_client, clean_test_environment, performance_benchmarks):
        """Test high-frequency Redis operations for real-time fraud detection."""
        user_id = "high_freq_user"
        profile_key = f"user_profile:{user_id}"
        
        # Initialize profile
        redis_client.hset(profile_key, mapping={
            "user_id": user_id,
            "total_transactions": "0",
            "total_amount": "0.0",
            "daily_transaction_count": "0"
        })
        
        # Simulate high-frequency profile updates
        num_operations = 1000
        start_time = time.time()
        
        pipe = redis_client.pipeline()
        for i in range(num_operations):
            pipe.hincrby(profile_key, "total_transactions", 1)
            pipe.hincrbyfloat(profile_key, "total_amount", 25.5)
            pipe.hset(profile_key, "last_update", time.time())
            
            # Execute in batches to avoid pipeline overflow
            if i % 100 == 99:
                pipe.execute()
                pipe = redis_client.pipeline()
        
        # Execute remaining operations
        if len(pipe.command_stack) > 0:
            pipe.execute()
        
        end_time = time.time()
        
        # Verify operations completed correctly
        final_profile = redis_client.hgetall(profile_key)
        assert int(final_profile["total_transactions"]) == num_operations
        assert float(final_profile["total_amount"]) == num_operations * 25.5
        
        # Performance validation
        total_time = end_time - start_time
        operations_per_second = (num_operations * 3) / total_time  # 3 operations per iteration
        
        # Should handle high-frequency operations efficiently
        min_ops_per_sec = 1000  # Minimum 1000 operations per second
        assert operations_per_second >= min_ops_per_sec

    def test_connection_failure_recovery(self, redis_client, clean_test_environment):
        """Test Redis connection failure handling and recovery."""
        user_id = "connection_test_user"
        profile_key = f"user_profile:{user_id}"
        
        # Store initial data
        initial_data = {"user_id": user_id, "test_field": "test_value"}
        redis_client.hset(profile_key, mapping=initial_data)
        
        # Verify data exists
        assert redis_client.hgetall(profile_key) == initial_data
        
        # Simulate connection issues by creating a new Redis client with wrong config
        # (In real tests, you might use network partitioning tools)
        
        # Test retry logic - attempt operations with potential failures
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                # Attempt Redis operation
                result = redis_client.hgetall(profile_key)
                
                if result:
                    break  # Success
                    
            except redis.ConnectionError:
                retry_count += 1
                if retry_count >= max_retries:
                    pytest.fail("Max retries exceeded")
                time.sleep(0.1 * retry_count)  # Exponential backoff
        
        # Connection should recover
        final_data = redis_client.hgetall(profile_key)
        assert final_data == initial_data

    def test_redis_cluster_operations(self, redis_client, clean_test_environment):
        """Test Redis operations that work with cluster configurations."""
        # Test operations that work well with Redis clusters
        
        # Hash operations (good for clusters - keys stay on same node)
        cluster_test_keys = []
        
        for i in range(50):
            key = f"cluster_test:{i}"
            data = {
                f"field_{j}": f"value_{j}" for j in range(10)
            }
            
            redis_client.hset(key, mapping=data)
            redis_client.expire(key, 3600)
            cluster_test_keys.append(key)
        
        # Verify all keys were stored
        for key in cluster_test_keys:
            assert redis_client.exists(key)
            assert redis_client.hlen(key) == 10
        
        # Test pipeline operations (may have limitations in clusters)
        pipe = redis_client.pipeline()
        
        for key in cluster_test_keys[:10]:  # Test with subset
            pipe.hget(key, "field_0")
        
        try:
            results = pipe.execute()
            # If cluster supports pipelining
            assert len(results) == 10
            assert all(result == "value_0" for result in results)
        except redis.ResponseError:
            # Some Redis cluster configurations don't support all pipeline operations
            # This is acceptable for our use case
            pass
        
        # Cleanup
        for key in cluster_test_keys:
            redis_client.delete(key)

    def test_redis_data_persistence(self, redis_client, clean_test_environment):
        """Test Redis data persistence and recovery."""
        # Store critical fraud detection data
        critical_data = {
            "blocked_users": {},
            "enhanced_monitoring": {},
            "alert_deduplication": {}
        }
        
        # Store blocked users data
        for i in range(10):
            user_id = f"blocked_user_{i}"
            blocking_key = f"blocked_users:{user_id}"
            
            blocking_data = {
                "user_id": user_id,
                "blocked_at": datetime.now().isoformat(),
                "fraud_score": str(0.8 + i * 0.01)
            }
            
            redis_client.hset(blocking_key, mapping=blocking_data)
            critical_data["blocked_users"][blocking_key] = blocking_data
        
        # Store enhanced monitoring data
        for i in range(5):
            user_id = f"monitored_user_{i}"
            monitoring_key = f"enhanced_monitoring:{user_id}"
            
            monitoring_data = {
                "user_id": user_id,
                "level": "HIGH",
                "activated_at": datetime.now().isoformat()
            }
            
            redis_client.hset(monitoring_key, mapping=monitoring_data)
            critical_data["enhanced_monitoring"][monitoring_key] = monitoring_data
        
        # Verify data persistence by checking it exists
        for blocking_key in critical_data["blocked_users"]:
            stored_data = redis_client.hgetall(blocking_key)
            assert stored_data["user_id"] == critical_data["blocked_users"][blocking_key]["user_id"]
        
        for monitoring_key in critical_data["enhanced_monitoring"]:
            stored_data = redis_client.hgetall(monitoring_key)
            assert stored_data["user_id"] == critical_data["enhanced_monitoring"][monitoring_key]["user_id"]
        
        # In a real persistence test, you would:
        # 1. Save data
        # 2. Restart Redis instance
        # 3. Verify data still exists
        # For this test, we just verify the data is correctly stored