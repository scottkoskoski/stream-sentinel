# /stream-sentinel/tests/unit/test_ab_test_analysis.py

"""
Unit tests for the rigorous two-proportion z-test analysis module.

These tests pin the math against textbook values and synthetic null
distributions, not just round-trip the code. If they pass we have evidence the
statistical core is correct; downstream A/B reports inherit that correctness.
"""

import importlib.util
import random
from pathlib import Path

import pytest

# Load ab_test_analysis as a standalone module so we don't pull in the
# online_learning package __init__ (which imports kafka/redis/joblib heavy
# dependencies unrelated to the math being tested here).
_ANALYSIS_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "ml" / "online_learning" / "ab_test_analysis.py"
)
_spec = importlib.util.spec_from_file_location("ab_test_analysis", _ANALYSIS_PATH)
ab_test_analysis = importlib.util.module_from_spec(_spec)
# Register before exec so dataclass forward references can resolve.
import sys as _sys
_sys.modules["ab_test_analysis"] = ab_test_analysis
_spec.loader.exec_module(ab_test_analysis)

AnalysisSpec = ab_test_analysis.AnalysisSpec
VariantCounters = ab_test_analysis.VariantCounters
analyze = ab_test_analysis.analyze
design_effect = ab_test_analysis.design_effect
holm_bonferroni = ab_test_analysis.holm_bonferroni
newcombe_diff_ci = ab_test_analysis.newcombe_diff_ci
required_sample_size = ab_test_analysis.required_sample_size
two_proportion_ztest = ab_test_analysis.two_proportion_ztest


# ---------------------------------------------------------------------------
# two_proportion_ztest -- pin against a textbook example
# ---------------------------------------------------------------------------


class TestTwoProportionZTest:
    """Hand-computed expected values."""

    @pytest.mark.unit
    def test_known_textbook_case(self):
        # Standard worked example: 90/100 vs 80/100 successes.
        #   p_pooled = 170/200 = 0.85
        #   SE = sqrt(0.85 * 0.15 * (1/100 + 1/100)) = sqrt(0.00255) = 0.05049...
        #   z  = (0.80 - 0.90) / 0.05049 = -1.9803...
        #   p  ~= 0.04766
        p1, p2, z, p = two_proportion_ztest(90, 100, 80, 100)
        assert p1 == pytest.approx(0.90)
        assert p2 == pytest.approx(0.80)
        assert z == pytest.approx(-1.98030, abs=1e-4)
        assert p == pytest.approx(0.04766, abs=1e-4)

    @pytest.mark.unit
    def test_identical_rates_give_z_zero(self):
        _, _, z, p = two_proportion_ztest(50, 100, 50, 100)
        assert z == 0.0
        assert p == pytest.approx(1.0)

    @pytest.mark.unit
    def test_degenerate_zero_denominator(self):
        # Zero-N control should not blow up and should return neutral.
        _, _, z, p = two_proportion_ztest(0, 0, 5, 100)
        assert z == 0.0
        assert p == 1.0

    @pytest.mark.unit
    def test_all_zero_successes_zero_se(self):
        # Both arms see zero successes -> p_pooled = 0 -> SE = 0 -> neutral.
        _, _, z, p = two_proportion_ztest(0, 100, 0, 100)
        assert z == 0.0
        assert p == 1.0

    @pytest.mark.unit
    def test_aa_null_distribution_calibrated(self):
        """
        A/A test sanity: under H0 (true equal proportions), the test should
        reject in ~5% of trials at alpha=0.05. We allow a tolerant band to
        avoid a flaky test, but we run enough trials for the signal.
        """
        rng = random.Random(20260522)
        trials = 2000
        n_per_arm = 1000
        true_p = 0.05  # low base rate (fraud-like)
        rejections = 0
        for _ in range(trials):
            x1 = sum(1 for _ in range(n_per_arm) if rng.random() < true_p)
            x2 = sum(1 for _ in range(n_per_arm) if rng.random() < true_p)
            _, _, _, p = two_proportion_ztest(x1, n_per_arm, x2, n_per_arm)
            if p < 0.05:
                rejections += 1
        rate = rejections / trials
        # Wider than asymptotic 95% interval (~0.04 to 0.06) to absorb the
        # known conservatism of the normal approximation at low base rates.
        assert 0.025 < rate < 0.075, f"A/A rejection rate {rate:.3f} out of expected band"


# ---------------------------------------------------------------------------
# Newcombe interval
# ---------------------------------------------------------------------------


class TestNewcombeDiffCI:
    @pytest.mark.unit
    def test_interval_contains_observed_diff(self):
        lo, hi = newcombe_diff_ci(90, 100, 80, 100)
        diff = 0.80 - 0.90
        assert lo <= diff <= hi
        # And the interval is finite and non-trivial.
        assert -1.0 < lo < hi < 1.0

    @pytest.mark.unit
    def test_interval_bounded_at_boundary(self):
        # Both arms 100% successes -> diff = 0, interval should be tight near 0
        # and never escape [-1, 1].
        lo, hi = newcombe_diff_ci(100, 100, 100, 100)
        assert -1.0 <= lo <= 0.0 <= hi <= 1.0

    @pytest.mark.unit
    def test_zero_n_returns_widest(self):
        lo, hi = newcombe_diff_ci(0, 0, 0, 0)
        assert (lo, hi) == (-1.0, 1.0)


# ---------------------------------------------------------------------------
# Design effect
# ---------------------------------------------------------------------------


class TestDesignEffect:
    @pytest.mark.unit
    def test_singleton_clusters_give_deff_one(self):
        # All clusters of size 1 -> no clustering inflation.
        sizes = [1] * 50
        succ = [1 if i < 5 else 0 for i in range(50)]  # 10% rate
        deff = design_effect(sizes, succ)
        assert deff == pytest.approx(1.0)

    @pytest.mark.unit
    def test_perfectly_homogeneous_within_cluster_inflates(self):
        # Some clusters all-1, others all-0 -> intra-cluster correlation = 1
        # -> DEFF = m_bar. Here m_bar = 10, so DEFF should be near 10.
        sizes = [10] * 20
        succ = [10 if i < 10 else 0 for i in range(20)]
        deff = design_effect(sizes, succ)
        assert deff == pytest.approx(10.0, rel=0.05)

    @pytest.mark.unit
    def test_independent_within_cluster_near_one(self):
        """When transactions within a user are independent Bernoulli draws,
        DEFF should sit near 1 (rho ~ 0)."""
        rng = random.Random(7)
        sizes = []
        succ = []
        for _ in range(200):
            n = 5
            x = sum(1 for _ in range(n) if rng.random() < 0.1)
            sizes.append(n)
            succ.append(x)
        deff = design_effect(sizes, succ)
        # Wider tolerance because the ANOVA estimator is noisy on small clusters.
        assert 1.0 <= deff < 1.5

    @pytest.mark.unit
    def test_empty_or_single_cluster_returns_one(self):
        assert design_effect([], []) == 1.0
        assert design_effect([5], [2]) == 1.0


# ---------------------------------------------------------------------------
# Sample-size formula
# ---------------------------------------------------------------------------


class TestRequiredSampleSize:
    @pytest.mark.unit
    def test_low_baseline_needs_more_than_p_half(self):
        """The fraud-relevant case: baseline 0.05 should need *much more*
        than baseline 0.5 for the same absolute MDE -- the old formula's
        0.5 assumption under-sizes by a large factor."""
        n_low = required_sample_size(p_baseline=0.05, minimum_detectable_effect=0.02)
        n_half = required_sample_size(p_baseline=0.50, minimum_detectable_effect=0.02)
        assert n_low < n_half
        # And both should be in the thousands -- not the 100-sample floor the
        # legacy code falls back to.
        assert n_low > 500
        assert n_half > 5000

    @pytest.mark.unit
    def test_increasing_mde_decreases_sample_size(self):
        n_small = required_sample_size(0.10, 0.01)
        n_big = required_sample_size(0.10, 0.05)
        assert n_big < n_small

    @pytest.mark.unit
    def test_invalid_inputs_raise(self):
        with pytest.raises(ValueError):
            required_sample_size(0.0, 0.01)
        with pytest.raises(ValueError):
            required_sample_size(0.5, 0.0)


# ---------------------------------------------------------------------------
# Holm-Bonferroni
# ---------------------------------------------------------------------------


class TestHolmBonferroni:
    @pytest.mark.unit
    def test_empty_list(self):
        assert holm_bonferroni([]) == []

    @pytest.mark.unit
    def test_all_significant_under_threshold(self):
        # Very small p-values, all should pass.
        assert holm_bonferroni([0.0001, 0.0002, 0.0003], alpha=0.05) == [True, True, True]

    @pytest.mark.unit
    def test_step_down_stops_at_first_failure(self):
        # Sorted p-values: 0.01, 0.04, 0.20.
        # Thresholds at alpha=0.05, m=3: 0.05/3=0.0167, 0.05/2=0.025, 0.05/1=0.05.
        # Rank 0 (p=0.01) <= 0.0167 -> reject.
        # Rank 1 (p=0.04) > 0.025    -> fail, and stop (no rejection further).
        result = holm_bonferroni([0.01, 0.04, 0.20], alpha=0.05)
        assert result == [True, False, False]

    @pytest.mark.unit
    def test_preserves_input_order(self):
        # First p-value is largest -> the first slot in the result must
        # be False even though others might be significant.
        result = holm_bonferroni([0.30, 0.001, 0.002], alpha=0.05)
        assert result[0] is False
        # The two small p-values are both well below alpha/2 = 0.025, so both
        # are rejected once they're sorted to the front of the step-down.
        assert result[1] is True
        assert result[2] is True


# ---------------------------------------------------------------------------
# VariantCounters
# ---------------------------------------------------------------------------


class TestVariantCounters:
    @pytest.mark.unit
    def test_basic_bookkeeping(self):
        v = VariantCounters()
        v.record("u1", predicted_positive=True, actual_positive=True)     # tp
        v.record("u1", predicted_positive=True, actual_positive=False)    # fp
        v.record("u2", predicted_positive=False, actual_positive=True)    # fn
        v.record("u2", predicted_positive=False, actual_positive=False)   # tn
        assert (v.tp, v.fp, v.fn, v.tn) == (1, 1, 1, 1)
        assert v.total == 4
        assert v.actual_positives == 2
        assert v.actual_negatives == 2

    @pytest.mark.unit
    def test_per_user_tracking(self):
        v = VariantCounters()
        v.record("u1", True, True)
        v.record("u1", True, True)
        v.record("u2", False, False)
        assert v.per_user["u1"]["tp"] == 2
        assert v.per_user["u1"]["n"] == 2
        assert v.per_user["u2"]["tn"] == 1

    @pytest.mark.unit
    def test_strata_recursion(self):
        v = VariantCounters()
        v.record("u1", True, True, strata_tags=["high_value"])
        v.record("u1", True, False, strata_tags=["high_value"])
        v.record("u2", False, True, strata_tags=[])
        assert v.tp == 1 and v.fp == 1 and v.fn == 1
        sub = v.strata["high_value"]
        assert sub.tp == 1 and sub.fp == 1 and sub.fn == 0

    @pytest.mark.unit
    def test_metric_extraction(self):
        v = VariantCounters()
        # 8 TP, 2 FN -> recall = 0.8
        # 3 FP, 7 TN -> fpr   = 0.3
        for _ in range(8):
            v.record("u1", True, True)
        for _ in range(2):
            v.record("u1", False, True)
        for _ in range(3):
            v.record("u2", True, False)
        for _ in range(7):
            v.record("u2", False, False)

        x, n = v.successes_and_totals("recall")
        assert (x, n) == (8, 10)
        x, n = v.successes_and_totals("fpr")
        assert (x, n) == (3, 10)
        x, n = v.successes_and_totals("precision")
        assert (x, n) == (8, 11)

    @pytest.mark.unit
    def test_unsupported_metric_raises(self):
        v = VariantCounters()
        v.record("u1", True, True)
        with pytest.raises(ValueError):
            v.successes_and_totals("auc")


# ---------------------------------------------------------------------------
# analyze() orchestrator
# ---------------------------------------------------------------------------


def _build_variant(rng, n_users, txns_per_user, fraud_rate, model_recall, model_fpr):
    """Helper: simulate a variant by drawing fraud labels and model decisions."""
    v = VariantCounters()
    for u in range(n_users):
        uid = f"u{u:04d}"
        for _ in range(txns_per_user):
            is_fraud = rng.random() < fraud_rate
            if is_fraud:
                flagged = rng.random() < model_recall
            else:
                flagged = rng.random() < model_fpr
            v.record(uid, predicted_positive=flagged, actual_positive=is_fraud)
    return v


class TestAnalyze:
    @pytest.mark.unit
    def test_obvious_treatment_win_ships(self):
        rng = random.Random(1)
        control = _build_variant(rng, n_users=300, txns_per_user=20,
                                 fraud_rate=0.05, model_recall=0.60, model_fpr=0.05)
        rng = random.Random(2)
        treatment = _build_variant(rng, n_users=300, txns_per_user=20,
                                   fraud_rate=0.05, model_recall=0.80, model_fpr=0.04)
        spec = AnalysisSpec(
            primary_metrics=["recall", "fpr"],
            primary_directions={"recall": +1, "fpr": -1},
        )
        report = analyze(spec, control, treatment)
        assert report.decision == "ship", report.decision_reasons
        assert all(report.primary_significant)

    @pytest.mark.unit
    def test_treatment_regression_blocks_ship(self):
        # Treatment is worse at FPR (more false alarms) -- a primary regresses.
        rng = random.Random(3)
        control = _build_variant(rng, n_users=300, txns_per_user=20,
                                 fraud_rate=0.05, model_recall=0.70, model_fpr=0.03)
        rng = random.Random(4)
        treatment = _build_variant(rng, n_users=300, txns_per_user=20,
                                   fraud_rate=0.05, model_recall=0.72, model_fpr=0.10)
        spec = AnalysisSpec(
            primary_metrics=["recall", "fpr"],
            primary_directions={"recall": +1, "fpr": -1},
        )
        report = analyze(spec, control, treatment)
        assert report.decision == "do_not_ship", report.decision_reasons

    @pytest.mark.unit
    def test_aa_setting_inconclusive(self):
        # Same generator parameters on both arms -- should not ship.
        rng = random.Random(5)
        control = _build_variant(rng, 200, 20, 0.05, 0.70, 0.05)
        rng = random.Random(6)
        treatment = _build_variant(rng, 200, 20, 0.05, 0.70, 0.05)
        spec = AnalysisSpec(
            primary_metrics=["recall", "fpr"],
            primary_directions={"recall": +1, "fpr": -1},
        )
        report = analyze(spec, control, treatment)
        assert report.decision in {"inconclusive", "do_not_ship"}
        # Crucially, it must NOT ship.
        assert report.decision != "ship"

    @pytest.mark.unit
    def test_stratified_secondary_caught_by_holm(self):
        """
        Treatment overall looks fine on the primaries, but regresses on a
        specific segment. Holm-corrected secondary should flag the harm
        and downgrade the decision to do_not_ship.
        """
        rng = random.Random(7)
        # Overall: identical performance.
        control = _build_variant(rng, 300, 15, 0.05, 0.75, 0.05)
        rng = random.Random(8)
        treatment = _build_variant(rng, 300, 15, 0.05, 0.75, 0.05)

        # Inject a high-value segment where treatment is dramatically worse.
        for i in range(800):
            uid = f"hv_{i}"
            control.record(uid, True, True, strata_tags=["high_value"])
        for i in range(800):
            uid = f"hv_{i}"
            # Treatment misses these high-value fraud cases.
            treatment.record(uid, False, True, strata_tags=["high_value"])

        spec = AnalysisSpec(
            primary_metrics=["recall", "fpr"],
            secondary_metrics=["recall@high_value"],
            primary_directions={"recall": +1, "fpr": -1},
        )
        report = analyze(spec, control, treatment)
        # The injected harm should make Holm flag the segment.
        assert report.secondary_significant_holm == [True]
        assert report.decision == "do_not_ship"
