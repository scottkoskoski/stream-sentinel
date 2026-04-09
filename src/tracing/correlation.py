"""
Correlation ID generation, extraction, and injection for Kafka message headers.

Kafka headers are a list of ``(key, value)`` tuples where *value* is ``bytes``.
This module handles the encoding/decoding transparently so callers work with
plain Python strings.

Constants:
    HEADER_CORRELATION_ID  -- Kafka header key for the correlation ID.
    HEADER_SPAN_ID         -- Kafka header key for the current span ID.
    HEADER_PARENT_SPAN_ID  -- Kafka header key for the parent span ID.
"""

import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Header key names
# ---------------------------------------------------------------------------

HEADER_CORRELATION_ID = "X-Correlation-ID"
HEADER_SPAN_ID = "X-Span-ID"
HEADER_PARENT_SPAN_ID = "X-Parent-Span-ID"


# ---------------------------------------------------------------------------
# ID generation helpers
# ---------------------------------------------------------------------------


def generate_correlation_id() -> str:
    """Generate a new correlation ID.

    Format: ``corr-<16 hex chars>`` (compact, URL-safe, unique).
    """
    return f"corr-{uuid.uuid4().hex[:16]}"


def generate_span_id() -> str:
    """Generate a new span ID.

    Format: ``span-<16 hex chars>``.
    """
    return f"span-{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------------------
# Header manipulation
# ---------------------------------------------------------------------------


def extract_correlation_id(
    message_headers: Optional[List[Tuple[str, bytes]]],
) -> Optional[str]:
    """Extract the correlation ID from Kafka message headers.

    Kafka headers are a list of ``(key, value)`` tuples where *value* is
    ``bytes``.  Returns ``None`` if the header is absent or the headers list
    is ``None``.

    Args:
        message_headers: Raw Kafka message headers (may be ``None``).

    Returns:
        The decoded correlation ID string, or ``None``.
    """
    if not message_headers:
        return None

    for key, value in message_headers:
        if key == HEADER_CORRELATION_ID and value is not None:
            return value.decode("utf-8")

    return None


def extract_span_id(
    message_headers: Optional[List[Tuple[str, bytes]]],
) -> Optional[str]:
    """Extract the span ID from Kafka message headers."""
    if not message_headers:
        return None

    for key, value in message_headers:
        if key == HEADER_SPAN_ID and value is not None:
            return value.decode("utf-8")

    return None


def inject_correlation_id(
    headers: Optional[List[Tuple[str, bytes]]],
    correlation_id: str,
    span_id: Optional[str] = None,
    parent_span_id: Optional[str] = None,
) -> List[Tuple[str, bytes]]:
    """Add tracing headers to a Kafka message header list.

    Existing tracing headers are replaced (not duplicated).  All other
    headers are preserved.

    Args:
        headers: Existing Kafka headers (may be ``None``).
        correlation_id: Correlation ID to inject.
        span_id: Optional span ID to inject.
        parent_span_id: Optional parent span ID to inject.

    Returns:
        A new header list containing the tracing headers.
    """
    tracing_keys = {HEADER_CORRELATION_ID, HEADER_SPAN_ID, HEADER_PARENT_SPAN_ID}

    # Preserve non-tracing headers from the original list
    result: List[Tuple[str, bytes]] = []
    if headers:
        result = [(k, v) for k, v in headers if k not in tracing_keys]

    # Inject tracing headers
    result.append((HEADER_CORRELATION_ID, correlation_id.encode("utf-8")))
    if span_id:
        result.append((HEADER_SPAN_ID, span_id.encode("utf-8")))
    if parent_span_id:
        result.append((HEADER_PARENT_SPAN_ID, parent_span_id.encode("utf-8")))

    return result


# ---------------------------------------------------------------------------
# Thread-local tracing context
# ---------------------------------------------------------------------------

_thread_local = threading.local()


class TracingContext:
    """Thread-local tracing context for the current transaction.

    Stores the correlation ID, span ID, parent span ID, and start time for
    the current processing scope.  Used to enrich log entries automatically
    so every log line within a transaction's processing carries the
    correlation ID without explicit ``extra=`` arguments.

    Usage::

        with TracingContext(correlation_id="corr-abc123") as ctx:
            logger.info("Processing started")  # auto-enriched
            ctx.span_id  # "span-..."
            ctx.elapsed_ms  # float

    Or without a context manager::

        ctx = TracingContext(correlation_id="corr-abc123")
        ctx.attach()
        ...
        ctx.detach()
    """

    def __init__(
        self,
        correlation_id: Optional[str] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
    ):
        self.correlation_id = correlation_id or generate_correlation_id()
        self.span_id = span_id or generate_span_id()
        self.parent_span_id = parent_span_id
        self.start_time = time.monotonic()
        self._previous_context: Optional["TracingContext"] = None

    # -- context manager --------------------------------------------------

    def __enter__(self) -> "TracingContext":
        self.attach()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.detach()

    # -- attach / detach --------------------------------------------------

    def attach(self) -> None:
        """Activate this context on the current thread."""
        self._previous_context = getattr(_thread_local, "current_context", None)
        _thread_local.current_context = self

    def detach(self) -> None:
        """Deactivate this context, restoring the previous one (if any)."""
        _thread_local.current_context = self._previous_context
        self._previous_context = None

    # -- accessors --------------------------------------------------------

    @property
    def elapsed_ms(self) -> float:
        """Milliseconds since this context was created."""
        return (time.monotonic() - self.start_time) * 1000.0

    def to_dict(self) -> Dict[str, Any]:
        """Return a dict suitable for merging into log ``extra``."""
        result: Dict[str, Any] = {
            "correlation_id": self.correlation_id,
            "span_id": self.span_id,
        }
        if self.parent_span_id:
            result["parent_span_id"] = self.parent_span_id
        return result

    # -- class methods for accessing the current context ------------------

    @classmethod
    def current(cls) -> Optional["TracingContext"]:
        """Return the active tracing context for the current thread, or ``None``."""
        return getattr(_thread_local, "current_context", None)

    @classmethod
    def current_correlation_id(cls) -> Optional[str]:
        """Return the correlation ID of the active context, or ``None``."""
        ctx = cls.current()
        return ctx.correlation_id if ctx else None
