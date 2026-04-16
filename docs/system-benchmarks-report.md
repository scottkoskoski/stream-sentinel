# System Benchmarks Report

> **Update (2026-04-16):** This report captures the baseline before the
> C++ inference extension was built and the feature-extractor hot path
> was vectorized. The end-to-end per-transaction numbers below (~30 ms
> single / ~7 ms batch) no longer reflect the current state -- a single
> message now costs ~0.32 ms end-to-end on the single-message path
> (~3,100 txn/sec per consumer). See `README.md` Performance table or
> `docs/fraud-detection/README.md` for the current numbers. The
> throughput and data-quality sections below remain accurate as the
> producer characterization of that date.

## Test Environment

- Single Kafka broker (localhost:9092), 12 partitions on `synthetic-transactions`
- Single-threaded producer and consumer processes
- Python 3.13, confluent-kafka, XGBoost 2.1.3
- Hardware: 12-core CPU, local Docker infrastructure

## Producer Throughput

| Metric | Value |
|--------|-------|
| Target TPS | 2,000 |
| Measured TPS | 949 |
| Test Duration | 30 seconds |
| Messages Produced | 28,482 |
| Delivery Errors | 0 (0.000%) |
| Users Simulated | 5,000 |

### Delivery Latency (Kafka ack round-trip)

| Percentile | Latency |
|-----------|---------|
| P50 | 257 ms |
| P95 | 563 ms |
| P99 | 636 ms |
| Mean | 269 ms |

### Bottleneck Analysis

The producer achieves 949 TPS against a 2,000 TPS target. The bottleneck is CPU-bound transaction generation, not Kafka throughput. Each synthetic transaction requires:
- IEEE-CIS feature generation (50+ fields with entity tracking)
- C/D/M feature computation with entity state lookups
- Fraud correlation logic (temporal, velocity, amount multipliers)
- JSON serialization

**Scaling path**: Multiple producer instances across the 12-partition topic would reach 10k+ TPS linearly. A single producer is sufficient for development and testing.

## Consumer Throughput

| Metric | Value |
|--------|-------|
| Messages Consumed | 118,148 |
| Elapsed Time | 5.6 seconds |
| Measured TPS | 21,196 |
| Consumer Lag | 0 |

The consumer processes messages 22x faster than the producer generates them, with zero lag. The consumer is not the bottleneck at any realistic single-producer load.

## End-to-End Pipeline Latency

### Fraud Detection (fraud_detector.py)

| Component | Latency |
|-----------|---------|
| Redis blocked_users check (SISMEMBER) | < 1 ms |
| Redis user profile load (HGETALL) | < 1 ms |
| Feature extraction | < 5 ms |
| ML model inference (single) | ~21 ms P50 |
| ML model inference (batch-128) | ~0.047 ms/prediction |
| Kafka publish (fraud-alerts) | < 1 ms |
| **Total per transaction (single mode)** | **~30 ms** |
| **Total per transaction (batch-128)** | **~7 ms** |

### Alert Processing (alert_processor.py)

| Component | Latency |
|-----------|---------|
| Alert deserialization | < 1 ms |
| Severity classification | < 1 ms |
| Response action execution | 1-5 ms |
| Redis blocking (SADD) | < 1 ms |
| **Total per alert** | **< 5 ms** |

SLA compliance observed during testing:
- All alerts processed within SLA targets
- CRITICAL (1s target): met
- HIGH (5s target): met

## Data Quality

### Field Completeness

All 28,482 produced messages contained all required fields:
- `transaction_id`: 100% present
- `card1`: 100% present
- `transaction_amt`: 100% present, all positive
- `generated_timestamp`: 100% present

### Fraud Rate

| Metric | Value |
|--------|-------|
| Target fraud rate | 2.71% (IEEE-CIS baseline) |
| Configured rate | ~2.78% (post-calibration) |
| Observed in 100-sample check | 5.0% (within sampling variance for small n) |

### Amount Distribution

| Statistic | Value |
|-----------|-------|
| Minimum | $0.25 |
| Maximum | $5,000 |
| Mean | $141.35 |
| Median | $51.31 |

## Resource Utilization

| Resource | Usage |
|----------|-------|
| Project disk usage | 4.0 GB |
| Production model | 59 MB (pkl) |
| Docker containers | 10 services |
| Redis keys (after run) | ~18,847 user profiles |

## Performance Summary

| Benchmark | Result | Target | Status |
|-----------|--------|--------|--------|
| Producer TPS (single) | 949 | 2,000 | Below target (CPU-bound, scales with instances) |
| Consumer TPS | 21,196 | 10,000 | Exceeds target |
| Fraud detection P99 | ~40 ms | < 100 ms | Meets target |
| Batch inference throughput | 16,979 pred/s | 10,000 | Exceeds target |
| Alert processing | < 5 ms | < 1 s | Meets target |
| Delivery error rate | 0.000% | < 0.01% | Meets target |
| Consumer lag | 0 | < 1 s | Meets target |
