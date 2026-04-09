"""
Unit tests for the distributed tracing / correlation ID system.

Tests cover:
- Correlation ID generation and format
- Header extraction (present, absent, None)
- Header injection (new, replace existing, preserve non-tracing)
- TracingContext lifecycle (attach, detach, context manager, nesting)
- traced_produce adds correct headers
- traced_consume creates and attaches a TracingContext
- Round-trip: inject -> extract preserves correlation ID
"""

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, call, patch

import pytest

# Ensure src/ is on the path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

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
from tracing.middleware import traced_consume, traced_produce

# ---------------------------------------------------------------------------
# Test: generate_correlation_id
# ---------------------------------------------------------------------------


class TestGenerateCorrelationId:
    def test_format(self):
        cid = generate_correlation_id()
        assert cid.startswith("corr-")
        assert len(cid) == 21  # "corr-" (5) + 16 hex chars

    def test_unique(self):
        ids = {generate_correlation_id() for _ in range(1000)}
        assert len(ids) == 1000, "All generated IDs should be unique"

    def test_hex_suffix(self):
        cid = generate_correlation_id()
        hex_part = cid[5:]
        int(hex_part, 16)  # Should not raise ValueError


class TestGenerateSpanId:
    def test_format(self):
        sid = generate_span_id()
        assert sid.startswith("span-")
        assert len(sid) == 21  # "span-" (5) + 16 hex chars

    def test_unique(self):
        ids = {generate_span_id() for _ in range(1000)}
        assert len(ids) == 1000


# ---------------------------------------------------------------------------
# Test: extract_correlation_id
# ---------------------------------------------------------------------------


class TestExtractCorrelationId:
    def test_present(self):
        headers = [(HEADER_CORRELATION_ID, b"corr-abc123")]
        assert extract_correlation_id(headers) == "corr-abc123"

    def test_absent(self):
        headers = [("X-Other-Header", b"value")]
        assert extract_correlation_id(headers) is None

    def test_none_headers(self):
        assert extract_correlation_id(None) is None

    def test_empty_list(self):
        assert extract_correlation_id([]) is None

    def test_multiple_headers_returns_first(self):
        headers = [
            (HEADER_CORRELATION_ID, b"first"),
            (HEADER_CORRELATION_ID, b"second"),
        ]
        assert extract_correlation_id(headers) == "first"

    def test_none_value_skipped(self):
        headers = [
            (HEADER_CORRELATION_ID, None),
            ("X-Other", b"val"),
        ]
        assert extract_correlation_id(headers) is None

    def test_mixed_headers(self):
        headers = [
            ("X-Other", b"foo"),
            (HEADER_CORRELATION_ID, b"corr-xyz789"),
            (HEADER_SPAN_ID, b"span-001"),
        ]
        assert extract_correlation_id(headers) == "corr-xyz789"


class TestExtractSpanId:
    def test_present(self):
        headers = [(HEADER_SPAN_ID, b"span-abc123")]
        assert extract_span_id(headers) == "span-abc123"

    def test_absent(self):
        assert extract_span_id([]) is None

    def test_none_headers(self):
        assert extract_span_id(None) is None


# ---------------------------------------------------------------------------
# Test: inject_correlation_id
# ---------------------------------------------------------------------------


class TestInjectCorrelationId:
    def test_inject_into_none(self):
        result = inject_correlation_id(None, "corr-test")
        assert len(result) == 1
        assert result[0] == (HEADER_CORRELATION_ID, b"corr-test")

    def test_inject_into_empty(self):
        result = inject_correlation_id([], "corr-test")
        assert len(result) == 1
        assert result[0] == (HEADER_CORRELATION_ID, b"corr-test")

    def test_preserves_non_tracing_headers(self):
        existing = [("X-Custom", b"value")]
        result = inject_correlation_id(existing, "corr-test")
        assert len(result) == 2
        assert ("X-Custom", b"value") in result

    def test_replaces_existing_correlation_id(self):
        existing = [(HEADER_CORRELATION_ID, b"old-id")]
        result = inject_correlation_id(existing, "corr-new")
        corr_headers = [v for k, v in result if k == HEADER_CORRELATION_ID]
        assert len(corr_headers) == 1
        assert corr_headers[0] == b"corr-new"

    def test_inject_with_span_and_parent(self):
        result = inject_correlation_id(
            None, "corr-test", span_id="span-001", parent_span_id="span-000"
        )
        keys = [k for k, v in result]
        assert HEADER_CORRELATION_ID in keys
        assert HEADER_SPAN_ID in keys
        assert HEADER_PARENT_SPAN_ID in keys

    def test_bytes_encoding(self):
        result = inject_correlation_id(None, "corr-test")
        _, value = result[0]
        assert isinstance(value, bytes)
        assert value == b"corr-test"

    def test_does_not_mutate_original(self):
        original = [("X-Custom", b"value")]
        original_copy = list(original)
        inject_correlation_id(original, "corr-test")
        assert original == original_copy


# ---------------------------------------------------------------------------
# Test: TracingContext
# ---------------------------------------------------------------------------


class TestTracingContext:
    def test_auto_generates_ids(self):
        ctx = TracingContext()
        assert ctx.correlation_id.startswith("corr-")
        assert ctx.span_id.startswith("span-")
        assert ctx.parent_span_id is None

    def test_custom_ids(self):
        ctx = TracingContext(
            correlation_id="corr-custom",
            span_id="span-custom",
            parent_span_id="span-parent",
        )
        assert ctx.correlation_id == "corr-custom"
        assert ctx.span_id == "span-custom"
        assert ctx.parent_span_id == "span-parent"

    def test_elapsed_ms(self):
        ctx = TracingContext()
        time.sleep(0.01)
        assert ctx.elapsed_ms >= 10.0

    def test_to_dict(self):
        ctx = TracingContext(
            correlation_id="corr-dict",
            span_id="span-dict",
            parent_span_id="span-parent",
        )
        d = ctx.to_dict()
        assert d["correlation_id"] == "corr-dict"
        assert d["span_id"] == "span-dict"
        assert d["parent_span_id"] == "span-parent"

    def test_to_dict_no_parent(self):
        ctx = TracingContext(correlation_id="corr-dict")
        d = ctx.to_dict()
        assert "parent_span_id" not in d

    def test_context_manager(self):
        assert TracingContext.current() is None

        with TracingContext(correlation_id="corr-cm") as ctx:
            assert TracingContext.current() is ctx
            assert TracingContext.current_correlation_id() == "corr-cm"

        assert TracingContext.current() is None

    def test_attach_detach(self):
        assert TracingContext.current() is None

        ctx = TracingContext(correlation_id="corr-ad")
        ctx.attach()
        assert TracingContext.current() is ctx

        ctx.detach()
        assert TracingContext.current() is None

    def test_nested_contexts(self):
        with TracingContext(correlation_id="corr-outer") as outer:
            assert TracingContext.current_correlation_id() == "corr-outer"

            with TracingContext(correlation_id="corr-inner") as inner:
                assert TracingContext.current_correlation_id() == "corr-inner"

            # Outer should be restored
            assert TracingContext.current_correlation_id() == "corr-outer"

        assert TracingContext.current() is None

    def test_thread_isolation(self):
        """TracingContext should be thread-local."""
        results = {}

        def worker(name: str):
            with TracingContext(correlation_id=f"corr-{name}"):
                time.sleep(0.01)
                results[name] = TracingContext.current_correlation_id()

        t1 = threading.Thread(target=worker, args=("thread1",))
        t2 = threading.Thread(target=worker, args=("thread2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["thread1"] == "corr-thread1"
        assert results["thread2"] == "corr-thread2"

    def test_current_returns_none_when_no_context(self):
        assert TracingContext.current() is None
        assert TracingContext.current_correlation_id() is None


# ---------------------------------------------------------------------------
# Test: traced_produce
# ---------------------------------------------------------------------------


class TestTracedProduce:
    def test_adds_tracing_headers(self):
        producer = MagicMock()
        traced_produce(
            producer,
            "test-topic",
            b"payload",
            key="key1",
            correlation_id="corr-produce",
        )

        producer.produce.assert_called_once()
        kwargs = producer.produce.call_args[1]
        assert kwargs["topic"] == "test-topic"
        assert kwargs["value"] == b"payload"
        assert kwargs["key"] == "key1"

        headers = kwargs["headers"]
        header_keys = [k for k, v in headers]
        assert HEADER_CORRELATION_ID in header_keys
        assert HEADER_SPAN_ID in header_keys

        corr_val = next(v for k, v in headers if k == HEADER_CORRELATION_ID)
        assert corr_val == b"corr-produce"

    def test_uses_active_context_when_no_explicit_id(self):
        producer = MagicMock()

        with TracingContext(correlation_id="corr-from-ctx"):
            traced_produce(producer, "test-topic", b"payload")

        kwargs = producer.produce.call_args[1]
        headers = kwargs["headers"]
        corr_val = next(v for k, v in headers if k == HEADER_CORRELATION_ID)
        assert corr_val == b"corr-from-ctx"

    def test_generates_new_id_when_no_context(self):
        producer = MagicMock()
        returned_id = traced_produce(producer, "test-topic", b"payload")

        assert returned_id.startswith("corr-")
        kwargs = producer.produce.call_args[1]
        headers = kwargs["headers"]
        corr_val = next(v for k, v in headers if k == HEADER_CORRELATION_ID)
        assert corr_val == returned_id.encode("utf-8")

    def test_returns_correlation_id(self):
        producer = MagicMock()
        cid = traced_produce(
            producer,
            "test-topic",
            b"payload",
            correlation_id="corr-ret",
        )
        assert cid == "corr-ret"

    def test_preserves_existing_headers(self):
        producer = MagicMock()
        existing = [("X-Custom", b"val")]
        traced_produce(
            producer,
            "test-topic",
            b"payload",
            headers=existing,
            correlation_id="corr-test",
        )

        kwargs = producer.produce.call_args[1]
        headers = kwargs["headers"]
        assert ("X-Custom", b"val") in headers

    def test_includes_callback(self):
        producer = MagicMock()
        cb = MagicMock()
        traced_produce(
            producer,
            "test-topic",
            b"payload",
            correlation_id="corr-test",
            callback=cb,
        )

        kwargs = producer.produce.call_args[1]
        assert kwargs["callback"] is cb

    def test_sets_parent_span_from_context(self):
        producer = MagicMock()

        with TracingContext(
            correlation_id="corr-parent",
            span_id="span-parent-001",
        ):
            traced_produce(producer, "test-topic", b"payload")

        kwargs = producer.produce.call_args[1]
        headers = kwargs["headers"]
        parent_val = next((v for k, v in headers if k == HEADER_PARENT_SPAN_ID), None)
        assert parent_val == b"span-parent-001"


# ---------------------------------------------------------------------------
# Test: traced_consume
# ---------------------------------------------------------------------------


def _make_mock_message(
    headers: Optional[List[Tuple[str, bytes]]] = None,
    topic: str = "test-topic",
) -> MagicMock:
    """Create a mock Kafka message with optional headers."""
    msg = MagicMock()
    msg.headers.return_value = headers
    msg.topic.return_value = topic
    return msg


class TestTracedConsume:
    def setup_method(self):
        """Ensure no active context before each test."""
        ctx = TracingContext.current()
        if ctx:
            ctx.detach()

    def teardown_method(self):
        """Clean up any attached context after each test."""
        ctx = TracingContext.current()
        if ctx:
            ctx.detach()

    def test_extracts_existing_correlation_id(self):
        msg = _make_mock_message(headers=[(HEADER_CORRELATION_ID, b"corr-existing")])
        ctx = traced_consume(msg)
        try:
            assert ctx.correlation_id == "corr-existing"
            assert TracingContext.current() is ctx
        finally:
            ctx.detach()

    def test_generates_new_id_when_absent(self):
        msg = _make_mock_message(headers=[])
        ctx = traced_consume(msg)
        try:
            assert ctx.correlation_id.startswith("corr-")
        finally:
            ctx.detach()

    def test_generates_new_id_when_headers_none(self):
        msg = _make_mock_message(headers=None)
        ctx = traced_consume(msg)
        try:
            assert ctx.correlation_id.startswith("corr-")
        finally:
            ctx.detach()

    def test_creates_new_span_id(self):
        msg = _make_mock_message(headers=[(HEADER_CORRELATION_ID, b"corr-test")])
        ctx = traced_consume(msg)
        try:
            assert ctx.span_id.startswith("span-")
        finally:
            ctx.detach()

    def test_sets_parent_span_from_header(self):
        msg = _make_mock_message(
            headers=[
                (HEADER_CORRELATION_ID, b"corr-test"),
                (HEADER_SPAN_ID, b"span-upstream"),
            ]
        )
        ctx = traced_consume(msg)
        try:
            assert ctx.parent_span_id == "span-upstream"
        finally:
            ctx.detach()

    def test_attaches_to_thread(self):
        msg = _make_mock_message(headers=[(HEADER_CORRELATION_ID, b"corr-attach")])
        ctx = traced_consume(msg)
        try:
            assert TracingContext.current() is ctx
            assert TracingContext.current_correlation_id() == "corr-attach"
        finally:
            ctx.detach()


# ---------------------------------------------------------------------------
# Test: round-trip (inject -> extract)
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_inject_then_extract_preserves_id(self):
        original_id = "corr-roundtrip-test"
        headers = inject_correlation_id(None, original_id)
        extracted = extract_correlation_id(headers)
        assert extracted == original_id

    def test_inject_then_extract_preserves_span(self):
        headers = inject_correlation_id(None, "corr-test", span_id="span-rt")
        extracted = extract_span_id(headers)
        assert extracted == "span-rt"

    def test_produce_consume_round_trip(self):
        """Simulate a full produce -> consume round trip."""
        # Produce side
        producer = MagicMock()
        original_corr_id = "corr-full-roundtrip"

        traced_produce(
            producer,
            "test-topic",
            b"payload",
            correlation_id=original_corr_id,
        )

        # Extract headers that were passed to producer.produce()
        produce_kwargs = producer.produce.call_args[1]
        produced_headers = produce_kwargs["headers"]

        # Consume side -- create a mock message with the produced headers
        msg = _make_mock_message(headers=produced_headers)
        ctx = traced_consume(msg)

        try:
            assert ctx.correlation_id == original_corr_id
            # The consumer's parent_span should be the producer's span
            producer_span = next(
                v.decode("utf-8") for k, v in produced_headers if k == HEADER_SPAN_ID
            )
            assert ctx.parent_span_id == producer_span
        finally:
            ctx.detach()

    def test_multi_hop_trace(self):
        """Simulate a 3-hop trace: producer -> consumer1 -> consumer2."""
        producer = MagicMock()
        original_id = "corr-multihop"

        # Hop 1: Initial produce
        traced_produce(
            producer,
            "topic-1",
            b"msg1",
            correlation_id=original_id,
        )
        hop1_headers = producer.produce.call_args[1]["headers"]
        producer.reset_mock()

        # Hop 2: Consumer1 receives, then produces
        msg1 = _make_mock_message(headers=hop1_headers)
        ctx1 = traced_consume(msg1)
        try:
            assert ctx1.correlation_id == original_id

            traced_produce(producer, "topic-2", b"msg2")
            hop2_headers = producer.produce.call_args[1]["headers"]
            producer.reset_mock()
        finally:
            ctx1.detach()

        # Hop 3: Consumer2 receives
        msg2 = _make_mock_message(headers=hop2_headers)
        ctx2 = traced_consume(msg2)
        try:
            # Correlation ID is preserved across all hops
            assert ctx2.correlation_id == original_id
            # Parent span should reference consumer1's span
            assert ctx2.parent_span_id is not None
        finally:
            ctx2.detach()
