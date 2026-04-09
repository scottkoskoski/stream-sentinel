"""
Structured JSON Logging Utility for Stream-Sentinel

Provides a shared logging configuration that emits structured JSON log lines
with contextual fields (transaction_id, user_id, consumer_group, partition,
offset) automatically attached when present.

Usage:
    from utils.logging import get_logger, configure_logging

    # Call once at process startup
    configure_logging()

    # Get a logger for each module
    logger = get_logger(__name__)

    # Basic structured log
    logger.info("Transaction processed", extra={"transaction_id": "tx_123", "user_id": "u_42"})

    # Context adapter for attaching recurring fields
    from utils.logging import ContextLogger
    ctx_logger = ContextLogger(logger, consumer_group="fraud-detection")
    ctx_logger.info("Started", extra={"partition": 3})
"""

import logging
import sys
from typing import Any, Dict, Optional

try:
    from pythonjsonlogger import jsonlogger

    JSON_LOGGER_AVAILABLE = True
except ImportError:
    JSON_LOGGER_AVAILABLE = False


# Fields that should appear in every structured log line when provided
CONTEXT_FIELDS = [
    "transaction_id",
    "user_id",
    "consumer_group",
    "partition",
    "offset",
    "component",
    "correlation_id",
    "span_id",
    "parent_span_id",
]


class StreamSentinelJsonFormatter(
    jsonlogger.JsonFormatter if JSON_LOGGER_AVAILABLE else logging.Formatter
):
    """JSON formatter that always includes timestamp, level, logger name, and message."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if JSON_LOGGER_AVAILABLE:
            # Default format string that python-json-logger expands
            fmt = kwargs.pop("fmt", "%(asctime)s %(name)s %(levelname)s %(message)s")
            super().__init__(fmt, *args, **kwargs)
        else:
            super().__init__(
                fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                *args,
                **kwargs,
            )

    def add_fields(
        self,
        log_record: Dict[str, Any],
        record: logging.LogRecord,
        message_dict: Dict[str, Any],
    ) -> None:
        """Inject Stream-Sentinel context fields into every log record."""
        super().add_fields(log_record, record, message_dict)

        # Ensure standard fields
        log_record["level"] = record.levelname
        log_record["logger"] = record.name

        # Propagate context fields from the LogRecord into the JSON output
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                log_record[field] = value

        # Auto-inject tracing context from the active TracingContext when
        # correlation_id is not already set via extra={}
        if "correlation_id" not in log_record:
            tracing_ctx = _get_active_tracing_context()
            if tracing_ctx is not None:
                for key, val in tracing_ctx.items():
                    if key not in log_record:
                        log_record[key] = val


def _get_active_tracing_context() -> Optional[Dict[str, Any]]:
    """Return the active TracingContext fields, or None.

    Imports ``tracing.correlation.TracingContext`` lazily to avoid circular
    imports (the tracing module may import logging utilities itself).
    """
    try:
        from tracing.correlation import TracingContext

        ctx = TracingContext.current()
        if ctx is not None:
            return ctx.to_dict()
    except ImportError:
        pass
    return None


class _PlainJsonFormatter(logging.Formatter):
    """Minimal JSON-ish fallback when python-json-logger is not installed.

    Emits key=value pairs rather than true JSON so the output is still
    machine-parseable without the dependency.
    """

    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = []
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                extras.append(f"{field}={value}")

        # Auto-inject tracing context when not already present
        if not any(e.startswith("correlation_id=") for e in extras):
            tracing_ctx = _get_active_tracing_context()
            if tracing_ctx is not None:
                for key, val in tracing_ctx.items():
                    if not any(e.startswith(f"{key}=") for e in extras):
                        extras.append(f"{key}={val}")

        if extras:
            return f"{base} | {' '.join(extras)}"
        return base


class ContextLogger(logging.LoggerAdapter):
    """Logger adapter that attaches recurring context fields to every log call.

    Example::

        logger = ContextLogger(
            get_logger(__name__),
            consumer_group="fraud-detection",
            component="fraud_detector",
        )
        # These fields appear in every log line automatically
        logger.info("Processing started")

        # Additional per-call fields via extra=
        logger.info("Transaction scored", extra={"transaction_id": "tx_42", "user_id": "u_7"})
    """

    def __init__(self, logger: logging.Logger, **context: Any) -> None:
        super().__init__(logger, context)

    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple:
        # Merge adapter-level context with per-call extra, per-call wins
        extra = kwargs.get("extra", {})
        merged = {**self.extra, **extra}
        kwargs["extra"] = merged
        return msg, kwargs


_configured = False


def configure_logging(level: str = "INFO") -> None:
    """Configure structured JSON logging for the entire process.

    Should be called once at process startup (e.g., in main()).
    Subsequent calls are no-ops.
    """
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any existing handlers to avoid duplicate output
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if JSON_LOGGER_AVAILABLE:
        formatter = StreamSentinelJsonFormatter()
    else:
        formatter = _PlainJsonFormatter()

    handler.setFormatter(formatter)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger.

    If configure_logging() has not been called yet, it is called
    automatically with defaults so that imports in library code work
    without requiring explicit setup.
    """
    if not _configured:
        configure_logging()
    return logging.getLogger(name)
