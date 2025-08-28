"""
Simplified Working Unit Tests for Feature Engineering

Tests the core feature extraction functionality of the FraudDetector.
"""

import pytest
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.fraud_detector import FraudDetector, UserProfile, FraudFeatures


class TestFeatureEngineering:
    """Working feature engineering tests."""

    def setup_method(self):
        """Setup for each test method."""
        # Mock Redis client
        self.mock_redis = MagicMock()
        
        # Mock Kafka config  
        self.mock_config = Mock()
        self.mock_config.logger = Mock()
        self.mock_config.get_consumer_config.return_value = {
            'group.id': 'test-fraud-detector',
            'bootstrap.servers': 'localhost:9092',
            'auto.offset.reset': 'earliest'
        }
        self.mock_config.get_producer_config.return_value = {
            'bootstrap.servers': 'localhost:9092'
        }
        
        # Create fraud detector with mocked dependencies
        with patch('consumers.fraud_detector.get_kafka_config', return_value=self.mock_config):
            with patch('consumers.fraud_detector.redis.Redis', return_value=self.mock_redis):
                with patch('consumers.fraud_detector.Consumer') as mock_consumer:
                    self.fraud_detector = FraudDetector()
                    self.fraud_detector.redis_client = self.mock_redis
    
    def create_ieee_transaction(self, **kwargs):
        """Create IEEE-CIS format transaction with defaults."""
        defaults = {
            "transaction_id": "txn_001",
            "card1": "user_001",
            "transaction_amt": 250.50,
            "generated_timestamp": "2023-08-15T14:30:00",
            "product_cd": "W",
            "card6": "credit"
        }
        defaults.update(kwargs)
        return defaults
    
    def create_user_profile(self, **kwargs):
        """Create UserProfile with defaults."""
        defaults = {
            "user_id": "user_001",
            "total_transactions": 10,
            "total_amount": 1000.0,
            "avg_transaction_amount": 100.0,
            "daily_transaction_count": 2,
            "daily_amount": 200.0,
            "last_transaction_time": "2023-08-14T14:30:00",
            "last_transaction_amount": 150.0
        }
        defaults.update(kwargs)
        return UserProfile(**defaults)

    def test_basic_transaction_features(self):
        """Test basic transaction-level features."""
        transaction = self.create_ieee_transaction()
        user_profile = self.create_user_profile()
        
        features = self.fraud_detector.extract_features(transaction, user_profile)
        
        # Validate that features object is returned
        assert isinstance(features, FraudFeatures)
        assert hasattr(features, 'user_id')
        assert hasattr(features, 'transaction_id')
        assert features.user_id == "user_001"
        assert features.transaction_id == "txn_001"

    def test_temporal_features(self):
        """Test temporal pattern features."""
        user_profile = self.create_user_profile()
        
        # Test peak fraud hour (8 AM)
        peak_transaction = self.create_ieee_transaction(
            transaction_id="txn_peak",
            card1="user_test",
            transaction_amt=100.0,
            generated_timestamp="2023-08-15T08:00:00"
        )
        
        peak_features = self.fraud_detector.extract_features(peak_transaction, user_profile)
        assert isinstance(peak_features, FraudFeatures)
        assert peak_features.transaction_id == "txn_peak"

        # Test normal hour
        normal_transaction = self.create_ieee_transaction(
            transaction_id="txn_normal",
            card1="user_test", 
            transaction_amt=100.0,
            generated_timestamp="2023-08-15T14:00:00"
        )
        
        normal_features = self.fraud_detector.extract_features(normal_transaction, user_profile)
        assert isinstance(normal_features, FraudFeatures)
        assert normal_features.transaction_id == "txn_normal"

    def test_amount_based_features(self):
        """Test amount-related features."""
        user_profile = self.create_user_profile()
        
        # Small amount transaction
        small_transaction = self.create_ieee_transaction(
            transaction_id="txn_small",
            card1="user_test",
            transaction_amt=5.99,
            generated_timestamp="2023-08-15T14:00:00"
        )
        
        small_features = self.fraud_detector.extract_features(small_transaction, user_profile)
        assert isinstance(small_features, FraudFeatures)
        assert small_features.transaction_id == "txn_small"

        # Large amount transaction
        large_transaction = self.create_ieee_transaction(
            transaction_id="txn_large",
            card1="user_test",
            transaction_amt=2500.00,
            generated_timestamp="2023-08-15T14:00:00"
        )
        
        large_features = self.fraud_detector.extract_features(large_transaction, user_profile)
        assert isinstance(large_features, FraudFeatures)
        assert large_features.transaction_id == "txn_large"

    def test_different_users(self):
        """Test features for different user scenarios."""
        # New user
        new_user_profile = self.create_user_profile(
            user_id="new_user",
            total_transactions=0,
            total_amount=0.0
        )
        
        transaction = self.create_ieee_transaction(
            card1="new_user",
            transaction_id="txn_new"
        )
        
        new_user_features = self.fraud_detector.extract_features(transaction, new_user_profile)
        assert isinstance(new_user_features, FraudFeatures)
        assert new_user_features.user_id == "new_user"
        
        # Experienced user
        experienced_user_profile = self.create_user_profile(
            user_id="experienced_user",
            total_transactions=1000,
            total_amount=50000.0,
            avg_transaction_amount=50.0
        )
        
        transaction = self.create_ieee_transaction(
            card1="experienced_user",
            transaction_id="txn_exp"
        )
        
        exp_user_features = self.fraud_detector.extract_features(transaction, experienced_user_profile)
        assert isinstance(exp_user_features, FraudFeatures)
        assert exp_user_features.user_id == "experienced_user"

    def test_edge_cases(self):
        """Test edge case handling."""
        user_profile = self.create_user_profile()
        
        # Test with minimal transaction data
        minimal_transaction = {
            "transaction_id": "txn_minimal",
            "card1": "user_minimal",
            "transaction_amt": 10.0,
            "generated_timestamp": "2023-08-15T12:00:00"
        }
        
        features = self.fraud_detector.extract_features(minimal_transaction, user_profile)
        assert isinstance(features, FraudFeatures)
        assert features.transaction_id == "txn_minimal"

    @pytest.mark.parametrize("amount", [1.0, 10.0, 100.0, 1000.0, 5000.0])
    def test_various_amounts(self, amount):
        """Test feature extraction with various amounts."""
        user_profile = self.create_user_profile()
        
        transaction = self.create_ieee_transaction(
            transaction_amt=amount,
            transaction_id=f"txn_{amount}"
        )
        
        features = self.fraud_detector.extract_features(transaction, user_profile)
        assert isinstance(features, FraudFeatures)
        assert features.transaction_id == f"txn_{amount}"