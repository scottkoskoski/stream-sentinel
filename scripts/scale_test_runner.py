#!/usr/bin/env python3

"""
Interactive Consumer Scaling Test Runner for Stream-Sentinel

Starts N consumer instances as threads, produces a configurable number of
test messages, monitors partition assignment and lag, then outputs a
summary report covering throughput, duplicate/gap analysis, and partition
distribution.

Usage:
    python scripts/scale_test_runner.py --consumers 4 --messages 10000
    python scripts/scale_test_runner.py --consumers 12 --messages 50000 --json
"""

import argparse
import json
import logging
import signal
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Resolve project root so imports work regardless of cwd
_project_root = Path(__file__).resolve().parent.parent
_src_path = str(_project_root / "src")
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic

from kafka.config import get_kafka_config
from kafka.consumer_group_monitor import ConsumerGroupMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("scale_test_runner")

# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

NUM_PARTITIONS = 12


class _ConsumerWorker:
    """Lightweight consumer that collects message IDs and throughput stats."""

    def __init__(
        self,
        group_id: str,
        topic: str,
        bootstrap_servers: str,
        worker_id: int,
    ):
        self.group_id = group_id
        self.topic = topic
        self.worker_id = worker_id

        self._consumer = Consumer(
            {
                "bootstrap.servers": bootstrap_servers,
                "group.id": group_id,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
                "session.timeout.ms": 10000,
                "max.poll.interval.ms": 30000,
            }
        )

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.message_ids: List[str] = []
        self.assigned_partitions: Set[int] = set()
        self.message_count = 0
        self._lock = threading.Lock()
        self.assignment_ready = threading.Event()

        # Per-second throughput samples
        self._throughput_samples: List[float] = []
        self._sample_start: float = 0.0
        self._sample_count: int = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        def _on_assign(consumer, partitions):
            pids = {tp.partition for tp in partitions}
            with self._lock:
                self.assigned_partitions = pids
            self.assignment_ready.set()
            logger.info("Worker %d -> partitions %s", self.worker_id, sorted(pids))

        self._consumer.subscribe([self.topic], on_assign=_on_assign)
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name=f"worker-{self.worker_id}")
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        try:
            self._consumer.commit(asynchronous=False)
        except Exception:
            pass
        try:
            self._consumer.close()
        except Exception:
            pass

    def _poll_loop(self) -> None:
        uncommitted = 0
        self._sample_start = time.time()
        self._sample_count = 0

        while self._running:
            msg = self._consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                continue

            try:
                data = json.loads(msg.value().decode("utf-8"))
                msg_id = data.get("message_id", "")
            except Exception:
                msg_id = ""

            with self._lock:
                self.message_ids.append(msg_id)
                self.message_count += 1

            self._sample_count += 1
            elapsed = time.time() - self._sample_start
            if elapsed >= 1.0:
                self._throughput_samples.append(self._sample_count / elapsed)
                self._sample_count = 0
                self._sample_start = time.time()

            uncommitted += 1
            if uncommitted >= 200:
                try:
                    self._consumer.commit(asynchronous=False)
                except Exception:
                    pass
                uncommitted = 0

    @property
    def avg_throughput(self) -> float:
        if not self._throughput_samples:
            return 0.0
        return sum(self._throughput_samples) / len(self._throughput_samples)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_scale_test(
    num_consumers: int,
    num_messages: int,
    json_output: bool = False,
) -> Dict[str, Any]:
    """Execute the full scaling test and return a result dict."""
    kafka_cfg = get_kafka_config()
    bootstrap_servers = kafka_cfg.bootstrap_servers
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})

    # Create isolated test topic
    topic = f"scale-test-{uuid.uuid4().hex[:8]}"
    logger.info("Creating topic '%s' with %d partitions", topic, NUM_PARTITIONS)
    new_topic = NewTopic(
        topic=topic,
        num_partitions=NUM_PARTITIONS,
        replication_factor=1,
        config={
            "cleanup.policy": "delete",
            "retention.ms": "600000",
            "compression.type": "lz4",
        },
    )
    futures = admin.create_topics([new_topic])
    for _, fut in futures.items():
        fut.result(timeout=10)
    time.sleep(2)

    group = f"scale-runner-{uuid.uuid4().hex[:8]}"
    monitor = ConsumerGroupMonitor(group_id=group, topics=[topic])

    try:
        # ----- Produce messages -----
        logger.info("Producing %d messages...", num_messages)
        prod = Producer(kafka_cfg.get_producer_config("transaction"))
        produced_ids: List[str] = []
        produce_start = time.time()

        for i in range(num_messages):
            msg_id = f"st-{uuid.uuid4().hex[:12]}"
            payload = json.dumps(
                {
                    "message_id": msg_id,
                    "sequence": i,
                    "timestamp": time.time(),
                    "amount": 100.0 + i,
                    "user_id": f"user_{i % 100:04d}",
                    "transaction_id": msg_id,
                }
            ).encode("utf-8")
            try:
                prod.produce(topic, key=str(i % NUM_PARTITIONS), value=payload)
            except BufferError:
                prod.poll(0.5)
                prod.produce(topic, key=str(i % NUM_PARTITIONS), value=payload)
            produced_ids.append(msg_id)
            if (i + 1) % 5000 == 0:
                prod.flush(timeout=10)

        prod.flush(timeout=30)
        produce_elapsed = time.time() - produce_start
        produce_rate = num_messages / produce_elapsed if produce_elapsed > 0 else 0
        logger.info(
            "Produced %d messages in %.1fs (%.0f msg/s)",
            num_messages,
            produce_elapsed,
            produce_rate,
        )

        # ----- Start consumers -----
        logger.info("Starting %d consumers (group=%s)...", num_consumers, group)
        workers: List[_ConsumerWorker] = []
        for i in range(num_consumers):
            w = _ConsumerWorker(
                group_id=group,
                topic=topic,
                bootstrap_servers=bootstrap_servers,
                worker_id=i,
            )
            workers.append(w)
            w.start()

        # Wait for partition assignment
        for w in workers:
            if not w.assignment_ready.wait(timeout=30):
                logger.warning("Worker %d never received assignment", w.worker_id)

        # Take a group snapshot
        time.sleep(3)
        snapshot = monitor.get_group_snapshot()
        logger.info(
            "Group snapshot: %d members, %d partitions, balanced=%s",
            snapshot.member_count,
            snapshot.total_partitions,
            snapshot.is_balanced,
        )

        # ----- Wait for consumption -----
        consume_start = time.time()
        deadline = time.time() + 120  # 2 minute max

        while time.time() < deadline:
            total = sum(w.message_count for w in workers)
            elapsed = time.time() - consume_start
            rate = total / elapsed if elapsed > 0 else 0
            if not json_output:
                print(
                    f"\r  Consumed {total}/{num_messages} " f"({rate:.0f} msg/s, {elapsed:.1f}s elapsed)",
                    end="",
                    flush=True,
                )
            if total >= num_messages:
                break
            time.sleep(1)

        consume_elapsed = time.time() - consume_start
        if not json_output:
            print()

        # ----- Stop consumers -----
        for w in workers:
            w.stop()

        # ----- Analyze results -----
        all_ids: List[str] = []
        per_consumer_stats: List[Dict[str, Any]] = []
        for w in workers:
            all_ids.extend(w.message_ids)
            per_consumer_stats.append(
                {
                    "worker_id": w.worker_id,
                    "messages": w.message_count,
                    "partitions": sorted(w.assigned_partitions),
                    "avg_throughput_msg_s": round(w.avg_throughput, 1),
                }
            )

        produced_set = set(produced_ids)
        consumed_set = set(all_ids)
        missing = produced_set - consumed_set
        extra = consumed_set - produced_set
        duplicates = len(all_ids) - len(consumed_set)

        aggregate_rate = sum(w.message_count for w in workers) / consume_elapsed if consume_elapsed > 0 else 0

        result = {
            "config": {
                "consumers": num_consumers,
                "messages_produced": num_messages,
                "partitions": NUM_PARTITIONS,
                "topic": topic,
                "group": group,
            },
            "production": {
                "elapsed_seconds": round(produce_elapsed, 2),
                "rate_msg_s": round(produce_rate, 1),
            },
            "consumption": {
                "total_consumed": len(all_ids),
                "elapsed_seconds": round(consume_elapsed, 2),
                "aggregate_rate_msg_s": round(aggregate_rate, 1),
            },
            "integrity": {
                "messages_missing": len(missing),
                "messages_extra": len(extra),
                "duplicates": duplicates,
                "all_delivered": len(missing) == 0,
                "no_duplicates": duplicates == 0,
            },
            "partition_assignment": {
                "balanced": snapshot.is_balanced,
                "member_count": snapshot.member_count,
            },
            "per_consumer": per_consumer_stats,
        }

        return result

    finally:
        monitor.close()
        # Clean up test topic
        try:
            del_futures = admin.delete_topics([topic])
            for _, fut in del_futures.items():
                fut.result(timeout=10)
        except Exception:
            pass


def _print_report(result: Dict[str, Any]) -> None:
    """Print a human-readable summary report."""
    cfg = result["config"]
    prod = result["production"]
    cons = result["consumption"]
    integrity = result["integrity"]
    pa = result["partition_assignment"]

    print("\n" + "=" * 64)
    print("  Stream-Sentinel Consumer Scaling Test Report")
    print("=" * 64)

    print(f"\n  Configuration")
    print(f"    Consumers:  {cfg['consumers']}")
    print(f"    Messages:   {cfg['messages_produced']:,}")
    print(f"    Partitions: {cfg['partitions']}")
    print(f"    Topic:      {cfg['topic']}")
    print(f"    Group:      {cfg['group']}")

    print(f"\n  Production")
    print(f"    Elapsed:    {prod['elapsed_seconds']}s")
    print(f"    Rate:       {prod['rate_msg_s']:,.0f} msg/s")

    print(f"\n  Consumption")
    print(f"    Total:      {cons['total_consumed']:,}")
    print(f"    Elapsed:    {cons['elapsed_seconds']}s")
    print(f"    Aggregate:  {cons['aggregate_rate_msg_s']:,.0f} msg/s")

    print(f"\n  Integrity")
    status_all = "PASS" if integrity["all_delivered"] else "FAIL"
    status_dup = "PASS" if integrity["no_duplicates"] else "FAIL"
    print(f"    Missing:    {integrity['messages_missing']} [{status_all}]")
    print(f"    Duplicates: {integrity['duplicates']} [{status_dup}]")

    print(f"\n  Partition Assignment")
    print(f"    Members:    {pa['member_count']}")
    balanced_str = "YES" if pa["balanced"] else "NO"
    print(f"    Balanced:   {balanced_str}")

    print(f"\n  Per-Consumer Breakdown")
    for cs in result["per_consumer"]:
        print(
            f"    Worker {cs['worker_id']:>2}: "
            f"{cs['messages']:>7,} msgs, "
            f"partitions={cs['partitions']}, "
            f"avg {cs['avg_throughput_msg_s']:,.0f} msg/s"
        )

    overall = "PASS" if (integrity["all_delivered"] and integrity["no_duplicates"]) else "FAIL"
    print(f"\n  Overall: {overall}")
    print("=" * 64 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream-Sentinel Consumer Scaling Test Runner")
    parser.add_argument(
        "--consumers",
        "-c",
        type=int,
        default=4,
        help="Number of consumer instances to run (default: 4)",
    )
    parser.add_argument(
        "--messages",
        "-m",
        type=int,
        default=10000,
        help="Number of test messages to produce (default: 10000)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    if args.consumers < 1:
        print("Error: --consumers must be >= 1")
        sys.exit(1)
    if args.consumers > NUM_PARTITIONS:
        print(f"Warning: {args.consumers} consumers > {NUM_PARTITIONS} partitions. " f"Some consumers will be idle.")
    if args.messages < 1:
        print("Error: --messages must be >= 1")
        sys.exit(1)

    result = run_scale_test(
        num_consumers=args.consumers,
        num_messages=args.messages,
        json_output=args.json_output,
    )

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        _print_report(result)

    # Exit code: 0 if integrity checks pass, 1 otherwise
    if result["integrity"]["all_delivered"] and result["integrity"]["no_duplicates"]:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
