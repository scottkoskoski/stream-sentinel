#!/usr/bin/env python3
"""
Model Deployment CLI for Stream-Sentinel

Register, promote, rollback, and A/B test fraud detection models
via the ModelRegistry and ABTestManager.

Usage:
    python scripts/deploy_model.py register --model-path models/new.pkl --version 2.0.0
    python scripts/deploy_model.py promote  --version 2.0.0 --strategy canary --traffic-pct 10
    python scripts/deploy_model.py promote  --version 2.0.0 --strategy full
    python scripts/deploy_model.py rollback --to-version 1.0.0
    python scripts/deploy_model.py status
    python scripts/deploy_model.py ab-test  --control 1.0.0 --treatment 2.0.0 --split 80/20
"""

import argparse
import json
import logging
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ml.online_learning.ab_test_manager import ABTestManager
from ml.online_learning.model_registry import (
    DeploymentStage,
    ModelMetadata,
    ModelRegistry,
    ModelStatus,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("deploy_model")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_model_pickle(path: str):
    """Load a model pickle file and return the raw data."""
    resolved = Path(path).resolve()
    if not resolved.is_file():
        logger.error("Model file not found: %s", resolved)
        sys.exit(1)
    with open(resolved, "rb") as f:
        return pickle.load(f)


def _build_metadata(version: str, model_path: str) -> ModelMetadata:
    """Build ModelMetadata suitable for registration."""
    now = datetime.now().isoformat()
    return ModelMetadata(
        model_id=f"fraud_detector_v{version}",
        version=version,
        name=f"Fraud Detector v{version}",
        description=f"Model registered from {model_path}",
        model_type="xgboost",
        algorithm="XGBClassifier",
        framework="xgboost",
        training_data_hash="n/a",
        performance_metrics={},
        validation_metrics={},
        training_start_time=now,
        training_end_time=now,
        training_duration_minutes=0.0,
        training_samples=0,
        status=ModelStatus.STAGING,
        deployment_stage=DeploymentStage.STAGING_DEPLOYMENT,
        training_trigger="manual",
    )


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------


def cmd_register(args):
    """Register a new model version in the registry."""
    registry = ModelRegistry()

    model_data = _load_model_pickle(args.model_path)
    metadata = _build_metadata(args.version, args.model_path)

    success = registry.register_model(model_data, metadata)
    if success:
        print(f"Registered model {metadata.model_id} v{args.version}")
    else:
        print("Registration failed -- check logs for details")
        sys.exit(1)


def cmd_promote(args):
    """Promote a registered model to production."""
    registry = ModelRegistry()

    model_id = f"fraud_detector_v{args.version}"
    if model_id not in registry.registered_models:
        print(f"Model {model_id} not found in registry. Register it first.")
        sys.exit(1)

    if args.strategy == "full":
        traffic_pct = 100.0
    else:
        traffic_pct = args.traffic_pct

    success = registry.deploy_model(
        model_id=model_id,
        environment="production",
        traffic_percentage=traffic_pct,
        deployment_strategy=args.strategy,
    )
    if success:
        print(
            f"Promoted {model_id} to production "
            f"(strategy={args.strategy}, traffic={traffic_pct}%)"
        )
    else:
        print("Promotion failed -- check logs for details")
        sys.exit(1)


def cmd_rollback(args):
    """Rollback production to a specific version."""
    registry = ModelRegistry()

    target_model_id = f"fraud_detector_v{args.to_version}"
    if target_model_id not in registry.registered_models:
        print(f"Target model {target_model_id} not found in registry.")
        sys.exit(1)

    success = registry.deploy_model(
        model_id=target_model_id,
        environment="production",
        traffic_percentage=100.0,
        deployment_strategy="rollback",
    )
    if success:
        print(f"Rolled back production to {target_model_id}")
    else:
        print("Rollback failed -- check logs for details")
        sys.exit(1)


def cmd_status(args):
    """Print registry and deployment status."""
    registry = ModelRegistry()

    print("\n=== Model Registry Status ===")
    stats = registry.get_registry_statistics()
    print(f"Total models:       {stats['total_models']}")
    print(f"Active deployments: {stats['active_deployments']}")
    print(f"Status distribution: {stats['status_distribution']}")

    print("\n--- Active Deployments ---")
    for env, deployment in registry.active_deployments.items():
        if deployment:
            print(
                f"  {env}: model={deployment['model_id']}  "
                f"version={deployment['version']}  "
                f"traffic={deployment['traffic_percentage']}%  "
                f"deployed_at={deployment['deployed_at']}"
            )
        else:
            print(f"  {env}: (none)")

    print("\n--- Registered Models ---")
    for model_id, metadata in registry.registered_models.items():
        print(
            f"  {model_id}  v{metadata.version}  "
            f"status={metadata.status.value}  "
            f"stage={metadata.deployment_stage.value}"
        )

    # A/B test status
    try:
        ab_manager = ABTestManager()
        experiments = ab_manager.list_experiments()
        if experiments:
            print("\n--- A/B Test Experiments ---")
            for exp in experiments:
                print(
                    f"  {exp['experiment_id']}  name={exp['name']}  "
                    f"status={exp['status']}  "
                    f"samples={exp['current_sample_size']}  "
                    f"decision={exp['decision_result']}"
                )
        else:
            print("\nNo A/B test experiments found.")
    except Exception as e:
        print(f"\nA/B test manager unavailable: {e}")

    print()


def cmd_ab_test(args):
    """Create and start an A/B test between two model versions."""
    # Parse the split (e.g. "80/20")
    try:
        parts = args.split.split("/")
        control_pct = float(parts[0]) / 100.0
        treatment_pct = float(parts[1]) / 100.0
    except (IndexError, ValueError):
        print(f"Invalid split format '{args.split}'. Expected e.g. '80/20'")
        sys.exit(1)

    if abs((control_pct + treatment_pct) - 1.0) > 0.01:
        print(f"Split must sum to 100, got {args.split}")
        sys.exit(1)

    control_model_id = f"fraud_detector_v{args.control}"
    treatment_model_id = f"fraud_detector_v{args.treatment}"

    ab_manager = ABTestManager()

    experiment_id = ab_manager.create_experiment(
        name=f"AB test: v{args.control} vs v{args.treatment}",
        description=f"Comparing model versions {args.control} and {args.treatment}",
        hypothesis=f"v{args.treatment} improves fraud detection over v{args.control}",
        control_model=(control_model_id, args.control),
        treatment_model=(treatment_model_id, args.treatment),
        traffic_split=(control_pct, treatment_pct),
        primary_metric=args.metric,
    )

    if not experiment_id:
        print("Failed to create experiment")
        sys.exit(1)

    # Start it immediately
    started = ab_manager.start_experiment(experiment_id)
    if started:
        print(
            f"A/B test started: {experiment_id}  "
            f"control=v{args.control} ({control_pct*100:.0f}%)  "
            f"treatment=v{args.treatment} ({treatment_pct*100:.0f}%)  "
            f"metric={args.metric}"
        )
    else:
        print(f"Experiment created ({experiment_id}) but failed to start")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Stream-Sentinel model deployment CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- register ---
    p_register = sub.add_parser("register", help="Register a model in the registry")
    p_register.add_argument(
        "--model-path", required=True, help="Path to model .pkl file"
    )
    p_register.add_argument(
        "--version", required=True, help="Semantic version (e.g. 2.0.0)"
    )

    # --- promote ---
    p_promote = sub.add_parser("promote", help="Promote a model to production")
    p_promote.add_argument("--version", required=True, help="Model version to promote")
    p_promote.add_argument(
        "--strategy",
        choices=["full", "canary", "blue_green", "rolling"],
        default="full",
        help="Deployment strategy (default: full)",
    )
    p_promote.add_argument(
        "--traffic-pct",
        type=float,
        default=100.0,
        help="Traffic percentage for canary/rolling (default: 100)",
    )

    # --- rollback ---
    p_rollback = sub.add_parser("rollback", help="Rollback production to a version")
    p_rollback.add_argument(
        "--to-version", required=True, help="Version to roll back to"
    )

    # --- status ---
    sub.add_parser("status", help="Show registry and deployment status")

    # --- ab-test ---
    p_ab = sub.add_parser("ab-test", help="Create and start an A/B test")
    p_ab.add_argument("--control", required=True, help="Control model version")
    p_ab.add_argument("--treatment", required=True, help="Treatment model version")
    p_ab.add_argument(
        "--split",
        default="50/50",
        help="Traffic split as control/treatment (e.g. 80/20, default: 50/50)",
    )
    p_ab.add_argument(
        "--metric",
        default="f1",
        choices=["precision", "recall", "f1", "auc", "business_value"],
        help="Primary metric (default: f1)",
    )

    args = parser.parse_args()

    dispatch = {
        "register": cmd_register,
        "promote": cmd_promote,
        "rollback": cmd_rollback,
        "status": cmd_status,
        "ab-test": cmd_ab_test,
    }

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
