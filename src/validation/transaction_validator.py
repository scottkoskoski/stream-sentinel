"""
Transaction input validation for Stream-Sentinel.

Validates incoming transactions at the Kafka consumer ingestion point
before they enter the fraud detection pipeline. Designed for < 1ms
overhead per transaction with in-memory duplicate/velocity tracking.

Usage:
    from validation.transaction_validator import TransactionValidator

    validator = TransactionValidator()
    result = validator.validate(transaction_dict)
    if not result.is_valid:
        # send to DLQ
    elif result.warnings:
        # proceed but log warnings
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("stream_sentinel.validation")

# ---------------------------------------------------------------------------
# Prometheus metrics -- importable even when prometheus_client is absent.
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter

    transactions_validated_total = Counter(
        "transactions_validated_total",
        "Total transactions that passed through validation",
        ["result"],  # "valid", "rejected", "warned"
    )
    validation_errors_total = Counter(
        "validation_errors_total",
        "Total hard validation errors by type",
        ["error_type"],
    )
    validation_warnings_total = Counter(
        "validation_warnings_total",
        "Total validation warnings by type",
        ["warning_type"],
    )
except ImportError:
    transactions_validated_total = None
    validation_errors_total = None
    validation_warnings_total = None


# ---------------------------------------------------------------------------
# Configuration defaults (overridable via environment variables)
# ---------------------------------------------------------------------------
DEFAULT_MAX_TRANSACTION_AMT = 10_000.0
DEFAULT_TIMESTAMP_FUTURE_TOLERANCE_SECONDS = 300  # 5 minutes
DEFAULT_DUPLICATE_WINDOW_SECONDS = 60
DEFAULT_VELOCITY_THRESHOLD = 100  # transactions per hour
DEFAULT_VELOCITY_WINDOW_SECONDS = 3600  # 1 hour

VALID_PRODUCT_CODES: Set[str] = {"W", "C", "R", "H", "S"}
VALID_CARD4_VALUES: Set[str] = {"visa", "mastercard", "discover", "american express"}

REQUIRED_FIELDS = ["transaction_id", "transaction_amt", "generated_timestamp"]
# At least one user identifier must be present: user_id or card1
USER_ID_FIELDS = ["user_id", "card1"]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class ValidationResult:
    """Outcome of transaction validation."""

    is_valid: bool
    transaction: dict
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    validation_time_ms: float = 0.0


# ---------------------------------------------------------------------------
# TransactionValidator
# ---------------------------------------------------------------------------
class TransactionValidator:
    """Fast, in-memory transaction validator.

    Performs schema checks, business-rule checks (duplicate detection,
    velocity), and annotates the result with warnings or hard errors.

    Thread-safe: internal state is guarded by a lock.
    """

    def __init__(
        self,
        max_transaction_amt: Optional[float] = None,
        timestamp_future_tolerance_s: Optional[int] = None,
        duplicate_window_s: Optional[int] = None,
        velocity_threshold: Optional[int] = None,
        velocity_window_s: Optional[int] = None,
    ):
        # Configurable thresholds (env vars override defaults)
        self.max_transaction_amt = max_transaction_amt or float(
            os.environ.get("VALIDATION_MAX_TRANSACTION_AMT", DEFAULT_MAX_TRANSACTION_AMT)
        )
        self.timestamp_future_tolerance_s = timestamp_future_tolerance_s or int(
            os.environ.get("VALIDATION_TIMESTAMP_FUTURE_TOLERANCE_S", DEFAULT_TIMESTAMP_FUTURE_TOLERANCE_SECONDS)
        )
        self.duplicate_window_s = duplicate_window_s or int(
            os.environ.get("VALIDATION_DUPLICATE_WINDOW_S", DEFAULT_DUPLICATE_WINDOW_SECONDS)
        )
        self.velocity_threshold = velocity_threshold or int(
            os.environ.get("VALIDATION_VELOCITY_THRESHOLD", DEFAULT_VELOCITY_THRESHOLD)
        )
        self.velocity_window_s = velocity_window_s or int(
            os.environ.get("VALIDATION_VELOCITY_WINDOW_S", DEFAULT_VELOCITY_WINDOW_SECONDS)
        )

        # In-memory duplicate tracking: {transaction_id: timestamp_seen}
        self._seen_ids: Dict[str, float] = {}
        # In-memory velocity tracking: {user_id: [timestamp1, timestamp2, ...]}
        self._user_timestamps: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

        # Track last cleanup time to avoid excessive sweeps
        self._last_cleanup = time.monotonic()
        self._cleanup_interval_s = 10.0  # sweep every 10 seconds at most

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, transaction: Any) -> ValidationResult:
        """Validate a transaction dict.

        Returns a ``ValidationResult`` with ``is_valid=False`` on hard
        errors (missing required fields, bad types) and warnings for
        soft issues (duplicates, high velocity).  Validation is
        designed to complete in < 1 ms for the hot path.
        """
        start = time.monotonic()
        errors: List[str] = []
        warnings: List[str] = []

        # --- Basic type guard ---
        if not isinstance(transaction, dict):
            errors.append("Transaction must be a dict")
            result = ValidationResult(
                is_valid=False,
                transaction=transaction if isinstance(transaction, dict) else {},
                errors=errors,
                warnings=warnings,
                validation_time_ms=(time.monotonic() - start) * 1000,
            )
            self._record_metrics(result)
            return result

        # --- Required fields ---
        self._check_required_fields(transaction, errors)

        # --- Field type / value checks ---
        self._check_transaction_amt(transaction, errors)
        self._check_timestamp(transaction, errors, warnings)
        self._check_transaction_id_type(transaction, errors)
        self._check_product_cd(transaction, warnings)
        self._check_card4(transaction, warnings)

        # Short-circuit: if we already have hard errors, skip business rules
        if errors:
            result = ValidationResult(
                is_valid=False,
                transaction=transaction,
                errors=errors,
                warnings=warnings,
                validation_time_ms=(time.monotonic() - start) * 1000,
            )
            self._record_metrics(result)
            return result

        # --- Business rule checks (non-blocking) ---
        now = time.monotonic()
        with self._lock:
            self._maybe_cleanup(now)
            self._check_duplicate(transaction, warnings, now)
            self._check_velocity(transaction, warnings, now)

        result = ValidationResult(
            is_valid=True,
            transaction=transaction,
            errors=errors,
            warnings=warnings,
            validation_time_ms=(time.monotonic() - start) * 1000,
        )
        self._record_metrics(result)
        return result

    # ------------------------------------------------------------------
    # Schema validation helpers
    # ------------------------------------------------------------------

    def _check_required_fields(self, txn: dict, errors: List[str]) -> None:
        for f in REQUIRED_FIELDS:
            if f not in txn or txn[f] is None:
                errors.append(f"Missing required field: {f}")

        # At least one user identifier required
        has_user_id = any(f in txn and txn[f] is not None for f in USER_ID_FIELDS)
        if not has_user_id:
            errors.append(f"Missing user identifier: at least one of {USER_ID_FIELDS} is required")

    def _check_transaction_amt(self, txn: dict, errors: List[str]) -> None:
        amt = txn.get("transaction_amt")
        if amt is None:
            return  # already caught by required-field check

        # Allow numeric strings (the producer sometimes serialises as string)
        if isinstance(amt, str):
            try:
                amt = float(amt)
            except (ValueError, TypeError):
                errors.append(f"transaction_amt is not numeric: {amt!r}")
                return

        if not isinstance(amt, (int, float)):
            errors.append(f"transaction_amt must be numeric, got {type(amt).__name__}")
            return

        if amt <= 0:
            errors.append(f"transaction_amt must be > 0, got {amt}")
        elif amt > self.max_transaction_amt:
            errors.append(f"transaction_amt exceeds maximum ({amt} > {self.max_transaction_amt})")

    def _check_transaction_id_type(self, txn: dict, errors: List[str]) -> None:
        tid = txn.get("transaction_id")
        if tid is None:
            return  # already caught by required-field check
        if not isinstance(tid, str):
            errors.append(f"transaction_id must be a string, got {type(tid).__name__}")

    def _check_timestamp(self, txn: dict, errors: List[str], warnings: List[str]) -> None:
        ts_raw = txn.get("generated_timestamp")
        if ts_raw is None:
            return  # already caught by required-field check

        if not isinstance(ts_raw, str):
            errors.append(f"generated_timestamp must be a string, got {type(ts_raw).__name__}")
            return

        try:
            dt = datetime.fromisoformat(ts_raw)
        except (ValueError, TypeError):
            errors.append(f"generated_timestamp is not valid ISO format: {ts_raw!r}")
            return

        # Future-timestamp check (with tolerance)
        now_utc = datetime.now(timezone.utc)
        # If the parsed datetime is naive, assume UTC for comparison
        if dt.tzinfo is None:
            dt_aware = dt.replace(tzinfo=timezone.utc)
        else:
            dt_aware = dt

        delta_seconds = (dt_aware - now_utc).total_seconds()
        if delta_seconds > self.timestamp_future_tolerance_s:
            errors.append(
                f"generated_timestamp is too far in the future "
                f"({delta_seconds:.0f}s ahead, tolerance={self.timestamp_future_tolerance_s}s)"
            )

    def _check_product_cd(self, txn: dict, warnings: List[str]) -> None:
        pcd = txn.get("product_cd")
        if pcd is not None and pcd not in VALID_PRODUCT_CODES:
            warnings.append(f"Unknown product_cd: {pcd!r} (expected one of {sorted(VALID_PRODUCT_CODES)})")

    def _check_card4(self, txn: dict, warnings: List[str]) -> None:
        card4 = txn.get("card4")
        if card4 is not None:
            if not isinstance(card4, str):
                warnings.append(f"card4 should be a string, got {type(card4).__name__}")
                return
            if card4.lower() not in VALID_CARD4_VALUES:
                warnings.append(f"Unknown card4 value: {card4!r} (expected one of {sorted(VALID_CARD4_VALUES)})")

    # ------------------------------------------------------------------
    # Business rule helpers (must be called under self._lock)
    # ------------------------------------------------------------------

    def _check_duplicate(self, txn: dict, warnings: List[str], now: float) -> None:
        tid = txn.get("transaction_id")
        if tid is None:
            return

        if tid in self._seen_ids:
            warnings.append(f"Duplicate transaction_id detected: {tid}")
        # Always update the timestamp so the TTL window slides
        self._seen_ids[tid] = now

    def _check_velocity(self, txn: dict, warnings: List[str], now: float) -> None:
        user_id = txn.get("user_id") or txn.get("card1")
        if user_id is None:
            return

        user_id = str(user_id)
        timestamps = self._user_timestamps.setdefault(user_id, [])
        timestamps.append(now)

        # Count transactions within the velocity window
        cutoff = now - self.velocity_window_s
        recent = [t for t in timestamps if t >= cutoff]
        self._user_timestamps[user_id] = recent  # prune old entries

        if len(recent) > self.velocity_threshold:
            warnings.append(
                f"High velocity for user {user_id}: "
                f"{len(recent)} transactions in last {self.velocity_window_s}s "
                f"(threshold={self.velocity_threshold})"
            )

    # ------------------------------------------------------------------
    # Cleanup / metrics
    # ------------------------------------------------------------------

    def _maybe_cleanup(self, now: float) -> None:
        """Evict expired entries from tracking dicts.

        Called under ``self._lock``.  Only runs at most once per
        ``_cleanup_interval_s`` to amortise the cost.
        """
        if now - self._last_cleanup < self._cleanup_interval_s:
            return
        self._last_cleanup = now

        # Evict stale duplicate IDs
        dup_cutoff = now - self.duplicate_window_s
        expired_ids = [tid for tid, ts in self._seen_ids.items() if ts < dup_cutoff]
        for tid in expired_ids:
            del self._seen_ids[tid]

        # Evict users with no recent activity
        vel_cutoff = now - self.velocity_window_s
        stale_users = []
        for uid, timestamps in self._user_timestamps.items():
            fresh = [t for t in timestamps if t >= vel_cutoff]
            if fresh:
                self._user_timestamps[uid] = fresh
            else:
                stale_users.append(uid)
        for uid in stale_users:
            del self._user_timestamps[uid]

    def _record_metrics(self, result: ValidationResult) -> None:
        """Increment Prometheus counters if available."""
        if transactions_validated_total is None:
            return

        if not result.is_valid:
            transactions_validated_total.labels(result="rejected").inc()
            for err in result.errors:
                # Extract a short error type from the message
                error_type = self._error_type_from_message(err)
                validation_errors_total.labels(error_type=error_type).inc()
        elif result.warnings:
            transactions_validated_total.labels(result="warned").inc()
            for warn in result.warnings:
                warning_type = self._warning_type_from_message(warn)
                validation_warnings_total.labels(warning_type=warning_type).inc()
        else:
            transactions_validated_total.labels(result="valid").inc()

    @staticmethod
    def _error_type_from_message(msg: str) -> str:
        if "Missing required field" in msg:
            return "missing_required_field"
        if "Missing user identifier" in msg:
            return "missing_user_identifier"
        if "not numeric" in msg or "must be numeric" in msg:
            return "invalid_amount_type"
        if "must be > 0" in msg:
            return "invalid_amount_value"
        if "exceeds maximum" in msg:
            return "amount_exceeds_max"
        if "must be a string" in msg and "transaction_id" in msg:
            return "invalid_transaction_id_type"
        if "not valid ISO" in msg or "must be a string" in msg:
            return "invalid_timestamp"
        if "too far in the future" in msg:
            return "future_timestamp"
        return "unknown"

    @staticmethod
    def _warning_type_from_message(msg: str) -> str:
        if "Duplicate" in msg:
            return "duplicate_transaction"
        if "High velocity" in msg:
            return "high_velocity"
        if "product_cd" in msg:
            return "unknown_product_cd"
        if "card4" in msg:
            return "unknown_card4"
        return "unknown"
