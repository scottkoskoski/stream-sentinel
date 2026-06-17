"""
End-to-End Fraud Detection Workflow Tests

Tests complete fraud detection workflows from transaction ingestion
through alert processing with realistic data volumes and production-like scenarios.

These tests use the ACTUAL methods available in FraudDetector and AlertProcessor:
- FraudDetector.extract_features(transaction, user_profile) -> FraudFeatures
- FraudDetector.get_user_profile(user_id) -> UserProfile
- FraudDetector.save_user_profile(profile) -> None
- FraudDetector.process_transaction(transaction) -> None
- FraudDetector._calculate_fraud_score(...) -> float (rule-based)
- AlertProcessor.classify_alert_severity(alert) -> AlertSeverity
- AlertProcessor.get_alert_context(alert) -> AlertContext
- AlertProcessor.execute_response_action(context, severity) -> AlertResponse
- AlertProcessor.process_alert(alert) -> None
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.alert_processor import AlertContext, AlertProcessor, AlertResponse, AlertSeverity, ResponseAction
from consumers.fraud_detector import FraudDetector, FraudFeatures, UserProfile


def make_mock_fraud_detector(ml_score=None):
    """Create a FraudDetector with mocked infrastructure dependencies."""
    mock_redis = MagicMock()
    mock_redis.hgetall.return_value = {}  # New user by default
    mock_redis.ping.return_value = True

    mock_config = Mock()
    mock_config.get_consumer_config.return_value = {
        "group.id": "e2e-test-group",
        "bootstrap.servers": "localhost:9092",
        "auto.offset.reset": "earliest",
    }
    mock_config.get_producer_config.return_value = {"bootstrap.servers": "localhost:9092"}

    with patch("consumers.fraud_detector.get_kafka_config", return_value=mock_config):
        with patch("consumers.fraud_detector.redis.Redis", return_value=mock_redis):
            with patch("consumers.fraud_detector.Consumer"):
                with patch("consumers.fraud_detector.Producer"):
                    detector = FraudDetector(use_ml_model=False)
                    detector.redis_client = mock_redis
    return detector


def make_mock_alert_processor():
    """Create an AlertProcessor with mocked infrastructure dependencies."""
    mock_redis = MagicMock()
    mock_redis.hgetall.return_value = {}
    mock_redis.zrangebyscore.return_value = []
    mock_redis.ping.return_value = True

    mock_config = Mock()
    mock_config.get_consumer_config.return_value = {
        "group.id": "e2e-alert-test-group",
        "bootstrap.servers": "localhost:9092",
        "auto.offset.reset": "earliest",
    }
    mock_config.get_producer_config.return_value = {"bootstrap.servers": "localhost:9092"}

    with patch("consumers.alert_processor.get_kafka_config", return_value=mock_config):
        with patch("consumers.alert_processor.redis.Redis", return_value=mock_redis):
            with patch("consumers.alert_processor.Consumer"):
                with patch("consumers.alert_processor.Producer"):
                    processor = AlertProcessor()
                    processor.redis_client = mock_redis
    return processor


def create_ieee_transaction(**kwargs):
    """Create IEEE-CIS format transaction with defaults."""
    defaults = {
        "transaction_id": "txn_e2e_001",
        "card1": "user_e2e_001",
        "transaction_amt": 250.50,
        "generated_timestamp": "2023-08-15T14:30:00",
        "product_cd": "W",
        "card6": "credit",
    }
    defaults.update(kwargs)
    return defaults


@pytest.mark.e2e
class TestFraudDetectionWorkflows:
    """End-to-end fraud detection workflow tests using real code paths."""

    def test_normal_transaction_processing_workflow(self):
        """Test complete workflow for normal (non-fraudulent) transactions.

        Uses the actual extract_features() and _calculate_fraud_score() methods.
        """
        detector = make_mock_fraud_detector()

        # Normal transaction: moderate amount, normal hour, no velocity
        normal_txn = create_ieee_transaction(
            transaction_id="normal_txn_001",
            card1="normal_user_001",
            transaction_amt=45.75,
            generated_timestamp="2023-08-15T14:30:00",
        )

        # User with established history -- avg $100 transactions
        user_profile = UserProfile(
            user_id="normal_user_001",
            total_transactions=50,
            total_amount=5000.0,
            avg_transaction_amount=100.0,
            daily_transaction_count=2,
            daily_amount=200.0,
            last_transaction_time="2023-08-15T12:00:00",
            last_transaction_amount=80.0,
        )

        # Step 1: Extract features using the REAL extract_features method
        features = detector.extract_features(normal_txn, user_profile)

        assert isinstance(features, FraudFeatures)
        assert features.user_id == "normal_user_001"
        assert features.amount == 45.75
        assert features.transaction_hour == 14
        assert features.transaction_day == 1  # Tuesday

        # Step 2: Verify behavioral features are computed correctly
        # $45.75 / $100 avg = 0.4575 ratio
        assert features.amount_vs_avg_ratio == pytest.approx(0.4575, rel=0.01)
        assert features.is_high_amount is False  # < $1000
        assert features.is_unusual_hour is False  # 14:00 is normal
        # 2.5 hours since last txn = 9000 seconds > 300s
        assert features.is_rapid_transaction is False

        # Step 3: Verify fraud score is low for normal transaction
        # Rule-based score: no risk factors triggered
        assert features.fraud_score < 0.3, f"Normal transaction should have low fraud score, got {features.fraud_score}"
        assert features.is_fraud_alert is False

    def test_high_risk_fraud_transaction_workflow(self):
        """Test complete workflow for high-risk fraudulent transactions.

        Exercises the rule-based scoring path with multiple risk factors.
        """
        detector = make_mock_fraud_detector()

        # High-risk transaction: large amount at unusual hour, rapid succession
        fraud_txn = create_ieee_transaction(
            transaction_id="fraud_txn_001",
            card1="fraud_user_001",
            transaction_amt=5000.0,  # High amount
            generated_timestamp="2023-08-15T03:15:00",  # 3 AM - unusual hour
        )

        # User with low average spend who suddenly has big transaction
        user_profile = UserProfile(
            user_id="fraud_user_001",
            total_transactions=20,
            total_amount=400.0,
            avg_transaction_amount=20.0,
            daily_transaction_count=30,  # High daily count
            daily_amount=600.0,
            last_transaction_time="2023-08-15T03:14:00",  # 1 minute ago -- rapid
            last_transaction_amount=50.0,
        )

        features = detector.extract_features(fraud_txn, user_profile)

        # Verify risk indicators are correctly computed
        assert features.is_high_amount is True  # > $1000
        assert features.is_unusual_hour is True  # 3 AM
        assert features.is_rapid_transaction is True  # < 5 minutes since last
        # $5000 / $20 avg = 250x ratio
        assert features.amount_vs_avg_ratio == pytest.approx(250.0, rel=0.01)

        # Fraud score should be HIGH with all these risk factors
        # Rule-based: amount_ratio>5 (+0.3), high_amount (+0.2),
        #   unusual_hour (+0.15), rapid (+0.25), daily_count>25 (+0.1)
        # = 1.0 (clamped)
        assert features.fraud_score >= 0.7, f"High-risk transaction should score >= 0.7, got {features.fraud_score}"
        assert features.is_fraud_alert is True

    def test_new_user_first_transaction_workflow(self):
        """Test fraud detection for a brand new user's first transaction."""
        detector = make_mock_fraud_detector()

        first_txn = create_ieee_transaction(
            transaction_id="new_user_first_txn",
            card1="brand_new_user",
            transaction_amt=125.00,
            generated_timestamp="2023-08-15T10:00:00",
        )

        # Brand new user: all zeros
        new_user = UserProfile(user_id="brand_new_user")

        features = detector.extract_features(first_txn, new_user)

        assert features.user_id == "brand_new_user"
        assert features.amount == 125.00

        # New user: avg is 0 so amount_vs_avg_ratio defaults to 1.0
        assert features.amount_vs_avg_ratio == 1.0
        # No previous transaction, so time_since_last = 0
        assert features.time_since_last_transaction == 0.0
        # Normal amount and normal hour -- low risk
        assert features.is_high_amount is False
        assert features.is_unusual_hour is False

        # Score should be low since no risk indicators triggered
        assert features.fraud_score < 0.5

    def test_velocity_fraud_detection(self):
        """Test that rapid successive transactions escalate fraud score."""
        detector = make_mock_fraud_detector()

        # Simulate a user making rapid transactions
        user_profile = UserProfile(
            user_id="velocity_user",
            total_transactions=10,
            total_amount=500.0,
            avg_transaction_amount=50.0,
            daily_transaction_count=55,  # Very high daily count > 50
            daily_amount=2750.0,
            last_transaction_time="2023-08-15T14:29:30",  # 30 seconds ago
            last_transaction_amount=50.0,
        )

        rapid_txn = create_ieee_transaction(
            transaction_id="velocity_txn",
            card1="velocity_user",
            transaction_amt=50.0,
            generated_timestamp="2023-08-15T14:30:00",
        )

        features = detector.extract_features(rapid_txn, user_profile)

        # Should detect rapid transaction (30 seconds < 300 seconds)
        assert features.is_rapid_transaction is True
        assert features.time_since_last_transaction == pytest.approx(30.0, abs=1.0)
        # Daily count > 50 should trigger excessive daily transactions
        assert features.daily_transaction_count == 55

        # Score should include rapid transaction and daily count penalties
        assert features.fraud_score >= 0.25

    def test_alert_severity_classification_pipeline(self):
        """Test the full alert classification pipeline through AlertProcessor."""
        processor = make_mock_alert_processor()

        # Low-risk alert
        low_alert = {
            "alert_id": "alert_low_001",
            "user_id": "user_low",
            "fraud_score": 0.15,
        }
        assert processor.classify_alert_severity(low_alert) == AlertSeverity.LOW

        # Critical alert
        critical_alert = {
            "alert_id": "alert_critical_001",
            "user_id": "user_critical",
            "fraud_score": 0.95,
        }
        assert processor.classify_alert_severity(critical_alert) == AlertSeverity.CRITICAL

        # High alert with velocity risk
        high_alert = {
            "alert_id": "alert_high_001",
            "user_id": "user_high",
            "fraud_score": 0.75,
            "risk_factors": {"is_rapid_transaction": True, "velocity_score": 20},
            "transaction_details": {"amount": 1500},
        }
        assert processor.classify_alert_severity(high_alert) == AlertSeverity.HIGH

        # Medium alert: score 0.45 with 2 risk factors
        medium_alert = {
            "alert_id": "alert_med_001",
            "user_id": "user_med",
            "fraud_score": 0.45,
            "risk_factors": {
                "is_high_amount": True,
                "is_unusual_hour": True,
            },
        }
        assert processor.classify_alert_severity(medium_alert) == AlertSeverity.MEDIUM

    def test_alert_response_action_execution(self):
        """Test that the AlertProcessor executes correct response actions."""
        processor = make_mock_alert_processor()

        # Test IMMEDIATE_BLOCK for critical fraud
        critical_context = AlertContext(
            original_alert={
                "alert_id": "crit_001",
                "user_id": "blocked_user",
                "fraud_score": 0.95,
            },
            user_risk_profile={"risk_level": "high"},
            historical_alerts=[],
            transaction_pattern={"recent_alerts_24h": 0, "is_repeat_offender": False},
            recommended_action=ResponseAction.IMMEDIATE_BLOCK,
            confidence_score=0.95,
            enrichment_timestamp=datetime.now().isoformat(),
        )

        response = processor.execute_response_action(critical_context, AlertSeverity.CRITICAL)

        assert isinstance(response, AlertResponse)
        assert response.action == ResponseAction.IMMEDIATE_BLOCK
        assert response.status == "completed"
        assert "user_blocked" in response.details

        # Verify Redis was called to block the user
        processor.redis_client.sadd.assert_called_with("blocked_users", "blocked_user")

    def test_alert_response_log_only(self):
        """Test LOG_ONLY action for low-risk alerts."""
        processor = make_mock_alert_processor()

        low_context = AlertContext(
            original_alert={
                "alert_id": "low_001",
                "user_id": "low_user",
                "fraud_score": 0.1,
            },
            user_risk_profile={"risk_level": "low"},
            historical_alerts=[],
            transaction_pattern={"recent_alerts_24h": 0, "is_repeat_offender": False},
            recommended_action=ResponseAction.LOG_ONLY,
            confidence_score=0.5,
            enrichment_timestamp=datetime.now().isoformat(),
        )

        response = processor.execute_response_action(low_context, AlertSeverity.LOW)

        assert response.action == ResponseAction.LOG_ONLY
        assert response.status == "completed"
        assert response.details["action"] == "log_only"
        assert response.details["logged"] is True

    def test_full_pipeline_detection_to_alert(self):
        """Test the full pipeline: transaction -> features -> score -> alert classification."""
        detector = make_mock_fraud_detector()
        processor = make_mock_alert_processor()

        # High-risk transaction that should trigger alerts
        txn = create_ieee_transaction(
            transaction_id="pipeline_txn_001",
            card1="pipeline_user",
            transaction_amt=3000.0,
            generated_timestamp="2023-08-15T02:00:00",
        )

        user_profile = UserProfile(
            user_id="pipeline_user",
            total_transactions=5,
            total_amount=100.0,
            avg_transaction_amount=20.0,
            daily_transaction_count=1,
            daily_amount=20.0,
            last_transaction_time="2023-08-15T01:58:00",  # 2 minutes ago
            last_transaction_amount=20.0,
        )

        # Step 1: Extract features
        features = detector.extract_features(txn, user_profile)

        # Verify risk factors
        assert features.is_high_amount is True
        assert features.is_unusual_hour is True
        assert features.is_rapid_transaction is True

        # Step 2: Build alert data matching what publish_fraud_alert would create
        alert_data = {
            "alert_id": f"alert_{features.transaction_id}",
            "user_id": features.user_id,
            "fraud_score": features.fraud_score,
            "risk_factors": {
                "is_high_amount": features.is_high_amount,
                "is_unusual_hour": features.is_unusual_hour,
                "is_rapid_transaction": features.is_rapid_transaction,
                "amount_vs_avg_ratio": features.amount_vs_avg_ratio,
                "velocity_score": features.velocity_score,
            },
            "transaction_details": {
                "amount": features.amount,
                "hour": features.transaction_hour,
            },
        }

        # Step 3: Classify severity
        severity = processor.classify_alert_severity(alert_data)

        # With fraud_score >= 0.7 and is_rapid_transaction, should be HIGH or CRITICAL
        assert severity in (
            AlertSeverity.HIGH,
            AlertSeverity.CRITICAL,
        ), f"Expected HIGH or CRITICAL severity, got {severity}"

    def test_user_profile_lifecycle(self):
        """Test user profile creation, update, and retrieval through FraudDetector."""
        detector = make_mock_fraud_detector()

        # Create new profile
        new_profile = UserProfile(user_id="lifecycle_user")
        assert new_profile.total_transactions == 0
        assert new_profile.total_amount == 0.0

        # Simulate transaction processing
        new_profile.update_transaction_stats(100.0, "2023-08-15T10:00:00")
        new_profile.update_daily_stats(100.0, "2023-08-15T10:00:00")

        assert new_profile.total_transactions == 1
        assert new_profile.total_amount == 100.0
        assert new_profile.avg_transaction_amount == 100.0
        assert new_profile.daily_transaction_count == 1
        assert new_profile.daily_amount == 100.0

        # Second transaction
        new_profile.update_transaction_stats(200.0, "2023-08-15T11:00:00")
        new_profile.update_daily_stats(200.0, "2023-08-15T11:00:00")

        assert new_profile.total_transactions == 2
        assert new_profile.total_amount == 300.0
        assert new_profile.avg_transaction_amount == 150.0
        assert new_profile.daily_transaction_count == 2
        assert new_profile.daily_amount == 300.0

        # Save and verify Redis call
        detector.save_user_profile(new_profile)
        detector.redis_client.hset.assert_called()
        saved_data = detector.redis_client.hset.call_args[1]["mapping"]
        assert saved_data["user_id"] == "lifecycle_user"
        assert saved_data["total_transactions"] == 2
        assert saved_data["total_amount"] == 300.0

    def test_bulk_transaction_scoring_consistency(self):
        """Test that scoring is consistent across a batch of transactions."""
        detector = make_mock_fraud_detector()

        user_profile = UserProfile(
            user_id="bulk_user",
            total_transactions=100,
            total_amount=5000.0,
            avg_transaction_amount=50.0,
            daily_transaction_count=3,
            daily_amount=150.0,
            last_transaction_time="2023-08-15T12:00:00",
            last_transaction_amount=50.0,
        )

        # Score the same transaction 10 times -- should be deterministic
        txn = create_ieee_transaction(
            card1="bulk_user",
            transaction_amt=50.0,
            generated_timestamp="2023-08-15T14:00:00",
        )

        scores = []
        for _ in range(10):
            features = detector.extract_features(txn, user_profile)
            scores.append(features.fraud_score)

        # All scores should be identical (deterministic scoring)
        assert all(s == scores[0] for s in scores), f"Scores should be deterministic, got varying scores: {set(scores)}"

    def test_daily_stats_reset_on_new_day(self):
        """Test that UserProfile daily stats reset when day changes."""
        profile = UserProfile(
            user_id="reset_user",
            daily_transaction_count=10,
            daily_amount=500.0,
            last_reset_date="2023-08-14",
        )

        # Transaction on a new day
        profile.update_daily_stats(100.0, "2023-08-15T09:00:00")

        assert profile.daily_transaction_count == 1  # Reset + 1
        assert profile.daily_amount == 100.0  # Reset + new amount
        assert profile.last_reset_date == "2023-08-15"

    def test_process_alert_end_to_end(self):
        """Test AlertProcessor.process_alert() which runs the entire pipeline."""
        processor = make_mock_alert_processor()

        # Mock the producer so publish_response works
        mock_producer = Mock()
        processor.producer = mock_producer

        alert = {
            "alert_id": "e2e_alert_001",
            "user_id": "e2e_user",
            "fraud_score": 0.5,
            "transaction_details": {"amount": 500.0},
            "timestamp": datetime.now().isoformat(),
        }

        # Should not raise
        processor.process_alert(alert)

        # Verify the full pipeline ran: producer.produce should be called for response
        assert mock_producer.produce.called
        assert processor.processed_alerts == 1
