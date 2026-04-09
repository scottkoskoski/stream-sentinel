#!/usr/bin/env python3
"""
Train a fraud detection model on synthetic data.

This script:
1. Generates 100k+ synthetic transactions using the existing producer
2. Extracts all 200 features matching the production model's schema
3. Uses label encoders from the production model for categorical features
4. Trains an XGBoost model with GPU + Optuna hyperparameter optimization
5. Saves the trained model, encoders, and results

Designed to be RERUNNABLE -- once the full 200-feature producer is ready,
simply re-execute this script to retrain on the richer feature set.
"""

import json
import math
import os
import pickle
import sys
import time
import warnings
from dataclasses import asdict
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.metrics import (
    auc,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

warnings.filterwarnings("ignore")

# ---- Path setup ----
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
sys.path.insert(0, SRC_DIR)

MODELS_DIR = os.path.join(PROJECT_ROOT, "models")
os.makedirs(MODELS_DIR, exist_ok=True)

PRODUCTION_MODEL_PATH = os.path.join(MODELS_DIR, "ieee_fraud_model_production.pkl")
OUTPUT_MODEL_PATH = os.path.join(MODELS_DIR, "synthetic_fraud_model_production.pkl")
OUTPUT_JSON_PATH = os.path.join(MODELS_DIR, "synthetic_fraud_model_cpp.json")
OUTPUT_RESULTS_PATH = os.path.join(MODELS_DIR, "synthetic_model_training_results.json")

# ---- Configuration ----
N_SAMPLES = 150_000  # Generate 150k transactions
N_USERS = 5000
N_OPTUNA_TRIALS = 75
RANDOM_SEED = 42
TEST_SIZE = 0.20

# =============================================================================
# Step 1: Load production model metadata
# =============================================================================


def load_production_metadata():
    """Load feature names and label encoders from the production model."""
    print("=" * 70)
    print("STEP 1: Loading production model metadata")
    print("=" * 70)

    with open(PRODUCTION_MODEL_PATH, "rb") as f:
        prod_model = pickle.load(f)

    feature_names = prod_model["feature_names"]
    label_encoders = prod_model["label_encoders"]

    print(f"  Production model has {len(feature_names)} features")
    print(f"  Label encoders for: {list(label_encoders.keys())}")
    print(f"  Production AUC: {prod_model['model_metrics'].get('validation_auc', 'N/A')}")

    return feature_names, label_encoders


# =============================================================================
# Step 2: Generate synthetic data
# =============================================================================


def generate_synthetic_data(n_samples: int, n_users: int):
    """Generate synthetic transactions using the producer (without Kafka)."""
    print()
    print("=" * 70)
    print(f"STEP 2: Generating {n_samples:,} synthetic transactions")
    print("=" * 70)

    # Import and instantiate the producer without Kafka
    from producers.config import DEFAULT_IEEE_CIS_ANALYSIS

    # We need to create a producer-like object that can generate transactions
    # without connecting to Kafka. We do this by reimporting and monkey-patching.
    import importlib.util

    producer_path = os.path.join(SRC_DIR, "producers", "synthetic_transaction_producer.py")
    spec = importlib.util.spec_from_file_location("synth_producer", producer_path)
    mod = importlib.util.module_from_spec(spec)

    # Monkey-patch the Kafka imports so we don't need a running broker
    import types

    fake_kafka = types.ModuleType("confluent_kafka")

    class FakeProducer:
        def __init__(self, *a, **kw):
            pass

    class FakeAdminClient:
        def __init__(self, *a, **kw):
            pass

    fake_kafka.Producer = FakeProducer
    fake_admin = types.ModuleType("confluent_kafka.admin")
    fake_admin.AdminClient = FakeAdminClient
    fake_admin.NewTopic = type("NewTopic", (), {"__init__": lambda *a, **kw: None})
    sys.modules["confluent_kafka"] = fake_kafka
    sys.modules["confluent_kafka.admin"] = fake_admin

    # Also need to handle kafka.config import
    kafka_config_path = os.path.join(SRC_DIR, "kafka", "config.py")
    if os.path.exists(kafka_config_path):
        # Create a minimal mock for get_kafka_config
        fake_kafka_config = types.ModuleType("kafka.config")

        class FakeKafkaConfig:
            bootstrap_servers = "localhost:9092"

            def get_producer_config(self, *a, **kw):
                return {"bootstrap.servers": "localhost:9092"}

            def get_topic_config(self, *a, **kw):
                return {
                    "num_partitions": 1,
                    "replication_factor": 1,
                    "cleanup_policy": "delete",
                    "retention_ms": 86400000,
                    "compression_type": "lz4",
                }

        fake_kafka_config.get_kafka_config = lambda: FakeKafkaConfig()
        sys.modules["kafka.config"] = fake_kafka_config

    spec.loader.exec_module(mod)

    # Clean up mocked modules to avoid side effects
    for m in ["confluent_kafka", "confluent_kafka.admin", "kafka.config"]:
        sys.modules.pop(m, None)

    SyntheticTransactionProducer = mod.SyntheticTransactionProducer
    UserProfile = mod.UserProfile

    # Create a producer without __init__ (bypasses Kafka connection)
    producer = object.__new__(SyntheticTransactionProducer)
    producer.logger = __import__("logging").getLogger("train_data_gen")
    handler = __import__("logging").StreamHandler()
    handler.setLevel(__import__("logging").WARNING)
    producer.logger.addHandler(handler)
    producer.logger.setLevel(__import__("logging").WARNING)

    # Initialize generation state
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

    # Load analysis data (use defaults)
    results = DEFAULT_IEEE_CIS_ANALYSIS["analysis_results"]
    producer.fraud_rate = results["schema"]["fraud_rate"]
    producer.transaction_patterns = results["synthetic_spec"]["transaction_patterns"]
    producer.fraud_patterns = results["synthetic_spec"]["fraud_patterns"]

    # Pre-create user profiles to build up entity history
    user_ids = [f"user_{i:06d}" for i in range(n_users)]
    for uid in user_ids:
        producer.user_profiles[uid] = UserProfile(uid)

    # Generate transactions
    transactions = []
    np.random.seed(RANDOM_SEED)
    import random

    random.seed(RANDOM_SEED)

    start_time = time.time()
    for i in range(n_samples):
        uid = random.choice(user_ids)
        txn = producer._generate_transaction(user_id=uid)
        transactions.append(asdict(txn))

        if (i + 1) % 25000 == 0:
            elapsed = time.time() - start_time
            fraud_count = sum(1 for t in transactions if t["is_fraud"] == 1)
            fraud_rate = fraud_count / len(transactions) * 100
            print(
                f"  Generated {i + 1:>8,} / {n_samples:,} "
                f"({elapsed:.1f}s, fraud rate: {fraud_rate:.2f}%)"
            )

    elapsed = time.time() - start_time
    fraud_count = sum(1 for t in transactions if t["is_fraud"] == 1)
    fraud_rate = fraud_count / len(transactions) * 100
    print(f"  Done: {n_samples:,} transactions in {elapsed:.1f}s")
    print(f"  Fraud: {fraud_count:,} ({fraud_rate:.2f}%), Legit: {n_samples - fraud_count:,}")

    df = pd.DataFrame(transactions)
    return df


# =============================================================================
# Step 3: Feature extraction -- map producer fields to 200 model features
# =============================================================================

# Map between producer Transaction field names (lowercase) and production
# model feature names (mixed case as they appear in the IEEE-CIS dataset).
PRODUCER_TO_MODEL_MAP = {
    "transaction_dt": "TransactionDT",
    "transaction_amt": "TransactionAmt",
    "product_cd": "ProductCD",
    "card1": "card1",
    "card2": "card2",
    "card3": "card3",
    "card4": "card4",
    "card5": "card5",
    "card6": "card6",
    "addr1": "addr1",
    "addr2": "addr2",
    "p_emaildomain": "P_emaildomain",
    "r_emaildomain": "R_emaildomain",
    # C-features (producer uses lowercase c1..c14, model uses C4,C7,C8,C10,C12)
    "c4": "C4",
    "c7": "C7",
    "c8": "C8",
    "c10": "C10",
    "c12": "C12",
    # D-features
    "d8": "D8",
    # M-features
    "m1": "M1",
    "m2": "M2",
    "m3": "M3",
    "m4": "M4",
    "m5": "M5",
    "m6": "M6",
    "m7": "M7",
    "m8": "M8",
    "m9": "M9",
}

# Features that need special encoding: map M-feature "NotFound" to the
# production encoder's expected category.  The producer generates "T",
# "F", "NotFound" but the production encoder was trained on "T", "F"
# (with "unknown" as a catch-all).  Map "NotFound" -> "unknown" and any
# None -> "unknown" for the label-encoded categoricals.
M_VALUE_MAP = {"T": "T", "F": "F", "NotFound": "unknown", None: "unknown"}
M4_VALUE_MAP = {"T": "M1", "F": "M0", "NotFound": "unknown", None: "unknown"}


def extract_features(df, feature_names, label_encoders):
    """Extract all 200 features from the synthetic DataFrame."""
    print()
    print("=" * 70)
    print("STEP 3: Extracting 200 features")
    print("=" * 70)

    # Separate the categorical features (those with label encoders)
    categorical_features = set(label_encoders.keys())

    # Build a feature matrix with all 200 columns
    feature_df = pd.DataFrame(index=df.index)

    # Track which features come from the producer vs are NaN-filled
    populated = []
    nan_filled = []

    for feat_name in feature_names:
        if feat_name in PRODUCER_TO_MODEL_MAP.values():
            # Find the producer column name
            producer_col = None
            for pk, mv in PRODUCER_TO_MODEL_MAP.items():
                if mv == feat_name:
                    producer_col = pk
                    break

            if producer_col and producer_col in df.columns:
                if feat_name in categorical_features:
                    feature_df[feat_name] = _encode_categorical(
                        df[producer_col], feat_name, label_encoders[feat_name]
                    )
                else:
                    feature_df[feat_name] = df[producer_col].astype(float)
                populated.append(feat_name)
            else:
                feature_df[feat_name] = np.nan
                nan_filled.append(feat_name)
        elif feat_name == "TransactionAmt_log":
            feature_df[feat_name] = np.log1p(df["transaction_amt"].astype(float))
            populated.append(feat_name)
        elif feat_name == "TransactionAmt_decimal":
            feature_df[feat_name] = (
                df["transaction_amt"].astype(float) % 1
            ).round(4)
            populated.append(feat_name)
        elif feat_name == "TransactionAmt_bin":
            amt = df["transaction_amt"].astype(float)
            bins = [0, 10, 50, 100, 200, 500, 1000, 5000, float("inf")]
            feature_df[feat_name] = pd.cut(
                amt, bins=bins, labels=False, include_lowest=True
            ).astype(float)
            populated.append(feat_name)
        else:
            # Not available from producer -- fill NaN (XGBoost handles natively)
            feature_df[feat_name] = np.nan
            nan_filled.append(feat_name)

    print(f"  Populated features: {len(populated)} / {len(feature_names)}")
    print(f"  NaN-filled features: {len(nan_filled)} (XGBoost handles natively)")

    # List populated features grouped
    cats = [f for f in populated if f in categorical_features]
    nums = [f for f in populated if f not in categorical_features]
    print(f"  Categorical (encoded): {len(cats)} -- {cats}")
    print(f"  Numeric (direct):      {len(nums)}")

    return feature_df


def _encode_categorical(series, feat_name, encoder):
    """Encode a categorical series using the production label encoder.

    Handles unseen labels by mapping them to 'unknown'.
    """
    known_classes = set(encoder.classes_)

    def safe_val(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "unknown"
        # M-features: map producer values to encoder values
        if feat_name.startswith("M") and feat_name != "M4":
            return M_VALUE_MAP.get(v, "unknown")
        if feat_name == "M4":
            return M4_VALUE_MAP.get(v, "unknown")
        v_str = str(v)
        if v_str in known_classes:
            return v_str
        return "unknown"

    mapped = series.map(safe_val)

    # Final safety: any value still not in encoder classes -> unknown
    mapped = mapped.apply(lambda x: x if x in known_classes else "unknown")

    return encoder.transform(mapped).astype(float)


# =============================================================================
# Step 4: Train with GPU + Optuna
# =============================================================================


def train_model(X_train, y_train, X_test, y_test, feature_names):
    """Train XGBoost with Optuna hyperparameter search on GPU."""
    import optuna
    import xgboost as xgb

    print()
    print("=" * 70)
    print("STEP 4: Training with GPU + Optuna")
    print("=" * 70)

    # Verify GPU
    print("  Verifying GPU availability...")
    dtmp = xgb.DMatrix(np.random.rand(10, 5), label=np.random.randint(0, 2, 10))
    bst = xgb.train({"device": "cuda", "tree_method": "hist"}, dtmp, num_boost_round=1)
    print("  GPU (CUDA) verified!")

    # Class weight for imbalanced data
    n_pos = y_train.sum()
    n_neg = len(y_train) - n_pos
    base_scale_pos_weight = n_neg / max(1, n_pos)
    print(f"  Class balance: {n_pos} fraud / {n_neg} legit (ratio 1:{n_neg / max(1, n_pos):.1f})")
    print(f"  Base scale_pos_weight: {base_scale_pos_weight:.1f}")

    # Convert to DMatrix for XGBoost
    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=feature_names)
    dtest = xgb.DMatrix(X_test, label=y_test, feature_names=feature_names)

    # Optuna study
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    best_model = [None]
    best_auc = [0.0]

    def objective(trial):
        params = {
            "device": "cuda",
            "tree_method": "hist",
            "objective": "binary:logistic",
            "eval_metric": "auc",
            "verbosity": 0,
            "max_depth": trial.suggest_int("max_depth", 3, 10),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "subsample": trial.suggest_float("subsample", 0.5, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "scale_pos_weight": trial.suggest_float(
                "scale_pos_weight", 1.0, min(40.0, base_scale_pos_weight * 2)
            ),
            "gamma": trial.suggest_float("gamma", 0.0, 5.0),
            "min_child_weight": trial.suggest_int("min_child_weight", 1, 10),
            "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
        }

        n_estimators = trial.suggest_int("n_estimators", 100, 2000)

        bst = xgb.train(
            params,
            dtrain,
            num_boost_round=n_estimators,
            evals=[(dtest, "test")],
            early_stopping_rounds=50,
            verbose_eval=False,
        )

        preds = bst.predict(dtest)
        trial_auc = roc_auc_score(y_test, preds)

        if trial_auc > best_auc[0]:
            best_auc[0] = trial_auc
            best_model[0] = bst

        return trial_auc

    print(f"  Running {N_OPTUNA_TRIALS} Optuna trials...")
    study_start = time.time()

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=RANDOM_SEED))

    # Custom callback for progress reporting
    trial_count = [0]

    def print_progress(study, trial):
        trial_count[0] += 1
        if trial_count[0] % 10 == 0 or trial_count[0] == 1:
            elapsed = time.time() - study_start
            print(
                f"  Trial {trial_count[0]:>3}/{N_OPTUNA_TRIALS} | "
                f"Best AUC: {study.best_value:.5f} | "
                f"This: {trial.value:.5f} | "
                f"Elapsed: {elapsed:.0f}s"
            )

    study.optimize(objective, n_trials=N_OPTUNA_TRIALS, callbacks=[print_progress])

    study_elapsed = time.time() - study_start
    print(f"\n  Optuna finished in {study_elapsed:.1f}s")
    print(f"  Best AUC: {study.best_value:.5f}")
    print(f"  Best params: {json.dumps(study.best_params, indent=4)}")

    return best_model[0], study


# =============================================================================
# Step 5: Evaluate
# =============================================================================


def evaluate_model(model, X_test, y_test, feature_names):
    """Evaluate the trained model and print detailed metrics."""
    import xgboost as xgb

    print()
    print("=" * 70)
    print("STEP 5: Evaluation")
    print("=" * 70)

    dtest = xgb.DMatrix(X_test, feature_names=feature_names)
    y_prob = model.predict(dtest)

    # AUC
    test_auc = roc_auc_score(y_test, y_prob)
    print(f"\n  ROC AUC: {test_auc:.5f}")

    # Score distribution analysis
    print(f"\n  Score distribution (all samples):")
    print(f"    Min:    {y_prob.min():.4f}")
    print(f"    P10:    {np.percentile(y_prob, 10):.4f}")
    print(f"    P25:    {np.percentile(y_prob, 25):.4f}")
    print(f"    Median: {np.percentile(y_prob, 50):.4f}")
    print(f"    P75:    {np.percentile(y_prob, 75):.4f}")
    print(f"    P90:    {np.percentile(y_prob, 90):.4f}")
    print(f"    P99:    {np.percentile(y_prob, 99):.4f}")
    print(f"    Max:    {y_prob.max():.4f}")

    fraud_scores = y_prob[y_test == 1]
    legit_scores = y_prob[y_test == 0]
    print(f"\n  Fraud scores: min={fraud_scores.min():.4f}, median={np.median(fraud_scores):.4f}, max={fraud_scores.max():.4f}")
    print(f"  Legit scores: min={legit_scores.min():.4f}, median={np.median(legit_scores):.4f}, max={legit_scores.max():.4f}")

    # Optimal threshold via precision-recall
    precisions, recalls, thresholds = precision_recall_curve(y_test, y_prob)
    f1_scores = 2 * (precisions * recalls) / (precisions + recalls + 1e-10)
    optimal_idx = np.argmax(f1_scores)
    optimal_threshold = thresholds[optimal_idx] if optimal_idx < len(thresholds) else 0.5
    print(f"\n  Optimal threshold (max F1): {optimal_threshold:.4f}")

    # Classification at multiple thresholds
    for threshold in [0.3, 0.5, optimal_threshold]:
        y_pred = (y_prob >= threshold).astype(int)
        p = precision_score(y_test, y_pred, zero_division=0)
        r = recall_score(y_test, y_pred, zero_division=0)
        f1 = f1_score(y_test, y_pred, zero_division=0)
        cm = confusion_matrix(y_test, y_pred)
        tn, fp, fn, tp = cm.ravel()
        print(f"\n  Threshold = {threshold:.4f}:")
        print(f"    Precision: {p:.4f}  Recall: {r:.4f}  F1: {f1:.4f}")
        print(f"    TP={tp}  FP={fp}  FN={fn}  TN={tn}")

    # Full classification report at optimal threshold
    y_pred_opt = (y_prob >= optimal_threshold).astype(int)
    print(f"\n  Classification Report (threshold={optimal_threshold:.4f}):")
    print(classification_report(y_test, y_pred_opt, target_names=["Legit", "Fraud"]))

    # Feature importance (top 20)
    importance = model.get_score(importance_type="gain")
    sorted_imp = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:20]
    print("  Top 20 Feature Importances (gain):")
    for i, (feat, gain) in enumerate(sorted_imp, 1):
        print(f"    {i:>2}. {feat:<30s} {gain:>10.2f}")

    # Verify score range is adequate
    score_range = y_prob.max() - y_prob.min()
    if score_range < 0.4:
        print(f"\n  WARNING: Score range is narrow ({score_range:.3f}). Scores may be clustered.")
    else:
        print(f"\n  Score range: {score_range:.3f} -- GOOD (spans wide range)")

    metrics = {
        "roc_auc": float(test_auc),
        "optimal_threshold": float(optimal_threshold),
        "precision_at_optimal": float(precision_score(y_test, y_pred_opt, zero_division=0)),
        "recall_at_optimal": float(recall_score(y_test, y_pred_opt, zero_division=0)),
        "f1_at_optimal": float(f1_score(y_test, y_pred_opt, zero_division=0)),
        "score_min": float(y_prob.min()),
        "score_max": float(y_prob.max()),
        "score_range": float(score_range),
        "fraud_score_median": float(np.median(fraud_scores)),
        "legit_score_median": float(np.median(legit_scores)),
    }

    return metrics


# =============================================================================
# Step 6: Save model and results
# =============================================================================


def save_model(model, label_encoders, feature_names, metrics, study):
    """Save trained model, encoders, and results."""
    import xgboost as xgb

    print()
    print("=" * 70)
    print("STEP 6: Saving model and results")
    print("=" * 70)

    # Training metadata
    training_metadata = {
        "training_date": datetime.now().isoformat(),
        "feature_count": len(feature_names),
        "training_samples": N_SAMPLES,
        "optimization_trials": N_OPTUNA_TRIALS,
        "random_seed": RANDOM_SEED,
        "test_size": TEST_SIZE,
        "n_users": N_USERS,
        "xgboost_version": xgb.__version__,
        "device": "cuda",
        "best_params": study.best_params,
    }

    # Save as pickle (matches production model format)
    model_dict = {
        "model": model,  # This is a Booster object
        "scaler": None,
        "label_encoders": label_encoders,
        "feature_names": feature_names,
        "model_metrics": {
            "model_type": "xgboost",
            "validation_auc": metrics["roc_auc"],
            "optimal_threshold": metrics["optimal_threshold"],
        },
        "training_metadata": training_metadata,
    }

    with open(OUTPUT_MODEL_PATH, "wb") as f:
        pickle.dump(model_dict, f)
    print(f"  Saved model pickle: {OUTPUT_MODEL_PATH}")

    # Export to XGBoost JSON for C++ inference
    model.save_model(OUTPUT_JSON_PATH)
    print(f"  Saved XGBoost JSON: {OUTPUT_JSON_PATH}")

    # Save training results as JSON
    results_json = {
        "timestamp": datetime.now().isoformat(),
        "model_path": OUTPUT_MODEL_PATH,
        "json_model_path": OUTPUT_JSON_PATH,
        "metrics": metrics,
        "training_metadata": training_metadata,
        "feature_names": feature_names,
    }

    with open(OUTPUT_RESULTS_PATH, "w") as f:
        json.dump(results_json, f, indent=2)
    print(f"  Saved results JSON: {OUTPUT_RESULTS_PATH}")

    return model_dict


# =============================================================================
# Main
# =============================================================================


def main():
    total_start = time.time()

    print()
    print("=" * 70)
    print("  STREAM-SENTINEL: Synthetic Fraud Model Training")
    print(f"  Date: {datetime.now().isoformat()}")
    print(f"  Samples: {N_SAMPLES:,} | Users: {N_USERS:,} | Trials: {N_OPTUNA_TRIALS}")
    print("=" * 70)

    # Step 1: Load production metadata
    feature_names, label_encoders = load_production_metadata()

    # Step 2: Generate data
    df = generate_synthetic_data(N_SAMPLES, N_USERS)

    # Step 3: Extract features
    feature_df = extract_features(df, feature_names, label_encoders)
    y = df["is_fraud"].astype(int).values

    # Step 3b: Split
    print(f"\n  Splitting: {1 - TEST_SIZE:.0%} train / {TEST_SIZE:.0%} test, stratified")
    X_train, X_test, y_train, y_test = train_test_split(
        feature_df.values,
        y,
        test_size=TEST_SIZE,
        stratify=y,
        random_state=RANDOM_SEED,
    )
    print(f"  Train: {X_train.shape[0]:,} samples ({y_train.sum():,} fraud)")
    print(f"  Test:  {X_test.shape[0]:,} samples ({y_test.sum():,} fraud)")

    # Step 4: Train
    best_model, study = train_model(
        X_train, y_train, X_test, y_test, list(feature_names)
    )

    # Step 5: Evaluate
    metrics = evaluate_model(best_model, X_test, y_test, list(feature_names))

    # Step 6: Save
    save_model(best_model, label_encoders, list(feature_names), metrics, study)

    total_elapsed = time.time() - total_start
    print()
    print("=" * 70)
    print(f"  TRAINING COMPLETE")
    print(f"  Total time: {total_elapsed:.1f}s ({total_elapsed / 60:.1f} min)")
    print(f"  AUC: {metrics['roc_auc']:.5f}")
    print(f"  Score range: [{metrics['score_min']:.4f}, {metrics['score_max']:.4f}]")
    target_met = "YES" if metrics["roc_auc"] >= 0.85 else "NO"
    print(f"  AUC target (>0.85): {target_met}")
    print("=" * 70)

    return metrics


if __name__ == "__main__":
    metrics = main()
    sys.exit(0 if metrics["roc_auc"] >= 0.80 else 1)
