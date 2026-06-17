"""
Unit Tests for Feature Engineering

Tests the feature extraction functionality of the FraudDetector with
meaningful assertions on actual computed values, verifying that features
differ based on input data.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.fraud_detector import FraudDetector, UserProfile


class TestFeatureEngineering:
    """Feature engineering tests with value-level assertions."""

    def setup_method(self):
        """Setup for each test method."""
        self.mock_redis = MagicMock()
        self.mock_config = Mock()
        self.mock_config.logger = Mock()
        self.mock_config.get_consumer_config.return_value = {
            "group.id": "test-fraud-detector",
            "bootstrap.servers": "localhost:9092",
            "auto.offset.reset": "earliest",
        }
        self.mock_config.get_producer_config.return_value = {"bootstrap.servers": "localhost:9092"}

        with patch("consumers.fraud_detector.get_kafka_config", return_value=self.mock_config):
            with patch("consumers.fraud_detector.redis.Redis", return_value=self.mock_redis):
                with patch("consumers.fraud_detector.Consumer"):
                    with patch("consumers.fraud_detector.Producer"):
                        self.fraud_detector = FraudDetector(use_ml_model=False)
                        self.fraud_detector.redis_client = self.mock_redis

    def create_ieee_transaction(self, **kwargs):
        """Create IEEE-CIS format transaction with defaults."""
        defaults = {
            "transaction_id": "txn_001",
            "card1": "user_001",
            "transaction_amt": 250.50,
            "generated_timestamp": "2023-08-15T14:30:00",
            "product_cd": "W",
            "card6": "credit",
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
            "last_transaction_amount": 150.0,
        }
        defaults.update(kwargs)
        return UserProfile(**defaults)

    def test_basic_transaction_features_values(self):
        """Test that basic transaction features contain correct values."""
        transaction = self.create_ieee_transaction(transaction_amt=123.45, generated_timestamp="2023-08-15T16:45:00")
        user_profile = self.create_user_profile()

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.amount == 123.45
        assert features.transaction_hour == 16
        assert features.transaction_day == 1  # Tuesday
        assert features.user_id == "user_001"
        assert features.transaction_id == "txn_001"

    def test_temporal_features_peak_vs_normal(self):
        """Test that temporal features differ between unusual and normal hours."""
        user_profile = self.create_user_profile(last_transaction_time="2023-08-14T14:00:00")

        # Unusual hour: 3 AM
        unusual_txn = self.create_ieee_transaction(
            transaction_id="txn_unusual",
            transaction_amt=100.0,
            generated_timestamp="2023-08-15T03:00:00",
        )
        unusual_features = self.fraud_detector.extract_features(unusual_txn, user_profile)

        # Normal hour: 2 PM
        normal_txn = self.create_ieee_transaction(
            transaction_id="txn_normal",
            transaction_amt=100.0,
            generated_timestamp="2023-08-15T14:00:00",
        )
        normal_features = self.fraud_detector.extract_features(normal_txn, user_profile)

        # Unusual hour flag differs
        assert unusual_features.is_unusual_hour is True
        assert normal_features.is_unusual_hour is False
        assert unusual_features.transaction_hour == 3
        assert normal_features.transaction_hour == 14

        # Fraud score should be higher for unusual hour
        assert unusual_features.fraud_score > normal_features.fraud_score

    def test_temporal_features_boundary_hours(self):
        """Test the boundary between normal and unusual hours (6 and 22)."""
        user_profile = self.create_user_profile(
            avg_transaction_amount=100.0, last_transaction_time="2023-08-14T14:00:00"
        )

        # Hour 5 = unusual (< 6)
        txn_5am = self.create_ieee_transaction(transaction_amt=100.0, generated_timestamp="2023-08-15T05:00:00")
        features_5am = self.fraud_detector.extract_features(txn_5am, user_profile)
        assert features_5am.is_unusual_hour is True

        # Hour 6 = normal (not < 6 and not > 22)
        txn_6am = self.create_ieee_transaction(transaction_amt=100.0, generated_timestamp="2023-08-15T06:00:00")
        features_6am = self.fraud_detector.extract_features(txn_6am, user_profile)
        assert features_6am.is_unusual_hour is False

        # Hour 22 = normal
        txn_10pm = self.create_ieee_transaction(transaction_amt=100.0, generated_timestamp="2023-08-15T22:00:00")
        features_10pm = self.fraud_detector.extract_features(txn_10pm, user_profile)
        assert features_10pm.is_unusual_hour is False

        # Hour 23 = unusual (> 22)
        txn_11pm = self.create_ieee_transaction(transaction_amt=100.0, generated_timestamp="2023-08-15T23:00:00")
        features_11pm = self.fraud_detector.extract_features(txn_11pm, user_profile)
        assert features_11pm.is_unusual_hour is True

    def test_amount_ratio_computation(self):
        """Test amount_vs_avg_ratio is computed correctly."""
        user_profile = self.create_user_profile(
            avg_transaction_amount=100.0,
            last_transaction_time="2023-08-14T14:00:00",
            last_transaction_amount=100.0,
        )

        transaction = self.create_ieee_transaction(transaction_amt=350.0, generated_timestamp="2023-08-15T14:00:00")

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.amount_vs_avg_ratio == pytest.approx(3.5, rel=0.01)
        assert features.amount_vs_last_ratio == pytest.approx(3.5, rel=0.01)

    def test_amount_ratio_new_user_defaults(self):
        """Test that new user (avg=0) gets ratio defaulting to 1.0."""
        new_user = UserProfile(user_id="new_user")

        transaction = self.create_ieee_transaction(
            card1="new_user",
            transaction_amt=500.0,
            generated_timestamp="2023-08-15T14:00:00",
        )

        features = self.fraud_detector.extract_features(transaction, new_user)

        # avg = 0 -> default ratio 1.0
        assert features.amount_vs_avg_ratio == 1.0
        # last_amount = 0 -> default ratio 1.0
        assert features.amount_vs_last_ratio == 1.0

    def test_high_amount_threshold(self):
        """Test the $1000 high amount threshold boundary."""
        user_profile = self.create_user_profile(
            avg_transaction_amount=1000.0, last_transaction_time="2023-08-14T14:00:00"
        )

        # Exactly $1000 is NOT high (threshold is >1000)
        txn_1000 = self.create_ieee_transaction(transaction_amt=1000.0, generated_timestamp="2023-08-15T14:00:00")
        features_1000 = self.fraud_detector.extract_features(txn_1000, user_profile)
        assert features_1000.is_high_amount is False

        # $1001 IS high
        txn_1001 = self.create_ieee_transaction(transaction_amt=1001.0, generated_timestamp="2023-08-15T14:00:00")
        features_1001 = self.fraud_detector.extract_features(txn_1001, user_profile)
        assert features_1001.is_high_amount is True

    def test_rapid_transaction_threshold(self):
        """Test the 300-second rapid transaction threshold."""
        user_profile = self.create_user_profile(avg_transaction_amount=100.0)

        # 299 seconds = rapid
        user_profile.last_transaction_time = "2023-08-15T14:25:01"
        txn_rapid = self.create_ieee_transaction(transaction_amt=100.0, generated_timestamp="2023-08-15T14:30:00")
        features_rapid = self.fraud_detector.extract_features(txn_rapid, user_profile)
        assert features_rapid.is_rapid_transaction is True
        assert features_rapid.time_since_last_transaction == pytest.approx(299.0, abs=1.0)

        # 301 seconds = not rapid
        user_profile.last_transaction_time = "2023-08-15T14:24:59"
        txn_not_rapid = self.create_ieee_transaction(transaction_amt=100.0, generated_timestamp="2023-08-15T14:30:00")
        features_not_rapid = self.fraud_detector.extract_features(txn_not_rapid, user_profile)
        assert features_not_rapid.is_rapid_transaction is False
        assert features_not_rapid.time_since_last_transaction == pytest.approx(301.0, abs=1.0)

    def test_velocity_score_computation(self):
        """Test velocity score = daily_count / 24.0."""
        user_profile = self.create_user_profile(
            daily_transaction_count=48,
            avg_transaction_amount=100.0,
            last_transaction_time="2023-08-14T14:00:00",
        )

        transaction = self.create_ieee_transaction(transaction_amt=100.0, generated_timestamp="2023-08-15T14:00:00")

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.velocity_score == pytest.approx(2.0, rel=0.01)  # 48/24

    def test_velocity_score_zero_for_new_user(self):
        """Test that new user with 0 daily transactions has velocity 0."""
        new_user = UserProfile(user_id="new_user")

        transaction = self.create_ieee_transaction(
            card1="new_user",
            transaction_amt=100.0,
            generated_timestamp="2023-08-15T14:00:00",
        )

        features = self.fraud_detector.extract_features(transaction, new_user)

        assert features.velocity_score == 0.0
        assert features.daily_transaction_count == 0

    def test_small_vs_large_amount_features_differ(self):
        """Test that feature values actually differ between small and large amounts."""
        user_profile = self.create_user_profile(
            avg_transaction_amount=100.0,
            last_transaction_time="2023-08-14T14:00:00",
            last_transaction_amount=100.0,
        )

        small_txn = self.create_ieee_transaction(
            transaction_id="txn_small",
            transaction_amt=5.99,
            generated_timestamp="2023-08-15T14:00:00",
        )
        small_features = self.fraud_detector.extract_features(small_txn, user_profile)

        large_txn = self.create_ieee_transaction(
            transaction_id="txn_large",
            transaction_amt=2500.00,
            generated_timestamp="2023-08-15T14:00:00",
        )
        large_features = self.fraud_detector.extract_features(large_txn, user_profile)

        # Amount features should differ
        assert small_features.amount != large_features.amount
        assert small_features.amount_vs_avg_ratio != large_features.amount_vs_avg_ratio
        assert small_features.amount_vs_last_ratio != large_features.amount_vs_last_ratio
        assert small_features.is_high_amount != large_features.is_high_amount
        assert large_features.is_high_amount is True
        assert small_features.is_high_amount is False

        # Fraud scores should differ
        assert large_features.fraud_score > small_features.fraud_score

    def test_new_vs_experienced_user_features_differ(self):
        """Test that features differ for new users vs experienced users."""
        new_user = UserProfile(user_id="new_user")

        experienced_user = UserProfile(
            user_id="experienced_user",
            total_transactions=1000,
            total_amount=50000.0,
            avg_transaction_amount=50.0,
            daily_transaction_count=5,
            daily_amount=250.0,
            last_transaction_time="2023-08-15T13:00:00",
            last_transaction_amount=50.0,
        )

        transaction = self.create_ieee_transaction(transaction_amt=250.50, generated_timestamp="2023-08-15T14:00:00")

        new_features = self.fraud_detector.extract_features({**transaction, "card1": "new_user"}, new_user)
        exp_features = self.fraud_detector.extract_features(
            {**transaction, "card1": "experienced_user"}, experienced_user
        )

        # Behavioral features should differ
        assert new_features.amount_vs_avg_ratio != exp_features.amount_vs_avg_ratio
        assert new_features.daily_transaction_count != exp_features.daily_transaction_count
        assert new_features.time_since_last_transaction != exp_features.time_since_last_transaction
        assert new_features.velocity_score != exp_features.velocity_score

        # Experienced user: 250.5 / 50 = 5.01 ratio -> triggers high deviation
        assert exp_features.amount_vs_avg_ratio == pytest.approx(5.01, rel=0.01)
        # New user defaults to 1.0 ratio
        assert new_features.amount_vs_avg_ratio == 1.0

    def test_time_since_last_transaction_no_history(self):
        """Test time_since_last when user has no prior transactions."""
        new_user = UserProfile(user_id="no_history_user")

        transaction = self.create_ieee_transaction(card1="no_history_user", generated_timestamp="2023-08-15T14:00:00")

        features = self.fraud_detector.extract_features(transaction, new_user)

        # No last_transaction_time -> 0.0 seconds
        assert features.time_since_last_transaction == 0.0
        # With 0 time diff, is_rapid_transaction should be True (0 < 300)
        assert features.is_rapid_transaction is True

    def test_daily_amount_total_tracked(self):
        """Test that daily_amount_total reflects user profile daily amount."""
        user_profile = self.create_user_profile(daily_amount=350.75)

        transaction = self.create_ieee_transaction(generated_timestamp="2023-08-15T14:00:00")

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.daily_amount_total == 350.75

    @pytest.mark.parametrize("amount", [0.01, 1.0, 10.0, 100.0, 999.99, 1000.01, 5000.0])
    def test_various_amounts_produce_valid_features(self, amount):
        """Test that feature extraction works for a range of amounts."""
        user_profile = self.create_user_profile(
            avg_transaction_amount=100.0, last_transaction_time="2023-08-14T14:00:00"
        )

        transaction = self.create_ieee_transaction(transaction_amt=amount, generated_timestamp="2023-08-15T14:00:00")

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.amount == amount
        assert 0.0 <= features.fraud_score <= 1.0
        assert features.amount_vs_avg_ratio == pytest.approx(amount / 100.0, rel=0.01)

    def test_weekday_extraction(self):
        """Test that transaction_day correctly maps to weekday."""
        user_profile = self.create_user_profile(last_transaction_time="2023-08-10T14:00:00")

        # 2023-08-14 = Monday (0), 2023-08-15 = Tuesday (1), etc.
        for day, expected_weekday in [
            (14, 0),
            (15, 1),
            (16, 2),
            (17, 3),
            (18, 4),
            (19, 5),
            (20, 6),
        ]:
            txn = self.create_ieee_transaction(transaction_amt=100.0, generated_timestamp=f"2023-08-{day}T14:00:00")
            features = self.fraud_detector.extract_features(txn, user_profile)
            assert (
                features.transaction_day == expected_weekday
            ), f"2023-08-{day} should be weekday {expected_weekday}, got {features.transaction_day}"
