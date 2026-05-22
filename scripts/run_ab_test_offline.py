#!/usr/bin/env python3
# /stream-sentinel/scripts/run_ab_test_offline.py

"""
Offline A/B test harness.

Replays a labelled synthetic transaction stream through two scoring variants
and emits a rigorous report (two-proportion z-tests with cluster correction,
Newcombe CIs, Holm-corrected secondaries, ship/no-ship decision).

Why offline first
-----------------
The live ABTestManager couples assignment, scoring, persistence, and analysis
into one process. We want to validate the *statistical* layer in isolation so
that when we wire it into the live system we know any anomalies are coming
from infra or data, not the math.

Variants
--------
- A/A guardrail: both arms use ``--threshold-control`` -- the test should fail
  to reject the null. If it ships, the framework is broken.
- A/B: control = ``--threshold-control`` (default 0.3), treatment =
  ``--threshold-treatment`` (default 0.5). Same scorer, different decision
  rule -- this is the cheapest defensible first real test.

User assignment uses the same MD5 hash scheme as the live ABTestManager so the
sampling distribution mirrors production.

Usage
-----
    python scripts/run_ab_test_offline.py \
        --num-users 5000 --txns-per-user 30 \
        --threshold-control 0.3 --threshold-treatment 0.5 \
        --report-path reports/ab_test_offline.json

The script writes a JSON report and prints a human-readable summary.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Import the analysis module directly to avoid pulling in the online_learning
# package __init__ (which imports kafka/redis/joblib heavy deps not needed
# offline).
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ANALYSIS_PATH = _PROJECT_ROOT / "src" / "ml" / "online_learning" / "ab_test_analysis.py"
_spec = importlib.util.spec_from_file_location("ab_test_analysis", _ANALYSIS_PATH)
ab_test_analysis = importlib.util.module_from_spec(_spec)
sys.modules["ab_test_analysis"] = ab_test_analysis
_spec.loader.exec_module(ab_test_analysis)

AnalysisSpec = ab_test_analysis.AnalysisSpec
VariantCounters = ab_test_analysis.VariantCounters
analyze = ab_test_analysis.analyze
required_sample_size = ab_test_analysis.required_sample_size


# ---------------------------------------------------------------------------
# Synthetic data generation (lightweight; mirrors the system's fraud rates)
# ---------------------------------------------------------------------------


@dataclass
class SyntheticUser:
    user_id: str
    avg_amount: float
    fraud_propensity: float  # 0..1 multiplier on base fraud rate
    daily_freq: int          # approximate baseline transactions per day
    is_new: bool             # for the new-user segment cut


def _gen_user(rng: random.Random, idx: int) -> SyntheticUser:
    return SyntheticUser(
        user_id=f"user_{idx:06d}",
        avg_amount=rng.lognormvariate(mu=3.5, sigma=0.8),       # ~$35 median
        fraud_propensity=rng.betavariate(1.5, 30.0),            # mostly low
        daily_freq=max(1, int(rng.gauss(4, 2))),
        is_new=(rng.random() < 0.15),                           # 15% new users
    )


def _generate_transaction(
    rng: random.Random,
    user: SyntheticUser,
    base_time: datetime,
    base_fraud_rate: float,
) -> Dict[str, Any]:
    """Generate one labelled transaction for ``user``."""
    hours_offset = rng.uniform(0, 24)
    ts = base_time + timedelta(hours=hours_offset)
    hour = ts.hour
    is_night = hour < 6 or hour > 22

    # Compose fraud probability
    p = base_fraud_rate * (1.0 + user.fraud_propensity * 4.0)
    if is_night:
        p *= 2.0
    if user.is_new:
        p *= 1.6   # fraudsters target new accounts
    p = min(p, 0.5)
    is_fraud = rng.random() < p

    # Amount: legitimate ~ user avg with noise; fraud sometimes high-value
    if is_fraud and rng.random() < 0.35:
        amount = rng.uniform(500.0, 4000.0)        # high-value fraud tail
    elif is_fraud:
        amount = max(1.0, user.avg_amount * rng.uniform(0.5, 6.0))
    else:
        amount = max(1.0, user.avg_amount * rng.uniform(0.6, 2.5))

    return {
        "transaction_id": f"{user.user_id}_{int(ts.timestamp()*1000)}",
        "user_id": user.user_id,
        "amount": round(amount, 2),
        "timestamp": ts.isoformat(),
        "hour": hour,
        "is_night": is_night,
        "is_fraud": int(is_fraud),
        "is_new_user": int(user.is_new),
        "amount_vs_avg_ratio": amount / max(1.0, user.avg_amount),
    }


# ---------------------------------------------------------------------------
# Scorer -- mirrors the production rule-based scorer
# (see src/consumers/fraud_detector.py:_calculate_fraud_score)
# ---------------------------------------------------------------------------


def rule_based_score(
    amount_vs_avg_ratio: float,
    amount: float,
    is_night: bool,
    velocity_score: float,
    daily_count: int,
    is_rapid: bool,
) -> float:
    score = 0.0
    if amount_vs_avg_ratio > 5.0:
        score += 0.3
    elif amount_vs_avg_ratio > 3.0:
        score += 0.2
    elif amount_vs_avg_ratio > 2.0:
        score += 0.1

    if amount > 1000.0:
        score += 0.2
    if is_night:
        score += 0.15
    if is_rapid:
        score += 0.25
    if velocity_score > 10:
        score += 0.2
    elif velocity_score > 5:
        score += 0.1
    if daily_count > 50:
        score += 0.15
    elif daily_count > 25:
        score += 0.1

    return min(score, 1.0)


# ---------------------------------------------------------------------------
# Variant assignment (matches ABTestManager._assign_user_to_variant)
# ---------------------------------------------------------------------------


def assign_variant(user_id: str, experiment_id: str, control_alloc: float) -> str:
    h = hashlib.md5(f"{user_id}:{experiment_id}".encode()).hexdigest()
    ratio = (int(h, 16) % 10000) / 10000.0
    return "control" if ratio <= control_alloc else "treatment"


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


@dataclass
class ReplayResult:
    control: VariantCounters
    treatment: VariantCounters
    n_total: int
    n_fraud: int
    elapsed_s: float


def replay(
    users: List[SyntheticUser],
    txns_per_user: int,
    base_fraud_rate: float,
    threshold_control: float,
    threshold_treatment: float,
    experiment_id: str,
    seed: int,
) -> ReplayResult:
    """
    Run the synthetic stream through both variants.

    Each user is hash-assigned to one variant, but we *compute the score once*
    per transaction and apply the variant-specific threshold. This matches the
    cleanest threshold-A/B design: the model behaviour is identical and we are
    measuring only the effect of the decision threshold.
    """
    rng = random.Random(seed)
    base_time = datetime(2026, 5, 22, 0, 0, 0)

    control = VariantCounters()
    treatment = VariantCounters()
    n_total = 0
    n_fraud = 0
    start = time.time()

    for user in users:
        variant_label = assign_variant(user.user_id, experiment_id, control_alloc=0.5)
        bucket = control if variant_label == "control" else treatment
        threshold = threshold_control if variant_label == "control" else threshold_treatment

        # Running per-user state for velocity / rapid-transaction features
        daily_count = 0
        last_ts: Optional[datetime] = None

        for _ in range(txns_per_user):
            txn = _generate_transaction(rng, user, base_time, base_fraud_rate)
            n_total += 1
            n_fraud += txn["is_fraud"]

            ts = datetime.fromisoformat(txn["timestamp"])
            time_since_last = (ts - last_ts).total_seconds() if last_ts else 1e9
            is_rapid = time_since_last < 300
            velocity = daily_count / 24.0
            daily_count += 1
            last_ts = ts

            score = rule_based_score(
                amount_vs_avg_ratio=txn["amount_vs_avg_ratio"],
                amount=txn["amount"],
                is_night=txn["is_night"],
                velocity_score=velocity,
                daily_count=daily_count,
                is_rapid=is_rapid,
            )
            predicted_positive = score >= threshold
            actual_positive = bool(txn["is_fraud"])

            # Build stratum tags (high_value, new_user). A transaction may
            # land in multiple strata; record() recurses into each.
            tags: List[str] = []
            if txn["amount"] >= 500.0:
                tags.append("high_value")
            if txn["is_new_user"]:
                tags.append("new_user")

            bucket.record(
                user_id=user.user_id,
                predicted_positive=predicted_positive,
                actual_positive=actual_positive,
                strata_tags=tags,
            )

    elapsed = time.time() - start
    return ReplayResult(
        control=control,
        treatment=treatment,
        n_total=n_total,
        n_fraud=n_fraud,
        elapsed_s=elapsed,
    )


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _fmt_pct(x: float) -> str:
    if math.isnan(x):
        return "  n/a"
    return f"{x * 100:6.2f}%"


def _print_result(prefix: str, r) -> None:
    diff_lo, diff_hi = r.ci95_diff
    print(
        f"  {prefix} {r.metric}@{r.stratum:<11} "
        f"p1={_fmt_pct(r.p1)}  p2={_fmt_pct(r.p2)}  "
        f"diff={r.diff:+.4f}  "
        f"CI95=[{diff_lo:+.4f}, {diff_hi:+.4f}]  "
        f"p_naive={r.p_value:.4g}  "
        f"p_cluster={r.p_value_clustered:.4g}  "
        f"DEFF=({r.deff_control:.2f}, {r.deff_treatment:.2f})"
    )


def print_report(title: str, report) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)
    print("Primaries (intersection-union; both must pass to ship):")
    for r in report.primaries:
        _print_result("[P]", r)
    if report.secondaries:
        print("Secondaries (Holm-corrected):")
        for r, sig in zip(report.secondaries, report.secondary_significant_holm):
            _print_result(f"[{'S*' if sig else 'S '}]", r)
    print()
    print(f"Decision: {report.decision.upper()}")
    for reason in report.decision_reasons:
        print(f"  - {reason}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_spec() -> AnalysisSpec:
    return AnalysisSpec(
        primary_metrics=["recall", "fpr"],
        secondary_metrics=[
            "recall@high_value",
            "recall@new_user",
            "fpr@high_value",
            "fpr@new_user",
            "precision",
        ],
        primary_directions={"recall": +1, "fpr": -1, "precision": +1},
        alpha=0.05,
        family_alpha=0.05,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--num-users", type=int, default=4000)
    ap.add_argument("--txns-per-user", type=int, default=25)
    ap.add_argument("--base-fraud-rate", type=float, default=0.035)
    ap.add_argument("--threshold-control", type=float, default=0.3)
    ap.add_argument("--threshold-treatment", type=float, default=0.5)
    ap.add_argument("--experiment-id", default="offline_threshold_test_v1")
    ap.add_argument("--seed", type=int, default=20260522)
    ap.add_argument("--skip-aa", action="store_true", help="Skip the A/A guardrail run")
    ap.add_argument("--report-path", default="reports/ab_test_offline.json")
    args = ap.parse_args()

    print(f"Generating {args.num_users} users x {args.txns_per_user} txns "
          f"(base fraud rate {args.base_fraud_rate:.3f})")

    rng = random.Random(args.seed)
    users = [_gen_user(rng, i) for i in range(args.num_users)]

    spec = build_spec()

    # ---- Sample-size sanity check -----------------------------------------
    n_needed = required_sample_size(
        p_baseline=0.60,                 # rough prior on recall under control
        minimum_detectable_effect=0.05,
        alpha=spec.alpha,
        power=0.80,
    )
    print(f"Per-arm sample size for MDE=0.05 @ baseline recall=0.60: {n_needed} fraud cases")

    # ---- A/A guardrail ----------------------------------------------------
    aa_report = None
    if not args.skip_aa:
        print("\n[1/2] Running A/A guardrail (both arms threshold=control)")
        aa = replay(
            users=users,
            txns_per_user=args.txns_per_user,
            base_fraud_rate=args.base_fraud_rate,
            threshold_control=args.threshold_control,
            threshold_treatment=args.threshold_control,  # same threshold -> A/A
            experiment_id=args.experiment_id + "_AA",
            seed=args.seed,
        )
        print(f"  Replayed {aa.n_total:,} transactions ({aa.n_fraud:,} fraud) in {aa.elapsed_s:.1f}s")
        aa_report = analyze(spec, aa.control, aa.treatment)
        print_report("A/A guardrail", aa_report)
        if aa_report.decision == "ship":
            print("\n!! A/A test 'shipped' -- framework or sampling is suspect. "
                  "Halting before the real A/B.")
            _save_report(args.report_path, {
                "aa": aa_report.to_dict(),
                "ab": None,
                "halted": True,
                "halted_reason": "A/A produced ship decision",
            })
            sys.exit(1)

    # ---- A/B test ---------------------------------------------------------
    print(f"\n[2/2] Running A/B test (control={args.threshold_control}, "
          f"treatment={args.threshold_treatment})")
    ab = replay(
        users=users,
        txns_per_user=args.txns_per_user,
        base_fraud_rate=args.base_fraud_rate,
        threshold_control=args.threshold_control,
        threshold_treatment=args.threshold_treatment,
        experiment_id=args.experiment_id,
        seed=args.seed,
    )
    print(f"  Replayed {ab.n_total:,} transactions ({ab.n_fraud:,} fraud) in {ab.elapsed_s:.1f}s")
    print(f"  Assignment split: control n={ab.control.total}, treatment n={ab.treatment.total}")
    ab_report = analyze(spec, ab.control, ab.treatment)
    print_report(
        f"A/B: threshold {args.threshold_control} (control) vs "
        f"{args.threshold_treatment} (treatment)",
        ab_report,
    )

    # ---- Persist ----------------------------------------------------------
    _save_report(args.report_path, {
        "config": vars(args),
        "spec": {
            "primary_metrics": spec.primary_metrics,
            "secondary_metrics": spec.secondary_metrics,
            "alpha": spec.alpha,
            "family_alpha": spec.family_alpha,
        },
        "sample_size_estimate": n_needed,
        "aa": aa_report.to_dict() if aa_report else None,
        "ab": ab_report.to_dict(),
        "summary": {
            "control_total": ab.control.total,
            "treatment_total": ab.treatment.total,
            "control_actual_positives": ab.control.actual_positives,
            "treatment_actual_positives": ab.treatment.actual_positives,
            "control_predicted_positives": ab.control.predicted_positives,
            "treatment_predicted_positives": ab.treatment.predicted_positives,
        },
    })
    print(f"\nReport saved to {args.report_path}")


def _save_report(path: str, payload: Dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump(payload, f, indent=2, default=str)


if __name__ == "__main__":
    main()
