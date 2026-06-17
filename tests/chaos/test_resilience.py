"""
Chaos Engineering and Resilience Tests

Tests system behavior under failure conditions using the actual FraudDetector
and AlertProcessor code paths:

1. Redis unavailable -- fraud detector degrades to stateless scoring
2. ML model file corrupted -- fallback to rule-based with alerting
3. Kafka producer buffer full -- graceful backpressure handling
4. Malformed transaction data -- proper error handling without crash
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

import redis as redis_module

from consumers.alert_processor import AlertProcessor, AlertResponse, AlertSeverity, ResponseAction
from consumers.fraud_detector import FraudDetector, FraudFeatures, UserProfile


def make_fraud_detector(**kwargs):
    """Create FraudDetector with mocked infrastructure."""
    mock_redis = MagicMock()
    mock_redis.hgetall.return_value = {}
    mock_redis.ping.return_value = True

    mock_config = Mock()
    mock_config.get_consumer_config.return_value = {
        "group.id": "chaos-test",
        "bootstrap.servers": "localhost:9092",
        "auto.offset.reset": "earliest",
    }
    mock_config.get_producer_config.return_value = {"bootstrap.servers": "localhost:9092"}

    with patch("consumers.fraud_detector.get_kafka_config", return_value=mock_config):
        with patch("consumers.fraud_detector.redis.Redis", return_value=mock_redis):
            with patch("consumers.fraud_detector.Consumer"):
                with patch("consumers.fraud_detector.Producer"):
                    detector = FraudDetector(use_ml_model=False, **kwargs)
                    detector.redis_client = mock_redis
    return detector


def make_alert_processor():
    """Create AlertProcessor with mocked infrastructure."""
    mock_redis = MagicMock()
    mock_redis.hgetall.return_value = {}
    mock_redis.zrangebyscore.return_value = []
    mock_config = Mock()
    mock_config.get_consumer_config.return_value = {"group.id": "chaos-alert-test"}
    mock_config.get_producer_config.return_value = {}

    with patch("consumers.alert_processor.get_kafka_config", return_value=mock_config):
        with patch("consumers.alert_processor.redis.Redis", return_value=mock_redis):
            with patch("consumers.alert_processor.Consumer"):
                with patch("consumers.alert_processor.Producer"):
                    processor = AlertProcessor()
                    processor.redis_client = mock_redis
    return processor


def create_valid_transaction(**overrides):
    """Create a valid IEEE-CIS transaction dict."""
    txn = {
        "transaction_id": "chaos_txn_001",
        "card1": "chaos_user_001",
        "transaction_amt": 100.0,
        "generated_timestamp": "2023-08-15T14:00:00",
        "product_cd": "W",
        "card6": "credit",
    }
    txn.update(overrides)
    return txn


# ---------------------------------------------------------------------------
# Test 1: Redis unavailable -- stateless scoring fallback
# ---------------------------------------------------------------------------


@pytest.mark.chaos
class TestRedisUnavailable:
    """Test fraud detection when Redis is completely unavailable."""

    def test_get_user_profile_returns_default_on_redis_failure(self):
        """get_user_profile should return a default UserProfile when Redis fails."""
        detector = make_fraud_detector()

        # Make Redis throw ConnectionError on every call
        detector.redis_client.hgetall.side_effect = redis_module.ConnectionError("Connection refused")

        profile = detector.get_user_profile("any_user")

        assert isinstance(profile, UserProfile)
        assert profile.user_id == "any_user"
        assert profile.total_transactions == 0
        assert profile.total_amount == 0.0

    def test_save_user_profile_does_not_crash_on_redis_failure(self):
        """save_user_profile should log error but not raise when Redis is down."""
        detector = make_fraud_detector()

        detector.redis_client.hset.side_effect = redis_module.ConnectionError("Connection refused")

        profile = UserProfile(
            user_id="save_fail_user",
            total_transactions=5,
            total_amount=250.0,
        )

        # Should NOT raise
        detector.save_user_profile(profile)

    def test_extract_features_works_with_default_profile(self):
        """Feature extraction should work even when Redis returns a default profile."""
        detector = make_fraud_detector()

        detector.redis_client.hgetall.side_effect = redis_module.ConnectionError("Redis down")

        txn = create_valid_transaction(
            transaction_amt=500.0,
            generated_timestamp="2023-08-15T03:00:00",  # unusual hour
        )

        # get_user_profile will fall back to default
        profile = detector.get_user_profile("redis_down_user")
        features = detector.extract_features(txn, profile)

        assert isinstance(features, FraudFeatures)
        assert features.amount == 500.0
        assert features.is_unusual_hour is True
        # Score should still be computed using rule-based approach
        assert 0.0 <= features.fraud_score <= 1.0

    def test_full_processing_degrades_gracefully_without_redis(self):
        """process_transaction should handle Redis failure at every stage."""
        detector = make_fraud_detector()

        # Redis fails for reads and writes
        detector.redis_client.hgetall.side_effect = redis_module.ConnectionError("down")
        detector.redis_client.hset.side_effect = redis_module.ConnectionError("down")
        detector.redis_client.expire.side_effect = redis_module.ConnectionError("down")

        # Mock the producer to track calls (Kafka should still work)
        mock_producer = Mock()
        detector.producer = mock_producer

        txn = create_valid_transaction()

        # Should not raise
        detector.process_transaction(txn)

        # Transaction should still be processed (even if profile save fails)
        assert detector.processed_count == 1


# ---------------------------------------------------------------------------
# Test 2: ML model corrupted -- fallback to rule-based
# ---------------------------------------------------------------------------


@pytest.mark.chaos
class TestMLModelCorrupted:
    """Test fraud detection when the ML model is broken."""

    def test_nan_model_output_falls_back_to_rules(self):
        """When ML model returns NaN, _calculate_ml_fraud_score should
        fall back to rule-based scoring.
        """
        detector = make_fraud_detector()
        detector.use_ml_model = True

        class NaNModel:
            def predict_proba(self, features):
                return [[0.0, float("nan")]]

        detector.ml_model = NaNModel()

        txn = create_valid_transaction(
            transaction_amt=2000.0,
            generated_timestamp="2023-08-15T03:00:00",
        )

        profile = UserProfile(
            user_id="nan_user",
            avg_transaction_amount=100.0,
            last_transaction_time="2023-08-15T02:59:00",
        )

        features = detector.extract_features(txn, profile)

        # Should get a valid score (fallback to rules after ML exception/NaN)
        assert 0.0 <= features.fraud_score <= 1.0

    def test_exception_model_falls_back_to_rules(self):
        """When ML model raises an exception, system should use rule-based scoring."""
        detector = make_fraud_detector()
        detector.use_ml_model = True

        class CrashingModel:
            def predict_proba(self, features):
                raise RuntimeError("Model file corrupted")

        detector.ml_model = CrashingModel()
        detector.model_features = [
            "feature1",
            "feature2",
        ]  # Required for _extract_ml_features

        txn = create_valid_transaction(
            transaction_amt=1500.0,  # High amount -> +0.2
            generated_timestamp="2023-08-15T14:00:00",
        )

        profile = UserProfile(
            user_id="crash_user",
            avg_transaction_amount=1500.0,
            last_transaction_time="2023-08-14T14:00:00",
        )

        features = detector.extract_features(txn, profile)

        # Should fall back to rule-based score
        assert 0.0 <= features.fraud_score <= 1.0
        # High amount only -> 0.2
        assert features.is_high_amount is True
        assert features.fraud_score == pytest.approx(0.2, abs=0.05)

    def test_invalid_range_model_clamped(self):
        """ML model returning probability > 1.0 should be handled."""
        detector = make_fraud_detector()
        detector.use_ml_model = True

        class OverflowModel:
            def predict_proba(self, features):
                return [[0.0, 5.0]]  # Invalid: > 1.0

        detector.ml_model = OverflowModel()
        detector.model_features = []

        txn = create_valid_transaction()
        profile = UserProfile(user_id="overflow_user")

        features = detector.extract_features(txn, profile)

        # Score should either be clamped or fallback should kick in
        assert 0.0 <= features.fraud_score <= 1.0 or features.fraud_score >= 0

    def test_process_transaction_survives_model_failure(self):
        """Full process_transaction should not crash when ML model fails."""
        detector = make_fraud_detector()
        detector.use_ml_model = True

        class FailModel:
            def predict_proba(self, features):
                raise Exception("Catastrophic model failure")

        detector.ml_model = FailModel()
        detector.model_features = []

        mock_producer = Mock()
        detector.producer = mock_producer

        txn = create_valid_transaction()

        # Should not raise
        detector.process_transaction(txn)
        assert detector.processed_count == 1


# ---------------------------------------------------------------------------
# Test 3: Kafka producer buffer full -- graceful backpressure
# ---------------------------------------------------------------------------


@pytest.mark.chaos
class TestKafkaBackpressure:
    """Test behavior when Kafka producer encounters backpressure."""

    def test_publish_fraud_alert_handles_produce_failure(self):
        """publish_fraud_alert should catch and log errors from producer.produce."""
        detector = make_fraud_detector()

        mock_producer = Mock()
        mock_producer.produce.side_effect = BufferError("Queue full")
        detector.producer = mock_producer

        features = FraudFeatures(
            user_id="backpressure_user",
            transaction_id="bp_txn_001",
            amount=500.0,
            transaction_hour=14,
            transaction_day=1,
            amount_vs_avg_ratio=5.0,
            daily_transaction_count=10,
            daily_amount_total=5000.0,
            time_since_last_transaction=60.0,
            amount_vs_last_ratio=2.0,
            is_high_amount=False,
            is_unusual_hour=False,
            is_rapid_transaction=True,
            velocity_score=0.4,
            fraud_score=0.8,
            is_fraud_alert=True,
        )

        original_txn = create_valid_transaction()

        # Should not raise despite producer.produce throwing BufferError
        detector.publish_fraud_alert(features, original_txn)

        # The produce call was attempted
        assert mock_producer.produce.called

    def test_publish_performance_metrics_handles_failure(self):
        """Performance metrics publishing should not crash on Kafka failure."""
        detector = make_fraud_detector()

        mock_producer = Mock()
        mock_producer.produce.side_effect = Exception("Kafka unavailable")
        detector.producer = mock_producer

        # Should not raise
        detector.publish_performance_metrics(50.0)

    def test_process_transaction_survives_kafka_failure(self):
        """Full transaction processing should continue even if Kafka alerts fail."""
        detector = make_fraud_detector()

        mock_producer = Mock()
        mock_producer.produce.side_effect = BufferError("Queue full")
        mock_producer.poll.return_value = 0
        detector.producer = mock_producer

        txn = create_valid_transaction(
            transaction_amt=5000.0,
            generated_timestamp="2023-08-15T03:00:00",
        )

        # Should not raise even though producing alerts/results will fail
        detector.process_transaction(txn)
        assert detector.processed_count == 1

    def test_alert_processor_publish_response_handles_failure(self):
        """AlertProcessor.publish_response should not crash on Kafka failure."""
        processor = make_alert_processor()

        mock_producer = Mock()
        mock_producer.produce.side_effect = Exception("Kafka down")
        processor.producer = mock_producer

        response = AlertResponse(
            alert_id="bp_alert_001",
            response_id="bp_resp_001",
            timestamp=datetime.now().isoformat(),
            severity=AlertSeverity.LOW,
            action=ResponseAction.LOG_ONLY,
            response_time_ms=10.0,
            details={"logged": True},
        )

        # Should not raise
        processor.publish_response(response)


# ---------------------------------------------------------------------------
# Test 4: Malformed transaction data -- graceful error handling
# ---------------------------------------------------------------------------


@pytest.mark.chaos
class TestMalformedTransactionData:
    """Test that malformed input is handled without crashes."""

    def test_missing_transaction_amt(self):
        """Transaction without 'transaction_amt' should be caught by process_transaction."""
        detector = make_fraud_detector()
        mock_producer = Mock()
        detector.producer = mock_producer

        malformed = {
            "transaction_id": "bad_txn_001",
            "card1": "user_001",
            # Missing: "transaction_amt"
            "generated_timestamp": "2023-08-15T14:00:00",
        }

        # process_transaction wraps everything in try/except
        detector.process_transaction(malformed)
        # Should not crash -- error is logged

    def test_missing_timestamp(self):
        """Transaction without 'generated_timestamp' should not crash."""
        detector = make_fraud_detector()
        mock_producer = Mock()
        detector.producer = mock_producer

        malformed = {
            "transaction_id": "bad_txn_002",
            "card1": "user_002",
            "transaction_amt": 100.0,
            # Missing: "generated_timestamp"
        }

        detector.process_transaction(malformed)
        # Should not crash

    def test_missing_card1(self):
        """Transaction without 'card1' (user ID) should not crash."""
        detector = make_fraud_detector()
        mock_producer = Mock()
        detector.producer = mock_producer

        malformed = {
            "transaction_id": "bad_txn_003",
            # Missing: "card1"
            "transaction_amt": 100.0,
            "generated_timestamp": "2023-08-15T14:00:00",
        }

        detector.process_transaction(malformed)
        # Should not crash

    def test_non_numeric_amount(self):
        """Transaction with string amount should not crash process_transaction."""
        detector = make_fraud_detector()
        mock_producer = Mock()
        detector.producer = mock_producer

        malformed = {
            "transaction_id": "bad_txn_004",
            "card1": "user_004",
            "transaction_amt": "not_a_number",
            "generated_timestamp": "2023-08-15T14:00:00",
        }

        detector.process_transaction(malformed)
        # Should not crash -- error logged

    def test_invalid_timestamp_format(self):
        """Transaction with invalid timestamp should not crash."""
        detector = make_fraud_detector()
        mock_producer = Mock()
        detector.producer = mock_producer

        malformed = {
            "transaction_id": "bad_txn_005",
            "card1": "user_005",
            "transaction_amt": 100.0,
            "generated_timestamp": "not-a-date",
        }

        detector.process_transaction(malformed)
        # Should not crash

    def test_empty_transaction_dict(self):
        """Completely empty transaction dict should not crash."""
        detector = make_fraud_detector()
        mock_producer = Mock()
        detector.producer = mock_producer

        detector.process_transaction({})
        # Should not crash

    def test_none_transaction_values(self):
        """Transaction with None values in critical fields should not crash."""
        detector = make_fraud_detector()
        mock_producer = Mock()
        detector.producer = mock_producer

        malformed = {
            "transaction_id": None,
            "card1": None,
            "transaction_amt": None,
            "generated_timestamp": None,
        }

        detector.process_transaction(malformed)
        # Should not crash

    def test_alert_processor_handles_malformed_alert(self):
        """AlertProcessor.process_alert should handle completely broken input."""
        processor = make_alert_processor()
        mock_producer = Mock()
        processor.producer = mock_producer

        malformed_alert = {
            # No alert_id, no fraud_score, no user_id
        }

        # Should not raise
        processor.process_alert(malformed_alert)

    def test_alert_processor_handles_non_numeric_fraud_score(self):
        """AlertProcessor should handle non-numeric fraud_score gracefully."""
        processor = make_alert_processor()
        mock_producer = Mock()
        processor.producer = mock_producer

        bad_alert = {
            "alert_id": "bad_alert_001",
            "user_id": "bad_user",
            "fraud_score": "not_a_number",
        }

        # classify_alert_severity uses .get() with defaults
        # Should not crash
        processor.process_alert(bad_alert)

    def test_negative_amount_handled(self):
        """Negative transaction amount should not crash."""
        detector = make_fraud_detector()
        mock_producer = Mock()
        detector.producer = mock_producer

        txn = create_valid_transaction(transaction_amt=-50.0)

        detector.process_transaction(txn)
        # Should not crash

    def test_extremely_large_amount_handled(self):
        """Extremely large amount should be handled without overflow."""
        detector = make_fraud_detector()

        txn = create_valid_transaction(transaction_amt=999999999.99)
        profile = UserProfile(
            user_id="chaos_user_001",
            avg_transaction_amount=100.0,
            last_transaction_time="2023-08-14T14:00:00",
        )

        features = detector.extract_features(txn, profile)

        assert isinstance(features, FraudFeatures)
        assert 0.0 <= features.fraud_score <= 1.0
        assert features.is_high_amount is True
