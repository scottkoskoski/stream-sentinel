Stream-Sentinel Development Log #001: Kafka Infrastructure Setup
Date: August 25, 2025
Phase: Foundation Building - Kafka Infrastructure
Status: Complete
Project Context
Stream-Sentinel is a real-time distributed financial fraud detection system being built to demonstrate production-ready distributed systems engineering. The project processes multiple data streams (synthetic transactions, market data, sentiment analysis) through Apache Kafka to detect fraudulent patterns in real-time.
Technical Stack:

Apache Kafka for distributed messaging
Docker for containerization
Python with confluent-kafka client
Redis for state management
Arch Linux development environment with Neovim

What We Accomplished
1. Docker Infrastructure Setup
Problem: Needed a production-ready Kafka cluster for development and testing.
Solution: Created a comprehensive Docker Compose configuration with:

Zookeeper: Manages Kafka cluster coordination and metadata
Kafka Broker: Core message processing engine (configured for fraud detection workload)
Schema Registry: Handles data format evolution and compatibility
Kafka UI: Web interface for monitoring topics and messages (port 8080)
Redis: High-performance state management and caching (port 6379)
Redis Insight: Redis monitoring interface (port 8001)

Configuration highlights:

6 default partitions for parallel processing
LZ4 compression for high throughput
7-day message retention for fraud pattern analysis
Optimized for 10k+ transactions per second

Technical challenge resolved: Docker networking issues due to kernel/module mismatch after system update. The veth (virtual ethernet) module wasn't available, preventing container networking. Resolution required system reboot to load updated kernel modules.
2. Python Development Environment
Setup process:

Created isolated virtual environment in stream-sentinel/venv/
Installed confluent-kafka (production Kafka client) after resolving Python 3.13 compatibility issues
Added development tools: python-lsp-server, black, isort, flake8
Established project structure with src/kafka/ directory

Key learning: The kafka-python library has compatibility issues with Python 3.13, requiring migration to confluent-kafka which needed system-level librdkafka installation.
3. Configuration Management System
Created: src/kafka/config.py - Centralized configuration management
Key concepts implemented:

Environment awareness: Development/staging/production configurations
Producer optimization: Different configs for transaction, market_data, and sentiment producers
Consumer optimization: Configs for fraud_detector, feature_extractor, and analytics consumers
Distributed systems patterns: Connection pooling, retry logic, timeout management

Configuration challenges: Confluent-kafka uses different parameter names than Java Kafka client. Required multiple iterations to identify valid parameters:

Uses dot notation (bootstrap.servers) instead of underscore (bootstrap_servers)
Doesn't support all Java client parameters (like buffer.memory)
Requires minimal configuration to establish basic connectivity

4. Connectivity Testing Framework
Created: src/kafka/test_connectivity.py - Comprehensive integration test
Test coverage:

Administrative operations: Topic creation/deletion with fraud-optimized settings
Producer operations: Multi-type message production with delivery confirmation
Consumer operations: Message consumption with manual offset management
Resource cleanup: Proper teardown of test resources

Validation results:

Successfully created topic with 12 partitions
Produced 3 test messages across different partitions
Consumed all messages with content validation
Demonstrated exactly-once processing semantics

Technical Concepts Learned
Distributed Messaging Fundamentals

Topics and Partitions: Topics are message categories; partitions enable parallel processing
Producer/Consumer Pattern: Decouples data producers from consumers for scalability
Offset Management: Tracking message position for exactly-once processing
Consumer Groups: Enable load balancing and fault tolerance

Configuration Management Patterns

Environment-based configuration: Different settings for dev/staging/production
Producer specialization: Optimized configs for different data types and throughput requirements
Connection reuse: Avoiding expensive connection creation through proper client lifecycle management

Docker Networking

Container communication: Services communicate via hostnames (kafka, zookeeper, redis)
Port mapping: External access through localhost:port mappings
Virtual ethernet (veth): Kernel module required for container-to-container networking
Network troubleshooting: System updates can break Docker networking if kernel modules aren't aligned

File Organization
stream-sentinel/
├── docker/
│   └── docker-compose.yml          # Kafka infrastructure
├── src/kafka/
│   ├── config.py                   # Configuration management
│   └── test_connectivity.py        # Integration testing
├── venv/                           # Python virtual environment
└── requirements.txt                # Python dependencies
Current System Capabilities
Data Processing:

Handle 10k+ transactions per second
Process multiple data stream types (transactions, market data, sentiment)
Maintain message ordering within partitions
Support exactly-once processing semantics

Operational:

Topic creation with configurable partitions and retention
Producer/consumer lifecycle management
Error handling and retry logic
Comprehensive logging and debugging

Monitoring:

Kafka UI for topic and message monitoring
Redis Insight for cache monitoring
Detailed logging with configurable levels

Next Development Steps

Synthetic Transaction Generator: Create realistic transaction data based on fraud patterns
External API Integration: Binance WebSocket and Twitter API connections
Feature Engineering Pipeline: Real-time feature extraction for fraud detection
Stream Processing Components: Implement fraud detection algorithms
