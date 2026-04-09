"""
Distributed tracing for Stream-Sentinel.

Provides lightweight correlation ID propagation across the Kafka topic chain
without external dependencies (no OpenTelemetry).  Each transaction receives a
correlation ID at ingestion time, and that ID is carried through every
downstream topic (fraud-alerts, fraud-detection-results, blocked-transactions,
dead-letter-queue, model-drift-alerts) via Kafka message headers.

Usage:
    from tracing.correlation import generate_correlation_id, TracingContext
    from tracing.middleware import traced_produce, traced_consume
"""

from tracing.correlation import (
    TracingContext,
    extract_correlation_id,
    generate_correlation_id,
    inject_correlation_id,
)
from tracing.middleware import traced_consume, traced_produce

__all__ = [
    "generate_correlation_id",
    "extract_correlation_id",
    "inject_correlation_id",
    "TracingContext",
    "traced_produce",
    "traced_consume",
]
