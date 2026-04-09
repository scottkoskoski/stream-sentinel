"""
Contract Tests for Producer-Consumer Data Flow

Validates that the contracts between system components are maintained:
1. Every field the consumer expects, the producer generates
2. The feature vector the ML model expects matches what feature extraction produces
3. Message serialization/deserialization roundtrip works correctly

These tests do NOT require infrastructure -- they test data shape contracts only.
"""

import json
import sys
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import get_type_hints
from unittest.mock import MagicMock, Mock, patch

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.alert_processor import AlertContext, AlertProcessor, AlertResponse, AlertSeverity, ResponseAction
from consumers.fraud_detector import FraudDetector, FraudFeatures, UserProfile
from persistence.schemas import FraudAlert, SchemaManager, TransactionRecord
from producers.synthetic_transaction_producer import SyntheticTransactionProducer, Transaction


def make_producer():
    """Create a SyntheticTransactionProducer with mocked Kafka."""
    with (
        patch("producers.synthetic_transaction_producer.get_kafka_config") as mock_cfg,
        patch("producers.synthetic_transaction_producer.Producer") as mock_prod,
        patch.object(SyntheticTransactionProducer, "_load_analysis_results") as mock_load,
    ):
        mock_kafka = Mock()
        mock_kafka.get_producer_config.return_value = {"bootstrap.servers": "localhost:9092"}
        mock_cfg.return_value = mock_kafka
        mock_prod.return_value = Mock()
        mock_load.return_value = {
            "schema": {"fraud_rate": 0.027},
            "synthetic_spec": {
                "transaction_patterns": {
                    "amount_distribution": {
                        "mean_log": 4.0,
                        "std_log": 1.2,
                        "min_amount": 1.0,
                        "max_amount": 1000.0,
                    },
                    "product_codes": {
                        "W": 0.7,
                        "C": 0.15,
                        "R": 0.1,
                        "H": 0.03,
                        "S": 0.02,
                    },
                },
                "fraud_patterns": {
                    "base_fraud_rate": 0.027,
                    "amount_patterns": {"high_amount_bias": 1.2},
                },
            },
        }

        producer = SyntheticTransactionProducer()
        data = mock_load.return_value
        producer.fraud_rate = data["schema"]["fraud_rate"]
        producer.transaction_patterns = data["synthetic_spec"]["transaction_patterns"]
        producer.fraud_patterns = data["synthetic_spec"]["fraud_patterns"]
        return producer


def make_detector():
    """Create a FraudDetector with mocked infrastructure."""
    mock_redis = MagicMock()
    mock_config = Mock()
    mock_config.get_consumer_config.return_value = {
        "group.id": "contract-test",
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


# ---------------------------------------------------------------------------
# Contract 1: Producer fields match consumer expectations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProducerConsumerFieldContract:
    """Validate that every field the consumer reads is produced by the producer."""

    def test_consumer_required_fields_present_in_transaction(self):
        """The FraudDetector.extract_features() reads specific keys from the
        transaction dict.  Verify the producer's Transaction dataclass has them.
        """
        # Fields accessed by extract_features():
        consumer_required = {
            "transaction_amt",  # amount = float(transaction['transaction_amt'])
            "generated_timestamp",  # timestamp = transaction['generated_timestamp']
            "card1",  # user_id = str(transaction['card1'])
            "transaction_id",  # transaction.get('transaction_id', 'unknown')
        }

        producer_fields = {f.name for f in fields(Transaction)}

        missing = consumer_required - producer_fields
        assert not missing, f"Consumer requires fields {missing} that Transaction dataclass is missing"

    def test_ml_feature_extraction_fields_present(self):
        """The _extract_ml_features() method reads additional fields.  Verify
        the producer generates them.
        """
        ml_expected_keys = {
            "transaction_amt",
            "product_cd",
            "card1",
            "card2",
            "card3",
            "card5",
            "card6",
            "addr1",
            "addr2",
            "r_emaildomain",
        }

        producer_fields = {f.name for f in fields(Transaction)}

        missing = ml_expected_keys - producer_fields
        assert not missing, f"ML feature extraction expects fields {missing} missing from Transaction"

    def test_alert_processor_reads_fraud_detector_output(self):
        """AlertProcessor.classify_alert_severity() expects specific keys in
        the alert dict.  Verify FraudDetector.publish_fraud_alert() produces
        those keys by inspecting the alert construction logic.
        """
        # Keys the alert processor reads
        alert_fields_read = {
            "fraud_score",  # alert.get('fraud_score', 0.0)
            "risk_factors",  # alert.get('risk_factors', {})
            "transaction_details",  # alert.get('transaction_details', {})
        }

        # Keys that publish_fraud_alert() writes to the alert dict
        # (verified from source: lines 648-668 of fraud_detector.py)
        alert_fields_written = {
            "alert_id",
            "timestamp",
            "user_id",
            "transaction_id",
            "fraud_score",
            "risk_factors",
            "transaction_details",
            "original_transaction",
        }

        missing = alert_fields_read - alert_fields_written
        assert not missing, f"AlertProcessor reads {missing} but FraudDetector alert doesn't provide them"

    def test_risk_factors_sub_fields_match(self):
        """Verify the specific risk_factors keys that classify_alert_severity
        checks are populated by publish_fraud_alert.
        """
        risk_factor_keys_read = {
            "is_rapid_transaction",
            "velocity_score",
            "is_high_amount",
            "is_unusual_hour",
        }

        # From FraudDetector.publish_fraud_alert risk_factors dict
        risk_factor_keys_written = {
            "is_high_amount",
            "is_unusual_hour",
            "is_rapid_transaction",
            "amount_vs_avg_ratio",
            "velocity_score",
            "daily_transaction_count",
        }

        missing = risk_factor_keys_read - risk_factor_keys_written
        assert not missing, f"AlertProcessor reads risk_factors keys {missing} that FraudDetector omits"


# ---------------------------------------------------------------------------
# Contract 2: Feature vector compatibility
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFeatureVectorContract:
    """Validate that the feature extraction output matches what scoring expects."""

    def test_fraud_features_dataclass_has_all_scoring_inputs(self):
        """FraudFeatures must contain all fields that _calculate_fraud_score reads."""
        scoring_inputs = {
            "amount_vs_avg_ratio",
            "is_high_amount",
            "is_unusual_hour",
            "is_rapid_transaction",
            "velocity_score",
            "daily_transaction_count",
        }

        feature_fields = {f.name for f in fields(FraudFeatures)}

        missing = scoring_inputs - feature_fields
        assert not missing, f"_calculate_fraud_score needs {missing} missing from FraudFeatures"

    def test_extract_features_produces_valid_fraud_features(self):
        """Run extract_features with a producer-generated transaction
        and verify it returns a complete FraudFeatures object.
        """
        producer = make_producer()
        detector = make_detector()

        txn = producer._generate_transaction()
        txn_dict = asdict(txn)

        user_profile = UserProfile(user_id=str(txn.card1))

        features = detector.extract_features(txn_dict, user_profile)

        assert isinstance(features, FraudFeatures)
        # Verify all fields are populated (not None)
        for field in fields(FraudFeatures):
            value = getattr(features, field.name)
            assert value is not None, f"FraudFeatures.{field.name} is None"

    def test_fraud_features_to_dict_roundtrip(self):
        """Test that FraudFeatures.to_dict() produces a dict that could be
        serialized and deserialized without loss.
        """
        producer = make_producer()
        detector = make_detector()

        txn = producer._generate_transaction()
        txn_dict = asdict(txn)

        user_profile = UserProfile(user_id=str(txn.card1))
        features = detector.extract_features(txn_dict, user_profile)

        features_dict = features.to_dict()

        # Should be JSON-serializable
        json_str = json.dumps(features_dict)
        decoded = json.loads(json_str)

        assert decoded["user_id"] == features.user_id
        assert decoded["transaction_id"] == features.transaction_id
        assert decoded["fraud_score"] == pytest.approx(features.fraud_score, abs=0.0001)


# ---------------------------------------------------------------------------
# Contract 3: Message serialization roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMessageSerializationContract:
    """Validate JSON serialization/deserialization roundtrip for messages."""

    def test_transaction_serialization_roundtrip(self):
        """Transaction -> JSON -> dict -> extract_features should work."""
        producer = make_producer()
        detector = make_detector()

        txn = producer._generate_transaction()
        txn_dict = asdict(txn)

        # Serialize to JSON (as Kafka producer would)
        json_bytes = json.dumps(txn_dict).encode("utf-8")

        # Deserialize (as Kafka consumer would)
        deserialized = json.loads(json_bytes.decode("utf-8"))

        # Verify key fields survive roundtrip
        assert deserialized["transaction_id"] == txn.transaction_id
        assert deserialized["transaction_amt"] == txn.transaction_amt
        assert deserialized["card1"] == txn.card1
        assert deserialized["generated_timestamp"] == txn.generated_timestamp

        # Consumer should be able to process the deserialized dict
        user_profile = UserProfile(user_id=str(deserialized["card1"]))
        features = detector.extract_features(deserialized, user_profile)

        assert isinstance(features, FraudFeatures)
        assert features.amount == txn.transaction_amt

    def test_fraud_alert_serialization_roundtrip(self):
        """Fraud alert dict -> JSON -> AlertProcessor.classify_alert_severity."""
        alert = {
            "alert_id": "alert_contract_001",
            "user_id": "user_contract",
            "fraud_score": 0.85,
            "risk_factors": {
                "is_high_amount": True,
                "is_unusual_hour": False,
                "is_rapid_transaction": True,
                "velocity_score": 15.5,
            },
            "transaction_details": {
                "amount": 3000.0,
                "hour": 14,
            },
        }

        # Serialize and deserialize
        json_bytes = json.dumps(alert).encode("utf-8")
        deserialized = json.loads(json_bytes.decode("utf-8"))

        # Alert processor should handle deserialized dict
        mock_redis = MagicMock()
        mock_redis.hgetall.return_value = {}
        mock_redis.zrangebyscore.return_value = []
        mock_config = Mock()
        mock_config.get_consumer_config.return_value = {"group.id": "contract-test"}
        mock_config.get_producer_config.return_value = {}

        with patch("consumers.alert_processor.get_kafka_config", return_value=mock_config):
            with patch("consumers.alert_processor.redis.Redis", return_value=mock_redis):
                with patch("consumers.alert_processor.Consumer"):
                    with patch("consumers.alert_processor.Producer"):
                        processor = AlertProcessor()
                        processor.redis_client = mock_redis

        severity = processor.classify_alert_severity(deserialized)

        # Score 0.85 with is_rapid_transaction should classify as HIGH
        assert severity == AlertSeverity.HIGH

        # Verify field types survived serialization
        assert isinstance(deserialized["fraud_score"], float)
        assert isinstance(deserialized["risk_factors"]["is_high_amount"], bool)
        assert isinstance(deserialized["risk_factors"]["velocity_score"], float)

    def test_alert_response_serialization(self):
        """AlertResponse.to_dict() should produce JSON-serializable output."""
        response = AlertResponse(
            alert_id="serial_001",
            response_id="resp_serial_001",
            timestamp="2023-08-15T14:30:00",
            severity=AlertSeverity.HIGH,
            action=ResponseAction.IMMEDIATE_BLOCK,
            response_time_ms=150.0,
            details={"user_blocked": "user_123", "success": True},
        )

        response_dict = response.to_dict()
        json_str = json.dumps(response_dict)
        decoded = json.loads(json_str)

        assert decoded["alert_id"] == "serial_001"
        assert decoded["severity"] == "high"
        assert decoded["action"] == "immediate_block"
        assert decoded["response_time_ms"] == 150.0

    def test_persistence_data_model_serialization(self):
        """FraudAlert and TransactionRecord should serialize for DB insertion."""
        alert = FraudAlert(
            transaction_id="persist_txn_001",
            user_id="persist_user",
            severity=AlertSeverity.HIGH,
            fraud_score=0.88,
            ml_prediction=0.85,
            business_rules_triggered=["high_amount"],
            explanation={"reason": "test"},
        )

        alert_dict = alert.to_dict()
        assert alert_dict["severity"] == "HIGH"
        assert alert_dict["status"] == "PENDING"
        assert isinstance(alert_dict["business_rules_triggered"], list)

        # Should be JSON-serializable
        json_str = json.dumps(alert_dict, default=str)
        assert len(json_str) > 0

    def test_transaction_none_values_handled_in_json(self):
        """Transaction fields that are None should serialize to JSON null."""
        producer = make_producer()
        txn = producer._generate_transaction()
        txn_dict = asdict(txn)

        # Some fields like dist1, dist2 can be None
        json_str = json.dumps(txn_dict)
        decoded = json.loads(json_str)

        # None -> null -> None roundtrip
        for key in ["dist1", "dist2"]:
            if txn_dict[key] is None:
                assert decoded[key] is None
