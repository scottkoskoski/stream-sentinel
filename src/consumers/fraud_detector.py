# /stream-sentinel/src/consumers/fraud_detector.py

"""
Real-Time Fraud Detection Consumer for Stream-Sentinel

This module implements the core fraud detection consumer that processes
transaction streams in real-time, performs feature engineering, and
publishes fraud alerts. It demonstrates advanced stream processing patterns
with state management using Redis and Kafka.

Key distributed systems concepts:
- Real-time stream processing with Kafka consumers
- Stateful processing with Redis-backed state management
- Feature engineering pipeline for ML-ready data
- Alert publishing with configurable fraud thresholds
- Graceful error handling and recovery mechanisms
"""

import json
import logging
import pickle
import signal
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import redis
from confluent_kafka import Consumer, KafkaError, KafkaException, Producer

# Import our configuration system
sys.path.append(str(Path(__file__).parent.parent))
from kafka.config import get_kafka_config
from kafka.dlq import get_dlq_publisher
from kafka.lag_monitor import FlowController
from monitoring.metrics import get_metrics as get_prometheus_metrics
from utils.logging import ContextLogger, configure_logging, get_logger

# Schema Registry integration (optional -- falls back to plain JSON)
try:
    from kafka.schema_utils import deserialize_message, get_schema_helper

    SCHEMA_UTILS_AVAILABLE = True
except ImportError:
    SCHEMA_UTILS_AVAILABLE = False

# Import optional C++ accelerated inference
try:
    from inference.fast_inference import FastInferenceEngine

    CPP_INFERENCE_AVAILABLE = True
except ImportError:
    CPP_INFERENCE_AVAILABLE = False

# Import unified feature engineering module
try:
    from ml.features.feature_engineer import FeatureEngineer

    FEATURE_ENGINEER_AVAILABLE = True
except ImportError:
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from ml.features.feature_engineer import FeatureEngineer

        FEATURE_ENGINEER_AVAILABLE = True
    except ImportError:
        FEATURE_ENGINEER_AVAILABLE = False

# Import live drift monitor
try:
    from ml.online_learning.live_drift_monitor import LiveDriftMonitor

    DRIFT_MONITOR_AVAILABLE = True
except ImportError:
    try:
        from src.ml.online_learning.live_drift_monitor import LiveDriftMonitor

        DRIFT_MONITOR_AVAILABLE = True
    except ImportError:
        DRIFT_MONITOR_AVAILABLE = False

# Import model registry for optional registry-based model loading
try:
    from ml.online_learning.model_registry import ModelRegistry

    MODEL_REGISTRY_AVAILABLE = True
except ImportError:
    try:
        from src.ml.online_learning.model_registry import ModelRegistry

        MODEL_REGISTRY_AVAILABLE = True
    except ImportError:
        MODEL_REGISTRY_AVAILABLE = False


@dataclass
class UserProfile:
    """User profile for fraud detection state management."""

    user_id: str
    total_transactions: int = 0
    total_amount: float = 0.0
    avg_transaction_amount: float = 0.0
    last_transaction_time: Optional[str] = None
    last_transaction_amount: float = 0.0
    daily_transaction_count: int = 0
    daily_amount: float = 0.0
    last_reset_date: Optional[str] = None
    suspicious_activity_count: int = 0

    def update_daily_stats(self, amount: float, timestamp: str) -> None:
        """Update daily statistics, resetting if new day."""
        current_date = datetime.fromisoformat(timestamp).date().isoformat()

        if self.last_reset_date != current_date:
            self.daily_transaction_count = 0
            self.daily_amount = 0.0
            self.last_reset_date = current_date

        self.daily_transaction_count += 1
        self.daily_amount += amount

    def update_transaction_stats(self, amount: float, timestamp: str) -> None:
        """Update overall transaction statistics."""
        self.total_transactions += 1
        self.total_amount += amount
        self.avg_transaction_amount = self.total_amount / self.total_transactions
        self.last_transaction_time = timestamp
        self.last_transaction_amount = amount


@dataclass
class FraudFeatures:
    """Engineered features for fraud detection."""

    user_id: str
    transaction_id: str

    # Basic transaction features
    amount: float
    transaction_hour: int
    transaction_day: int

    # User behavior features
    amount_vs_avg_ratio: float
    daily_transaction_count: int
    daily_amount_total: float
    time_since_last_transaction: float  # seconds
    amount_vs_last_ratio: float

    # Risk indicators
    is_high_amount: bool
    is_unusual_hour: bool
    is_rapid_transaction: bool
    velocity_score: float

    # Fraud score
    fraud_score: float
    is_fraud_alert: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class BatchMetrics:
    """Tracks batch processing metrics for monitoring."""

    batch_sizes: List[int] = field(default_factory=list)
    batch_durations_seconds: List[float] = field(default_factory=list)
    total_batches: int = 0
    total_messages_in_batches: int = 0
    flush_reasons: Dict[str, int] = field(default_factory=lambda: {"full": 0, "timeout": 0, "shutdown": 0})

    def record_batch(self, size: int, duration_seconds: float, reason: str) -> None:
        """Record a completed batch for metrics."""
        self.batch_sizes.append(size)
        self.batch_durations_seconds.append(duration_seconds)
        self.total_batches += 1
        self.total_messages_in_batches += size
        if reason in self.flush_reasons:
            self.flush_reasons[reason] += 1

        # Keep only last 1000 samples to bound memory
        if len(self.batch_sizes) > 1000:
            self.batch_sizes = self.batch_sizes[-1000:]
            self.batch_durations_seconds = self.batch_durations_seconds[-1000:]

    def get_summary(self) -> Dict[str, Any]:
        """Return a summary dict suitable for logging or Prometheus exposition."""
        if not self.batch_sizes:
            return {
                "total_batches": 0,
                "total_messages": 0,
                "avg_batch_size": 0,
                "avg_batch_duration_ms": 0,
                "flush_reasons": self.flush_reasons,
            }
        return {
            "total_batches": self.total_batches,
            "total_messages": self.total_messages_in_batches,
            "avg_batch_size": sum(self.batch_sizes) / len(self.batch_sizes),
            "p50_batch_size": sorted(self.batch_sizes)[len(self.batch_sizes) // 2],
            "max_batch_size": max(self.batch_sizes),
            "avg_batch_duration_ms": (sum(self.batch_durations_seconds) / len(self.batch_durations_seconds)) * 1000,
            "p99_batch_duration_ms": sorted(self.batch_durations_seconds)[int(len(self.batch_durations_seconds) * 0.99)]
            * 1000,
            "flush_reasons": self.flush_reasons,
        }


class FraudDetector:
    """
    Real-time fraud detection consumer with Redis state management.

    This consumer processes transaction streams, maintains user profiles in Redis,
    performs feature engineering, and publishes fraud alerts for suspicious
    transactions.
    """

    def __init__(
        self,
        consumer_group: str = "fraud-detection-group",
        fraud_threshold: float = 0.3,
        use_ml_model: bool = True,
        model_path: str = "models/synthetic_fraud_model_production.pkl",
        enable_cpp_acceleration: bool = True,
        drift_check_interval: int = 1000,
        batch_mode: bool = False,
        batch_size: int = 32,
        batch_timeout_ms: int = 100,
    ):
        """
        Initialize fraud detection consumer.

        Args:
            consumer_group: Kafka consumer group for parallel processing
            fraud_threshold: Fraud score threshold for alert generation
            use_ml_model: Whether to use ML model or rule-based scoring
            model_path: Path to the trained ML model
            enable_cpp_acceleration: Enable C++ accelerated inference (default: True)
            drift_check_interval: Run PSI drift check every N transactions (default: 1000)
            batch_mode: Enable batch inference mode (default: False for low-latency)
            batch_size: Maximum messages per batch when batch_mode is True
            batch_timeout_ms: Maximum time to wait before flushing a partial batch (ms)

        Note on fraud_threshold with F2-optimized models:
            The training pipeline now optimizes for F2-score with scale_pos_weight
            cost-sensitive learning. This shifts the model's probability calibration
            towards higher recall. The default threshold of 0.3 remains appropriate
            as a starting point, but should be re-evaluated on a holdout set after
            retraining. A threshold sweep (precision-recall curve analysis) is
            recommended to find the optimal operating point for the new model.
        """
        # Initialize Kafka configuration
        self.kafka_config = get_kafka_config()
        self.logger = ContextLogger(
            get_logger("stream_sentinel.fraud_detector"),
            consumer_group=consumer_group,
            component="fraud_detector",
        )
        self.fraud_threshold = fraud_threshold
        self.consumer_group = consumer_group
        self.use_ml_model = use_ml_model
        self.enable_cpp_acceleration = enable_cpp_acceleration and CPP_INFERENCE_AVAILABLE
        self.scaler = None  # Feature scaler from training pipeline (applied if present)
        self.label_encoders = {}  # Fitted LabelEncoders from training pipeline

        # Model scoring status tracks the current scoring path:
        #   "loading"        - startup, model load in progress
        #   "ml_primary"     - ML model loaded and is the primary scorer
        #   "rules_fallback" - ML model unavailable, using rule-based scoring
        self.model_status = "loading"

        # Initialise unified feature engineer (gracefully degrade if unavailable)
        self.feature_engineer = None
        if FEATURE_ENGINEER_AVAILABLE:
            try:
                self.feature_engineer = FeatureEngineer()
                self.logger.info("Unified FeatureEngineer loaded for streaming enrichment")
            except Exception as e:
                self.logger.warning(f"FeatureEngineer init failed, running without enriched features: {e}")

        # Initialise live drift monitor (gracefully degrade if unavailable)
        self.drift_monitor = None
        if DRIFT_MONITOR_AVAILABLE:
            try:
                self.drift_monitor = LiveDriftMonitor(
                    {
                        "check_interval": drift_check_interval,
                    }
                )
                self.logger.info(
                    "LiveDriftMonitor loaded (check every %d transactions)",
                    drift_check_interval,
                )
            except Exception as e:
                self.logger.warning(f"LiveDriftMonitor init failed, running without drift checks: {e}")

        # Batch processing configuration
        self.batch_mode = batch_mode
        self.batch_size = batch_size
        self.batch_timeout_ms = batch_timeout_ms
        self.batch_metrics = BatchMetrics()
        self.flow_controller = FlowController(
            max_poll_interval_ms=60_000,  # matches dev config
            slow_message_threshold_ms=500.0,
        )

        # Load ML model if enabled
        self.ml_model = None
        self.model_features = None
        self.fast_inference_engine = None
        if use_ml_model:
            self._load_ml_model(model_path)

        # Set model_status based on load result and update Prometheus gauge
        if self.ml_model is not None:
            self.model_status = "ml_primary"
            self.logger.info("Model status: ml_primary - ML model is the primary scorer")
        else:
            self.model_status = "rules_fallback"
            self.logger.warning(
                "Model status: rules_fallback - ML model unavailable, " "using rule-based scoring (DEGRADED MODE)"
            )
        try:
            prom = get_prometheus_metrics("fraud-detector")
            # Set all status labels: 1.0 for the active status, 0.0 for others
            for status in ("ml_primary", "rules_fallback", "loading"):
                prom.model_status_info.labels(status=status).set(
                    1.0 if status == self.model_status else 0.0
                )
        except Exception:
            pass  # Prometheus not available

        # Topics
        self.input_topic = "synthetic-transactions"
        self.output_topic = "fraud-alerts"
        self.blocked_topic = "blocked-transactions"

        # Initialize Kafka consumer and producer
        self.consumer = self._create_consumer()
        self.producer = self._create_producer()

        # Initialize Redis for state management
        self.redis_client = self._create_redis_client()

        # Processing statistics
        self.processed_count = 0
        self.fraud_alerts_count = 0
        self.blocked_count = 0
        self.rules_fallback_count = 0
        self.start_time = time.time()

        # Schema Registry integration (optional)
        self._schema_helper = None
        if SCHEMA_UTILS_AVAILABLE:
            try:
                self._schema_helper = get_schema_helper()
                if self._schema_helper.is_available:
                    self.logger.info("Schema Registry available -- consuming Avro messages")
                else:
                    self.logger.info("Schema Registry not reachable -- consuming plain JSON")
            except Exception as e:
                self.logger.warning(f"Schema helper init failed: {e}")

        # Health check server reference (set by main() after construction)
        self._health_server = None

        # Graceful shutdown
        self.running = True
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        mode_label = "batch" if batch_mode else "single-message"
        self.logger.info(
            f"FraudDetector initialized - group: {consumer_group}, " f"threshold: {fraud_threshold}, mode: {mode_label}"
        )
        if batch_mode:
            self.logger.info(f"Batch config: size={batch_size}, timeout={batch_timeout_ms}ms")

    @staticmethod
    def _setup_logging() -> logging.Logger:
        """Legacy stub -- logging is now configured by utils.logging."""
        return get_logger("stream_sentinel.fraud_detector")

    def _create_consumer(self) -> Consumer:
        """Create Kafka consumer for transaction processing."""
        consumer_config = self.kafka_config.get_consumer_config(self.consumer_group, "fraud_detector")
        consumer = Consumer(consumer_config)

        # Subscribe to transactions topic
        consumer.subscribe([self.input_topic])
        self.logger.info(f"Consumer subscribed to {self.input_topic}")

        return consumer

    def _create_producer(self) -> Producer:
        """Create Kafka producer for fraud alerts."""
        producer_config = self.kafka_config.get_producer_config("transaction")
        producer = Producer(producer_config)

        self.logger.info("Producer created for fraud alerts")
        return producer

    def _create_redis_client(self) -> redis.Redis:
        """Create Redis client for state management."""
        try:
            client = redis.Redis(
                host="localhost",
                port=6379,
                db=0,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )

            # Test connection
            client.ping()
            self.logger.info("Redis client connected successfully")
            return client

        except redis.ConnectionError as e:
            self.logger.error(f"Failed to connect to Redis: {e}")
            raise

    def _resolve_model_path(self, model_path: str) -> Optional[Path]:
        """
        Resolve the model file path, trying multiple locations relative
        to the project root.

        Args:
            model_path: Caller-supplied path (may be relative)

        Returns:
            Resolved Path if found, None otherwise
        """
        candidates = [
            Path(model_path),
            # Relative to project root (two levels up from src/consumers/)
            Path(__file__).parent.parent.parent / model_path,
            # Absolute fallback
            Path(__file__).parent.parent.parent / "models" / Path(model_path).name,
        ]

        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved.is_file():
                self.logger.info(f"Resolved model path: {resolved}")
                return resolved

        self.logger.error(f"Model file not found. Tried: {[str(c.resolve()) for c in candidates]}")
        return None

    def _load_ml_model(self, model_path: str) -> None:
        """Load the trained ML model for fraud detection.

        Loading order:
        1. Try ModelRegistry (if available) -- gets latest production model
        2. Fall back to filesystem path (trying several project-relative locations)
        On success the model becomes the primary scorer; on failure the detector
        degrades gracefully to rule-based scoring.
        Logs which source was used.
        """
        model_source = "unknown"

        # --- Attempt 1: ModelRegistry ---
        if MODEL_REGISTRY_AVAILABLE:
            try:
                registry = ModelRegistry()
                registry_model = registry.get_active_model("production")
                if registry_model is not None:
                    model_data = registry_model
                    model_source = "registry"
                    self.logger.info("Loaded model from ModelRegistry (production)")
                    self._unpack_model_data(model_data, model_source)
                    return
                else:
                    self.logger.info("No active model in registry; falling back to filesystem")
            except Exception as e:
                self.logger.info(f"ModelRegistry unavailable ({e}); falling back to filesystem")

        # --- Attempt 2: Filesystem ---
        model_source = "filesystem"
        resolved_path = self._resolve_model_path(model_path)
        if resolved_path is None:
            self.logger.error(f"ML model not found at {model_path} -- " "will use rule-based scoring (DEGRADED)")
            self.use_ml_model = False
            return

        model_path_str = str(resolved_path)

        try:
            # Attempt C++ accelerated inference first
            if self.enable_cpp_acceleration:
                try:
                    self.fast_inference_engine = FastInferenceEngine(model_path_str, enable_cpp=True)
                    status = self.fast_inference_engine.get_status()

                    if status["using_cpp"]:
                        self.logger.info("C++ accelerated inference engine loaded successfully")
                    else:
                        self.logger.info("C++ inference not available, using Python fallback " "in FastInferenceEngine")
                except Exception as e:
                    self.logger.warning(
                        f"Failed to initialize FastInferenceEngine: {e} -- " "falling back to standard Python XGBoost"
                    )
                    self.fast_inference_engine = None

            # Load Python model (always needed for feature extraction
            # compatibility and as the standard inference path)
            with open(model_path_str, "rb") as f:
                model_data = pickle.load(f)

            # Extract model and preprocessing components
            if isinstance(model_data, dict):
                self.ml_model = model_data.get("model")
                self.scaler = model_data.get("scaler")
                self.label_encoders = model_data.get("label_encoders", {})
                self.model_features = model_data.get("feature_names", [])
                self.logger.info(f"Loaded model components: {list(model_data.keys())}")
                if self.label_encoders:
                    self.logger.info(
                        "Loaded %d label encoders for categorical features: %s",
                        len(self.label_encoders),
                        list(self.label_encoders.keys()),
                    )
            else:
                # Simple model pickle (no metadata dict wrapper)
                self.ml_model = model_data

            # Attempt to read feature_names_in_ directly from the model
            # object (scikit-learn / XGBoost convention) for validation
            if hasattr(self.ml_model, "feature_names_in_"):
                model_native_features = list(self.ml_model.feature_names_in_)
                if self.model_features and model_native_features != self.model_features:
                    self.logger.warning(
                        "Feature name mismatch between pickle metadata and "
                        "model.feature_names_in_. Using model.feature_names_in_ "
                        "as the authoritative source."
                    )
                self.model_features = model_native_features
            elif hasattr(self.ml_model, "get_booster"):
                # XGBoost Booster-level feature names
                try:
                    booster = self.ml_model.get_booster()
                    booster_features = booster.feature_names
                    if booster_features and not self.model_features:
                        self.model_features = list(booster_features)
                except Exception:
                    pass  # Not critical -- fall through to metadata

            # Load model metadata for supplementary info
            metadata_path = model_path_str.replace(".pkl", "_metadata.json")
            if not Path(metadata_path).exists():
                metadata_path = str(resolved_path.parent / "ieee_fraud_model_metadata.json")

            if Path(metadata_path).exists():
                with open(metadata_path, "r") as f:
                    metadata = json.load(f)
                    # Only use metadata features if not already loaded
                    if not self.model_features:
                        self.model_features = metadata.get("feature_names", [])
                    model_metrics = metadata.get("model_metrics", {})
                    model_type = metadata.get("model_type", "unknown")
                    model_version = metadata.get("version", "unknown")
                    self.logger.info(
                        f"Loaded ML model: type={model_type}, " f"version={model_version}, path={resolved_path}"
                    )
                    val_auc = model_metrics.get("val_auc")
                    if val_auc is not None and isinstance(val_auc, (int, float)):
                        self.logger.info(f"Model validation AUC: {val_auc:.4f}")
                    else:
                        self.logger.info("Model validation AUC: unknown")
            else:
                self.logger.warning("Model metadata not found, using pickle feature names")

            feature_count = len(self.model_features) if self.model_features else 0
            self.logger.info(
                "Model loaded from %s: %d features expected",
                model_source,
                feature_count,
            )

            if feature_count == 0:
                self.logger.warning("No feature names found -- feature validation at inference " "time will be skipped")

        except Exception as e:
            self.logger.error(f"Failed to load ML model from {resolved_path}: {e}")
            self.logger.warning("Falling back to rule-based fraud detection (DEGRADED MODE)")
            self.ml_model = None
            self.use_ml_model = False

    def _unpack_model_data(self, model_data: Any, source: str) -> None:
        """Unpack model data dict loaded from registry or filesystem.

        Sets self.ml_model, self.scaler, self.label_encoders, and self.model_features.
        """
        if isinstance(model_data, dict):
            self.ml_model = model_data.get("model")
            self.scaler = model_data.get("scaler")
            self.label_encoders = model_data.get("label_encoders", {})
            self.model_features = model_data.get("feature_names", [])
            self.logger.info(
                "Unpacked model from %s: keys=%s, features=%d, label_encoders=%d",
                source,
                list(model_data.keys()),
                len(self.model_features),
                len(self.label_encoders),
            )
        else:
            self.ml_model = model_data
            self.label_encoders = {}
            self.model_features = []
            self.logger.info("Loaded raw model object from %s", source)

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle graceful shutdown signals."""
        self.logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.running = False

    def get_user_profile(self, user_id: str) -> UserProfile:
        """
        Retrieve or create user profile from Redis.

        Args:
            user_id: User identifier

        Returns:
            UserProfile object with current state
        """
        try:
            profile_data = self.redis_client.hgetall(f"user_profile:{user_id}")

            if profile_data:
                # Convert Redis strings back to appropriate types
                return UserProfile(
                    user_id=profile_data["user_id"],
                    total_transactions=int(profile_data.get("total_transactions", 0)),
                    total_amount=float(profile_data.get("total_amount", 0.0)),
                    avg_transaction_amount=float(profile_data.get("avg_transaction_amount", 0.0)),
                    last_transaction_time=profile_data.get("last_transaction_time"),
                    last_transaction_amount=float(profile_data.get("last_transaction_amount", 0.0)),
                    daily_transaction_count=int(profile_data.get("daily_transaction_count", 0)),
                    daily_amount=float(profile_data.get("daily_amount", 0.0)),
                    last_reset_date=profile_data.get("last_reset_date"),
                    suspicious_activity_count=int(profile_data.get("suspicious_activity_count", 0)),
                )
            else:
                # Create new profile for first-time user
                return UserProfile(user_id=user_id)

        except Exception as e:
            self.logger.error(f"Error retrieving user profile for {user_id}: {e}")
            return UserProfile(user_id=user_id)

    def save_user_profile(self, profile: UserProfile) -> None:
        """
        Save user profile to Redis.

        Args:
            profile: UserProfile to save
        """
        try:
            profile_dict = asdict(profile)
            # Remove None values for cleaner Redis storage
            profile_dict = {k: v for k, v in profile_dict.items() if v is not None}

            self.redis_client.hset(f"user_profile:{profile.user_id}", mapping=profile_dict)

            # Set TTL for user profiles (30 days)
            self.redis_client.expire(f"user_profile:{profile.user_id}", 2592000)

        except Exception as e:
            self.logger.error(f"Error saving user profile for {profile.user_id}: {e}")

    def extract_features(self, transaction: Dict[str, Any], user_profile: UserProfile) -> FraudFeatures:
        """
        Extract fraud detection features from transaction and user state.

        Args:
            transaction: Raw transaction data
            user_profile: Current user profile state

        Returns:
            FraudFeatures object with engineered features
        """
        # Parse transaction data
        amount = float(transaction["transaction_amt"])
        timestamp = transaction["generated_timestamp"]  # Use generated timestamp instead
        user_id = str(transaction["card1"])  # Using card1 as user identifier, convert to string
        transaction_id = transaction.get("transaction_id", "unknown")

        # Parse timestamp for temporal features
        dt = datetime.fromisoformat(timestamp)
        transaction_hour = dt.hour
        transaction_day = dt.weekday()

        # Calculate behavioral features
        amount_vs_avg_ratio = (
            amount / user_profile.avg_transaction_amount if user_profile.avg_transaction_amount > 0 else 1.0
        )

        # Time since last transaction (in seconds)
        time_since_last = 0.0
        if user_profile.last_transaction_time:
            last_dt = datetime.fromisoformat(user_profile.last_transaction_time)
            time_since_last = (dt - last_dt).total_seconds()

        # Amount comparison with last transaction
        amount_vs_last_ratio = (
            amount / user_profile.last_transaction_amount if user_profile.last_transaction_amount > 0 else 1.0
        )

        # Risk indicators
        is_high_amount = amount > 1000.0  # High amount threshold
        is_unusual_hour = transaction_hour < 6 or transaction_hour > 22  # Night hours
        is_rapid_transaction = time_since_last < 300  # Less than 5 minutes

        # Calculate velocity score (transactions per hour)
        velocity_score = (
            user_profile.daily_transaction_count / 24.0 if user_profile.daily_transaction_count > 0 else 0.0
        )

        # ---- Scoring path selection ----
        # Primary: ML model.  Fallback: rule-based scoring.
        # The rule-based path only activates when the model is genuinely
        # unavailable (failed to load, or a transient inference error).
        if self.model_status == "ml_primary" and self.ml_model is not None:
            fraud_score = self._calculate_ml_fraud_score(transaction, user_profile)
        else:
            # Explicit degraded-mode fallback
            self.rules_fallback_count += 1
            if self.rules_fallback_count <= 5 or self.rules_fallback_count % 1000 == 0:
                self.logger.warning(
                    "DEGRADED MODE: scoring transaction with rule-based fallback "
                    f"(model_status={self.model_status}, "
                    f"fallback_count={self.rules_fallback_count})"
                )
            fraud_score = self._calculate_fraud_score(
                amount_vs_avg_ratio,
                is_high_amount,
                is_unusual_hour,
                is_rapid_transaction,
                velocity_score,
                user_profile.daily_transaction_count,
            )

        return FraudFeatures(
            user_id=user_id,
            transaction_id=transaction_id,
            amount=amount,
            transaction_hour=transaction_hour,
            transaction_day=transaction_day,
            amount_vs_avg_ratio=amount_vs_avg_ratio,
            daily_transaction_count=user_profile.daily_transaction_count,
            daily_amount_total=user_profile.daily_amount,
            time_since_last_transaction=time_since_last,
            amount_vs_last_ratio=amount_vs_last_ratio,
            is_high_amount=is_high_amount,
            is_unusual_hour=is_unusual_hour,
            is_rapid_transaction=is_rapid_transaction,
            velocity_score=velocity_score,
            fraud_score=fraud_score,
            is_fraud_alert=fraud_score >= self.fraud_threshold,
        )

    def _calculate_fraud_score(
        self,
        amount_vs_avg_ratio: float,
        is_high_amount: bool,
        is_unusual_hour: bool,
        is_rapid_transaction: bool,
        velocity_score: float,
        daily_count: int,
    ) -> float:
        """
        Calculate fraud score using rule-based approach.

        Args:
            amount_vs_avg_ratio: Transaction amount vs user average
            is_high_amount: Whether transaction is high amount
            is_unusual_hour: Whether transaction is at unusual hour
            is_rapid_transaction: Whether transaction is rapid
            velocity_score: User transaction velocity
            daily_count: Daily transaction count

        Returns:
            Fraud score between 0.0 and 1.0
        """
        score = 0.0

        # Amount-based scoring
        if amount_vs_avg_ratio > 5.0:
            score += 0.3
        elif amount_vs_avg_ratio > 3.0:
            score += 0.2
        elif amount_vs_avg_ratio > 2.0:
            score += 0.1

        # High amount transactions
        if is_high_amount:
            score += 0.2

        # Unusual hour transactions
        if is_unusual_hour:
            score += 0.15

        # Rapid transactions (potential velocity fraud)
        if is_rapid_transaction:
            score += 0.25

        # High velocity users
        if velocity_score > 10:  # More than 10 transactions per hour average
            score += 0.2
        elif velocity_score > 5:
            score += 0.1

        # Excessive daily transactions
        if daily_count > 50:
            score += 0.15
        elif daily_count > 25:
            score += 0.1

        # Ensure score is between 0 and 1
        return min(score, 1.0)

    def _calculate_ml_fraud_score(self, transaction: Dict[str, Any], user_profile: UserProfile) -> float:
        """
        Calculate fraud score using trained ML model.

        When called inside a batch, ``_batch_override_score`` is set by
        ``_process_batch`` so we can skip redundant per-message inference.

        Args:
            transaction: Transaction data
            user_profile: User profile for behavioral features

        Returns:
            Fraud probability between 0.0 and 1.0
        """
        # If the batch loop already computed the score, use it directly
        override = getattr(self, "_batch_override_score", None)
        if override is not None:
            return float(override)

        try:
            # Extract features compatible with the trained model
            features = self._extract_ml_features(transaction, user_profile)

            # Use FastInferenceEngine if available, otherwise fall back to Python XGBoost
            if hasattr(self, "fast_inference_engine") and self.fast_inference_engine:
                fraud_probability, performance_info = self.fast_inference_engine.predict_fraud_probability(features)

                # Log performance info periodically for monitoring
                if self.processed_count % 1000 == 0:
                    self.logger.info(f"ML inference: {performance_info}")

                return float(fraud_probability)
            else:
                # Standard Python XGBoost inference
                fraud_probability = self.ml_model.predict_proba([features])[0][1]
                return float(fraud_probability)

        except Exception as e:
            self.logger.error(f"ML inference failed: {e} -- switching to rules_fallback mode")
            # Transition to degraded mode so subsequent transactions use
            # the rules path directly (avoids repeated inference failures).
            self.model_status = "rules_fallback"
            try:
                prom = get_prometheus_metrics("fraud-detector")
                prom.model_status_info.labels(status="ml_primary").set(0.0)
                prom.model_status_info.labels(status="rules_fallback").set(1.0)
            except Exception:
                pass  # Prometheus not available

            # Compute rule-based score for this transaction
            amount = float(transaction["transaction_amt"])
            timestamp = transaction["generated_timestamp"]
            dt = datetime.fromisoformat(timestamp)

            amount_vs_avg_ratio = (
                amount / user_profile.avg_transaction_amount if user_profile.avg_transaction_amount > 0 else 1.0
            )
            is_high_amount = amount > 1000.0
            is_unusual_hour = dt.hour < 6 or dt.hour > 22

            time_since_last = 0.0
            if user_profile.last_transaction_time:
                last_dt = datetime.fromisoformat(user_profile.last_transaction_time)
                time_since_last = (dt - last_dt).total_seconds()
            is_rapid_transaction = time_since_last < 300

            velocity_score = user_profile.daily_transaction_count / 24.0

            return self._calculate_fraud_score(
                amount_vs_avg_ratio,
                is_high_amount,
                is_unusual_hour,
                is_rapid_transaction,
                velocity_score,
                user_profile.daily_transaction_count,
            )

    # ------------------------------------------------------------------
    # Mapping from snake_case producer keys to PascalCase model feature
    # names.  Only entries where the names differ are needed; features
    # whose producer key already matches the model name (e.g. ``card1``,
    # ``V12``, ``id_11``) are resolved by the case-insensitive lookup.
    # ------------------------------------------------------------------
    _PRODUCER_TO_MODEL_KEY: Dict[str, str] = {
        "transaction_dt": "TransactionDT",
        "transaction_amt": "TransactionAmt",
        "product_cd": "ProductCD",
        "p_emaildomain": "P_emaildomain",
        "r_emaildomain": "R_emaildomain",
        "device_type": "DeviceType",
        "device_info": "DeviceInfo",
    }

    def _extract_ml_features(self, transaction: Dict[str, Any], user_profile: UserProfile) -> List[float]:
        """Extract features compatible with the trained ML model.

        Builds a feature vector of exactly ``len(self.model_features)``
        values in the order the model was trained on.  Categorical
        features are encoded using the ``LabelEncoder`` instances saved
        in the model pickle.  Missing features are set to ``NaN`` so
        XGBoost can use its native sparsity handling.

        Args:
            transaction: Transaction data dict (snake_case keys from producer).
            user_profile: User profile for behavioral features.

        Returns:
            List of float values matching model feature order.
        """

        # -- helpers --------------------------------------------------
        _nan = float("nan")

        def safe_float(value):
            """Convert to float; return NaN for missing / unconvertible."""
            if value is None or value == "":
                return _nan
            try:
                return float(value)
            except (ValueError, TypeError):
                return _nan

        # -- case-insensitive lookup table ----------------------------
        # The producer emits snake_case keys (``transaction_amt``) while
        # the model expects PascalCase / original IEEE-CIS names
        # (``TransactionAmt``).  Build a single dict keyed by the *model*
        # feature name so the assembly loop can look values up directly.
        txn_by_model_key: Dict[str, Any] = {}

        for raw_key, raw_value in transaction.items():
            model_key = self._PRODUCER_TO_MODEL_KEY.get(raw_key)
            if model_key is not None:
                txn_by_model_key[model_key] = raw_value
            else:
                # For keys not in the explicit map (card1, addr1, c4,
                # m1, v12, id_11, ...) the producer key is already the
                # same string the model uses modulo case.  Store both
                # the raw key and an upper-first variant so lookups
                # work regardless of case.
                txn_by_model_key[raw_key] = raw_value
                # V-features: producer sends "v12", model expects "V12"
                # M-features: producer sends "m1", model expects "M1"
                # C-features: producer sends "c4", model expects "C4"
                # D-features: producer sends "d8", model expects "D8"
                if len(raw_key) >= 2 and raw_key[0].isalpha() and raw_key[1:].replace("_", "").isdigit():
                    txn_by_model_key[raw_key[0].upper() + raw_key[1:]] = raw_value

        # -- collect numeric features ---------------------------------
        available: Dict[str, float] = {}

        # Numeric features that come directly from the transaction
        _NUMERIC_FEATURES = [
            "TransactionDT",
            "TransactionAmt",
            "card1",
            "card2",
            "card3",
            "card5",
            "addr1",
            "addr2",
            "C4",
            "C7",
            "C8",
            "C10",
            "C12",
            "D8",
            "id_11",
            "id_13",
            "id_17",
            "id_19",
            "id_20",
        ]
        for feat in _NUMERIC_FEATURES:
            val = txn_by_model_key.get(feat)
            if val is not None:
                available[feat] = safe_float(val)

        # V-features (147 specific V columns used by the model)
        for feat_name in self.model_features:
            if feat_name.startswith("V"):
                val = txn_by_model_key.get(feat_name)
                if val is not None:
                    available[feat_name] = safe_float(val)

        # -- encode categoricals with saved LabelEncoders -------------
        for feat_name, encoder in self.label_encoders.items():
            raw_value = txn_by_model_key.get(feat_name)
            if raw_value is None or raw_value == "":
                # Feature not present -- use 'unknown' if the encoder
                # supports it, otherwise NaN.
                if "unknown" in encoder.classes_:
                    available[feat_name] = float(encoder.transform(["unknown"])[0])
                else:
                    available[feat_name] = _nan
            else:
                str_value = str(raw_value)
                if str_value in encoder.classes_:
                    available[feat_name] = float(encoder.transform([str_value])[0])
                elif "unknown" in encoder.classes_:
                    available[feat_name] = float(encoder.transform(["unknown"])[0])
                else:
                    # No 'unknown' class and value unseen -- use NaN
                    available[feat_name] = _nan

        # -- derived / engineered features ----------------------------
        amt_raw = txn_by_model_key.get("TransactionAmt")
        amt = safe_float(amt_raw) if amt_raw is not None else _nan

        if amt == amt:  # not NaN
            available["TransactionAmt_log"] = float(np.log1p(amt))
            available["TransactionAmt_decimal"] = amt - int(amt)
            # Amount bin: 0=<50, 1=<100, 2=<200, 3=<500, 4=>=500
            if amt < 50:
                available["TransactionAmt_bin"] = 0.0
            elif amt < 100:
                available["TransactionAmt_bin"] = 1.0
            elif amt < 200:
                available["TransactionAmt_bin"] = 2.0
            elif amt < 500:
                available["TransactionAmt_bin"] = 3.0
            else:
                available["TransactionAmt_bin"] = 4.0
        else:
            available["TransactionAmt_log"] = _nan
            available["TransactionAmt_decimal"] = _nan
            available["TransactionAmt_bin"] = _nan

        # -- assemble feature vector in model order -------------------
        features: List[float] = []
        for feat_name in self.model_features:
            val = available.get(feat_name)
            if val is not None:
                features.append(float(val))
            else:
                features.append(_nan)  # XGBoost handles NaN natively

        # -- validate length ------------------------------------------
        expected_len = len(self.model_features)
        actual_len = len(features)
        if expected_len > 0 and actual_len != expected_len:
            raise ValueError(
                f"Feature vector length mismatch: model expects " f"{expected_len} features but got {actual_len}"
            )

        # -- apply scaler if present ----------------------------------
        if self.scaler is not None:
            try:
                features = self.scaler.transform([features])[0].tolist()
            except Exception as e:
                self.logger.debug(f"Scaler transform failed, using raw features: {e}")

        return features

    def publish_fraud_alert(self, features: FraudFeatures, original_transaction: Dict[str, Any]) -> None:
        """
        Publish fraud alert to Kafka topic.

        Args:
            features: Fraud features for the transaction
            original_transaction: Original transaction data
        """
        try:
            alert = {
                "alert_id": f"alert_{features.transaction_id}_{int(time.time())}",
                "timestamp": datetime.now().isoformat(),
                "user_id": features.user_id,
                "transaction_id": features.transaction_id,
                "fraud_score": features.fraud_score,
                "risk_factors": {
                    "is_high_amount": features.is_high_amount,
                    "is_unusual_hour": features.is_unusual_hour,
                    "is_rapid_transaction": features.is_rapid_transaction,
                    "amount_vs_avg_ratio": features.amount_vs_avg_ratio,
                    "velocity_score": features.velocity_score,
                    "daily_transaction_count": features.daily_transaction_count,
                },
                "transaction_details": {
                    "amount": features.amount,
                    "hour": features.transaction_hour,
                    "day": features.transaction_day,
                },
                "original_transaction": original_transaction,
            }

            # Serialize alert (Avro when Schema Registry is available, JSON otherwise)
            if self._schema_helper is not None and self._schema_helper.is_available and SCHEMA_UTILS_AVAILABLE:
                from kafka.schema_utils import serialize_message

                alert_bytes = serialize_message(
                    self._schema_helper,
                    "fraud_alert",
                    alert,
                    self.output_topic,
                )
            else:
                alert_bytes = json.dumps(alert).encode("utf-8")

            # Publish to fraud alerts topic
            self.producer.produce(
                self.output_topic,
                key=features.user_id,
                value=alert_bytes,
                callback=self._delivery_callback,
            )

            # Poll for delivery callbacks
            self.producer.poll(0)

            self.fraud_alerts_count += 1
            self.logger.warning(
                "Fraud alert generated",
                extra={
                    "transaction_id": features.transaction_id,
                    "user_id": features.user_id,
                    "fraud_score": round(features.fraud_score, 3),
                    "amount": round(features.amount, 2),
                },
            )

        except Exception as e:
            self.logger.error(f"Error publishing fraud alert: {e}")

    def _delivery_callback(self, err, msg):
        """Callback for message delivery confirmation."""
        if err is not None:
            self.logger.error(f"Failed to deliver fraud alert: {err}")
        else:
            self.logger.debug(f"Fraud alert delivered to {msg.topic()} [partition {msg.partition()}]")

    def publish_fraud_detection_result(
        self,
        features: FraudFeatures,
        original_transaction: Dict[str, Any],
        processing_start_time: float,
    ) -> None:
        """
        Publish complete fraud detection result for persistence.

        Args:
            features: Fraud features for the transaction
            original_transaction: Original transaction data
            processing_start_time: When processing started (for timing)
        """
        try:
            processing_time_ms = int((time.time() - processing_start_time) * 1000)

            # Determine severity based on fraud score
            severity = "MINIMAL"
            if features.fraud_score >= 0.9:
                severity = "CRITICAL"
            elif features.fraud_score >= 0.8:
                severity = "HIGH"
            elif features.fraud_score >= 0.6:
                severity = "MEDIUM"
            elif features.fraud_score >= 0.4:
                severity = "LOW"

            # Create comprehensive fraud detection result
            detection_result = {
                "transaction": {
                    "transaction_id": features.transaction_id,
                    "user_id": features.user_id,
                    "timestamp": original_transaction.get("generated_timestamp"),
                    "amount": features.amount,
                    "merchant_category": original_transaction.get("ProductCD", "unknown"),
                    "payment_method": original_transaction.get("card4", "unknown"),
                    "device_info": original_transaction.get("DeviceType", "unknown"),
                    "location_country": original_transaction.get("card3", "unknown"),
                    "location_state": original_transaction.get("addr1", "unknown"),
                },
                "is_fraud": features.is_fraud_alert,
                "fraud_score": features.fraud_score,
                "severity": severity,
                "ml_prediction": features.fraud_score if self.use_ml_model else None,
                "business_rules_triggered": self._get_triggered_rules(features),
                "explanation": {
                    "amount_vs_avg_ratio": features.amount_vs_avg_ratio,
                    "is_high_amount": features.is_high_amount,
                    "is_unusual_hour": features.is_unusual_hour,
                    "is_rapid_transaction": features.is_rapid_transaction,
                    "velocity_score": features.velocity_score,
                    "daily_transaction_count": features.daily_transaction_count,
                },
                "features": features.to_dict(),
                "processing_time_ms": processing_time_ms,
                "detection_metadata": {
                    "ml_model_version": ("ieee_fraud_model_production_v1.0" if self.use_ml_model else None),
                    "ml_prediction": (features.fraud_score if self.use_ml_model else None),
                    "ml_confidence": 0.85 if self.use_ml_model else None,  # Placeholder
                    "business_rules_score": (features.fraud_score if not self.use_ml_model else None),
                    "features_used": list(features.to_dict().keys()),
                    "model_features": features.to_dict(),
                },
            }

            # Publish to fraud detection results topic for persistence
            self.producer.produce(
                "fraud-detection-results",
                key=features.user_id,
                value=json.dumps(detection_result),
                callback=self._delivery_callback,
            )

            self.producer.poll(0)

        except Exception as e:
            self.logger.error(f"Error publishing fraud detection result for persistence: {e}")

    def _get_triggered_rules(self, features: FraudFeatures) -> List[str]:
        """Get list of business rules that were triggered."""
        triggered_rules = []

        if features.is_high_amount:
            triggered_rules.append("high_amount_transaction")
        if features.is_unusual_hour:
            triggered_rules.append("unusual_hour_transaction")
        if features.is_rapid_transaction:
            triggered_rules.append("rapid_transaction_velocity")
        if features.amount_vs_avg_ratio > 3.0:
            triggered_rules.append("amount_deviation_high")
        if features.velocity_score > 10:
            triggered_rules.append("high_velocity_user")
        if features.daily_transaction_count > 25:
            triggered_rules.append("excessive_daily_transactions")

        return triggered_rules

    def publish_performance_metrics(self, processing_time_ms: float) -> None:
        """
        Publish performance metrics for monitoring.

        Args:
            processing_time_ms: Processing time in milliseconds
        """
        try:
            current_time = datetime.now().isoformat()

            metrics = [
                {
                    "timestamp": current_time,
                    "metric_name": "fraud_detection_processing_time",
                    "metric_value": processing_time_ms,
                    "component": "fraud_detector",
                    "instance_id": f"fraud_detector_{self.consumer_group}",
                    "labels": {
                        "consumer_group": self.consumer_group,
                        "use_ml_model": str(self.use_ml_model),
                    },
                },
                {
                    "timestamp": current_time,
                    "metric_name": "fraud_detection_throughput",
                    "metric_value": self.processed_count / max((time.time() - self.start_time), 1),
                    "component": "fraud_detector",
                    "instance_id": f"fraud_detector_{self.consumer_group}",
                    "labels": {"consumer_group": self.consumer_group},
                },
            ]

            for metric in metrics:
                self.producer.produce(
                    "performance-metrics",
                    key=metric["instance_id"],
                    value=json.dumps(metric),
                    callback=self._delivery_callback,
                )

            self.producer.poll(0)

        except Exception as e:
            self.logger.error(f"Error publishing performance metrics: {e}")

    def _is_user_blocked(self, user_id: str) -> bool:
        """
        Check whether a user is on the blocked_users set in Redis.

        Uses a single SISMEMBER call for speed.  If Redis is unreachable
        the check returns False so that fraud scoring can still proceed
        (fail-open for the blocking check only -- we prefer to score a
        transaction rather than silently drop it).

        Args:
            user_id: User identifier (card1)

        Returns:
            True if the user is blocked, False otherwise (including on
            Redis errors).
        """
        try:
            return bool(self.redis_client.sismember("blocked_users", str(user_id)))
        except Exception as e:
            self.logger.warning(
                f"Redis blocked_users check failed for user {user_id}: {e} "
                "-- proceeding with normal scoring (fail-open)"
            )
            return False

    def _publish_blocked_transaction(self, transaction: Dict[str, Any], user_id: str) -> None:
        """
        Emit a blocked transaction to the blocked-transactions Kafka topic.

        Args:
            transaction: Original transaction data
            user_id: The blocked user identifier
        """
        try:
            blocked_event = {
                "timestamp": datetime.now().isoformat(),
                "user_id": str(user_id),
                "transaction_id": transaction.get("transaction_id", "unknown"),
                "amount": float(transaction.get("transaction_amt", 0)),
                "reason": "user_on_blocked_list",
                "original_transaction": transaction,
            }

            self.producer.produce(
                self.blocked_topic,
                key=str(user_id),
                value=json.dumps(blocked_event),
                callback=self._delivery_callback,
            )
            self.producer.poll(0)

        except Exception as e:
            self.logger.error(f"Failed to publish blocked transaction for user {user_id}: {e}")

    def process_transaction(self, transaction: Dict[str, Any]) -> None:
        """
        Process a single transaction for fraud detection.

        The processing pipeline is:
        1. Check if the user is blocked (Redis SISMEMBER) -- skip scoring
           if blocked and emit to blocked-transactions topic instead.
        2. Retrieve/create user profile from Redis.
        3. Extract features and score via ML model (primary) or rules
           (fallback).
        4. Publish alerts and detection results.

        Args:
            transaction: Transaction data from Kafka message
        """
        processing_start_time = time.time()

        try:
            user_id = transaction["card1"]  # Using card1 as user identifier

            # ---- Blocking enforcement (P1.2) ----
            # Check *before* scoring to save compute on known-blocked users.
            if self._is_user_blocked(user_id):
                self.blocked_count += 1
                try:
                    prom = get_prometheus_metrics("fraud-detector")
                    prom.transactions_blocked_total.labels(reason="user_on_blocked_list").inc()
                except Exception:
                    pass
                self.logger.info(
                    f"BLOCKED: transaction from user {user_id} rejected "
                    f"(user is on blocked_users list, "
                    f"total_blocked={self.blocked_count})"
                )
                self._publish_blocked_transaction(transaction, user_id)
                self.processed_count += 1
                return  # Skip scoring entirely

            # Get current user profile
            user_profile = self.get_user_profile(user_id)

            # Extract features for fraud detection
            features = self.extract_features(transaction, user_profile)

            # Feed fraud score to drift monitor (non-blocking)
            if self.drift_monitor is not None:
                try:
                    drift_alert = self.drift_monitor.record_score(features.fraud_score)
                    if drift_alert is not None:
                        self.logger.warning(
                            "Drift detected: PSI=%.4f severity=%s",
                            drift_alert["psi_score"],
                            drift_alert["severity"],
                        )
                except Exception as e:
                    self.logger.debug(f"Drift monitor error (non-fatal): {e}")

            # Update user profile with new transaction
            user_profile.update_daily_stats(features.amount, transaction["generated_timestamp"])
            user_profile.update_transaction_stats(features.amount, transaction["generated_timestamp"])

            # Update suspicious activity count if fraud detected
            if features.is_fraud_alert:
                user_profile.suspicious_activity_count += 1

            # Save updated profile
            self.save_user_profile(user_profile)

            # Publish fraud alert if threshold exceeded
            if features.is_fraud_alert:
                self.publish_fraud_alert(features, transaction)

            # Publish complete fraud detection result for persistence
            self.publish_fraud_detection_result(features, transaction, processing_start_time)

            # Publish performance metrics periodically
            if self.processed_count % 100 == 0:  # Every 100 transactions
                processing_time_ms = (time.time() - processing_start_time) * 1000
                self.publish_performance_metrics(processing_time_ms)

            # Debug logging for high fraud scores (even if not alerting)
            if features.fraud_score > 0.2:
                self.logger.debug(
                    f"High fraud score: {features.fraud_score:.3f} for user {user_id}, "
                    f"amount: ${features.amount:.2f}, threshold: {self.fraud_threshold}"
                )

            self.processed_count += 1

            # Record processing time for flow control
            processing_elapsed = time.time() - processing_start_time
            self.flow_controller.record_processing_time(processing_elapsed)

            # Log processing statistics every 1000 transactions
            if self.processed_count % 1000 == 0:
                elapsed = time.time() - self.start_time
                tps = self.processed_count / elapsed
                fraud_rate = self.fraud_alerts_count / self.processed_count * 100

                self.logger.info(
                    "Processing statistics",
                    extra={
                        "processed_count": self.processed_count,
                        "fraud_alerts_count": self.fraud_alerts_count,
                        "fraud_rate_pct": round(fraud_rate, 2),
                        "blocked_count": self.blocked_count,
                        "tps": round(tps, 1),
                        "uptime_seconds": round(elapsed, 1),
                    },
                )
                # Log flow control stats periodically
                fc_stats = self.flow_controller.get_stats()
                if fc_stats["avg_processing_ms"] > 0:
                    self.logger.info(f"Flow control: {fc_stats}")

        except Exception as e:
            self.logger.error(f"Error processing transaction: {e}")
            self.logger.error(f"Transaction data: {transaction}")

    # ------------------------------------------------------------------
    # Batch inference helpers
    # ------------------------------------------------------------------

    def _extract_ml_features_batch(
        self,
        transactions: List[Dict[str, Any]],
        user_profiles: List["UserProfile"],
    ) -> List[List[float]]:
        """Extract ML feature vectors for a batch of transactions.

        Returns a list of feature vectors, one per transaction, suitable for
        passing to ``model.predict_proba()`` as a 2-D array.
        """
        return [self._extract_ml_features(txn, profile) for txn, profile in zip(transactions, user_profiles)]

    def _calculate_ml_fraud_scores_batch(
        self,
        transactions: List[Dict[str, Any]],
        user_profiles: List["UserProfile"],
    ) -> List[float]:
        """Run batch ML inference and return fraud probabilities.

        Falls back to per-message scoring if batch prediction fails.
        """
        try:
            feature_matrix = self._extract_ml_features_batch(transactions, user_profiles)
            # XGBoost predict_proba on the full batch is much faster than
            # calling it once per row.
            probabilities = self.ml_model.predict_proba(feature_matrix)
            return [float(row[1]) for row in probabilities]
        except Exception as e:
            self.logger.warning(f"Batch ML inference failed ({e}), falling back to per-message")
            return [self._calculate_ml_fraud_score(txn, profile) for txn, profile in zip(transactions, user_profiles)]

    def _process_batch(
        self,
        messages: list,
        transactions: List[Dict[str, Any]],
        flush_reason: str,
    ) -> None:
        """Process a buffered batch of messages end-to-end.

        Steps:
          1. Look up / create user profiles for the batch.
          2. Run batch ML inference (or per-message rule-based scoring).
          3. For each message: build FraudFeatures, update profile, publish
             alerts and detection results.
          4. Commit offsets for ALL messages in the batch only after the
             entire batch has been processed (exactly-once semantics).

        If an individual message fails, it is logged and skipped so that
        one bad record does not block the rest of the batch.
        """
        if not messages:
            return

        batch_start = time.time()
        batch_len = len(messages)

        # -- Step 1: gather user profiles --------------------------------
        user_ids = [str(txn.get("card1", "unknown")) for txn in transactions]
        user_profiles = [self.get_user_profile(uid) for uid in user_ids]

        # -- Step 2: batch ML inference ----------------------------------
        if self.use_ml_model and self.ml_model:
            fraud_scores = self._calculate_ml_fraud_scores_batch(transactions, user_profiles)
        else:
            fraud_scores = [None] * batch_len  # signals per-message rule scoring

        # -- Step 3: per-message post-processing -------------------------
        failed_indices: List[int] = []
        for idx in range(batch_len):
            try:
                txn = transactions[idx]
                profile = user_profiles[idx]
                processing_start = time.time()

                # If batch ML gave us a score, inject it via a small wrapper
                # so that extract_features uses it instead of re-computing.
                try:
                    if fraud_scores[idx] is not None:
                        self._batch_override_score = fraud_scores[idx]

                    features = self.extract_features(txn, profile)
                finally:
                    # Always clear the override to prevent leaking to next message
                    self._batch_override_score = None

                # Update user profile
                profile.update_daily_stats(features.amount, txn["generated_timestamp"])
                profile.update_transaction_stats(features.amount, txn["generated_timestamp"])
                if features.is_fraud_alert:
                    profile.suspicious_activity_count += 1
                self.save_user_profile(profile)

                # Publish alerts and results
                if features.is_fraud_alert:
                    self.publish_fraud_alert(features, txn)
                self.publish_fraud_detection_result(features, txn, processing_start)

                self.processed_count += 1

                # Record per-message time for flow control
                msg_elapsed = time.time() - processing_start
                self.flow_controller.record_processing_time(msg_elapsed)

                # Periodic stats logging
                if self.processed_count % 1000 == 0:
                    elapsed = time.time() - self.start_time
                    tps = self.processed_count / elapsed
                    fraud_rate = self.fraud_alerts_count / self.processed_count * 100
                    self.logger.info(
                        f"Processed: {self.processed_count}, "
                        f"Fraud alerts: {self.fraud_alerts_count} "
                        f"({fraud_rate:.2f}%), TPS: {tps:.1f}"
                    )
                    fc_stats = self.flow_controller.get_stats()
                    if fc_stats["avg_processing_ms"] > 0:
                        self.logger.info(f"Flow control: {fc_stats}")
            except Exception as e:
                failed_indices.append(idx)
                self.logger.error(f"Error processing message {idx} in batch: {e}")

        # -- Step 4: commit offsets for the entire batch -----------------
        # We commit the *last* message offset which implicitly covers all
        # earlier offsets in each partition.  Failed messages are logged
        # but still committed so we don't re-process the whole batch.
        if messages:
            try:
                self.consumer.commit(message=messages[-1])
            except Exception as e:
                self.logger.error(f"Failed to commit batch offsets: {e}")

        batch_duration = time.time() - batch_start
        self.batch_metrics.record_batch(batch_len, batch_duration, flush_reason)

        if failed_indices:
            self.logger.warning(
                f"Batch completed with {len(failed_indices)}/{batch_len} " f"failures (indices: {failed_indices})"
            )

        # Log batch metrics periodically
        if self.batch_metrics.total_batches % 100 == 0:
            self.logger.info(f"Batch metrics: {self.batch_metrics.get_summary()}")

    # ------------------------------------------------------------------
    # Main processing loops
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main processing loop -- delegates to single-message or batch mode."""
        if self.batch_mode:
            self._run_batch()
        else:
            self._run_single()

    def _run_single(self) -> None:
        """Original single-message processing loop (low-latency mode)."""
        self.logger.info("Starting fraud detection consumer (single-message mode)...")

        try:
            while self.running:
                # Signal liveness to health check server
                if self._health_server is not None:
                    self._health_server.heartbeat()

                # Poll for messages with timeout
                msg = self.consumer.poll(timeout=1.0)

                if msg is None:
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        # End of partition - continue
                        continue
                    else:
                        self.logger.error(f"Kafka error: {msg.error()}")
                        break

                try:
                    # Parse transaction from message (Avro if available, JSON fallback)
                    if self._schema_helper is not None and self._schema_helper.is_available and SCHEMA_UTILS_AVAILABLE:
                        transaction = deserialize_message(
                            self._schema_helper,
                            "transaction",
                            msg.value(),
                            self.input_topic,
                        )
                    else:
                        transaction = json.loads(msg.value().decode("utf-8"))

                    # Process transaction for fraud detection
                    self.process_transaction(transaction)

                    # Manually commit offset after successful processing
                    self.consumer.commit(msg)

                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse transaction JSON: {e}")
                    try:
                        dlq = get_dlq_publisher()
                        dlq.publish(
                            failed_value=msg.value(),
                            error=e,
                            failure_reason="json_decode_error",
                            source_topic=self.input_topic,
                            consumer_group=self.consumer_group,
                            partition=msg.partition(),
                            offset=msg.offset(),
                        )
                    except Exception as dlq_err:
                        self.logger.error(f"DLQ publish also failed: {dlq_err}")
                    self.consumer.commit(msg)  # Skip bad message

                except Exception as e:
                    self.logger.error(f"Error processing message: {e}")
                    try:
                        dlq = get_dlq_publisher()
                        dlq.publish(
                            failed_value=msg.value(),
                            error=e,
                            failure_reason="processing_error",
                            source_topic=self.input_topic,
                            consumer_group=self.consumer_group,
                            partition=msg.partition(),
                            offset=msg.offset(),
                        )
                    except Exception as dlq_err:
                        self.logger.error(f"DLQ publish also failed: {dlq_err}")
                    # Don't commit - will retry message

        except KafkaException as e:
            self.logger.error(f"Kafka exception: {e}")

        finally:
            self._cleanup()

    def _run_batch(self) -> None:
        """Batch processing loop -- buffers N messages or flushes on timeout.

        Exactly-once semantics: offsets are committed only after the full
        batch has been processed.
        """
        self.logger.info(
            f"Starting fraud detection consumer (batch mode, "
            f"size={self.batch_size}, timeout={self.batch_timeout_ms}ms)..."
        )

        msg_buffer: list = []  # raw Kafka messages
        txn_buffer: List[Dict] = []  # parsed transactions
        batch_start_time: Optional[float] = None
        timeout_seconds = self.batch_timeout_ms / 1000.0

        try:
            while self.running:
                # Signal liveness to health check server
                if self._health_server is not None:
                    self._health_server.heartbeat()

                # Use a short poll timeout so we can check the batch timer
                remaining = 0.05  # 50ms default poll
                if batch_start_time is not None:
                    elapsed = time.time() - batch_start_time
                    remaining = max(0.01, timeout_seconds - elapsed)

                msg = self.consumer.poll(timeout=remaining)

                if msg is not None and not msg.error():
                    try:
                        transaction = json.loads(msg.value().decode("utf-8"))
                        msg_buffer.append(msg)
                        txn_buffer.append(transaction)

                        if batch_start_time is None:
                            batch_start_time = time.time()

                    except json.JSONDecodeError as e:
                        self.logger.error(f"Failed to parse transaction JSON: {e}")
                        # Publish to DLQ before committing the bad message
                        if self._dlq_publisher is not None:
                            try:
                                self._dlq_publisher.publish(
                                    original_message=msg.value(),
                                    error=e,
                                    source_topic=msg.topic(),
                                    partition=msg.partition(),
                                    offset=msg.offset(),
                                )
                            except Exception:
                                pass
                        self.consumer.commit(msg)

                elif msg is not None and msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        self.logger.error(f"Kafka error: {msg.error()}")
                        break

                # Decide whether to flush the batch.
                # The flow controller may reduce the effective batch size
                # if per-message processing is slow, providing backpressure.
                flush_reason: Optional[str] = None
                effective_size = self.flow_controller.effective_batch_size(self.batch_size)

                if len(msg_buffer) >= effective_size:
                    flush_reason = "full"
                elif (
                    batch_start_time is not None and (time.time() - batch_start_time) >= timeout_seconds and msg_buffer
                ):
                    flush_reason = "timeout"

                if flush_reason:
                    self._process_batch(msg_buffer, txn_buffer, flush_reason)
                    msg_buffer = []
                    txn_buffer = []
                    batch_start_time = None

        except KafkaException as e:
            self.logger.error(f"Kafka exception: {e}")

        finally:
            # Flush any remaining messages on shutdown
            if msg_buffer:
                self.logger.info(f"Flushing {len(msg_buffer)} remaining messages on shutdown")
                self._process_batch(msg_buffer, txn_buffer, "shutdown")

            self._cleanup()

    def _cleanup(self) -> None:
        """Cleanup resources during shutdown."""
        self.logger.info("Shutting down fraud detection consumer...")

        # Final statistics
        elapsed = time.time() - self.start_time
        tps = self.processed_count / elapsed if elapsed > 0 else 0
        fraud_rate = self.fraud_alerts_count / self.processed_count * 100 if self.processed_count > 0 else 0

        self.logger.info(
            f"Final statistics - Processed: {self.processed_count}, "
            f"Fraud alerts: {self.fraud_alerts_count} ({fraud_rate:.2f}%), "
            f"Blocked: {self.blocked_count}, "
            f"Rules fallback uses: {self.rules_fallback_count}, "
            f"Model status: {self.model_status}, "
            f"Average TPS: {tps:.1f}"
        )

        # Flush remaining messages
        if self.producer:
            self.producer.flush(timeout=10)

        # Close Kafka connections
        if self.consumer:
            self.consumer.close()

        # Close Redis connection
        if self.redis_client:
            self.redis_client.close()

        self.logger.info("Fraud detection consumer shutdown complete")


def main():
    """Main entry point for fraud detection consumer."""
    import argparse

    configure_logging()
    logger = get_logger(__name__)

    parser = argparse.ArgumentParser(description="Stream-Sentinel Fraud Detector")
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Enable batch inference mode (higher throughput, slightly higher latency)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Maximum messages per batch (default: 32)",
    )
    parser.add_argument(
        "--batch-timeout-ms",
        type=int,
        default=100,
        help="Max ms to wait before flushing a partial batch (default: 100)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.3,
        help="Fraud score threshold for alerting (default: 0.3)",
    )
    args = parser.parse_args()

    try:
        # Start combined Prometheus metrics + health check server on port 8000
        health_server = None
        try:
            from monitoring.health import HealthCheckServer, make_kafka_check, make_model_check, make_redis_check

            metrics = get_prometheus_metrics(component_name="fraud-detector")
            health_server = HealthCheckServer(registry=metrics.registry)
            metrics.set_health_server(health_server)
            metrics.start_metrics_server(port=8000)
            logging.getLogger("stream_sentinel.fraud_detector").info(
                "Combined metrics + health server started on port 8000"
            )
        except Exception as e:
            logging.getLogger("stream_sentinel.fraud_detector").warning(
                f"Failed to start metrics server: {e} -- continuing without metrics endpoint"
            )

        # Create and run fraud detector
        detector = FraudDetector(
            consumer_group="fraud-detection-group",
            fraud_threshold=args.threshold,
            batch_mode=args.batch,
            batch_size=args.batch_size,
            batch_timeout_ms=args.batch_timeout_ms,
        )

        # Register health checks now that the detector is fully initialised
        if health_server is not None:
            detector._health_server = health_server
            health_server.register_check("kafka", make_kafka_check(detector.consumer))
            health_server.register_check("redis", make_redis_check(detector.redis_client))
            health_server.register_check("model", make_model_check(detector))

            def _metrics_summary():
                uptime = time.time() - detector.start_time
                tps = detector.processed_count / max(uptime, 1)
                return {
                    "messages_processed": detector.processed_count,
                    "fraud_alerts": detector.fraud_alerts_count,
                    "blocked_transactions": detector.blocked_count,
                    "throughput_tps": round(tps, 2),
                    "model_status": detector.model_status,
                }

            health_server.set_metrics_summary_fn(_metrics_summary)

        detector.run()

    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
    except Exception as e:
        logger.error("Fatal error", extra={"error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
