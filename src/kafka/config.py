# /stream-sentinel/src/kafka/config.py

"""
Kafka Configuration Module for Stream-Sentinel

This module provides centralized configuration management for all Kafka clients
in the fraud detection system. It handles connection settings, serialization,
error handling, and environment-specific configurations.

Key concepts demonstrated:
- Configuration management patterns for distributed systems
- Producer/Consumer optimization for different workloads
- Schema Registry integration for data format evolution
- Environment-aware settings (dev/staging/production)
"""

import os
import logging
from typing import Dict, Any, Optional
from enum import Enum


class Environment(Enum):
    """Environment types for configuration management."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class KafkaConfig:
    """
    Centralized Kafka configuration management.

    This class provides optimized configurations for different types of workloads
    in our fraud detection system:
    - High-throughput transaction producers
    - Real-time fraud detection consumers
    - Market data ingestion
    - Sentiment analysis processing
    """

    def __init__(self, environment: Optional[str] = None):
        """
        Initialize Kafka configuration.

        Args:
            environment: Override environment detection (dev/staging/production)
        """
        # Environment detection
        self.environment = Environment(
            environment or os.getenv("STREAM_SENTINEL_ENV", "development")
        )

        # Core connection settings
        self.bootstrap_servers = self._get_bootstrap_servers()
        self.schema_registry_url = self._get_schema_registry_url()

        # Setup logging
        self.logger = self._setup_logging()

        self.logger.info(
            f"Kafka configuration initialized for {self.environment.value}"
        )

    def _get_bootstrap_servers(self) -> str:
        """Get Kafka bootstrap servers based on environment."""
        servers_map = {
            Environment.DEVELOPMENT: "localhost:9092",
            Environment.STAGING: os.getenv(
                "KAFKA_STAGING_SERVERS", "kafka-staging:9092"
            ),
            Environment.PRODUCTION: os.getenv(
                "KAFKA_PROD_SERVERS",
                "kafka-prod-1:9092,kafka-prod-2:9092,kafka-prod-3:9092",
            ),
        }
        return servers_map[self.environment]

    def _get_schema_registry_url(self) -> str:
        """Get Schema Registry URL based on environment."""
        registry_map = {
            Environment.DEVELOPMENT: "http://localhost:8081",
            Environment.STAGING: os.getenv(
                "SCHEMA_REGISTRY_STAGING", "http://schema-registry-staging:8081"
            ),
            Environment.PRODUCTION: os.getenv(
                "SCHEMA_REGISTRY_PROD", "http://schema-registry-prod:8081"
            ),
        }
        return registry_map[self.environment]

    def _setup_logging(self) -> logging.Logger:
        """Setup logging configuration for Kafka operations."""
        logger = logging.getLogger("stream_sentinel.kafka")

        if not logger.handlers:  # Avoid duplicate handlers
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

            # Log level based on environment
            log_levels = {
                Environment.DEVELOPMENT: logging.DEBUG,
                Environment.STAGING: logging.INFO,
                Environment.PRODUCTION: logging.WARNING,
            }
            logger.setLevel(log_levels[self.environment])

        return logger

    def get_producer_config(self, producer_type: str = "default") -> Dict[str, Any]:
        """
        Get optimized producer configuration for confluent-kafka.

        Args:
            producer_type: Type of producer (transaction, market_data, sentiment)
                         Each type has different performance characteristics

        Returns:
            Dictionary with Kafka producer configuration
        """
        # Base configuration for all producers (minimal working config)
        base_config = {
            "bootstrap.servers": self.bootstrap_servers,
        }

        # Producer-specific optimizations (very basic for confluent-kafka)
        producer_configs = {
            "transaction": {
                "linger.ms": 5,
            },
            "market_data": {
                "linger.ms": 1,
            },
            "sentiment": {
                "linger.ms": 100,
            },
        }

        # Merge base config with producer-specific settings
        config = {**base_config, **producer_configs.get(producer_type, {})}

        # Environment-specific adjustments
        if self.environment == Environment.PRODUCTION:
            config.update(
                {
                    "acks": "all",  # Maximum durability in production
                    "retries": 2147483647,
                    "max.in.flight.requests.per.connection": 5,
                }
            )
        elif self.environment == Environment.DEVELOPMENT:
            config.update(
                {
                    "acks": "1",  # Faster acknowledgment for development
                    "retries": 10,
                    "request.timeout.ms": 10000,  # Shorter timeout for faster feedback
                }
            )

        self.logger.debug(
            f"Generated {producer_type} producer config for {self.environment.value}"
        )
        return config

    def get_consumer_config(
        self, consumer_group: str, consumer_type: str = "default"
    ) -> Dict[str, Any]:
        """
        Get optimized consumer configuration for confluent-kafka.

        Args:
            consumer_group: Consumer group ID for this consumer
            consumer_type: Type of consumer (fraud_detector, feature_extractor, analytics)

        Returns:
            Dictionary with Kafka consumer configuration
        """
        # Base configuration for all consumers
        base_config = {
            "bootstrap.servers": self.bootstrap_servers,
            "group.id": consumer_group,
            # Offset management
            "auto.offset.reset": "latest",  # Start from newest messages by default
            "enable.auto.commit": False,  # Manual commit for exactly-once processing
            # Session management
            "session.timeout.ms": 30000,  # 30 second session timeout
            "heartbeat.interval.ms": 3000,  # Heartbeat every 3 seconds
            "max.poll.interval.ms": 300000,  # 5 minutes max processing time
            # Fetch settings for optimal throughput
            "fetch.min.bytes": 1024,  # Wait for at least 1KB
            "fetch.max.wait.ms": 500,  # Wait up to 500ms for data
            "max.partition.fetch.bytes": 1048576,  # 1MB max per partition
        }

        # Consumer-specific optimizations
        consumer_configs = {
            "fraud_detector": {
                # Real-time fraud detection consumer (low latency critical)
                "fetch.min.bytes": 1,  # Process immediately
                "fetch.max.wait.ms": 100,  # Minimal wait time
                "auto.offset.reset": "latest",  # Only process new transactions
            },
            "feature_extractor": {
                # Feature extraction consumer (throughput optimized)
                "fetch.min.bytes": 32768,  # Wait for larger batches
                "fetch.max.wait.ms": 1000,  # Allow more batching time
                "max.partition.fetch.bytes": 2097152,  # 2MB per partition
            },
            "analytics": {
                # Analytics consumer (batch processing optimized)
                "fetch.min.bytes": 65536,  # Large batches for analytics
                "fetch.max.wait.ms": 5000,  # Longer wait for larger batches
                "auto.offset.reset": "earliest",  # Process all historical data
            },
        }

        # Merge base config with consumer-specific settings
        config = {**base_config, **consumer_configs.get(consumer_type, {})}

        # Environment-specific adjustments
        if self.environment == Environment.DEVELOPMENT:
            config.update(
                {
                    "session.timeout.ms": 10000,  # Shorter timeout for faster development cycles
                    "max.poll.interval.ms": 60000,  # Shorter processing window for debugging
                }
            )

        self.logger.debug(
            f"Generated {consumer_type} consumer config for group '{consumer_group}'"
        )
        return config

    def get_topic_config(self, topic_type: str) -> Dict[str, Any]:
        """
        Get recommended topic configuration for creation.

        Args:
            topic_type: Type of topic (transactions, market_data, sentiment, alerts)

        Returns:
            Dictionary with topic configuration parameters
        """
        # Base topic settings
        base_config = {
            "cleanup_policy": "delete",  # Delete old messages (vs. compaction)
            "compression_type": "lz4",  # Match producer compression
        }

        # Topic-specific configurations
        topic_configs = {
            "transactions": {
                "num_partitions": 12,  # High parallelism for transaction processing
                "replication_factor": (
                    3 if self.environment == Environment.PRODUCTION else 1
                ),
                "retention_ms": 604800000,  # 7 days retention
                "segment_ms": 86400000,  # Daily segments (24 hours)
                "min_insync_replicas": (
                    2 if self.environment == Environment.PRODUCTION else 1
                ),
            },
            "market_data": {
                "num_partitions": 6,  # Moderate parallelism
                "replication_factor": (
                    3 if self.environment == Environment.PRODUCTION else 1
                ),
                "retention_ms": 259200000,  # 3 days retention (less critical for replay)
                "segment_ms": 3600000,  # Hourly segments
                "min_insync_replicas": (
                    2 if self.environment == Environment.PRODUCTION else 1
                ),
            },
            "sentiment": {
                "num_partitions": 3,  # Lower volume data
                "replication_factor": (
                    3 if self.environment == Environment.PRODUCTION else 1
                ),
                "retention_ms": 1209600000,  # 14 days retention (useful for analysis)
                "segment_ms": 86400000,  # Daily segments
                "min_insync_replicas": (
                    2 if self.environment == Environment.PRODUCTION else 1
                ),
            },
            "alerts": {
                "num_partitions": 6,  # Moderate parallelism for alert processing
                "replication_factor": (
                    3 if self.environment == Environment.PRODUCTION else 1
                ),
                "retention_ms": 2592000000,  # 30 days retention (compliance)
                "segment_ms": 86400000,  # Daily segments
                "min_insync_replicas": (
                    2 if self.environment == Environment.PRODUCTION else 1
                ),
                "cleanup_policy": "compact",  # Keep latest alert per key
            },
        }

        # Merge configurations
        config = {**base_config, **topic_configs.get(topic_type, {})}

        self.logger.debug(f"Generated topic config for '{topic_type}' topic")
        return config

    def get_schema_registry_config(self) -> Dict[str, Any]:
        """Get Schema Registry client configuration."""
        config = {
            "url": self.schema_registry_url,
            "auth": None,  # Add authentication in production
        }

        if self.environment == Environment.PRODUCTION:
            # Add authentication and SSL settings for production
            config.update(
                {
                    "auth": (
                        os.getenv("SCHEMA_REGISTRY_USER"),
                        os.getenv("SCHEMA_REGISTRY_PASSWORD"),
                    ),
                    "verify_ssl": True,
                }
            )

        return config

    def get_monitoring_config(self) -> Dict[str, Any]:
        """Get configuration for Kafka monitoring and metrics."""
        return {
            "jmx_port": 9101,
            "metrics_reporters": [
                "io.confluent.metrics.reporter.ConfluentMetricsReporter"
            ],
            "confluent_metrics_reporter_bootstrap_servers": self.bootstrap_servers,
            "confluent_metrics_reporter_topic": "_confluent-metrics",
        }


# Convenience function for getting a configured instance
def get_kafka_config(environment: Optional[str] = None) -> KafkaConfig:
    """
    Factory function to get a KafkaConfig instance.

    Args:
        environment: Optional environment override

    Returns:
        Configured KafkaConfig instance
    """
    return KafkaConfig(environment)


# Example usage and testing
if __name__ == "__main__":
    # Example of how this configuration will be used
    config = get_kafka_config()

    # Get different producer configurations
    transaction_producer_config = config.get_producer_config("transaction")
    market_data_producer_config = config.get_producer_config("market_data")

    # Get different consumer configurations
    fraud_detector_config = config.get_consumer_config(
        "fraud-detection-group", "fraud_detector"
    )
    analytics_config = config.get_consumer_config("analytics-group", "analytics")

    # Get topic configurations
    transactions_topic_config = config.get_topic_config("transactions")

    print("Kafka configuration module loaded successfully!")
    print(f"Environment: {config.environment.value}")
    print(f"Bootstrap servers: {config.bootstrap_servers}")
    print(f"Schema Registry: {config.schema_registry_url}")

    # Test basic connectivity with confluent-kafka
    try:
        from confluent_kafka import Producer

        # Create a producer with our configuration
        producer_config = config.get_producer_config("transaction")
        producer = Producer(producer_config)

        print("✅ Confluent Kafka producer created successfully!")
        producer.flush()  # Clean shutdown

    except ImportError:
        print(
            "⚠️  Confluent Kafka not installed - install with: pip install confluent-kafka"
        )
    except Exception as e:
        print(f"❌ Kafka connection failed: {e}")
        print("Make sure your Kafka cluster is running (docker-compose up -d)")
