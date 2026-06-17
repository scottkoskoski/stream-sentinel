"""
Unit Tests for Fraud Scoring and ML Integration

Tests the core fraud scoring functionality with meaningful assertions
on actual fraud score values, feature computations, and behavioral differences.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.fraud_detector import FraudDetector, FraudFeatures, UserProfile


class TestFraudScoring:
    """Fraud scoring unit tests with value-level assertions."""

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

    def test_rule_based_score_no_risk_factors(self):
        """Test that a normal transaction scores 0.0 with no risk factors."""
        transaction = self.create_ieee_transaction(
            transaction_amt=50.0,  # Below $1000
            generated_timestamp="2023-08-15T14:00:00",  # Normal hour
        )
        user_profile = self.create_user_profile(
            avg_transaction_amount=50.0,  # Ratio = 1.0, no deviation penalty
            daily_transaction_count=2,
            last_transaction_time="2023-08-15T10:00:00",  # 4 hours ago, not rapid
        )

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.fraud_score == 0.0, f"No risk factors should yield score 0.0, got {features.fraud_score}"
        assert features.is_fraud_alert is False

    def test_rule_based_score_high_amount_only(self):
        """Test score with only the high amount flag triggered."""
        transaction = self.create_ieee_transaction(
            transaction_amt=1500.0,  # > $1000 threshold
            generated_timestamp="2023-08-15T14:00:00",  # Normal hour
        )
        user_profile = self.create_user_profile(
            avg_transaction_amount=1500.0,  # Ratio = 1.0, no deviation penalty
            daily_transaction_count=2,
            last_transaction_time="2023-08-14T14:00:00",  # Day ago, not rapid
        )

        features = self.fraud_detector.extract_features(transaction, user_profile)

        # Only is_high_amount triggered -> +0.2
        assert features.is_high_amount is True
        assert features.is_unusual_hour is False
        assert features.is_rapid_transaction is False
        assert features.fraud_score == pytest.approx(0.2, abs=0.01)

    def test_rule_based_score_unusual_hour_only(self):
        """Test score with only the unusual hour flag triggered."""
        transaction = self.create_ieee_transaction(
            transaction_amt=50.0,
            generated_timestamp="2023-08-15T03:00:00",  # 3 AM = unusual
        )
        user_profile = self.create_user_profile(
            avg_transaction_amount=50.0,
            daily_transaction_count=2,
            last_transaction_time="2023-08-14T14:00:00",
        )

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.is_unusual_hour is True
        assert features.is_high_amount is False
        assert features.is_rapid_transaction is False
        assert features.fraud_score == pytest.approx(0.15, abs=0.01)

    def test_rule_based_score_rapid_transaction_only(self):
        """Test score with only rapid transaction flag triggered."""
        transaction = self.create_ieee_transaction(
            transaction_amt=50.0,
            generated_timestamp="2023-08-15T14:01:00",  # 1 minute after last
        )
        user_profile = self.create_user_profile(
            avg_transaction_amount=50.0,
            daily_transaction_count=2,
            last_transaction_time="2023-08-15T14:00:00",  # 60 seconds ago
        )

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.is_rapid_transaction is True
        assert features.time_since_last_transaction == pytest.approx(60.0, abs=1.0)
        assert features.fraud_score == pytest.approx(0.25, abs=0.01)

    def test_rule_based_score_amount_deviation_medium(self):
        """Test amount deviation scoring: ratio between 2.0 and 3.0."""
        transaction = self.create_ieee_transaction(transaction_amt=250.0, generated_timestamp="2023-08-15T14:00:00")
        user_profile = self.create_user_profile(
            avg_transaction_amount=100.0,  # Ratio = 2.5 -> +0.1
            daily_transaction_count=2,
            last_transaction_time="2023-08-14T14:00:00",
        )

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.amount_vs_avg_ratio == pytest.approx(2.5, rel=0.01)
        assert features.fraud_score == pytest.approx(0.1, abs=0.01)

    def test_rule_based_score_amount_deviation_high(self):
        """Test amount deviation scoring: ratio between 3.0 and 5.0."""
        transaction = self.create_ieee_transaction(transaction_amt=400.0, generated_timestamp="2023-08-15T14:00:00")
        user_profile = self.create_user_profile(
            avg_transaction_amount=100.0,  # Ratio = 4.0 -> +0.2
            daily_transaction_count=2,
            last_transaction_time="2023-08-14T14:00:00",
        )

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.amount_vs_avg_ratio == pytest.approx(4.0, rel=0.01)
        assert features.fraud_score == pytest.approx(0.2, abs=0.01)

    def test_rule_based_score_amount_deviation_extreme(self):
        """Test amount deviation scoring: ratio > 5.0."""
        transaction = self.create_ieee_transaction(transaction_amt=600.0, generated_timestamp="2023-08-15T14:00:00")
        user_profile = self.create_user_profile(
            avg_transaction_amount=100.0,  # Ratio = 6.0 -> +0.3
            daily_transaction_count=2,
            last_transaction_time="2023-08-14T14:00:00",
        )

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.amount_vs_avg_ratio == pytest.approx(6.0, rel=0.01)
        assert features.fraud_score == pytest.approx(0.3, abs=0.01)

    def test_rule_based_score_multiple_factors_combined(self):
        """Test combined scoring with multiple risk factors."""
        # High amount + unusual hour + rapid + amount deviation > 5x
        transaction = self.create_ieee_transaction(
            transaction_amt=5000.0,  # > $1000 (+0.2), ratio = 250x -> +0.3
            generated_timestamp="2023-08-15T03:00:00",  # 3 AM (+0.15)
        )
        user_profile = self.create_user_profile(
            avg_transaction_amount=20.0,
            daily_transaction_count=2,
            last_transaction_time="2023-08-15T03:00:00",  # Same minute (+0.25)
        )

        features = self.fraud_detector.extract_features(transaction, user_profile)

        # Sum: 0.3 + 0.2 + 0.15 + 0.25 = 0.9, capped at 1.0
        assert features.fraud_score >= 0.9
        assert features.fraud_score <= 1.0
        assert features.is_fraud_alert is True  # Exceeds default 0.7 threshold

    def test_rule_based_score_excessive_daily_count(self):
        """Test scoring penalty for excessive daily transactions."""
        transaction = self.create_ieee_transaction(transaction_amt=50.0, generated_timestamp="2023-08-15T14:00:00")
        # daily_count > 50 -> +0.15
        user_profile = self.create_user_profile(
            avg_transaction_amount=50.0,
            daily_transaction_count=55,
            last_transaction_time="2023-08-14T14:00:00",
        )

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.fraud_score == pytest.approx(0.15, abs=0.01)

    def test_fraud_score_clamped_to_one(self):
        """Test that fraud score never exceeds 1.0."""
        # Trigger ALL risk factors
        transaction = self.create_ieee_transaction(transaction_amt=5000.0, generated_timestamp="2023-08-15T03:00:00")
        user_profile = self.create_user_profile(
            avg_transaction_amount=10.0,
            daily_transaction_count=55,
            last_transaction_time="2023-08-15T02:59:00",
        )

        features = self.fraud_detector.extract_features(transaction, user_profile)

        # All factors: 0.3 + 0.2 + 0.15 + 0.25 + 0.15 = 1.05 -> clamped to 1.0
        assert features.fraud_score == 1.0

    @pytest.mark.parametrize(
        "amount,expected_risk,expected_min_score",
        [
            (1.0, "low", 0.0),  # Very small, within avg -> no flags
            (50.0, "low", 0.0),  # Normal amount, within avg
            (5000.0, "high", 0.5),  # High amount + extreme deviation
        ],
    )
    def test_amount_based_risk_assessment(self, amount, expected_risk, expected_min_score):
        """Test risk assessment based on transaction amounts with actual score assertions."""
        user_profile = self.create_user_profile(
            avg_transaction_amount=50.0,
            daily_transaction_count=2,
            last_transaction_time="2023-08-14T14:00:00",
        )

        transaction = self.create_ieee_transaction(transaction_amt=amount, generated_timestamp="2023-08-15T14:00:00")

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert features.fraud_score >= expected_min_score, (
            f"Amount ${amount} with risk '{expected_risk}' "
            f"should score >= {expected_min_score}, got {features.fraud_score}"
        )

        if expected_risk == "high":
            assert features.is_high_amount is True
            assert features.amount_vs_avg_ratio > 5.0
        else:
            assert features.fraud_score < 0.5

    def test_fraud_scoring_consistency(self):
        """Test that same transaction produces identical scores every time."""
        transaction = self.create_ieee_transaction()
        user_profile = self.create_user_profile()

        features1 = self.fraud_detector.extract_features(transaction, user_profile)
        features2 = self.fraud_detector.extract_features(transaction, user_profile)

        assert features1.fraud_score == features2.fraud_score
        assert features1.amount_vs_avg_ratio == features2.amount_vs_avg_ratio
        assert features1.is_high_amount == features2.is_high_amount
        assert features1.is_unusual_hour == features2.is_unusual_hour
        assert features1.is_rapid_transaction == features2.is_rapid_transaction
        assert features1.velocity_score == features2.velocity_score

    def test_model_fallback_to_rules(self):
        """Test that FraudDetector falls back to rule-based when ML model unavailable."""
        assert self.fraud_detector.use_ml_model is False
        assert self.fraud_detector.ml_model is None

        # Should still score using rules
        transaction = self.create_ieee_transaction(transaction_amt=1500.0, generated_timestamp="2023-08-15T14:00:00")
        user_profile = self.create_user_profile(
            avg_transaction_amount=1500.0, last_transaction_time="2023-08-14T14:00:00"
        )

        features = self.fraud_detector.extract_features(transaction, user_profile)

        # Should get a valid score even without ML model
        assert 0.0 <= features.fraud_score <= 1.0
        # Only is_high_amount triggered
        assert features.is_high_amount is True
        assert features.fraud_score == pytest.approx(0.2, abs=0.01)

    def test_threshold_determines_alert_flag(self):
        """Test that is_fraud_alert is set based on the fraud_threshold."""
        # Default threshold is 0.3 (calibrated for synthetic data)
        assert self.fraud_detector.fraud_threshold == 0.3

        # Score below threshold -> no alert
        transaction = self.create_ieee_transaction(transaction_amt=50.0, generated_timestamp="2023-08-15T14:00:00")
        user_profile = self.create_user_profile(
            avg_transaction_amount=50.0, last_transaction_time="2023-08-14T14:00:00"
        )

        features = self.fraud_detector.extract_features(transaction, user_profile)
        assert features.fraud_score < 0.3
        assert features.is_fraud_alert is False

    def test_features_object_has_all_fields(self):
        """Test that FraudFeatures dataclass has all expected fields populated."""
        transaction = self.create_ieee_transaction()
        user_profile = self.create_user_profile()

        features = self.fraud_detector.extract_features(transaction, user_profile)

        assert isinstance(features, FraudFeatures)
        # All fields should be set
        assert features.user_id == "user_001"
        assert features.transaction_id == "txn_001"
        assert features.amount == 250.50
        assert features.transaction_hour == 14
        assert features.transaction_day == 1  # Tuesday
        assert isinstance(features.amount_vs_avg_ratio, float)
        assert isinstance(features.daily_transaction_count, int)
        assert isinstance(features.daily_amount_total, float)
        assert isinstance(features.time_since_last_transaction, float)
        assert isinstance(features.amount_vs_last_ratio, float)
        assert isinstance(features.is_high_amount, bool)
        assert isinstance(features.is_unusual_hour, bool)
        assert isinstance(features.is_rapid_transaction, bool)
        assert isinstance(features.velocity_score, float)
        assert isinstance(features.fraud_score, float)
        assert isinstance(features.is_fraud_alert, bool)

    def test_features_to_dict(self):
        """Test FraudFeatures.to_dict() produces valid dictionary."""
        transaction = self.create_ieee_transaction()
        user_profile = self.create_user_profile()

        features = self.fraud_detector.extract_features(transaction, user_profile)
        features_dict = features.to_dict()

        assert isinstance(features_dict, dict)
        assert features_dict["user_id"] == "user_001"
        assert features_dict["transaction_id"] == "txn_001"
        assert features_dict["amount"] == 250.50
        assert "fraud_score" in features_dict
        assert "is_fraud_alert" in features_dict
