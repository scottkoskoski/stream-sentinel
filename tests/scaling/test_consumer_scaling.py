"""
Consumer Scaling Validation Tests for Stream-Sentinel

Verifies multi-consumer coordination:
- Partition assignment across N consumers in the same group
- Rebalancing on join/leave with zero message loss
- Linear throughput scaling (>70% efficiency)
- Exactly-once processing (no duplicates, no drops)
- Graceful shutdown with committed offsets

All tests require a live Kafka cluster and are marked with both
``scaling`` and ``requires_infrastructure``.
"""

import json
import logging
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional, Set

import pytest
from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic

_project_root = Path(__file__).resolve().parent.parent.parent
_src_path = str(_project_root / "src")
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

from kafka.config import get_kafka_config

logger = logging.getLogger("stream_sentinel.test_consumer_scaling")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NUM_PARTITIONS = 12
SCALING_TOPIC_PREFIX = "test-scaling"
DEFAULT_POLL_TIMEOUT = 1.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_topic() -> str:
    """Generate a unique topic name for test isolation."""
    return f"{SCALING_TOPIC_PREFIX}-{uuid.uuid4().hex[:8]}"


def _unique_group() -> str:
    """Generate a unique consumer group for test isolation."""
    return f"scaling-test-{uuid.uuid4().hex[:8]}"


def _create_topic(
    admin: AdminClient,
    topic: str,
    num_partitions: int = NUM_PARTITIONS,
    timeout: float = 10.0,
) -> None:
    """Create a topic and wait for it to be ready."""
    new_topic = NewTopic(
        topic=topic,
        num_partitions=num_partitions,
        replication_factor=1,
        config={
            "cleanup.policy": "delete",
            "retention.ms": "300000",
            "compression.type": "lz4",
        },
    )
    futures = admin.create_topics([new_topic])
    for _, fut in futures.items():
        fut.result(timeout=timeout)
    # Give Kafka a moment to propagate the metadata
    time.sleep(2)


def _delete_topic(admin: AdminClient, topic: str) -> None:
    """Best-effort topic deletion."""
    try:
        futures = admin.delete_topics([topic])
        for _, fut in futures.items():
            fut.result(timeout=10)
    except Exception:
        pass


def _produce_messages(
    producer: Producer,
    topic: str,
    count: int,
    key_prefix: str = "msg",
) -> List[str]:
    """Produce *count* JSON messages and return their unique IDs."""
    msg_ids: List[str] = []
    for i in range(count):
        msg_id = f"{key_prefix}-{uuid.uuid4().hex[:12]}"
        payload = json.dumps(
            {
                "message_id": msg_id,
                "sequence": i,
                "timestamp": time.time(),
                "amount": 100.0 + i,
                "user_id": f"user_{i % 100:04d}",
                "transaction_id": msg_id,
            }
        )
        producer.produce(
            topic,
            key=str(i % NUM_PARTITIONS),
            value=payload.encode("utf-8"),
        )
        msg_ids.append(msg_id)
        # Periodic flush to avoid buffer overflow on large batches
        if (i + 1) % 5000 == 0:
            producer.flush(timeout=10)

    producer.flush(timeout=30)
    return msg_ids


class _ConsumerWorker:
    """Thread-safe wrapper around a Kafka Consumer for test scenarios.

    Each worker runs in its own thread, polls messages, and collects the
    unique ``message_id`` values it sees.  Workers share no mutable state
    except through explicit thread-safe collections.
    """

    def __init__(
        self,
        group_id: str,
        topic: str,
        bootstrap_servers: str,
        worker_id: int = 0,
        commit_every: int = 50,
    ):
        self.group_id = group_id
        self.topic = topic
        self.worker_id = worker_id
        self.commit_every = commit_every

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

        # Collected state -- safe to read after stop()
        self.message_ids: List[str] = []
        self.assigned_partitions: Set[int] = set()
        self.message_count = 0
        self._lock = threading.Lock()

        # Event fired once at least one partition has been assigned
        self.assignment_ready = threading.Event()
        # Records every partition-assignment callback
        self.assignment_history: List[Set[int]] = []

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        def _on_assign(consumer, partitions):
            pids = {tp.partition for tp in partitions}
            with self._lock:
                self.assigned_partitions = pids
                self.assignment_history.append(pids)
            self.assignment_ready.set()
            logger.info("Worker %d assigned partitions: %s", self.worker_id, sorted(pids))

        def _on_revoke(consumer, partitions):
            pids = {tp.partition for tp in partitions}
            logger.info("Worker %d revoked partitions: %s", self.worker_id, sorted(pids))

        self._consumer.subscribe(
            [self.topic],
            on_assign=_on_assign,
            on_revoke=_on_revoke,
        )

        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name=f"consumer-worker-{self.worker_id}",
        )
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        try:
            self._consumer.close()
        except Exception:
            pass

    def _poll_loop(self) -> None:
        uncommitted = 0
        while self._running:
            msg = self._consumer.poll(timeout=DEFAULT_POLL_TIMEOUT)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.warning("Worker %d poll error: %s", self.worker_id, msg.error())
                continue

            try:
                data = json.loads(msg.value().decode("utf-8"))
                msg_id = data.get("message_id", "")
            except Exception:
                msg_id = ""

            with self._lock:
                self.message_ids.append(msg_id)
                self.message_count += 1

            uncommitted += 1
            if uncommitted >= self.commit_every:
                self._consumer.commit(asynchronous=False)
                uncommitted = 0

        # Final commit before shutdown
        try:
            self._consumer.commit(asynchronous=False)
        except Exception:
            pass

    def get_partition_set(self) -> Set[int]:
        with self._lock:
            return set(self.assigned_partitions)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kafka_cfg():
    return get_kafka_config()


@pytest.fixture(scope="module")
def admin_client(kafka_cfg):
    admin = AdminClient({"bootstrap.servers": kafka_cfg.bootstrap_servers})
    return admin


@pytest.fixture(scope="module")
def bootstrap_servers(kafka_cfg):
    return kafka_cfg.bootstrap_servers


@pytest.fixture()
def producer(kafka_cfg):
    p = Producer(kafka_cfg.get_producer_config("transaction"))
    yield p
    p.flush(timeout=10)


@pytest.fixture()
def scaling_topic(admin_client):
    """Create a fresh test topic and clean it up after the test."""
    topic = _unique_topic()
    _create_topic(admin_client, topic)
    yield topic
    _delete_topic(admin_client, topic)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.scaling
@pytest.mark.requires_infrastructure
class TestPartitionAssignment:
    """Verify partition assignment across varying consumer counts."""

    @pytest.mark.parametrize(
        "num_consumers,expected_per_consumer",
        [
            (1, 12),
            (3, 4),
            (6, 2),
            (12, 1),
        ],
        ids=["1c-12p", "3c-4p", "6c-2p", "12c-1p"],
    )
    def test_partition_distribution(
        self,
        num_consumers: int,
        expected_per_consumer: int,
        scaling_topic: str,
        bootstrap_servers: str,
    ):
        """N consumers in the same group should share 12 partitions evenly."""
        group = _unique_group()
        workers: List[_ConsumerWorker] = []

        try:
            for i in range(num_consumers):
                w = _ConsumerWorker(
                    group_id=group,
                    topic=scaling_topic,
                    bootstrap_servers=bootstrap_servers,
                    worker_id=i,
                )
                workers.append(w)
                w.start()

            # Wait for all workers to get their assignment
            for w in workers:
                assert w.assignment_ready.wait(
                    timeout=30
                ), f"Worker {w.worker_id} never received a partition assignment"

            # Allow some extra time for rebalance to fully settle
            time.sleep(3)

            # Collect all assigned partitions
            all_assigned: Set[int] = set()
            for w in workers:
                pset = w.get_partition_set()
                # Each consumer should get roughly expected_per_consumer partitions
                # Allow +/-1 for edge cases in Kafka's range assignor
                assert abs(len(pset) - expected_per_consumer) <= 1, (
                    f"Worker {w.worker_id} has {len(pset)} partitions, " f"expected ~{expected_per_consumer}"
                )
                # No overlap
                overlap = all_assigned & pset
                assert not overlap, f"Partition overlap detected: {overlap}"
                all_assigned |= pset

            # All partitions should be covered
            assert all_assigned == set(
                range(NUM_PARTITIONS)
            ), f"Not all partitions covered: missing {set(range(NUM_PARTITIONS)) - all_assigned}"

        finally:
            for w in workers:
                w.stop()


@pytest.mark.scaling
@pytest.mark.requires_infrastructure
class TestRebalancing:
    """Verify partition rebalancing when consumers join and leave."""

    def test_rebalance_on_join_and_leave(
        self,
        scaling_topic: str,
        bootstrap_servers: str,
        producer: Producer,
    ):
        """Start 1 consumer, add a 2nd, remove the 2nd, verify all transitions."""
        group = _unique_group()
        num_messages = 200

        # Produce messages so there is something to consume
        produced_ids = _produce_messages(producer, scaling_topic, num_messages)

        # Phase 1: single consumer gets all 12 partitions
        worker1 = _ConsumerWorker(
            group_id=group,
            topic=scaling_topic,
            bootstrap_servers=bootstrap_servers,
            worker_id=1,
        )
        worker1.start()
        assert worker1.assignment_ready.wait(timeout=30)
        time.sleep(3)

        assert len(worker1.get_partition_set()) == NUM_PARTITIONS, "Single consumer should own all 12 partitions"

        # Phase 2: add a second consumer -> rebalance to 6+6
        worker2 = _ConsumerWorker(
            group_id=group,
            topic=scaling_topic,
            bootstrap_servers=bootstrap_servers,
            worker_id=2,
        )
        worker2.start()
        assert worker2.assignment_ready.wait(timeout=30)
        time.sleep(5)  # Wait for rebalance to settle

        p1 = worker1.get_partition_set()
        p2 = worker2.get_partition_set()
        assert len(p1) + len(p2) == NUM_PARTITIONS
        assert not (p1 & p2), "Partition overlap after 2-consumer rebalance"
        # Each should have ~6 (+/-1)
        assert abs(len(p1) - 6) <= 1
        assert abs(len(p2) - 6) <= 1

        # Phase 3: remove worker2 -> worker1 reacquires all partitions
        worker2.stop()
        time.sleep(15)  # Wait for session timeout + rebalance

        assert worker1.get_partition_set() == set(
            range(NUM_PARTITIONS)
        ), "Consumer 1 should reacquire all partitions after consumer 2 leaves"

        # Let consumer 1 drain remaining messages
        time.sleep(5)
        worker1.stop()

        # Verify no messages were lost across all phases
        all_ids = set(worker1.message_ids) | set(worker2.message_ids)
        missing = set(produced_ids) - all_ids
        assert len(missing) == 0, f"{len(missing)} messages lost during rebalancing"


@pytest.mark.scaling
@pytest.mark.requires_infrastructure
class TestThroughputScaling:
    """Verify that adding consumers provides approximately linear scaling."""

    @staticmethod
    def _measure_throughput(
        topic: str,
        group_id: str,
        bootstrap_servers: str,
        num_consumers: int,
        duration_seconds: float = 10.0,
    ) -> float:
        """Produce messages continuously and measure aggregate consumption rate."""
        # Start a background producer that feeds the topic for the duration
        prod_cfg = get_kafka_config().get_producer_config("transaction")
        prod = Producer(prod_cfg)
        stop_producing = threading.Event()

        def _producer_thread():
            seq = 0
            while not stop_producing.is_set():
                payload = json.dumps(
                    {
                        "message_id": f"tp-{uuid.uuid4().hex[:8]}",
                        "sequence": seq,
                        "timestamp": time.time(),
                        "amount": 42.0,
                        "user_id": "user_perf",
                        "transaction_id": f"tp-{seq}",
                    }
                ).encode("utf-8")
                try:
                    prod.produce(
                        topic,
                        key=str(seq % NUM_PARTITIONS),
                        value=payload,
                    )
                except BufferError:
                    prod.poll(0.1)
                    continue
                seq += 1
                if seq % 1000 == 0:
                    prod.poll(0)
            prod.flush(timeout=10)

        pt = threading.Thread(target=_producer_thread, daemon=True)
        pt.start()

        # Start consumers
        workers: List[_ConsumerWorker] = []
        for i in range(num_consumers):
            w = _ConsumerWorker(
                group_id=group_id,
                topic=topic,
                bootstrap_servers=bootstrap_servers,
                worker_id=i,
                commit_every=200,
            )
            workers.append(w)
            w.start()

        # Wait for assignments
        for w in workers:
            w.assignment_ready.wait(timeout=30)
        time.sleep(2)  # Stabilize

        # Measure
        start_counts = [w.message_count for w in workers]
        time.sleep(duration_seconds)
        end_counts = [w.message_count for w in workers]

        total_consumed = sum(e - s for s, e in zip(start_counts, end_counts))
        throughput = total_consumed / duration_seconds

        # Cleanup
        stop_producing.set()
        pt.join(timeout=10)
        for w in workers:
            w.stop()

        return throughput

    def test_linear_scaling(
        self,
        scaling_topic: str,
        bootstrap_servers: str,
    ):
        """Adding consumers should yield >70% of ideal linear scaling."""
        duration = 8.0

        # Baseline: 1 consumer
        tp_1 = self._measure_throughput(
            scaling_topic,
            _unique_group(),
            bootstrap_servers,
            num_consumers=1,
            duration_seconds=duration,
        )
        logger.info("Throughput with 1 consumer: %.1f msg/s", tp_1)

        # 2 consumers
        tp_2 = self._measure_throughput(
            scaling_topic,
            _unique_group(),
            bootstrap_servers,
            num_consumers=2,
            duration_seconds=duration,
        )
        logger.info("Throughput with 2 consumers: %.1f msg/s", tp_2)

        # 4 consumers
        tp_4 = self._measure_throughput(
            scaling_topic,
            _unique_group(),
            bootstrap_servers,
            num_consumers=4,
            duration_seconds=duration,
        )
        logger.info("Throughput with 4 consumers: %.1f msg/s", tp_4)

        # Verify scaling efficiency > 70%
        # Efficiency = actual_speedup / ideal_speedup
        if tp_1 > 0:
            efficiency_2 = (tp_2 / tp_1) / 2.0
            efficiency_4 = (tp_4 / tp_1) / 4.0

            logger.info(
                "Scaling efficiency: 2x=%.1f%%, 4x=%.1f%%",
                efficiency_2 * 100,
                efficiency_4 * 100,
            )

            # 4 consumers should deliver at least 2.8x the throughput of 1
            assert tp_4 >= tp_1 * 2.8, (
                f"4-consumer throughput ({tp_4:.0f}) is less than 2.8x "
                f"single-consumer ({tp_1:.0f}). Scaling efficiency too low."
            )


@pytest.mark.scaling
@pytest.mark.requires_infrastructure
class TestNoDuplicateProcessing:
    """Ensure exactly-once semantics within a consumer group."""

    def test_no_duplicates_no_drops(
        self,
        scaling_topic: str,
        bootstrap_servers: str,
        producer: Producer,
    ):
        """Produce N messages, consume with 3 workers, verify each ID once."""
        group = _unique_group()
        num_messages = 3000

        produced_ids = _produce_messages(producer, scaling_topic, num_messages)
        produced_set = set(produced_ids)

        workers: List[_ConsumerWorker] = []
        try:
            for i in range(3):
                w = _ConsumerWorker(
                    group_id=group,
                    topic=scaling_topic,
                    bootstrap_servers=bootstrap_servers,
                    worker_id=i,
                    commit_every=100,
                )
                workers.append(w)
                w.start()

            # Wait for assignments
            for w in workers:
                assert w.assignment_ready.wait(timeout=30)

            # Allow time to consume all messages
            deadline = time.time() + 60
            while time.time() < deadline:
                total = sum(w.message_count for w in workers)
                if total >= num_messages:
                    break
                time.sleep(1)

            # Collect results
            all_ids: List[str] = []
            for w in workers:
                all_ids.extend(w.message_ids)

        finally:
            for w in workers:
                w.stop()

        consumed_set = set(all_ids)

        # Check for drops
        missing = produced_set - consumed_set
        assert len(missing) == 0, f"{len(missing)} messages were dropped (not consumed)"

        # Check for duplicates
        duplicates = len(all_ids) - len(consumed_set)
        assert duplicates == 0, f"{duplicates} duplicate messages detected across 3 consumers"


@pytest.mark.scaling
@pytest.mark.requires_infrastructure
class TestGracefulShutdown:
    """Verify consumers commit offsets before exiting on shutdown."""

    def test_offset_committed_on_stop(
        self,
        scaling_topic: str,
        bootstrap_servers: str,
        producer: Producer,
    ):
        """After a worker stops, committed offsets should reflect processed msgs."""
        group = _unique_group()
        num_messages = 500

        _produce_messages(producer, scaling_topic, num_messages)

        worker = _ConsumerWorker(
            group_id=group,
            topic=scaling_topic,
            bootstrap_servers=bootstrap_servers,
            worker_id=0,
            commit_every=50,
        )
        worker.start()
        assert worker.assignment_ready.wait(timeout=30)

        # Consume for a few seconds
        time.sleep(5)
        consumed_before_stop = worker.message_count

        # Graceful stop -- triggers final commit in _poll_loop
        worker.stop()
        assert consumed_before_stop > 0, "Worker consumed no messages"

        # Verify committed offsets via a new consumer
        verify_consumer = Consumer(
            {
                "bootstrap.servers": bootstrap_servers,
                "group.id": group,
                "enable.auto.commit": False,
            }
        )
        try:
            partitions = [TopicPartition(scaling_topic, p) for p in range(NUM_PARTITIONS)]
            committed = verify_consumer.committed(partitions, timeout=10)
            total_committed = sum(tp.offset for tp in committed if tp.offset > 0)
        finally:
            verify_consumer.close()

        assert total_committed > 0, "No offsets were committed after graceful shutdown"
        # Committed offsets should be close to consumed count
        # (they can be slightly less because of commit_every batching)
        assert total_committed >= consumed_before_stop - 50, (
            f"Committed offsets ({total_committed}) are significantly behind "
            f"consumed count ({consumed_before_stop})"
        )

    def test_no_message_loss_after_restart(
        self,
        scaling_topic: str,
        bootstrap_servers: str,
        producer: Producer,
    ):
        """Stop and restart a consumer -- second run should pick up where 1st left off."""
        group = _unique_group()
        total_messages = 1000

        produced_ids = _produce_messages(producer, scaling_topic, total_messages)

        # First run: consume a subset
        worker1 = _ConsumerWorker(
            group_id=group,
            topic=scaling_topic,
            bootstrap_servers=bootstrap_servers,
            worker_id=0,
            commit_every=50,
        )
        worker1.start()
        assert worker1.assignment_ready.wait(timeout=30)
        time.sleep(5)
        worker1.stop()

        first_run_ids = set(worker1.message_ids)
        logger.info("First run consumed %d messages", len(first_run_ids))

        # Second run: consume the rest
        worker2 = _ConsumerWorker(
            group_id=group,
            topic=scaling_topic,
            bootstrap_servers=bootstrap_servers,
            worker_id=1,
            commit_every=50,
        )
        worker2.start()
        assert worker2.assignment_ready.wait(timeout=30)

        # Wait for the remaining messages
        deadline = time.time() + 30
        while time.time() < deadline:
            combined = len(first_run_ids) + worker2.message_count
            if combined >= total_messages:
                break
            time.sleep(1)

        worker2.stop()
        second_run_ids = set(worker2.message_ids)

        all_consumed = first_run_ids | second_run_ids
        missing = set(produced_ids) - all_consumed
        assert len(missing) == 0, f"{len(missing)} messages lost between stop and restart"
