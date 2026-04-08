#!/usr/bin/env python3

"""
Model Export Utility for Stream-Sentinel C++ Inference

Converts a pickled Python XGBoost model to native XGBoost JSON format
so that the C API (and therefore the C++ wrapper) can load it directly.

Usage:
    python src/inference/export_model.py [--input PATH] [--output PATH]

Default input:  models/ieee_fraud_model_production.pkl
Default output: models/ieee_fraud_model_production_cpp.json
"""

import argparse
import json
import logging
import pickle
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def export_model(input_path: str, output_path: str) -> None:
    """Load a pickled XGBoost model and re-save it in native JSON format.

    The pickle may contain either:
      - A bare ``xgboost.XGBClassifier`` / ``XGBRegressor``
      - A dict with a ``'model'`` key holding the estimator

    The native JSON file produced by ``booster.save_model()`` is what the
    XGBoost C API expects when calling ``XGBoosterLoadModel()``.

    Args:
        input_path: Path to the ``.pkl`` file.
        output_path: Destination ``.json`` file for the native model.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        logger.error("Input model not found: %s", input_path)
        sys.exit(1)

    logger.info("Loading pickled model from %s", input_path)
    with open(input_path, "rb") as f:
        model_data = pickle.load(f)

    # Extract the estimator from dict wrapper if needed
    if isinstance(model_data, dict):
        estimator = model_data.get("model")
        if estimator is None:
            logger.error(
                "Pickle dict does not contain a 'model' key. Keys: %s",
                list(model_data.keys()),
            )
            sys.exit(1)
        logger.info("Extracted estimator from dict (type: %s)", type(estimator).__name__)
    else:
        estimator = model_data
        logger.info("Loaded estimator directly (type: %s)", type(estimator).__name__)

    # Get the underlying Booster object.  XGBClassifier exposes it via
    # .get_booster(); a raw Booster is already the right type.
    try:
        import xgboost as xgb
    except ImportError:
        logger.error("xgboost package is required: pip install xgboost")
        sys.exit(1)

    if isinstance(estimator, xgb.Booster):
        booster = estimator
    elif hasattr(estimator, "get_booster"):
        booster = estimator.get_booster()
    else:
        logger.error(
            "Unsupported estimator type: %s (expected XGBClassifier or Booster)",
            type(estimator).__name__,
        )
        sys.exit(1)

    # Save in native JSON format -- this is what XGBoosterLoadModel() reads.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(output_path))
    logger.info("Exported native XGBoost JSON model to %s", output_path)

    # Verify the file was written and is non-trivial
    size_kb = output_path.stat().st_size / 1024
    logger.info("Output file size: %.1f KB", size_kb)

    # Optionally export metadata alongside the model
    meta_path = output_path.with_suffix(".meta.json")
    metadata = {
        "source_pickle": str(input_path),
        "format": "xgboost_json",
        "num_features": int(booster.num_features()),
    }
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Wrote metadata to %s", meta_path)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Export pickled XGBoost model to native JSON format for C++ inference"
    )
    parser.add_argument(
        "--input", "-i",
        default="models/ieee_fraud_model_production.pkl",
        help="Path to the pickled model (.pkl)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output path for native JSON model (default: <input>_cpp.json)",
    )
    args = parser.parse_args()

    if args.output is None:
        args.output = args.input.replace(".pkl", "_cpp.json")

    export_model(args.input, args.output)


if __name__ == "__main__":
    main()
