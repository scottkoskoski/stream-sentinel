# /stream-sentinel/src/ml/online_learning/retraining_trigger.py

"""
Retraining Trigger -- listens for drift alerts and decides whether to
launch a model retraining job.

Design:
  * Consumes from ``model-drift-alerts`` topic (published by LiveDriftMonitor
    and/or DriftDetector).
  * Evaluates three guards before scheduling retraining:
      1. **Minimum data volume** -- at least N labeled samples since last retrain.
      2. **Cooldown period**     -- at most one retrain every X hours.
      3. **Severity threshold**  -- only retrain on significant drift (PSI above a
         configurable floor or severity >= configured level).
  * When retraining is warranted, publishes a job message to
    ``model-retraining-jobs`` topic.
  * After retraining (orchestrated externally), the new model must pass a
    validation gate: its AUC must beat the current production AUC by a
    configurable delta.  ``validate_retrained_model()`` encapsulates this check.
  * All parameters are exposed in RetrainingConfig with sensible defaults.
  * Degrades gracefully when Redis or Kafka are unavailable.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class RetrainingConfig:
    """All tunables for the retraining trigger."""

    # Kafka
    kafka_servers: str = "localhost:9092"
    drift_alerts_topic: str = "model-drift-alerts"
    retraining_jobs_topic: str = "model-retraining-jobs"
    consumer_group: str = "retraining-trigger-group"

    # Guard 1 -- minimum labeled samples since last retrain
    min_labeled_samples: int = 5000

    # Guard 2 -- cooldown
    cooldown_hours: float = 6.0

    # Guard 3 -- severity threshold
    # Retraining is only triggered when PSI >= this value ...
    min_psi_for_retrain: float = 0.15
    # ... OR severity is at least this level (low/medium/high/critical)
    min_severity_for_retrain: str = "medium"

    # Validation gate -- new model must beat current AUC by this margin
    auc_improvement_threshold: float = 0.005  # 0.5 %

    # Redis for state persistence
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None
    redis_db: int = 4
    redis_key_prefix: str = "retrain_trigger"

    # Operational
    poll_timeout_seconds: float = 1.0
    max_consecutive_errors: int = 10


# Severity ordering for comparisons
_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# ---------------------------------------------------------------------------
# RetrainingTrigger
# ---------------------------------------------------------------------------

class RetrainingTrigger:
    """
    Listens for model drift alerts and conditionally publishes retraining
    jobs after evaluating guard conditions.

    Typical lifecycle::

        trigger = RetrainingTrigger(config)
        trigger.run()  # blocking event loop
    """

    def __init__(self, config: Optional[RetrainingConfig] = None):
        self.config = config or RetrainingConfig()
        self._consumer = None
        self._producer = None
        self._redis = None
        self._redis_available = False
        self._running = False

        # In-memory state (also persisted to Redis when available)
        self._last_retrain_time: Optional[datetime] = None
        self._labeled_sample_count: int = 0
        self._current_production_auc: float = 0.0
        self._alerts_processed: int = 0
        self._retrains_triggered: int = 0

        self._init_kafka()
        self._init_redis()
        self._load_state()

        logger.info(
            "RetrainingTrigger initialised "
            "(cooldown=%sh, min_samples=%d, min_psi=%.2f, auc_delta=%.4f)",
            self.config.cooldown_hours,
            self.config.min_labeled_samples,
            self.config.min_psi_for_retrain,
            self.config.auc_improvement_threshold,
        )

    # ------------------------------------------------------------------
    # Dependency init
    # ------------------------------------------------------------------

    def _init_kafka(self) -> None:
        try:
            from confluent_kafka import Consumer, Producer

            consumer_config = {
                "bootstrap.servers": self.config.kafka_servers,
                "group.id": self.config.consumer_group,
                "auto.offset.reset": "latest",
                "enable.auto.commit": True,
            }
            self._consumer = Consumer(consumer_config)
            self._consumer.subscribe([self.config.drift_alerts_topic])

            self._producer = Producer({
                "bootstrap.servers": self.config.kafka_servers,
                "linger.ms": 10,
                "compression.type": "lz4",
            })
            logger.info(
                "Kafka consumer/producer ready (topic=%s)",
                self.config.drift_alerts_topic,
            )
        except Exception as exc:
            logger.error("Failed to initialise Kafka: %s", exc)
            raise

    def _init_redis(self) -> None:
        try:
            import redis as _redis

            self._redis = _redis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password,
                db=self.config.redis_db,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            self._redis.ping()
            self._redis_available = True
        except Exception as exc:
            logger.warning("Redis unavailable (%s); state will be in-memory only.", exc)
            self._redis = None
            self._redis_available = False

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Load persisted state from Redis."""
        if not self._redis_available:
            return
        try:
            prefix = self.config.redis_key_prefix
            data = self._redis.get(f"{prefix}:state")
            if data:
                state = json.loads(data)
                if state.get("last_retrain_time"):
                    self._last_retrain_time = datetime.fromisoformat(
                        state["last_retrain_time"]
                    )
                self._labeled_sample_count = state.get("labeled_sample_count", 0)
                self._current_production_auc = state.get("current_production_auc", 0.0)
                self._retrains_triggered = state.get("retrains_triggered", 0)
                logger.info("Loaded retraining state from Redis")
        except Exception as exc:
            logger.warning("Failed to load state from Redis: %s", exc)

    def _save_state(self) -> None:
        """Persist current state to Redis."""
        if not self._redis_available:
            return
        try:
            prefix = self.config.redis_key_prefix
            state = {
                "last_retrain_time": (
                    self._last_retrain_time.isoformat()
                    if self._last_retrain_time
                    else None
                ),
                "labeled_sample_count": self._labeled_sample_count,
                "current_production_auc": self._current_production_auc,
                "retrains_triggered": self._retrains_triggered,
                "updated_at": datetime.now().isoformat(),
            }
            self._redis.set(f"{prefix}:state", json.dumps(state))
        except Exception as exc:
            logger.warning("Failed to save state to Redis: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_labeled_sample_count(self, count: int) -> None:
        """Increment the labeled-sample counter (called by feedback processor)."""
        self._labeled_sample_count += count
        self._save_state()

    def set_production_auc(self, auc: float) -> None:
        """Update the current production model's AUC for validation gating."""
        self._current_production_auc = auc
        self._save_state()

    def validate_retrained_model(self, new_auc: float) -> bool:
        """
        Validate that a retrained model passes the improvement gate.

        Returns True if the new AUC exceeds the current production AUC
        by at least ``auc_improvement_threshold``.
        """
        delta = new_auc - self._current_production_auc
        passed = delta >= self.config.auc_improvement_threshold

        logger.info(
            "Validation gate: new_auc=%.4f current_auc=%.4f delta=%.4f threshold=%.4f passed=%s",
            new_auc,
            self._current_production_auc,
            delta,
            self.config.auc_improvement_threshold,
            passed,
        )
        return passed

    def get_status(self) -> Dict[str, Any]:
        """Return a status dict for monitoring / health checks."""
        return {
            "alerts_processed": self._alerts_processed,
            "retrains_triggered": self._retrains_triggered,
            "labeled_sample_count": self._labeled_sample_count,
            "current_production_auc": self._current_production_auc,
            "last_retrain_time": (
                self._last_retrain_time.isoformat()
                if self._last_retrain_time
                else None
            ),
            "cooldown_remaining_hours": self._cooldown_remaining_hours(),
            "redis_available": self._redis_available,
        }

    # ------------------------------------------------------------------
    # Event loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Blocking event loop: consume drift alerts and evaluate retraining.

        Runs until ``stop()`` is called or a fatal error occurs.
        """
        import signal

        self._running = True

        def _handle_signal(signum, frame):
            logger.info("Received signal %d, stopping retraining trigger...", signum)
            self._running = False

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        logger.info("RetrainingTrigger event loop started")
        consecutive_errors = 0

        while self._running:
            try:
                msg = self._consumer.poll(timeout=self.config.poll_timeout_seconds)
                if msg is None:
                    continue
                if msg.error():
                    logger.warning("Kafka consumer error: %s", msg.error())
                    consecutive_errors += 1
                    if consecutive_errors >= self.config.max_consecutive_errors:
                        logger.error("Too many consecutive Kafka errors; stopping.")
                        break
                    continue

                consecutive_errors = 0
                self._handle_alert(msg)

            except Exception as exc:
                logger.error("Unexpected error in event loop: %s", exc)
                consecutive_errors += 1
                if consecutive_errors >= self.config.max_consecutive_errors:
                    break

        self._cleanup()

    def stop(self) -> None:
        """Signal the event loop to stop."""
        self._running = False

    # ------------------------------------------------------------------
    # Alert handling
    # ------------------------------------------------------------------

    def _handle_alert(self, msg) -> None:
        """Process a single drift alert message."""
        try:
            alert = json.loads(msg.value().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("Malformed drift alert message: %s", exc)
            return

        self._alerts_processed += 1

        logger.info(
            "Drift alert received: psi=%.4f severity=%s",
            alert.get("psi_score", 0.0),
            alert.get("severity", "unknown"),
        )

        # Evaluate guard conditions
        if not self._should_retrain(alert):
            return

        # Publish retraining job
        self._publish_retraining_job(alert)

    def _should_retrain(self, alert: Dict[str, Any]) -> bool:
        """Evaluate the three guard conditions."""

        # Guard 1 -- minimum labeled samples
        if self._labeled_sample_count < self.config.min_labeled_samples:
            logger.info(
                "Retrain skipped: insufficient labeled samples (%d < %d)",
                self._labeled_sample_count,
                self.config.min_labeled_samples,
            )
            return False

        # Guard 2 -- cooldown period
        if self._last_retrain_time is not None:
            cooldown_delta = timedelta(hours=self.config.cooldown_hours)
            if datetime.now() - self._last_retrain_time < cooldown_delta:
                remaining = self._cooldown_remaining_hours()
                logger.info(
                    "Retrain skipped: cooldown active (%.1f hours remaining)",
                    remaining,
                )
                return False

        # Guard 3 -- severity / PSI threshold
        psi = alert.get("psi_score", 0.0)
        severity = alert.get("severity", "low")
        severity_rank = _SEVERITY_ORDER.get(severity, 0)
        min_rank = _SEVERITY_ORDER.get(self.config.min_severity_for_retrain, 1)

        if psi < self.config.min_psi_for_retrain and severity_rank < min_rank:
            logger.info(
                "Retrain skipped: drift not severe enough "
                "(psi=%.4f < %.4f, severity=%s < %s)",
                psi, self.config.min_psi_for_retrain,
                severity, self.config.min_severity_for_retrain,
            )
            return False

        return True

    def _publish_retraining_job(self, alert: Dict[str, Any]) -> None:
        """Publish a retraining job to the jobs topic."""
        job = {
            "job_type": "model_retraining",
            "trigger": "drift_detection",
            "timestamp": datetime.now().isoformat(),
            "drift_alert": alert,
            "labeled_samples_available": self._labeled_sample_count,
            "current_production_auc": self._current_production_auc,
            "validation_gate": {
                "min_auc_improvement": self.config.auc_improvement_threshold,
                "current_auc": self._current_production_auc,
            },
            "priority": "high" if alert.get("severity") in ("high", "critical") else "medium",
        }

        try:
            self._producer.produce(
                self.config.retraining_jobs_topic,
                key=f"retrain_{int(time.time())}",
                value=json.dumps(job),
            )
            self._producer.poll(0)

            # Update state
            self._last_retrain_time = datetime.now()
            self._retrains_triggered += 1
            # Reset labeled sample counter after scheduling retrain
            self._labeled_sample_count = 0
            self._save_state()

            logger.warning(
                "RETRAINING JOB PUBLISHED: psi=%.4f severity=%s priority=%s",
                alert.get("psi_score", 0.0),
                alert.get("severity", "unknown"),
                job["priority"],
            )

        except Exception as exc:
            logger.error("Failed to publish retraining job: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cooldown_remaining_hours(self) -> float:
        if self._last_retrain_time is None:
            return 0.0
        elapsed = (datetime.now() - self._last_retrain_time).total_seconds() / 3600.0
        remaining = self.config.cooldown_hours - elapsed
        return max(0.0, remaining)

    def _cleanup(self) -> None:
        logger.info("Shutting down RetrainingTrigger...")
        if self._producer:
            self._producer.flush(timeout=5)
        if self._consumer:
            self._consumer.close()
        logger.info("RetrainingTrigger shutdown complete")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """Run the retraining trigger as a standalone service."""
    import argparse

    parser = argparse.ArgumentParser(description="Model Retraining Trigger")
    parser.add_argument("--kafka", default="localhost:9092", help="Kafka bootstrap servers")
    parser.add_argument("--cooldown", type=float, default=6.0, help="Cooldown hours between retrains")
    parser.add_argument("--min-samples", type=int, default=5000, help="Min labeled samples")
    parser.add_argument("--min-psi", type=float, default=0.15, help="Min PSI to trigger retrain")
    args = parser.parse_args()

    config = RetrainingConfig(
        kafka_servers=args.kafka,
        cooldown_hours=args.cooldown,
        min_labeled_samples=args.min_samples,
        min_psi_for_retrain=args.min_psi,
    )

    trigger = RetrainingTrigger(config)
    trigger.run()


if __name__ == "__main__":
    main()
