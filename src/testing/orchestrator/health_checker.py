"""
Service Health Checker for Integration Testing

Provides sophisticated health checking capabilities for distributed services
with protocol-specific validation and comprehensive diagnostics.

Key Features:
- Protocol-aware health checking (HTTP, Redis, Kafka, PostgreSQL, ClickHouse)
- Detailed diagnostic information for debugging failures
- Configurable timeout and retry policies
- Performance metrics for health check operations
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

import aiohttp
import asyncpg
import httpx
import redis.asyncio as redis
from confluent_kafka.admin import AdminClient


class HealthStatus(Enum):
    """Health check status enumeration."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Detailed health check result with diagnostics."""

    status: HealthStatus
    response_time_ms: float
    details: Dict[str, Any]
    error_message: Optional[str] = None
    timestamp: float = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


class ServiceHealthChecker:
    """
    Multi-protocol service health checker with comprehensive diagnostics.

    Supports health checking for various service types used in the fraud
    detection system with protocol-specific validation logic.
    """

    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.ServiceHealthChecker")
        self._http_session: Optional[httpx.AsyncClient] = None

    async def check_service_health(self, service_name: str, health_check_url: str, timeout: int = 30) -> HealthStatus:
        """
        Check service health with protocol detection.

        Args:
            service_name: Name of the service being checked
            health_check_url: URL or connection string for health check
            timeout: Maximum time to wait for response

        Returns:
            HealthStatus indicating the service health
        """
        start_time = time.time()

        try:
            # Determine protocol and route to appropriate checker
            if health_check_url.startswith(("http://", "https://")):
                result = await self._check_http_health(service_name, health_check_url, timeout)
            elif health_check_url.startswith("redis://"):
                result = await self._check_redis_health(service_name, health_check_url, timeout)
            elif health_check_url.startswith("postgresql://"):
                result = await self._check_postgres_health(service_name, health_check_url, timeout)
            elif "kafka" in service_name.lower() or ":9092" in health_check_url:
                result = await self._check_kafka_health(service_name, health_check_url, timeout)
            else:
                # Default to basic connectivity check
                result = await self._check_tcp_connectivity(service_name, health_check_url, timeout)

            response_time = (time.time() - start_time) * 1000

            self.logger.debug(f"Health check for {service_name}: {result.value} " f"({response_time:.1f}ms)")

            return result

        except asyncio.TimeoutError:
            self.logger.warning(f"Health check timeout for {service_name}")
            return HealthStatus.TIMEOUT
        except Exception as e:
            self.logger.error(f"Health check error for {service_name}: {e}")
            return HealthStatus.UNHEALTHY

    async def _check_http_health(self, service_name: str, url: str, timeout: int) -> HealthStatus:
        """Check HTTP/HTTPS service health."""
        if self._http_session is None:
            self._http_session = httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True)

        try:
            response = await self._http_session.get(url)

            if 200 <= response.status_code < 300:
                return HealthStatus.HEALTHY
            elif 400 <= response.status_code < 500:
                # Client errors might indicate service is up but misconfigured
                self.logger.warning(f"HTTP health check for {service_name} returned {response.status_code}")
                return HealthStatus.UNHEALTHY
            else:
                return HealthStatus.UNHEALTHY

        except httpx.ConnectError:
            return HealthStatus.UNHEALTHY
        except httpx.TimeoutException:
            return HealthStatus.TIMEOUT
        except Exception as e:
            self.logger.error(f"HTTP health check failed for {service_name}: {e}")
            return HealthStatus.UNHEALTHY

    async def _check_redis_health(self, service_name: str, connection_string: str, timeout: int) -> HealthStatus:
        """Check Redis service health."""
        try:
            # Parse Redis connection string
            redis_client = redis.from_url(
                connection_string,
                socket_connect_timeout=timeout,
                socket_timeout=timeout,
            )

            # Perform basic ping operation
            result = await asyncio.wait_for(redis_client.ping(), timeout=timeout)

            await redis_client.close()

            if result:
                return HealthStatus.HEALTHY
            else:
                return HealthStatus.UNHEALTHY

        except asyncio.TimeoutError:
            return HealthStatus.TIMEOUT
        except Exception as e:
            self.logger.error(f"Redis health check failed for {service_name}: {e}")
            return HealthStatus.UNHEALTHY

    async def _check_postgres_health(self, service_name: str, connection_string: str, timeout: int) -> HealthStatus:
        """Check PostgreSQL service health."""
        try:
            # Create connection with timeout
            conn = await asyncio.wait_for(asyncpg.connect(connection_string), timeout=timeout)

            # Perform simple query
            result = await conn.fetchval("SELECT 1")
            await conn.close()

            if result == 1:
                return HealthStatus.HEALTHY
            else:
                return HealthStatus.UNHEALTHY

        except asyncio.TimeoutError:
            return HealthStatus.TIMEOUT
        except asyncpg.InvalidCatalogNameError:
            # Database doesn't exist yet, but server is running
            self.logger.info(f"PostgreSQL server running but database not ready for {service_name}")
            return HealthStatus.UNHEALTHY
        except Exception as e:
            self.logger.error(f"PostgreSQL health check failed for {service_name}: {e}")
            return HealthStatus.UNHEALTHY

    async def _check_kafka_health(self, service_name: str, bootstrap_servers: str, timeout: int) -> HealthStatus:
        """Check Kafka service health."""
        try:
            # Create admin client for cluster metadata
            admin_client = AdminClient(
                {
                    "bootstrap.servers": bootstrap_servers,
                    "socket.timeout.ms": timeout * 1000,
                    "api.version.request.timeout.ms": timeout * 1000,
                }
            )

            # Get cluster metadata - this will fail if Kafka is not ready
            metadata = admin_client.list_topics(timeout=timeout)

            if metadata and metadata.topics is not None:
                return HealthStatus.HEALTHY
            else:
                return HealthStatus.UNHEALTHY

        except Exception as e:
            # Kafka client exceptions can be varied, log for debugging
            self.logger.debug(f"Kafka health check failed for {service_name}: {e}")
            return HealthStatus.UNHEALTHY

    async def _check_tcp_connectivity(self, service_name: str, address: str, timeout: int) -> HealthStatus:
        """Check basic TCP connectivity for services without specific protocols."""
        try:
            # Parse host:port from address
            if ":" in address:
                host, port_str = address.rsplit(":", 1)
                port = int(port_str)
            else:
                # Default to common service ports
                host = address
                if "redis" in service_name.lower():
                    port = 6379
                elif "postgres" in service_name.lower():
                    port = 5432
                elif "kafka" in service_name.lower():
                    port = 9092
                else:
                    port = 80

            # Attempt TCP connection
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)

            writer.close()
            await writer.wait_closed()

            return HealthStatus.HEALTHY

        except asyncio.TimeoutError:
            return HealthStatus.TIMEOUT
        except Exception as e:
            self.logger.debug(f"TCP connectivity check failed for {service_name}: {e}")
            return HealthStatus.UNHEALTHY

    async def get_detailed_health_info(
        self, service_name: str, health_check_url: str, timeout: int = 30
    ) -> HealthCheckResult:
        """
        Get detailed health information including diagnostics.

        Returns comprehensive health check result with timing and diagnostic data.
        """
        start_time = time.time()

        try:
            status = await self.check_service_health(service_name, health_check_url, timeout)
            response_time = (time.time() - start_time) * 1000

            # Collect additional diagnostic information based on service type
            details = await self._collect_service_diagnostics(service_name, health_check_url, timeout)

            return HealthCheckResult(
                status=status,
                response_time_ms=response_time,
                details=details,
                timestamp=time.time(),
            )

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                response_time_ms=response_time,
                details={},
                error_message=str(e),
                timestamp=time.time(),
            )

    async def _collect_service_diagnostics(
        self, service_name: str, health_check_url: str, timeout: int
    ) -> Dict[str, Any]:
        """Collect service-specific diagnostic information."""
        diagnostics = {
            "service_name": service_name,
            "check_url": health_check_url,
            "timestamp": time.time(),
        }

        try:
            if "redis" in service_name.lower():
                diagnostics.update(await self._get_redis_diagnostics(health_check_url, timeout))
            elif "kafka" in service_name.lower():
                diagnostics.update(await self._get_kafka_diagnostics(health_check_url, timeout))
            elif "postgres" in service_name.lower():
                diagnostics.update(await self._get_postgres_diagnostics(health_check_url, timeout))
            elif health_check_url.startswith("http"):
                diagnostics.update(await self._get_http_diagnostics(health_check_url, timeout))

        except Exception as e:
            diagnostics["diagnostic_error"] = str(e)

        return diagnostics

    async def _get_redis_diagnostics(self, connection_string: str, timeout: int) -> Dict[str, Any]:
        """Get Redis-specific diagnostic information."""
        try:
            redis_client = redis.from_url(connection_string, socket_timeout=timeout)

            info = await redis_client.info()
            await redis_client.close()

            return {
                "redis_version": info.get("redis_version"),
                "connected_clients": info.get("connected_clients", 0),
                "used_memory": info.get("used_memory", 0),
                "uptime_in_seconds": info.get("uptime_in_seconds", 0),
            }
        except Exception:
            return {"redis_diagnostics_failed": True}

    async def _get_kafka_diagnostics(self, bootstrap_servers: str, timeout: int) -> Dict[str, Any]:
        """Get Kafka-specific diagnostic information."""
        try:
            admin_client = AdminClient(
                {
                    "bootstrap.servers": bootstrap_servers,
                    "socket.timeout.ms": timeout * 1000,
                }
            )

            metadata = admin_client.list_topics(timeout=timeout)

            return {
                "broker_count": len(metadata.brokers) if metadata.brokers else 0,
                "topic_count": len(metadata.topics) if metadata.topics else 0,
                "cluster_id": getattr(metadata, "cluster_id", "unknown"),
            }
        except Exception:
            return {"kafka_diagnostics_failed": True}

    async def _get_postgres_diagnostics(self, connection_string: str, timeout: int) -> Dict[str, Any]:
        """Get PostgreSQL-specific diagnostic information."""
        try:
            conn = await asyncio.wait_for(asyncpg.connect(connection_string), timeout=timeout)

            version = await conn.fetchval("SELECT version()")
            active_connections = await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")

            await conn.close()

            return {
                "postgres_version": version.split(" ")[1] if version else "unknown",
                "active_connections": active_connections or 0,
            }
        except Exception:
            return {"postgres_diagnostics_failed": True}

    async def _get_http_diagnostics(self, url: str, timeout: int) -> Dict[str, Any]:
        """Get HTTP service diagnostic information."""
        try:
            if self._http_session is None:
                self._http_session = httpx.AsyncClient(timeout=httpx.Timeout(timeout))

            response = await self._http_session.get(url)

            return {
                "status_code": response.status_code,
                "response_headers": dict(response.headers),
                "content_type": response.headers.get("content-type", "unknown"),
            }
        except Exception:
            return {"http_diagnostics_failed": True}

    async def close(self):
        """Clean up resources."""
        if self._http_session:
            await self._http_session.aclose()
            self._http_session = None
