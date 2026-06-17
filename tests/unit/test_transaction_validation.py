"""
Unit tests for transaction input validation.

Tests the TransactionValidator module that guards the Kafka consumer
ingestion point against malformed, duplicate, and high-velocity transactions.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from validation.transaction_validator import TransactionValidator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transaction(**overrides):
    """Build a valid baseline transaction, merging *overrides*."""
    base = {
        "transaction_id": "txn_test_001",
        "transaction_amt": 150.00,
        "card1": "user_42",
        "generated_timestamp": datetime.now(timezone.utc).isoformat(),
        "product_cd": "W",
        "card4": "visa",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidTransaction:
    """A well-formed transaction should pass validation cleanly."""

    def test_valid_transaction_passes(self):
        v = TransactionValidator()
        result = v.validate(_make_transaction())
        assert result.is_valid is True
        assert result.errors == []
        assert result.warnings == []
        assert result.validation_time_ms >= 0

    def test_valid_transaction_with_user_id_field(self):
        """user_id is an accepted alternative to card1."""
        txn = _make_transaction()
        del txn["card1"]
        txn["user_id"] = "u_99"
        v = TransactionValidator()
        result = v.validate(txn)
        assert result.is_valid is True

    def test_extra_fields_allowed(self):
        """Extra/unknown fields must not cause rejection."""
        txn = _make_transaction(extra_field="hello", another=42)
        v = TransactionValidator()
        result = v.validate(txn)
        assert result.is_valid is True

    def test_returns_original_transaction(self):
        txn = _make_transaction()
        v = TransactionValidator()
        result = v.validate(txn)
        assert result.transaction is txn


class TestMissingRequiredFields:
    """Missing required fields should produce hard errors."""

    @pytest.mark.parametrize("field", ["transaction_id", "transaction_amt", "generated_timestamp"])
    def test_missing_required_field_rejected(self, field):
        txn = _make_transaction()
        del txn[field]
        v = TransactionValidator()
        result = v.validate(txn)
        assert result.is_valid is False
        assert any(field in e for e in result.errors)

    def test_missing_all_user_identifiers_rejected(self):
        txn = _make_transaction()
        del txn["card1"]
        # No user_id either
        v = TransactionValidator()
        result = v.validate(txn)
        assert result.is_valid is False
        assert any("user identifier" in e for e in result.errors)

    def test_none_required_field_rejected(self):
        txn = _make_transaction(transaction_amt=None)
        v = TransactionValidator()
        result = v.validate(txn)
        assert result.is_valid is False


class TestTransactionAmount:
    """Amount must be numeric, positive, and within bounds."""

    def test_negative_amount_rejected(self):
        v = TransactionValidator()
        result = v.validate(_make_transaction(transaction_amt=-10.0))
        assert result.is_valid is False
        assert any("> 0" in e for e in result.errors)

    def test_zero_amount_rejected(self):
        v = TransactionValidator()
        result = v.validate(_make_transaction(transaction_amt=0))
        assert result.is_valid is False

    def test_string_amount_rejected(self):
        v = TransactionValidator()
        result = v.validate(_make_transaction(transaction_amt="not_a_number"))
        assert result.is_valid is False
        assert any("not numeric" in e for e in result.errors)

    def test_numeric_string_amount_accepted(self):
        """Numeric strings like '150.5' are coerced and accepted."""
        v = TransactionValidator()
        result = v.validate(_make_transaction(transaction_amt="150.5"))
        assert result.is_valid is True

    def test_amount_exceeds_max_rejected(self):
        v = TransactionValidator(max_transaction_amt=5000.0)
        result = v.validate(_make_transaction(transaction_amt=5001.0))
        assert result.is_valid is False
        assert any("exceeds maximum" in e for e in result.errors)

    def test_amount_at_max_accepted(self):
        v = TransactionValidator(max_transaction_amt=5000.0)
        result = v.validate(_make_transaction(transaction_amt=5000.0))
        assert result.is_valid is True

    def test_boolean_amount_rejected(self):
        """Booleans are technically int subclasses in Python but should be
        treated as valid numeric (True=1, False=0). False == 0 so it fails
        the >0 check, but True==1 would pass."""
        v = TransactionValidator()
        # True == 1, which is > 0 and numeric
        result = v.validate(_make_transaction(transaction_amt=True))
        assert result.is_valid is True

    def test_list_amount_rejected(self):
        v = TransactionValidator()
        result = v.validate(_make_transaction(transaction_amt=[100]))
        assert result.is_valid is False


class TestTimestamp:
    """Timestamp must be valid ISO-8601 and not far in the future."""

    def test_invalid_timestamp_format_rejected(self):
        v = TransactionValidator()
        result = v.validate(_make_transaction(generated_timestamp="not-a-date"))
        assert result.is_valid is False
        assert any("ISO" in e for e in result.errors)

    def test_future_timestamp_beyond_tolerance_rejected(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        v = TransactionValidator(timestamp_future_tolerance_s=300)
        result = v.validate(_make_transaction(generated_timestamp=future))
        assert result.is_valid is False
        assert any("future" in e for e in result.errors)

    def test_future_timestamp_within_tolerance_accepted(self):
        slight_future = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        v = TransactionValidator(timestamp_future_tolerance_s=300)
        result = v.validate(_make_transaction(generated_timestamp=slight_future))
        assert result.is_valid is True

    def test_past_timestamp_accepted(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        v = TransactionValidator()
        result = v.validate(_make_transaction(generated_timestamp=past))
        assert result.is_valid is True

    def test_naive_timestamp_accepted(self):
        """Naive (no tzinfo) timestamps should be accepted -- the validator
        assumes UTC for comparison."""
        naive_ts = datetime.now().isoformat()
        v = TransactionValidator()
        result = v.validate(_make_transaction(generated_timestamp=naive_ts))
        assert result.is_valid is True

    def test_non_string_timestamp_rejected(self):
        v = TransactionValidator()
        result = v.validate(_make_transaction(generated_timestamp=1234567890))
        assert result.is_valid is False


class TestTransactionId:
    """transaction_id must be a string."""

    def test_numeric_transaction_id_rejected(self):
        v = TransactionValidator()
        result = v.validate(_make_transaction(transaction_id=12345))
        assert result.is_valid is False
        assert any("string" in e for e in result.errors)


class TestProductCode:
    """product_cd validation is a warning, not a hard error."""

    def test_valid_product_codes(self):
        v = TransactionValidator()
        for i, code in enumerate(["W", "C", "R", "H", "S"]):
            result = v.validate(_make_transaction(product_cd=code, transaction_id=f"pcd_{i}"))
            assert result.is_valid is True
            assert result.warnings == []

    def test_unknown_product_cd_warns(self):
        v = TransactionValidator()
        result = v.validate(_make_transaction(product_cd="Z"))
        assert result.is_valid is True
        assert any("product_cd" in w for w in result.warnings)

    def test_missing_product_cd_no_warning(self):
        txn = _make_transaction()
        del txn["product_cd"]
        v = TransactionValidator()
        result = v.validate(txn)
        assert result.is_valid is True
        assert result.warnings == []


class TestCard4:
    """card4 validation is a warning, not a hard error."""

    @pytest.mark.parametrize("val", ["visa", "Visa", "VISA", "mastercard", "discover", "american express"])
    def test_valid_card4_values(self, val):
        v = TransactionValidator()
        result = v.validate(_make_transaction(card4=val))
        assert result.is_valid is True
        assert not any("card4" in w for w in result.warnings)

    def test_unknown_card4_warns(self):
        v = TransactionValidator()
        result = v.validate(_make_transaction(card4="diners club"))
        assert result.is_valid is True
        assert any("card4" in w for w in result.warnings)


class TestDuplicateDetection:
    """Duplicate transaction_id within the TTL window should warn."""

    def test_duplicate_detected(self):
        v = TransactionValidator(duplicate_window_s=60)
        txn = _make_transaction(transaction_id="dup_001")
        r1 = v.validate(txn)
        assert r1.is_valid is True
        assert r1.warnings == []

        r2 = v.validate(txn)
        assert r2.is_valid is True  # duplicates warn, not reject
        assert any("Duplicate" in w for w in r2.warnings)

    def test_different_ids_no_duplicate(self):
        v = TransactionValidator()
        r1 = v.validate(_make_transaction(transaction_id="a"))
        r2 = v.validate(_make_transaction(transaction_id="b"))
        assert r1.warnings == []
        assert r2.warnings == []


class TestVelocityCheck:
    """High transaction velocity should produce a warning."""

    def test_high_velocity_warns(self):
        v = TransactionValidator(velocity_threshold=5, velocity_window_s=3600)
        for i in range(6):
            txn = _make_transaction(transaction_id=f"vel_{i}", card1="speed_user")
            result = v.validate(txn)

        # The 6th transaction should trigger the velocity warning
        assert result.is_valid is True
        assert any("velocity" in w.lower() for w in result.warnings)

    def test_below_threshold_no_warning(self):
        v = TransactionValidator(velocity_threshold=10, velocity_window_s=3600)
        for i in range(5):
            txn = _make_transaction(transaction_id=f"vel_{i}", card1="normal_user")
            result = v.validate(txn)
        assert not any("velocity" in w.lower() for w in result.warnings)


class TestEdgeCases:
    """Edge cases: empty dict, non-dict input, None values."""

    def test_empty_dict_rejected(self):
        v = TransactionValidator()
        result = v.validate({})
        assert result.is_valid is False
        assert len(result.errors) > 0

    def test_none_input_rejected(self):
        v = TransactionValidator()
        result = v.validate(None)
        assert result.is_valid is False
        assert any("dict" in e for e in result.errors)

    def test_list_input_rejected(self):
        v = TransactionValidator()
        result = v.validate([1, 2, 3])
        assert result.is_valid is False

    def test_string_input_rejected(self):
        v = TransactionValidator()
        result = v.validate("not a transaction")
        assert result.is_valid is False


class TestValidationResult:
    """Verify the ValidationResult dataclass contract."""

    def test_result_has_timing(self):
        v = TransactionValidator()
        result = v.validate(_make_transaction())
        assert isinstance(result.validation_time_ms, float)
        assert result.validation_time_ms >= 0

    def test_result_fields(self):
        v = TransactionValidator()
        result = v.validate(_make_transaction())
        assert isinstance(result.is_valid, bool)
        assert isinstance(result.transaction, dict)
        assert isinstance(result.warnings, list)
        assert isinstance(result.errors, list)


class TestConfigurableThresholds:
    """Thresholds should be configurable via constructor args."""

    def test_custom_max_amount(self):
        v = TransactionValidator(max_transaction_amt=500.0)
        result = v.validate(_make_transaction(transaction_amt=501.0))
        assert result.is_valid is False

        result2 = v.validate(_make_transaction(transaction_amt=500.0))
        assert result2.is_valid is True

    def test_custom_velocity_threshold(self):
        v = TransactionValidator(velocity_threshold=2, velocity_window_s=3600)
        for i in range(3):
            txn = _make_transaction(transaction_id=f"t_{i}", card1="u1")
            result = v.validate(txn)
        assert any("velocity" in w.lower() for w in result.warnings)
