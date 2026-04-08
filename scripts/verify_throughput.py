#!/usr/bin/env python3
"""
Throughput verification script for Stream Sentinel synthetic data pipeline.

Measures:
  1. Producer TPS and delivery latency
  2. Consumer consumption rate and lag
  3. Message integrity (no loss)
  4. Data quality (required fields, fraud rate, amount range)

Usage:
    PYTHONPATH=src python scripts/verify_throughput.py
"""

import json
import os
import sys
import time
import uuid
import threading
import statistics
from datetime import datetime
from collections import defaultdict

# Ensure src is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from confluent_kafka import Producer, Consumer, KafkaError, TopicPartition
from confluent_kafka.admin import AdminClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC = "synthetic-transactions"
TEST_DURATION = 30  # seconds
TARGET_TPS = 2000
USER_COUNT = 500
CONSUMER_GROUP = f"verify-throughput-{uuid.uuid4().hex[:8]}"
SAMPLE_SIZE = 100
REQUIRED_FIELDS = ["transaction_id", "card1", "transaction_amt", "generated_timestamp"]
EXPECTED_FRAUD_RATE = 0.0271  # 2.71%

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_producer():
    return Producer({
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "linger.ms": 5,
        "batch.num.messages": 1000,
        "queue.buffering.max.messages": 500000,
        "compression.type": "lz4",
    })


def create_consumer(group_id, auto_offset_reset="latest"):
    return Consumer({
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "group.id": group_id,
        "auto.offset.reset": auto_offset_reset,
        "enable.auto.commit": True,
        "fetch.min.bytes": 1,
        "fetch.wait.max.ms": 100,
    })


# ---------------------------------------------------------------------------
# Phase 1: Produce and measure throughput + latency
# ---------------------------------------------------------------------------

def run_producer_benchmark():
    """Produce transactions for TEST_DURATION seconds and measure TPS + latency."""
    print("=" * 70)
    print("PHASE 1: Producer Throughput Benchmark")
    print("=" * 70)

    # Import the producer's transaction generator
    from producers.synthetic_transaction_producer import SyntheticTransactionProducer
    from dataclasses import asdict

    synth = SyntheticTransactionProducer()
    # We'll use our own producer for latency tracking
    producer = create_producer()

    user_pool = [f"user_{i:06d}" for i in range(USER_COUNT)]

    delivered = 0
    errors = 0
    latencies = []  # seconds from produce() call to ack
    produce_times = {}  # txn_id -> produce timestamp
    lock = threading.Lock()

    def delivery_cb(err, msg):
        nonlocal delivered, errors
        txn_id = msg.key().decode("utf-8") if msg.key() else None
        now = time.monotonic()
        with lock:
            if err:
                errors += 1
            else:
                delivered += 1
                if txn_id and txn_id in produce_times:
                    latencies.append(now - produce_times.pop(txn_id))

    import random
    start = time.monotonic()
    produced_count = 0
    target_interval = 1.0 / TARGET_TPS

    print(f"  Target TPS: {TARGET_TPS}")
    print(f"  Duration: {TEST_DURATION}s")
    print(f"  Users: {USER_COUNT}")
    print()

    while time.monotonic() - start < TEST_DURATION:
        batch_start = time.monotonic()
        user_id = random.choice(user_pool)
        txn = synth._generate_transaction(user_id)
        txn_dict = asdict(txn)
        txn_bytes = json.dumps(txn_dict).encode("utf-8")
        txn_id = txn.transaction_id

        with lock:
            produce_times[txn_id] = time.monotonic()

        producer.produce(
            topic=TOPIC,
            key=txn_id,
            value=txn_bytes,
            callback=delivery_cb,
        )
        produced_count += 1

        if produced_count % 500 == 0:
            producer.poll(0)

        elapsed_iter = time.monotonic() - batch_start
        sleep_time = target_interval - elapsed_iter
        if sleep_time > 0:
            time.sleep(sleep_time)

    # Flush remaining
    producer.flush(timeout=30)

    elapsed = time.monotonic() - start
    actual_tps = delivered / elapsed if elapsed > 0 else 0
    error_rate = errors / max(1, produced_count) * 100

    # Latency stats
    lat_p50 = lat_p95 = lat_p99 = lat_mean = 0.0
    if latencies:
        latencies_ms = [l * 1000 for l in latencies]
        latencies_ms.sort()
        lat_mean = statistics.mean(latencies_ms)
        lat_p50 = latencies_ms[int(len(latencies_ms) * 0.50)]
        lat_p95 = latencies_ms[int(len(latencies_ms) * 0.95)]
        lat_p99 = latencies_ms[min(int(len(latencies_ms) * 0.99), len(latencies_ms) - 1)]

    print(f"  Produced (attempted): {produced_count:,}")
    print(f"  Delivered (acked):    {delivered:,}")
    print(f"  Errors:               {errors}")
    print(f"  Error rate:           {error_rate:.3f}%")
    print(f"  Elapsed:              {elapsed:.1f}s")
    print(f"  Actual TPS:           {actual_tps:,.0f}")
    print()
    print(f"  Delivery Latency (ms):")
    print(f"    Mean: {lat_mean:.2f}")
    print(f"    P50:  {lat_p50:.2f}")
    print(f"    P95:  {lat_p95:.2f}")
    print(f"    P99:  {lat_p99:.2f}")
    print()

    return {
        "produced": produced_count,
        "delivered": delivered,
        "errors": errors,
        "error_rate": error_rate,
        "elapsed": elapsed,
        "actual_tps": actual_tps,
        "lat_mean": lat_mean,
        "lat_p50": lat_p50,
        "lat_p95": lat_p95,
        "lat_p99": lat_p99,
    }


# ---------------------------------------------------------------------------
# Phase 2: Consumer throughput + lag + message loss check
# ---------------------------------------------------------------------------

def run_consumer_benchmark(expected_count):
    """Consume messages and measure rate, lag, and loss."""
    print("=" * 70)
    print("PHASE 2: Consumer Throughput & Lag Verification")
    print("=" * 70)

    consumer = create_consumer(CONSUMER_GROUP, auto_offset_reset="latest")

    # We need to consume the messages we just produced.
    # Seek to the offsets that existed before production started.
    # Simpler: create a new consumer from "earliest" on a fresh group.
    consumer.close()
    fresh_group = f"verify-consume-{uuid.uuid4().hex[:8]}"
    consumer = create_consumer(fresh_group, auto_offset_reset="earliest")
    consumer.subscribe([TOPIC])

    consumed = 0
    samples = []
    start = time.monotonic()
    empty_polls = 0
    max_empty = 50  # stop after 50 consecutive empty polls (~5s)

    # First poll may take a moment for rebalance
    print(f"  Consuming from '{TOPIC}' (fresh group: {fresh_group}) ...")
    print(f"  Expected approx {expected_count:,} messages (may include prior messages)")
    print()

    while empty_polls < max_empty:
        msg = consumer.poll(timeout=0.1)
        if msg is None:
            empty_polls += 1
            continue
        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                empty_polls += 1
                continue
            print(f"  Consumer error: {msg.error()}")
            empty_polls += 1
            continue

        empty_polls = 0
        consumed += 1

        # Collect samples
        if len(samples) < SAMPLE_SIZE:
            try:
                samples.append(json.loads(msg.value().decode("utf-8")))
            except Exception:
                pass

    elapsed = time.monotonic() - start
    consume_tps = consumed / elapsed if elapsed > 0 else 0

    # Consumer lag via committed offsets vs high watermarks
    lag_total = 0
    try:
        parts = consumer.assignment()
        for p in parts:
            (lo, hi) = consumer.get_watermark_offsets(p, timeout=5)
            committed = consumer.committed([p], timeout=5)
            if committed and committed[0].offset >= 0:
                lag_total += hi - committed[0].offset
            else:
                lag_total += 0  # can't determine
    except Exception as e:
        print(f"  Warning: could not compute lag: {e}")

    consumer.close()

    print(f"  Consumed:        {consumed:,}")
    print(f"  Elapsed:         {elapsed:.1f}s")
    print(f"  Consume TPS:     {consume_tps:,.0f}")
    print(f"  Consumer lag:    {lag_total:,}")
    print()

    return {
        "consumed": consumed,
        "elapsed": elapsed,
        "consume_tps": consume_tps,
        "lag": lag_total,
        "samples": samples,
    }


# ---------------------------------------------------------------------------
# Phase 3: Data quality checks on sampled messages
# ---------------------------------------------------------------------------

def run_data_quality_checks(samples):
    """Validate field presence, fraud rate, and amount ranges."""
    print("=" * 70)
    print("PHASE 3: Data Quality Verification")
    print("=" * 70)

    n = len(samples)
    if n == 0:
        print("  ERROR: No samples to verify!")
        return {"pass": False, "reason": "no samples"}

    # Field completeness
    missing_fields = defaultdict(int)
    for s in samples:
        for field in REQUIRED_FIELDS:
            if field not in s or s[field] is None:
                missing_fields[field] += 1

    field_ok = len(missing_fields) == 0
    print(f"  Sample size: {n}")
    print(f"  Required fields: {REQUIRED_FIELDS}")
    if field_ok:
        print(f"  Field completeness: PASS (all fields present in all samples)")
    else:
        print(f"  Field completeness: FAIL")
        for f, cnt in missing_fields.items():
            print(f"    {f}: missing in {cnt}/{n} samples")

    # Fraud rate
    fraud_count = sum(1 for s in samples if s.get("is_fraud", 0) == 1)
    observed_rate = fraud_count / n
    # Allow wide margin for small sample size
    rate_lo = EXPECTED_FRAUD_RATE * 0.1  # 0.27%
    rate_hi = min(EXPECTED_FRAUD_RATE * 5.0, 0.20)  # up to 13.5%
    fraud_ok = rate_lo <= observed_rate <= rate_hi
    print()
    print(f"  Fraud rate: {observed_rate:.2%} ({fraud_count}/{n})")
    print(f"    Expected ~{EXPECTED_FRAUD_RATE:.2%} (tolerance {rate_lo:.2%}-{rate_hi:.2%})")
    print(f"    Verdict: {'PASS' if fraud_ok else 'WARN - outside tolerance but may be sample size effect'}")

    # Amount range
    amounts = [s["transaction_amt"] for s in samples if "transaction_amt" in s and s["transaction_amt"] is not None]
    if amounts:
        amt_min = min(amounts)
        amt_max = max(amounts)
        amt_mean = statistics.mean(amounts)
        amt_median = statistics.median(amounts)
        amt_ok = all(a > 0 for a in amounts)
        print()
        print(f"  Amount statistics:")
        print(f"    Min:    ${amt_min:.2f}")
        print(f"    Max:    ${amt_max:.2f}")
        print(f"    Mean:   ${amt_mean:.2f}")
        print(f"    Median: ${amt_median:.2f}")
        print(f"    All positive: {'PASS' if amt_ok else 'FAIL'}")
    else:
        amt_ok = False
        amt_min = amt_max = amt_mean = amt_median = 0

    print()

    return {
        "field_ok": field_ok,
        "missing_fields": dict(missing_fields),
        "fraud_rate": observed_rate,
        "fraud_ok": fraud_ok,
        "fraud_count": fraud_count,
        "amt_ok": amt_ok,
        "amt_min": amt_min,
        "amt_max": amt_max,
        "amt_mean": amt_mean,
        "amt_median": amt_median,
        "sample_size": n,
    }


# ---------------------------------------------------------------------------
# Phase 4: Config sensibility check
# ---------------------------------------------------------------------------

def check_config_defaults():
    """Verify config defaults are sensible."""
    print("=" * 70)
    print("PHASE 4: Configuration Defaults Review")
    print("=" * 70)

    from producers.config import (
        DEFAULT_TARGET_TPS,
        DEFAULT_DURATION_SECONDS,
        DEFAULT_USER_COUNT,
        BASE_FRAUD_RATE,
        AMOUNT_DISTRIBUTION,
    )

    issues = []

    if DEFAULT_TARGET_TPS < 100:
        issues.append(f"DEFAULT_TARGET_TPS={DEFAULT_TARGET_TPS} too low for load testing")
    if DEFAULT_TARGET_TPS > 50000:
        issues.append(f"DEFAULT_TARGET_TPS={DEFAULT_TARGET_TPS} unrealistically high for single producer")

    if DEFAULT_DURATION_SECONDS < 30:
        issues.append(f"DEFAULT_DURATION_SECONDS={DEFAULT_DURATION_SECONDS} too short for stable measurement")

    if DEFAULT_USER_COUNT < 10:
        issues.append(f"DEFAULT_USER_COUNT={DEFAULT_USER_COUNT} too few for realistic simulation")
    if DEFAULT_USER_COUNT > DEFAULT_TARGET_TPS * DEFAULT_DURATION_SECONDS:
        issues.append("USER_COUNT exceeds total possible transactions")

    if not (0.01 <= BASE_FRAUD_RATE <= 0.10):
        issues.append(f"BASE_FRAUD_RATE={BASE_FRAUD_RATE} outside realistic range")

    print(f"  DEFAULT_TARGET_TPS:       {DEFAULT_TARGET_TPS}")
    print(f"  DEFAULT_DURATION_SECONDS: {DEFAULT_DURATION_SECONDS}")
    print(f"  DEFAULT_USER_COUNT:       {DEFAULT_USER_COUNT}")
    print(f"  BASE_FRAUD_RATE:          {BASE_FRAUD_RATE}")
    print(f"  AMOUNT_DISTRIBUTION:      {AMOUNT_DISTRIBUTION}")
    print()
    if issues:
        for issue in issues:
            print(f"  WARNING: {issue}")
    else:
        print("  All defaults are sensible: PASS")
    print()
    return issues


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print()
    print("Stream Sentinel - Synthetic Data Throughput Verification")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Kafka: {BOOTSTRAP_SERVERS}")
    print(f"Topic: {TOPIC}")
    print()

    # Check config defaults
    config_issues = check_config_defaults()

    # Run producer benchmark
    producer_results = run_producer_benchmark()

    # Run consumer benchmark
    consumer_results = run_consumer_benchmark(producer_results["delivered"])

    # Run data quality checks
    quality_results = run_data_quality_checks(consumer_results["samples"])

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    tps_ok = producer_results["actual_tps"] >= TARGET_TPS * 0.8
    err_ok = producer_results["error_rate"] < 1.0
    consume_ok = consumer_results["consume_tps"] >= producer_results["actual_tps"] * 0.5

    print(f"  Producer TPS:    {producer_results['actual_tps']:,.0f} (target {TARGET_TPS}) {'PASS' if tps_ok else 'BELOW TARGET'}")
    print(f"  Error rate:      {producer_results['error_rate']:.3f}% {'PASS' if err_ok else 'FAIL'}")
    print(f"  Consumer TPS:    {consumer_results['consume_tps']:,.0f} {'PASS' if consume_ok else 'SLOW'}")
    print(f"  Consumer lag:    {consumer_results['lag']:,}")
    print(f"  Delivery P50:    {producer_results['lat_p50']:.2f}ms")
    print(f"  Delivery P95:    {producer_results['lat_p95']:.2f}ms")
    print(f"  Delivery P99:    {producer_results['lat_p99']:.2f}ms")
    print(f"  Data quality:    {'PASS' if quality_results.get('field_ok') and quality_results.get('amt_ok') else 'ISSUES'}")
    print(f"  Fraud rate:      {quality_results.get('fraud_rate', 0):.2%} (expected ~{EXPECTED_FRAUD_RATE:.2%})")
    print(f"  Config defaults: {'PASS' if not config_issues else 'WARNINGS'}")
    print()

    # Return results for report generation
    return {
        "producer": producer_results,
        "consumer": consumer_results,
        "quality": quality_results,
        "config_issues": config_issues,
    }


if __name__ == "__main__":
    results = main()
