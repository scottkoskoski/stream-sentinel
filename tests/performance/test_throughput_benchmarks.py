"""
Performance / throughput benchmarks for the fraud detection pipeline.

These tests exercise the *real* FraudDetector scoring path (no mocked model)
against the live Redis/Kafka stack. They run on shared CI runners using the
pure-Python inference path (the C++ extension is not built there), so the
thresholds below are deliberately modest floors that catch gross regressions
and total breakage rather than the production targets (10k+ TPS, sub-ms
latency) which require dedicated hardware and the compiled inference engine.
"""

import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import psutil
import pytest
from confluent_kafka import Producer

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.fraud_detector import FraudDetector

# CI-appropriate throughput floors (see module docstring for rationale).
MIN_PROCESSING_TPS = 50
MIN_CONCURRENT_TPS = 50
MIN_REDIS_OPS_PER_SEC = 500


def _to_model_transaction(t: Dict[str, Any]) -> Dict[str, Any]:
    """Adapt a synthetic test transaction (user_id/amount/timestamp keys) to
    the producer/model field names the FraudDetector pipeline expects
    (card1/transaction_amt/generated_timestamp)."""
    return {
        "card1": str(t.get("user_id", "perf_user")),
        "transaction_amt": float(t.get("amount", 0.0)),
        "generated_timestamp": t.get("timestamp") or datetime.now().isoformat(),
        "transaction_id": t.get("transaction_id", "perf_txn"),
        "product_cd": "W",
    }


@dataclass
class PerformanceMetrics:
    """Performance measurement container."""

    transactions_processed: int
    total_time_seconds: float
    avg_latency_ms: float
    throughput_tps: float
    errors_count: int


@pytest.mark.performance
@pytest.mark.slow
@pytest.mark.requires_infrastructure
class TestThroughputBenchmarks:
    """Throughput and performance benchmarks against the real scoring path."""

    def test_sustained_10k_tps_processing(
        self,
        kafka_config,
        redis_client,
        test_topics,
        synthetic_transactions,
        performance_benchmarks,
        clean_test_environment,
    ):
        """Bounded Kafka production-throughput smoke test.

        The production target is 10k+ TPS sustained; on a shared CI runner we
        validate a bounded burst still produces at a healthy rate with a low
        error rate and bounded memory.
        """

        transaction_topic = test_topics[0]

        # Bounded burst rather than a multi-minute sustained load.
        base_transactions = synthetic_transactions[:1000]
        total_transactions = 5000
        test_transactions = []
        for i in range(total_transactions):
            base_tx = base_transactions[i % len(base_transactions)].copy()
            base_tx["transaction_id"] = f"perf_txn_{i:08d}"
            base_tx["user_id"] = f"perf_user_{i % 1000}"
            base_tx["timestamp"] = datetime.now().isoformat()
            test_transactions.append(base_tx)

        print(f"Generated {len(test_transactions)} transactions for performance test")

        # High-throughput producer configuration. Note: confluent-kafka uses
        # librdkafka property names (no kafka-python "buffer.memory").
        producer_config = kafka_config.get_producer_config("transaction")
        producer_config.update(
            {
                "batch.size": 65536,
                "linger.ms": 5,
                "compression.type": "lz4",
                "acks": "1",
                "retries": 3,
            }
        )
        producer = Producer(producer_config)

        start_time = time.time()
        produced_count = 0
        production_errors = 0

        process = psutil.Process()
        max_memory_mb = 0.0

        def delivery_callback(err, msg):
            nonlocal production_errors
            if err is not None:
                production_errors += 1

        monitoring_active = True

        def monitor_resources():
            nonlocal max_memory_mb
            while monitoring_active:
                max_memory_mb = max(max_memory_mb, process.memory_info().rss / 1024 / 1024)
                time.sleep(1)

        monitor_thread = threading.Thread(target=monitor_resources)
        monitor_thread.start()

        try:
            batch_size = 1000
            for i in range(0, len(test_transactions), batch_size):
                batch = test_transactions[i : i + batch_size]
                for transaction in batch:
                    producer.produce(
                        topic=transaction_topic,
                        key=transaction["user_id"],
                        value=json.dumps(transaction),
                        callback=delivery_callback,
                    )
                    produced_count += 1
                producer.poll(0)
                producer.flush(timeout=30)
            producer.flush(timeout=30)
        finally:
            monitoring_active = False
            monitor_thread.join()

        production_time = time.time() - start_time
        actual_production_tps = produced_count / production_time if production_time > 0 else 0.0

        print("Production completed:")
        print(f"  Transactions: {produced_count}")
        print(f"  Time: {production_time:.1f}s")
        print(f"  TPS: {actual_production_tps:.0f}")
        print(f"  Errors: {production_errors}")
        print(f"  Max Memory: {max_memory_mb:.0f}MB")

        assert produced_count == total_transactions
        assert (
            actual_production_tps >= MIN_REDIS_OPS_PER_SEC
        ), f"Production TPS {actual_production_tps:.0f} below CI floor {MIN_REDIS_OPS_PER_SEC}"
        assert production_errors < produced_count * 0.01, f"Too many production errors: {production_errors}"
        assert max_memory_mb < performance_benchmarks["max_memory_mb"], f"Memory usage {max_memory_mb}MB exceeds limit"

    def test_fraud_detection_processing_throughput(
        self,
        kafka_config,
        redis_client,
        test_topics,
        synthetic_transactions,
        performance_benchmarks,
        clean_test_environment,
    ):
        """Measure single-threaded fraud-scoring throughput via the real path."""

        test_transactions = synthetic_transactions[:2000]

        fraud_detector = FraudDetector()
        # Reuse one profile so we measure scoring throughput, not Redis round-trips.
        profile = fraud_detector.get_user_profile("perf_user")

        processing_times = []
        processed_count = 0
        processing_errors = 0

        start_time = time.time()
        print(f"Starting fraud detection processing for {len(test_transactions)} transactions...")

        for i, transaction in enumerate(test_transactions):
            model_tx = _to_model_transaction(transaction)
            tx_start = time.time()
            try:
                features = fraud_detector.extract_features(model_tx, profile)
                assert 0.0 <= features.fraud_score <= 1.0
                processed_count += 1
            except Exception as e:  # surface, don't silently pass as 0 TPS
                processing_errors += 1
                if processing_errors <= 3:
                    print(f"Processing error for transaction {i}: {e}")
            processing_times.append((time.time() - tx_start) * 1000)

        total_time = time.time() - start_time
        actual_tps = processed_count / total_time if total_time > 0 else 0.0
        avg_latency_ms = statistics.mean(processing_times) if processing_times else 0.0
        p99_latency_ms = (
            statistics.quantiles(processing_times, n=100)[98] if len(processing_times) >= 100 else max(processing_times)
        )

        print("Processing completed:")
        print(f"  Transactions: {processed_count}")
        print(f"  Time: {total_time:.1f}s")
        print(f"  TPS: {actual_tps:.0f}")
        print(f"  Avg Latency: {avg_latency_ms:.2f}ms")
        print(f"  P99 Latency: {p99_latency_ms:.2f}ms")
        print(f"  Errors: {processing_errors}")

        assert processing_errors == 0, f"Scoring raised {processing_errors} errors"
        assert actual_tps >= MIN_PROCESSING_TPS, f"Processing TPS {actual_tps:.0f} below CI floor {MIN_PROCESSING_TPS}"
        assert (
            avg_latency_ms <= performance_benchmarks["max_latency_ms"]
        ), f"Average latency {avg_latency_ms:.2f}ms exceeds limit"

    def test_concurrent_user_processing_scalability(
        self,
        kafka_config,
        redis_client,
        test_topics,
        synthetic_transactions,
        clean_test_environment,
    ):
        """Test scoring under concurrent multi-user load (real path)."""

        concurrent_users = 200
        transactions_per_user = 5

        user_transactions = {}
        for user_id in range(concurrent_users):
            user_key = f"concurrent_user_{user_id:04d}"
            user_transactions[user_key] = []
            for tx_id in range(transactions_per_user):
                base_tx = synthetic_transactions[tx_id].copy()
                base_tx["transaction_id"] = f"concurrent_{user_id}_{tx_id}"
                base_tx["user_id"] = user_key
                base_tx["timestamp"] = datetime.now().isoformat()
                user_transactions[user_key].append(base_tx)

        fraud_detector = FraudDetector()

        def process_user_transactions(user_id, transactions):
            profile = fraud_detector.get_user_profile(user_id)
            user_processing_times = []
            user_errors = 0
            for transaction in transactions:
                tx_start = time.time()
                try:
                    fraud_detector.extract_features(_to_model_transaction(transaction), profile)
                except Exception:
                    user_errors += 1
                user_processing_times.append((time.time() - tx_start) * 1000)
            return {
                "user_id": user_id,
                "transactions_processed": len(transactions) - user_errors,
                "errors": user_errors,
                "avg_latency_ms": (statistics.mean(user_processing_times) if user_processing_times else 0),
            }

        print(f"Starting concurrent processing for {concurrent_users} users...")
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(process_user_transactions, user_id, transactions)
                for user_id, transactions in user_transactions.items()
            ]
            user_results = []
            for future in as_completed(futures):
                try:
                    user_results.append(future.result(timeout=120))
                except Exception as e:
                    print(f"User processing failed: {e}")

        total_time = time.time() - start_time
        total_transactions = sum(r["transactions_processed"] for r in user_results)
        total_errors = sum(r["errors"] for r in user_results)
        concurrent_tps = total_transactions / total_time if total_time > 0 else 0.0

        print("Concurrent processing completed:")
        print(f"  Users: {len(user_results)}")
        print(f"  Total transactions: {total_transactions}")
        print(f"  Time: {total_time:.1f}s")
        print(f"  TPS: {concurrent_tps:.0f}")
        print(f"  Total errors: {total_errors}")

        assert (
            len(user_results) >= concurrent_users * 0.95
        ), f"Only {len(user_results)} of {concurrent_users} users processed"
        assert concurrent_tps >= MIN_CONCURRENT_TPS, f"Concurrent TPS {concurrent_tps:.0f} below CI floor"
        assert total_errors == 0, f"Errors during concurrent processing: {total_errors}"

    def test_memory_usage_under_sustained_load(
        self,
        kafka_config,
        redis_client,
        test_topics,
        synthetic_transactions,
        performance_benchmarks,
        clean_test_environment,
    ):
        """Test memory stability under sustained scoring load (real path)."""

        fraud_detector = FraudDetector()
        profile = fraud_detector.get_user_profile("mem_user")

        batch_size = 500
        num_batches = 10
        total_transactions = batch_size * num_batches

        memory_measurements = []
        process = psutil.Process()
        print(f"Testing memory usage with {total_transactions} transactions...")

        for batch_num in range(num_batches):
            batch_start_memory = process.memory_info().rss / 1024 / 1024
            for i in range(batch_size):
                transaction = synthetic_transactions[i % len(synthetic_transactions)].copy()
                transaction["transaction_id"] = f"mem_test_{batch_num}_{i}"
                transaction["user_id"] = f"mem_user_{(batch_num * batch_size + i) % 1000}"
                fraud_detector.extract_features(_to_model_transaction(transaction), profile)
            batch_end_memory = process.memory_info().rss / 1024 / 1024
            memory_measurements.append(
                {
                    "batch": batch_num,
                    "end_memory_mb": batch_end_memory,
                    "growth_mb": batch_end_memory - batch_start_memory,
                }
            )
            if batch_num % 5 == 0:
                print(f"Batch {batch_num}: Memory {batch_end_memory:.0f}MB")

        final_memory_mb = memory_measurements[-1]["end_memory_mb"]
        initial_memory_mb = memory_measurements[0]["end_memory_mb"]
        total_growth_mb = final_memory_mb - initial_memory_mb

        print("Memory analysis:")
        print(f"  Final memory: {final_memory_mb:.0f}MB")
        print(f"  Total growth: {total_growth_mb:.0f}MB")

        assert (
            final_memory_mb < performance_benchmarks["max_memory_mb"]
        ), f"Final memory usage {final_memory_mb}MB exceeds limit"
        # Growth should be bounded (no unbounded leak across batches).
        memory_growth_rate = total_growth_mb / num_batches
        assert memory_growth_rate < 50, f"Memory growth rate {memory_growth_rate:.1f}MB per batch is too high"

    def test_redis_connection_pool_performance(self, redis_client, clean_test_environment):
        """Test Redis throughput under concurrency (real Redis)."""

        num_threads = 50
        operations_per_thread = 100

        def redis_operations(thread_id):
            operations = []
            for i in range(operations_per_thread):
                start_time = time.time()
                profile_key = f"perf_user_{thread_id}_{i}"
                redis_client.hset(
                    profile_key,
                    mapping={
                        "user_id": f"user_{thread_id}_{i}",
                        "total_transactions": str(i),
                        "total_amount": str(i * 25.5),
                        "last_update": str(time.time()),
                    },
                )
                _ = redis_client.hgetall(profile_key)
                redis_client.hincrby(profile_key, "total_transactions", 1)
                redis_client.expire(profile_key, 3600)
                operations.append((time.time() - start_time) * 1000)
            return {
                "thread_id": thread_id,
                "operations": len(operations),
                "avg_latency_ms": statistics.mean(operations),
            }

        print(f"Testing Redis performance with {num_threads} threads...")
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(redis_operations, t) for t in range(num_threads)]
            thread_results = [f.result(timeout=60) for f in as_completed(futures)]

        total_time = time.time() - start_time
        total_operations = sum(r["operations"] for r in thread_results)
        avg_thread_latency = statistics.mean(r["avg_latency_ms"] for r in thread_results)
        operations_per_second = total_operations / total_time if total_time > 0 else 0.0

        print("Redis performance results:")
        print(f"  Total operations: {total_operations}")
        print(f"  Time: {total_time:.1f}s")
        print(f"  Operations/sec: {operations_per_second:.0f}")
        print(f"  Avg latency: {avg_thread_latency:.1f}ms")

        assert (
            operations_per_second >= MIN_REDIS_OPS_PER_SEC
        ), f"Redis operations/sec {operations_per_second:.0f} below CI floor {MIN_REDIS_OPS_PER_SEC}"
        assert avg_thread_latency <= 200, f"Average Redis latency {avg_thread_latency:.1f}ms is too high"
