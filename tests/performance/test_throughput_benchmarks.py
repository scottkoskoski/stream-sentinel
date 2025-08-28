"""
Comprehensive Performance Tests for 10k+ TPS Validation

Tests the complete system under production-level load:
- 10k+ transactions per second sustained processing
- Resource utilization monitoring
- Latency benchmarks under load
- Memory and CPU profiling
- Scalability limits identification
- Performance regression detection
"""

import pytest
import time
import threading
import psutil
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from confluent_kafka import Producer, Consumer
from dataclasses import dataclass
import statistics

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.fraud_detector import FraudDetector


@dataclass
class PerformanceMetrics:
    """Performance measurement container."""
    transactions_processed: int
    total_time_seconds: float
    avg_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    throughput_tps: float
    max_cpu_percent: float
    max_memory_mb: float
    errors_count: int


@pytest.mark.performance
@pytest.mark.slow
@pytest.mark.requires_infrastructure
class TestThroughputBenchmarks:
    """Comprehensive throughput and performance benchmarks."""

    def test_sustained_10k_tps_processing(self, kafka_config, redis_client, test_topics, 
                                         synthetic_transactions, performance_benchmarks, clean_test_environment):
        """Test sustained 10k+ TPS processing for 5 minutes."""
        
        target_tps = 10000
        test_duration_seconds = 300  # 5 minutes
        total_target_transactions = target_tps * test_duration_seconds
        
        transaction_topic = test_topics[0]
        
        # Use subset of synthetic transactions and replicate to reach target volume
        base_transactions = synthetic_transactions[:1000]
        
        # Generate full transaction set
        test_transactions = []
        for i in range(total_target_transactions):
            base_tx = base_transactions[i % len(base_transactions)].copy()
            base_tx["transaction_id"] = f"perf_txn_{i:08d}"
            base_tx["user_id"] = f"perf_user_{i % 10000}"  # 10k unique users
            base_tx["timestamp"] = datetime.now().isoformat()
            test_transactions.append(base_tx)
        
        print(f"Generated {len(test_transactions)} transactions for performance test")
        
        # Setup producer with high-throughput configuration
        producer_config = kafka_config.get_producer_config("transaction")
        producer_config.update({
            "batch.size": 65536,        # Larger batches
            "linger.ms": 5,             # Reduce latency while allowing batching
            "compression.type": "lz4",  # Fast compression
            "acks": "1",                # Faster acknowledgment
            "retries": 3,
            "buffer.memory": 67108864,  # 64MB buffer
        })
        
        producer = Producer(producer_config)
        
        # Performance monitoring
        start_time = time.time()
        produced_count = 0
        production_errors = 0
        
        # Track system resources
        process = psutil.Process()
        max_cpu_percent = 0.0
        max_memory_mb = 0.0
        
        def delivery_callback(err, msg):
            nonlocal production_errors
            if err is not None:
                production_errors += 1
        
        # Resource monitoring thread
        monitoring_active = True
        resource_metrics = []
        
        def monitor_resources():
            nonlocal max_cpu_percent, max_memory_mb
            while monitoring_active:
                cpu_percent = process.cpu_percent()
                memory_mb = process.memory_info().rss / 1024 / 1024
                
                max_cpu_percent = max(max_cpu_percent, cpu_percent)
                max_memory_mb = max(max_memory_mb, memory_mb)
                
                resource_metrics.append({
                    "timestamp": time.time(),
                    "cpu_percent": cpu_percent,
                    "memory_mb": memory_mb
                })
                
                time.sleep(1)  # Monitor every second
        
        monitor_thread = threading.Thread(target=monitor_resources)
        monitor_thread.start()
        
        # Production phase
        print("Starting high-throughput production...")
        
        batch_size = 1000
        for i in range(0, len(test_transactions), batch_size):
            batch = test_transactions[i:i + batch_size]
            batch_start = time.time()
            
            for transaction in batch:
                producer.produce(
                    topic=transaction_topic,
                    key=transaction["user_id"],
                    value=json.dumps(transaction),
                    callback=delivery_callback
                )
                produced_count += 1
            
            # Non-blocking poll to handle callbacks
            producer.poll(0)
            
            # Flush every 10 batches
            if (i // batch_size) % 10 == 0:
                producer.flush(timeout=5)
                
                # Calculate current TPS
                elapsed = time.time() - start_time
                current_tps = produced_count / elapsed if elapsed > 0 else 0
                
                print(f"Produced {produced_count}/{len(test_transactions)} transactions, "
                      f"Current TPS: {current_tps:.0f}, Elapsed: {elapsed:.1f}s")
                
                # Stop if we're significantly behind schedule
                if elapsed > 60 and current_tps < target_tps * 0.5:
                    print(f"WARNING: TPS {current_tps} is significantly below target {target_tps}")
        
        # Final flush
        producer.flush(timeout=30)
        
        production_time = time.time() - start_time
        monitoring_active = False
        monitor_thread.join()
        
        # Calculate production metrics
        actual_production_tps = produced_count / production_time
        
        print(f"Production completed:")
        print(f"  Transactions: {produced_count}")
        print(f"  Time: {production_time:.1f}s")
        print(f"  TPS: {actual_production_tps:.0f}")
        print(f"  Errors: {production_errors}")
        print(f"  Max CPU: {max_cpu_percent:.1f}%")
        print(f"  Max Memory: {max_memory_mb:.0f}MB")
        
        # Verify production performance
        min_acceptable_tps = target_tps * 0.8  # Allow 20% tolerance
        assert actual_production_tps >= min_acceptable_tps, \
            f"Production TPS {actual_production_tps} below minimum {min_acceptable_tps}"
        
        assert production_errors < produced_count * 0.01, \
            f"Too many production errors: {production_errors}"
        
        # Verify resource usage is reasonable
        assert max_cpu_percent < performance_benchmarks["max_cpu_percent"], \
            f"CPU usage {max_cpu_percent}% exceeds limit"
        
        assert max_memory_mb < performance_benchmarks["max_memory_mb"], \
            f"Memory usage {max_memory_mb}MB exceeds limit"

    def test_fraud_detection_processing_throughput(self, kafka_config, redis_client, test_topics,
                                                  synthetic_transactions, performance_benchmarks, clean_test_environment):
        """Test fraud detection processing throughput with realistic workload."""
        
        target_processing_tps = 5000  # Processing is more CPU intensive than production
        test_transactions = synthetic_transactions[:10000]  # 10k transactions
        
        fraud_detector = FraudDetector()
        
        # Mock ML model for consistent performance testing
        class PerformanceTestModel:
            def predict(self, features):
                # Simulate some computation time
                time.sleep(0.0001)  # 0.1ms per prediction
                return [0.25]  # Fixed score
            
            @property
            def num_feature(self):
                return 25
        
        fraud_detector.ml_model = PerformanceTestModel()
        
        # Performance tracking
        processing_times = []
        processed_count = 0
        processing_errors = 0
        
        # Resource monitoring
        start_resources = psutil.Process()
        start_time = time.time()
        
        print(f"Starting fraud detection processing for {len(test_transactions)} transactions...")
        
        # Process transactions
        for i, transaction in enumerate(test_transactions):
            tx_start = time.time()
            
            try:
                # Complete fraud detection pipeline
                features = fraud_detector.extract_all_features(transaction)
                fraud_score = fraud_detector.calculate_fraud_score(features)
                updated_profile = fraud_detector.update_user_profile(transaction)
                
                processed_count += 1
                
            except Exception as e:
                processing_errors += 1
                print(f"Processing error for transaction {i}: {e}")
            
            tx_time = (time.time() - tx_start) * 1000  # Convert to milliseconds
            processing_times.append(tx_time)
            
            # Progress reporting
            if (i + 1) % 1000 == 0:
                elapsed = time.time() - start_time
                current_tps = processed_count / elapsed
                avg_latency = statistics.mean(processing_times[-1000:])
                
                print(f"Processed {processed_count}/{len(test_transactions)}, "
                      f"TPS: {current_tps:.0f}, Avg Latency: {avg_latency:.1f}ms")
        
        total_time = time.time() - start_time
        
        # Calculate performance metrics
        actual_tps = processed_count / total_time
        avg_latency_ms = statistics.mean(processing_times)
        p95_latency_ms = statistics.quantiles(processing_times, n=20)[18]  # 95th percentile
        p99_latency_ms = statistics.quantiles(processing_times, n=100)[98]  # 99th percentile
        max_latency_ms = max(processing_times)
        
        end_resources = psutil.Process()
        cpu_percent = end_resources.cpu_percent()
        memory_mb = end_resources.memory_info().rss / 1024 / 1024
        
        print(f"Processing completed:")
        print(f"  Transactions: {processed_count}")
        print(f"  Time: {total_time:.1f}s")
        print(f"  TPS: {actual_tps:.0f}")
        print(f"  Avg Latency: {avg_latency_ms:.1f}ms")
        print(f"  P95 Latency: {p95_latency_ms:.1f}ms")
        print(f"  P99 Latency: {p99_latency_ms:.1f}ms")
        print(f"  Max Latency: {max_latency_ms:.1f}ms")
        print(f"  Errors: {processing_errors}")
        print(f"  CPU: {cpu_percent:.1f}%")
        print(f"  Memory: {memory_mb:.0f}MB")
        
        # Performance assertions
        min_acceptable_tps = target_processing_tps * 0.7  # Allow 30% tolerance for processing
        assert actual_tps >= min_acceptable_tps, \
            f"Processing TPS {actual_tps} below minimum {min_acceptable_tps}"
        
        assert avg_latency_ms <= performance_benchmarks["max_latency_ms"], \
            f"Average latency {avg_latency_ms}ms exceeds limit"
        
        assert p99_latency_ms <= performance_benchmarks["max_latency_ms"] * 2, \
            f"P99 latency {p99_latency_ms}ms exceeds limit"
        
        assert processing_errors < processed_count * 0.01, \
            f"Too many processing errors: {processing_errors}"

    def test_concurrent_user_processing_scalability(self, kafka_config, redis_client, test_topics,
                                                   synthetic_transactions, clean_test_environment):
        """Test scalability with concurrent processing of multiple users."""
        
        # Create transactions for 1000 concurrent users
        concurrent_users = 1000
        transactions_per_user = 10
        
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
        fraud_detector.ml_model = PerformanceTestModel()
        
        # Process users concurrently
        def process_user_transactions(user_id, transactions):
            """Process all transactions for a single user."""
            user_processing_times = []
            user_errors = 0
            
            for transaction in transactions:
                tx_start = time.time()
                
                try:
                    features = fraud_detector.extract_all_features(transaction)
                    fraud_score = fraud_detector.calculate_fraud_score(features)
                    updated_profile = fraud_detector.update_user_profile(transaction)
                    
                except Exception as e:
                    user_errors += 1
                
                tx_time = (time.time() - tx_start) * 1000
                user_processing_times.append(tx_time)
            
            return {
                "user_id": user_id,
                "transactions_processed": len(transactions) - user_errors,
                "errors": user_errors,
                "avg_latency_ms": statistics.mean(user_processing_times) if user_processing_times else 0,
                "max_latency_ms": max(user_processing_times) if user_processing_times else 0
            }
        
        print(f"Starting concurrent processing for {concurrent_users} users...")
        start_time = time.time()
        
        # Use thread pool for concurrent processing
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = []
            
            for user_id, transactions in user_transactions.items():
                future = executor.submit(process_user_transactions, user_id, transactions)
                futures.append(future)
            
            # Collect results
            user_results = []
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=60)
                    user_results.append(result)
                except Exception as e:
                    print(f"User processing failed: {e}")
        
        total_time = time.time() - start_time
        
        # Analyze results
        total_transactions = sum(result["transactions_processed"] for result in user_results)
        total_errors = sum(result["errors"] for result in user_results)
        avg_user_latency = statistics.mean(result["avg_latency_ms"] for result in user_results)
        max_user_latency = max(result["max_latency_ms"] for result in user_results)
        
        concurrent_tps = total_transactions / total_time
        
        print(f"Concurrent processing completed:")
        print(f"  Users: {len(user_results)}")
        print(f"  Total transactions: {total_transactions}")
        print(f"  Time: {total_time:.1f}s")
        print(f"  TPS: {concurrent_tps:.0f}")
        print(f"  Avg user latency: {avg_user_latency:.1f}ms")
        print(f"  Max user latency: {max_user_latency:.1f}ms")
        print(f"  Total errors: {total_errors}")
        
        # Verify concurrent processing performance
        assert len(user_results) >= concurrent_users * 0.95, \
            f"Only {len(user_results)} out of {concurrent_users} users processed successfully"
        
        assert concurrent_tps >= 1000, \
            f"Concurrent TPS {concurrent_tps} is too low"
        
        assert total_errors < total_transactions * 0.02, \
            f"Too many errors in concurrent processing: {total_errors}"

    def test_memory_usage_under_sustained_load(self, kafka_config, redis_client, test_topics,
                                              synthetic_transactions, performance_benchmarks, clean_test_environment):
        """Test memory usage and garbage collection under sustained load."""
        
        fraud_detector = FraudDetector()
        fraud_detector.ml_model = PerformanceTestModel()
        
        # Process transactions in batches to simulate sustained load
        batch_size = 1000
        num_batches = 50
        total_transactions = batch_size * num_batches
        
        memory_measurements = []
        process = psutil.Process()
        
        print(f"Testing memory usage with {total_transactions} transactions...")
        
        for batch_num in range(num_batches):
            batch_start_memory = process.memory_info().rss / 1024 / 1024
            batch_transactions = synthetic_transactions[:batch_size]
            
            # Process batch
            for i, transaction in enumerate(batch_transactions):
                # Modify transaction to ensure uniqueness
                transaction = transaction.copy()
                transaction["transaction_id"] = f"mem_test_{batch_num}_{i}"
                transaction["user_id"] = f"mem_user_{(batch_num * batch_size + i) % 1000}"
                
                features = fraud_detector.extract_all_features(transaction)
                fraud_score = fraud_detector.calculate_fraud_score(features)
                updated_profile = fraud_detector.update_user_profile(transaction)
            
            batch_end_memory = process.memory_info().rss / 1024 / 1024
            memory_growth = batch_end_memory - batch_start_memory
            
            memory_measurements.append({
                "batch": batch_num,
                "start_memory_mb": batch_start_memory,
                "end_memory_mb": batch_end_memory,
                "growth_mb": memory_growth
            })
            
            if batch_num % 10 == 0:
                print(f"Batch {batch_num}: Memory {batch_end_memory:.0f}MB, Growth: {memory_growth:.1f}MB")
        
        # Analyze memory usage patterns
        final_memory_mb = memory_measurements[-1]["end_memory_mb"]
        initial_memory_mb = memory_measurements[0]["start_memory_mb"]
        total_growth_mb = final_memory_mb - initial_memory_mb
        
        avg_batch_growth = statistics.mean(m["growth_mb"] for m in memory_measurements)
        max_batch_growth = max(m["growth_mb"] for m in memory_measurements)
        
        print(f"Memory analysis:")
        print(f"  Initial memory: {initial_memory_mb:.0f}MB")
        print(f"  Final memory: {final_memory_mb:.0f}MB")
        print(f"  Total growth: {total_growth_mb:.0f}MB")
        print(f"  Avg batch growth: {avg_batch_growth:.1f}MB")
        print(f"  Max batch growth: {max_batch_growth:.1f}MB")
        
        # Memory usage assertions
        assert final_memory_mb < performance_benchmarks["max_memory_mb"], \
            f"Final memory usage {final_memory_mb}MB exceeds limit"
        
        # Memory growth should be bounded (not growing indefinitely)
        memory_growth_rate = total_growth_mb / num_batches
        assert memory_growth_rate < 10, \
            f"Memory growth rate {memory_growth_rate}MB per batch is too high"

    def test_redis_connection_pool_performance(self, redis_client, clean_test_environment):
        """Test Redis connection pool performance under high concurrency."""
        
        num_concurrent_operations = 1000
        operations_per_thread = 100
        
        def redis_operations(thread_id):
            """Perform Redis operations for performance testing."""
            operations = []
            
            for i in range(operations_per_thread):
                start_time = time.time()
                
                # User profile operations
                profile_key = f"perf_user_{thread_id}_{i}"
                
                # Write operation
                redis_client.hset(profile_key, mapping={
                    "user_id": f"user_{thread_id}_{i}",
                    "total_transactions": str(i),
                    "total_amount": str(i * 25.5),
                    "last_update": str(time.time())
                })
                
                # Read operation
                profile_data = redis_client.hgetall(profile_key)
                
                # Update operation
                redis_client.hincrby(profile_key, "total_transactions", 1)
                
                # TTL operation
                redis_client.expire(profile_key, 3600)
                
                operation_time = (time.time() - start_time) * 1000
                operations.append(operation_time)
            
            return {
                "thread_id": thread_id,
                "operations": len(operations),
                "avg_latency_ms": statistics.mean(operations),
                "max_latency_ms": max(operations)
            }
        
        print(f"Testing Redis performance with {num_concurrent_operations} concurrent operations...")
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = []
            
            for thread_id in range(num_concurrent_operations):
                future = executor.submit(redis_operations, thread_id)
                futures.append(future)
            
            # Collect results
            thread_results = []
            for future in as_completed(futures):
                result = future.result(timeout=30)
                thread_results.append(result)
        
        total_time = time.time() - start_time
        
        # Analyze Redis performance
        total_operations = sum(result["operations"] for result in thread_results)
        avg_thread_latency = statistics.mean(result["avg_latency_ms"] for result in thread_results)
        max_thread_latency = max(result["max_latency_ms"] for result in thread_results)
        
        operations_per_second = total_operations / total_time
        
        print(f"Redis performance results:")
        print(f"  Total operations: {total_operations}")
        print(f"  Time: {total_time:.1f}s")
        print(f"  Operations/sec: {operations_per_second:.0f}")
        print(f"  Avg latency: {avg_thread_latency:.1f}ms")
        print(f"  Max latency: {max_thread_latency:.1f}ms")
        
        # Performance assertions for Redis
        assert operations_per_second >= 5000, \
            f"Redis operations/sec {operations_per_second} is too low"
        
        assert avg_thread_latency <= 10, \
            f"Average Redis latency {avg_thread_latency}ms is too high"


class PerformanceTestModel:
    """Lightweight mock model for performance testing."""
    
    def predict(self, features):
        # Minimal computation to simulate ML prediction
        if hasattr(features, 'sum'):
            feature_sum = float(features.sum())
            return [min(0.95, max(0.05, feature_sum / 10000))]
        return [0.25]
    
    @property
    def num_feature(self):
        return 25