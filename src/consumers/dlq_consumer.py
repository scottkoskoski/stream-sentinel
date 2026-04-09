#!/usr/bin/env python3
"""
Dead Letter Queue (DLQ) Consumer for Stream-Sentinel.

Reads failed messages from the ``dead-letter-queue`` Kafka topic, logs
complete error context, and persists each record to a JSON-lines file
for offline investigation.

Usage:
    python src/consumers/dlq_consumer.py
    python src/consumers/dlq_consumer.py --output /var/log/stream-sentinel/dlq.jsonl
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from confluent_kafka import Consumer, KafkaError, KafkaException

# Add src to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from kafka.config import get_kafka_config

# Distributed tracing
try:
    from tracing.correlation import extract_correlation_id
    from tracing.middleware import traced_consume

    TRACING_AVAILABLE = True
except ImportError:
    TRACING_AVAILABLE = False

DLQ_TOPIC = "dead-letter-queue"
DEFAULT_OUTPUT_PATH = "/tmp/stream-sentinel-dlq.jsonl"

logger = logging.getLogger("stream_sentinel.dlq_consumer")


class DLQConsumer:
    """Consumes and logs messages from the dead-letter-queue topic."""

    def __init__(self, output_path: str = DEFAULT_OUTPUT_PATH):
        self.output_path = output_path
        self.running = False
        self.processed = 0
        self.start_time = time.time()

        # Ensure output directory exists
        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)

        # Kafka consumer
        kafka_cfg = get_kafka_config()
        consumer_config = kafka_cfg.get_consumer_config(
            consumer_group="dlq-investigation-group",
            consumer_type="analytics",  # earliest offset reset
        )
        self.consumer = Consumer(consumer_config)
        self.consumer.subscribe([DLQ_TOPIC])

        # Health check server reference (set by main() after construction)
        self._health_server = None

        # Signal handling
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info(
            "DLQ consumer initialized -- topic=%s output=%s",
            DLQ_TOPIC,
            self.output_path,
        )

    # ------------------------------------------------------------------
    def _signal_handler(self, signum, _frame):
        logger.info("Received signal %d, shutting down ...", signum)
        self.running = False

    # ------------------------------------------------------------------
    def run(self) -> None:
        """Main processing loop."""
        self.running = True
        logger.info("Starting DLQ consumer ...")

        try:
            while self.running:
                # Signal liveness to health check server
                if self._health_server is not None:
                    self._health_server.heartbeat()

                msg = self.consumer.poll(timeout=1.0)

                if msg is None:
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    logger.error("Kafka error: %s", msg.error())
                    continue

                self._handle_message(msg)

        except KafkaException as exc:
            logger.error("Kafka exception: %s", exc)
        finally:
            self._shutdown()

    # ------------------------------------------------------------------
    def _handle_message(self, msg) -> None:
        """Process a single DLQ message."""
        # Extract tracing context from message headers
        tracing_ctx = None
        if TRACING_AVAILABLE:
            tracing_ctx = traced_consume(msg)

        try:
            raw = msg.value()
            envelope = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            envelope = {"raw_bytes": repr(msg.value())}

        # Augment with consumption metadata
        envelope["_dlq_consumed_at"] = datetime.now(timezone.utc).isoformat()
        envelope["_dlq_partition"] = msg.partition()
        envelope["_dlq_offset"] = msg.offset()

        # Include correlation ID from tracing context or message payload
        correlation_id = None
        if tracing_ctx:
            correlation_id = tracing_ctx.correlation_id
        if correlation_id is None:
            correlation_id = envelope.get("correlation_id")
        if correlation_id:
            envelope["_dlq_correlation_id"] = correlation_id

        # Log with full context
        failure_reason = envelope.get("failure_reason", "unknown")
        source_topic = envelope.get("source_topic", "unknown")
        error_message = envelope.get("error_message", "N/A")

        logger.warning(
            "DLQ message: reason=%s source_topic=%s error=%s correlation_id=%s",
            failure_reason,
            source_topic,
            error_message,
            correlation_id or "none",
        )

        # Persist to JSONL file
        try:
            with open(self.output_path, "a") as fh:
                fh.write(json.dumps(envelope, default=str) + "\n")
        except OSError as io_err:
            logger.error("Failed to write DLQ record to %s: %s", self.output_path, io_err)

        self.consumer.commit(msg)
        self.processed += 1

        # Periodic stats
        if self.processed % 100 == 0:
            elapsed = time.time() - self.start_time
            logger.info(
                "DLQ stats: processed=%d elapsed=%.1fs rate=%.2f msg/s",
                self.processed,
                elapsed,
                self.processed / max(elapsed, 1),
            )

        # Detach tracing context after processing
        if tracing_ctx is not None:
            tracing_ctx.detach()

    # ------------------------------------------------------------------
    def _shutdown(self) -> None:
        elapsed = time.time() - self.start_time
        logger.info(
            "DLQ consumer shutting down -- processed %d messages in %.1fs",
            self.processed,
            elapsed,
        )
        if self.consumer:
            self.consumer.close()


# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Stream-Sentinel DLQ Consumer")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help=f"Path to JSONL output file (default: {DEFAULT_OUTPUT_PATH})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Start combined Prometheus metrics + health check server on port 8004
    health_server = None
    try:
        from monitoring.health import HealthCheckServer, make_kafka_check
        from monitoring.metrics import get_metrics as get_prometheus_metrics

        metrics = get_prometheus_metrics(component_name="dlq-consumer")
        health_server = HealthCheckServer(registry=metrics.registry)
        metrics.set_health_server(health_server)
        metrics.start_metrics_server(port=8004)
        logger.info("Combined metrics + health server started on port 8004")
    except Exception as e:
        logger.warning(
            "Failed to start metrics server: %s -- continuing without metrics endpoint",
            e,
        )

    consumer = DLQConsumer(output_path=args.output)

    # Register health checks now that the consumer is fully initialised
    if health_server is not None:
        consumer._health_server = health_server
        health_server.register_check("kafka", make_kafka_check(consumer.consumer))

        def _metrics_summary():
            uptime = time.time() - consumer.start_time
            mps = consumer.processed / max(uptime, 1)
            return {
                "messages_processed": consumer.processed,
                "throughput_mps": round(mps, 2),
            }

        health_server.set_metrics_summary_fn(_metrics_summary)

    consumer.run()


if __name__ == "__main__":
    main()
