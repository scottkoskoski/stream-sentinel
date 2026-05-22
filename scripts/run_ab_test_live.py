#!/usr/bin/env python3
# /stream-sentinel/scripts/run_ab_test_live.py

"""
Live A/B test entry point.

Creates and starts an experiment in the ABTestManager (backed by Redis). Any
fraud-detection consumer already running with A/B routing enabled will pick
up the experiment on its next refresh and begin assigning users to variants
via the existing MD5 hash scheme.

Prerequisites
-------------
- Docker stack up: ``docker compose -f docker/docker-compose.yml up -d``
- At least one ``src/consumers/fraud_detector.py`` running with the
  ABTestManager available (it is initialised best-effort in
  ``fraud_detector.py:345``).
- Both model versions registered in the ModelRegistry. For threshold
  experiments (same scorer, different decision rule), use the same
  ``--control-version`` and ``--treatment-version``; the threshold delta
  comes from the consumer's startup ``--threshold`` flag on the treatment
  instance.

Usage
-----
    # ML model A/B (e.g., production vs. retrained candidate)
    python scripts/run_ab_test_live.py \\
        --control-version 1.0.0 --treatment-version 2.0.0 \\
        --primary-metric recall --mde 0.05

    # Status of currently-running experiments
    python scripts/run_ab_test_live.py status

    # Stop an experiment immediately
    python scripts/run_ab_test_live.py stop --experiment-id exp_1747920000

What this script does NOT do
----------------------------
- It does not run the analysis on the streaming data -- ABTestManager does
  that inline as labelled outcomes arrive (every 100 samples). To inspect
  results, use ``status`` to read the persisted experiment from Redis.
- It does not start the fraud detector itself. Run the consumer separately
  (see ``CLAUDE.md`` for the canonical command).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make `ml.online_learning` importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ml.online_learning.ab_test_manager import ABTestManager  # noqa: E402


def cmd_create(args: argparse.Namespace) -> int:
    manager = ABTestManager()
    # Override the default baseline if the user supplied a better prior.
    if args.baseline is not None:
        manager.primary_metric_baseline = args.baseline

    experiment_id = manager.create_experiment(
        name=args.name or f"live: v{args.control_version} vs v{args.treatment_version}",
        description=args.description or (
            f"Comparing fraud_detector v{args.control_version} (control) "
            f"and v{args.treatment_version} (treatment) on primary metric "
            f"'{args.primary_metric}' with MDE={args.mde}"
        ),
        hypothesis=args.hypothesis or (
            f"v{args.treatment_version} improves {args.primary_metric} over "
            f"v{args.control_version} by at least {args.mde}"
        ),
        control_model=(f"fraud_detector_v{args.control_version}", args.control_version),
        treatment_model=(f"fraud_detector_v{args.treatment_version}", args.treatment_version),
        traffic_split=(args.control_alloc, 1.0 - args.control_alloc),
        primary_metric=args.primary_metric,
        minimum_effect_size=args.mde,
        significance_level=args.alpha,
    )

    if not experiment_id:
        print("Failed to create experiment", file=sys.stderr)
        return 1

    if not manager.start_experiment(experiment_id):
        print(f"Experiment {experiment_id} created but failed to start", file=sys.stderr)
        return 1

    target_n = manager.active_experiments[experiment_id].target_sample_size
    print(
        f"Live A/B test started: {experiment_id}\n"
        f"  control:   v{args.control_version} ({args.control_alloc*100:.0f}%)\n"
        f"  treatment: v{args.treatment_version} ({(1-args.control_alloc)*100:.0f}%)\n"
        f"  primary:   {args.primary_metric}\n"
        f"  MDE:       {args.mde}\n"
        f"  alpha:     {args.alpha}\n"
        f"  target n/arm (base-rate-aware): {target_n}"
    )
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    manager = ABTestManager()
    if not manager.active_experiments:
        print("No active experiments")
        return 0
    for exp in manager.active_experiments.values():
        v_summaries = []
        for v in exp.variants:
            v_summaries.append({
                "variant_id": v.variant_id,
                "model_version": v.model_version,
                "type": v.variant_type.value,
                "total_predictions": v.total_predictions,
                "precision": round(v.precision, 4),
                "recall": round(v.recall, 4),
                "f1": round(v.f1_score, 4),
                "fpr": round(v.false_positive_rate, 4),
            })
        out = {
            "experiment_id": exp.experiment_id,
            "name": exp.name,
            "status": exp.status.value,
            "primary_metric": exp.primary_metric,
            "current_sample_size": exp.current_sample_size,
            "target_sample_size": exp.target_sample_size,
            "p_value": exp.p_value,
            "effect_size": exp.effect_size,
            "decision": exp.decision_result.value,
            "winner": exp.winner,
            "variants": v_summaries,
        }
        print(json.dumps(out, indent=2))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    manager = ABTestManager()
    if args.experiment_id not in manager.active_experiments:
        print(f"No active experiment {args.experiment_id}", file=sys.stderr)
        return 1
    manager._stop_experiment_early(  # noqa: SLF001 -- explicit manual stop
        manager.active_experiments[args.experiment_id],
        reason=args.reason or "manual stop via run_ab_test_live.py",
    )
    print(f"Stopped {args.experiment_id}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="command")

    p_create = sub.add_parser("create", help="Create and start a live A/B experiment (default)")
    p_create.add_argument("--control-version", required=True)
    p_create.add_argument("--treatment-version", required=True)
    p_create.add_argument("--control-alloc", type=float, default=0.5,
                          help="Traffic fraction assigned to control (default 0.5)")
    p_create.add_argument("--primary-metric", default="recall",
                          choices=["recall", "fpr", "precision", "f1"])
    p_create.add_argument("--mde", type=float, default=0.05,
                          help="Minimum detectable effect size (absolute, default 0.05)")
    p_create.add_argument("--alpha", type=float, default=0.05)
    p_create.add_argument("--baseline", type=float, default=None,
                          help="Override the prior baseline rate used for sample-size sizing")
    p_create.add_argument("--name", default=None)
    p_create.add_argument("--description", default=None)
    p_create.add_argument("--hypothesis", default=None)

    sub.add_parser("status", help="Show currently-running experiments")

    p_stop = sub.add_parser("stop", help="Stop a running experiment")
    p_stop.add_argument("--experiment-id", required=True)
    p_stop.add_argument("--reason", default=None)

    args = ap.parse_args()
    if args.command in (None, "create"):
        if args.command is None:
            ap.error("subcommand required (create | status | stop)")
        return cmd_create(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "stop":
        return cmd_stop(args)
    ap.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
