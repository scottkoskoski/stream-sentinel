"""
Kafka tracing middleware for Stream-Sentinel.

Provides helper functions that wrap Kafka produce/consume operations with
automatic correlation ID propagation and span tracking.

Usage::

    from tracing.middleware import traced_produce, traced_consume

    # On the consumer side:
    tracing_ctx = traced_consume(msg)

    # On the producer side:
    traced_produce(producer, "fraud-alerts", value, key=key,
                   correlation_id=tracing_ctx.correlation_id)
"""

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from tracing.correlation import (
    HEADER_CORRELATION_ID,
    HEADER_PARENT_SPAN_ID,
    HEADER_SPAN_ID,
    TracingContext,
    extract_correlation_id,
    extract_span_id,
    generate_correlation_id,
    generate_span_id,
    inject_correlation_id,
)

logger = logging.getLogger("stream_sentinel.tracing")


def traced_produce(
    producer: Any,
    topic: str,
    value: Any,
    key: Any = None,
    headers: Optional[List[Tuple[str, bytes]]] = None,
    correlation_id: Optional[str] = None,
    parent_span_id: Optional[str] = None,
    callback: Optional[Callable] = None,
) -> str:
    """Produce a Kafka message with tracing headers injected.

    If *correlation_id* is not provided, the function checks for an active
    ``TracingContext`` on the current thread.  If none is found, a new
    correlation ID is generated so that every produced message is traceable.

    A new span ID is generated for each produce call to represent the
    downstream hop.

    Args:
        producer: ``confluent_kafka.Producer`` instance.
        topic: Destination Kafka topic.
        value: Message value (bytes or str).
        key: Optional message key.
        headers: Existing Kafka headers to augment.
        correlation_id: Explicit correlation ID (overrides context).
        parent_span_id: Explicit parent span ID (overrides context).
        callback: Delivery callback.

    Returns:
        The correlation ID that was injected.
    """
    # Resolve correlation ID
    if correlation_id is None:
        ctx = TracingContext.current()
        if ctx:
            correlation_id = ctx.correlation_id
            if parent_span_id is None:
                parent_span_id = ctx.span_id
        else:
            correlation_id = generate_correlation_id()

    # Generate a new span ID for this produce hop
    new_span_id = generate_span_id()

    # Build headers with tracing info
    traced_headers = inject_correlation_id(
        headers,
        correlation_id,
        span_id=new_span_id,
        parent_span_id=parent_span_id,
    )

    # Produce with tracing headers
    produce_kwargs: Dict[str, Any] = {
        "topic": topic,
        "value": value,
        "headers": traced_headers,
    }
    if key is not None:
        produce_kwargs["key"] = key
    if callback is not None:
        produce_kwargs["callback"] = callback

    producer.produce(**produce_kwargs)

    logger.debug(
        "Traced produce: topic=%s correlation_id=%s span_id=%s",
        topic,
        correlation_id,
        new_span_id,
    )

    return correlation_id


def traced_consume(message: Any) -> TracingContext:
    """Extract tracing context from a consumed Kafka message.

    If the message carries a correlation ID header, it is reused.  Otherwise
    a new one is generated (entry point of the trace).  A new span ID is
    created for the consumer processing, and the producer's span ID (if
    present) becomes the parent span.

    The returned ``TracingContext`` is automatically *attached* to the
    current thread so that subsequent log entries and ``traced_produce``
    calls pick it up.

    Args:
        message: A ``confluent_kafka.Message`` object.

    Returns:
        A ``TracingContext`` that is already attached to the current thread.
    """
    raw_headers = message.headers() if hasattr(message, "headers") else None

    # Extract existing tracing info from headers
    correlation_id = extract_correlation_id(raw_headers)
    upstream_span_id = extract_span_id(raw_headers)

    # If no correlation ID exists, this is the trace entry point
    if correlation_id is None:
        correlation_id = generate_correlation_id()

    ctx = TracingContext(
        correlation_id=correlation_id,
        parent_span_id=upstream_span_id,
    )
    ctx.attach()

    logger.debug(
        "Traced consume: topic=%s correlation_id=%s span_id=%s parent_span=%s",
        message.topic() if hasattr(message, "topic") else "unknown",
        ctx.correlation_id,
        ctx.span_id,
        ctx.parent_span_id,
    )

    return ctx
