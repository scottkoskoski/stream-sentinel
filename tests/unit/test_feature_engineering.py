"""
Comprehensive Unit Tests for Feature Engineering

Tests all 20+ real-time features used in fraud detection with
complete edge case coverage and accuracy validation.
"""

import pytest
import json
import redis
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from dataclasses import asdict

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.fraud_detector import FraudDetector, UserProfile, FeatureSet


class TestFeatureEngineering:
    """Comprehensive feature engineering tests."""

    def setup_method(self):
        """Setup for each test method."""
        # Mock Redis client
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

    def test_basic_transaction_features(self):
        """Test basic transaction-level features."""
        transaction = {
            "transaction_id": "txn_001",
            "user_id": "user_001",
            "amount": 250.50,
            "timestamp": "2023-08-15T14:30:00",
            "merchant_category": "grocery",
            "card_type": "credit"
        }
        
        features = self.fraud_detector.extract_transaction_features(transaction)
        
        # Validate basic features
        assert features["transaction_amount"] == 250.50
        assert features["hour_of_day"] == 14
        assert features["day_of_week"] == 1  # Tuesday
        assert features["merchant_category_encoded"] > 0
        assert features["card_type_encoded"] > 0
        assert features["amount_log"] == pytest.approx(np.log1p(250.50), rel=1e-6)

    def test_temporal_features(self):
        """Test temporal pattern features."""
        # Test peak fraud hour (8 AM)
        peak_transaction = {
            "transaction_id": "txn_peak",
            "user_id": "user_test",
            "amount": 100.0,
            "timestamp": "2023-08-15T08:00:00"
        }
        
        peak_features = self.fraud_detector.extract_transaction_features(peak_transaction)
        assert peak_features["is_peak_fraud_hour"] == 1
        assert peak_features["hour_of_day"] == 8

        # Test normal hour
        normal_transaction = {
            "transaction_id": "txn_normal", 
            "user_id": "user_test",
            "amount": 100.0,
            "timestamp": "2023-08-15T14:00:00"
        }
        
        normal_features = self.fraud_detector.extract_transaction_features(normal_transaction)
        assert normal_features["is_peak_fraud_hour"] == 0
        assert normal_features["hour_of_day"] == 14

    def test_amount_based_features(self):
        """Test amount-related features."""
        # Small amount (high fraud risk)
        small_transaction = {
            "transaction_id": "txn_small",
            "user_id": "user_test", 
            "amount": 5.99,
            "timestamp": "2023-08-15T14:00:00"
        }
        
        small_features = self.fraud_detector.extract_transaction_features(small_transaction)
        assert small_features["is_small_amount"] == 1
        assert small_features["transaction_amount"] == 5.99
        assert small_features["amount_log"] == pytest.approx(np.log1p(5.99), rel=1e-6)

        # Large amount 
        large_transaction = {
            "transaction_id": "txn_large",
            "user_id": "user_test",
            "amount": 2500.00,
            "timestamp": "2023-08-15T14:00:00"
        }
        
        large_features = self.fraud_detector.extract_transaction_features(large_transaction)
        assert large_features["is_small_amount"] == 0
        assert large_features["transaction_amount"] == 2500.00
        assert large_features["is_large_amount"] == 1

    def test_user_behavioral_features_new_user(self):
        """Test behavioral features for new users."""
        # Mock Redis to return None (new user)
        self.mock_redis.hgetall.return_value = {}
        
        transaction = {
            "transaction_id": "txn_new_user",
            "user_id": "new_user_001",
            "amount": 50.00,
            "timestamp": "2023-08-15T14:00:00"
        }
        
        features = self.fraud_detector.extract_user_features(transaction)
        
        # New user features
        assert features["is_new_user"] == 1
        assert features["user_transaction_count"] == 0
        assert features["user_avg_amount"] == 0.0
        assert features["deviation_from_avg"] == 0.0
        assert features["days_since_last_transaction"] == 999  # Large value for new users

    def test_user_behavioral_features_existing_user(self):
        """Test behavioral features for existing users."""
        # Mock existing user profile
        existing_profile = {
            "total_transactions": "150",
            "total_amount": "3000.00",
            "avg_transaction_amount": "20.00",
            "last_transaction_time": "2023-08-14T12:00:00",
            "last_transaction_amount": "25.00",
            "daily_transaction_count": "3",
            "daily_amount": "75.00",
            "suspicious_activity_count": "2"
        }
        
        self.mock_redis.hgetall.return_value = existing_profile
        
        transaction = {
            "transaction_id": "txn_existing_user",
            "user_id": "existing_user_001", 
            "amount": 100.00,
            "timestamp": "2023-08-15T14:00:00"
        }
        
        features = self.fraud_detector.extract_user_features(transaction)
        
        # Existing user features
        assert features["is_new_user"] == 0
        assert features["user_transaction_count"] == 150
        assert features["user_avg_amount"] == 20.00
        assert features["deviation_from_avg"] == 4.0  # (100-20)/20
        assert features["daily_transaction_count"] == 3
        assert features["days_since_last_transaction"] == 1  # About 1 day

    def test_velocity_features(self):
        """Test transaction velocity features."""
        # Mock recent transactions for velocity calculation
        recent_transactions = [
            {"timestamp": "2023-08-15T13:55:00", "amount": "25.00"},
            {"timestamp": "2023-08-15T13:58:00", "amount": "30.00"},
            {"timestamp": "2023-08-15T13:59:30", "amount": "45.00"}
        ]
        
        self.mock_redis.lrange.return_value = [json.dumps(tx) for tx in recent_transactions]
        
        transaction = {
            "transaction_id": "txn_velocity",
            "user_id": "user_velocity",
            "amount": 50.00,
            "timestamp": "2023-08-15T14:00:00"
        }
        
        features = self.fraud_detector.extract_velocity_features(transaction)
        
        # Velocity features
        assert features["transactions_last_hour"] == 3
        assert features["amount_last_hour"] == 100.00  # 25+30+45
        assert features["transactions_last_10min"] >= 1  # At least one in last 10 minutes
        assert features["avg_time_between_transactions"] > 0

    def test_high_velocity_detection(self):
        """Test high velocity transaction detection."""
        # Mock many recent transactions
        recent_transactions = []
        base_time = datetime(2023, 8, 15, 13, 45, 0)
        
        for i in range(15):  # 15 transactions in 15 minutes
            tx_time = base_time + timedelta(minutes=i)
            recent_transactions.append({
                "timestamp": tx_time.isoformat(),
                "amount": "20.00"
            })
        
        self.mock_redis.lrange.return_value = [json.dumps(tx) for tx in recent_transactions]
        
        transaction = {
            "transaction_id": "txn_high_velocity",
            "user_id": "user_high_velocity", 
            "amount": 25.00,
            "timestamp": "2023-08-15T14:00:00"
        }
        
        features = self.fraud_detector.extract_velocity_features(transaction)
        
        # High velocity indicators
        assert features["transactions_last_hour"] == 15
        assert features["is_high_velocity"] == 1
        assert features["avg_time_between_transactions"] < 5  # Less than 5 minutes average

    def test_merchant_category_encoding(self):
        """Test merchant category encoding consistency."""
        categories = ["grocery", "gas", "restaurant", "online", "retail", "unknown"]
        encoded_values = set()
        
        for category in categories:
            transaction = {
                "transaction_id": f"txn_{category}",
                "user_id": "user_test",
                "amount": 100.00,
                "timestamp": "2023-08-15T14:00:00",
                "merchant_category": category
            }
            
            features = self.fraud_detector.extract_transaction_features(transaction)
            encoded_value = features["merchant_category_encoded"]
            
            # Ensure consistent encoding
            assert encoded_value >= 0
            assert encoded_value not in encoded_values  # Each category should have unique encoding
            encoded_values.add(encoded_value)

    def test_weekend_detection(self):
        """Test weekend/weekday detection."""
        # Test weekend (Saturday)
        weekend_transaction = {
            "transaction_id": "txn_weekend",
            "user_id": "user_test",
            "amount": 100.00,
            "timestamp": "2023-08-12T14:00:00"  # Saturday
        }
        
        weekend_features = self.fraud_detector.extract_transaction_features(weekend_transaction)
        assert weekend_features["is_weekend"] == 1
        assert weekend_features["day_of_week"] == 5  # Saturday

        # Test weekday (Monday)
        weekday_transaction = {
            "transaction_id": "txn_weekday",
            "user_id": "user_test", 
            "amount": 100.00,
            "timestamp": "2023-08-14T14:00:00"  # Monday
        }
        
        weekday_features = self.fraud_detector.extract_transaction_features(weekday_transaction)
        assert weekday_features["is_weekend"] == 0
        assert weekday_features["day_of_week"] == 0  # Monday

    def test_feature_completeness(self):
        """Test that all expected features are generated."""
        # Mock comprehensive user profile
        user_profile = {
            "total_transactions": "100",
            "total_amount": "2000.00", 
            "avg_transaction_amount": "20.00",
            "last_transaction_time": "2023-08-14T12:00:00",
            "last_transaction_amount": "25.00",
            "daily_transaction_count": "5",
            "daily_amount": "100.00",
            "suspicious_activity_count": "1"
        }
        
        self.mock_redis.hgetall.return_value = user_profile
        self.mock_redis.lrange.return_value = []  # No recent transactions
        
        transaction = {
            "transaction_id": "txn_complete",
            "user_id": "user_complete",
            "amount": 75.00,
            "timestamp": "2023-08-15T14:30:00",
            "merchant_category": "grocery",
            "card_type": "credit"
        }
        
        # Extract all features
        tx_features = self.fraud_detector.extract_transaction_features(transaction)
        user_features = self.fraud_detector.extract_user_features(transaction)
        velocity_features = self.fraud_detector.extract_velocity_features(transaction)
        
        # Combine all features
        all_features = {**tx_features, **user_features, **velocity_features}
        
        # Expected feature list (25 features total)
        expected_features = [
            "transaction_amount", "amount_log", "hour_of_day", "day_of_week", 
            "is_weekend", "is_peak_fraud_hour", "is_small_amount", "is_large_amount",
            "merchant_category_encoded", "card_type_encoded", "is_new_user",
            "user_transaction_count", "user_total_amount", "user_avg_amount", 
            "deviation_from_avg", "daily_transaction_count", "daily_amount_spent",
            "days_since_last_transaction", "amount_ratio_to_last", "suspicious_activity_count",
            "transactions_last_hour", "amount_last_hour", "transactions_last_10min",
            "avg_time_between_transactions", "is_high_velocity"
        ]
        
        # Verify all features are present
        for feature in expected_features:
            assert feature in all_features, f"Missing feature: {feature}"
        
        # Verify feature count
        assert len(all_features) >= len(expected_features)

    def test_feature_data_types(self):
        """Test that features have correct data types."""
        transaction = {
            "transaction_id": "txn_types",
            "user_id": "user_types",
            "amount": 100.00,
            "timestamp": "2023-08-15T14:00:00",
            "merchant_category": "grocery",
            "card_type": "credit"
        }
        
        # Mock user profile
        self.mock_redis.hgetall.return_value = {
            "total_transactions": "50",
            "avg_transaction_amount": "30.00"
        }
        self.mock_redis.lrange.return_value = []
        
        # Extract features
        tx_features = self.fraud_detector.extract_transaction_features(transaction)
        user_features = self.fraud_detector.extract_user_features(transaction)
        velocity_features = self.fraud_detector.extract_velocity_features(transaction)
        
        all_features = {**tx_features, **user_features, **velocity_features}
        
        # Check data types
        float_features = ["transaction_amount", "amount_log", "user_avg_amount", "deviation_from_avg"]
        int_features = ["hour_of_day", "day_of_week", "is_weekend", "user_transaction_count"]
        
        for feature in float_features:
            if feature in all_features:
                assert isinstance(all_features[feature], (int, float)), f"{feature} should be numeric"
        
        for feature in int_features:
            if feature in all_features:
                assert isinstance(all_features[feature], int), f"{feature} should be integer"

    def test_edge_case_handling(self):
        """Test edge case handling in feature engineering."""
        # Test with missing fields
        incomplete_transaction = {
            "transaction_id": "txn_incomplete",
            "user_id": "user_incomplete",
            "amount": 50.00
            # Missing timestamp, merchant_category, card_type
        }
        
        # Should handle gracefully without raising exceptions
        try:
            features = self.fraud_detector.extract_transaction_features(incomplete_transaction)
            assert "transaction_amount" in features
        except Exception as e:
            pytest.fail(f"Should handle incomplete transactions gracefully: {e}")

        # Test with invalid timestamp
        invalid_timestamp_transaction = {
            "transaction_id": "txn_invalid_time",
            "user_id": "user_test",
            "amount": 50.00,
            "timestamp": "invalid-timestamp"
        }
        
        try:
            features = self.fraud_detector.extract_transaction_features(invalid_timestamp_transaction)
            assert "transaction_amount" in features
        except Exception as e:
            pytest.fail(f"Should handle invalid timestamps gracefully: {e}")

    def test_redis_connection_failure(self):
        """Test behavior when Redis connection fails."""
        # Mock Redis connection failure
        self.mock_redis.hgetall.side_effect = redis.ConnectionError("Connection failed")
        
        transaction = {
            "transaction_id": "txn_redis_fail",
            "user_id": "user_redis_fail",
            "amount": 100.00,
            "timestamp": "2023-08-15T14:00:00"
        }
        
        # Should handle Redis failure gracefully
        features = self.fraud_detector.extract_user_features(transaction)
        
        # Should default to new user features
        assert features["is_new_user"] == 1
        assert features["user_transaction_count"] == 0

    @pytest.mark.parametrize("amount,expected_small,expected_large", [
        (5.0, 1, 0),      # Small amount
        (50.0, 0, 0),     # Normal amount
        (2000.0, 0, 1),   # Large amount
        (9.99, 1, 0),     # Boundary case - small
        (10.01, 0, 0),    # Boundary case - not small
    ])
    def test_amount_categorization_parametrized(self, amount, expected_small, expected_large):
        """Test amount categorization with various values."""
        transaction = {
            "transaction_id": f"txn_{amount}",
            "user_id": "user_test",
            "amount": amount,
            "timestamp": "2023-08-15T14:00:00"
        }
        
        features = self.fraud_detector.extract_transaction_features(transaction)
        
        assert features["is_small_amount"] == expected_small
        assert features["is_large_amount"] == expected_large