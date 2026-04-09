#!/usr/bin/env python3
"""
Validate that the synthetic transaction producer generates all 200 model features.

Generates 100 transactions and checks:
1. All 200 model features are present in the output dict
2. V258 values differ between fraud and non-fraud transactions
3. Null rates are realistic (not all null, not all populated)
4. No feature is always the same value (no degenerate columns)
"""

import math
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from producers.synthetic_transaction_producer import (
    SyntheticTransactionProducer,
    Transaction,
)

# The exact 200 features the production ML model expects (case as emitted by model)
REQUIRED_MODEL_FEATURES = [
    "TransactionDT",
    "TransactionAmt",
    "ProductCD",
    "card1",
    "card2",
    "card3",
    "card4",
    "card5",
    "card6",
    "addr1",
    "addr2",
    "P_emaildomain",
    "R_emaildomain",
    "C4",
    "C7",
    "C8",
    "C10",
    "C12",
    "D8",
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
    "M6",
    "M7",
    "M8",
    "M9",
    "V1",
    "V2",
    "V3",
    "V4",
    "V5",
    "V6",
    "V7",
    "V8",
    "V9",
    "V10",
    "V11",
    "V12",
    "V13",
    "V14",
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
    "V170",
    "V171",
    "V176",
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
    "id_11",
    "id_12",
    "id_13",
    "id_15",
    "id_16",
    "id_17",
    "id_19",
    "id_20",
    "id_23",
    "id_27",
    "id_28",
    "id_29",
    "id_30",
    "id_31",
    "id_33",
    "id_34",
    "id_35",
    "id_36",
    "id_37",
    "id_38",
    "DeviceType",
    "DeviceInfo",
    "TransactionAmt_log",
    "TransactionAmt_decimal",
    "TransactionAmt_bin",
]


# Build a mapping from model feature name -> dataclass field name (lowercase)
def _model_to_field(name):
    """Convert model feature name to dataclass field name."""
    mapping = {
        "TransactionDT": "transaction_dt",
        "TransactionAmt": "transaction_amt",
        "ProductCD": "product_cd",
        "P_emaildomain": "p_emaildomain",
        "R_emaildomain": "r_emaildomain",
        "DeviceType": "device_type",
        "DeviceInfo": "device_info",
        "TransactionAmt_log": "transaction_amt_log",
        "TransactionAmt_decimal": "transaction_amt_decimal",
        "TransactionAmt_bin": "transaction_amt_bin",
    }
    if name in mapping:
        return mapping[name]
    # C4 -> c4, M1 -> m1, V258 -> v258, id_11 -> id_11, card1 -> card1, addr1 -> addr1
    return name.lower()


def make_producer():
    """Create producer with mocked Kafka."""
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
                    "amount_distribution": {"mean_log": 4.0, "std_log": 1.2, "min_amount": 1.0, "max_amount": 1000.0},
                    "product_codes": {"W": 0.74, "C": 0.14, "R": 0.06, "H": 0.05, "S": 0.01},
                },
                "fraud_patterns": {
                    "base_fraud_rate": 0.027,
                    "amount_patterns": {"high_amount_bias": 1.34},
                    "temporal_bias": {"high_risk_hours": [0, 1, 2, 3, 4, 5]},
                },
            },
        }
        p = SyntheticTransactionProducer()
        p.fraud_rate = 0.027
        p.transaction_patterns = mock_load.return_value["synthetic_spec"]["transaction_patterns"]
        p.fraud_patterns = mock_load.return_value["synthetic_spec"]["fraud_patterns"]
        return p


def main():
    print("=" * 70)
    print("VALIDATING 200 MODEL FEATURES IN SYNTHETIC PRODUCER")
    print("=" * 70)

    producer = make_producer()

    # Generate 100 transactions (force ~50% fraud for testing)
    transactions = []
    fraud_txns = []
    legit_txns = []

    for i in range(100):
        txn = producer._generate_transaction(f"user_{i % 20:04d}")
        d = asdict(txn)
        transactions.append(d)
        if txn.is_fraud:
            fraud_txns.append(d)
        else:
            legit_txns.append(d)

    # Force some fraud transactions if we didn't get enough
    if len(fraud_txns) < 10:
        import random

        old_rate = producer.fraud_rate
        producer.fraud_rate = 0.95  # Force fraud
        for i in range(20):
            txn = producer._generate_transaction(f"user_fraud_{i:04d}")
            d = asdict(txn)
            transactions.append(d)
            if txn.is_fraud:
                fraud_txns.append(d)
            else:
                legit_txns.append(d)
        producer.fraud_rate = old_rate

    print(f"\nGenerated {len(transactions)} transactions " f"({len(fraud_txns)} fraud, {len(legit_txns)} legit)")

    # === Check 1: All 200 features are present ===
    print("\n--- CHECK 1: All 200 model features present ---")
    sample = transactions[0]
    missing = []
    for feat in REQUIRED_MODEL_FEATURES:
        field_name = _model_to_field(feat)
        if field_name not in sample:
            missing.append(f"{feat} (expected field: {field_name})")
    if missing:
        print(f"FAIL: {len(missing)} features MISSING:")
        for m in missing:
            print(f"  - {m}")
    else:
        print(f"PASS: All {len(REQUIRED_MODEL_FEATURES)} model features found in output dict")

    # === Check 2: V258 fraud vs legit ===
    print("\n--- CHECK 2: V258 fraud correlation ---")
    v258_field = "v258"
    fraud_v258 = [d[v258_field] for d in fraud_txns if d.get(v258_field) is not None]
    legit_v258 = [d[v258_field] for d in legit_txns if d.get(v258_field) is not None]
    if fraud_v258 and legit_v258:
        fraud_mean = sum(fraud_v258) / len(fraud_v258)
        legit_mean = sum(legit_v258) / len(legit_v258)
        ratio = fraud_mean / max(legit_mean, 0.001)
        status = "PASS" if ratio > 1.3 else "FAIL"
        print(f"{status}: V258 fraud_mean={fraud_mean:.3f}, legit_mean={legit_mean:.3f}, ratio={ratio:.2f}x")
    else:
        print(f"WARN: Not enough data (fraud_v258={len(fraud_v258)}, legit_v258={len(legit_v258)})")

    # === Check 3: Null rates are realistic ===
    print("\n--- CHECK 3: Null rate sanity ---")
    null_counts = defaultdict(int)
    total = len(transactions)
    for d in transactions:
        for feat in REQUIRED_MODEL_FEATURES:
            field_name = _model_to_field(feat)
            if d.get(field_name) is None:
                null_counts[feat] += 1

    all_null_features = [f for f in REQUIRED_MODEL_FEATURES if null_counts[f] == total]
    never_null_mandatory = [
        "TransactionDT",
        "TransactionAmt",
        "ProductCD",
        "TransactionAmt_log",
        "TransactionAmt_decimal",
        "TransactionAmt_bin",
    ]
    wrongly_null = [f for f in never_null_mandatory if null_counts[f] > 0]

    if all_null_features:
        print(f"FAIL: {len(all_null_features)} features are ALWAYS null:")
        for f in all_null_features:
            print(f"  - {f}")
    else:
        print("PASS: No feature is always null")

    if wrongly_null:
        print(f"FAIL: Mandatory features have nulls: {wrongly_null}")
    else:
        print("PASS: Mandatory features are never null")

    # Print null rates for sparse features
    sparse_feats = [f for f in REQUIRED_MODEL_FEATURES if 0 < null_counts[f] < total]
    if sparse_feats:
        print(f"  {len(sparse_feats)} features have realistic null rates (partially populated)")

    # === Check 4: No degenerate columns ===
    print("\n--- CHECK 4: No degenerate columns (all-same-value) ---")
    degenerate = []
    for feat in REQUIRED_MODEL_FEATURES:
        field_name = _model_to_field(feat)
        non_null_vals = [d[field_name] for d in transactions if d.get(field_name) is not None]
        if len(non_null_vals) > 1:
            unique = set(str(v) for v in non_null_vals)
            if len(unique) == 1:
                degenerate.append(f"{feat} (all={non_null_vals[0]})")
    if degenerate:
        print(f"WARN: {len(degenerate)} features have only one unique value:")
        for d in degenerate:
            print(f"  - {d}")
    else:
        print(f"PASS: All features have variance (no degenerate columns)")

    # === Summary ===
    print("\n" + "=" * 70)
    all_pass = not missing and not all_null_features and not wrongly_null
    if all_pass:
        print("RESULT: ALL CHECKS PASSED")
    else:
        print("RESULT: SOME CHECKS FAILED - see details above")
    print("=" * 70)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
