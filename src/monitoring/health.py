"""
Health check and readiness probe module for Stream-Sentinel consumers.

Provides /health (liveness), /readiness, and /health/details endpoints that
integrate with the existing Prometheus metrics HTTP server so all endpoints
are served on the same port (8000-8003 depending on the consumer).

Usage:
    from monitoring.health import HealthCheckServer, DependencyCheck

    server = HealthCheckServer(registry=metrics.registry)

    # Register dependency checks before starting
    server.register_check("kafka", kafka_check_fn)
    server.register_check("redis", redis_check_fn)

    # Start combined metrics + health HTTP server
    server.start(port=8000)
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, Optional

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

logger = logging.getLogger(__name__)

# Default timeout for individual dependency checks (seconds)
_DEFAULT_CHECK_TIMEOUT = 2.0

# How many seconds without a heartbeat before liveness declares unhealthy
_LIVENESS_STALE_THRESHOLD = 60.0


class DependencyCheck:
    """Tracks the result of a single dependency health check."""

    __slots__ = (
        "name",
        "check_fn",
        "timeout",
        "last_status",
        "last_detail",
        "last_success_time",
        "last_check_time",
        "last_error",
    )

    def __init__(
        self,
        name: str,
        check_fn: Callable[[], Dict[str, Any]],
        timeout: float = _DEFAULT_CHECK_TIMEOUT,
    ):
        self.name = name
        self.check_fn = check_fn
        self.timeout = timeout
        self.last_status: Optional[str] = None
        self.last_detail: Dict[str, Any] = {}
        self.last_success_time: Optional[float] = None
        self.last_check_time: Optional[float] = None
        self.last_error: Optional[str] = None

    def run(self) -> Dict[str, Any]:
        """Execute the check with a timeout, returning a status dict.

        The *check_fn* must return a dict with at least ``{"status": "..."}``
        where status is a short human-readable word like ``"connected"`` or
        ``"loaded"``.  Any additional keys are passed through to the response.

        On failure the returned dict has ``{"status": "error", "error": "..."}``.
        """
        result: Dict[str, Any] = {}
        error_holder: list = []

        def _target():
            try:
                result.update(self.check_fn())
            except Exception as exc:
                error_holder.append(str(exc))

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=self.timeout)

        self.last_check_time = time.time()

        if t.is_alive():
            # Timed out
            self.last_status = "timeout"
            self.last_error = f"Check timed out after {self.timeout}s"
            self.last_detail = {"status": "timeout", "error": self.last_error}
        elif error_holder:
            self.last_status = "error"
            self.last_error = error_holder[0]
            self.last_detail = {"status": "error", "error": self.last_error}
        else:
            self.last_status = result.get("status", "unknown")
            self.last_error = None
            self.last_success_time = time.time()
            self.last_detail = result

        return dict(self.last_detail)


class HealthCheckHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves Prometheus /metrics and health-check routes.

    Instance attributes ``registry`` and ``health_server`` are injected via
    the factory classmethod ``make_handler_class``.
    """

    # These are set by make_handler_class()
    registry: CollectorRegistry = None  # type: ignore[assignment]
    health_server: "HealthCheckServer" = None  # type: ignore[assignment]

    def do_GET(self):
        path = self.path.rstrip("/")

        if path == "/metrics" or path == "":
            self._serve_metrics()
        elif path == "/health":
            self._serve_health()
        elif path == "/readiness":
            self._serve_readiness()
        elif path == "/health/details":
            self._serve_details()
        else:
            self.send_error(404, "Not Found")

    def _serve_metrics(self):
        """Prometheus metrics endpoint -- mirrors default MetricsHandler."""
        output = generate_latest(self.registry)
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPE_LATEST)
        self.send_header("Content-Length", str(len(output)))
        self.end_headers()
        self.wfile.write(output)

    def _serve_health(self):
        """Liveness probe.

        Returns 200 if the consumer loop has sent a heartbeat within the
        staleness threshold, 503 otherwise.
        """
        hs = self.health_server
        now = time.time()
        uptime = now - hs.start_time

        last_hb = hs.last_heartbeat
        stale = (now - last_hb) > _LIVENESS_STALE_THRESHOLD if last_hb else False

        if stale:
            status_text = "unhealthy"
            http_code = 503
        else:
            status_text = "healthy"
            http_code = 200

        body = {
            "status": status_text,
            "uptime_seconds": round(uptime, 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._send_json(http_code, body)

    def _serve_readiness(self):
        """Readiness probe.

        Runs all registered dependency checks and returns 200 only if every
        one of them reports a non-error status.
        """
        hs = self.health_server
        checks_result = hs.run_all_checks()
        all_ok = all(v.get("status") not in ("error", "timeout") for v in checks_result.values())

        body = {
            "status": "ready" if all_ok else "not_ready",
            "checks": checks_result,
        }
        self._send_json(200 if all_ok else 503, body)

    def _serve_details(self):
        """Detailed health endpoint combining liveness, readiness, and
        a metrics summary."""
        hs = self.health_server
        now = time.time()
        uptime = now - hs.start_time

        checks_result = hs.run_all_checks()
        all_ok = all(v.get("status") not in ("error", "timeout") for v in checks_result.values())

        last_hb = hs.last_heartbeat
        stale = (now - last_hb) > _LIVENESS_STALE_THRESHOLD if last_hb else False

        if stale:
            overall = "unhealthy"
        elif not all_ok:
            overall = "degraded"
        else:
            overall = "ready"

        body = {
            "status": overall,
            "uptime_seconds": round(uptime, 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": checks_result,
            "metrics_summary": hs.get_metrics_summary(),
        }
        self._send_json(200 if overall == "ready" else 503, body)

    # -- helpers -------------------------------------------------------

    def _send_json(self, code: int, body: dict):
        payload = json.dumps(body, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):  # noqa: A002  (shadow builtin ok)
        """Suppress default stderr logging from BaseHTTPRequestHandler."""
        pass

    @classmethod
    def make_handler_class(
        cls,
        registry: CollectorRegistry,
        health_server: "HealthCheckServer",
    ):
        """Return a *new* handler subclass with the given registry and server
        bound as class-level attributes so ``HTTPServer`` can instantiate it
        without extra constructor args."""

        class _Handler(cls):
            pass

        _Handler.registry = registry
        _Handler.health_server = health_server
        return _Handler


class HealthCheckServer:
    """Manages dependency checks and runs a combined HTTP server.

    Typical lifecycle::

        server = HealthCheckServer(registry=prom_registry)
        server.register_check("kafka", my_kafka_check)
        server.start(port=8000)           # non-blocking (daemon thread)
        ...
        server.heartbeat()                # call from consumer loop
    """

    def __init__(self, registry: Optional[CollectorRegistry] = None):
        self.registry = registry or CollectorRegistry()
        self.start_time = time.time()
        self.last_heartbeat: Optional[float] = None
        self._checks: Dict[str, DependencyCheck] = {}
        self._lock = threading.Lock()
        self._http_server: Optional[HTTPServer] = None

        # Optional metrics summary callback (set by consumer)
        self._metrics_summary_fn: Optional[Callable[[], Dict[str, Any]]] = None

    # -- public API ----------------------------------------------------

    def register_check(
        self,
        name: str,
        check_fn: Callable[[], Dict[str, Any]],
        timeout: float = _DEFAULT_CHECK_TIMEOUT,
    ) -> None:
        """Register a named dependency check.

        Args:
            name: Human-readable dependency name (e.g. ``"kafka"``).
            check_fn: Callable returning ``{"status": "...", ...}``.
            timeout: Max seconds to wait for the check (default 2).
        """
        with self._lock:
            self._checks[name] = DependencyCheck(name, check_fn, timeout)
        logger.debug("Registered health check: %s", name)

    def set_metrics_summary_fn(self, fn: Callable[[], Dict[str, Any]]) -> None:
        """Set a callback that returns summary metrics for /health/details."""
        self._metrics_summary_fn = fn

    def heartbeat(self) -> None:
        """Signal that the consumer loop is alive.  Call this from the main
        processing loop on every poll iteration."""
        self.last_heartbeat = time.time()

    def run_all_checks(self) -> Dict[str, Dict[str, Any]]:
        """Run every registered check and return combined results."""
        results: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            checks = list(self._checks.values())

        for check in checks:
            results[check.name] = check.run()

        return results

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Return a summary of key metrics, if a callback is registered."""
        if self._metrics_summary_fn:
            try:
                return self._metrics_summary_fn()
            except Exception as exc:
                return {"error": str(exc)}
        return {}

    def start(self, port: int) -> None:
        """Start the combined metrics + health HTTP server in a daemon thread.

        This replaces the default ``prometheus_client.start_http_server``.
        """
        handler_cls = HealthCheckHandler.make_handler_class(
            registry=self.registry,
            health_server=self,
        )
        self._http_server = HTTPServer(("0.0.0.0", port), handler_cls)

        thread = threading.Thread(
            target=self._http_server.serve_forever,
            daemon=True,
            name=f"health-metrics-{port}",
        )
        thread.start()
        logger.info(
            "Combined health + metrics server started on port %d "
            "(endpoints: /metrics, /health, /readiness, /health/details)",
            port,
        )


# ---- Convenience check-builder functions -----------------------------
#
# These return callables suitable for ``register_check()``.  Each one is a
# closure that captures the relevant client / object reference.


def make_kafka_check(consumer) -> Callable[[], Dict[str, Any]]:
    """Build a Kafka connectivity check from a ``confluent_kafka.Consumer``.

    Returns partition assignment info when connected.
    """

    def _check() -> Dict[str, Any]:
        assignment = consumer.assignment()
        if assignment:
            return {
                "status": "connected",
                "partitions_assigned": len(assignment),
                "topics": list({tp.topic for tp in assignment}),
            }
        # Consumer is alive but has no partitions yet (rebalancing?)
        return {"status": "no_partitions", "partitions_assigned": 0}

    return _check


def make_redis_check(redis_client) -> Callable[[], Dict[str, Any]]:
    """Build a Redis connectivity check from a ``redis.Redis`` client.

    Measures round-trip latency of PING.
    """

    def _check() -> Dict[str, Any]:
        start = time.time()
        redis_client.ping()
        latency_ms = (time.time() - start) * 1000
        return {"status": "connected", "latency_ms": round(latency_ms, 2)}

    return _check


def make_model_check(detector) -> Callable[[], Dict[str, Any]]:
    """Build an ML model readiness check from a FraudDetector instance.

    Reports model_status and model type.
    """

    def _check() -> Dict[str, Any]:
        model_status = getattr(detector, "model_status", "unknown")
        if model_status == "ml_primary" and getattr(detector, "ml_model", None) is not None:
            model_type = type(detector.ml_model).__name__
            return {
                "status": "loaded",
                "model_type": model_type,
                "scoring_mode": "ml_primary",
            }
        elif model_status == "rules_fallback":
            return {
                "status": "degraded",
                "scoring_mode": "rules_fallback",
                "detail": "ML model unavailable, using rule-based scoring",
            }
        else:
            return {
                "status": "loading",
                "scoring_mode": model_status,
            }

    return _check


def make_database_check(persistence_layer) -> Callable[[], Dict[str, Any]]:
    """Build a database health check from a persistence layer instance.

    Calls the persistence layer's own ``health_check()`` method which tests
    PostgreSQL and ClickHouse connectivity.
    """

    def _check() -> Dict[str, Any]:
        health = persistence_layer.health_check()
        all_ok = all(health.values())
        components = {}
        for name, ok in health.items():
            components[name] = "connected" if ok else "error"
        return {
            "status": "connected" if all_ok else "error",
            "components": components,
        }

    return _check
