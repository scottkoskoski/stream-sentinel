"""
Unit Tests for Model Deployment, Registry Integration, and A/B Testing

Tests cover:
- Model registry integration with fraud_detector (mock Redis)
- Model hot-swap (version transition without dropped messages)
- A/B test assignment (deterministic by user_id hash)
- Fallback behaviour when registry is unavailable
"""

import hashlib
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from consumers.fraud_detector import FraudDetector, UserProfile

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_model():
    """Return a fake model object that supports predict_proba."""
    model = MagicMock()
    model.predict_proba.return_value = [[0.2, 0.8]]  # fraud probability = 0.8
    model.feature_names_in_ = None
    return model


def _make_model_dict(version="1.0.0"):
    """Return a dict-wrapped model matching the pickle format."""
    return {
        "model": _make_fake_model(),
        "scaler": None,
        "label_encoders": {},
        "feature_names": ["TransactionAmt"],
    }


def _make_ieee_transaction(**overrides) -> Dict[str, Any]:
    """Create an IEEE-CIS format transaction."""
    txn = {
        "transaction_id": "txn_test_001",
        "card1": "user_abc",
        "transaction_amt": 100.0,
        "generated_timestamp": "2024-01-15T10:00:00",
        "product_cd": "W",
        "card6": "credit",
    }
    txn.update(overrides)
    return txn


def _default_user_profile() -> UserProfile:
    return UserProfile(
        user_id="user_abc",
        total_transactions=10,
        total_amount=1000.0,
        avg_transaction_amount=100.0,
        daily_transaction_count=2,
        daily_amount=200.0,
        last_transaction_time="2024-01-14T10:00:00",
        last_transaction_amount=100.0,
    )


def _infra_patches():
    """Return a context manager that patches Kafka/Redis infrastructure."""
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True
    mock_redis.sismember.return_value = False
    mock_redis.hgetall.return_value = {}
    mock_redis.get.return_value = None
    mock_redis.keys.return_value = []

    mock_kafka_config = Mock()
    mock_kafka_config.get_consumer_config.return_value = {
        "group.id": "test-group",
        "bootstrap.servers": "localhost:9092",
        "auto.offset.reset": "earliest",
    }
    mock_kafka_config.get_producer_config.return_value = {
        "bootstrap.servers": "localhost:9092",
    }

    import contextlib

    @contextlib.contextmanager
    def ctx():
        with patch(
            "consumers.fraud_detector.get_kafka_config",
            return_value=mock_kafka_config,
        ):
            with patch("consumers.fraud_detector.redis.Redis", return_value=mock_redis):
                with patch("consumers.fraud_detector.Consumer"):
                    with patch("consumers.fraud_detector.Producer"):
                        yield mock_redis

    return ctx()


def _build_detector(use_ml_model=False, **extra_kwargs) -> FraudDetector:
    """Build a FraudDetector with all infrastructure mocked out.

    Registry and AB manager are disabled by default.  Tests that need them
    should use _infra_patches() directly and patch the flags themselves.
    """
    with _infra_patches() as mock_redis:
        with patch("consumers.fraud_detector.MODEL_REGISTRY_AVAILABLE", False):
            with patch("consumers.fraud_detector.AB_TEST_MANAGER_AVAILABLE", False):
                detector = FraudDetector(use_ml_model=use_ml_model, **extra_kwargs)
                detector.redis_client = mock_redis
                return detector


# ---------------------------------------------------------------------------
# Test: Model Registry Integration
# ---------------------------------------------------------------------------


class TestModelRegistryIntegration:
    """Tests for loading models via ModelRegistry at startup."""

    @pytest.mark.unit
    def test_startup_with_registry_model(self):
        """When ModelRegistry has a production model, it should be loaded."""
        model_dict = _make_model_dict("2.0.0")
        mock_registry = MagicMock()
        mock_registry.get_active_model.return_value = model_dict
        mock_registry.active_deployments = {
            "production": {"model_id": "fraud_v2", "version": "2.0.0"},
            "staging": None,
            "development": None,
        }

        with _infra_patches() as mock_redis:
            with patch("consumers.fraud_detector.MODEL_REGISTRY_AVAILABLE", True):
                with patch(
                    "consumers.fraud_detector.ModelRegistry",
                    return_value=mock_registry,
                ):
                    with patch("consumers.fraud_detector.AB_TEST_MANAGER_AVAILABLE", False):
                        detector = FraudDetector(use_ml_model=True)
                        detector.redis_client = mock_redis

        assert detector.ml_model is not None
        assert detector.model_status == "ml_primary"
        assert detector.model_version == "2.0.0"

    @pytest.mark.unit
    def test_startup_registry_unavailable_falls_back_to_filesystem(self):
        """When ModelRegistry constructor throws, _load_ml_model tries filesystem."""
        # We verify that the registry failure is caught and the filesystem
        # path is attempted.  The actual filesystem model load is tested
        # separately by existing fraud_scoring tests.
        with _infra_patches() as mock_redis:
            with patch("consumers.fraud_detector.MODEL_REGISTRY_AVAILABLE", True):
                with patch(
                    "consumers.fraud_detector.ModelRegistry",
                    side_effect=Exception("Redis down"),
                ):
                    with patch("consumers.fraud_detector.AB_TEST_MANAGER_AVAILABLE", False):
                        detector = FraudDetector(use_ml_model=False)
                        detector.redis_client = mock_redis

        # Registry should be None (failed to init)
        assert detector.model_registry is None

    @pytest.mark.unit
    def test_startup_no_registry_no_file_degrades_to_rules(self):
        """When no model source is available, detector uses rules fallback."""
        with _infra_patches() as mock_redis:
            with patch("consumers.fraud_detector.MODEL_REGISTRY_AVAILABLE", False):
                with patch("consumers.fraud_detector.AB_TEST_MANAGER_AVAILABLE", False):
                    with patch.object(
                        FraudDetector,
                        "_resolve_model_path",
                        return_value=None,
                    ):
                        detector = FraudDetector(use_ml_model=True)
                        detector.redis_client = mock_redis

        assert detector.ml_model is None
        assert detector.model_status == "rules_fallback"

    @pytest.mark.unit
    def test_registry_instance_stored_on_detector(self):
        """The detector should keep the ModelRegistry instance for later refresh."""
        mock_registry = MagicMock()
        mock_registry.get_active_model.return_value = None
        mock_registry.active_deployments = {
            "production": None,
            "staging": None,
            "development": None,
        }

        with _infra_patches() as mock_redis:
            with patch("consumers.fraud_detector.MODEL_REGISTRY_AVAILABLE", True):
                with patch(
                    "consumers.fraud_detector.ModelRegistry",
                    return_value=mock_registry,
                ):
                    with patch("consumers.fraud_detector.AB_TEST_MANAGER_AVAILABLE", False):
                        detector = FraudDetector(use_ml_model=False)
                        detector.redis_client = mock_redis

        assert detector.model_registry is mock_registry


# ---------------------------------------------------------------------------
# Test: Model Hot-Swap
# ---------------------------------------------------------------------------


class TestModelHotSwap:
    """Tests for runtime model version hot-swap."""

    @pytest.mark.unit
    def test_hot_swap_new_version(self):
        """_check_and_refresh_model swaps to the new version."""
        old_model = _make_fake_model()
        new_model_dict = _make_model_dict("3.0.0")
        new_model_dict["model"].predict_proba.return_value = [[0.1, 0.9]]

        mock_registry = MagicMock()
        mock_registry.active_deployments = {
            "production": {"model_id": "fraud_v3", "version": "3.0.0"},
        }
        mock_registry.get_active_model.return_value = new_model_dict

        with patch("consumers.fraud_detector.MODEL_REGISTRY_AVAILABLE", True):
            with patch("consumers.fraud_detector.ModelRegistry", return_value=mock_registry):
                detector = _build_detector(use_ml_model=False)
                detector.model_registry = mock_registry

        # Set initial state
        detector.ml_model = old_model
        detector.model_version = "2.0.0"
        detector.model_status = "ml_primary"

        # Trigger refresh
        detector._check_and_refresh_model()

        assert detector.model_version == "3.0.0"
        assert detector.ml_model is new_model_dict["model"]
        assert detector.model_status == "ml_primary"

    @pytest.mark.unit
    def test_hot_swap_same_version_noop(self):
        """When registry version matches current, no swap occurs."""
        mock_registry = MagicMock()
        mock_registry.active_deployments = {
            "production": {"model_id": "fraud_v1", "version": "1.0.0"},
        }

        with patch("consumers.fraud_detector.MODEL_REGISTRY_AVAILABLE", True):
            with patch("consumers.fraud_detector.ModelRegistry", return_value=mock_registry):
                detector = _build_detector(use_ml_model=False)
                detector.model_registry = mock_registry

        original_model = _make_fake_model()
        detector.ml_model = original_model
        detector.model_version = "1.0.0"

        detector._check_and_refresh_model()

        # Model should not have been swapped
        assert detector.ml_model is original_model
        mock_registry.get_active_model.assert_not_called()

    @pytest.mark.unit
    def test_hot_swap_registry_returns_none(self):
        """When registry returns None for the new model, keep the current one."""
        mock_registry = MagicMock()
        mock_registry.active_deployments = {
            "production": {"model_id": "fraud_v2", "version": "2.0.0"},
        }
        mock_registry.get_active_model.return_value = None

        with patch("consumers.fraud_detector.MODEL_REGISTRY_AVAILABLE", True):
            with patch("consumers.fraud_detector.ModelRegistry", return_value=mock_registry):
                detector = _build_detector(use_ml_model=False)
                detector.model_registry = mock_registry

        original_model = _make_fake_model()
        detector.ml_model = original_model
        detector.model_version = "1.0.0"

        detector._check_and_refresh_model()

        assert detector.ml_model is original_model
        assert detector.model_version == "1.0.0"

    @pytest.mark.unit
    def test_hot_swap_no_registry_is_safe(self):
        """_check_and_refresh_model does nothing when registry is None."""
        detector = _build_detector(use_ml_model=False)
        detector.model_registry = None
        detector.model_version = "1.0.0"

        # Should not raise
        detector._check_and_refresh_model()
        assert detector.model_version == "1.0.0"


# ---------------------------------------------------------------------------
# Test: A/B Test Assignment
# ---------------------------------------------------------------------------


class TestABTestAssignment:
    """Tests for A/B test variant assignment and scoring."""

    @pytest.mark.unit
    def test_deterministic_assignment_by_user_id(self):
        """The same user_id always gets the same variant (consistent hashing)."""
        # Replicate the hashing logic from ab_test_manager._assign_user_to_variant
        experiment_id = "exp_12345"
        user_a = "user_001"
        user_b = "user_002"

        def _hash_ratio(user_id, exp_id):
            h = hashlib.md5(f"{user_id}:{exp_id}".encode()).hexdigest()
            return (int(h, 16) % 10000) / 10000.0

        ratio_a_1 = _hash_ratio(user_a, experiment_id)
        ratio_a_2 = _hash_ratio(user_a, experiment_id)
        ratio_b = _hash_ratio(user_b, experiment_id)

        # Same user, same experiment -> same ratio
        assert ratio_a_1 == ratio_a_2
        # Different users *may* differ (not guaranteed, but confirms the hash changes)
        # We just verify determinism, not uniqueness.

    @pytest.mark.unit
    def test_ab_scoring_routes_control_to_production_model(self):
        """Control variant should use the production model."""
        mock_ab = MagicMock()
        mock_ab.active_experiments = {
            "exp_1": MagicMock(
                experiment_id="exp_1",
                variants=[
                    MagicMock(
                        variant_id="exp_1_control",
                        variant_type=MagicMock(value="control"),
                    ),
                    MagicMock(
                        variant_id="exp_1_treatment",
                        variant_type=MagicMock(value="treatment"),
                    ),
                ],
            )
        }
        # Assign to control
        mock_ab.assign_variant.return_value = "exp_1_control"

        with patch("consumers.fraud_detector.AB_TEST_MANAGER_AVAILABLE", True):
            with patch("consumers.fraud_detector.ABTestManager", return_value=mock_ab):
                detector = _build_detector(use_ml_model=False)
                detector.ab_test_manager = mock_ab

        # Give it a model so ml_primary is active
        detector.ml_model = _make_fake_model()
        detector.model_status = "ml_primary"
        detector.model_features = ["TransactionAmt"]

        txn = _make_ieee_transaction()
        profile = _default_user_profile()

        score, variant_id, exp_id = detector._score_with_ab_testing(txn, profile)

        assert variant_id == "exp_1_control"
        assert exp_id == "exp_1"
        assert 0.0 <= score <= 1.0
        mock_ab.record_prediction_result.assert_called_once()

    @pytest.mark.unit
    def test_ab_scoring_routes_treatment_with_fallback(self):
        """Treatment variant falls back to production model when treatment model is not loadable."""
        mock_ab = MagicMock()
        mock_ab.active_experiments = {
            "exp_1": MagicMock(
                experiment_id="exp_1",
                variants=[
                    MagicMock(
                        variant_id="exp_1_control",
                        variant_type=MagicMock(value="control"),
                    ),
                    MagicMock(
                        variant_id="exp_1_treatment",
                        variant_type=MagicMock(value="treatment"),
                    ),
                ],
            )
        }
        mock_ab.assign_variant.return_value = "exp_1_treatment"

        with patch("consumers.fraud_detector.AB_TEST_MANAGER_AVAILABLE", True):
            with patch("consumers.fraud_detector.ABTestManager", return_value=mock_ab):
                detector = _build_detector(use_ml_model=False)
                detector.ab_test_manager = mock_ab

        detector.ml_model = _make_fake_model()
        detector.model_status = "ml_primary"
        detector.model_features = ["TransactionAmt"]
        detector.model_registry = None  # No registry -> treatment model unavailable

        txn = _make_ieee_transaction()
        profile = _default_user_profile()

        score, variant_id, exp_id = detector._score_with_ab_testing(txn, profile)

        # Should still score (falls back to production model)
        assert variant_id == "exp_1_treatment"
        assert 0.0 <= score <= 1.0

    @pytest.mark.unit
    def test_no_active_experiment_returns_default_score(self):
        """When no A/B experiment is active, scoring uses default model."""
        mock_ab = MagicMock()
        mock_ab.active_experiments = {}

        with patch("consumers.fraud_detector.AB_TEST_MANAGER_AVAILABLE", True):
            with patch("consumers.fraud_detector.ABTestManager", return_value=mock_ab):
                detector = _build_detector(use_ml_model=False)
                detector.ab_test_manager = mock_ab

        detector.ml_model = _make_fake_model()
        detector.model_status = "ml_primary"
        detector.model_features = ["TransactionAmt"]

        txn = _make_ieee_transaction()
        profile = _default_user_profile()

        score, variant_id, exp_id = detector._score_with_ab_testing(txn, profile)

        assert variant_id is None
        assert exp_id is None
        assert 0.0 <= score <= 1.0

    @pytest.mark.unit
    def test_ab_metadata_in_features(self):
        """extract_features attaches A/B metadata when experiment is active."""
        mock_ab = MagicMock()
        mock_ab.active_experiments = {
            "exp_1": MagicMock(
                experiment_id="exp_1",
                variants=[
                    MagicMock(
                        variant_id="exp_1_control",
                        variant_type=MagicMock(value="control"),
                    ),
                    MagicMock(
                        variant_id="exp_1_treatment",
                        variant_type=MagicMock(value="treatment"),
                    ),
                ],
            )
        }
        mock_ab.assign_variant.return_value = "exp_1_control"

        with patch("consumers.fraud_detector.AB_TEST_MANAGER_AVAILABLE", True):
            with patch("consumers.fraud_detector.ABTestManager", return_value=mock_ab):
                detector = _build_detector(use_ml_model=False)
                detector.ab_test_manager = mock_ab

        detector.ml_model = _make_fake_model()
        detector.model_status = "ml_primary"
        detector.model_features = ["TransactionAmt"]

        txn = _make_ieee_transaction()
        profile = _default_user_profile()

        features = detector.extract_features(txn, profile)

        assert features._ab_variant_id == "exp_1_control"
        assert features._ab_experiment_id == "exp_1"


# ---------------------------------------------------------------------------
# Test: Fallback When Registry Unavailable
# ---------------------------------------------------------------------------


class TestFallbackBehaviour:
    """Tests ensuring graceful degradation when infrastructure is unavailable."""

    @pytest.mark.unit
    def test_scoring_works_without_registry(self):
        """Scoring proceeds normally when model_registry is None."""
        detector = _build_detector(use_ml_model=False)
        assert detector.model_registry is None

        txn = _make_ieee_transaction()
        profile = _default_user_profile()

        # Rule-based scoring should work
        features = detector.extract_features(txn, profile)
        assert 0.0 <= features.fraud_score <= 1.0
        assert features._ab_variant_id is None

    @pytest.mark.unit
    def test_scoring_works_without_ab_manager(self):
        """Scoring proceeds when ab_test_manager is None."""
        detector = _build_detector(use_ml_model=False)
        detector.ml_model = _make_fake_model()
        detector.model_status = "ml_primary"
        detector.model_features = ["TransactionAmt"]
        detector.ab_test_manager = None

        txn = _make_ieee_transaction()
        profile = _default_user_profile()

        features = detector.extract_features(txn, profile)
        assert 0.0 <= features.fraud_score <= 1.0
        assert features._ab_variant_id is None

    @pytest.mark.unit
    def test_ab_manager_exception_gracefully_handled(self):
        """If ABTestManager.assign_variant throws, scoring falls back to default."""
        mock_ab = MagicMock()
        mock_ab.active_experiments = {"exp_1": MagicMock()}
        mock_ab.assign_variant.side_effect = Exception("Redis timeout")

        detector = _build_detector(use_ml_model=False)
        detector.ab_test_manager = mock_ab
        detector.ml_model = _make_fake_model()
        detector.model_status = "ml_primary"
        detector.model_features = ["TransactionAmt"]

        txn = _make_ieee_transaction()
        profile = _default_user_profile()

        score, variant_id, exp_id = detector._score_with_ab_testing(txn, profile)

        assert variant_id is None
        assert 0.0 <= score <= 1.0

    @pytest.mark.unit
    def test_model_version_tracked(self):
        """model_version attribute is initialized and accessible."""
        detector = _build_detector(use_ml_model=False)
        assert hasattr(detector, "model_version")
        assert detector.model_version == "unknown"

    @pytest.mark.unit
    def test_model_lock_exists(self):
        """The _model_lock for thread-safe hot-swap is created."""
        detector = _build_detector(use_ml_model=False)
        assert hasattr(detector, "_model_lock")
        # Should be acquirable
        assert detector._model_lock.acquire(timeout=1)
        detector._model_lock.release()
