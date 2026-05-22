# /stream-sentinel/src/ml/online_learning/ab_test_analysis.py

"""
Rigorous two-proportion z-test analysis for fraud-detection A/B experiments.

This module contains the statistical core used by the ABTestManager and the
offline replay harness. It is intentionally framework-free (pure functions on
counts) so the math can be unit-tested in isolation.

What it provides
----------------
- ``two_proportion_ztest``    -- pooled-variance z-test, two-sided p-value.
- ``newcombe_diff_ci``        -- Newcombe hybrid interval for (p2 - p1).
- ``design_effect``           -- empirical DEFF for user-clustered outcomes.
- ``required_sample_size``    -- base-rate-aware n per arm for a target MDE.
- ``holm_bonferroni``         -- step-down adjustment for a family of p-values.
- ``VariantCounters``         -- per-variant, per-stratum confusion-matrix
                                 bookkeeping, including per-user counts for
                                 clustering correction.
- ``AnalysisSpec`` / ``analyze`` -- pre-registered analysis with primary
                                    co-metrics and Holm-corrected secondaries.

Statistical choices and why
---------------------------
- Co-primary metrics (recall AND fpr) are tested at the full alpha without
  correction because we *require both* to favour treatment to declare a win
  (intersection-union test).
- Secondary metrics are corrected with Holm-Bonferroni to control the family
  -wise error rate when we slice by segment.
- The naive transaction-level z-test under-states variance when users see one
  arm but contribute many transactions. We estimate the design effect
  empirically and re-test at the cluster-adjusted effective sample size.
- We surface both naive and cluster-adjusted p-values so the reader can see how
  much clustering moved the needle. The cluster-adjusted value is the one used
  for the ship decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from scipy import stats

# ---------------------------------------------------------------------------
# Low-level statistical primitives
# ---------------------------------------------------------------------------


def two_proportion_ztest(
    x1: int, n1: int, x2: int, n2: int
) -> Tuple[float, float, float, float]:
    """
    Pooled-variance two-proportion z-test (two-sided).

    Args:
        x1: successes in control
        n1: total in control
        x2: successes in treatment
        n2: total in treatment

    Returns:
        (p1, p2, z, p_value)

    Notes:
        - Matches the formulation used in ``ab_test_manager._perform_statistical_test``
          and ``drift_detector._detect_concept_drift`` so behaviour is consistent.
        - Returns z=0, p=1 in degenerate cases (zero denominators or zero SE),
          which is the conservative outcome.
    """
    if n1 <= 0 or n2 <= 0:
        return 0.0, 0.0, 0.0, 1.0

    p1 = x1 / n1
    p2 = x2 / n2
    p_pooled = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p_pooled * (1.0 - p_pooled) * (1.0 / n1 + 1.0 / n2))

    if se == 0.0:
        return p1, p2, 0.0, 1.0

    z = (p2 - p1) / se
    p_value = 2.0 * (1.0 - stats.norm.cdf(abs(z)))
    return p1, p2, z, p_value


def _wilson_score_interval(x: int, n: int, alpha: float = 0.05) -> Tuple[float, float]:
    """Wilson score interval for a single proportion (boundary-friendly)."""
    if n <= 0:
        return 0.0, 1.0
    z = stats.norm.ppf(1.0 - alpha / 2.0)
    phat = x / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2.0 * n)) / denom
    half = (z * math.sqrt(phat * (1.0 - phat) / n + z * z / (4.0 * n * n))) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe_diff_ci(
    x1: int, n1: int, x2: int, n2: int, alpha: float = 0.05
) -> Tuple[float, float]:
    """
    Newcombe hybrid score interval for (p2 - p1).

    Why Newcombe instead of plain normal-approx CI: stays inside [-1, 1] near
    the boundary (p close to 0 or 1) and has good coverage with small or
    imbalanced samples -- both common in fraud (FP is rare, FN is rarer).

    Reference: Newcombe R.G. (1998), Stat. Med. 17:873-890, method 10.
    """
    if n1 <= 0 or n2 <= 0:
        return -1.0, 1.0

    l1, u1 = _wilson_score_interval(x1, n1, alpha)
    l2, u2 = _wilson_score_interval(x2, n2, alpha)
    p1, p2 = x1 / n1, x2 / n2
    diff = p2 - p1

    lower = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    upper = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return max(-1.0, lower), min(1.0, upper)


def design_effect(per_cluster_size: Sequence[int], per_cluster_successes: Sequence[int]) -> float:
    """
    Empirical design effect (DEFF) for one arm.

    DEFF = 1 + (m_bar - 1) * rho

    where m_bar is the average cluster size and rho is the intra-cluster
    correlation, estimated via one-way ANOVA decomposition of the indicator
    variable Y_ij = 1{transaction j of user i was a "success"}.

    Returns 1.0 (no clustering inflation) when there is only one cluster, all
    clusters have size 1, or the within-cluster variance dominates.
    """
    sizes = [s for s in per_cluster_size if s > 0]
    if len(sizes) < 2:
        return 1.0

    succ = list(per_cluster_successes)
    if len(succ) != len(sizes):
        raise ValueError("size and success arrays must match in length")

    n_total = sum(sizes)
    x_total = sum(succ)
    if n_total == 0:
        return 1.0
    p_overall = x_total / n_total
    m_bar = n_total / len(sizes)

    # Mean square between clusters (MSB) and within clusters (MSW)
    msb_num = sum(s * ((x / s) - p_overall) ** 2 for s, x in zip(sizes, succ) if s > 0)
    msb = msb_num / (len(sizes) - 1) if len(sizes) > 1 else 0.0

    msw_num = 0.0
    msw_den = 0
    for s, x in zip(sizes, succ):
        if s <= 1:
            continue
        phat = x / s
        # Sum of squared deviations within cluster for a Bernoulli sample
        msw_num += s * phat * (1.0 - phat)
        msw_den += s - 1
    msw = msw_num / msw_den if msw_den > 0 else 0.0

    # ANOVA estimator of rho (truncated to [0, 1] -- negative ICC is treated as zero)
    if msb + (m_bar - 1) * msw <= 0:
        rho = 0.0
    else:
        rho = (msb - msw) / (msb + (m_bar - 1) * msw)
    rho = max(0.0, min(1.0, rho))

    deff = 1.0 + (m_bar - 1.0) * rho
    return max(1.0, deff)


def required_sample_size(
    p_baseline: float,
    minimum_detectable_effect: float,
    alpha: float = 0.05,
    power: float = 0.8,
    two_sided: bool = True,
) -> int:
    """
    Per-arm sample size for a two-proportion z-test at a given MDE.

    Uses the standard normal-approximation formula. Critically, it uses the
    *baseline* proportion rather than 0.5 -- the latter (as in the existing
    ``_calculate_sample_size``) drastically under-sizes recall tests on rare
    outcomes like fraud.

    Args:
        p_baseline: control-arm proportion (e.g. expected recall under control)
        minimum_detectable_effect: absolute effect size to detect (p2 - p1)
        alpha: significance level
        power: target power
        two_sided: use a two-sided test

    Returns:
        Required sample size per arm (rounded up).
    """
    if not 0.0 < p_baseline < 1.0:
        raise ValueError("p_baseline must be in (0, 1)")
    if minimum_detectable_effect <= 0.0:
        raise ValueError("minimum_detectable_effect must be > 0")

    p1 = p_baseline
    p2 = min(1.0 - 1e-9, p_baseline + minimum_detectable_effect)
    p_bar = (p1 + p2) / 2.0

    z_alpha = stats.norm.ppf(1.0 - alpha / (2.0 if two_sided else 1.0))
    z_beta = stats.norm.ppf(power)

    numerator = (
        z_alpha * math.sqrt(2.0 * p_bar * (1.0 - p_bar))
        + z_beta * math.sqrt(p1 * (1.0 - p1) + p2 * (1.0 - p2))
    ) ** 2
    n = numerator / (minimum_detectable_effect ** 2)
    return int(math.ceil(n))


def holm_bonferroni(p_values: Sequence[float], alpha: float = 0.05) -> List[bool]:
    """
    Holm-Bonferroni step-down correction.

    Returns a list of booleans (one per input p-value, in original order)
    indicating whether each null hypothesis is rejected at family-wise
    error rate ``alpha``.
    """
    m = len(p_values)
    if m == 0:
        return []

    indexed = sorted(enumerate(p_values), key=lambda t: t[1])
    rejected = [False] * m
    for rank, (orig_idx, p) in enumerate(indexed):
        threshold = alpha / (m - rank)
        if p <= threshold:
            rejected[orig_idx] = True
        else:
            break  # step-down: once we fail to reject, stop
    return rejected


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass
class ProportionTestResult:
    """Result of one two-proportion z-test, with cluster correction."""

    metric: str
    stratum: str  # "overall", "high_value", "new_user", ...
    n1: int
    x1: int
    n2: int
    x2: int
    p1: float
    p2: float
    diff: float          # p2 - p1
    relative_lift: float # (p2 - p1) / p1  (NaN if p1 == 0)
    z: float
    p_value: float       # naive transaction-level
    ci95_diff: Tuple[float, float]
    deff_control: float
    deff_treatment: float
    p_value_clustered: float  # adjusted for clustering
    n1_effective: float
    n2_effective: float

    def to_dict(self) -> Dict[str, object]:
        return {
            "metric": self.metric,
            "stratum": self.stratum,
            "n1": self.n1,
            "x1": self.x1,
            "n2": self.n2,
            "x2": self.x2,
            "p1": self.p1,
            "p2": self.p2,
            "diff": self.diff,
            "relative_lift": self.relative_lift,
            "z": self.z,
            "p_value": self.p_value,
            "ci95_diff_low": self.ci95_diff[0],
            "ci95_diff_high": self.ci95_diff[1],
            "deff_control": self.deff_control,
            "deff_treatment": self.deff_treatment,
            "p_value_clustered": self.p_value_clustered,
            "n1_effective": self.n1_effective,
            "n2_effective": self.n2_effective,
        }


@dataclass
class VariantCounters:
    """
    Per-variant confusion-matrix bookkeeping with stratification and clustering.

    ``per_user`` tracks (predicted_positives, true_positives, false_positives,
    actual_positives, actual_negatives, total) for each user assigned to this
    variant. Per-user totals are used to estimate the design effect.

    ``strata`` recurses: each stratum (e.g. "high_value") gets its own
    VariantCounters with no further substrata.
    """

    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    # per_user: user_id -> dict of running counts
    per_user: Dict[str, Dict[str, int]] = field(default_factory=dict)
    strata: Dict[str, "VariantCounters"] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def actual_positives(self) -> int:
        return self.tp + self.fn

    @property
    def actual_negatives(self) -> int:
        return self.fp + self.tn

    @property
    def predicted_positives(self) -> int:
        return self.tp + self.fp

    def record(
        self,
        user_id: str,
        predicted_positive: bool,
        actual_positive: bool,
        strata_tags: Optional[Sequence[str]] = None,
    ) -> None:
        """Record one labelled prediction for this variant."""
        if predicted_positive and actual_positive:
            self.tp += 1
            outcome = "tp"
        elif predicted_positive and not actual_positive:
            self.fp += 1
            outcome = "fp"
        elif not predicted_positive and actual_positive:
            self.fn += 1
            outcome = "fn"
        else:
            self.tn += 1
            outcome = "tn"

        ucounts = self.per_user.setdefault(
            user_id,
            {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "n": 0},
        )
        ucounts[outcome] += 1
        ucounts["n"] += 1

        if strata_tags:
            for tag in strata_tags:
                sub = self.strata.setdefault(tag, VariantCounters())
                sub.record(user_id, predicted_positive, actual_positive, strata_tags=None)

    # -- metric extractors ---------------------------------------------------
    def successes_and_totals(self, metric: str) -> Tuple[int, int]:
        """
        Return (successes, totals) for the named metric.

        Each metric has a binary outcome at the *transaction* level so the
        two-proportion z-test is well-defined.
        """
        if metric == "recall":
            # numerator: TPs, denominator: actual positives (fraud cases)
            return self.tp, self.actual_positives
        if metric == "fpr":
            # numerator: FPs, denominator: actual negatives (legitimate txns)
            return self.fp, self.actual_negatives
        if metric == "precision":
            return self.tp, self.predicted_positives
        if metric == "flag_rate":
            return self.predicted_positives, self.total
        raise ValueError(f"Unsupported metric: {metric}")

    def per_user_outcomes(self, metric: str) -> Tuple[List[int], List[int]]:
        """
        For each user, return (per-user denominator count, per-user successes)
        used to compute the design effect for a given metric.
        """
        sizes: List[int] = []
        succ: List[int] = []
        for uc in self.per_user.values():
            if metric == "recall":
                denom = uc["tp"] + uc["fn"]
                num = uc["tp"]
            elif metric == "fpr":
                denom = uc["fp"] + uc["tn"]
                num = uc["fp"]
            elif metric == "precision":
                denom = uc["tp"] + uc["fp"]
                num = uc["tp"]
            elif metric == "flag_rate":
                denom = uc["n"]
                num = uc["tp"] + uc["fp"]
            else:
                raise ValueError(f"Unsupported metric: {metric}")
            if denom > 0:
                sizes.append(denom)
                succ.append(num)
        return sizes, succ


# ---------------------------------------------------------------------------
# High-level analysis orchestrator
# ---------------------------------------------------------------------------


@dataclass
class AnalysisSpec:
    """
    Pre-registered analysis plan.

    ``primary_metrics`` are tested at the full alpha each. The ship decision
    requires *all* primaries to be significant AND directionally favourable
    (intersection-union); this controls the type-I rate without correction.

    ``secondary_metrics`` may include stratified cuts encoded as
    "metric@stratum" (e.g. "recall@high_value"). They are corrected with
    Holm-Bonferroni at ``family_alpha``.

    ``primary_directions`` maps each primary metric to the direction that
    counts as a "win":
       +1 -> treatment is better when p2 > p1 (e.g. recall, precision)
       -1 -> treatment is better when p2 < p1 (e.g. fpr, fn-rate)
    """

    primary_metrics: List[str]
    secondary_metrics: List[str] = field(default_factory=list)
    primary_directions: Mapping[str, int] = field(default_factory=dict)
    alpha: float = 0.05
    family_alpha: float = 0.05

    def direction_for(self, metric: str) -> int:
        base = metric.split("@", 1)[0]
        if base in self.primary_directions:
            return self.primary_directions[base]
        # Sensible defaults
        if base in {"fpr", "fn_rate", "false_alarm_rate"}:
            return -1
        return +1


@dataclass
class ExperimentReport:
    """Decision-ready report from an analysis run."""

    primaries: List[ProportionTestResult]
    secondaries: List[ProportionTestResult]
    primary_significant: List[bool]
    secondary_significant_holm: List[bool]
    decision: str        # "ship", "do_not_ship", "inconclusive"
    decision_reasons: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "primaries": [t.to_dict() for t in self.primaries],
            "secondaries": [t.to_dict() for t in self.secondaries],
            "primary_significant": self.primary_significant,
            "secondary_significant_holm": self.secondary_significant_holm,
            "decision": self.decision,
            "decision_reasons": self.decision_reasons,
        }


def _run_one_test(
    metric_name: str,
    stratum: str,
    control: VariantCounters,
    treatment: VariantCounters,
    alpha: float,
) -> ProportionTestResult:
    x1, n1 = control.successes_and_totals(metric_name)
    x2, n2 = treatment.successes_and_totals(metric_name)
    p1, p2, z, p_value = two_proportion_ztest(x1, n1, x2, n2)
    ci_lo, ci_hi = newcombe_diff_ci(x1, n1, x2, n2, alpha=alpha)

    # Cluster correction: re-test at effective n = n / DEFF using same observed
    # rates (i.e. we keep p1, p2 fixed and inflate the standard error).
    sizes1, succ1 = control.per_user_outcomes(metric_name)
    sizes2, succ2 = treatment.per_user_outcomes(metric_name)
    deff1 = design_effect(sizes1, succ1)
    deff2 = design_effect(sizes2, succ2)

    n1_eff = n1 / deff1 if deff1 > 0 else float(n1)
    n2_eff = n2 / deff2 if deff2 > 0 else float(n2)

    if n1_eff > 0 and n2_eff > 0 and 0 < p1 + p2 < 2:
        p_pooled = (x1 + x2) / (n1 + n2)
        se_eff = math.sqrt(p_pooled * (1.0 - p_pooled) * (1.0 / n1_eff + 1.0 / n2_eff))
        if se_eff > 0:
            z_eff = (p2 - p1) / se_eff
            p_value_clustered = 2.0 * (1.0 - stats.norm.cdf(abs(z_eff)))
        else:
            p_value_clustered = 1.0
    else:
        p_value_clustered = 1.0

    diff = p2 - p1
    relative_lift = (diff / p1) if p1 > 0 else float("nan")

    return ProportionTestResult(
        metric=metric_name,
        stratum=stratum,
        n1=n1,
        x1=x1,
        n2=n2,
        x2=x2,
        p1=p1,
        p2=p2,
        diff=diff,
        relative_lift=relative_lift,
        z=z,
        p_value=p_value,
        ci95_diff=(ci_lo, ci_hi),
        deff_control=deff1,
        deff_treatment=deff2,
        p_value_clustered=p_value_clustered,
        n1_effective=n1_eff,
        n2_effective=n2_eff,
    )


def _resolve_counters(
    variant: VariantCounters, metric_with_stratum: str
) -> Tuple[str, str, VariantCounters]:
    """Parse "metric@stratum" and return the relevant VariantCounters."""
    if "@" in metric_with_stratum:
        metric, stratum = metric_with_stratum.split("@", 1)
        counters = variant.strata.get(stratum, VariantCounters())
        return metric, stratum, counters
    return metric_with_stratum, "overall", variant


def analyze(
    spec: AnalysisSpec,
    control: VariantCounters,
    treatment: VariantCounters,
) -> ExperimentReport:
    """
    Run the pre-registered analysis and produce a ship decision.

    Ship rule (intersection-union over primaries):
      All primary metrics must (a) be statistically significant after cluster
      adjustment at ``spec.alpha``, AND (b) move in the favourable direction.
      Secondary metrics may exhibit harms: if any secondary moves
      *significantly* in the unfavourable direction after Holm correction, the
      decision is downgraded to "do_not_ship".

    Outcomes:
      "ship"          -- all primaries pass and no secondary harms detected
      "do_not_ship"   -- a primary moves the wrong way significantly, or a
                         secondary harm is detected
      "inconclusive"  -- otherwise (primary failed to reach significance with
                         no opposing-direction signal)
    """
    primaries: List[ProportionTestResult] = []
    primary_significant: List[bool] = []
    for m in spec.primary_metrics:
        metric, stratum, ctrl_c = _resolve_counters(control, m)
        _, _, trt_c = _resolve_counters(treatment, m)
        res = _run_one_test(metric, stratum, ctrl_c, trt_c, spec.alpha)
        primaries.append(res)
        primary_significant.append(res.p_value_clustered < spec.alpha)

    secondaries: List[ProportionTestResult] = []
    secondary_p_values: List[float] = []
    for m in spec.secondary_metrics:
        metric, stratum, ctrl_c = _resolve_counters(control, m)
        _, _, trt_c = _resolve_counters(treatment, m)
        res = _run_one_test(metric, stratum, ctrl_c, trt_c, spec.alpha)
        secondaries.append(res)
        secondary_p_values.append(res.p_value_clustered)

    secondary_significant_holm = holm_bonferroni(secondary_p_values, spec.family_alpha)

    # Decision
    reasons: List[str] = []
    bad_primary = False
    good_primary = True
    for res, sig in zip(primaries, primary_significant):
        direction = spec.direction_for(res.metric)
        favourable = (direction == +1 and res.diff > 0) or (direction == -1 and res.diff < 0)
        if sig and not favourable:
            bad_primary = True
            reasons.append(
                f"Primary {res.metric}@{res.stratum} moved against treatment "
                f"(diff={res.diff:+.4f}, p_clustered={res.p_value_clustered:.4g})"
            )
        if not (sig and favourable):
            good_primary = False
            if not sig:
                reasons.append(
                    f"Primary {res.metric}@{res.stratum} not significant "
                    f"(p_clustered={res.p_value_clustered:.4g})"
                )

    secondary_harm = False
    for res, sig in zip(secondaries, secondary_significant_holm):
        direction = spec.direction_for(res.metric)
        favourable = (direction == +1 and res.diff > 0) or (direction == -1 and res.diff < 0)
        if sig and not favourable:
            secondary_harm = True
            reasons.append(
                f"Secondary {res.metric}@{res.stratum} regressed "
                f"(diff={res.diff:+.4f}, p_holm-significant)"
            )

    if bad_primary or secondary_harm:
        decision = "do_not_ship"
    elif good_primary:
        decision = "ship"
        if not reasons:
            reasons.append("All primaries significant and favourable; no secondary harms.")
    else:
        decision = "inconclusive"

    return ExperimentReport(
        primaries=primaries,
        secondaries=secondaries,
        primary_significant=primary_significant,
        secondary_significant_holm=secondary_significant_holm,
        decision=decision,
        decision_reasons=reasons,
    )


__all__ = [
    "two_proportion_ztest",
    "newcombe_diff_ci",
    "design_effect",
    "required_sample_size",
    "holm_bonferroni",
    "VariantCounters",
    "ProportionTestResult",
    "AnalysisSpec",
    "ExperimentReport",
    "analyze",
]
