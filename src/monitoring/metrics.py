"""
Prometheus metrics framework for Stream-Sentinel

Provides comprehensive observability metrics for all system components:
- Kafka producer/consumer metrics
- Fraud detection performance metrics  
- Redis state management metrics
- Model inference performance
- Business metrics (fraud rates, alert counts)
"""

import time
from typing import Dict, Optional, List
from prometheus_client import (
    Counter, Histogram, Gauge, Info, CollectorRegistry, 
    generate_latest, CONTENT_TYPE_LATEST, start_http_server
)
import threading
import logging

logger = logging.getLogger(__name__)

class StreamSentinelMetrics:
    """Centralized metrics collector for Stream-Sentinel components"""
    
    def __init__(self, registry: Optional[CollectorRegistry] = None, component_name: str = "stream-sentinel"):
        """
        Initialize metrics collector
        
        Args:
            registry: Prometheus registry (creates new if None)
            component_name: Name of the component for metric labeling
        """
        self.registry = registry or CollectorRegistry()
        self.component_name = component_name
        
        # System info
        self.info = Info(
            'stream_sentinel_build_info',
            'Stream-Sentinel build and version information',
            registry=self.registry
        )
        
        # Kafka Metrics
        self._init_kafka_metrics()
        
        # Fraud Detection Metrics
        self._init_fraud_detection_metrics()
        
        # Redis Metrics  
        self._init_redis_metrics()
        
        # Model Performance Metrics
        self._init_model_metrics()
        
        # Business Metrics
        self._init_business_metrics()
        
        # System Health Metrics
        self._init_health_metrics()
        
        logger.info(f"Initialized metrics for component: {component_name}")
    
    def _init_kafka_metrics(self):
        """Initialize Kafka-related metrics"""
        
        # Message throughput
        self.kafka_messages_produced = Counter(
            'kafka_messages_produced_total',
            'Total number of messages produced to Kafka',
            ['topic', 'component'],
            registry=self.registry
        )
        
        self.kafka_messages_consumed = Counter(
            'kafka_messages_consumed_total', 
            'Total number of messages consumed from Kafka',
            ['topic', 'component', 'consumer_group'],
            registry=self.registry
        )
        
        # Message processing latency
        self.kafka_produce_latency = Histogram(
            'kafka_produce_duration_seconds',
            'Time spent producing messages to Kafka',
            ['topic', 'component'],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
            registry=self.registry
        )
        
        self.kafka_consume_latency = Histogram(
            'kafka_consume_duration_seconds',
            'Time spent consuming and processing messages from Kafka',
            ['topic', 'component'],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
            registry=self.registry
        )
        
        # Consumer lag
        self.kafka_consumer_lag = Gauge(
            'kafka_consumer_lag_messages',
            'Number of messages behind the latest offset',
            ['topic', 'partition', 'consumer_group'],
            registry=self.registry
        )
        
        # Error rates
        self.kafka_errors = Counter(
            'kafka_errors_total',
            'Total number of Kafka operation errors',
            ['operation', 'error_type', 'component'],
            registry=self.registry
        )
    
    def _init_fraud_detection_metrics(self):
        """Initialize fraud detection metrics"""
        
        # Transaction processing
        self.transactions_processed = Counter(
            'transactions_processed_total',
            'Total number of transactions processed',
            ['component', 'status'],
            registry=self.registry
        )
        
        # Fraud detection latency (end-to-end)
        self.fraud_detection_latency = Histogram(
            'fraud_detection_duration_seconds',
            'Time to complete fraud detection for a transaction',
            ['component'],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
            registry=self.registry
        )
        
        # Detailed processing stage latencies
        self.feature_extraction_latency = Histogram(
            'feature_extraction_duration_seconds',
            'Time spent extracting features for fraud detection',
            buckets=[0.001, 0.002, 0.005, 0.01, 0.025, 0.05, 0.1],
            registry=self.registry
        )
        
        self.model_inference_latency = Histogram(
            'model_inference_duration_seconds', 
            'Time spent on ML model inference',
            ['model_version', 'model_type'],
            buckets=[0.001, 0.002, 0.005, 0.01, 0.025, 0.05, 0.1],
            registry=self.registry
        )
        
        self.business_rules_latency = Histogram(
            'business_rules_duration_seconds',
            'Time spent evaluating business rules',
            buckets=[0.0001, 0.0005, 0.001, 0.002, 0.005, 0.01],
            registry=self.registry
        )
        
        # Fraud detection accuracy metrics
        self.fraud_predictions = Counter(
            'fraud_predictions_total',
            'Total fraud predictions made',
            ['prediction', 'model_version'],
            registry=self.registry
        )
        
        self.fraud_score_distribution = Histogram(
            'fraud_score_distribution',
            'Distribution of fraud scores',
            buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
            registry=self.registry
        )
    
    def _init_redis_metrics(self):
        """Initialize Redis state management metrics"""
        
        # Redis operations
        self.redis_operations = Counter(
            'redis_operations_total',
            'Total Redis operations performed',
            ['operation', 'database', 'status'],
            registry=self.registry
        )
        
        self.redis_operation_latency = Histogram(
            'redis_operation_duration_seconds',
            'Time spent on Redis operations', 
            ['operation', 'database'],
            buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1],
            registry=self.registry
        )
        
        # Redis connection pool
        self.redis_connections = Gauge(
            'redis_connections_active',
            'Number of active Redis connections',
            ['pool_name'],
            registry=self.registry
        )
        
        # Cache hit rates
        self.redis_cache_operations = Counter(
            'redis_cache_operations_total',
            'Redis cache operations (hits/misses)',
            ['operation', 'cache_name'],
            registry=self.registry
        )
    
    def _init_model_metrics(self):
        """Initialize ML model performance metrics"""
        
        # Model loading and updates
        self.model_loads = Counter(
            'model_loads_total',
            'Total number of model load operations',
            ['model_name', 'version', 'status'],
            registry=self.registry
        )
        
        self.model_update_latency = Histogram(
            'model_update_duration_seconds',
            'Time to load/update ML model',
            ['model_name'],
            buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0],
            registry=self.registry
        )
        
        # Current model info
        self.current_model_info = Gauge(
            'current_model_version',
            'Currently loaded model version',
            ['model_name', 'version', 'algorithm'],
            registry=self.registry
        )
        
        # Online learning metrics
        self.drift_detection_runs = Counter(
            'drift_detection_runs_total',
            'Total drift detection evaluations',
            ['detector_type', 'result'],
            registry=self.registry
        )
        
        self.model_retraining_runs = Counter(
            'model_retraining_runs_total',
            'Total model retraining operations',
            ['trigger', 'status'],
            registry=self.registry
        )
    
    def _init_business_metrics(self):
        """Initialize business-specific metrics"""
        
        # Fraud rates
        self.fraud_rate = Gauge(
            'fraud_rate',
            'Current fraud detection rate (percentage)',
            ['time_window'],
            registry=self.registry
        )
        
        # Alert generation
        self.alerts_generated = Counter(
            'alerts_generated_total',
            'Total fraud alerts generated',
            ['severity', 'alert_type'],
            registry=self.registry
        )
        
        self.alerts_processed = Counter(
            'alerts_processed_total',
            'Total fraud alerts processed',
            ['action_taken', 'severity'],
            registry=self.registry
        )
        
        # User management
        self.users_blocked = Counter(
            'users_blocked_total',
            'Total users blocked due to fraud',
            ['reason', 'severity'],
            registry=self.registry
        )
        
        self.false_positive_rate = Gauge(
            'false_positive_rate',
            'Current false positive rate from fraud detection',
            ['time_window'],
            registry=self.registry
        )
    
    def _init_health_metrics(self):
        """Initialize system health metrics"""
        
        # Component health
        self.component_health = Gauge(
            'component_health_status',
            'Health status of system components (1=healthy, 0=unhealthy)',
            ['component_name', 'check_type'],
            registry=self.registry
        )
        
        # Resource utilization
        self.memory_usage = Gauge(
            'memory_usage_bytes',
            'Memory usage by component',
            ['component'],
            registry=self.registry
        )
        
        self.cpu_usage = Gauge(
            'cpu_usage_percent',
            'CPU usage by component',
            ['component'],
            registry=self.registry
        )
        
        # Error rates
        self.error_rate = Counter(
            'errors_total',
            'Total errors by component and type',
            ['component', 'error_type', 'severity'],
            registry=self.registry
        )
    
    # Convenience methods for recording metrics
    
    def record_kafka_produce(self, topic: str, duration: float, success: bool = True):
        """Record Kafka message production"""
        self.kafka_messages_produced.labels(
            topic=topic, 
            component=self.component_name
        ).inc()
        
        self.kafka_produce_latency.labels(
            topic=topic,
            component=self.component_name
        ).observe(duration)
        
        if not success:
            self.kafka_errors.labels(
                operation='produce',
                error_type='send_failure', 
                component=self.component_name
            ).inc()
    
    def record_kafka_consume(self, topic: str, duration: float, consumer_group: str):
        """Record Kafka message consumption"""
        self.kafka_messages_consumed.labels(
            topic=topic,
            component=self.component_name,
            consumer_group=consumer_group
        ).inc()
        
        self.kafka_consume_latency.labels(
            topic=topic,
            component=self.component_name
        ).observe(duration)
    
    def record_fraud_detection(self, duration: float, fraud_score: float, prediction: str, model_version: str):
        """Record fraud detection results"""
        self.transactions_processed.labels(
            component=self.component_name,
            status='completed'
        ).inc()
        
        self.fraud_detection_latency.labels(
            component=self.component_name
        ).observe(duration)
        
        self.fraud_score_distribution.observe(fraud_score)
        
        self.fraud_predictions.labels(
            prediction=prediction,
            model_version=model_version
        ).inc()
    
    def record_model_inference(self, duration: float, model_version: str, model_type: str = "xgboost"):
        """Record ML model inference performance"""
        self.model_inference_latency.labels(
            model_version=model_version,
            model_type=model_type
        ).observe(duration)
    
    def record_redis_operation(self, operation: str, duration: float, database: str = "0", success: bool = True):
        """Record Redis operation"""
        status = 'success' if success else 'error'
        
        self.redis_operations.labels(
            operation=operation,
            database=database,
            status=status
        ).inc()
        
        self.redis_operation_latency.labels(
            operation=operation,
            database=database
        ).observe(duration)
    
    def record_alert(self, severity: str, alert_type: str = "fraud"):
        """Record alert generation"""
        self.alerts_generated.labels(
            severity=severity,
            alert_type=alert_type
        ).inc()
    
    def update_fraud_rate(self, rate: float, time_window: str = "1h"):
        """Update current fraud detection rate"""
        self.fraud_rate.labels(time_window=time_window).set(rate)
    
    def update_component_health(self, component_name: str, is_healthy: bool, check_type: str = "general"):
        """Update component health status"""
        status = 1 if is_healthy else 0
        self.component_health.labels(
            component_name=component_name,
            check_type=check_type
        ).set(status)
    
    def get_metrics(self) -> str:
        """Get metrics in Prometheus format"""
        return generate_latest(self.registry)
    
    def start_metrics_server(self, port: int = 8000):
        """Start HTTP server for metrics endpoint"""
        start_http_server(port, registry=self.registry)
        logger.info(f"Started metrics server on port {port}")

# Global metrics instance
_metrics_instance: Optional[StreamSentinelMetrics] = None
_metrics_lock = threading.Lock()

def get_metrics(component_name: str = "stream-sentinel") -> StreamSentinelMetrics:
    """Get global metrics instance (singleton pattern)"""
    global _metrics_instance
    
    with _metrics_lock:
        if _metrics_instance is None:
            _metrics_instance = StreamSentinelMetrics(component_name=component_name)
        
        return _metrics_instance

def start_metrics_server(port: int = 8000, component_name: str = "stream-sentinel"):
    """Start metrics HTTP server"""
    metrics = get_metrics(component_name)
    metrics.start_metrics_server(port)

# Context managers for automatic metric recording

class timer:
    """Context manager for timing operations"""
    
    def __init__(self, histogram, **labels):
        self.histogram = histogram
        self.labels = labels
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time:
            duration = time.time() - self.start_time
            self.histogram.labels(**self.labels).observe(duration)

class kafka_produce_timer:
    """Context manager for Kafka produce operations"""
    
    def __init__(self, metrics: StreamSentinelMetrics, topic: str):
        self.metrics = metrics
        self.topic = topic
        self.start_time = None
        self.success = True
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.success = False
        
        if self.start_time:
            duration = time.time() - self.start_time
            self.metrics.record_kafka_produce(self.topic, duration, self.success)

class fraud_detection_timer:
    """Context manager for fraud detection operations"""
    
    def __init__(self, metrics: StreamSentinelMetrics):
        self.metrics = metrics
        self.start_time = None
        self.fraud_score = 0.0
        self.prediction = "unknown"
        self.model_version = "unknown"
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def set_result(self, fraud_score: float, prediction: str, model_version: str):
        """Set fraud detection result"""
        self.fraud_score = fraud_score
        self.prediction = prediction
        self.model_version = model_version
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time:
            duration = time.time() - self.start_time
            self.metrics.record_fraud_detection(
                duration, self.fraud_score, self.prediction, self.model_version
            )

# Decorator for automatic metric recording

def track_execution_time(histogram, **labels):
    """Decorator to track function execution time"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            with timer(histogram, **labels):
                return func(*args, **kwargs)
        return wrapper
    return decorator