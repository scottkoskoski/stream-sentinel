# /stream-sentinel/src/ml/features/feature_engineer.py

"""
Unified Feature Engineering for Stream-Sentinel

Computes derived features for both offline training (batch DataFrames) and
online inference (single-transaction dicts).  Every computation is implemented
twice -- once vectorised for pandas and once scalar for streaming -- behind
the same public interface so that training and serving always agree on feature
definitions.

Feature groups
--------------
1. Velocity features     -- txns per hour / per day for user
2. Merchant risk score   -- fraud rate lookup by merchant category
3. Amount anomaly        -- z-score vs user historical mean/std
4. Temporal features     -- time_since_last, is_weekend, day_of_week, is_business_hours
5. Interaction features  -- amount * hour_risk_multiplier, velocity * amount_deviation
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class FeatureConfig:
    """Tunable knobs for the feature engineering module."""

    # Merchant category risk scores (configurable lookup table).
    # Keys are ProductCD values from IEEE-CIS dataset; values are baseline
    # fraud-rate estimates.  Callers can override with empirical rates.
    merchant_risk_table: Dict[str, float] = field(default_factory=lambda: {
        "W": 0.035,   # most common, moderate risk
        "C": 0.055,   # card-present, slightly higher
        "H": 0.045,   # hospitality
        "R": 0.065,   # recurring / subscription
        "S": 0.080,   # services -- highest risk
    })
    default_merchant_risk: float = 0.04

    # Hour-of-day risk multipliers (0-23).
    # Higher multipliers for late-night / early-morning hours.
    hour_risk_multipliers: Dict[int, float] = field(default_factory=lambda: {
        0: 1.8, 1: 2.0, 2: 2.2, 3: 2.3, 4: 2.1, 5: 1.7,
        6: 1.2, 7: 1.0, 8: 0.9, 9: 0.8, 10: 0.8, 11: 0.8,
        12: 0.9, 13: 0.9, 14: 0.9, 15: 0.9, 16: 1.0, 17: 1.0,
        18: 1.1, 19: 1.2, 20: 1.3, 21: 1.4, 22: 1.5, 23: 1.7,
    })

    # Business hours definition
    business_hours_start: int = 9
    business_hours_end: int = 17

    # Velocity thresholds (informational -- used only for logging)
    high_velocity_per_hour: float = 5.0
    high_velocity_per_day: float = 30.0


# Singleton default config -- importable for convenience
DEFAULT_FEATURE_CONFIG = FeatureConfig()


# ---------------------------------------------------------------------------
# Streaming (single-record) helpers
# ---------------------------------------------------------------------------

def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# FeatureEngineer
# ---------------------------------------------------------------------------

class FeatureEngineer:
    """
    Compute derived features for fraud detection.

    Usage -- **streaming** (fraud_detector consumer)::

        fe = FeatureEngineer()
        extra = fe.compute_streaming_features(transaction_dict, user_profile_dict)
        # extra is a flat dict of new feature values

    Usage -- **batch** (data_processor training pipeline)::

        fe = FeatureEngineer()
        df = fe.compute_batch_features(df)
        # df now has extra columns appended
    """

    def __init__(self, config: Optional[FeatureConfig] = None):
        self.config = config or DEFAULT_FEATURE_CONFIG
        logger.info("FeatureEngineer initialised with %d merchant categories",
                     len(self.config.merchant_risk_table))

    # ------------------------------------------------------------------
    # Public API -- streaming context
    # ------------------------------------------------------------------

    def compute_streaming_features(
        self,
        transaction: Dict[str, Any],
        user_profile: Dict[str, Any],
    ) -> Dict[str, float]:
        """
        Compute all derived features for a single transaction.

        Parameters
        ----------
        transaction : dict
            Raw transaction fields (keys like ``transaction_amt``,
            ``generated_timestamp``, ``ProductCD``, ``card1``, etc.).
        user_profile : dict
            User state with keys: ``total_transactions``, ``total_amount``,
            ``avg_transaction_amount``, ``last_transaction_time``,
            ``daily_transaction_count``, ``daily_amount``,
            ``amount_std`` (optional -- if unavailable, z-score uses estimate).

        Returns
        -------
        dict[str, float]
            Flat mapping of feature-name to value.
        """
        features: Dict[str, float] = {}

        amount = _safe_float(transaction.get("transaction_amt"))
        timestamp_str = transaction.get("generated_timestamp", "")
        product_cd = str(transaction.get("ProductCD",
                         transaction.get("product_cd", "W")) or "W")

        # Parse timestamp safely
        hour, day_of_week, is_weekend, ts_epoch = self._parse_timestamp(timestamp_str)

        # --- 1. Velocity features ----------------------------------------
        daily_count = _safe_float(user_profile.get("daily_transaction_count"))
        total_txns = _safe_float(user_profile.get("total_transactions"))

        features["velocity_per_hour"] = daily_count / 24.0 if daily_count > 0 else 0.0
        features["velocity_per_day"] = daily_count

        # --- 2. Merchant risk score --------------------------------------
        features["merchant_risk_score"] = self.config.merchant_risk_table.get(
            product_cd, self.config.default_merchant_risk
        )

        # --- 3. Amount anomaly (z-score) ---------------------------------
        avg_amt = _safe_float(user_profile.get("avg_transaction_amount"))
        amt_std = _safe_float(user_profile.get("amount_std"))

        if amt_std > 0:
            features["amount_zscore"] = (amount - avg_amt) / amt_std
        elif avg_amt > 0 and total_txns >= 2:
            # Rough estimate: std ~ 0.5 * mean (fallback when std not tracked)
            estimated_std = avg_amt * 0.5
            features["amount_zscore"] = (amount - avg_amt) / estimated_std
        else:
            features["amount_zscore"] = 0.0

        # --- 4. Temporal features ----------------------------------------
        features["hour_of_day"] = float(hour)
        features["day_of_week"] = float(day_of_week)
        features["is_weekend"] = float(is_weekend)
        features["is_business_hours"] = float(
            self.config.business_hours_start <= hour < self.config.business_hours_end
            and not is_weekend
        )

        last_ts_str = user_profile.get("last_transaction_time")
        if last_ts_str and ts_epoch > 0:
            _, _, _, last_epoch = self._parse_timestamp(str(last_ts_str))
            if last_epoch > 0:
                features["time_since_last_txn"] = ts_epoch - last_epoch
            else:
                features["time_since_last_txn"] = 0.0
        else:
            features["time_since_last_txn"] = 0.0

        # --- 5. Interaction features -------------------------------------
        hour_risk = self.config.hour_risk_multipliers.get(hour, 1.0)
        features["amount_x_hour_risk"] = amount * hour_risk

        amount_deviation = abs(features["amount_zscore"])
        features["velocity_x_amount_deviation"] = features["velocity_per_hour"] * amount_deviation

        return features

    # ------------------------------------------------------------------
    # Public API -- batch context
    # ------------------------------------------------------------------

    def compute_batch_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add derived feature columns to a training DataFrame **in-place** and
        return the same DataFrame.

        Expected columns
        ----------------
        - ``TransactionAmt``
        - ``TransactionDT`` (epoch seconds from IEEE-CIS dataset)
        - ``ProductCD``
        - Optionally per-user aggregates (added externally or computed here)

        The method creates user-level aggregates when they are not already
        present, using a group-by on ``card1`` (user proxy).
        """
        df = df.copy()

        # Ensure required columns have safe types
        if "TransactionAmt" not in df.columns:
            logger.warning("TransactionAmt column missing; skipping batch feature engineering")
            return df

        amount = df["TransactionAmt"].fillna(0.0)

        # --- Temporal features from TransactionDT -------------------------
        dt_col = None
        for candidate in ("TransactionDT", "TransactionDT_raw"):
            if candidate in df.columns:
                dt_col = candidate
                break

        if dt_col is not None:
            seconds = df[dt_col].fillna(0).astype(float)
            hour = (seconds / 3600) % 24
            df["feat_hour_of_day"] = hour
            df["feat_day_of_week"] = ((seconds / 86400) % 7).astype(int)
            df["feat_is_weekend"] = df["feat_day_of_week"].isin([5, 6]).astype(float)
            df["feat_is_business_hours"] = (
                (hour >= self.config.business_hours_start)
                & (hour < self.config.business_hours_end)
                & (~df["feat_is_weekend"].astype(bool))
            ).astype(float)
        else:
            logger.info("No TransactionDT column found; temporal features skipped in batch mode")

        # --- Merchant risk score ------------------------------------------
        if "ProductCD" in df.columns:
            df["feat_merchant_risk_score"] = (
                df["ProductCD"]
                .map(self.config.merchant_risk_table)
                .fillna(self.config.default_merchant_risk)
            )
        else:
            df["feat_merchant_risk_score"] = self.config.default_merchant_risk

        # --- Per-user velocity and amount stats ---------------------------
        user_col = "card1" if "card1" in df.columns else None

        if user_col is not None:
            user_group = df.groupby(user_col)

            # Velocity: transaction count per user (proxy for daily velocity
            # within training window)
            user_counts = user_group[user_col].transform("count")
            df["feat_velocity_per_day"] = user_counts.astype(float)
            df["feat_velocity_per_hour"] = df["feat_velocity_per_day"] / 24.0

            # Amount statistics per user
            df["feat_user_mean_amt"] = user_group["TransactionAmt"].transform("mean")
            df["feat_user_std_amt"] = user_group["TransactionAmt"].transform("std").fillna(0.0)

            # Amount z-score
            std_safe = df["feat_user_std_amt"].replace(0, np.nan)
            df["feat_amount_zscore"] = (
                (amount - df["feat_user_mean_amt"]) / std_safe
            ).fillna(0.0)

            # Time since last transaction (within sorted data)
            if dt_col is not None:
                df_sorted = df.sort_values([user_col, dt_col])
                df["feat_time_since_last_txn"] = (
                    df_sorted.groupby(user_col)[dt_col].diff().fillna(0.0)
                )
            else:
                df["feat_time_since_last_txn"] = 0.0
        else:
            # No user column -- fill with dataset-level stats
            df["feat_velocity_per_day"] = 0.0
            df["feat_velocity_per_hour"] = 0.0
            df["feat_user_mean_amt"] = amount.mean()
            df["feat_user_std_amt"] = amount.std()
            overall_std = amount.std()
            df["feat_amount_zscore"] = (
                (amount - amount.mean()) / overall_std if overall_std > 0 else 0.0
            )
            df["feat_time_since_last_txn"] = 0.0

        # --- Interaction features -----------------------------------------
        if "feat_hour_of_day" in df.columns:
            hour_risk_series = df["feat_hour_of_day"].apply(
                lambda h: self.config.hour_risk_multipliers.get(int(h) % 24, 1.0)
            )
            df["feat_amount_x_hour_risk"] = amount * hour_risk_series
        else:
            df["feat_amount_x_hour_risk"] = amount

        amt_dev = df.get("feat_amount_zscore", pd.Series(0.0, index=df.index)).abs()
        vel = df.get("feat_velocity_per_hour", pd.Series(0.0, index=df.index))
        df["feat_velocity_x_amount_deviation"] = vel * amt_dev

        logger.info(
            "Batch feature engineering complete: added %d derived columns",
            sum(1 for c in df.columns if c.startswith("feat_")),
        )
        return df

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_timestamp(ts_str: str) -> Tuple[int, int, bool, float]:
        """
        Parse an ISO-format timestamp string.

        Returns (hour, day_of_week, is_weekend, epoch_seconds).
        On failure returns (0, 0, False, 0.0).
        """
        if not ts_str:
            return 0, 0, False, 0.0
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(str(ts_str))
            return (
                dt.hour,
                dt.weekday(),
                dt.weekday() >= 5,
                dt.timestamp(),
            )
        except (ValueError, TypeError, OSError):
            return 0, 0, False, 0.0

    # ------------------------------------------------------------------
    # Feature name catalogue (useful for model training metadata)
    # ------------------------------------------------------------------

    @staticmethod
    def streaming_feature_names() -> List[str]:
        """Return the canonical list of features produced in streaming mode."""
        return [
            "velocity_per_hour",
            "velocity_per_day",
            "merchant_risk_score",
            "amount_zscore",
            "hour_of_day",
            "day_of_week",
            "is_weekend",
            "is_business_hours",
            "time_since_last_txn",
            "amount_x_hour_risk",
            "velocity_x_amount_deviation",
        ]

    @staticmethod
    def batch_feature_names() -> List[str]:
        """Return the canonical list of columns produced in batch mode."""
        return [
            "feat_hour_of_day",
            "feat_day_of_week",
            "feat_is_weekend",
            "feat_is_business_hours",
            "feat_merchant_risk_score",
            "feat_velocity_per_day",
            "feat_velocity_per_hour",
            "feat_user_mean_amt",
            "feat_user_std_amt",
            "feat_amount_zscore",
            "feat_time_since_last_txn",
            "feat_amount_x_hour_risk",
            "feat_velocity_x_amount_deviation",
        ]
