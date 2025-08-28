#!/usr/bin/env python3
"""
Kafka consumer for persisting fraud detection data to PostgreSQL and ClickHouse.

This consumer handles asynchronous persistence of transaction data, fraud detection results,
and system metrics without impacting real-time fraud detection performance.
"""

import json
import logging
import signal
import sys
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from confluent_kafka import Consumer, KafkaError, KafkaException

# Add src to path for imports
sys.path.append('/home/scottyk/Documents/stream-sentinel/src')

from persistence.database import get_persistence_layer, close_persistence_layer
from persistence.schemas import FraudAlert, TransactionRecord, AlertSeverity, AlertStatus
from kafka.config import get_kafka_config


class PersistenceConsumer:
    """Kafka consumer for database persistence operations."""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.consumer = None
        self.persistence_layer = None
        self.running = False
        
        # Performance metrics
        self.messages_processed = 0
        self.processing_errors = 0
        self.start_time = time.time()
        self.last_metrics_log = time.time()
        
        # Batch processing settings
        self.batch_size = 100
        self.batch_timeout_ms = 1000
        self.current_batch = []
        self.last_batch_time = time.time()
        
        self._initialize_consumer()
        self._setup_signal_handlers()
    
    def _initialize_consumer(self):
        """Initialize Kafka consumer and persistence layer."""
        try:
            # Get Kafka configuration
            kafka_config = get_kafka_config('persistence-consumer')
            kafka_config.update({
                'group.id': 'stream-sentinel-persistence',
                'auto.offset.reset': 'earliest',
                'enable.auto.commit': True,
                'auto.commit.interval.ms': 1000,
                'max.poll.interval.ms': 300000,  # 5 minutes
                'session.timeout.ms': 30000,
                'heartbeat.interval.ms': 3000,
                'max.poll.records': self.batch_size
            })
            
            self.consumer = Consumer(kafka_config)
            
            # Subscribe to persistence topics
            topics = [
                'fraud-detection-results',
                'transaction-records',
                'performance-metrics',
                'audit-events'
            ]
            
            self.consumer.subscribe(topics)
            self.logger.info(f"Subscribed to topics: {topics}")
            
            # Initialize persistence layer
            self.persistence_layer = get_persistence_layer()
            
            # Test database connectivity
            health = self.persistence_layer.health_check()
            if not all(health.values()):
                raise Exception(f"Database health check failed: {health}")
            
            self.logger.info("Persistence consumer initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize persistence consumer: {e}")
            raise
    
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
    
    def start(self):
        """Start the persistence consumer."""
        self.logger.info("Starting persistence consumer...")
        self.running = True
        
        try:
            while self.running:
                self._process_messages()
                self._check_batch_timeout()
                self._log_metrics()
                
        except KeyboardInterrupt:
            self.logger.info("Received keyboard interrupt")
        except Exception as e:
            self.logger.error(f"Unexpected error in consumer loop: {e}")
        finally:
            self._shutdown()
    
    def _process_messages(self):
        """Process incoming Kafka messages."""
        try:
            # Poll for messages with short timeout for responsive shutdown
            msg = self.consumer.poll(timeout=1.0)
            
            if msg is None:
                return
            
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    # End of partition - not an error
                    return
                else:
                    self.logger.error(f"Kafka error: {msg.error()}")
                    self.processing_errors += 1
                    return
            
            # Process the message
            self._handle_message(msg)
            
        except KafkaException as e:
            self.logger.error(f"Kafka exception: {e}")
            self.processing_errors += 1
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")
            self.processing_errors += 1
    
    def _handle_message(self, msg):
        """Handle individual Kafka message."""
        try:
            topic = msg.topic()
            value = json.loads(msg.value().decode('utf-8'))
            
            # Add to batch for processing
            self.current_batch.append({
                'topic': topic,
                'value': value,
                'timestamp': datetime.now(timezone.utc),
                'partition': msg.partition(),
                'offset': msg.offset()
            })
            
            # Process batch if it's full
            if len(self.current_batch) >= self.batch_size:
                self._process_batch()
            
            self.messages_processed += 1
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to decode message JSON: {e}")
            self.processing_errors += 1
        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
            self.processing_errors += 1
    
    def _check_batch_timeout(self):
        """Check if batch should be processed due to timeout."""
        current_time = time.time()
        if (self.current_batch and 
            (current_time - self.last_batch_time) * 1000 > self.batch_timeout_ms):
            self._process_batch()
    
    def _process_batch(self):
        """Process accumulated batch of messages."""
        if not self.current_batch:
            return
        
        start_time = time.time()
        batch_size = len(self.current_batch)
        
        try:
            # Group messages by topic for efficient processing
            grouped_messages = {}
            for msg_data in self.current_batch:
                topic = msg_data['topic']
                if topic not in grouped_messages:
                    grouped_messages[topic] = []
                grouped_messages[topic].append(msg_data)
            
            # Process each topic group
            for topic, messages in grouped_messages.items():
                self._process_topic_batch(topic, messages)
            
            processing_time = (time.time() - start_time) * 1000
            self.logger.debug(
                f"Processed batch of {batch_size} messages in {processing_time:.2f}ms"
            )
            
        except Exception as e:
            self.logger.error(f"Error processing batch: {e}")
            self.processing_errors += len(self.current_batch)
        finally:
            # Clear batch and update timing
            self.current_batch = []
            self.last_batch_time = time.time()
    
    def _process_topic_batch(self, topic: str, messages: List[Dict[str, Any]]):
        """Process batch of messages for a specific topic."""
        if topic == 'fraud-detection-results':
            self._process_fraud_detection_batch(messages)
        elif topic == 'transaction-records':
            self._process_transaction_records_batch(messages)
        elif topic == 'performance-metrics':
            self._process_performance_metrics_batch(messages)
        elif topic == 'audit-events':
            self._process_audit_events_batch(messages)
        else:
            self.logger.warning(f"Unknown topic: {topic}")
    
    def _process_fraud_detection_batch(self, messages: List[Dict[str, Any]]):
        """Process batch of fraud detection results."""
        for msg_data in messages:
            try:
                data = msg_data['value']
                
                # Extract transaction record
                transaction_data = data.get('transaction', {})
                transaction = TransactionRecord(
                    transaction_id=transaction_data.get('transaction_id'),
                    user_id=transaction_data.get('user_id'),
                    timestamp=datetime.fromisoformat(transaction_data.get('timestamp')),
                    amount=float(transaction_data.get('amount', 0)),
                    merchant_category=transaction_data.get('merchant_category', ''),
                    payment_method=transaction_data.get('payment_method', ''),
                    device_info=transaction_data.get('device_info', ''),
                    location_country=transaction_data.get('location_country', ''),
                    location_state=transaction_data.get('location_state', ''),
                    is_fraud=data.get('is_fraud', False),
                    fraud_score=float(data.get('fraud_score', 0)),
                    processing_time_ms=int(data.get('processing_time_ms', 0))
                )
                
                # Extract fraud alert if severity warrants it
                alert = None
                if data.get('severity') in ['MEDIUM', 'HIGH', 'CRITICAL']:
                    alert = FraudAlert(
                        transaction_id=transaction.transaction_id,
                        user_id=transaction.user_id,
                        severity=AlertSeverity(data.get('severity')),
                        fraud_score=transaction.fraud_score,
                        ml_prediction=float(data.get('ml_prediction', 0)),
                        business_rules_triggered=data.get('business_rules_triggered', []),
                        explanation=data.get('explanation', {})
                    )
                
                # Persist to databases
                if alert:
                    self.persistence_layer.persist_fraud_detection_result(
                        transaction=transaction,
                        alert=alert,
                        features=data.get('features', {}),
                        detection_metadata=data.get('detection_metadata', {})
                    )
                else:
                    # Just persist transaction record to ClickHouse
                    self.persistence_layer.clickhouse.insert_transaction_record(transaction)
                    
            except Exception as e:
                self.logger.error(f"Error processing fraud detection message: {e}")
                self.processing_errors += 1
    
    def _process_transaction_records_batch(self, messages: List[Dict[str, Any]]):
        """Process batch of transaction records for ClickHouse."""
        transactions = []
        
        for msg_data in messages:
            try:
                data = msg_data['value']
                
                transaction = TransactionRecord(
                    transaction_id=data.get('transaction_id'),
                    user_id=data.get('user_id'),
                    timestamp=datetime.fromisoformat(data.get('timestamp')),
                    amount=float(data.get('amount', 0)),
                    merchant_category=data.get('merchant_category', ''),
                    payment_method=data.get('payment_method', ''),
                    device_info=data.get('device_info', ''),
                    location_country=data.get('location_country', ''),
                    location_state=data.get('location_state', ''),
                    is_fraud=data.get('is_fraud', False),
                    fraud_score=float(data.get('fraud_score', 0)),
                    processing_time_ms=int(data.get('processing_time_ms', 0))
                )
                
                transactions.append(transaction)
                
            except Exception as e:
                self.logger.error(f"Error parsing transaction record: {e}")
                self.processing_errors += 1
        
        if transactions:
            success = self.persistence_layer.clickhouse.batch_insert_transactions(transactions)
            if not success:
                self.processing_errors += len(transactions)
    
    def _process_performance_metrics_batch(self, messages: List[Dict[str, Any]]):
        """Process batch of performance metrics."""
        metrics = []
        
        for msg_data in messages:
            try:
                data = msg_data['value']
                
                metric = {
                    'timestamp': datetime.fromisoformat(data.get('timestamp')),
                    'metric_name': data.get('metric_name'),
                    'metric_value': float(data.get('metric_value')),
                    'component': data.get('component'),
                    'instance_id': data.get('instance_id', 'unknown'),
                    'additional_labels': data.get('labels', {})
                }
                
                metrics.append(metric)
                
            except Exception as e:
                self.logger.error(f"Error parsing performance metric: {e}")
                self.processing_errors += 1
        
        if metrics:
            success = self.persistence_layer.clickhouse.insert_performance_metrics(metrics)
            if not success:
                self.processing_errors += len(metrics)
    
    def _process_audit_events_batch(self, messages: List[Dict[str, Any]]):
        """Process batch of audit events."""
        for msg_data in messages:
            try:
                data = msg_data['value']
                
                success = self.persistence_layer.postgres.log_audit_event(
                    event_type=data.get('event_type'),
                    entity_type=data.get('entity_type'),
                    entity_id=data.get('entity_id'),
                    action=data.get('action'),
                    actor_id=data.get('actor_id'),
                    details=data.get('details')
                )
                
                if not success:
                    self.processing_errors += 1
                    
            except Exception as e:
                self.logger.error(f"Error processing audit event: {e}")
                self.processing_errors += 1
    
    def _log_metrics(self):
        """Log performance metrics periodically."""
        current_time = time.time()
        
        # Log metrics every 60 seconds
        if current_time - self.last_metrics_log >= 60:
            uptime = current_time - self.start_time
            messages_per_second = self.messages_processed / max(uptime, 1)
            error_rate = self.processing_errors / max(self.messages_processed, 1) * 100
            
            # Get database performance metrics
            db_metrics = self.persistence_layer.get_performance_metrics()
            
            self.logger.info(
                f"Persistence Consumer Metrics - "
                f"Messages: {self.messages_processed}, "
                f"Errors: {self.processing_errors} ({error_rate:.2f}%), "
                f"Rate: {messages_per_second:.2f} msg/s, "
                f"Uptime: {uptime:.1f}s"
            )
            
            self.logger.info(f"Database Metrics - PostgreSQL: {db_metrics['postgresql']}")
            self.logger.info(f"Database Metrics - ClickHouse: {db_metrics['clickhouse']}")
            
            self.last_metrics_log = current_time
    
    def _shutdown(self):
        """Graceful shutdown of consumer."""
        self.logger.info("Shutting down persistence consumer...")
        
        try:
            # Process any remaining batch
            if self.current_batch:
                self.logger.info(f"Processing final batch of {len(self.current_batch)} messages")
                self._process_batch()
            
            # Close Kafka consumer
            if self.consumer:
                self.consumer.close()
                self.logger.info("Kafka consumer closed")
            
            # Close persistence layer
            if self.persistence_layer:
                self.persistence_layer.close()
                self.logger.info("Database connections closed")
            
            # Log final metrics
            uptime = time.time() - self.start_time
            self.logger.info(
                f"Persistence consumer shutdown complete - "
                f"Processed {self.messages_processed} messages "
                f"with {self.processing_errors} errors in {uptime:.1f}s"
            )
            
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")


def main():
    """Main entry point for persistence consumer."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('/tmp/persistence_consumer.log')
        ]
    )
    
    logger = logging.getLogger(__name__)
    logger.info("Starting Stream-Sentinel Persistence Consumer")
    
    try:
        consumer = PersistenceConsumer()
        consumer.start()
    except Exception as e:
        logger.error(f"Failed to start persistence consumer: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()