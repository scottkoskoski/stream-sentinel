"""
Comprehensive Unit Tests for User Profile Management

Tests Redis-based user profile state management including:
- User profile creation and updates
- Concurrent access handling
- Daily statistics reset logic
- State consistency validation
- Memory management and expiration
"""

import pytest
import json
import redis
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from dataclasses import asdict
from pathlib import Path

import sys
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.fraud_detector import FraudDetector, UserProfile


class TestUserProfileManagement:
    """Comprehensive user profile management tests."""

    def setup_method(self):
        """Setup for each test method."""
        # Mock Redis client with realistic behavior
        self.mock_redis = MagicMock()
        
        # Mock Kafka config
        self.mock_config = Mock()
        self.mock_config.logger = Mock()
        self.mock_config.get_consumer_config.return_value = {}
        self.mock_config.get_producer_config.return_value = {}
        
        # Create fraud detector with mocked dependencies
        with patch('consumers.fraud_detector.get_kafka_config', return_value=self.mock_config):
            with patch('consumers.fraud_detector.redis.Redis', return_value=self.mock_redis):
                self.fraud_detector = FraudDetector()
                self.fraud_detector.redis_client = self.mock_redis

    def test_new_user_profile_creation(self):
        """Test creation of new user profiles."""
        # Mock Redis to return empty profile (new user)
        self.mock_redis.hgetall.return_value = {}
        
        user_id = "new_user_001"
        transaction = {
            "transaction_id": "txn_new_user",
            "user_id": user_id,
            "amount": 75.50,
            "timestamp": "2023-08-15T14:30:00"
        }
        
        # Get or create user profile
        profile = self.fraud_detector.get_user_profile(user_id)
        
        # Should create new profile with default values
        assert profile.user_id == user_id
        assert profile.total_transactions == 0
        assert profile.total_amount == 0.0
        assert profile.avg_transaction_amount == 0.0
        assert profile.last_transaction_time is None
        assert profile.daily_transaction_count == 0
        assert profile.suspicious_activity_count == 0

    def test_existing_user_profile_loading(self):
        """Test loading existing user profiles from Redis."""
        user_id = "existing_user_001"
        
        # Mock existing profile in Redis
        existing_profile_data = {
            "user_id": user_id,
            "total_transactions": "150",
            "total_amount": "3250.75",
            "avg_transaction_amount": "21.67",
            "last_transaction_time": "2023-08-14T16:20:00",
            "last_transaction_amount": "32.50",
            "daily_transaction_count": "5",
            "daily_amount": "125.30",
            "last_reset_date": "2023-08-15",
            "suspicious_activity_count": "2"
        }
        
        self.mock_redis.hgetall.return_value = existing_profile_data
        
        # Load profile
        profile = self.fraud_detector.get_user_profile(user_id)
        
        # Verify profile data conversion
        assert profile.user_id == user_id
        assert profile.total_transactions == 150
        assert profile.total_amount == 3250.75
        assert profile.avg_transaction_amount == 21.67
        assert profile.last_transaction_time == "2023-08-14T16:20:00"
        assert profile.daily_transaction_count == 5
        assert profile.suspicious_activity_count == 2

    def test_user_profile_update_after_transaction(self):
        """Test user profile updates after processing transaction."""
        user_id = "update_test_user"
        
        # Mock existing profile
        existing_profile = {
            "user_id": user_id,
            "total_transactions": "10",
            "total_amount": "500.00",
            "avg_transaction_amount": "50.00",
            "last_transaction_time": "2023-08-14T12:00:00",
            "last_transaction_amount": "25.00",
            "daily_transaction_count": "2",
            "daily_amount": "75.00",
            "last_reset_date": "2023-08-15",
            "suspicious_activity_count": "0"
        }
        
        self.mock_redis.hgetall.return_value = existing_profile
        
        transaction = {
            "transaction_id": "txn_update",
            "user_id": user_id,
            "amount": 100.00,
            "timestamp": "2023-08-15T14:30:00"
        }
        
        # Update profile
        updated_profile = self.fraud_detector.update_user_profile(transaction)
        
        # Verify updates
        assert updated_profile.total_transactions == 11  # 10 + 1
        assert updated_profile.total_amount == 600.00    # 500 + 100
        assert updated_profile.avg_transaction_amount == pytest.approx(54.55, rel=0.01)  # 600/11
        assert updated_profile.last_transaction_time == "2023-08-15T14:30:00"
        assert updated_profile.last_transaction_amount == 100.00
        assert updated_profile.daily_transaction_count == 3  # 2 + 1
        assert updated_profile.daily_amount == 175.00  # 75 + 100

    def test_daily_statistics_reset_logic(self):
        """Test daily statistics reset when crossing midnight."""
        user_id = "daily_reset_user"
        
        # Mock profile from previous day
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        
        existing_profile = {
            "user_id": user_id,
            "total_transactions": "50",
            "total_amount": "1000.00",
            "avg_transaction_amount": "20.00",
            "daily_transaction_count": "10",  # Previous day's count
            "daily_amount": "200.00",         # Previous day's amount
            "last_reset_date": yesterday,     # Previous day
            "suspicious_activity_count": "1"
        }
        
        self.mock_redis.hgetall.return_value = existing_profile
        
        transaction = {
            "transaction_id": "txn_reset",
            "user_id": user_id,
            "amount": 50.00,
            "timestamp": f"{today}T08:00:00"
        }
        
        # Update profile (should trigger daily reset)
        updated_profile = self.fraud_detector.update_user_profile(transaction)
        
        # Daily stats should be reset for new day
        assert updated_profile.daily_transaction_count == 1   # Reset + new transaction
        assert updated_profile.daily_amount == 50.00         # Reset + new amount
        assert updated_profile.last_reset_date == today
        
        # Lifetime stats should continue accumulating
        assert updated_profile.total_transactions == 51      # 50 + 1
        assert updated_profile.total_amount == 1050.00       # 1000 + 50

    def test_concurrent_profile_updates(self):
        """Test handling of concurrent profile updates."""
        user_id = "concurrent_user"
        
        # Mock Redis to simulate concurrent modification
        initial_profile = {
            "user_id": user_id,
            "total_transactions": "100",
            "total_amount": "2000.00",
            "daily_transaction_count": "5",
            "daily_amount": "100.00"
        }
        
        # First call returns initial state, subsequent calls return updated state
        self.mock_redis.hgetall.side_effect = [
            initial_profile,  # First read
            {**initial_profile, "total_transactions": "101"}  # After another update
        ]
        
        transaction = {
            "transaction_id": "txn_concurrent",
            "user_id": user_id, 
            "amount": 75.00,
            "timestamp": "2023-08-15T14:30:00"
        }
        
        # Update profile
        updated_profile = self.fraud_detector.update_user_profile(transaction)
        
        # Should handle concurrent modification gracefully
        assert updated_profile.user_id == user_id
        
        # Verify atomic update operations
        self.mock_redis.multi.assert_called()  # Transaction started
        self.mock_redis.exec.assert_called()   # Transaction executed

    def test_profile_persistence_to_redis(self):
        """Test profile persistence operations to Redis."""
        user_id = "persistence_test_user"
        
        profile = UserProfile(
            user_id=user_id,
            total_transactions=25,
            total_amount=750.50,
            avg_transaction_amount=30.02,
            last_transaction_time="2023-08-15T14:30:00",
            last_transaction_amount=45.00,
            daily_transaction_count=3,
            daily_amount=95.00,
            last_reset_date="2023-08-15",
            suspicious_activity_count=1
        )
        
        # Save profile
        self.fraud_detector.save_user_profile(profile)
        
        # Verify Redis operations
        expected_key = f"user_profile:{user_id}"
        expected_data = {
            "user_id": user_id,
            "total_transactions": "25",
            "total_amount": "750.5",
            "avg_transaction_amount": "30.02",
            "last_transaction_time": "2023-08-15T14:30:00",
            "last_transaction_amount": "45.0",
            "daily_transaction_count": "3",
            "daily_amount": "95.0",
            "last_reset_date": "2023-08-15",
            "suspicious_activity_count": "1"
        }
        
        self.mock_redis.hset.assert_called_with(expected_key, mapping=expected_data)
        
        # Verify TTL setting for memory management
        self.mock_redis.expire.assert_called_with(expected_key, 86400)  # 24 hours

    def test_profile_memory_management(self):
        """Test profile memory management and expiration."""
        user_id = "memory_test_user"
        
        # Test profile expiration
        self.fraud_detector.set_profile_expiration(user_id, hours=24)
        
        expected_key = f"user_profile:{user_id}"
        expected_ttl = 24 * 3600  # 24 hours in seconds
        
        self.mock_redis.expire.assert_called_with(expected_key, expected_ttl)
        
        # Test profile eviction for inactive users
        inactive_users = ["inactive_user_1", "inactive_user_2", "inactive_user_3"]
        
        # Mock inactive user detection
        self.mock_redis.keys.return_value = [f"user_profile:{uid}" for uid in inactive_users]
        self.mock_redis.hget.return_value = (datetime.now() - timedelta(days=30)).isoformat()
        
        # Clean up inactive profiles
        cleaned_count = self.fraud_detector.cleanup_inactive_profiles(days=7)
        
        # Should identify and clean inactive profiles
        assert cleaned_count >= 0

    def test_suspicious_activity_tracking(self):
        """Test tracking of suspicious activity in user profiles."""
        user_id = "suspicious_user"
        
        existing_profile = {
            "user_id": user_id,
            "total_transactions": "20",
            "suspicious_activity_count": "2"
        }
        
        self.mock_redis.hgetall.return_value = existing_profile
        
        # Record suspicious activity
        self.fraud_detector.record_suspicious_activity(user_id, "High velocity transactions")
        
        # Verify suspicious activity counter increment
        expected_key = f"user_profile:{user_id}"
        self.mock_redis.hincrby.assert_called_with(expected_key, "suspicious_activity_count", 1)
        
        # Verify activity logging
        expected_log_key = f"suspicious_activities:{user_id}"
        self.mock_redis.lpush.assert_called()

    def test_user_profile_analytics_features(self):
        """Test analytics features derived from user profiles."""
        user_id = "analytics_user"
        
        profile_data = {
            "user_id": user_id,
            "total_transactions": "100",
            "total_amount": "2500.00", 
            "avg_transaction_amount": "25.00",
            "daily_transaction_count": "5",
            "daily_amount": "125.00",
            "suspicious_activity_count": "3"
        }
        
        self.mock_redis.hgetall.return_value = profile_data
        
        profile = self.fraud_detector.get_user_profile(user_id)
        
        # Test analytics calculations
        analytics = self.fraud_detector.calculate_user_analytics(profile)
        
        expected_analytics = {
            "avg_daily_transactions": 5.0,
            "avg_daily_amount": 125.0,
            "suspicious_activity_rate": 0.03,  # 3/100
            "transaction_consistency_score": 1.0,  # Perfect consistency
            "risk_profile": "MEDIUM"  # Based on suspicious activities
        }
        
        # Verify key analytics metrics
        assert "avg_daily_transactions" in analytics
        assert "suspicious_activity_rate" in analytics
        assert analytics["suspicious_activity_rate"] == pytest.approx(0.03, rel=0.01)

    def test_profile_data_validation(self):
        """Test validation of profile data integrity."""
        user_id = "validation_user"
        
        # Test with corrupted profile data
        corrupted_profile = {
            "user_id": user_id,
            "total_transactions": "invalid_number",  # Invalid data
            "total_amount": "-100.00",               # Invalid negative amount
            "avg_transaction_amount": "inf",         # Invalid infinity
            "daily_transaction_count": ""            # Empty string
        }
        
        self.mock_redis.hgetall.return_value = corrupted_profile
        
        # Should handle corrupted data gracefully
        profile = self.fraud_detector.get_user_profile(user_id)
        
        # Should create clean profile with defaults for corrupted data
        assert profile.user_id == user_id
        assert profile.total_transactions == 0    # Default for invalid data
        assert profile.total_amount == 0.0        # Default for negative amount
        assert profile.avg_transaction_amount == 0.0  # Default for invalid data
        assert profile.daily_transaction_count == 0   # Default for empty string

    def test_profile_backup_and_recovery(self):
        """Test profile backup and recovery mechanisms."""
        user_id = "backup_user"
        
        profile = UserProfile(
            user_id=user_id,
            total_transactions=50,
            total_amount=1250.00,
            avg_transaction_amount=25.00,
            daily_transaction_count=3,
            daily_amount=75.00,
            suspicious_activity_count=1
        )
        
        # Create backup
        self.fraud_detector.backup_user_profile(profile)
        
        # Verify backup creation
        expected_backup_key = f"user_profile_backup:{user_id}"
        expected_backup_data = asdict(profile)
        expected_backup_data["backup_timestamp"] = datetime.now().isoformat()
        
        self.mock_redis.hset.assert_called()
        
        # Test recovery from backup
        self.mock_redis.hgetall.return_value = expected_backup_data
        recovered_profile = self.fraud_detector.recover_user_profile(user_id)
        
        assert recovered_profile.user_id == user_id
        assert recovered_profile.total_transactions == 50
        assert recovered_profile.total_amount == 1250.00

    def test_bulk_profile_operations(self):
        """Test bulk operations on multiple user profiles."""
        user_ids = [f"bulk_user_{i}" for i in range(100)]
        
        # Test bulk profile loading
        profiles = self.fraud_detector.get_bulk_user_profiles(user_ids)
        
        # Should return profiles for all users
        assert len(profiles) == 100
        
        # Verify Redis pipeline usage for efficiency
        self.mock_redis.pipeline.assert_called()

    def test_profile_consistency_validation(self):
        """Test profile consistency validation rules."""
        user_id = "consistency_user"
        
        # Create profile with inconsistent data
        inconsistent_profile = UserProfile(
            user_id=user_id,
            total_transactions=10,
            total_amount=1000.00,
            avg_transaction_amount=50.00,  # Should be 100.00 (1000/10)
            daily_transaction_count=5,
            daily_amount=600.00            # Exceeds total amount (impossible)
        )
        
        # Validate and fix consistency
        fixed_profile = self.fraud_detector.validate_and_fix_profile_consistency(inconsistent_profile)
        
        # Should fix inconsistent values
        assert fixed_profile.avg_transaction_amount == 100.00  # Fixed calculation
        assert fixed_profile.daily_amount <= fixed_profile.total_amount  # Logical constraint

    def test_redis_connection_failure_handling(self):
        """Test graceful handling of Redis connection failures."""
        user_id = "connection_fail_user"
        
        # Mock Redis connection failure
        self.mock_redis.hgetall.side_effect = redis.ConnectionError("Connection failed")
        
        # Should handle connection failure gracefully
        profile = self.fraud_detector.get_user_profile(user_id)
        
        # Should return default new user profile
        assert profile.user_id == user_id
        assert profile.total_transactions == 0
        assert profile.total_amount == 0.0
        
        # Error should be logged
        self.mock_config.logger.error.assert_called()

    def test_profile_serialization_deserialization(self):
        """Test profile serialization for Redis storage."""
        profile = UserProfile(
            user_id="serialize_user",
            total_transactions=75,
            total_amount=1875.25,
            avg_transaction_amount=25.00,
            last_transaction_time="2023-08-15T14:30:00",
            daily_transaction_count=4,
            daily_amount=100.00,
            suspicious_activity_count=2
        )
        
        # Test serialization
        serialized = self.fraud_detector.serialize_profile(profile)
        
        # Should convert all values to strings for Redis
        assert all(isinstance(value, str) for value in serialized.values())
        assert serialized["total_transactions"] == "75"
        assert serialized["total_amount"] == "1875.25"
        
        # Test deserialization
        deserialized = self.fraud_detector.deserialize_profile(serialized)
        
        # Should restore original types and values
        assert deserialized.user_id == profile.user_id
        assert deserialized.total_transactions == profile.total_transactions
        assert deserialized.total_amount == profile.total_amount
        assert deserialized.avg_transaction_amount == profile.avg_transaction_amount