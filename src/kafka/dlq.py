"""
Dead Letter Queue (DLQ) publisher for Stream-Sentinel.

When a consumer cannot process a message (bad JSON, schema violation,
persistence failure, etc.) it should call ``publish_to_dlq`` instead of
silently dropping the message.  The failed message is wrapped in an error
metadata envelope and published to the ``dead-letter-queue`` Kafka topic
so it can be investigated later without data loss.

Usage:
    from kafka.dlq import get_dlq_publisher

    dlq = get_dlq_publisher()
    dlq.publish(
        failed_value=raw_bytes,
        error=exception_instance,
        failure_reason="json_decode_error",
        source_topic="synthetic-transactions",
        consumer_group="fraud-detection-group",
    )
"""

import json
import logging
import threading
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from confluent_kafka import Producer

from kafka.config import get_kafka_config

logger = logging.getLogger("stream_sentinel.dlq")

# ---------------------------------------------------------------------------
# Prometheus counter -- importable even when prometheus_client is absent.
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter

    dlq_messages_total = Counter(
        "dlq_messages_total",
        "Total messages sent to the dead-letter-queue",
        ["failure_reason", "source_topic", "consumer_group"],
    )
except ImportError:
    dlq_messages_total = None


# ---------------------------------------------------------------------------
# DLQ Publisher
# ---------------------------------------------------------------------------

DLQ_TOPIC = "dead-letter-queue"


class DeadLetterQueuePublisher:
    """Publishes failed messages to the dead-letter-queue topic."""

    def __init__(self):
        kafka_cfg = get_kafka_config()
        producer_config = kafka_cfg.get_producer_config("default")
        self._producer = Producer(producer_config)
        self._lock = threading.Lock()
        logger.info("DLQ publisher initialized (topic=%s)", DLQ_TOPIC)

    # ------------------------------------------------------------------
    def publish(
        self,
        failed_value: Any,
        error: Exception,
        failure_reason: str,
        source_topic: str,
        consumer_group: str,
        partition: Optional[int] = None,
        offset: Optional[int] = None,
        extra_context: Optional[dict] = None,
    ) -> None:
        """
        Wrap *failed_value* in an error envelope and publish to the DLQ.

        Parameters
        ----------
        failed_value : Any
            The raw message value that could not be processed.  Bytes are
            decoded to a UTF-8 string on a best-effort basis.
        error : Exception
            The exception that caused the failure.
        failure_reason : str
            Short machine-readable tag (e.g. ``"json_decode_error"``,
            ``"persistence_failure"``).
        source_topic : str
            Kafka topic the message was originally consumed from.
        consumer_group : str
            Consumer group ID of the failing consumer.
        partition : int, optional
            Source partition (for traceability).
        offset : int, optional
            Source offset (for traceability).
        extra_context : dict, optional
            Arbitrary metadata to attach.
        """
        try:
            # Best-effort decode of the raw value for JSON storage.
            if isinstance(failed_value, (bytes, bytearray)):
                try:
                    value_str = failed_value.decode("utf-8")
                except Exception:
                    value_str = repr(failed_value)
            elif failed_value is None:
                value_str = None
            else:
                value_str = str(failed_value)

            envelope = {
                "failed_message": value_str,
                "error_type": type(error).__name__,
                "error_message": str(error),
                "error_traceback": traceback.format_exception(type(error), error, error.__traceback__),
                "failure_reason": failure_reason,
                "source_topic": source_topic,
                "consumer_group": consumer_group,
                "source_partition": partition,
                "source_offset": offset,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "extra_context": extra_context or {},
            }

            payload = json.dumps(envelope, default=str).encode("utf-8")

            with self._lock:
                self._producer.produce(
                    DLQ_TOPIC,
                    value=payload,
                    key=failure_reason.encode("utf-8"),
                    callback=self._delivery_cb,
                )
                self._producer.poll(0)

            # Increment Prometheus counter when available.
            if dlq_messages_total is not None:
                dlq_messages_total.labels(
                    failure_reason=failure_reason,
                    source_topic=source_topic,
                    consumer_group=consumer_group,
                ).inc()

            logger.warning(
                "Published to DLQ: reason=%s topic=%s group=%s error=%s",
                failure_reason,
                source_topic,
                consumer_group,
                error,
            )

        except Exception as pub_err:
            # Never let DLQ publishing crash the consumer.
            logger.error(
                "Failed to publish to DLQ (reason=%s): %s",
                failure_reason,
                pub_err,
            )

    # ------------------------------------------------------------------
    def flush(self, timeout: float = 5.0) -> None:
        """Flush pending DLQ messages (e.g. during shutdown)."""
        self._producer.flush(timeout=timeout)

    # ------------------------------------------------------------------
    @staticmethod
    def _delivery_cb(err, msg):
        if err is not None:
            logger.error("DLQ delivery failed: %s", err)
        else:
            logger.debug(
                "DLQ message delivered to %s [%d] @ %d",
                msg.topic(),
                msg.partition(),
                msg.offset(),
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_instance: Optional[DeadLetterQueuePublisher] = None
_instance_lock = threading.Lock()


def get_dlq_publisher() -> DeadLetterQueuePublisher:
    """Return a module-level singleton DLQ publisher."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = DeadLetterQueuePublisher()
        return _instance
