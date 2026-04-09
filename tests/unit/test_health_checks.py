"""
Unit tests for src/monitoring/health.py

Covers:
  - HealthCheckRegistry basic registration and execution
  - Parallel check execution (W6)
  - Startup grace period / liveness logic (W7)
  - HTTP endpoints: /health, /health/ready, /health/details
  - HEALTH_DETAILS_ENABLED=false returning 403 (C5)
"""

import json
import os
import time
import threading
import pytest
from unittest.mock import patch
from urllib.request import urlopen, Request
from urllib.error import HTTPError

import sys
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from monitoring.health import (
    HealthCheckRegistry,
    start_health_server,
    HEALTH_STARTUP_GRACE_SECONDS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _find_free_port():
    """Find an available TCP port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def registry():
    return HealthCheckRegistry()


@pytest.fixture()
def health_server(registry):
    """Start a health server on a random port and tear it down after test."""
    port = _find_free_port()
    server = start_health_server(registry, port=port)
    yield f"http://127.0.0.1:{port}", registry
    server.shutdown()


# ---------------------------------------------------------------------------
# HealthCheckRegistry unit tests
# ---------------------------------------------------------------------------

class TestHealthCheckRegistry:

    def test_register_and_run_check(self, registry):
        registry.register("dummy", lambda: {"healthy": True})
        results = registry.run_all_checks()
        assert "dummy" in results
        assert results["dummy"]["healthy"] is True

    def test_deregister_removes_check(self, registry):
        registry.register("tmp", lambda: {"healthy": True})
        registry.deregister("tmp")
        assert registry.run_all_checks() == {}

    def test_failing_check_captured(self, registry):
        def bad_check():
            raise RuntimeError("boom")

        registry.register("bad", bad_check)
        results = registry.run_all_checks()
        assert results["bad"]["healthy"] is False
        assert "boom" in results["bad"]["error"]

    def test_parallel_execution_faster_than_serial(self, registry):
        """Two checks each sleeping 0.2s should complete in ~0.2s, not ~0.4s."""
        registry.register("slow_a", lambda: (time.sleep(0.2) or {"healthy": True}))
        registry.register("slow_b", lambda: (time.sleep(0.2) or {"healthy": True}))

        start = time.monotonic()
        results = registry.run_all_checks(timeout=2.0)
        elapsed = time.monotonic() - start

        assert results["slow_a"]["healthy"] is True
        assert results["slow_b"]["healthy"] is True
        assert elapsed < 0.4, f"Checks ran serially ({elapsed:.2f}s)"

    def test_is_live_true_within_grace_period(self, registry):
        """Before grace period and with no heartbeat, liveness is True."""
        assert registry.is_live() is True

    def test_is_live_false_after_grace_no_heartbeat(self, registry):
        """After grace period without heartbeat, liveness is False."""
        # Simulate elapsed time by backdating start_time
        registry._start_time = time.monotonic() - (HEALTH_STARTUP_GRACE_SECONDS + 5)
        assert registry.is_live() is False

    def test_is_live_true_with_heartbeat(self, registry):
        """Once a heartbeat is recorded, liveness is always True."""
        registry._start_time = time.monotonic() - 120  # well past grace
        registry.record_heartbeat()
        assert registry.is_live() is True

    def test_is_ready_all_pass(self, registry):
        registry.register("a", lambda: {"healthy": True})
        registry.register("b", lambda: {"healthy": True})
        assert registry.is_ready() is True

    def test_is_ready_one_fails(self, registry):
        registry.register("ok", lambda: {"healthy": True})
        registry.register("fail", lambda: {"healthy": False})
        assert registry.is_ready() is False


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

class TestHealthEndpoints:

    def _get(self, base_url, path):
        """HTTP GET helper that returns (status_code, parsed_json)."""
        try:
            resp = urlopen(f"{base_url}{path}", timeout=5)
            return resp.status, json.loads(resp.read())
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def test_health_returns_ok(self, health_server):
        base_url, registry = health_server
        registry.record_heartbeat()
        code, body = self._get(base_url, "/health")
        assert code == 200
        assert body["status"] == "ok"

    def test_health_returns_503_after_grace(self, health_server):
        base_url, registry = health_server
        registry._start_time = time.monotonic() - (HEALTH_STARTUP_GRACE_SECONDS + 5)
        code, body = self._get(base_url, "/health")
        assert code == 503
        assert body["status"] == "unhealthy"

    def test_health_ready_passes(self, health_server):
        base_url, registry = health_server
        registry.register("ok", lambda: {"healthy": True})
        code, body = self._get(base_url, "/health/ready")
        assert code == 200

    def test_health_ready_fails(self, health_server):
        base_url, registry = health_server
        registry.register("bad", lambda: {"healthy": False})
        code, body = self._get(base_url, "/health/ready")
        assert code == 503

    def test_health_details(self, health_server):
        base_url, registry = health_server
        registry.register("kafka", lambda: {"healthy": True, "lag": 42})
        registry.record_heartbeat()
        code, body = self._get(base_url, "/health/details")
        assert code == 200
        assert body["status"] == "healthy"
        assert "kafka" in body["checks"]
        assert "uptime_seconds" in body
        assert "last_heartbeat_ago_seconds" in body

    @patch.dict(os.environ, {"HEALTH_DETAILS_ENABLED": "false"})
    def test_health_details_disabled(self):
        """When HEALTH_DETAILS_ENABLED=false, /health/details returns 403."""
        # Re-import to pick up patched env var
        import importlib
        import monitoring.health as health_mod

        importlib.reload(health_mod)
        try:
            reg = health_mod.HealthCheckRegistry()
            port = _find_free_port()
            server = health_mod.start_health_server(reg, port=port)
            try:
                try:
                    resp = urlopen(f"http://127.0.0.1:{port}/health/details", timeout=5)
                    code = resp.status
                    body = json.loads(resp.read())
                except HTTPError as e:
                    code = e.code
                    body = json.loads(e.read())

                assert code == 403
                assert body["status"] == "disabled"
            finally:
                server.shutdown()
        finally:
            # Restore module defaults
            importlib.reload(health_mod)

    def test_unknown_path_404(self, health_server):
        base_url, registry = health_server
        code, body = self._get(base_url, "/nonexistent")
        assert code == 404
