"""
Unit Tests for Health Check and Readiness Probe Module

Tests the health check server, dependency checks, HTTP handler routing,
and the convenience check-builder functions without requiring live
infrastructure (Kafka, Redis, etc.).
"""

import json
import sys
import time
import threading
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from monitoring.health import (
    DependencyCheck,
    HealthCheckHandler,
    HealthCheckServer,
    make_kafka_check,
    make_redis_check,
    make_model_check,
    make_database_check,
)


# ---------------------------------------------------------------------------
# DependencyCheck
# ---------------------------------------------------------------------------


class TestDependencyCheck:
    """Tests for individual dependency check execution."""

    def test_successful_check(self):
        def _ok():
            return {"status": "connected", "latency_ms": 1.5}

        check = DependencyCheck("redis", _ok)
        result = check.run()

        assert result["status"] == "connected"
        assert result["latency_ms"] == 1.5
        assert check.last_error is None
        assert check.last_success_time is not None

    def test_failing_check(self):
        def _fail():
            raise ConnectionError("Connection refused")

        check = DependencyCheck("redis", _fail)
        result = check.run()

        assert result["status"] == "error"
        assert "Connection refused" in result["error"]
        assert check.last_error is not None
        assert check.last_success_time is None

    def test_timeout_check(self):
        def _slow():
            time.sleep(5)
            return {"status": "connected"}

        check = DependencyCheck("slow_dep", _slow, timeout=0.1)
        result = check.run()

        assert result["status"] == "timeout"
        assert "timed out" in result["error"].lower()

    def test_tracks_last_check_time(self):
        before = time.time()

        check = DependencyCheck("test", lambda: {"status": "ok"})
        check.run()

        assert check.last_check_time is not None
        assert check.last_check_time >= before


# ---------------------------------------------------------------------------
# HealthCheckServer
# ---------------------------------------------------------------------------


class TestHealthCheckServer:
    """Tests for the server-side check registry and aggregation."""

    def test_register_and_run_checks(self):
        server = HealthCheckServer()
        server.register_check("dep_a", lambda: {"status": "ok"})
        server.register_check("dep_b", lambda: {"status": "connected"})

        results = server.run_all_checks()

        assert "dep_a" in results
        assert "dep_b" in results
        assert results["dep_a"]["status"] == "ok"
        assert results["dep_b"]["status"] == "connected"

    def test_heartbeat_updates_timestamp(self):
        server = HealthCheckServer()
        assert server.last_heartbeat is None

        server.heartbeat()
        assert server.last_heartbeat is not None
        assert server.last_heartbeat <= time.time()

    def test_metrics_summary_fn(self):
        server = HealthCheckServer()
        server.set_metrics_summary_fn(lambda: {"processed": 42})

        summary = server.get_metrics_summary()
        assert summary["processed"] == 42

    def test_metrics_summary_fn_not_set(self):
        server = HealthCheckServer()
        assert server.get_metrics_summary() == {}

    def test_metrics_summary_fn_error_handled(self):
        server = HealthCheckServer()
        server.set_metrics_summary_fn(lambda: 1 / 0)

        summary = server.get_metrics_summary()
        assert "error" in summary

    def test_mixed_check_results(self):
        server = HealthCheckServer()
        server.register_check("good", lambda: {"status": "connected"})
        server.register_check("bad", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        results = server.run_all_checks()
        assert results["good"]["status"] == "connected"
        assert results["bad"]["status"] == "error"


# ---------------------------------------------------------------------------
# HTTP endpoints (integration-style via real HTTP server)
# ---------------------------------------------------------------------------


class TestHealthHTTPEndpoints:
    """Tests the combined HTTP server by starting it on a random port."""

    @pytest.fixture(autouse=True)
    def _start_server(self):
        """Start a real HTTP server on an ephemeral port for each test."""
        # Use a mock registry so we don't need prometheus_client installed
        mock_registry = MagicMock()
        mock_registry.__iter__ = MagicMock(return_value=iter([]))

        self.server = HealthCheckServer(registry=mock_registry)
        self.server.register_check("test_dep", lambda: {"status": "connected"})
        self.server.heartbeat()

        # Patch generate_latest to avoid needing real prometheus_client
        with patch("monitoring.health.generate_latest", return_value=b"# HELP fake\n"):
            with patch("monitoring.health.CONTENT_TYPE_LATEST", "text/plain"):
                # Find a free port
                import socket

                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(("127.0.0.1", 0))
                self.port = sock.getsockname()[1]
                sock.close()

                self.server.start(self.port)
                # Give the server thread a moment to bind
                time.sleep(0.1)

                yield

    def _get(self, path: str) -> tuple:
        """Make a GET request and return (status_code, parsed_json_or_bytes)."""
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            resp = urllib.request.urlopen(url, timeout=3)
            body = resp.read()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return e.code, json.loads(body)
            except json.JSONDecodeError:
                return e.code, body

    def test_health_endpoint_returns_200(self):
        code, body = self._get("/health")
        assert code == 200
        assert body["status"] == "healthy"
        assert "uptime_seconds" in body
        assert "timestamp" in body

    def test_health_endpoint_unhealthy_when_stale(self):
        # Set heartbeat far in the past
        self.server.last_heartbeat = time.time() - 120
        code, body = self._get("/health")
        assert code == 503
        assert body["status"] == "unhealthy"

    def test_readiness_endpoint_ready(self):
        code, body = self._get("/readiness")
        assert code == 200
        assert body["status"] == "ready"
        assert "test_dep" in body["checks"]

    def test_readiness_endpoint_not_ready(self):
        self.server.register_check(
            "broken", lambda: (_ for _ in ()).throw(RuntimeError("down"))
        )
        code, body = self._get("/readiness")
        assert code == 503
        assert body["status"] == "not_ready"
        assert body["checks"]["broken"]["status"] == "error"

    def test_details_endpoint(self):
        self.server.set_metrics_summary_fn(lambda: {"msgs": 100})
        code, body = self._get("/health/details")
        assert code == 200
        assert body["status"] == "ready"
        assert "checks" in body
        assert body["metrics_summary"]["msgs"] == 100

    def test_metrics_endpoint(self):
        with patch("monitoring.health.generate_latest", return_value=b"# metrics\n"):
            with patch("monitoring.health.CONTENT_TYPE_LATEST", "text/plain"):
                code, body = self._get("/metrics")
                assert code == 200

    def test_404_for_unknown_path(self):
        code, _ = self._get("/unknown")
        assert code == 404


# ---------------------------------------------------------------------------
# Convenience check builders
# ---------------------------------------------------------------------------


class TestKafkaCheck:
    """Tests for make_kafka_check."""

    def test_connected_with_partitions(self):
        consumer = MagicMock()
        tp1 = MagicMock()
        tp1.topic = "transactions"
        tp2 = MagicMock()
        tp2.topic = "transactions"
        consumer.assignment.return_value = [tp1, tp2]

        check = make_kafka_check(consumer)
        result = check()

        assert result["status"] == "connected"
        assert result["partitions_assigned"] == 2

    def test_no_partitions(self):
        consumer = MagicMock()
        consumer.assignment.return_value = []

        check = make_kafka_check(consumer)
        result = check()

        assert result["status"] == "no_partitions"
        assert result["partitions_assigned"] == 0


class TestRedisCheck:
    """Tests for make_redis_check."""

    def test_connected(self):
        redis_client = MagicMock()
        redis_client.ping.return_value = True

        check = make_redis_check(redis_client)
        result = check()

        assert result["status"] == "connected"
        assert "latency_ms" in result

    def test_connection_error(self):
        redis_client = MagicMock()
        redis_client.ping.side_effect = ConnectionError("refused")

        check = make_redis_check(redis_client)
        with pytest.raises(ConnectionError):
            check()


class TestModelCheck:
    """Tests for make_model_check."""

    def test_ml_primary(self):
        detector = MagicMock()
        detector.model_status = "ml_primary"
        detector.ml_model = MagicMock()
        type(detector.ml_model).__name__ = "XGBClassifier"

        check = make_model_check(detector)
        result = check()

        assert result["status"] == "loaded"
        assert result["scoring_mode"] == "ml_primary"

    def test_rules_fallback(self):
        detector = MagicMock()
        detector.model_status = "rules_fallback"
        detector.ml_model = None

        check = make_model_check(detector)
        result = check()

        assert result["status"] == "degraded"
        assert result["scoring_mode"] == "rules_fallback"

    def test_loading(self):
        detector = MagicMock()
        detector.model_status = "loading"
        detector.ml_model = None

        check = make_model_check(detector)
        result = check()

        assert result["status"] == "loading"


class TestDatabaseCheck:
    """Tests for make_database_check."""

    def test_all_healthy(self):
        persistence = MagicMock()
        persistence.health_check.return_value = {
            "postgresql": True,
            "clickhouse": True,
        }

        check = make_database_check(persistence)
        result = check()

        assert result["status"] == "connected"
        assert result["components"]["postgresql"] == "connected"
        assert result["components"]["clickhouse"] == "connected"

    def test_partial_failure(self):
        persistence = MagicMock()
        persistence.health_check.return_value = {
            "postgresql": True,
            "clickhouse": False,
        }

        check = make_database_check(persistence)
        result = check()

        assert result["status"] == "error"
        assert result["components"]["clickhouse"] == "error"
