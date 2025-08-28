# /stream-sentinel/src/kafka/test_connectivity.py

"""
Kafka Connectivity Test Script for Stream-Sentinel

This script validates the complete Kafka infrastructure by:
1. Creating test topics with proper configurations
2. Producing test messages to demonstrate data ingestion
3. Consuming messages to verify end-to-end data flow
4. Cleaning up test resources

This serves as both validation and a reference implementation
for the producer/consumer patterns used in the fraud detection system.
"""

import json
import time
import sys
from typing import List, Dict, Any
from confluent_kafka import Producer, Consumer, KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic, ConfigResource, ResourceType

# Import our configuration module
from config import get_kafka_config


class KafkaConnectivityTester:
    """
    Comprehensive Kafka connectivity testing.

    Demonstrates all the key operations we'll use in the fraud detection system:
    - Administrative operations (topic creation/deletion)
    - High-performance message production
    - Reliable message consumption with proper offset management
    - Error handling and recovery patterns
    """

    def __init__(self):
        """Initialize the connectivity tester with our configuration."""
        self.config = get_kafka_config()
        self.logger = self.config.logger

        # Test topic name
        self.test_topic = "stream-sentinel-connectivity-test"

        # Create admin client for topic management
        admin_config = {"bootstrap.servers": self.config.bootstrap_servers}
        self.admin_client = AdminClient(admin_config)

        # Test messages to send
        self.test_messages = [
            {
                "message_id": 1,
                "type": "transaction",
                "amount": 250.75,
                "timestamp": time.time(),
                "status": "test",
            },
            {
                "message_id": 2,
                "type": "market_data",
                "symbol": "BTC",
                "price": 43250.50,
                "timestamp": time.time(),
                "status": "test",
            },
            {
                "message_id": 3,
                "type": "sentiment",
                "text": "Market sentiment appears bullish",
                "score": 0.8,
                "timestamp": time.time(),
                "status": "test",
            },
        ]

    def test_admin_operations(self) -> bool:
        """
        Test administrative operations: topic creation and validation.

        Returns:
            bool: True if admin operations successful
        """
        self.logger.info("Testing Kafka administrative operations...")

        try:
            # Check if test topic already exists
            existing_topics = self.admin_client.list_topics(timeout=10)
            if self.test_topic in existing_topics.topics:
                self.logger.info(
                    f"Topic '{self.test_topic}' already exists, deleting first..."
                )
                self.cleanup_test_topic()
                time.sleep(2)  # Wait for deletion to complete

            # Create test topic with fraud detection optimizations
            topic_config = self.config.get_topic_config("transactions")
            new_topic = NewTopic(
                topic=self.test_topic,
                num_partitions=topic_config["num_partitions"],
                replication_factor=topic_config["replication_factor"],
                config={
                    "cleanup.policy": topic_config["cleanup_policy"],
                    "retention.ms": str(300000),  # 5 minutes for test
                    "segment.ms": str(60000),  # 1 minute segments for test
                },
            )

            # Create the topic
            creation_result = self.admin_client.create_topics([new_topic])

            # Wait for creation to complete
            for topic_name, future in creation_result.items():
                try:
                    future.result(timeout=10)
                    self.logger.info(f"Topic '{topic_name}' created successfully")
                except Exception as e:
                    self.logger.error(f"Failed to create topic '{topic_name}': {e}")
                    return False

            # Validate topic was created with correct configuration
            topic_metadata = self.admin_client.list_topics(timeout=10)
            if self.test_topic not in topic_metadata.topics:
                self.logger.error(
                    f"Topic '{self.test_topic}' not found after creation"
                )
                return False

            topic_info = topic_metadata.topics[self.test_topic]
            self.logger.info(
                f"Topic validation: {len(topic_info.partitions)} partitions created"
            )

            return True

        except Exception as e:
            self.logger.error(f"Admin operations failed: {e}")
            return False

    def test_producer_operations(self) -> bool:
        """
        Test message production with different producer types.

        Returns:
            bool: True if all messages produced successfully
        """
        self.logger.info("Testing Kafka producer operations...")

        try:
            # Test different producer configurations
            producer_types = ["transaction", "market_data", "sentiment"]

            for i, producer_type in enumerate(producer_types):
                # Get optimized configuration for this producer type
                producer_config = self.config.get_producer_config(producer_type)
                producer = Producer(producer_config)

                # Prepare test message
                message = self.test_messages[i]
                message_key = f"{producer_type}_{message['message_id']}"
                message_value = json.dumps(message)

                self.logger.debug(f"Producing {producer_type} message: {message_key}")

                # Produce message with callback
                def delivery_callback(err, msg):
                    if err is not None:
                        self.logger.error(f"Message delivery failed: {err}")
                    else:
                        self.logger.debug(
                            f"Message delivered to {msg.topic()} [{msg.partition()}] @ offset {msg.offset()}"
                        )

                # Send the message
                producer.produce(
                    topic=self.test_topic,
                    key=message_key,
                    value=message_value,
                    callback=delivery_callback,
                )

                # Wait for delivery confirmation
                producer.flush(timeout=10)

                self.logger.info(f"{producer_type} producer test completed")

            self.logger.info(
                f"All {len(self.test_messages)} test messages produced successfully"
            )
            return True

        except Exception as e:
            self.logger.error(f"Producer operations failed: {e}")
            return False

    def test_consumer_operations(self) -> bool:
        """
        Test message consumption with proper offset management.

        Returns:
            bool: True if all messages consumed successfully
        """
        self.logger.info("Testing Kafka consumer operations...")

        try:
            # Get fraud detector consumer configuration (real-time processing)
            consumer_config = self.config.get_consumer_config(
                consumer_group="test-connectivity-group", consumer_type="fraud_detector"
            )

            # Override to read from beginning for test
            consumer_config["auto.offset.reset"] = "earliest"

            consumer = Consumer(consumer_config)

            # Subscribe to test topic
            consumer.subscribe([self.test_topic])

            consumed_messages = []
            max_messages = len(self.test_messages)
            timeout_seconds = 30
            start_time = time.time()

            self.logger.info(f"Consuming up to {max_messages} messages...")

            while len(consumed_messages) < max_messages:
                # Check timeout
                if time.time() - start_time > timeout_seconds:
                    self.logger.warning(f"Consumer timeout after {timeout_seconds}s")
                    break

                # Poll for messages
                msg = consumer.poll(timeout=5.0)

                if msg is None:
                    self.logger.debug("No message received, continuing...")
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        self.logger.debug("Reached end of partition")
                        continue
                    else:
                        self.logger.error(f"Consumer error: {msg.error()}")
                        break

                # Process the message
                try:
                    message_key = msg.key().decode("utf-8") if msg.key() else None
                    message_value = json.loads(msg.value().decode("utf-8"))

                    self.logger.debug(
                        f"Consumed message: {message_key} -> {message_value}"
                    )

                    consumed_messages.append(
                        {
                            "key": message_key,
                            "value": message_value,
                            "partition": msg.partition(),
                            "offset": msg.offset(),
                        }
                    )

                    # Manually commit offset for exactly-once processing
                    consumer.commit(asynchronous=False)

                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to decode message: {e}")
                    continue

            # Close consumer
            consumer.close()

            # Validate results
            if len(consumed_messages) == max_messages:
                self.logger.info(
                    f"Successfully consumed all {len(consumed_messages)} test messages"
                )

                # Verify message content
                for consumed in consumed_messages:
                    original_msg_id = consumed["value"]["message_id"]
                    original_msg = self.test_messages[
                        original_msg_id - 1
                    ]  # Adjust for 0-based indexing

                    if consumed["value"]["type"] == original_msg["type"]:
                        self.logger.debug(
                            f"Message {original_msg_id} content validated"
                        )
                    else:
                        self.logger.error(
                            f"Message {original_msg_id} content mismatch"
                        )
                        return False

                return True
            else:
                self.logger.error(
                    f"Only consumed {len(consumed_messages)} out of {max_messages} messages"
                )
                return False

        except Exception as e:
            self.logger.error(f"Consumer operations failed: {e}")
            return False

    def cleanup_test_topic(self) -> bool:
        """
        Clean up test resources by deleting the test topic.

        Returns:
            bool: True if cleanup successful
        """
        self.logger.info("Cleaning up test resources...")

        try:
            deletion_result = self.admin_client.delete_topics([self.test_topic])

            # Wait for deletion to complete
            for topic_name, future in deletion_result.items():
                try:
                    future.result(timeout=10)
                    self.logger.info(f"Topic '{topic_name}' deleted successfully")
                except Exception as e:
                    self.logger.warning(f"Topic deletion result: {e}")

            return True

        except Exception as e:
            self.logger.error(f"Cleanup failed: {e}")
            return False

    def run_full_test(self) -> bool:
        """
        Run the complete connectivity test suite.

        Returns:
            bool: True if all tests pass
        """
        self.logger.info("=" * 60)
        self.logger.info("Starting Stream-Sentinel Kafka Connectivity Test")
        self.logger.info("=" * 60)

        test_results = []

        # Test 1: Administrative operations
        self.logger.info("\nTest 1: Administrative Operations")
        admin_result = self.test_admin_operations()
        test_results.append(("Admin Operations", admin_result))

        if not admin_result:
            self.logger.error("Admin operations failed, stopping tests")
            return False

        # Test 2: Producer operations
        self.logger.info("\n📤 Test 2: Producer Operations")
        producer_result = self.test_producer_operations()
        test_results.append(("Producer Operations", producer_result))

        if not producer_result:
            self.logger.error("Producer operations failed, stopping tests")
            self.cleanup_test_topic()
            return False

        # Small delay to ensure messages are available
        time.sleep(2)

        # Test 3: Consumer operations
        self.logger.info("\n📥 Test 3: Consumer Operations")
        consumer_result = self.test_consumer_operations()
        test_results.append(("Consumer Operations", consumer_result))

        # Test 4: Cleanup
        self.logger.info("\n🧹 Test 4: Cleanup")
        cleanup_result = self.cleanup_test_topic()
        test_results.append(("Cleanup", cleanup_result))

        # Print test summary
        self.logger.info("\n" + "=" * 60)
        self.logger.info("TEST SUMMARY")
        self.logger.info("=" * 60)

        all_passed = True
        for test_name, result in test_results:
            status = "PASSED" if result else "FAILED"
            self.logger.info(f"{test_name:<20}: {status}")
            if not result:
                all_passed = False

        if all_passed:
            self.logger.info("\n🎉 All Kafka connectivity tests PASSED!")
            self.logger.info(
                "Stream-Sentinel is ready for fraud detection pipeline development."
            )
        else:
            self.logger.error("\n💥 Some tests FAILED!")
            self.logger.error("Please check Kafka cluster status and configuration.")

        self.logger.info("=" * 60)
        return all_passed


def main():
    """Main function to run connectivity tests."""
    tester = KafkaConnectivityTester()

    try:
        success = tester.run_full_test()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        tester.logger.info("\n🛑 Test interrupted by user")
        tester.cleanup_test_topic()
        sys.exit(1)
    except Exception as e:
        tester.logger.error(f"💥 Unexpected error: {e}")
        tester.cleanup_test_topic()
        sys.exit(1)


if __name__ == "__main__":
    main()
