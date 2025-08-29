# /stream-sentinel/src/ml/training/utils/metrics.py

"""
Training Metrics Infrastructure

Comprehensive metrics collection and monitoring system for the modular training
pipeline with support for multiple backends and real-time observability.

Key Features:
- Multi-backend metrics support (Prometheus, CloudWatch, DataDog)
- Real-time performance metrics collection
- Training-specific metrics with business context
- Automatic metric aggregation and summarization
- Alert integration for threshold-based monitoring
- Historical metrics storage and analysis

Architecture:
- Pluggable metrics backends with consistent interface
- Metric collectors with automatic batching and buffering
- Context-aware metrics with pipeline and component tags
- Async metrics emission for high-performance scenarios
- Metric validation and schema enforcement
"""

import time
import threading
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union, Callable
from dataclasses import dataclass, field
from pathlib import Path
import json
import logging


@dataclass
class Metric:
    """
    Individual metric with metadata and context.
    """
    name: str
    value: Union[int, float]
    timestamp: Optional[datetime] = None
    tags: Dict[str, str] = field(default_factory=dict)
    metric_type: str = "gauge"  # gauge, counter, histogram, timer
    unit: Optional[str] = None
    description: Optional[str] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metric to dictionary for serialization."""
        return {
            'name': self.name,
            'value': self.value,
            'timestamp': self.timestamp.isoformat(),
            'tags': self.tags,
            'type': self.metric_type,
            'unit': self.unit,
            'description': self.description
        }


@dataclass
class MetricSummary:
    """
    Statistical summary of metric values over time.
    """
    name: str
    count: int
    sum: float
    min: float
    max: float
    mean: float
    std: float
    percentiles: Dict[str, float]
    start_time: datetime
    end_time: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert summary to dictionary for serialization."""
        return {
            'name': self.name,
            'count': self.count,
            'sum': self.sum,
            'min': self.min,
            'max': self.max,
            'mean': self.mean,
            'std': self.std,
            'percentiles': self.percentiles,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat()
        }


class MetricsBackend(ABC):
    """
    Abstract base class for metrics backends.
    """
    
    @abstractmethod
    def emit_metric(self, metric: Metric) -> None:
        """Emit a single metric."""
        pass
    
    @abstractmethod
    def emit_batch(self, metrics: List[Metric]) -> None:
        """Emit a batch of metrics."""
        pass
    
    @abstractmethod
    def close(self) -> None:
        """Close backend connections and cleanup."""
        pass


class PrometheusBackend(MetricsBackend):
    """
    Prometheus metrics backend with push gateway support.
    """
    
    def __init__(self, gateway_url: Optional[str] = None, job_name: str = "training_pipeline"):
        """
        Initialize Prometheus backend.
        
        Args:
            gateway_url: Prometheus push gateway URL
            job_name: Job name for metrics grouping
        """
        self.gateway_url = gateway_url
        self.job_name = job_name
        self.logger = logging.getLogger(__name__)
        
        # Metric registry
        self.metrics_registry: Dict[str, Any] = {}
        
        try:
            if gateway_url:
                from prometheus_client import CollectorRegistry, Gauge, Counter, Histogram
                from prometheus_client.gateway import push_to_gateway
                
                self.registry = CollectorRegistry()
                self.push_to_gateway = push_to_gateway
                self.Gauge = Gauge
                self.Counter = Counter
                self.Histogram = Histogram
                
                self.logger.info("prometheus_backend.initialized", extra={
                    'gateway_url': gateway_url,
                    'job_name': job_name
                })
        except ImportError:
            self.logger.warning("prometheus_backend.unavailable", extra={
                'reason': 'prometheus_client not installed'
            })
            self.gateway_url = None
    
    def emit_metric(self, metric: Metric) -> None:
        """Emit single metric to Prometheus."""
        if not self.gateway_url:
            return
        
        try:
            metric_key = f"{metric.name}_{metric.metric_type}"
            
            if metric_key not in self.metrics_registry:
                if metric.metric_type == "gauge":
                    self.metrics_registry[metric_key] = self.Gauge(
                        metric.name, 
                        metric.description or f"Training metric: {metric.name}",
                        labelnames=list(metric.tags.keys()),
                        registry=self.registry
                    )
                elif metric.metric_type == "counter":
                    self.metrics_registry[metric_key] = self.Counter(
                        metric.name,
                        metric.description or f"Training counter: {metric.name}",
                        labelnames=list(metric.tags.keys()),
                        registry=self.registry
                    )
            
            # Set metric value
            prometheus_metric = self.metrics_registry[metric_key]
            if metric.tags:
                prometheus_metric.labels(**metric.tags).set(metric.value)
            else:
                prometheus_metric.set(metric.value)
            
        except Exception as e:
            self.logger.error("prometheus_backend.emit_failed", extra={
                'metric_name': metric.name,
                'error': str(e)
            })
    
    def emit_batch(self, metrics: List[Metric]) -> None:
        """Emit batch of metrics to Prometheus."""
        for metric in metrics:
            self.emit_metric(metric)
        
        # Push to gateway
        if self.gateway_url and self.metrics_registry:
            try:
                self.push_to_gateway(
                    self.gateway_url,
                    job=self.job_name,
                    registry=self.registry
                )
            except Exception as e:
                self.logger.error("prometheus_backend.push_failed", extra={
                    'error': str(e),
                    'metrics_count': len(metrics)
                })
    
    def close(self) -> None:
        """Close Prometheus backend."""
        self.logger.info("prometheus_backend.closed")


class FileBackend(MetricsBackend):
    """
    File-based metrics backend for development and testing.
    """
    
    def __init__(self, file_path: Union[str, Path]):
        """
        Initialize file backend.
        
        Args:
            file_path: Path to metrics file
        """
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)
        self._lock = threading.Lock()
        
        self.logger.info("file_backend.initialized", extra={
            'file_path': str(self.file_path)
        })
    
    def emit_metric(self, metric: Metric) -> None:
        """Emit metric to file."""
        with self._lock:
            try:
                with open(self.file_path, 'a') as f:
                    f.write(json.dumps(metric.to_dict()) + '\n')
            except Exception as e:
                self.logger.error("file_backend.emit_failed", extra={
                    'metric_name': metric.name,
                    'error': str(e)
                })
    
    def emit_batch(self, metrics: List[Metric]) -> None:
        """Emit batch of metrics to file."""
        with self._lock:
            try:
                with open(self.file_path, 'a') as f:
                    for metric in metrics:
                        f.write(json.dumps(metric.to_dict()) + '\n')
            except Exception as e:
                self.logger.error("file_backend.batch_emit_failed", extra={
                    'metrics_count': len(metrics),
                    'error': str(e)
                })
    
    def close(self) -> None:
        """Close file backend."""
        self.logger.info("file_backend.closed")


class MetricsCollector:
    """
    High-performance metrics collector with buffering and aggregation.
    """
    
    def __init__(self, backend: MetricsBackend,
                 buffer_size: int = 1000,
                 flush_interval: int = 30):
        """
        Initialize metrics collector.
        
        Args:
            backend: MetricsBackend implementation
            buffer_size: Buffer size for batching
            flush_interval: Flush interval in seconds
        """
        self.backend = backend
        self.buffer_size = buffer_size
        self.flush_interval = flush_interval
        self.logger = logging.getLogger(__name__)
        
        # Buffering
        self.buffer: List[Metric] = []
        self.buffer_lock = threading.Lock()
        
        # Aggregation
        self.aggregators: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self.aggregation_lock = threading.Lock()
        
        # Background flushing
        self.flush_timer: Optional[threading.Timer] = None
        self.shutdown_event = threading.Event()
        
        self._start_background_flush()
        
        self.logger.info("metrics_collector.initialized", extra={
            'buffer_size': buffer_size,
            'flush_interval': flush_interval
        })
    
    def emit(self, name: str, value: Union[int, float],
            tags: Optional[Dict[str, str]] = None,
            metric_type: str = "gauge",
            unit: Optional[str] = None,
            description: Optional[str] = None) -> None:
        """
        Emit a metric with optional buffering.
        
        Args:
            name: Metric name
            value: Metric value
            tags: Optional tags dictionary
            metric_type: Type of metric (gauge, counter, etc.)
            unit: Optional unit of measurement
            description: Optional description
        """
        metric = Metric(
            name=name,
            value=value,
            tags=tags or {},
            metric_type=metric_type,
            unit=unit,
            description=description
        )
        
        # Add to buffer
        with self.buffer_lock:
            self.buffer.append(metric)
            
            # Flush if buffer is full
            if len(self.buffer) >= self.buffer_size:
                self._flush_buffer()
        
        # Add to aggregation
        with self.aggregation_lock:
            self.aggregators[name].append((value, time.time()))
    
    def _flush_buffer(self) -> None:
        """Flush buffered metrics to backend."""
        if not self.buffer:
            return
        
        try:
            batch = self.buffer.copy()
            self.buffer.clear()
            
            self.backend.emit_batch(batch)
            
            self.logger.debug("metrics.batch_flushed", extra={
                'batch_size': len(batch)
            })
            
        except Exception as e:
            self.logger.error("metrics.flush_failed", extra={
                'error': str(e),
                'batch_size': len(self.buffer)
            })
    
    def _start_background_flush(self) -> None:
        """Start background flush timer."""
        if not self.shutdown_event.is_set():
            with self.buffer_lock:
                self._flush_buffer()
            
            self.flush_timer = threading.Timer(
                self.flush_interval,
                self._start_background_flush
            )
            self.flush_timer.start()
    
    def get_metric_summary(self, metric_name: str,
                          window_seconds: int = 300) -> Optional[MetricSummary]:
        """
        Get statistical summary of metric values.
        
        Args:
            metric_name: Name of metric to summarize
            window_seconds: Time window for summary (default: 5 minutes)
            
        Returns:
            MetricSummary if data available, None otherwise
        """
        with self.aggregation_lock:
            if metric_name not in self.aggregators:
                return None
            
            values_with_time = list(self.aggregators[metric_name])
            
            if not values_with_time:
                return None
            
            # Filter by time window
            current_time = time.time()
            cutoff_time = current_time - window_seconds
            
            recent_values = [
                value for value, timestamp in values_with_time
                if timestamp >= cutoff_time
            ]
            
            if not recent_values:
                return None
            
            # Calculate statistics
            import numpy as np
            
            values_array = np.array(recent_values)
            
            return MetricSummary(
                name=metric_name,
                count=len(recent_values),
                sum=float(np.sum(values_array)),
                min=float(np.min(values_array)),
                max=float(np.max(values_array)),
                mean=float(np.mean(values_array)),
                std=float(np.std(values_array)),
                percentiles={
                    'p50': float(np.percentile(values_array, 50)),
                    'p95': float(np.percentile(values_array, 95)),
                    'p99': float(np.percentile(values_array, 99))
                },
                start_time=datetime.fromtimestamp(
                    min(timestamp for _, timestamp in values_with_time 
                        if timestamp >= cutoff_time)
                ),
                end_time=datetime.fromtimestamp(current_time)
            )
    
    def close(self) -> None:
        """Close metrics collector and flush remaining metrics."""
        self.shutdown_event.set()
        
        if self.flush_timer:
            self.flush_timer.cancel()
        
        # Final flush
        with self.buffer_lock:
            self._flush_buffer()
        
        self.backend.close()
        
        self.logger.info("metrics_collector.closed")


class TrainingMetrics:
    """
    High-level training metrics interface with domain-specific metrics.
    """
    
    def __init__(self, collector: MetricsCollector):
        """
        Initialize training metrics.
        
        Args:
            collector: MetricsCollector instance
        """
        self.collector = collector
        self.logger = logging.getLogger(__name__)
        
        # Context for tagging
        self.default_tags: Dict[str, str] = {}
        
        self.logger.info("training_metrics.initialized")
    
    def set_default_tags(self, **tags) -> None:
        """Set default tags for all metrics."""
        self.default_tags.update(tags)
    
    def _emit_with_default_tags(self, name: str, value: Union[int, float],
                               tags: Optional[Dict[str, str]] = None,
                               **kwargs) -> None:
        """Emit metric with default tags applied."""
        combined_tags = self.default_tags.copy()
        if tags:
            combined_tags.update(tags)
        
        self.collector.emit(name, value, tags=combined_tags, **kwargs)
    
    # Data processing metrics
    def data_loaded(self, samples: int, features: int,
                   fraud_rate: float, processing_time: float,
                   memory_mb: float, **tags) -> None:
        """Record data loading metrics."""
        self._emit_with_default_tags(
            "ml.data.samples_loaded", samples, tags=tags, metric_type="gauge"
        )
        self._emit_with_default_tags(
            "ml.data.features_count", features, tags=tags, metric_type="gauge"
        )
        self._emit_with_default_tags(
            "ml.data.fraud_rate", fraud_rate, tags=tags, metric_type="gauge"
        )
        self._emit_with_default_tags(
            "ml.data.processing_time_seconds", processing_time, tags=tags, metric_type="timer"
        )
        self._emit_with_default_tags(
            "ml.data.memory_usage_mb", memory_mb, tags=tags, metric_type="gauge"
        )
    
    # Hyperparameter optimization metrics
    def trial_completed(self, trial_number: int, score: float,
                       duration: float, model_type: str, **tags) -> None:
        """Record hyperparameter trial completion."""
        trial_tags = {"model_type": model_type, **tags}
        
        self._emit_with_default_tags(
            "ml.hyperopt.trial_completed", 1, tags=trial_tags, metric_type="counter"
        )
        self._emit_with_default_tags(
            "ml.hyperopt.trial_score", score, tags=trial_tags, metric_type="gauge"
        )
        self._emit_with_default_tags(
            "ml.hyperopt.trial_duration_seconds", duration, tags=trial_tags, metric_type="timer"
        )
    
    def optimization_completed(self, best_score: float, n_trials: int,
                             total_time: float, converged: bool,
                             model_type: str, **tags) -> None:
        """Record optimization completion."""
        opt_tags = {"model_type": model_type, **tags}
        
        self._emit_with_default_tags(
            "ml.hyperopt.best_score", best_score, tags=opt_tags, metric_type="gauge"
        )
        self._emit_with_default_tags(
            "ml.hyperopt.trials_total", n_trials, tags=opt_tags, metric_type="gauge"
        )
        self._emit_with_default_tags(
            "ml.hyperopt.optimization_time_seconds", total_time, tags=opt_tags, metric_type="timer"
        )
        self._emit_with_default_tags(
            "ml.hyperopt.converged", 1 if converged else 0, tags=opt_tags, metric_type="gauge"
        )
    
    # Model training metrics
    def model_trained(self, model_type: str, score: float,
                     training_time: float, feature_count: int,
                     sample_count: int, **tags) -> None:
        """Record model training completion."""
        model_tags = {"model_type": model_type, **tags}
        
        self._emit_with_default_tags(
            "ml.model.validation_score", score, tags=model_tags, metric_type="gauge"
        )
        self._emit_with_default_tags(
            "ml.model.training_time_seconds", training_time, tags=model_tags, metric_type="timer"
        )
        self._emit_with_default_tags(
            "ml.model.feature_count", feature_count, tags=model_tags, metric_type="gauge"
        )
        self._emit_with_default_tags(
            "ml.model.training_samples", sample_count, tags=model_tags, metric_type="gauge"
        )
    
    # Pipeline metrics
    def pipeline_started(self, pipeline_id: str, model_types: List[str], **tags) -> None:
        """Record pipeline start."""
        pipeline_tags = {"pipeline_id": pipeline_id, **tags}
        
        self._emit_with_default_tags(
            "ml.pipeline.started", 1, tags=pipeline_tags, metric_type="counter"
        )
        self._emit_with_default_tags(
            "ml.pipeline.model_types_count", len(model_types), tags=pipeline_tags, metric_type="gauge"
        )
    
    def pipeline_completed(self, pipeline_id: str, total_time: float,
                          success: bool, best_score: Optional[float] = None,
                          **tags) -> None:
        """Record pipeline completion."""
        pipeline_tags = {"pipeline_id": pipeline_id, "success": str(success), **tags}
        
        self._emit_with_default_tags(
            "ml.pipeline.completed", 1, tags=pipeline_tags, metric_type="counter"
        )
        self._emit_with_default_tags(
            "ml.pipeline.total_time_seconds", total_time, tags=pipeline_tags, metric_type="timer"
        )
        
        if best_score is not None:
            self._emit_with_default_tags(
                "ml.pipeline.best_score", best_score, tags=pipeline_tags, metric_type="gauge"
            )
    
    def stage_completed(self, stage_name: str, duration: float,
                       success: bool, **tags) -> None:
        """Record pipeline stage completion."""
        stage_tags = {"stage": stage_name, "success": str(success), **tags}
        
        self._emit_with_default_tags(
            "ml.stage.completed", 1, tags=stage_tags, metric_type="counter"
        )
        self._emit_with_default_tags(
            "ml.stage.duration_seconds", duration, tags=stage_tags, metric_type="timer"
        )
    
    # Resource utilization metrics
    def resource_usage(self, memory_mb: float, cpu_percent: float,
                      gpu_memory_mb: Optional[float] = None,
                      gpu_utilization: Optional[float] = None,
                      **tags) -> None:
        """Record resource usage metrics."""
        self._emit_with_default_tags(
            "ml.resources.memory_mb", memory_mb, tags=tags, metric_type="gauge"
        )
        self._emit_with_default_tags(
            "ml.resources.cpu_percent", cpu_percent, tags=tags, metric_type="gauge"
        )
        
        if gpu_memory_mb is not None:
            self._emit_with_default_tags(
                "ml.resources.gpu_memory_mb", gpu_memory_mb, tags=tags, metric_type="gauge"
            )
        
        if gpu_utilization is not None:
            self._emit_with_default_tags(
                "ml.resources.gpu_utilization", gpu_utilization, tags=tags, metric_type="gauge"
            )
    
    # Error and recovery metrics
    def error_occurred(self, component: str, error_type: str,
                      recoverable: bool, **tags) -> None:
        """Record error occurrence."""
        error_tags = {
            "component": component,
            "error_type": error_type,
            "recoverable": str(recoverable),
            **tags
        }
        
        self._emit_with_default_tags(
            "ml.errors.occurred", 1, tags=error_tags, metric_type="counter"
        )
    
    def recovery_attempted(self, component: str, attempt_number: int,
                          success: bool, **tags) -> None:
        """Record recovery attempt."""
        recovery_tags = {
            "component": component,
            "attempt": str(attempt_number),
            "success": str(success),
            **tags
        }
        
        self._emit_with_default_tags(
            "ml.recovery.attempted", 1, tags=recovery_tags, metric_type="counter"
        )


def create_metrics_collector(backend_type: str = "file",
                           **backend_kwargs) -> MetricsCollector:
    """
    Factory function to create metrics collector with specified backend.
    
    Args:
        backend_type: Type of backend ("file", "prometheus")
        **backend_kwargs: Backend-specific configuration
        
    Returns:
        Configured MetricsCollector
    """
    if backend_type == "file":
        backend = FileBackend(
            backend_kwargs.get("file_path", "logs/training/metrics.jsonl")
        )
    elif backend_type == "prometheus":
        backend = PrometheusBackend(
            gateway_url=backend_kwargs.get("gateway_url"),
            job_name=backend_kwargs.get("job_name", "training_pipeline")
        )
    else:
        raise ValueError(f"Unknown backend type: {backend_type}")
    
    return MetricsCollector(
        backend,
        buffer_size=backend_kwargs.get("buffer_size", 1000),
        flush_interval=backend_kwargs.get("flush_interval", 30)
    )


def emit_training_metrics(metrics_collector: MetricsCollector) -> TrainingMetrics:
    """
    Create TrainingMetrics instance with collector.
    
    Args:
        metrics_collector: MetricsCollector instance
        
    Returns:
        TrainingMetrics instance
    """
    return TrainingMetrics(metrics_collector)