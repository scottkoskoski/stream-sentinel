# /stream-sentinel/src/ml/online_learning/live_drift_monitor.py

"""
Live Drift Monitor -- lightweight utility for embedding periodic PSI-based
drift detection into a streaming consumer (e.g. fraud_detector.py).

Design goals:
  * Reuse the statistical primitives from drift_detector.py.
  * Keep consumer-specific code minimal -- the consumer just calls
    ``monitor.record_score(score)`` after every prediction.
  * Store the baseline distribution in Redis; recompute baseline on first run.
  * Publish drift alerts to the ``model-drift-alerts`` Kafka topic.
  * Degrade gracefully when Redis or Kafka are unavailable.
"""

import json
import logging
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration dataclass (plain dict accepted too)
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    # How many scores to collect before running a drift check
    "check_interval": 1000,
    # Number of bins for PSI histogram
    "psi_bins": 10,
    # PSI threshold for triggering a drift alert
    "psi_threshold": 0.15,
    # Redis key prefix
    "redis_key_prefix": "drift_monitor",
    # Baseline window size (scores stored for first-run calibration)
    "baseline_window_size": 5000,
    # Kafka topic for drift alerts
    "drift_alerts_topic": "model-drift-alerts",
    # Redis connection params
    "redis_host": "localhost",
    "redis_port": 6379,
    "redis_password": None,
    "redis_db": 4,
    # Kafka bootstrap servers
    "kafka_servers": "localhost:9092",
}


class LiveDriftMonitor:
    """
    Lightweight monitor that tracks fraud score distribution and periodically
    checks for drift using Population Stability Index (PSI).

    Usage in a consumer::

        monitor = LiveDriftMonitor(config)
        # ... in processing loop:
        monitor.record_score(fraud_score)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = {**_DEFAULT_CONFIG, **(config or {})}
        self._redis = None
        self._producer = None
        self._redis_available = False
        self._kafka_available = False

        # Internal score buffer
        self._score_buffer: deque = deque(maxlen=self.config["check_interval"] * 2)
        self._scores_since_last_check = 0
        self._total_scores = 0
        self._last_psi: Optional[float] = None
        self._baseline_distribution: Optional[np.ndarray] = None
        self._bin_edges: Optional[np.ndarray] = None

        # Attempt connection to dependencies (non-fatal)
        self._init_redis()
        self._init_kafka()

        # Try to load baseline from Redis
        self._load_baseline()

        logger.info(
            "LiveDriftMonitor initialised (redis=%s, kafka=%s, interval=%d)",
            self._redis_available,
            self._kafka_available,
            self.config["check_interval"],
        )

    # ------------------------------------------------------------------
    # Dependency init (graceful degradation)
    # ------------------------------------------------------------------

    def _init_redis(self) -> None:
        try:
            import redis as _redis

            self._redis = _redis.Redis(
                host=self.config["redis_host"],
                port=self.config["redis_port"],
                password=self.config["redis_password"],
                db=self.config["redis_db"],
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            self._redis.ping()
            self._redis_available = True
        except Exception as exc:
            logger.warning(
                "LiveDriftMonitor: Redis unavailable (%s); " "baseline will be computed in-memory only.",
                exc,
            )
            self._redis = None
            self._redis_available = False

    def _init_kafka(self) -> None:
        try:
            from confluent_kafka import Producer

            self._producer = Producer(
                {
                    "bootstrap.servers": self.config["kafka_servers"],
                    "linger.ms": 10,
                    "compression.type": "lz4",
                }
            )
            self._kafka_available = True
        except Exception as exc:
            logger.warning(
                "LiveDriftMonitor: Kafka producer unavailable (%s); " "drift alerts will be logged only.",
                exc,
            )
            self._producer = None
            self._kafka_available = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_score(self, score: float) -> Optional[Dict[str, Any]]:
        """
        Record a fraud score and, if the check interval has been reached,
        run a PSI-based drift check.

        Returns a drift-alert dict when drift is detected, else None.
        """
        self._score_buffer.append(score)
        self._scores_since_last_check += 1
        self._total_scores += 1

        if self._scores_since_last_check >= self.config["check_interval"]:
            return self._run_drift_check()
        return None

    @property
    def last_psi(self) -> Optional[float]:
        """Most recent PSI value (None if no check has run yet)."""
        return self._last_psi

    def get_status(self) -> Dict[str, Any]:
        """Return a status dict suitable for health-check endpoints."""
        return {
            "total_scores": self._total_scores,
            "buffer_size": len(self._score_buffer),
            "last_psi": self._last_psi,
            "baseline_loaded": self._baseline_distribution is not None,
            "redis_available": self._redis_available,
            "kafka_available": self._kafka_available,
        }

    # ------------------------------------------------------------------
    # Drift detection logic
    # ------------------------------------------------------------------

    def _run_drift_check(self) -> Optional[Dict[str, Any]]:
        """Compute PSI between current score window and baseline."""
        self._scores_since_last_check = 0

        current_scores = np.array(list(self._score_buffer))
        if len(current_scores) < 50:
            return None

        # If no baseline yet, calibrate now
        if self._baseline_distribution is None:
            self._calibrate_baseline(current_scores)
            return None

        # Build current histogram using same bin edges
        current_hist, _ = np.histogram(current_scores, bins=self._bin_edges)
        current_dist = current_hist / current_hist.sum()

        psi = self._compute_psi(self._baseline_distribution, current_dist)
        self._last_psi = float(psi)

        logger.info(
            "Drift check: PSI=%.4f (threshold=%.4f, scores=%d)",
            psi,
            self.config["psi_threshold"],
            len(current_scores),
        )

        # Expose metric for Prometheus scraping (if prometheus_client available)
        self._emit_metric(psi)

        if psi > self.config["psi_threshold"]:
            alert = self._build_alert(psi, current_scores)
            self._publish_alert(alert)
            return alert

        return None

    def _calibrate_baseline(self, scores: np.ndarray) -> None:
        """Store the first batch of scores as the baseline distribution."""
        n_bins = self.config["psi_bins"]
        # Clip scores to [0, 1] range for fraud probabilities
        clipped = np.clip(scores, 0.0, 1.0)
        self._bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
        hist, _ = np.histogram(clipped, bins=self._bin_edges)
        self._baseline_distribution = hist / hist.sum()

        logger.info(
            "Baseline calibrated from %d scores (bins=%d)",
            len(scores),
            n_bins,
        )

        # Persist to Redis
        self._save_baseline()

    def _load_baseline(self) -> None:
        """Try to load previously saved baseline from Redis."""
        if not self._redis_available:
            return
        try:
            prefix = self.config["redis_key_prefix"]
            data = self._redis.get(f"{prefix}:baseline")
            if data:
                payload = json.loads(data)
                self._baseline_distribution = np.array(payload["distribution"])
                self._bin_edges = np.array(payload["bin_edges"])
                logger.info(
                    "Loaded baseline from Redis (%d bins)",
                    len(self._baseline_distribution),
                )
        except Exception as exc:
            logger.warning("Failed to load baseline from Redis: %s", exc)

    def _save_baseline(self) -> None:
        """Persist baseline distribution to Redis."""
        if not self._redis_available or self._baseline_distribution is None:
            return
        try:
            prefix = self.config["redis_key_prefix"]
            payload = {
                "distribution": self._baseline_distribution.tolist(),
                "bin_edges": self._bin_edges.tolist(),
                "calibrated_at": datetime.now().isoformat(),
                "num_scores": self._total_scores,
            }
            self._redis.set(f"{prefix}:baseline", json.dumps(payload))
            # Also set expiry of 30 days so stale baselines get cleaned up
            self._redis.expire(f"{prefix}:baseline", 2592000)
        except Exception as exc:
            logger.warning("Failed to save baseline to Redis: %s", exc)

    # ------------------------------------------------------------------
    # PSI computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_psi(baseline: np.ndarray, current: np.ndarray) -> float:
        """
        Compute Population Stability Index between two distributions.

        Both arrays should be normalised probability vectors of equal length.
        """
        eps = 1e-8
        p = np.where(baseline == 0, eps, baseline)
        q = np.where(current == 0, eps, current)
        return float(np.sum((q - p) * np.log(q / p)))

    # ------------------------------------------------------------------
    # Alert publishing
    # ------------------------------------------------------------------

    def _build_alert(self, psi: float, current_scores: np.ndarray) -> Dict[str, Any]:
        return {
            "alert_type": "model_drift",
            "drift_method": "psi",
            "psi_score": float(psi),
            "psi_threshold": self.config["psi_threshold"],
            "timestamp": datetime.now().isoformat(),
            "scores_analyzed": int(len(current_scores)),
            "current_mean_score": float(current_scores.mean()),
            "current_std_score": float(current_scores.std()),
            "total_scores_seen": self._total_scores,
            "severity": self._classify_severity(psi),
        }

    @staticmethod
    def _classify_severity(psi: float) -> str:
        if psi >= 0.5:
            return "critical"
        if psi >= 0.25:
            return "high"
        if psi >= 0.15:
            return "medium"
        return "low"

    def _publish_alert(self, alert: Dict[str, Any]) -> None:
        """Publish drift alert to Kafka and log."""
        logger.warning(
            "DRIFT DETECTED: PSI=%.4f severity=%s",
            alert["psi_score"],
            alert["severity"],
        )

        if self._kafka_available and self._producer is not None:
            try:
                topic = self.config["drift_alerts_topic"]
                self._producer.produce(
                    topic,
                    key=f"drift_{int(time.time())}",
                    value=json.dumps(alert),
                )
                self._producer.poll(0)
                logger.info("Drift alert published to %s", topic)
            except Exception as exc:
                logger.error("Failed to publish drift alert to Kafka: %s", exc)

    # ------------------------------------------------------------------
    # Prometheus metric (optional)
    # ------------------------------------------------------------------

    _psi_gauge = None  # class-level cached Prometheus Gauge

    def _emit_metric(self, psi: float) -> None:
        """Set a Prometheus gauge if prometheus_client is installed."""
        try:
            if LiveDriftMonitor._psi_gauge is None:
                from prometheus_client import Gauge

                LiveDriftMonitor._psi_gauge = Gauge(
                    "fraud_model_drift_psi",
                    "Population Stability Index for fraud score distribution",
                )
            LiveDriftMonitor._psi_gauge.set(psi)
        except Exception:
            pass  # prometheus_client not installed
