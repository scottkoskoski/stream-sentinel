# /stream-sentinel/src/ml/training/utils/logging.py

"""
Training Logging Infrastructure

Enterprise-grade structured logging system for the modular training pipeline
with comprehensive context tracking, performance monitoring, and operational
observability.

Key Features:
- Structured logging with JSON and text formatters
- Context-aware logging with pipeline and component tracking
- Performance metrics integration with timing decorators
- Log rotation and retention management
- Multiple output destinations (file, console, remote)
- Log aggregation and correlation support

Architecture:
- Hierarchical logger structure with component-specific loggers
- Structured log records with consistent schema
- Async logging support for high-performance scenarios
- Log filtering and sampling for production optimization
- Integration with monitoring and alerting systems
"""

import json
import logging
import logging.handlers
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from functools import wraps
from contextlib import contextmanager
import traceback
import sys
import os


class StructuredFormatter(logging.Formatter):
    """
    JSON-structured log formatter with consistent schema.
    """
    
    def __init__(self, include_extra: bool = True):
        super().__init__()
        self.include_extra = include_extra
        self.hostname = os.uname().nodename if hasattr(os, 'uname') else 'unknown'
        
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as structured JSON."""
        
        # Base log record
        log_entry = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
            'thread': record.thread,
            'thread_name': record.threadName,
            'process': record.process,
            'hostname': self.hostname
        }
        
        # Add exception info if present
        if record.exc_info:
            log_entry['exception'] = {
                'type': record.exc_info[0].__name__,
                'message': str(record.exc_info[1]),
                'traceback': traceback.format_exception(*record.exc_info)
            }
        
        # Add extra fields
        if self.include_extra and hasattr(record, '__dict__'):
            extra = {}
            for key, value in record.__dict__.items():
                if key not in log_entry and not key.startswith('_'):
                    try:
                        # Ensure value is JSON serializable
                        json.dumps(value)
                        extra[key] = value
                    except (TypeError, ValueError):
                        extra[key] = str(value)
            
            if extra:
                log_entry['extra'] = extra
        
        return json.dumps(log_entry)


class TextFormatter(logging.Formatter):
    """
    Human-readable text formatter with consistent structure.
    """
    
    def __init__(self, include_extra: bool = True):
        format_str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        super().__init__(format_str)
        self.include_extra = include_extra
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as structured text."""
        base_message = super().format(record)
        
        # Add extra fields
        if self.include_extra and hasattr(record, '__dict__'):
            extra_fields = []
            for key, value in record.__dict__.items():
                if (key not in ['name', 'levelname', 'message', 'created', 'asctime'] and 
                    not key.startswith('_')):
                    extra_fields.append(f"{key}={value}")
            
            if extra_fields:
                base_message += f" [{', '.join(extra_fields)}]"
        
        return base_message


class PerformanceLogFilter(logging.Filter):
    """
    Filter for performance-related log records with timing information.
    """
    
    def filter(self, record: logging.LogRecord) -> bool:
        """Add performance metrics to log records."""
        # Add process info if not present
        if not hasattr(record, 'memory_usage_mb'):
            try:
                import psutil
                process = psutil.Process()
                record.memory_usage_mb = process.memory_info().rss / (1024 * 1024)
                record.cpu_percent = process.cpu_percent()
            except (ImportError, Exception):
                record.memory_usage_mb = None
                record.cpu_percent = None
        
        return True


class TrainingLogger:
    """
    High-level training logger with component-specific context.
    """
    
    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        """
        Initialize training logger with configuration.
        
        Args:
            name: Logger name (usually component name)
            config: Logging configuration dictionary
        """
        self.name = name
        self.config = config or {}
        self.logger = logging.getLogger(name)
        
        # Context tracking
        self._context: Dict[str, Any] = {}
        self._context_lock = threading.RLock()
        
        # Performance tracking
        self._timers: Dict[str, float] = {}
        self._counters: Dict[str, int] = {}
        
        # Configure logger if not already configured
        if not self.logger.handlers:
            self._configure_logger()
    
    def _configure_logger(self) -> None:
        """Configure logger with handlers and formatters."""
        log_level = self.config.get('log_level', 'INFO').upper()
        self.logger.setLevel(getattr(logging, log_level))
        
        # Add performance filter
        perf_filter = PerformanceLogFilter()
        self.logger.addFilter(perf_filter)
        
        # Console handler
        if self.config.get('enable_console', True):
            console_handler = logging.StreamHandler(sys.stdout)
            
            if self.config.get('log_format', 'text') == 'json':
                console_handler.setFormatter(StructuredFormatter())
            else:
                console_handler.setFormatter(TextFormatter())
            
            self.logger.addHandler(console_handler)
        
        # File handler with rotation
        if self.config.get('enable_file', True):
            log_dir = Path(self.config.get('log_dir', 'logs/training'))
            log_dir.mkdir(parents=True, exist_ok=True)
            
            log_file = log_dir / f"{self.config.get('log_file_prefix', 'training')}.log"
            
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=self.config.get('log_rotation_size_mb', 100) * 1024 * 1024,
                backupCount=self.config.get('log_retention_count', 10)
            )
            
            file_handler.setFormatter(StructuredFormatter())
            self.logger.addHandler(file_handler)
    
    def set_context(self, **kwargs) -> None:
        """Set persistent context for all log messages."""
        with self._context_lock:
            self._context.update(kwargs)
    
    def clear_context(self) -> None:
        """Clear persistent context."""
        with self._context_lock:
            self._context.clear()
    
    @contextmanager
    def context(self, **kwargs):
        """Temporary context manager for log messages."""
        old_context = self._context.copy()
        try:
            self.set_context(**kwargs)
            yield
        finally:
            with self._context_lock:
                self._context = old_context
    
    def _log_with_context(self, level: str, message: str, 
                         extra: Optional[Dict[str, Any]] = None) -> None:
        """Log message with persistent and additional context."""
        combined_extra = {}
        
        # Add persistent context
        with self._context_lock:
            combined_extra.update(self._context)
        
        # Add call-specific extra data
        if extra:
            combined_extra.update(extra)
        
        # Get logger method
        log_method = getattr(self.logger, level.lower())
        
        # Log with extra data
        if combined_extra:
            log_method(message, extra=combined_extra)
        else:
            log_method(message)
    
    def debug(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log debug message."""
        self._log_with_context('DEBUG', message, extra)
    
    def info(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log info message."""
        self._log_with_context('INFO', message, extra)
    
    def warning(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log warning message."""
        self._log_with_context('WARNING', message, extra)
    
    def error(self, message: str, extra: Optional[Dict[str, Any]] = None,
             exc_info: bool = True) -> None:
        """Log error message with exception info."""
        if exc_info and extra:
            extra['exc_info'] = exc_info
        elif exc_info:
            extra = {'exc_info': exc_info}
        
        self._log_with_context('ERROR', message, extra)
    
    def critical(self, message: str, extra: Optional[Dict[str, Any]] = None,
                exc_info: bool = True) -> None:
        """Log critical message with exception info."""
        if exc_info and extra:
            extra['exc_info'] = exc_info
        elif exc_info:
            extra = {'exc_info': exc_info}
        
        self._log_with_context('CRITICAL', message, extra)
    
    def start_timer(self, timer_name: str) -> None:
        """Start a named timer for performance tracking."""
        self._timers[timer_name] = time.time()
        self.debug(f"timer.started", extra={'timer_name': timer_name})
    
    def stop_timer(self, timer_name: str, log_result: bool = True) -> float:
        """Stop a named timer and optionally log the duration."""
        if timer_name not in self._timers:
            self.warning(f"timer.not_found", extra={'timer_name': timer_name})
            return 0.0
        
        duration = time.time() - self._timers[timer_name]
        del self._timers[timer_name]
        
        if log_result:
            self.info(f"timer.completed", extra={
                'timer_name': timer_name,
                'duration_seconds': duration
            })
        
        return duration
    
    @contextmanager
    def timer(self, timer_name: str, log_result: bool = True):
        """Context manager for timing operations."""
        self.start_timer(timer_name)
        try:
            yield
        finally:
            self.stop_timer(timer_name, log_result)
    
    def increment_counter(self, counter_name: str, value: int = 1) -> int:
        """Increment a named counter."""
        self._counters[counter_name] = self._counters.get(counter_name, 0) + value
        current_value = self._counters[counter_name]
        
        self.debug(f"counter.incremented", extra={
            'counter_name': counter_name,
            'increment': value,
            'current_value': current_value
        })
        
        return current_value
    
    def get_counter(self, counter_name: str) -> int:
        """Get current counter value."""
        return self._counters.get(counter_name, 0)
    
    def log_performance_metrics(self) -> None:
        """Log current performance metrics."""
        try:
            import psutil
            process = psutil.Process()
            
            metrics = {
                'memory_usage_mb': process.memory_info().rss / (1024 * 1024),
                'cpu_percent': process.cpu_percent(),
                'open_files': len(process.open_files()),
                'threads': process.num_threads(),
                'active_timers': list(self._timers.keys()),
                'counters': self._counters.copy()
            }
            
            self.info("performance.metrics", extra=metrics)
            
        except ImportError:
            self.warning("performance.metrics_unavailable", extra={
                'reason': 'psutil not available'
            })


def timing_decorator(logger: Optional[TrainingLogger] = None, 
                    timer_name: Optional[str] = None):
    """
    Decorator for timing function execution.
    
    Args:
        logger: TrainingLogger instance
        timer_name: Name for the timer (defaults to function name)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            actual_logger = logger
            if actual_logger is None:
                # Create a default logger
                actual_logger = TrainingLogger(func.__module__)
            
            actual_timer_name = timer_name or f"{func.__name__}_execution"
            
            with actual_logger.timer(actual_timer_name):
                return func(*args, **kwargs)
        
        return wrapper
    return decorator


def log_exceptions(logger: Optional[TrainingLogger] = None):
    """
    Decorator for automatic exception logging.
    
    Args:
        logger: TrainingLogger instance
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            actual_logger = logger
            if actual_logger is None:
                actual_logger = TrainingLogger(func.__module__)
            
            try:
                return func(*args, **kwargs)
            except Exception as e:
                actual_logger.error(f"exception.{func.__name__}", extra={
                    'function': func.__name__,
                    'args_count': len(args),
                    'kwargs_keys': list(kwargs.keys()),
                    'exception_type': type(e).__name__,
                    'exception_message': str(e)
                })
                raise
        
        return wrapper
    return decorator


def setup_training_logging(level: str = "INFO",
                          log_format: str = "json",
                          log_dir: str = "logs/training",
                          enable_console: bool = True,
                          enable_file: bool = True) -> Dict[str, Any]:
    """
    Setup package-level training logging configuration.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Log format (json, text)
        log_dir: Directory for log files
        enable_console: Enable console logging
        enable_file: Enable file logging
        
    Returns:
        Logging configuration dictionary
    """
    config = {
        'log_level': level.upper(),
        'log_format': log_format.lower(),
        'log_dir': log_dir,
        'enable_console': enable_console,
        'enable_file': enable_file,
        'log_file_prefix': 'training',
        'log_rotation_size_mb': 100,
        'log_retention_count': 10,
        'log_retention_days': 30
    }
    
    # Setup root logger for the training package
    root_logger = TrainingLogger('ml.training', config)
    
    # Setup specific component loggers
    components = [
        'ml.training.checkpoint_manager',
        'ml.training.data_processor', 
        'ml.training.hyperparameter_optimizer',
        'ml.training.pipeline_orchestrator'
    ]
    
    for component in components:
        TrainingLogger(component, config)
    
    # Log initialization
    root_logger.info("training_logging.initialized", extra={
        'log_level': level,
        'log_format': log_format,
        'components_configured': len(components)
    })
    
    return config


def get_training_logger(name: str) -> TrainingLogger:
    """
    Get a training logger instance for a specific component.
    
    Args:
        name: Component name
        
    Returns:
        TrainingLogger instance
    """
    return TrainingLogger(name)


def configure_component_logger(component_name: str, 
                             config: Dict[str, Any]) -> TrainingLogger:
    """
    Configure a logger for a specific component with custom settings.
    
    Args:
        component_name: Name of the component
        config: Custom logging configuration
        
    Returns:
        Configured TrainingLogger instance
    """
    logger = TrainingLogger(f"ml.training.{component_name}", config)
    
    logger.info("component_logger.configured", extra={
        'component': component_name,
        'log_level': config.get('log_level', 'INFO')
    })
    
    return logger


# Context managers for common logging patterns
@contextmanager
def training_session(logger: TrainingLogger, session_id: str, **context):
    """Context manager for training session logging."""
    logger.set_context(session_id=session_id, **context)
    logger.info("training_session.started", extra={'session_id': session_id})
    
    start_time = time.time()
    try:
        yield logger
    except Exception as e:
        logger.error("training_session.failed", extra={
            'session_id': session_id,
            'error': str(e),
            'duration': time.time() - start_time
        })
        raise
    finally:
        duration = time.time() - start_time
        logger.info("training_session.completed", extra={
            'session_id': session_id,
            'duration': duration
        })
        logger.clear_context()


@contextmanager
def stage_logging(logger: TrainingLogger, stage_name: str, **context):
    """Context manager for pipeline stage logging."""
    stage_context = {'stage': stage_name, **context}
    
    with logger.context(**stage_context):
        logger.info("stage.started", extra={'stage_name': stage_name})
        
        start_time = time.time()
        try:
            yield logger
        except Exception as e:
            logger.error("stage.failed", extra={
                'stage_name': stage_name,
                'error': str(e),
                'duration': time.time() - start_time
            })
            raise
        finally:
            duration = time.time() - start_time
            logger.info("stage.completed", extra={
                'stage_name': stage_name,
                'duration': duration
            })