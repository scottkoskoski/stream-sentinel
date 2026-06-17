"""
Health-check HTTP endpoint for Stream-Sentinel consumers.

Provides /health (liveness), /health/ready (readiness), and
/health/details (verbose dependency status) endpoints.

Key design decisions:
  - ThreadingHTTPServer so metrics scrapes are never blocked by a slow
    health check (W6).
  - Dependency checks run in parallel via ThreadPoolExecutor (W6).
  - Bind address configurable via HEALTH_BIND_ADDRESS env var (C5).
  - /health/details can be disabled via HEALTH_DETAILS_ENABLED (C5).
  - Startup grace: if no heartbeat has been recorded and uptime exceeds
    HEALTH_STARTUP_GRACE_SECONDS (default 30), liveness returns 503 to
    signal the orchestrator that the consumer loop has not started (W7).
"""

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
HEALTH_BIND_ADDRESS: str = os.environ.get(
    "HEALTH_BIND_ADDRESS", "0.0.0.0"
)  # nosec B104 - intentional container health bind; address is env-configurable via HEALTH_BIND_ADDRESS
HEALTH_DETAILS_ENABLED: bool = os.environ.get("HEALTH_DETAILS_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
HEALTH_STARTUP_GRACE_SECONDS: float = float(os.environ.get("HEALTH_STARTUP_GRACE_SECONDS", "30"))


# ---------------------------------------------------------------------------
# Health-check registry
# ---------------------------------------------------------------------------
class HealthCheckRegistry:
    """Thread-safe registry of named health-check callables.

    Each check is a ``() -> dict`` returning at minimum ``{"healthy": bool}``.
    Checks are executed in parallel by ``run_all_checks``.
    """

    def __init__(self) -> None:
        self._checks: Dict[str, Callable[[], Dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._start_time: float = time.monotonic()
        self._last_heartbeat: Optional[float] = None

    # -- heartbeat bookkeeping (W7) -----------------------------------------
    def record_heartbeat(self) -> None:
        """Called by the consumer loop to indicate it is actively processing."""
        self._last_heartbeat = time.monotonic()

    @property
    def last_heartbeat(self) -> Optional[float]:
        return self._last_heartbeat

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self._start_time

    # -- check management ---------------------------------------------------
    def register(self, name: str, check: Callable[[], Dict[str, Any]]) -> None:
        with self._lock:
            self._checks[name] = check

    def deregister(self, name: str) -> None:
        with self._lock:
            self._checks.pop(name, None)

    def run_all_checks(self, timeout: float = 5.0) -> Dict[str, Dict[str, Any]]:
        """Execute all registered checks in parallel and return results.

        Each check runs in its own thread (via ``ThreadPoolExecutor``)
        so a slow Redis ping, for example, does not delay the Kafka or
        PostgreSQL checks.
        """
        with self._lock:
            checks_snapshot = dict(self._checks)

        results: Dict[str, Dict[str, Any]] = {}

        if not checks_snapshot:
            return results

        with ThreadPoolExecutor(max_workers=min(len(checks_snapshot), 8)) as pool:
            future_to_name = {pool.submit(fn): name for name, fn in checks_snapshot.items()}
            for future in as_completed(future_to_name, timeout=timeout):
                name = future_to_name[future]
                try:
                    results[name] = future.result(timeout=0)
                except Exception as exc:
                    results[name] = {"healthy": False, "error": str(exc)}

        # Any check that did not complete within the deadline
        for name in checks_snapshot:
            if name not in results:
                results[name] = {"healthy": False, "error": "timeout"}

        return results

    def is_live(self) -> bool:
        """Liveness probe logic.

        Returns ``False`` if no heartbeat has been recorded and the
        startup grace period has elapsed (W7).
        """
        if self._last_heartbeat is None:
            return self.uptime_seconds <= HEALTH_STARTUP_GRACE_SECONDS
        return True

    def is_ready(self) -> bool:
        """Readiness probe: all registered checks must pass."""
        results = self.run_all_checks()
        return all(r.get("healthy", False) for r in results.values())


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for health endpoints."""

    # Silence default stderr logging from BaseHTTPRequestHandler
    def log_message(self, format, *args):  # noqa: A002
        logger.debug(format, *args)

    def _send_json(self, status_code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        registry: HealthCheckRegistry = self.server.health_registry  # type: ignore[attr-defined]

        if self.path == "/health":
            if registry.is_live():
                self._send_json(200, {"status": "ok"})
            else:
                self._send_json(
                    503,
                    {
                        "status": "unhealthy",
                        "reason": "no heartbeat received within startup grace period",
                    },
                )

        elif self.path == "/health/ready":
            if registry.is_ready():
                self._send_json(200, {"status": "ready"})
            else:
                self._send_json(503, {"status": "not_ready"})

        elif self.path == "/health/details":
            if not HEALTH_DETAILS_ENABLED:
                self._send_json(403, {"status": "disabled"})
                return
            results = registry.run_all_checks()
            overall = all(r.get("healthy", False) for r in results.values())
            code = 200 if overall else 503
            self._send_json(
                code,
                {
                    "status": "healthy" if overall else "unhealthy",
                    "uptime_seconds": round(registry.uptime_seconds, 1),
                    "last_heartbeat_ago_seconds": (
                        round(time.monotonic() - registry.last_heartbeat, 1)
                        if registry.last_heartbeat is not None
                        else None
                    ),
                    "checks": results,
                },
            )

        else:
            self._send_json(404, {"error": "not found"})


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------
def start_health_server(
    registry: HealthCheckRegistry,
    port: int = 8080,
) -> ThreadingHTTPServer:
    """Start the health-check HTTP server in a daemon thread.

    Returns the server instance so callers can shut it down if needed.
    """
    bind_address = HEALTH_BIND_ADDRESS

    server = ThreadingHTTPServer((bind_address, port), _HealthHandler)
    server.health_registry = registry  # type: ignore[attr-defined]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    logger.info("Health-check server listening on %s:%d", bind_address, port)
    return server
