#!/usr/bin/env python3

"""
Consumer Group Monitor for Stream-Sentinel

Queries the Kafka AdminClient for consumer group state and provides
detailed reporting on member count, partition assignments, and per-consumer
lag.  Useful both programmatically (in scaling tests) and as a standalone
CLI diagnostic tool.

Usage:
    # CLI mode
    python src/kafka/consumer_group_monitor.py --group fraud-detection-group

    # Programmatic
    from kafka.consumer_group_monitor import ConsumerGroupMonitor
    monitor = ConsumerGroupMonitor("fraud-detection-group")
    snapshot = monitor.get_group_snapshot()
    print(snapshot.member_count, snapshot.total_lag)
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient

sys.path.append(str(Path(__file__).parent.parent))
from kafka.config import get_kafka_config

logger = logging.getLogger("stream_sentinel.consumer_group_monitor")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MemberAssignment:
    """Partition assignments for a single consumer group member."""

    member_id: str
    client_id: str
    host: str
    partitions: List[Dict[str, Any]] = field(default_factory=list)
    partition_count: int = 0
    total_lag: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GroupSnapshot:
    """Point-in-time snapshot of a consumer group's state."""

    group_id: str
    state: str
    member_count: int
    total_partitions: int
    total_lag: int
    members: List[MemberAssignment] = field(default_factory=list)
    partition_distribution: Dict[int, int] = field(default_factory=dict)
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def is_balanced(self) -> bool:
        """Check whether partitions are evenly distributed across members.

        The Kafka range/round-robin assignors guarantee that the difference
        between the largest and smallest assignment is at most 1.
        """
        if not self.partition_distribution:
            return True
        counts = list(self.partition_distribution.values())
        return (max(counts) - min(counts)) <= 1

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_balanced"] = self.is_balanced
        return d

    def to_prometheus(self) -> str:
        """Render key metrics in Prometheus text exposition format."""
        lines = [
            "# HELP consumer_group_members Number of members in the consumer group",
            "# TYPE consumer_group_members gauge",
            f'consumer_group_members{{group="{self.group_id}"}} {self.member_count}',
            "# HELP consumer_group_total_lag Total consumer lag across all partitions",
            "# TYPE consumer_group_total_lag gauge",
            f'consumer_group_total_lag{{group="{self.group_id}"}} {self.total_lag}',
            "# HELP consumer_group_total_partitions Assigned partitions in consumer group",
            "# TYPE consumer_group_total_partitions gauge",
            f'consumer_group_total_partitions{{group="{self.group_id}"}} {self.total_partitions}',
        ]
        for member in self.members:
            lines.append(
                f'consumer_group_member_partitions{{group="{self.group_id}",'
                f'member="{member.member_id}"}} {member.partition_count}'
            )
            lines.append(
                f'consumer_group_member_lag{{group="{self.group_id}",'
                f'member="{member.member_id}"}} {member.total_lag}'
            )
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class ConsumerGroupMonitor:
    """Query Kafka for consumer group metadata, assignments, and lag.

    Parameters
    ----------
    group_id : str
        The Kafka consumer group to inspect.
    topics : list[str] | None
        Topics to query for lag.  Defaults to ``["synthetic-transactions"]``.
    environment : str | None
        Override the Kafka config environment.
    """

    def __init__(
        self,
        group_id: str,
        topics: Optional[List[str]] = None,
        environment: Optional[str] = None,
    ):
        self.group_id = group_id
        self.topics = topics or ["synthetic-transactions"]
        self._kafka_config = get_kafka_config(environment)
        self._admin: Optional[AdminClient] = None
        self._lag_consumer: Optional[Consumer] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_group_snapshot(self) -> GroupSnapshot:
        """Collect a full point-in-time snapshot of the consumer group."""
        self._ensure_clients()

        # Fetch group description via admin client
        group_desc = self._describe_group()
        if group_desc is None:
            return GroupSnapshot(
                group_id=self.group_id,
                state="UNKNOWN",
                member_count=0,
                total_partitions=0,
                total_lag=0,
            )

        state = group_desc.get("state", "UNKNOWN")
        raw_members = group_desc.get("members", [])

        members: List[MemberAssignment] = []
        total_partitions = 0
        total_lag = 0
        partition_dist: Dict[int, int] = {}

        for raw in raw_members:
            member = self._build_member_assignment(raw)
            members.append(member)
            total_partitions += member.partition_count
            total_lag += member.total_lag
            partition_dist[member.partition_count] = (
                partition_dist.get(member.partition_count, 0) + 1
            )

        return GroupSnapshot(
            group_id=self.group_id,
            state=state,
            member_count=len(members),
            total_partitions=total_partitions,
            total_lag=total_lag,
            members=members,
            partition_distribution=partition_dist,
        )

    def wait_for_members(
        self,
        expected_members: int,
        timeout_seconds: float = 30.0,
        poll_interval: float = 1.0,
    ) -> GroupSnapshot:
        """Block until the group has exactly *expected_members* members.

        Raises ``TimeoutError`` if the condition is not met within the timeout.
        """
        deadline = time.time() + timeout_seconds
        last_snapshot = None

        while time.time() < deadline:
            last_snapshot = self.get_group_snapshot()
            if last_snapshot.member_count == expected_members:
                return last_snapshot
            time.sleep(poll_interval)

        count = last_snapshot.member_count if last_snapshot else 0
        raise TimeoutError(
            f"Expected {expected_members} members in group "
            f"'{self.group_id}' but found {count} after "
            f"{timeout_seconds}s"
        )

    def wait_for_stable(
        self,
        timeout_seconds: float = 60.0,
        poll_interval: float = 1.0,
    ) -> GroupSnapshot:
        """Block until the group reaches the Stable state."""
        deadline = time.time() + timeout_seconds
        last_snapshot = None

        while time.time() < deadline:
            last_snapshot = self.get_group_snapshot()
            if last_snapshot.state == "Stable":
                return last_snapshot
            time.sleep(poll_interval)

        state = last_snapshot.state if last_snapshot else "UNKNOWN"
        raise TimeoutError(
            f"Group '{self.group_id}' not Stable after "
            f"{timeout_seconds}s (state={state})"
        )

    def get_partition_lag(self, topic: str, partition: int) -> int:
        """Return the lag for a single topic-partition."""
        self._ensure_clients()
        tp = TopicPartition(topic, partition)
        try:
            committed = self._lag_consumer.committed([tp], timeout=10)
            offset = committed[0].offset if committed[0].offset >= 0 else 0
            _, high = self._lag_consumer.get_watermark_offsets(tp, timeout=10)
            return max(0, high - offset)
        except Exception as exc:
            logger.warning("Could not get lag for %s-%d: %s", topic, partition, exc)
            return -1

    def close(self) -> None:
        """Release underlying Kafka clients."""
        if self._lag_consumer is not None:
            try:
                self._lag_consumer.close()
            except Exception:
                pass
            self._lag_consumer = None
        self._admin = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_clients(self) -> None:
        if self._admin is None:
            self._admin = AdminClient(
                {"bootstrap.servers": self._kafka_config.bootstrap_servers}
            )
        if self._lag_consumer is None:
            self._lag_consumer = Consumer(
                {
                    "bootstrap.servers": self._kafka_config.bootstrap_servers,
                    "group.id": self.group_id,
                    "enable.auto.commit": False,
                }
            )

    def _describe_group(self) -> Optional[Dict[str, Any]]:
        """Use the admin client to describe the consumer group."""
        assert self._admin is not None
        try:
            # list_groups returns a ListOffsetsResultInternal in older
            # confluent-kafka; fall back to the lower-level API.
            groups = self._admin.list_groups(timeout=10)
            target = None
            for g in groups:
                if g.id == self.group_id:
                    target = g
                    break

            if target is None:
                logger.debug("Group '%s' not found", self.group_id)
                return None

            return {
                "state": target.state,
                "members": [
                    {
                        "member_id": m.id,
                        "client_id": m.client_id,
                        "host": m.client_host,
                        "assignment": m.assignment,
                    }
                    for m in target.members
                ],
            }
        except Exception as exc:
            logger.error("Failed to describe group '%s': %s", self.group_id, exc)
            return None

    def _build_member_assignment(self, raw_member: Dict[str, Any]) -> MemberAssignment:
        """Parse a raw member dict into a ``MemberAssignment``."""
        member_id = raw_member.get("member_id", "")
        client_id = raw_member.get("client_id", "")
        host = raw_member.get("host", "")

        partitions: List[Dict[str, Any]] = []
        total_lag = 0

        assignment_bytes = raw_member.get("assignment")
        if assignment_bytes:
            parsed = self._parse_assignment(assignment_bytes)
            for topic, pid in parsed:
                lag = self.get_partition_lag(topic, pid)
                partitions.append({"topic": topic, "partition": pid, "lag": lag})
                if lag > 0:
                    total_lag += lag

        return MemberAssignment(
            member_id=member_id,
            client_id=client_id,
            host=host,
            partitions=partitions,
            partition_count=len(partitions),
            total_lag=total_lag,
        )

    @staticmethod
    def _parse_assignment(assignment_bytes: bytes) -> List[tuple]:
        """Decode the consumer group protocol assignment payload.

        The binary format is:
            version: int16
            num_topics: int32
            for each topic:
                topic_name: int16-length-prefixed string
                num_partitions: int32
                partitions: int32[]

        Returns a list of ``(topic, partition)`` tuples.
        """
        results: List[tuple] = []
        if not assignment_bytes or len(assignment_bytes) < 4:
            return results

        try:
            import struct

            offset = 0
            # version
            (version,) = struct.unpack_from(">h", assignment_bytes, offset)
            offset += 2
            # number of topics
            (num_topics,) = struct.unpack_from(">i", assignment_bytes, offset)
            offset += 4

            for _ in range(num_topics):
                # topic name length
                (name_len,) = struct.unpack_from(">h", assignment_bytes, offset)
                offset += 2
                topic = assignment_bytes[offset : offset + name_len].decode("utf-8")
                offset += name_len
                # number of partitions
                (num_parts,) = struct.unpack_from(">i", assignment_bytes, offset)
                offset += 4
                for _ in range(num_parts):
                    (pid,) = struct.unpack_from(">i", assignment_bytes, offset)
                    offset += 4
                    results.append((topic, pid))
        except Exception as exc:
            logger.debug("Could not parse assignment bytes: %s", exc)

        return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Stream-Sentinel Consumer Group Monitor"
    )
    parser.add_argument(
        "--group",
        "-g",
        default="fraud-detection-group",
        help="Consumer group ID to inspect (default: fraud-detection-group)",
    )
    parser.add_argument(
        "--topic",
        "-t",
        default="synthetic-transactions",
        help="Topic to query for lag (default: synthetic-transactions)",
    )
    parser.add_argument(
        "--watch",
        "-w",
        type=float,
        default=0,
        help="Continuously poll every N seconds (0 = once and exit)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output in JSON format",
    )
    parser.add_argument(
        "--prometheus",
        action="store_true",
        help="Output in Prometheus text exposition format",
    )

    args = parser.parse_args()

    monitor = ConsumerGroupMonitor(
        group_id=args.group,
        topics=[args.topic],
    )

    try:
        while True:
            snapshot = monitor.get_group_snapshot()

            if args.prometheus:
                print(snapshot.to_prometheus())
            elif args.json_output:
                print(json.dumps(snapshot.to_dict(), indent=2, default=str))
            else:
                _print_human_readable(snapshot)

            if args.watch <= 0:
                break
            time.sleep(args.watch)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.close()


def _print_human_readable(snap: GroupSnapshot) -> None:
    """Pretty-print a GroupSnapshot to stdout."""
    print(f"\n{'=' * 60}")
    print(f"Consumer Group: {snap.group_id}")
    print(f"State:          {snap.state}")
    print(f"Members:        {snap.member_count}")
    print(f"Partitions:     {snap.total_partitions}")
    print(f"Total Lag:      {snap.total_lag}")
    print(f"Balanced:       {snap.is_balanced}")
    print(f"{'=' * 60}")

    for member in snap.members:
        parts_str = ", ".join(
            f"{p['topic']}-{p['partition']}(lag={p['lag']})" for p in member.partitions
        )
        print(
            f"  {member.client_id} ({member.member_id[:24]}...) "
            f"@ {member.host} -> [{parts_str}]"
        )

    print()


if __name__ == "__main__":
    main()
