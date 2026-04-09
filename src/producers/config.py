"""
Centralized configuration for synthetic transaction generation.

All generation parameters are defined here instead of being hardcoded
throughout the producer. This includes:
- Fraud rate and temporal patterns
- Per-feature null rates based on IEEE-CIS dataset statistics
- Card type and product code distributions
- Amount distributions for legitimate and fraudulent transactions
- User behavior templates
- Entity tracking parameters

The default values are derived from published IEEE-CIS Fraud Detection
dataset statistics (Kaggle competition). When an actual analysis JSON
is available at data/processed/ieee_cis_analysis.json, those values
take precedence.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Temporal fraud patterns
# ---------------------------------------------------------------------------
# Peak fraud hours: 2-4 AM (realistic for card-not-present fraud).
# The multiplier scales the base fraud rate for each hour of the day.
TEMPORAL_FRAUD_MULTIPLIERS: Dict[int, float] = {
    # Derived from IEEE-CIS hourly fraud rates divided by base rate (2.71%).
    # Average multiplier ~1.05 to avoid inflating overall fraud rate.
    0: 1.30,
    1: 1.50,
    2: 1.76,  # Peak window start
    3: 2.02,  # Peak
    4: 1.82,  # Peak window end
    5: 1.39,
    6: 1.08,
    7: 0.93,
    8: 0.84,
    9: 0.76,
    10: 0.70,
    11: 0.68,
    12: 0.69,
    13: 0.67,
    14: 0.68,
    15: 0.71,
    16: 0.76,
    17: 0.80,
    18: 0.85,
    19: 0.90,
    20: 0.97,
    21: 1.03,
    22: 1.10,
    23: 1.17,
}

PEAK_FRAUD_HOURS: List[int] = [
    0,
    1,
    2,
    3,
    4,
    5,
]  # IEEE-CIS high-risk hours (was [2,3,4])


# ---------------------------------------------------------------------------
# Fraud rate
# ---------------------------------------------------------------------------
BASE_FRAUD_RATE: float = 0.0271  # 2.71% -- matches IEEE-CIS dataset

# Normalization factor to compensate for multiplicative compounding of
# temporal (avg 1.05x), risk (avg 0.88x), amount (avg ~1.05x), and
# velocity (avg ~1.2x) multipliers. Without this, effective rate is ~4%.
FRAUD_RATE_NORMALIZATION: float = 0.65  # Calibrated empirically to yield ~2.7% effective rate
MAX_FRAUD_PROBABILITY: float = 0.15  # Hard cap per-transaction

# Amount-based fraud multipliers
SMALL_AMOUNT_THRESHOLD: float = 10.0
SMALL_AMOUNT_FRAUD_MULTIPLIER: float = (
    1.5  # Reduced from 1.9 -- compounding with temporal/risk/velocity inflated overall rate
)
LARGE_AMOUNT_THRESHOLD: float = 500.0
LARGE_AMOUNT_FRAUD_MULTIPLIER: float = 0.97  # IEEE shows ~2.63% for $500-1k vs 2.71% base (was 0.8)

# Risk profile multipliers
RISK_PROFILE_MULTIPLIERS: Dict[str, float] = {
    "low": 0.7,  # Weighted avg with distribution (60/30/10) = 0.7*0.6+1.0*0.3+1.6*0.1 = 0.88
    "medium": 1.0,
    "high": 1.6,  # Reduced from 2.0 to limit compounding
}

# Velocity fraud: if time since last txn < this many seconds, multiply
VELOCITY_WINDOW_SECONDS: int = 60  # Narrowed from 300s -- only rapid-fire triggers velocity
VELOCITY_FRAUD_MULTIPLIER: float = 1.5  # Reduced from 3.0 -- multiplicative compounding inflated rate
VELOCITY_MIN_TRANSACTIONS: int = 30  # Increased from 10 to reduce velocity trigger frequency


# ---------------------------------------------------------------------------
# Amount distribution (log-normal, from IEEE-CIS)
# ---------------------------------------------------------------------------
AMOUNT_DISTRIBUTION = {
    "mean_log": 4.0,
    "std_log": 1.2,
    "min_amount": 0.25,  # Aligned with IEEE-CIS spec (was 1.0)
    "max_amount": 5000.0,  # Raised to capture high-value fraud patterns (IEEE-CIS max: $31,937)
}

# Fraud amount bias: mean_log is multiplied by this for fraud transactions
FRAUD_AMOUNT_BIAS: float = 1.34  # Aligned with IEEE-CIS spec (was 1.2)


# ---------------------------------------------------------------------------
# Product code distribution (from IEEE-CIS)
# ---------------------------------------------------------------------------
PRODUCT_CODE_DISTRIBUTION: Dict[str, float] = {
    "W": 0.7434,
    "C": 0.1355,
    "R": 0.0629,
    "H": 0.0476,
    "S": 0.0106,
}


# ---------------------------------------------------------------------------
# Card feature distributions
# ---------------------------------------------------------------------------
CARD4_DISTRIBUTION: Dict[str, float] = {
    "visa": 0.58,
    "mastercard": 0.34,
    "discover": 0.05,
    "american express": 0.03,
}

CARD6_DISTRIBUTION: Dict[str, float] = {
    "debit": 0.26,
    "credit": 0.61,
    "debit or credit": 0.10,
    "charge card": 0.03,
}

CARD2_MISSING_RATE: float = 0.015  # ~1.5% null in IEEE-CIS
CARD3_COMMON_VALUE: float = 150.0
CARD5_RANGE: Tuple[int, int] = (100, 226)


# ---------------------------------------------------------------------------
# Email domain distributions
# ---------------------------------------------------------------------------
P_EMAIL_DOMAINS: Dict[str, float] = {
    "gmail.com": 0.32,
    "yahoo.com": 0.18,
    "hotmail.com": 0.08,
    "anonymous.com": 0.06,
    "outlook.com": 0.05,
    "aol.com": 0.04,
    "comcast.net": 0.03,
    "icloud.com": 0.03,
    "mail.com": 0.02,
    "protonmail.com": 0.01,
}
P_EMAIL_MISSING_RATE: float = 0.14  # ~14% null in IEEE-CIS

R_EMAIL_MISSING_RATE: float = 0.77  # ~77% null in IEEE-CIS -- very sparse
R_EMAIL_DOMAINS: Dict[str, float] = {
    "gmail.com": 0.30,
    "yahoo.com": 0.20,
    "hotmail.com": 0.12,
    "outlook.com": 0.08,
    "anonymous.com": 0.07,
    "aol.com": 0.05,
    "comcast.net": 0.04,
    "icloud.com": 0.04,
    "mail.com": 0.05,
    "protonmail.com": 0.05,
}


# ---------------------------------------------------------------------------
# Address and distance feature parameters
# ---------------------------------------------------------------------------
ADDR2_BASE: float = 87.0
ADDR2_RANGE: int = 10
DIST1_PRESENT_RATE: float = 0.40  # ~40% non-null
DIST2_PRESENT_RATE: float = 0.03  # Only ~3% non-null (very sparse)
DIST1_RANGE: Tuple[float, float] = (0.0, 1000.0)
DIST2_RANGE: Tuple[float, float] = (0.0, 500.0)


# ---------------------------------------------------------------------------
# C-feature (counting) null rates
#
# Derived from IEEE-CIS dataset missing value analysis:
#   C1-C2: core entity counts, rarely null (~2%)
#   C3-C5: moderate sparsity (~20-25%)
#   C6-C8: higher sparsity (~30-40%)
#   C9-C11: moderate (~15-25%)
#   C12-C14: variable (~10-35%)
# ---------------------------------------------------------------------------
C_FEATURE_NULL_RATES: Dict[str, float] = {
    "c1": 0.00,  # IEEE-CIS: 0% null (was 0.02)
    "c2": 0.00,  # IEEE-CIS: 0% null (was 0.02)
    "c3": 0.00,  # IEEE-CIS: 0% null (was 0.20)
    "c4": 0.00,  # IEEE-CIS: 0% null (was 0.08)
    "c5": 0.00,  # IEEE-CIS: 0% null (was 0.25)
    "c6": 0.15,  # IEEE-CIS: 15% null (was 0.30)
    "c7": 0.15,  # IEEE-CIS: 15% null (was 0.35)
    "c8": 0.15,  # IEEE-CIS: 15% null (was 0.40)
    "c9": 0.00,  # IEEE-CIS: 0% null (was 0.15)
    "c10": 0.15,  # IEEE-CIS: 15% null (was 0.30)
    "c11": 0.15,  # IEEE-CIS: 15% null (was 0.35)
    "c12": 0.00,  # IEEE-CIS: 0% null (was 0.10)
    "c13": 0.00,  # IEEE-CIS: 0% null (was 0.20)
    "c14": 0.00,  # IEEE-CIS: 0% null (was 0.25)
}


# ---------------------------------------------------------------------------
# D-feature (time delta) null rates
#
# From IEEE-CIS dataset:
#   D1: ~0% null (almost always present)
#   D2-D3: ~47% null
#   D4-D8: ~57-75% null (progressively sparser)
#   D9-D15: ~86-91% null (very sparse in original data)
# ---------------------------------------------------------------------------
D_FEATURE_NULL_RATES: Dict[str, float] = {
    "d1": 0.002,
    "d2": 0.47,
    "d3": 0.47,
    "d4": 0.57,
    "d5": 0.75,
    "d6": 0.57,
    "d7": 0.65,
    "d8": 0.68,
    "d9": 0.86,
    "d10": 0.87,
    "d11": 0.87,
    "d12": 0.89,
    "d13": 0.89,
    "d14": 0.90,
    "d15": 0.91,
}


# ---------------------------------------------------------------------------
# M-feature (match) null rates
#
# From IEEE-CIS dataset:
#   M1-M3: ~47% null
#   M4: ~53% null
#   M5-M6: ~47-53% null
#   M7-M9: ~53-57% null
# ---------------------------------------------------------------------------
M_FEATURE_NULL_RATES: Dict[str, float] = {
    "m1": 0.47,
    "m2": 0.47,
    "m3": 0.47,
    "m4": 0.53,
    "m5": 0.47,
    "m6": 0.47,
    "m7": 0.53,
    "m8": 0.57,
    "m9": 0.53,
}


# ---------------------------------------------------------------------------
# M-feature match probabilities
#
# For legitimate transactions: high T rate (identity checks pass)
# For fraudulent transactions: lower T rate (mismatches more common)
#
# Format: (T_weight, F_weight, NotFound_weight)
# ---------------------------------------------------------------------------
M_LEGITIMATE_WEIGHTS: Dict[str, Tuple[float, float, float]] = {
    "m1": (0.82, 0.13, 0.05),  # Name-address match
    "m2": (0.75, 0.18, 0.07),  # Email-card match
    "m3": (0.78, 0.16, 0.06),  # Phone-address match
    "m4": (0.88, 0.08, 0.04),  # Timezone match
    "m5": (0.90, 0.07, 0.03),  # Behavior pattern match
    "m6": (0.80, 0.15, 0.05),  # IP-address match
    "m7": (0.85, 0.11, 0.04),  # Card usage pattern match
    "m8": (0.25, 0.55, 0.20),  # Email-merchant match (usually no match)
    "m9": (0.83, 0.12, 0.05),  # Time pattern match
}

M_FRAUD_WEIGHTS: Dict[str, Tuple[float, float, float]] = {
    "m1": (0.35, 0.50, 0.15),
    "m2": (0.30, 0.50, 0.20),
    "m3": (0.32, 0.48, 0.20),
    "m4": (0.40, 0.42, 0.18),
    "m5": (0.25, 0.55, 0.20),
    "m6": (0.28, 0.52, 0.20),
    "m7": (0.30, 0.50, 0.20),
    "m8": (0.15, 0.60, 0.25),
    "m9": (0.30, 0.50, 0.20),
}


# ---------------------------------------------------------------------------
# Merchant categories (used by test fixture generator)
# ---------------------------------------------------------------------------
MERCHANT_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "grocery": {"fraud_rate": 0.01, "avg_amount": 45.0},
    "gas": {"fraud_rate": 0.015, "avg_amount": 35.0},
    "restaurant": {"fraud_rate": 0.02, "avg_amount": 28.0},
    "online": {"fraud_rate": 0.05, "avg_amount": 85.0},
    "retail": {"fraud_rate": 0.025, "avg_amount": 65.0},
    "pharmacy": {"fraud_rate": 0.008, "avg_amount": 22.0},
    "entertainment": {"fraud_rate": 0.03, "avg_amount": 75.0},
    "travel": {"fraud_rate": 0.04, "avg_amount": 250.0},
    "electronics": {"fraud_rate": 0.06, "avg_amount": 450.0},
    "jewelry": {"fraud_rate": 0.08, "avg_amount": 850.0},
}

HIGH_RISK_CATEGORIES: List[str] = ["online", "electronics", "jewelry", "travel"]


# ---------------------------------------------------------------------------
# User behavior templates
# ---------------------------------------------------------------------------
SPENDING_PATTERNS: Dict[str, Dict[str, Any]] = {
    "low": {
        "amount_range": (15.0, 50.0),
        "daily_frequency": (1, 3),
        "weight": 0.40,
    },
    "medium": {
        "amount_range": (40.0, 120.0),
        "daily_frequency": (2, 6),
        "weight": 0.40,
    },
    "high": {
        "amount_range": (100.0, 400.0),
        "daily_frequency": (3, 10),
        "weight": 0.20,
    },
}

TIME_PATTERNS: List[str] = ["business_hours", "evening", "night_owl", "random"]

# Location stability: how often a user transacts in their usual locations
LOCATION_STABILITY_RANGE: Tuple[float, float] = (0.60, 0.95)

# Number of devices per user
USER_DEVICE_RANGE: Tuple[int, int] = (1, 3)

# Number of typical locations per user
USER_LOCATION_RANGE: Tuple[int, int] = (2, 5)

# Risk profile distribution
RISK_PROFILE_DISTRIBUTION: Dict[str, float] = {
    "low": 0.60,
    "medium": 0.30,
    "high": 0.10,
}


# ---------------------------------------------------------------------------
# Fraud correlation parameters
#
# When a transaction is fraudulent, anomalies should be correlated rather
# than independently random. These parameters control the joint behavior.
# ---------------------------------------------------------------------------

# Probability that a fraud transaction gets the "full anomaly bundle"
# (unusual hour + high amount + velocity + mismatched M-features)
FRAUD_FULL_ANOMALY_RATE: float = 0.35

# Probability that individual anomaly dimensions fire for fraud txns
FRAUD_UNUSUAL_HOUR_RATE: float = 0.55  # Shift hour to 2-4 AM window
FRAUD_HIGH_AMOUNT_RATE: float = 0.40  # Use elevated amount
FRAUD_VELOCITY_BOOST_RATE: float = 0.30  # Inflate velocity counts
FRAUD_MISMATCH_RATE: float = 0.60  # Use fraud M-feature weights

# For the "full anomaly bundle", how many C-features get inflated
FRAUD_C_INFLATION_MIN: int = 3
FRAUD_C_INFLATION_MAX: int = 8


# ---------------------------------------------------------------------------
# Default IEEE-CIS analysis fallback
#
# Used when data/processed/ieee_cis_analysis.json is not available.
# These values are derived from the published Kaggle competition data.
# ---------------------------------------------------------------------------
DEFAULT_IEEE_CIS_ANALYSIS: Dict[str, Any] = {
    "analysis_metadata": {
        "analysis_date": "2025-01-15T00:00:00",
        "dataset_path": "data/raw/train_transaction.csv",
        "total_transactions_analyzed": 590540,
        "analyzer_version": "1.0.0",
        "source": "default_from_published_statistics",
    },
    "analysis_results": {
        "schema": {
            "fraud_rate": BASE_FRAUD_RATE,
        },
        "synthetic_spec": {
            "transaction_patterns": {
                "amount_distribution": AMOUNT_DISTRIBUTION,
                "product_codes": PRODUCT_CODE_DISTRIBUTION,
            },
            "fraud_patterns": {
                "base_fraud_rate": BASE_FRAUD_RATE,
                "amount_patterns": {
                    "high_amount_bias": FRAUD_AMOUNT_BIAS,
                },
                "temporal_bias": {
                    "high_risk_hours": PEAK_FRAUD_HOURS,
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# V-feature generation parameters
#
# V-features are Vesta's engineered features from the IEEE-CIS dataset.
# They represent various aggregations and counts related to payment card
# transactions. Grouped by type and importance.
# ---------------------------------------------------------------------------

# V1-V11: Card/address match flags [0, 1]
V_MATCH_FLAGS: List[str] = [f"V{i}" for i in range(1, 12)]

# V12-V14: Time-based aggregations [0, 300]
V_TIME_AGGS: List[str] = ["V12", "V13", "V14"]

# V19-V94: Transaction velocity/frequency features
V_VELOCITY_FEATURES: List[str] = [
    "V19",
    "V20",
    "V23",
    "V24",
    "V25",
    "V26",
    "V29",
    "V30",
    "V33",
    "V34",
    "V35",
    "V36",
    "V37",
    "V38",
    "V41",
    "V44",
    "V45",
    "V46",
    "V47",
    "V48",
    "V49",
    "V51",
    "V52",
    "V53",
    "V54",
    "V55",
    "V56",
    "V61",
    "V62",
    "V65",
    "V66",
    "V67",
    "V69",
    "V70",
    "V75",
    "V76",
    "V77",
    "V78",
    "V79",
    "V82",
    "V83",
    "V86",
    "V87",
    "V88",
    "V90",
    "V91",
    "V94",
]

# Null rate tiers for velocity features
V_VELOCITY_NULL_RATES: Dict[str, float] = {}
for _v in V_VELOCITY_FEATURES:
    _num = int(_v[1:])
    if _num <= 34:
        V_VELOCITY_NULL_RATES[_v] = 0.15
    elif _num <= 55:
        V_VELOCITY_NULL_RATES[_v] = 0.25
    else:
        V_VELOCITY_NULL_RATES[_v] = 0.30

# V107-V125: Critical time-based features (high model importance)
V_CRITICAL_TIME: List[str] = [
    "V107",
    "V108",
    "V109",
    "V110",
    "V111",
    "V112",
    "V113",
    "V114",
    "V115",
    "V116",
    "V117",
    "V118",
    "V119",
    "V120",
    "V121",
    "V122",
    "V123",
    "V124",
    "V125",
]

# V170-V204: Advanced aggregations (near-zero centered)
V_ADVANCED_LOW: List[str] = ["V170", "V171", "V176"]
V_ADVANCED_MID: List[str] = [
    "V186",
    "V187",
    "V188",
    "V189",
    "V190",
    "V191",
    "V192",
    "V193",
    "V194",
    "V195",
    "V196",
    "V197",
    "V198",
    "V199",
    "V200",
    "V201",
    "V203",
    "V204",
]

# V211-V265: Log-normal features (V258 is THE MOST IMPORTANT, gain=717.91)
V_LOGNORMAL: List[str] = [
    "V211",
    "V212",
    "V213",
    "V217",
    "V218",
    "V219",
    "V228",
    "V229",
    "V230",
    "V232",
    "V233",
    "V240",
    "V241",
    "V242",
    "V243",
    "V244",
    "V245",
    "V246",
    "V247",
    "V248",
    "V249",
    "V250",
    "V251",
    "V252",
    "V253",
    "V254",
    "V257",
    "V258",
    "V259",
    "V260",
    "V261",
    "V262",
    "V263",
    "V264",
    "V265",
]

# V273-V305: Sparse features (~40% null)
V_SPARSE: List[str] = [
    "V273",
    "V274",
    "V275",
    "V282",
    "V283",
    "V290",
    "V292",
    "V302",
    "V303",
    "V304",
    "V305",
]

# Top-importance V-features for fraud correlation
V_HIGH_IMPORTANCE: Dict[str, float] = {
    "V258": 3.0,  # THE most important feature -- fraud mean 3x legitimate
    "V201": 2.0,
    "V246": 2.0,
    "V12": 1.8,
    "V264": 1.8,
    "V244": 1.7,
}


# ---------------------------------------------------------------------------
# id-feature generation parameters
# ---------------------------------------------------------------------------

ID_CATEGORICAL_FEATURES: Dict[str, Dict[str, Any]] = {
    "id_12": {
        "values": ["Found", "NotFound", "unknown"],
        "weights": [0.55, 0.35, 0.10],
        "null_rate": 0.30,
    },
    "id_15": {
        "values": ["Found", "New", "Unknown", "unknown"],
        "weights": [0.50, 0.30, 0.10, 0.10],
        "null_rate": 0.30,
    },
    "id_16": {
        "values": ["Found", "NotFound", "unknown"],
        "weights": [0.55, 0.35, 0.10],
        "null_rate": 0.30,
    },
    "id_23": {
        "values": [
            "IP_PROXY:ANONYMOUS",
            "IP_PROXY:HIDDEN",
            "IP_PROXY:TRANSPARENT",
            "unknown",
        ],
        "weights": [0.10, 0.15, 0.60, 0.15],
        "fraud_weights": [0.40, 0.25, 0.20, 0.15],  # More ANONYMOUS for fraud
        "null_rate": 0.50,
    },
    "id_27": {
        "values": ["Found", "NotFound", "unknown"],
        "weights": [0.50, 0.40, 0.10],
        "null_rate": 0.30,
    },
    "id_28": {
        "values": ["Found", "New", "unknown"],
        "weights": [0.50, 0.35, 0.15],
        "null_rate": 0.30,
    },
    "id_29": {
        "values": ["Found", "NotFound", "unknown"],
        "weights": [0.50, 0.40, 0.10],
        "null_rate": 0.30,
    },
    "id_30": {
        "values": [
            "Windows 10",
            "Windows 7",
            "Mac OS X 10_12_6",
            "Android 7.0",
            "iOS 11.1.2",
            "unknown",
        ],
        "weights": [0.35, 0.15, 0.15, 0.15, 0.10, 0.10],
        "null_rate": 0.40,
    },
    "id_31": {
        "values": [
            "chrome 62.0",
            "chrome 63.0",
            "safari 11.0",
            "firefox 57.0",
            "edge 15.0",
            "ie 11.0",
            "unknown",
        ],
        "weights": [0.25, 0.25, 0.15, 0.10, 0.08, 0.07, 0.10],
        "null_rate": 0.40,
    },
    "id_33": {
        "values": [
            "1920x1080",
            "1366x768",
            "1440x900",
            "375x667",
            "360x640",
            "unknown",
        ],
        "weights": [0.30, 0.25, 0.15, 0.12, 0.10, 0.08],
        "null_rate": 0.40,
    },
    "id_34": {
        "values": [
            "match_status:0",
            "match_status:1",
            "match_status:2",
            "match_status:-1",
            "unknown",
        ],
        "weights": [0.30, 0.30, 0.15, 0.10, 0.15],
        "null_rate": 0.30,
    },
}

# id_35-id_38: T/F flags -- fraud correlates with more 'F'
ID_TF_FEATURES: List[str] = ["id_35", "id_36", "id_37", "id_38"]
ID_TF_LEGITIMATE_WEIGHTS: Tuple[float, float, float] = (
    0.70,
    0.20,
    0.10,
)  # T, F, unknown
ID_TF_FRAUD_WEIGHTS: Tuple[float, float, float] = (0.30, 0.55, 0.15)  # More F for fraud
ID_TF_NULL_RATE: float = 0.30

# Numeric id-features
ID_NUMERIC_FEATURES: Dict[str, Dict[str, Any]] = {
    "id_11": {"min": 72.0, "max": 400.0, "null_rate": 0.30},
    "id_13": {"min": 0.0, "max": 100.0, "null_rate": 0.30},
    "id_17": {"min": 0.0, "max": 500.0, "null_rate": 0.30},
    "id_19": {"min": 0.0, "max": 5000.0, "null_rate": 0.30},
    "id_20": {"min": 0.0, "max": 5000.0, "null_rate": 0.30},
}


# ---------------------------------------------------------------------------
# Device feature parameters
# ---------------------------------------------------------------------------

DEVICE_TYPE_DISTRIBUTION: Dict[str, float] = {
    "desktop": 0.60,
    "mobile": 0.35,
    "unknown": 0.05,
}
DEVICE_TYPE_FRAUD_DISTRIBUTION: Dict[str, float] = {
    "desktop": 0.45,
    "mobile": 0.50,  # Mobile slightly more fraud
    "unknown": 0.05,
}

DEVICE_INFO_DISTRIBUTION: Dict[str, float] = {
    "Windows": 0.35,
    "iOS Device": 0.20,
    "MacOS": 0.15,
    "Trident/7.0": 0.10,
    "rv:11.0": 0.05,
    "unknown": 0.15,
}
DEVICE_INFO_NULL_RATE: float = 0.60


# ---------------------------------------------------------------------------
# Derived amount feature bins
# ---------------------------------------------------------------------------
TRANSACTION_AMT_BIN_EDGES: List[float] = [0.0, 50.0, 100.0, 200.0, 500.0, float("inf")]


# ---------------------------------------------------------------------------
# Production defaults
# ---------------------------------------------------------------------------
DEFAULT_TARGET_TPS: int = 2000
DEFAULT_DURATION_SECONDS: int = 180
DEFAULT_USER_COUNT: int = 5000  # Increased for realistic per-user frequency at 2000 TPS (was 500)
DEFAULT_TOPIC_NAME: str = "synthetic-transactions"
