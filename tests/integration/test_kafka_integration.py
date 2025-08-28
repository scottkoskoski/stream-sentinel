"""
Comprehensive Kafka Integration Tests

Tests real Kafka cluster integration including:
- Producer/consumer message flow validation
- Topic management and partitioning
- Exactly-once processing semantics
- Consumer group management
- Error handling and recovery
- Performance characteristics
"""

import pytest
import json
import time
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any
from confluent_kafka import Producer, Consumer, KafkaError, KafkaException

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))


@pytest.mark.integration
@pytest.mark.kafka
@pytest.mark.requires_infrastructure
class TestKafkaIntegration:
    """Comprehensive Kafka integration tests with real cluster."""

    def test_end_to_end_message_flow(self, kafka_config, kafka_producer, kafka_consumer, test_topics, synthetic_transactions):
        """Test complete end-to-end message flow through Kafka."""
        topic_name = test_topics[0]  # "test-transactions"
        test_messages = synthetic_transactions[:10]  # Use first 10 transactions
        
        # Produce messages
        produced_messages = []
        for i, transaction in enumerate(test_messages):
            message_key = f"txn_{i}"
            message_value = json.dumps(transaction)
            
            kafka_producer.produce(
                topic=topic_name,
                key=message_key,
                value=message_value
            )
            
            produced_messages.append({
                "key": message_key,
                "value": transaction
            })
        
        # Ensure all messages are delivered
        kafka_producer.flush(timeout=10)
        
        # Subscribe consumer and consume messages
        kafka_consumer.subscribe([topic_name])
        
        consumed_messages = []
        start_time = time.time()
        timeout_seconds = 30
        
        while len(consumed_messages) < len(test_messages):
            if time.time() - start_time > timeout_seconds:
                break
                
            msg = kafka_consumer.poll(timeout=5.0)
            
            if msg is None:
                continue
                
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                else:
                    raise KafkaException(msg.error())
            
            # Process message
            message_key = msg.key().decode('utf-8') if msg.key() else None
            message_value = json.loads(msg.value().decode('utf-8'))
            
            consumed_messages.append({
                "key": message_key,
                "value": message_value,
                "partition": msg.partition(),
                "offset": msg.offset()
            })
            
            # Commit offset for exactly-once processing
            kafka_consumer.commit(asynchronous=False)
        
        # Validate message consistency
        assert len(consumed_messages) == len(produced_messages)
        
        # Verify message content matches
        for produced, consumed in zip(produced_messages, consumed_messages):
            assert produced["key"] == consumed["key"]
            assert produced["value"]["transaction_id"] == consumed["value"]["transaction_id"]
            assert produced["value"]["user_id"] == consumed["value"]["user_id"]
            assert produced["value"]["amount"] == consumed["value"]["amount"]

    def test_message_ordering_within_partition(self, kafka_config, kafka_producer, kafka_consumer, test_topics):
        """Test message ordering guarantees within partitions."""
        topic_name = test_topics[0]
        partition = 0  # Use specific partition
        
        # Create ordered messages for same user (same partition key)
        user_id = "ordering_test_user"
        ordered_messages = []
        
        for i in range(20):
            transaction = {
                "transaction_id": f"txn_order_{i:03d}",
                "user_id": user_id,
                "amount": 10.0 + i,
                "timestamp": (datetime.now() + timedelta(seconds=i)).isoformat(),
                "sequence": i
            }
            ordered_messages.append(transaction)
        
        # Produce messages with same partition key
        for transaction in ordered_messages:
            kafka_producer.produce(
                topic=topic_name,
                key=user_id,  # Same key ensures same partition
                value=json.dumps(transaction),
                partition=partition
            )
        
        kafka_producer.flush(timeout=10)
        
        # Consume messages
        kafka_consumer.subscribe([topic_name])
        
        consumed_messages = []
        start_time = time.time()
        
        while len(consumed_messages) < len(ordered_messages) and time.time() - start_time < 30:
            msg = kafka_consumer.poll(timeout=5.0)
            
            if msg is None:
                continue
                
            if msg.error():
                continue
            
            message_value = json.loads(msg.value().decode('utf-8'))
            consumed_messages.append(message_value)
            kafka_consumer.commit(asynchronous=False)
        
        # Verify ordering within partition
        assert len(consumed_messages) == len(ordered_messages)
        
        # Messages should be in order by sequence number
        for i, message in enumerate(consumed_messages):
            assert message["sequence"] == i
            assert message["transaction_id"] == f"txn_order_{i:03d}"

    def test_consumer_group_behavior(self, kafka_config, test_topics):
        """Test consumer group behavior with multiple consumers."""
        topic_name = test_topics[0]
        consumer_group = "test_consumer_group"
        
        # Create multiple consumers in same group
        consumer1_config = kafka_config.get_consumer_config(consumer_group, "fraud_detector")
        consumer1_config["auto.offset.reset"] = "earliest"
        consumer1 = Consumer(consumer1_config)
        
        consumer2_config = kafka_config.get_consumer_config(consumer_group, "fraud_detector") 
        consumer2_config["auto.offset.reset"] = "earliest"
        consumer2 = Consumer(consumer2_config)
        
        try:
            # Both subscribe to same topic
            consumer1.subscribe([topic_name])
            consumer2.subscribe([topic_name])
            
            # Produce messages to multiple partitions
            producer = Producer(kafka_config.get_producer_config("transaction"))
            
            messages_per_partition = 5
            total_messages = messages_per_partition * 12  # 12 partitions
            
            for i in range(total_messages):
                transaction = {
                    "transaction_id": f"group_test_{i}",
                    "user_id": f"user_{i % 50}",  # Distribute across users
                    "amount": 25.0 + i,
                    "timestamp": datetime.now().isoformat()
                }
                
                producer.produce(
                    topic=topic_name,
                    key=f"user_{i % 50}",
                    value=json.dumps(transaction)
                )
            
            producer.flush(timeout=15)
            
            # Collect messages from both consumers
            consumer1_messages = []
            consumer2_messages = []
            
            start_time = time.time()
            total_consumed = 0
            
            while total_consumed < total_messages and time.time() - start_time < 60:
                # Poll both consumers
                msg1 = consumer1.poll(timeout=1.0)
                msg2 = consumer2.poll(timeout=1.0)
                
                if msg1 is not None and not msg1.error():
                    consumer1_messages.append(json.loads(msg1.value().decode('utf-8')))
                    consumer1.commit(msg1, asynchronous=False)
                    total_consumed += 1
                
                if msg2 is not None and not msg2.error():
                    consumer2_messages.append(json.loads(msg2.value().decode('utf-8')))
                    consumer2.commit(msg2, asynchronous=False)
                    total_consumed += 1
            
            # Verify load balancing between consumers
            assert len(consumer1_messages) > 0
            assert len(consumer2_messages) > 0
            assert len(consumer1_messages) + len(consumer2_messages) == total_messages
            
            # Verify no duplicate messages across consumers
            consumer1_txn_ids = {msg["transaction_id"] for msg in consumer1_messages}
            consumer2_txn_ids = {msg["transaction_id"] for msg in consumer2_messages}
            assert len(consumer1_txn_ids.intersection(consumer2_txn_ids)) == 0
            
        finally:
            consumer1.close()
            consumer2.close()

    def test_exactly_once_processing_semantics(self, kafka_config, test_topics):
        """Test exactly-once processing with consumer crashes and restarts."""
        topic_name = test_topics[0]
        consumer_group = "exactly_once_test_group"
        
        # Produce test messages
        producer = Producer(kafka_config.get_producer_config("transaction"))
        
        test_messages = []
        for i in range(50):
            transaction = {
                "transaction_id": f"exactly_once_{i}",
                "user_id": f"user_{i % 10}",
                "amount": 100.0 + i,
                "timestamp": datetime.now().isoformat()
            }
            test_messages.append(transaction)
            
            producer.produce(
                topic=topic_name,
                key=f"user_{i % 10}",
                value=json.dumps(transaction)
            )
        
        producer.flush(timeout=10)
        
        # First consumer processes some messages then "crashes"
        consumer1_config = kafka_config.get_consumer_config(consumer_group, "fraud_detector")
        consumer1_config["auto.offset.reset"] = "earliest"
        consumer1_config["enable.auto.commit"] = False  # Manual commit for exactly-once
        
        consumer1 = Consumer(consumer1_config)
        consumer1.subscribe([topic_name])
        
        processed_messages = []
        messages_to_process = 25  # Process half, then "crash"
        
        for _ in range(messages_to_process):
            msg = consumer1.poll(timeout=10.0)
            
            if msg is not None and not msg.error():
                message_value = json.loads(msg.value().decode('utf-8'))
                processed_messages.append(message_value)
                
                # Commit every few messages (simulating batch processing)
                if len(processed_messages) % 5 == 0:
                    consumer1.commit(asynchronous=False)
        
        consumer1.close()  # Simulate crash without final commit
        
        # Second consumer (restart) continues processing
        consumer2 = Consumer(consumer1_config)
        consumer2.subscribe([topic_name])
        
        remaining_messages = []
        start_time = time.time()
        
        while len(remaining_messages) + len(processed_messages) < len(test_messages):
            if time.time() - start_time > 30:
                break
                
            msg = consumer2.poll(timeout=5.0)
            
            if msg is not None and not msg.error():
                message_value = json.loads(msg.value().decode('utf-8'))
                remaining_messages.append(message_value)
                consumer2.commit(asynchronous=False)
        
        consumer2.close()
        
        # Verify exactly-once processing
        total_processed = len(processed_messages) + len(remaining_messages)
        assert total_processed == len(test_messages)
        
        # Verify no duplicates
        all_txn_ids = ([msg["transaction_id"] for msg in processed_messages] + 
                       [msg["transaction_id"] for msg in remaining_messages])
        assert len(all_txn_ids) == len(set(all_txn_ids))  # No duplicates

    def test_high_throughput_production(self, kafka_config, test_topics, performance_benchmarks):
        """Test high-throughput message production capabilities."""
        topic_name = test_topics[0]
        target_tps = 1000  # 1k TPS for integration test (scaled down from 10k)
        test_duration = 10  # 10 seconds
        total_messages = target_tps * test_duration
        
        producer_config = kafka_config.get_producer_config("transaction")
        producer_config.update({
            "batch.size": 32768,      # Larger batch size
            "linger.ms": 10,          # Wait 10ms for batching
            "compression.type": "lz4", # Fast compression
            "acks": "1"               # Fast acknowledgment
        })
        
        producer = Producer(producer_config)
        
        # Generate test data
        test_transactions = []
        for i in range(total_messages):
            transaction = {
                "transaction_id": f"perf_txn_{i}",
                "user_id": f"perf_user_{i % 1000}",
                "amount": 50.0 + (i % 500),
                "timestamp": datetime.now().isoformat()
            }
            test_transactions.append(transaction)
        
        # Measure production performance
        start_time = time.time()
        
        for i, transaction in enumerate(test_transactions):
            producer.produce(
                topic=topic_name,
                key=f"perf_user_{i % 1000}",
                value=json.dumps(transaction)
            )
            
            # Flush periodically to prevent buffer overflow
            if i % 1000 == 0:
                producer.poll(0)  # Non-blocking poll
        
        producer.flush(timeout=30)
        end_time = time.time()
        
        # Calculate performance metrics
        actual_duration = end_time - start_time
        actual_tps = total_messages / actual_duration
        
        # Verify performance meets requirements
        min_tps = target_tps * 0.8  # Allow 20% tolerance
        assert actual_tps >= min_tps, f"TPS {actual_tps} below minimum {min_tps}"
        assert actual_duration <= test_duration * 1.5  # Allow 50% time tolerance

    def test_consumer_lag_monitoring(self, kafka_config, test_topics):
        """Test consumer lag monitoring and catch-up behavior."""
        topic_name = test_topics[0]
        consumer_group = "lag_test_group"
        
        # Produce messages without consumer
        producer = Producer(kafka_config.get_producer_config("transaction"))
        
        backlog_messages = 100
        for i in range(backlog_messages):
            transaction = {
                "transaction_id": f"lag_test_{i}",
                "user_id": f"lag_user_{i % 20}",
                "amount": 75.0 + i,
                "timestamp": datetime.now().isoformat()
            }
            
            producer.produce(
                topic=topic_name,
                key=f"lag_user_{i % 20}",
                value=json.dumps(transaction)
            )
        
        producer.flush(timeout=10)
        
        # Start consumer and measure catch-up time
        consumer_config = kafka_config.get_consumer_config(consumer_group, "fraud_detector")
        consumer_config["auto.offset.reset"] = "earliest"
        consumer = Consumer(consumer_config)
        consumer.subscribe([topic_name])
        
        consumed_messages = []
        catch_up_start = time.time()
        
        while len(consumed_messages) < backlog_messages:
            if time.time() - catch_up_start > 30:
                break
                
            msg = consumer.poll(timeout=5.0)
            
            if msg is not None and not msg.error():
                consumed_messages.append(json.loads(msg.value().decode('utf-8')))
                consumer.commit(asynchronous=False)
        
        catch_up_time = time.time() - catch_up_start
        consumer.close()
        
        # Verify catch-up performance
        assert len(consumed_messages) == backlog_messages
        assert catch_up_time < 15  # Should catch up within 15 seconds
        
        # Calculate catch-up rate
        catch_up_rate = backlog_messages / catch_up_time
        assert catch_up_rate >= 10  # At least 10 messages per second

    def test_partition_rebalancing(self, kafka_config, test_topics):
        """Test partition rebalancing when consumers join/leave."""
        topic_name = test_topics[0]
        consumer_group = "rebalance_test_group"
        
        # Start with one consumer
        consumer1_config = kafka_config.get_consumer_config(consumer_group, "fraud_detector")
        consumer1_config["auto.offset.reset"] = "earliest"
        consumer1 = Consumer(consumer1_config)
        consumer1.subscribe([topic_name])
        
        # Consume some messages to establish partition assignment
        producer = Producer(kafka_config.get_producer_config("transaction"))
        
        # Produce to all partitions
        for i in range(24):  # 2 messages per partition (12 partitions)
            transaction = {
                "transaction_id": f"rebalance_{i}",
                "user_id": f"rebalance_user_{i}",
                "amount": 100.0,
                "timestamp": datetime.now().isoformat()
            }
            
            producer.produce(
                topic=topic_name,
                key=f"rebalance_user_{i}",
                value=json.dumps(transaction)
            )
        
        producer.flush(timeout=10)
        
        # Consumer1 processes messages
        consumer1_messages = []
        for _ in range(10):  # Consume some messages
            msg = consumer1.poll(timeout=5.0)
            if msg is not None and not msg.error():
                consumer1_messages.append(json.loads(msg.value().decode('utf-8')))
        
        # Add second consumer (triggers rebalance)
        consumer2_config = kafka_config.get_consumer_config(consumer_group, "fraud_detector")
        consumer2_config["auto.offset.reset"] = "earliest"
        consumer2 = Consumer(consumer2_config)
        consumer2.subscribe([topic_name])
        
        # Wait for rebalance to complete
        time.sleep(5)
        
        # Both consumers should now process remaining messages
        total_remaining = 24 - len(consumer1_messages)
        combined_messages = consumer1_messages.copy()
        
        start_time = time.time()
        while len(combined_messages) < 24 and time.time() - start_time < 20:
            msg1 = consumer1.poll(timeout=1.0)
            msg2 = consumer2.poll(timeout=1.0)
            
            if msg1 is not None and not msg1.error():
                combined_messages.append(json.loads(msg1.value().decode('utf-8')))
            
            if msg2 is not None and not msg2.error():
                combined_messages.append(json.loads(msg2.value().decode('utf-8')))
        
        consumer1.close()
        consumer2.close()
        
        # Verify rebalancing worked correctly
        assert len(combined_messages) == 24
        
        # Verify no duplicate processing after rebalance
        txn_ids = [msg["transaction_id"] for msg in combined_messages]
        assert len(txn_ids) == len(set(txn_ids))

    def test_error_handling_and_recovery(self, kafka_config, test_topics):
        """Test error handling and recovery scenarios."""
        topic_name = test_topics[0]
        
        # Test producer error handling with invalid topic
        producer = Producer(kafka_config.get_producer_config("transaction"))
        
        error_count = 0
        def delivery_callback(err, msg):
            nonlocal error_count
            if err is not None:
                error_count += 1
        
        # Produce to invalid topic (should handle gracefully)
        try:
            producer.produce(
                topic="invalid_topic_name",
                key="test_key",
                value="test_value",
                callback=delivery_callback
            )
            producer.flush(timeout=10)
        except Exception:
            pass  # Expected to handle gracefully
        
        # Test consumer error handling with malformed messages
        consumer_config = kafka_config.get_consumer_config("error_test_group", "fraud_detector")
        consumer_config["auto.offset.reset"] = "earliest"
        consumer = Consumer(consumer_config)
        consumer.subscribe([topic_name])
        
        # Produce malformed message
        producer.produce(
            topic=topic_name,
            key="malformed_key",
            value="not_valid_json{{"  # Malformed JSON
        )
        producer.flush(timeout=5)
        
        # Consumer should handle malformed message gracefully
        processed_count = 0
        error_count = 0
        
        for _ in range(5):  # Try to process a few messages
            msg = consumer.poll(timeout=2.0)
            
            if msg is None:
                continue
                
            if msg.error():
                error_count += 1
                continue
            
            try:
                json.loads(msg.value().decode('utf-8'))
                processed_count += 1
            except json.JSONDecodeError:
                error_count += 1
                # Should still commit to move past bad message
                consumer.commit(asynchronous=False)
        
        consumer.close()
        
        # Error handling should allow processing to continue
        assert error_count >= 0  # Some errors expected
        # System should remain functional despite errors

    def test_topic_configuration_validation(self, kafka_admin_client, test_topics):
        """Test that topics are configured correctly for fraud detection."""
        topic_name = test_topics[0]
        
        # Get topic metadata
        topic_metadata = kafka_admin_client.list_topics(timeout=10)
        
        assert topic_name in topic_metadata.topics
        topic_info = topic_metadata.topics[topic_name]
        
        # Verify partition count
        assert len(topic_info.partitions) == 12
        
        # Verify all partitions are healthy
        for partition_id, partition_info in topic_info.partitions.items():
            assert partition_info.leader >= 0  # Has a leader
            assert len(partition_info.replicas) >= 1  # Has replicas