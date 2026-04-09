#!/usr/bin/env python3

"""
Consumer Lag Monitor for Stream-Sentinel

Periodically checks Kafka consumer group lag via the admin client and
emits Prometheus-style gauges.  Logs a warning when lag exceeds a
configurable threshold so operators can react before backlogs grow.

Usage:
    # As a standalone monitor
    python src/kafka/lag_monitor.py --group fraud-detection-group --threshold 10000

    # Programmatically
    from kafka.lag_monitor import LagMonitor
    monitor = LagMonitor("fraud-detection-group", lag_threshold=5000)
    monitor.start()   # background thread
    ...
    monitor.stop()
"""

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient

sys.path.append(str(Path(__file__).parent.parent))
from kafka.config import get_kafka_config

logger = logging.getLogger("stream_sentinel.lag_monitor")


class PartitionLag:
    """Lag info for a single partition."""

    __slots__ = ("topic", "partition", "committed_offset", "high_watermark", "lag")

    def __init__(
        self,
        topic: str,
        partition: int,
        committed_offset: int,
        high_watermark: int,
    ):
        self.topic = topic
        self.partition = partition
        self.committed_offset = committed_offset
        self.high_watermark = high_watermark
        self.lag = max(0, high_watermark - committed_offset)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic": self.topic,
            "partition": self.partition,
            "committed_offset": self.committed_offset,
            "high_watermark": self.high_watermark,
            "lag": self.lag,
        }


class LagMonitor:
    """Monitors consumer group lag and exposes Prometheus-compatible gauges.

    Attributes:
        group_id: The Kafka consumer group to monitor.
        lag_threshold: Emit a warning log when any partition exceeds this lag.
        poll_interval_seconds: How often to check lag (default 30s).
        partition_lags: Latest lag snapshot, keyed by ``(topic, partition)``.
        total_lag: Sum of lag across all partitions (Prometheus gauge).
    """

    def __init__(
        self,
        group_id: str,
        topics: Optional[List[str]] = None,
        lag_threshold: int = 10_000,
        poll_interval_seconds: float = 30.0,
        environment: Optional[str] = None,
    ):
        self.group_id = group_id
        self.topics = topics or ["synthetic-transactions"]
        self.lag_threshold = lag_threshold
        self.poll_interval_seconds = poll_interval_seconds

        self._kafka_config = get_kafka_config(environment)
        self._admin: Optional[AdminClient] = None
        self._consumer: Optional[Consumer] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Latest state -- readable from the main thread
        self.partition_lags: Dict[tuple, PartitionLag] = {}
        self.total_lag: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background monitoring thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="lag-monitor")
        self._thread.start()
        logger.info(
            "Lag monitor started for group '%s' (threshold=%d, interval=%.1fs)",
            self.group_id,
            self.lag_threshold,
            self.poll_interval_seconds,
        )

    def stop(self) -> None:
        """Stop the monitoring thread and release resources."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._close_clients()
        logger.info("Lag monitor stopped for group '%s'", self.group_id)

    def check_lag_once(self) -> Dict[tuple, PartitionLag]:
        """Run a single lag check (useful for ad-hoc queries)."""
        self._ensure_clients()
        return self._fetch_lag()

    def get_prometheus_metrics(self) -> str:
        """Return lag metrics in Prometheus text exposition format."""
        lines = [
            "# HELP kafka_consumer_lag Consumer group lag per partition",
            "# TYPE kafka_consumer_lag gauge",
        ]
        for (topic, part), pl in self.partition_lags.items():
            lines.append(
                f'kafka_consumer_lag{{group="{self.group_id}",' f'topic="{topic}",partition="{part}"}} {pl.lag}'
            )
        lines.extend(
            [
                "# HELP kafka_consumer_lag_total Total consumer group lag",
                "# TYPE kafka_consumer_lag_total gauge",
                f'kafka_consumer_lag_total{{group="{self.group_id}"}} {self.total_lag}',
            ]
        )
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_clients(self) -> None:
        if self._admin is None:
            admin_conf = {
                "bootstrap.servers": self._kafka_config.bootstrap_servers,
            }
            self._admin = AdminClient(admin_conf)

        if self._consumer is None:
            # We need a Consumer to call committed() and get_watermark_offsets().
            # This consumer does NOT subscribe or consume; it only queries metadata.
            consumer_conf = {
                "bootstrap.servers": self._kafka_config.bootstrap_servers,
                "group.id": self.group_id,
                "enable.auto.commit": False,
            }
            self._consumer = Consumer(consumer_conf)

    def _close_clients(self) -> None:
        if self._consumer is not None:
            try:
                self._consumer.close()
            except Exception:
                pass
            self._consumer = None
        self._admin = None

    def _fetch_lag(self) -> Dict[tuple, PartitionLag]:
        """Query committed offsets and high watermarks for all partitions."""
        if self._consumer is None:
            self._ensure_clients()

        results: Dict[tuple, PartitionLag] = {}
        total = 0

        for topic in self.topics:
            # Get partition metadata
            try:
                meta = self._consumer.list_topics(topic, timeout=10)
                topic_meta = meta.topics.get(topic)
                if topic_meta is None or topic_meta.error is not None:
                    logger.warning("Cannot get metadata for topic '%s'", topic)
                    continue

                partitions = [TopicPartition(topic, pid) for pid in topic_meta.partitions.keys()]
            except Exception as e:
                logger.error("Error listing topic '%s': %s", topic, e)
                continue

            # Fetch committed offsets
            try:
                committed = self._consumer.committed(partitions, timeout=10)
            except Exception as e:
                logger.error("Error fetching committed offsets for '%s': %s", topic, e)
                continue

            for tp in committed:
                try:
                    low, high = self._consumer.get_watermark_offsets(tp, timeout=10)
                except Exception as e:
                    logger.warning(
                        "Cannot get watermark for %s-%d: %s",
                        tp.topic,
                        tp.partition,
                        e,
                    )
                    continue

                # committed offset of -1001 means no committed offset yet
                committed_offset = tp.offset if tp.offset >= 0 else 0

                pl = PartitionLag(
                    topic=tp.topic,
                    partition=tp.partition,
                    committed_offset=committed_offset,
                    high_watermark=high,
                )
                results[(tp.topic, tp.partition)] = pl
                total += pl.lag

                if pl.lag > self.lag_threshold:
                    logger.warning(
                        "High lag on %s-%d: %d messages (threshold: %d)",
                        tp.topic,
                        tp.partition,
                        pl.lag,
                        self.lag_threshold,
                    )

        self.partition_lags = results
        self.total_lag = total
        return results

    def _monitor_loop(self) -> None:
        """Background loop that periodically checks lag."""
        self._ensure_clients()

        while self._running:
            try:
                lags = self._fetch_lag()
                logger.debug(
                    "Group '%s' total lag: %d across %d partitions",
                    self.group_id,
                    self.total_lag,
                    len(lags),
                )
            except Exception as e:
                logger.error("Lag check failed: %s", e)

            # Sleep in small increments so stop() is responsive
            slept = 0.0
            while slept < self.poll_interval_seconds and self._running:
                time.sleep(min(1.0, self.poll_interval_seconds - slept))
                slept += 1.0


# ---------------------------------------------------------------------------
# Flow control mixin for consumers
# ---------------------------------------------------------------------------


class FlowController:
    """Adaptive flow control that reduces batch size when processing is slow.

    Embed this in a consumer to automatically back off when per-message
    processing time approaches the poll interval.

    Usage::

        fc = FlowController(max_poll_interval_ms=300000)
        ...
        fc.record_processing_time(msg_processing_seconds)
        current_max = fc.effective_batch_size(base_batch_size)
    """

    def __init__(
        self,
        max_poll_interval_ms: int = 300_000,
        slow_message_threshold_ms: float = 500.0,
        window_size: int = 100,
    ):
        self.max_poll_interval_ms = max_poll_interval_ms
        self.slow_message_threshold_ms = slow_message_threshold_ms
        self._processing_times: List[float] = []
        self._window_size = window_size
        self._slow_count = 0
        self._logger = logging.getLogger("stream_sentinel.flow_control")

    def record_processing_time(self, seconds: float) -> None:
        """Record the wall-clock time to process one message."""
        ms = seconds * 1000.0
        self._processing_times.append(ms)
        if len(self._processing_times) > self._window_size:
            self._processing_times = self._processing_times[-self._window_size :]

        if ms > self.slow_message_threshold_ms:
            self._slow_count += 1
            if self._slow_count % 10 == 1:
                self._logger.warning(
                    "Slow message processing: %.1fms (threshold: %.1fms, " "slow count: %d)",
                    ms,
                    self.slow_message_threshold_ms,
                    self._slow_count,
                )

    def effective_batch_size(self, base_size: int) -> int:
        """Return a possibly-reduced batch size based on recent processing times.

        If recent average processing time * base_size would exceed 80% of the
        max poll interval, reduce the batch size proportionally.
        """
        if not self._processing_times:
            return base_size

        avg_ms = sum(self._processing_times) / len(self._processing_times)
        budget_ms = self.max_poll_interval_ms * 0.8
        safe_batch = max(1, int(budget_ms / max(avg_ms, 0.01)))
        return min(base_size, safe_batch)

    def get_stats(self) -> Dict[str, Any]:
        """Return flow control statistics."""
        if not self._processing_times:
            return {
                "avg_processing_ms": 0,
                "p99_processing_ms": 0,
                "slow_message_count": self._slow_count,
            }
        sorted_times = sorted(self._processing_times)
        return {
            "avg_processing_ms": sum(sorted_times) / len(sorted_times),
            "p99_processing_ms": sorted_times[int(len(sorted_times) * 0.99)],
            "slow_message_count": self._slow_count,
            "window_size": len(self._processing_times),
        }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="Monitor Kafka consumer group lag")
    parser.add_argument(
        "--group",
        "-g",
        default="fraud-detection-group",
        help="Consumer group ID to monitor",
    )
    parser.add_argument(
        "--topic",
        "-t",
        default="synthetic-transactions",
        help="Topic to monitor (default: synthetic-transactions)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=10_000,
        help="Lag warning threshold per partition (default: 10000)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="Polling interval in seconds (default: 30)",
    )
    args = parser.parse_args()

    monitor = LagMonitor(
        group_id=args.group,
        topics=[args.topic],
        lag_threshold=args.threshold,
        poll_interval_seconds=args.interval,
    )

    try:
        # Run in foreground for CLI usage
        monitor._ensure_clients()
        logger.info(
            "Monitoring consumer group '%s' (threshold=%d, interval=%.1fs)",
            args.group,
            args.threshold,
            args.interval,
        )
        while True:
            lags = monitor.check_lag_once()
            print(
                f"[{time.strftime('%H:%M:%S')}] "
                f"Group '{args.group}' -- "
                f"total lag: {monitor.total_lag}, "
                f"partitions: {len(lags)}"
            )
            for key in sorted(lags.keys()):
                pl = lags[key]
                flag = " ** HIGH" if pl.lag > args.threshold else ""
                print(
                    f"  {pl.topic}-{pl.partition}: "
                    f"committed={pl.committed_offset}, "
                    f"hwm={pl.high_watermark}, "
                    f"lag={pl.lag}{flag}"
                )
            print()
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        monitor._close_clients()


if __name__ == "__main__":
    main()
