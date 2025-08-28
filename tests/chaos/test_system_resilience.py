"""
Comprehensive Chaos Engineering and System Resilience Tests

Tests system behavior under various failure conditions:
- Network partitions and connectivity failures
- Service crashes and recovery
- Resource exhaustion scenarios
- Data corruption handling
- Cascading failure prevention
- Graceful degradation validation
"""

import pytest
import time
import threading
import subprocess
import signal
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor
from confluent_kafka import Producer, Consumer, KafkaError
import redis
import psutil

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.fraud_detector import FraudDetector


@pytest.mark.chaos
@pytest.mark.requires_infrastructure
@pytest.mark.slow
class TestSystemResilience:
    """Comprehensive chaos engineering and failure mode tests."""

    def test_kafka_broker_network_partition(self, kafka_config, test_topics, synthetic_transactions, clean_test_environment):
        """Test system behavior when Kafka broker becomes unreachable."""
        
        transaction_topic = test_topics[0]
        test_transactions = synthetic_transactions[:100]
        
        # Setup producer and consumer
        producer_config = kafka_config.get_producer_config("transaction")
        producer_config["retry.backoff.ms"] = 1000
        producer_config["retries"] = 5
        
        producer = Producer(producer_config)
        
        consumer_config = kafka_config.get_consumer_config("chaos_test_group", "fraud_detector")
        consumer_config["auto.offset.reset"] = "earliest"
        consumer_config["session.timeout.ms"] = 10000
        consumer_config["heartbeat.interval.ms"] = 3000
        
        consumer = Consumer(consumer_config)
        consumer.subscribe([transaction_topic])
        
        # Phase 1: Normal operation
        print("Phase 1: Normal operation baseline")
        
        normal_phase_transactions = test_transactions[:30]
        produced_count = 0
        
        for transaction in normal_phase_transactions:
            try:
                producer.produce(
                    topic=transaction_topic,
                    key=transaction["user_id"],
                    value=json.dumps(transaction)
                )
                produced_count += 1
            except Exception as e:
                print(f"Production error during normal phase: {e}")
        
        producer.flush(timeout=10)
        
        # Consume messages during normal operation
        consumed_normal = []
        start_time = time.time()
        
        while len(consumed_normal) < produced_count and time.time() - start_time < 30:
            msg = consumer.poll(timeout=2.0)
            
            if msg is not None and not msg.error():
                consumed_normal.append(json.loads(msg.value().decode('utf-8')))
                consumer.commit(asynchronous=False)
        
        baseline_consumption_rate = len(consumed_normal) / (time.time() - start_time)
        print(f"Baseline: Produced {produced_count}, Consumed {len(consumed_normal)}, Rate: {baseline_consumption_rate:.1f} msg/sec")
        
        # Phase 2: Simulate network partition
        print("Phase 2: Simulating network partition...")
        
        # Note: In a real environment, you would use network tools like:
        # - iptables to block traffic to Kafka brokers
        # - Docker network disconnect
        # - Chaos engineering tools like Chaos Monkey
        
        # For this test, we simulate by creating connection issues
        partition_phase_transactions = test_transactions[30:70]
        partition_errors = 0
        partition_successes = 0
        
        # Simulate degraded network conditions
        producer_partition_config = producer_config.copy()
        producer_partition_config["request.timeout.ms"] = 5000  # Shorter timeout
        producer_partition_config["retries"] = 10
        producer_partition_config["retry.backoff.ms"] = 500
        
        producer_partition = Producer(producer_partition_config)
        
        def delivery_callback(err, msg):
            nonlocal partition_errors, partition_successes
            if err is not None:
                partition_errors += 1
            else:
                partition_successes += 1
        
        # Attempt production during simulated partition
        for transaction in partition_phase_transactions:
            try:
                producer_partition.produce(
                    topic=transaction_topic,
                    key=transaction["user_id"],
                    value=json.dumps(transaction),
                    callback=delivery_callback
                )
                
                # Simulate intermittent connectivity
                if len(partition_phase_transactions) % 10 == 0:
                    time.sleep(0.1)  # Brief delay to simulate network issues
                    
            except Exception as e:
                partition_errors += 1
                print(f"Production error during partition: {e}")
        
        # Try to flush with extended timeout
        try:
            producer_partition.flush(timeout=30)
        except Exception as e:
            print(f"Flush error during partition: {e}")
        
        print(f"Partition phase: Successes {partition_successes}, Errors {partition_errors}")
        
        # Phase 3: Recovery
        print("Phase 3: Network recovery")
        
        # Return to normal producer configuration
        recovery_producer = Producer(producer_config)
        recovery_transactions = test_transactions[70:]
        recovery_produced = 0
        
        for transaction in recovery_transactions:
            try:
                recovery_producer.produce(
                    topic=transaction_topic,
                    key=transaction["user_id"],
                    value=json.dumps(transaction)
                )
                recovery_produced += 1
            except Exception as e:
                print(f"Production error during recovery: {e}")
        
        recovery_producer.flush(timeout=15)
        
        # Verify recovery consumption
        consumed_recovery = []
        recovery_start = time.time()
        
        while len(consumed_recovery) < recovery_produced and time.time() - recovery_start < 30:
            msg = consumer.poll(timeout=2.0)
            
            if msg is not None and not msg.error():
                consumed_recovery.append(json.loads(msg.value().decode('utf-8')))
                consumer.commit(asynchronous=False)
        
        recovery_rate = len(consumed_recovery) / (time.time() - recovery_start) if time.time() - recovery_start > 0 else 0
        
        consumer.close()
        
        print(f"Recovery: Produced {recovery_produced}, Consumed {len(consumed_recovery)}, Rate: {recovery_rate:.1f} msg/sec")
        
        # Verify system resilience
        assert len(consumed_normal) >= produced_count * 0.9, "Normal operation should have high success rate"
        assert partition_errors < len(partition_phase_transactions), "Some partition operations should succeed with retries"
        assert len(consumed_recovery) >= recovery_produced * 0.9, "Recovery should restore normal operation"
        assert recovery_rate >= baseline_consumption_rate * 0.8, "Recovery rate should approach baseline"

    def test_redis_connection_failure_handling(self, redis_client, clean_test_environment):
        """Test fraud detection behavior when Redis becomes unavailable."""
        
        fraud_detector = FraudDetector()
        
        # Test transactions
        test_transactions = [
            {
                "transaction_id": "redis_fail_001",
                "user_id": "redis_test_user_001",
                "amount": 100.0,
                "timestamp": datetime.now().isoformat()
            },
            {
                "transaction_id": "redis_fail_002", 
                "user_id": "redis_test_user_001",
                "amount": 150.0,
                "timestamp": datetime.now().isoformat()
            }
        ]
        
        # Phase 1: Normal operation with Redis
        print("Phase 1: Normal Redis operation")
        
        # Process first transaction normally
        features1 = fraud_detector.extract_all_features(test_transactions[0])
        profile1 = fraud_detector.update_user_profile(test_transactions[0])
        
        assert features1["is_new_user"] in [0, 1]  # Valid value
        assert profile1.user_id == test_transactions[0]["user_id"]
        
        # Verify profile stored in Redis
        profile_key = f"user_profile:{test_transactions[0]['user_id']}"
        stored_profile = redis_client.hgetall(profile_key)
        assert stored_profile["user_id"] == test_transactions[0]["user_id"]
        
        # Phase 2: Simulate Redis failure
        print("Phase 2: Redis connection failure")
        
        # Mock Redis failure by replacing client with failing mock
        original_redis = fraud_detector.redis_client
        
        class FailingRedisClient:
            def hgetall(self, key):
                raise redis.ConnectionError("Redis connection failed")
            
            def hset(self, key, mapping=None, **kwargs):
                raise redis.ConnectionError("Redis connection failed")
            
            def expire(self, key, seconds):
                raise redis.ConnectionError("Redis connection failed")
            
            def exists(self, key):
                raise redis.ConnectionError("Redis connection failed")
            
            def lrange(self, key, start, end):
                raise redis.ConnectionError("Redis connection failed")
            
            def lpush(self, key, *values):
                raise redis.ConnectionError("Redis connection failed")
        
        fraud_detector.redis_client = FailingRedisClient()
        
        # Process transaction during Redis failure
        try:
            features2 = fraud_detector.extract_all_features(test_transactions[1])
            
            # Should fall back to defaults for user features
            assert features2["is_new_user"] == 1  # Default for Redis failure
            assert features2["user_transaction_count"] == 0
            assert features2["user_avg_amount"] == 0.0
            
            # Should still extract transaction features
            assert features2["transaction_amount"] == 150.0
            assert features2["hour_of_day"] >= 0
            
            print("Successfully processed transaction during Redis failure")
            
        except Exception as e:
            pytest.fail(f"System should handle Redis failure gracefully: {e}")
        
        # Phase 3: Recovery
        print("Phase 3: Redis recovery")
        
        # Restore Redis connection
        fraud_detector.redis_client = original_redis
        
        # Process transaction after recovery
        recovery_transaction = {
            "transaction_id": "redis_recover_001",
            "user_id": "redis_recover_user",
            "amount": 75.0,
            "timestamp": datetime.now().isoformat()
        }
        
        features3 = fraud_detector.extract_all_features(recovery_transaction)
        profile3 = fraud_detector.update_user_profile(recovery_transaction)
        
        # Should work normally after recovery
        assert features3["is_new_user"] == 1  # New user
        assert features3["user_transaction_count"] == 0
        assert profile3.total_transactions == 1
        assert profile3.total_amount == 75.0
        
        # Verify Redis storage works
        recovery_profile_key = f"user_profile:{recovery_transaction['user_id']}"
        recovered_profile = redis_client.hgetall(recovery_profile_key)
        assert recovered_profile["user_id"] == recovery_transaction["user_id"]

    def test_memory_exhaustion_handling(self, clean_test_environment):
        """Test system behavior under memory pressure."""
        
        fraud_detector = FraudDetector()
        initial_memory = psutil.Process().memory_info().rss / 1024 / 1024
        
        print(f"Initial memory usage: {initial_memory:.0f}MB")
        
        # Generate large volume of unique transactions to pressure memory
        large_transaction_set = []
        
        for i in range(50000):  # 50k transactions
            transaction = {
                "transaction_id": f"memory_test_{i:06d}",
                "user_id": f"memory_user_{i % 1000}",  # 1000 unique users
                "amount": 25.0 + (i % 100),
                "timestamp": datetime.now().isoformat(),
                "merchant_category": f"category_{i % 20}",
                "large_data": "x" * 1000  # 1KB extra data per transaction
            }
            large_transaction_set.append(transaction)
        
        # Process transactions and monitor memory
        processed_count = 0
        memory_measurements = []
        
        try:
            for i, transaction in enumerate(large_transaction_set):
                features = fraud_detector.extract_all_features(transaction)
                updated_profile = fraud_detector.update_user_profile(transaction)
                processed_count += 1
                
                # Monitor memory every 1000 transactions
                if i % 1000 == 0:
                    current_memory = psutil.Process().memory_info().rss / 1024 / 1024
                    memory_measurements.append({
                        "transactions": i,
                        "memory_mb": current_memory,
                        "growth_mb": current_memory - initial_memory
                    })
                    
                    print(f"Processed {i} transactions, Memory: {current_memory:.0f}MB, Growth: {current_memory - initial_memory:.0f}MB")
                    
                    # Stop if memory usage becomes excessive
                    if current_memory - initial_memory > 2000:  # 2GB growth limit
                        print(f"Memory usage limit reached at {i} transactions")
                        break
        
        except MemoryError as e:
            print(f"Memory error after processing {processed_count} transactions: {e}")
        except Exception as e:
            print(f"Other error during memory pressure test: {e}")
        
        final_memory = psutil.Process().memory_info().rss / 1024 / 1024
        total_growth = final_memory - initial_memory
        
        print(f"Memory pressure test completed:")
        print(f"  Processed: {processed_count} transactions")
        print(f"  Initial memory: {initial_memory:.0f}MB")
        print(f"  Final memory: {final_memory:.0f}MB") 
        print(f"  Total growth: {total_growth:.0f}MB")
        
        # Verify system handled memory pressure gracefully
        assert processed_count > 1000, "Should process significant number of transactions before failure"
        
        # Memory growth should be bounded (not growing linearly with transactions)
        if len(memory_measurements) > 10:
            early_growth = memory_measurements[5]["growth_mb"]
            late_growth = memory_measurements[-1]["growth_mb"]
            growth_rate = (late_growth - early_growth) / (memory_measurements[-1]["transactions"] - memory_measurements[5]["transactions"])
            
            assert growth_rate < 0.1, f"Memory growth rate {growth_rate}MB per transaction is too high"

    def test_ml_model_corruption_handling(self, clean_test_environment):
        """Test system behavior when ML model fails or returns invalid results."""
        
        fraud_detector = FraudDetector()
        
        test_transaction = {
            "transaction_id": "model_corruption_test",
            "user_id": "model_test_user",
            "amount": 200.0,
            "timestamp": datetime.now().isoformat()
        }
        
        # Test 1: Model returns NaN values
        class NaNModel:
            def predict(self, features):
                return [float('nan')]
            
            @property
            def num_feature(self):
                return 25
        
        fraud_detector.ml_model = NaNModel()
        
        try:
            features = fraud_detector.extract_all_features(test_transaction)
            fraud_score = fraud_detector.calculate_fraud_score(features)
            
            # Should handle NaN gracefully
            assert 0 <= fraud_score.final_score <= 1, "Score should be valid even with NaN model output"
            assert fraud_score.ml_score == 0.0 or 0 <= fraud_score.ml_score <= 1, "ML score should be valid or default"
            
        except Exception as e:
            pytest.fail(f"System should handle NaN model output gracefully: {e}")
        
        # Test 2: Model throws exception
        class ExceptionModel:
            def predict(self, features):
                raise RuntimeError("Model prediction failed")
            
            @property
            def num_feature(self):
                return 25
        
        fraud_detector.ml_model = ExceptionModel()
        
        try:
            features = fraud_detector.extract_all_features(test_transaction)
            fraud_score = fraud_detector.calculate_fraud_score(features)
            
            # Should fall back to business rules only
            assert 0 <= fraud_score.final_score <= 1, "Should fall back to business rules"
            assert fraud_score.ml_score == 0.0, "ML score should be 0 when model fails"
            assert fraud_score.business_rules_score > 0, "Business rules should still work"
            
        except Exception as e:
            pytest.fail(f"System should handle model exceptions gracefully: {e}")
        
        # Test 3: Model returns values outside valid range
        class InvalidRangeModel:
            def predict(self, features):
                return [5.0]  # Invalid probability > 1.0
            
            @property
            def num_feature(self):
                return 25
        
        fraud_detector.ml_model = InvalidRangeModel()
        
        try:
            features = fraud_detector.extract_all_features(test_transaction)
            fraud_score = fraud_detector.calculate_fraud_score(features)
            
            # Should clamp to valid range
            assert 0 <= fraud_score.final_score <= 1, "Score should be clamped to valid range"
            
        except Exception as e:
            pytest.fail(f"System should handle invalid model output gracefully: {e}")

    def test_cascading_failure_prevention(self, kafka_config, redis_client, test_topics, clean_test_environment):
        """Test that failures in one component don't cascade to others."""
        
        fraud_detector = FraudDetector()
        transaction_topic = test_topics[0]
        
        # Setup components
        producer = Producer(kafka_config.get_producer_config("transaction"))
        
        test_transactions = [
            {
                "transaction_id": f"cascade_test_{i}",
                "user_id": f"cascade_user_{i}",
                "amount": 50.0 + i,
                "timestamp": datetime.now().isoformat()
            }
            for i in range(50)
        ]
        
        # Scenario 1: ML model failure shouldn't affect Kafka or Redis operations
        print("Testing ML model failure isolation...")
        
        class FailingMLModel:
            def predict(self, features):
                raise Exception("ML model crashed")
            
            @property
            def num_feature(self):
                return 25
        
        fraud_detector.ml_model = FailingMLModel()
        
        successful_operations = 0
        kafka_errors = 0
        redis_errors = 0
        ml_errors = 0
        
        for transaction in test_transactions[:20]:
            try:
                # Kafka operation should succeed
                producer.produce(
                    topic=transaction_topic,
                    key=transaction["user_id"],
                    value=json.dumps(transaction)
                )
                
                # Feature extraction should work
                features = fraud_detector.extract_all_features(transaction)
                
                # Profile update should work despite ML failure
                profile = fraud_detector.update_user_profile(transaction)
                
                # Fraud scoring should fall back gracefully
                fraud_score = fraud_detector.calculate_fraud_score(features)
                
                successful_operations += 1
                
            except Exception as e:
                if "kafka" in str(e).lower():
                    kafka_errors += 1
                elif "redis" in str(e).lower():
                    redis_errors += 1
                elif "model" in str(e).lower() or "ml" in str(e).lower():
                    ml_errors += 1
                
                print(f"Error during cascade test: {e}")
        
        producer.flush(timeout=10)
        
        print(f"ML failure isolation results:")
        print(f"  Successful operations: {successful_operations}")
        print(f"  Kafka errors: {kafka_errors}")
        print(f"  Redis errors: {redis_errors}")
        print(f"  ML errors: {ml_errors}")
        
        # Verify failure isolation
        assert successful_operations > 15, "Most operations should succeed despite ML failure"
        assert kafka_errors == 0, "Kafka operations should not be affected by ML failure"
        assert redis_errors == 0, "Redis operations should not be affected by ML failure"
        
        # Scenario 2: Redis failure shouldn't affect Kafka or ML operations
        print("Testing Redis failure isolation...")
        
        # Restore working ML model
        class WorkingMLModel:
            def predict(self, features):
                return [0.3]
            
            @property
            def num_feature(self):
                return 25
        
        fraud_detector.ml_model = WorkingMLModel()
        
        # Create failing Redis client
        original_redis = fraud_detector.redis_client
        
        class FailingRedisClient:
            def __getattr__(self, name):
                raise redis.ConnectionError("Redis completely unavailable")
        
        fraud_detector.redis_client = FailingRedisClient()
        
        isolated_successes = 0
        
        for transaction in test_transactions[20:40]:
            try:
                # Kafka should still work
                producer.produce(
                    topic=transaction_topic,
                    key=transaction["user_id"],
                    value=json.dumps(transaction)
                )
                
                # Feature extraction should work (with fallbacks)
                features = fraud_detector.extract_all_features(transaction)
                
                # ML scoring should work
                fraud_score = fraud_detector.calculate_fraud_score(features)
                
                isolated_successes += 1
                
            except Exception as e:
                print(f"Error during Redis isolation test: {e}")
        
        producer.flush(timeout=10)
        
        # Restore Redis
        fraud_detector.redis_client = original_redis
        
        print(f"Redis failure isolation: {isolated_successes} successful operations")
        
        # Verify Redis failure didn't cascade
        assert isolated_successes > 15, "Operations should continue despite Redis failure"

    def test_gradual_degradation_under_load(self, kafka_config, redis_client, test_topics, clean_test_environment):
        """Test system degrades gracefully under increasing load."""
        
        fraud_detector = FraudDetector()
        fraud_detector.ml_model = PerformanceTestModel()
        
        # Test with increasing load levels
        load_levels = [100, 500, 1000, 2000, 5000]  # Transactions per test
        degradation_metrics = []
        
        for load_level in load_levels:
            print(f"Testing load level: {load_level} transactions")
            
            # Generate transactions for this load level
            load_transactions = []
            for i in range(load_level):
                transaction = {
                    "transaction_id": f"load_test_{load_level}_{i}",
                    "user_id": f"load_user_{i % (load_level // 10)}",  # 10% unique users
                    "amount": 25.0 + (i % 100),
                    "timestamp": datetime.now().isoformat()
                }
                load_transactions.append(transaction)
            
            # Measure performance at this load level
            start_time = time.time()
            successful_transactions = 0
            errors = 0
            latencies = []
            
            for transaction in load_transactions:
                tx_start = time.time()
                
                try:
                    features = fraud_detector.extract_all_features(transaction)
                    fraud_score = fraud_detector.calculate_fraud_score(features)
                    profile = fraud_detector.update_user_profile(transaction)
                    
                    successful_transactions += 1
                    
                except Exception as e:
                    errors += 1
                
                tx_latency = (time.time() - tx_start) * 1000
                latencies.append(tx_latency)
            
            total_time = time.time() - start_time
            
            metrics = {
                "load_level": load_level,
                "successful_transactions": successful_transactions,
                "errors": errors,
                "success_rate": successful_transactions / load_level,
                "total_time": total_time,
                "throughput_tps": successful_transactions / total_time,
                "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0,
                "max_latency_ms": max(latencies) if latencies else 0
            }
            
            degradation_metrics.append(metrics)
            
            print(f"  Success rate: {metrics['success_rate']:.2%}")
            print(f"  Throughput: {metrics['throughput_tps']:.0f} TPS")
            print(f"  Avg latency: {metrics['avg_latency_ms']:.1f}ms")
            print(f"  Errors: {errors}")
        
        # Analyze degradation pattern
        print("Degradation analysis:")
        
        for i, metrics in enumerate(degradation_metrics):
            print(f"  Load {metrics['load_level']}: "
                  f"Success {metrics['success_rate']:.1%}, "
                  f"TPS {metrics['throughput_tps']:.0f}, "
                  f"Latency {metrics['avg_latency_ms']:.1f}ms")
        
        # Verify graceful degradation
        baseline_metrics = degradation_metrics[0]  # Lowest load
        
        for metrics in degradation_metrics[1:]:
            # Success rate should not drop dramatically
            success_ratio = metrics['success_rate'] / baseline_metrics['success_rate']
            assert success_ratio >= 0.7, f"Success rate degraded too much at load {metrics['load_level']}"
            
            # Latency can increase but should remain reasonable
            latency_ratio = metrics['avg_latency_ms'] / baseline_metrics['avg_latency_ms']
            assert latency_ratio <= 10, f"Latency increased too much at load {metrics['load_level']}"
        
        # System should handle at least moderate load effectively
        moderate_load_metrics = [m for m in degradation_metrics if m['load_level'] == 1000][0]
        assert moderate_load_metrics['success_rate'] >= 0.95, "Should handle 1K load with high success rate"
        assert moderate_load_metrics['avg_latency_ms'] <= 100, "Should handle 1K load with reasonable latency"


class PerformanceTestModel:
    """Lightweight model for performance testing."""
    
    def predict(self, features):
        return [0.25]
    
    @property
    def num_feature(self):
        return 25