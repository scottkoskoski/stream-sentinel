#!/usr/bin/env python3
"""
Synthetic Data Validation Script

Generates synthetic transactions without Kafka, compares distributions
against IEEE-CIS analysis, and checks feature compatibility with the
production model.
"""

import sys
import os
import json
import pickle
import importlib.util
import warnings

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# Setup paths
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# Load gen_config
_spec = importlib.util.spec_from_file_location(
    "gen_config", os.path.join(ROOT, "src/producers/config.py")
)
gen_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_config)

# Load IEEE-CIS analysis
with open(os.path.join(ROOT, "data/processed/ieee_cis_analysis.json")) as f:
    ieee_analysis = json.load(f)

ieee = ieee_analysis["analysis_results"]
ieee_spec = ieee["synthetic_spec"]

# Load production model metadata
model_data = pickle.load(
    open(os.path.join(ROOT, "models/ieee_fraud_model_production.pkl"), "rb")
)
model_feature_names = model_data["feature_names"]


# ---------------------------------------------------------------------------
# Generate synthetic transactions without Kafka
# ---------------------------------------------------------------------------
def generate_transactions(n=2000):
    """Generate n transactions by importing the producer class and calling
    _generate_transaction directly (no Kafka needed)."""
    # Monkey-patch confluent_kafka so import doesn't fail
    import types

    fake_kafka = types.ModuleType("confluent_kafka")
    fake_kafka.Producer = lambda *a, **kw: None

    class FakeAdmin:
        pass

    fake_admin = types.ModuleType("confluent_kafka.admin")
    fake_admin.AdminClient = FakeAdmin
    fake_admin.NewTopic = FakeAdmin
    sys.modules["confluent_kafka"] = fake_kafka
    sys.modules["confluent_kafka.admin"] = fake_admin

    # Also mock the kafka config module
    kafka_config_mod = types.ModuleType("config")

    class FakeKafkaConfig:
        bootstrap_servers = "localhost:9092"

        def get_producer_config(self, *a):
            return {}

        def get_topic_config(self, *a):
            return {
                "num_partitions": 1,
                "replication_factor": 1,
                "cleanup_policy": "delete",
                "retention_ms": 86400000,
                "compression_type": "lz4",
            }

    kafka_config_mod.get_kafka_config = lambda: FakeKafkaConfig()
    sys.modules["config"] = kafka_config_mod

    spec = importlib.util.spec_from_file_location(
        "synth_producer",
        os.path.join(ROOT, "src/producers/synthetic_transaction_producer.py"),
    )
    mod = importlib.util.module_from_spec(spec)

    # Suppress Kafka init by patching Producer
    import logging

    logging.getLogger("synthetic_producer").setLevel(logging.WARNING)
    spec.loader.exec_module(mod)

    # Create producer instance -- it will fail on Kafka connect but we only
    # need _generate_transaction
    producer = object.__new__(mod.SyntheticTransactionProducer)
    producer.logger = logging.getLogger("synth_producer")
    producer.logger.setLevel(logging.WARNING)
    producer.transaction_counter = 0
    producer.user_profiles = {}
    producer.entity_tracking = {
        "card_addresses": {},
        "address_cards": {},
        "email_transactions": {},
        "user_merchants": {},
        "card_emails": {},
        "email_addresses": {},
        "device_transactions": {},
        "card_firstseen": {},
        "user_cards": {},
        "user_created": {},
        "user_lasttxn": {},
        "card_firstuse": {},
        "device_lasttxn": {},
        "user_lastfraud": {},
        "email_lasttxn": {},
        "merchant_firstuse": {},
        "address_firstseen": {},
    }
    producer.stats = {
        "total_produced": 0,
        "fraud_produced": 0,
        "legitimate_produced": 0,
        "production_rate": 0.0,
        "errors": 0,
    }

    # Load analysis data
    producer.analysis_data = ieee
    producer.fraud_rate = ieee["schema"]["fraud_rate"]
    producer.transaction_patterns = ieee_spec["transaction_patterns"]
    producer.fraud_patterns = ieee_spec["fraud_patterns"]

    # Generate transactions using a pool of ~100 users
    import random
    from dataclasses import asdict

    records = []
    user_ids = [f"user_{i:06d}" for i in range(100)]
    for i in range(n):
        producer.transaction_counter = i
        uid = random.choice(user_ids)
        txn = producer._generate_transaction(user_id=uid)
        records.append(asdict(txn))

    return pd.DataFrame(records)


print("Generating 2000 synthetic transactions...")
df = generate_transactions(2000)
print(f"Generated {len(df)} transactions, {df['is_fraud'].sum()} fraudulent ({df['is_fraud'].mean()*100:.2f}%)")

# ---------------------------------------------------------------------------
# REPORT BUILDER
# ---------------------------------------------------------------------------
report_lines = []


def section(title):
    report_lines.append(f"\n## {title}\n")


def table(headers, rows):
    report_lines.append("| " + " | ".join(headers) + " |")
    report_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        report_lines.append("| " + " | ".join(str(c) for c in row) + " |")
    report_lines.append("")


report_lines.append("# Synthetic Data Validation Report\n")
report_lines.append(f"Generated: {pd.Timestamp.now().isoformat()}")
report_lines.append(f"Sample size: {len(df)} transactions")
report_lines.append(f"Fraud count: {df['is_fraud'].sum()} ({df['is_fraud'].mean()*100:.2f}%)")

# ===== 1. Transaction Amount =====
section("1. TransactionAmt Distribution")

ieee_amt = ieee_spec["feature_distributions"]["TransactionAmt"]
syn_amt = df["transaction_amt"]

rows = []
for stat, ieee_val, syn_val in [
    ("Mean", ieee_amt["mean"], syn_amt.mean()),
    ("Std", ieee_amt["std"], syn_amt.std()),
    ("Min", ieee_amt["min"], syn_amt.min()),
    ("Q25", ieee_amt["q25"], syn_amt.quantile(0.25)),
    ("Median", ieee_amt["median"], syn_amt.median()),
    ("Q75", ieee_amt["q75"], syn_amt.quantile(0.75)),
    ("Max", ieee_amt["max"], syn_amt.max()),
]:
    pct_diff = abs(syn_val - ieee_val) / max(ieee_val, 0.01) * 100
    rows.append(
        [stat, f"{ieee_val:.2f}", f"{syn_val:.2f}", f"{pct_diff:.1f}%"]
    )

table(["Statistic", "IEEE-CIS", "Synthetic", "% Diff"], rows)

# KS test
ks_stat, ks_p = stats.kstest(
    syn_amt,
    "lognorm",
    args=(ieee_amt["std"], 0, np.exp(np.log(ieee_amt["mean"]))),
)
report_lines.append(f"Note: Synthetic amounts are capped at {gen_config.AMOUNT_DISTRIBUTION['max_amount']} while IEEE-CIS max is {ieee_amt['max']:.2f}.")
report_lines.append(f"The log-normal parameters (mean_log={gen_config.AMOUNT_DISTRIBUTION['mean_log']}, std_log={gen_config.AMOUNT_DISTRIBUTION['std_log']}) produce the synthetic distribution.")

# ===== 2. Fraud Rate =====
section("2. Fraud Rate Analysis")

report_lines.append(f"**Target fraud rate (IEEE-CIS):** {ieee['schema']['fraud_rate']*100:.2f}%")
report_lines.append(f"**Observed synthetic fraud rate:** {df['is_fraud'].mean()*100:.2f}%")

# Hourly fraud rate comparison
report_lines.append("\n### Hourly Fraud Rate Comparison\n")
# The producer uses current time, so hours cluster. Instead report config multipliers.
ieee_hourly = ieee["fraud_patterns"]["temporal_patterns"]["hourly_patterns"]
rows = []
for h in range(24):
    ieee_rate = ieee_hourly[str(h)]["fraud_rate"]
    config_mult = gen_config.TEMPORAL_FRAUD_MULTIPLIERS.get(h, 1.0)
    effective = gen_config.BASE_FRAUD_RATE * config_mult
    rows.append([str(h), f"{ieee_rate*100:.2f}%", f"{config_mult:.1f}x", f"{effective*100:.2f}%"])

table(["Hour", "IEEE-CIS Rate", "Config Multiplier", "Effective Rate"], rows)

# Check if peak hours match
ieee_peak = ieee_spec["fraud_patterns"]["temporal_bias"]["high_risk_hours"]
config_peak = gen_config.PEAK_FRAUD_HOURS
report_lines.append(f"IEEE-CIS high-risk hours: {ieee_peak}")
report_lines.append(f"Config PEAK_FRAUD_HOURS: {config_peak}")
if set(config_peak) != set(ieee_peak):
    report_lines.append(f"**MISMATCH**: Config peak hours {config_peak} differ from IEEE-CIS {ieee_peak}. IEEE includes hours 0,1,5,22,23 as high-risk.")

# ===== 3. Card Features =====
section("3. Card Feature Distributions")

report_lines.append("### card1 (Primary Card ID)")
ieee_card1 = ieee_spec["feature_distributions"]["card1"]
syn_card1 = df["card1"].dropna()
rows = [
    ["Mean", f"{ieee_card1['mean']:.0f}", f"{syn_card1.mean():.0f}"],
    ["Std", f"{ieee_card1['std']:.0f}", f"{syn_card1.std():.0f}"],
    ["Min", f"{ieee_card1['min']:.0f}", f"{syn_card1.min():.0f}"],
    ["Max", f"{ieee_card1['max']:.0f}", f"{syn_card1.max():.0f}"],
]
table(["Stat", "IEEE-CIS", "Synthetic"], rows)

report_lines.append("### card4 (Card Network)")
card4_counts = df["card4"].value_counts(normalize=True)
rows = []
for net, ieee_pct in gen_config.CARD4_DISTRIBUTION.items():
    syn_pct = card4_counts.get(net, 0)
    rows.append([net, f"{ieee_pct*100:.1f}%", f"{syn_pct*100:.1f}%"])
table(["Network", "IEEE-CIS", "Synthetic"], rows)

report_lines.append("### card6 (Card Type)")
card6_counts = df["card6"].value_counts(normalize=True)
rows = []
for ct, ieee_pct in gen_config.CARD6_DISTRIBUTION.items():
    syn_pct = card6_counts.get(ct, 0)
    rows.append([ct, f"{ieee_pct*100:.1f}%", f"{syn_pct*100:.1f}%"])
table(["Type", "IEEE-CIS", "Synthetic"], rows)

# ===== 4. C-Features =====
section("4. C-Feature (Counting) Analysis")

ieee_c = ieee_spec.get("c_feature_statistics", {})
rows = []
for i in range(1, 15):
    col = f"c{i}"
    col_upper = f"C{i}"
    null_rate_actual = df[col].isna().mean()
    null_rate_config = gen_config.C_FEATURE_NULL_RATES.get(col, 0)
    null_rate_ieee = ieee["schema"]["missing_patterns"].get(col_upper, "N/A")

    if col_upper in ieee_c:
        ieee_mean = ieee_c[col_upper]["mean"]
        syn_mean = df[col].dropna().mean() if df[col].notna().any() else 0
        rows.append([col_upper,
                     f"{null_rate_ieee}" if isinstance(null_rate_ieee, str) else f"{null_rate_ieee:.2f}",
                     f"{null_rate_config:.2f}",
                     f"{null_rate_actual:.2f}",
                     f"{ieee_mean:.2f}",
                     f"{syn_mean:.2f}"])
    else:
        rows.append([col_upper,
                     f"{null_rate_ieee}" if isinstance(null_rate_ieee, str) else f"{null_rate_ieee:.2f}",
                     f"{null_rate_config:.2f}",
                     f"{null_rate_actual:.2f}",
                     "N/A", "N/A"])

table(["Feature", "IEEE Null%", "Config Null%", "Actual Null%", "IEEE Mean", "Synth Mean"], rows)

# Major mismatch: IEEE-CIS has 0% null for C1-C5,C9,C12-C14 but config has non-zero
report_lines.append("\n**Key Finding:** IEEE-CIS has 0% null rate for C1-C5, C9, C12-C14, but `config.py` uses non-zero null rates (2-25%). This is a distribution mismatch.")

# ===== 5. D-Features =====
section("5. D-Feature (Time Delta) Analysis")

ieee_d = ieee_spec.get("d_feature_statistics", {})
rows = []
for i in range(1, 16):
    col = f"d{i}"
    col_upper = f"D{i}"
    null_rate_actual = df[col].isna().mean()
    null_rate_config = gen_config.D_FEATURE_NULL_RATES.get(col, 0)
    null_rate_ieee = ieee["schema"]["missing_patterns"].get(col_upper, "N/A")

    if col_upper in ieee_d:
        ieee_mean = ieee_d[col_upper]["mean"]
        syn_mean = df[col].dropna().mean() if df[col].notna().any() else 0
        rows.append([col_upper,
                     f"{null_rate_ieee}" if isinstance(null_rate_ieee, str) else f"{null_rate_ieee:.3f}",
                     f"{null_rate_config:.3f}",
                     f"{null_rate_actual:.3f}",
                     f"{ieee_mean:.1f}",
                     f"{syn_mean:.1f}"])
    else:
        rows.append([col_upper,
                     f"{null_rate_ieee}" if isinstance(null_rate_ieee, str) else f"{null_rate_ieee:.3f}",
                     f"{null_rate_config:.3f}",
                     f"{null_rate_actual:.3f}",
                     "N/A", "N/A"])

table(["Feature", "IEEE Null%", "Config Null%", "Actual Null%", "IEEE Mean", "Synth Mean"], rows)

# ===== 6. M-Features =====
section("6. M-Feature (Match) Analysis")

rows = []
for i in range(1, 10):
    col = f"m{i}"
    col_upper = f"M{i}"
    null_rate_actual = df[col].isna().mean()
    null_rate_config = gen_config.M_FEATURE_NULL_RATES.get(col, 0)
    null_rate_ieee = ieee["schema"]["missing_patterns"].get(col_upper, 0)

    # Value distribution among non-null
    non_null = df[col].dropna()
    t_pct = (non_null == "T").mean() if len(non_null) > 0 else 0
    f_pct = (non_null == "F").mean() if len(non_null) > 0 else 0
    nf_pct = (non_null == "NotFound").mean() if len(non_null) > 0 else 0

    rows.append([col_upper,
                 f"{null_rate_ieee:.3f}",
                 f"{null_rate_config:.3f}",
                 f"{null_rate_actual:.3f}",
                 f"T:{t_pct:.0%} F:{f_pct:.0%} NF:{nf_pct:.0%}"])

table(["Feature", "IEEE Null%", "Config Null%", "Actual Null%", "Value Dist (non-null)"], rows)

# ===== 7. Feature Compatibility with Model =====
section("7. Feature Compatibility with Production Model")

# Map synthetic field names to model feature names
synth_fields = set(df.columns)
model_features = set(model_feature_names)

# Build mapping from synthetic -> model naming
synth_to_model = {}
for col in df.columns:
    # Direct uppercase match
    upper = col.upper() if col.startswith(('c', 'd', 'm')) and col[1:].isdigit() else col
    # Special cases
    mapping = {
        "transaction_dt": "TransactionDT",
        "transaction_amt": "TransactionAmt",
        "product_cd": "ProductCD",
        "p_emaildomain": "P_emaildomain",
        "r_emaildomain": "R_emaildomain",
    }
    if col in mapping:
        synth_to_model[col] = mapping[col]
    elif col.startswith("c") and col[1:].isdigit():
        synth_to_model[col] = col.upper()
    elif col.startswith("d") and col[1:].isdigit():
        synth_to_model[col] = col.upper()
    elif col.startswith("m") and col[1:].isdigit():
        synth_to_model[col] = col.upper()
    elif col.startswith("card") or col.startswith("addr") or col.startswith("dist"):
        synth_to_model[col] = col

synth_model_names = set(synth_to_model.values())

# Features model expects that synthetic provides
provided = model_features & synth_model_names
# Features model expects that synthetic DOES NOT provide
missing = model_features - synth_model_names
# Extra features synthetic generates but model doesn't use
extra = synth_model_names - model_features

report_lines.append(f"**Model expects:** {len(model_features)} features")
report_lines.append(f"**Synthetic provides (mapped):** {len(provided)} features")
report_lines.append(f"**Missing from synthetic:** {len(missing)} features")
report_lines.append(f"**Extra in synthetic (unused by model):** {len(extra)} features")

# Categorize missing features
v_features = sorted([f for f in missing if f.startswith("V")])
id_features = sorted([f for f in missing if f.startswith("id_") or f in ("DeviceType", "DeviceInfo")])
amt_derived = sorted([f for f in missing if f.startswith("TransactionAmt_")])
c_missing = sorted([f for f in missing if f.startswith("C")])
d_missing = sorted([f for f in missing if f.startswith("D")])
other_missing = sorted(missing - set(v_features) - set(id_features) - set(amt_derived) - set(c_missing) - set(d_missing))

report_lines.append(f"\n### Missing Feature Categories\n")
table(["Category", "Count", "Examples"], [
    ["V-features (Vesta)", len(v_features), ", ".join(v_features[:10]) + ("..." if len(v_features) > 10 else "")],
    ["id-features (Identity)", len(id_features), ", ".join(id_features[:10])],
    ["TransactionAmt derived", len(amt_derived), ", ".join(amt_derived)],
    ["C-features", len(c_missing), ", ".join(c_missing)],
    ["D-features", len(d_missing), ", ".join(d_missing)],
    ["Other", len(other_missing), ", ".join(other_missing)],
])

# C/D features the model uses vs what synthetic generates
model_c = sorted([f for f in model_features if f.startswith("C") and f[1:].isdigit()])
model_d = sorted([f for f in model_features if f.startswith("D") and f[1:].isdigit()])
synth_c = sorted([f"C{i}" for i in range(1,15)])
synth_d = sorted([f"D{i}" for i in range(1,16)])

report_lines.append(f"\n### C-Feature Coverage")
report_lines.append(f"Model uses: {model_c}")
report_lines.append(f"Synthetic generates: {synth_c}")
report_lines.append(f"Model needs but synthetic has: {sorted(set(model_c) & set(synth_c))}")
report_lines.append(f"Model needs but synthetic lacks: {sorted(set(model_c) - set(synth_c))}")

report_lines.append(f"\n### D-Feature Coverage")
report_lines.append(f"Model uses: {model_d}")
report_lines.append(f"Synthetic generates: {synth_d}")
report_lines.append(f"Model needs but synthetic has: {sorted(set(model_d) & set(synth_d))}")
report_lines.append(f"Model needs but synthetic lacks: {sorted(set(model_d) - set(synth_d))}")

# ===== 8. Amount Distribution Issues =====
section("8. Specific Distribution Issues")

report_lines.append("### Amount Capping")
report_lines.append(f"- Config max_amount: {gen_config.AMOUNT_DISTRIBUTION['max_amount']}")
report_lines.append(f"- IEEE-CIS spec max_amount: {ieee_spec['transaction_patterns']['amount_distribution']['max_amount']}")
report_lines.append(f"- IEEE-CIS actual max: {ieee_amt['max']}")
report_lines.append(f"- Synthetic max observed: {syn_amt.max():.2f}")
if gen_config.AMOUNT_DISTRIBUTION['max_amount'] < ieee_spec['transaction_patterns']['amount_distribution']['max_amount']:
    report_lines.append(f"- **ISSUE**: Config caps at {gen_config.AMOUNT_DISTRIBUTION['max_amount']} but IEEE spec says {ieee_spec['transaction_patterns']['amount_distribution']['max_amount']}.")

report_lines.append("\n### Amount Min")
report_lines.append(f"- Config min_amount: {gen_config.AMOUNT_DISTRIBUTION['min_amount']}")
report_lines.append(f"- IEEE-CIS spec min_amount: {ieee_spec['transaction_patterns']['amount_distribution']['min_amount']}")
if gen_config.AMOUNT_DISTRIBUTION['min_amount'] != ieee_spec['transaction_patterns']['amount_distribution']['min_amount']:
    report_lines.append(f"- **ISSUE**: Config min is {gen_config.AMOUNT_DISTRIBUTION['min_amount']} but IEEE spec says {ieee_spec['transaction_patterns']['amount_distribution']['min_amount']}.")

report_lines.append("\n### Fraud Amount Bias")
report_lines.append(f"- Config FRAUD_AMOUNT_BIAS: {gen_config.FRAUD_AMOUNT_BIAS}")
report_lines.append(f"- IEEE spec high_amount_bias: {ieee_spec['fraud_patterns']['amount_patterns']['high_amount_bias']}")
if abs(gen_config.FRAUD_AMOUNT_BIAS - ieee_spec['fraud_patterns']['amount_patterns']['high_amount_bias']) > 0.01:
    report_lines.append(f"- **ISSUE**: Config uses {gen_config.FRAUD_AMOUNT_BIAS} but IEEE spec says {ieee_spec['fraud_patterns']['amount_patterns']['high_amount_bias']}.")

# ===== 9. C-Feature Null Rate Mismatches =====
section("9. C-Feature Null Rate Mismatches")

report_lines.append("The IEEE-CIS dataset has 0% null for many C-features, but `config.py` applies artificial null rates:")
rows = []
for i in range(1, 15):
    col_upper = f"C{i}"
    ieee_null = ieee["schema"]["missing_patterns"].get(col_upper, 0)
    config_null = gen_config.C_FEATURE_NULL_RATES.get(f"c{i}", 0)
    if abs(ieee_null - config_null) > 0.01:
        rows.append([col_upper, f"{ieee_null:.2f}", f"{config_null:.2f}", f"{abs(ieee_null - config_null):.2f}"])

if rows:
    table(["Feature", "IEEE Null%", "Config Null%", "Abs Diff"], rows)
    report_lines.append("**Recommendation:** Align C-feature null rates with IEEE-CIS values.")

# ===== 10. Generation Pacing =====
section("10. Generation Pacing Assessment")

report_lines.append(f"- **DEFAULT_TARGET_TPS:** {gen_config.DEFAULT_TARGET_TPS}")
report_lines.append(f"- **DEFAULT_DURATION_SECONDS:** {gen_config.DEFAULT_DURATION_SECONDS}")
report_lines.append(f"- **DEFAULT_USER_COUNT:** {gen_config.DEFAULT_USER_COUNT}")
report_lines.append(f"- Total transactions per run: ~{gen_config.DEFAULT_TARGET_TPS * gen_config.DEFAULT_DURATION_SECONDS:,}")
report_lines.append("")
report_lines.append("### Assessment")
report_lines.append("- The IEEE-CIS dataset has 590,540 transactions over ~182 days, averaging ~3,245 transactions/day (~0.04 TPS).")
report_lines.append(f"- The default TPS of {gen_config.DEFAULT_TARGET_TPS} is a load-testing config, not a realistic production rate.")
report_lines.append(f"- For a large payment processor, 2000 TPS is realistic peak volume.")
report_lines.append(f"- The 500 user pool is small for 2000 TPS -- this means ~4 TPS per user, which is unrealistically high.")
report_lines.append(f"- **Recommendation:** Increase DEFAULT_USER_COUNT to 5000+ for more realistic per-user transaction frequency.")

# ===== 11. Summary of Issues =====
section("11. Summary of Issues and Recommendations")

issues = [
    ("CRITICAL", "Missing 149 V-features", "The model expects 149 V-features (Vesta engineered features) that the synthetic producer does not generate. These are needed for model inference."),
    ("CRITICAL", "Missing identity features", f"The model expects {len(id_features)} identity features ({', '.join(id_features[:5])}...) not generated by the producer."),
    ("CRITICAL", "Missing TransactionAmt derived features", f"Model expects {', '.join(amt_derived)} which are not generated."),
    ("HIGH", "Amount max_amount mismatch", f"Config caps at {gen_config.AMOUNT_DISTRIBUTION['max_amount']} vs IEEE spec {ieee_spec['transaction_patterns']['amount_distribution']['max_amount']}."),
    ("HIGH", "Amount min_amount mismatch", f"Config min is {gen_config.AMOUNT_DISTRIBUTION['min_amount']} vs IEEE spec {ieee_spec['transaction_patterns']['amount_distribution']['min_amount']}."),
    ("HIGH", "Fraud amount bias mismatch", f"Config {gen_config.FRAUD_AMOUNT_BIAS} vs IEEE {ieee_spec['fraud_patterns']['amount_patterns']['high_amount_bias']}."),
    ("MEDIUM", "C-feature null rate mismatches", "C1-C5, C9, C12-C14 have 0% null in IEEE but 2-25% in config."),
    ("MEDIUM", "Peak fraud hours mismatch", f"Config uses {gen_config.PEAK_FRAUD_HOURS} but IEEE high-risk hours include {ieee_peak}."),
    ("LOW", "User count too small", f"500 users at 2000 TPS = ~4 TPS/user, unrealistically high."),
]

rows = []
for sev, issue, detail in issues:
    rows.append([sev, issue, detail[:100] + ("..." if len(detail) > 100 else "")])

table(["Severity", "Issue", "Detail"], rows)

# Write report
report_path = os.path.join(ROOT, "data/SYNTHETIC_DATA_VALIDATION.md")
with open(report_path, "w") as f:
    f.write("\n".join(report_lines))

print(f"\nReport written to {report_path}")
print(f"\nKey findings:")
print(f"  - Model expects {len(model_features)} features, synthetic provides {len(provided)}")
print(f"  - Missing: {len(missing)} features ({len(v_features)} V-features, {len(id_features)} id-features, {len(amt_derived)} amt-derived)")
print(f"  - Amount config mismatches: max={gen_config.AMOUNT_DISTRIBUTION['max_amount']} vs {ieee_spec['transaction_patterns']['amount_distribution']['max_amount']}, min={gen_config.AMOUNT_DISTRIBUTION['min_amount']} vs {ieee_spec['transaction_patterns']['amount_distribution']['min_amount']}")
print(f"  - Fraud bias: {gen_config.FRAUD_AMOUNT_BIAS} vs {ieee_spec['fraud_patterns']['amount_patterns']['high_amount_bias']}")
