"""
Service Orchestrator for Multi-Service Integration Testing

Production-grade service lifecycle management with Docker Compose integration.
Handles dependency-aware startup, health checking, and graceful shutdown.

Key Features:
- Dependency-aware service startup ordering
- Exponential backoff health checking with detailed diagnostics
- Resource isolation and cleanup guarantees
- Comprehensive error handling and recovery
- Performance monitoring and SLA validation
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

import docker

from ..utils.test_config import IntegrationTestConfig
from .dependency_manager import ServiceDependencyManager
from .health_checker import HealthStatus, ServiceHealthChecker


class ServiceState(Enum):
    """Service lifecycle states."""

    STOPPED = "stopped"
    STARTING = "starting"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"
    FAILED = "failed"


class ServiceProfile(Enum):
    """Pre-defined service profiles for different testing scenarios."""

    FAST_INTEGRATION = "fast_integration"  # Embedded services for development
    FULL_INTEGRATION = "full_integration"  # Docker Compose for CI/CD
    PERFORMANCE_TESTING = "performance"  # Full stack with monitoring
    CHAOS_TESTING = "chaos"  # Enhanced fault injection


@dataclass
class ServiceInfo:
    """Service information and runtime state."""

    name: str
    container_name: str
    health_check_url: str
    dependencies: List[str] = field(default_factory=list)
    startup_timeout: int = 120
    health_check_timeout: int = 30
    required_ports: List[int] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)

    # Runtime state
    state: ServiceState = ServiceState.STOPPED
    container_id: Optional[str] = None
    startup_time: Optional[float] = None
    last_health_check: Optional[float] = None
    health_check_attempts: int = 0


class HealthCheckError(Exception):
    """Service health check failure."""

    def __init__(self, service_name: str, details: str, attempts: int = 0):
        self.service_name = service_name
        self.details = details
        self.attempts = attempts
        super().__init__(f"Health check failed for {service_name} after {attempts} attempts: {details}")


class ServiceOrchestrator:
    """
    Production-grade service orchestrator for distributed system testing.

    Manages Docker Compose service lifecycle with dependency resolution,
    health checking, and comprehensive error handling.
    """

    def __init__(self, config: IntegrationTestConfig):
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.ServiceOrchestrator")

        # Service management
        self.services: Dict[str, ServiceInfo] = {}
        self.dependency_manager = ServiceDependencyManager()
        self.health_checker = ServiceHealthChecker()

        # Docker integration
        self.docker_client = docker.from_env()
        self.compose_project_name = f"stream-sentinel-test-{int(time.time())}"

        # State management
        self.startup_order: List[str] = []
        self.running_services: Set[str] = set()

        # Performance monitoring
        self.startup_metrics: Dict[str, Dict[str, float]] = {}

        self._initialize_service_definitions()

    def _initialize_service_definitions(self):
        """Initialize service definitions based on configuration."""
        try:
            # Core infrastructure services
            self.services["redis"] = ServiceInfo(
                name="redis",
                container_name=f"{self.compose_project_name}_redis_1",
                health_check_url="redis://localhost:6379",
                dependencies=[],
                startup_timeout=60,
                required_ports=[6379],
            )

            self.services["kafka"] = ServiceInfo(
                name="kafka",
                container_name=f"{self.compose_project_name}_kafka_1",
                health_check_url="localhost:9092",
                dependencies=["zookeeper"],
                startup_timeout=120,
                required_ports=[9092],
            )

            self.services["zookeeper"] = ServiceInfo(
                name="zookeeper",
                container_name=f"{self.compose_project_name}_zookeeper_1",
                health_check_url="localhost:2181",
                dependencies=[],
                startup_timeout=60,
                required_ports=[2181],
            )

            self.services["postgres"] = ServiceInfo(
                name="postgres",
                container_name=f"{self.compose_project_name}_postgres_1",
                health_check_url="postgresql://test_user:test_pass@localhost:5432/test_db",
                dependencies=[],
                startup_timeout=90,
                required_ports=[5432],
                environment={
                    "POSTGRES_DB": "test_db",
                    "POSTGRES_USER": "test_user",
                    "POSTGRES_PASSWORD": "test_pass",
                },
            )

            self.services["clickhouse"] = ServiceInfo(
                name="clickhouse",
                container_name=f"{self.compose_project_name}_clickhouse_1",
                health_check_url="http://localhost:8123/ping",
                dependencies=[],
                startup_timeout=90,
                required_ports=[8123, 9000],
            )

            # Application services
            self.services["fraud_detector"] = ServiceInfo(
                name="fraud_detector",
                container_name=f"{self.compose_project_name}_fraud_detector_1",
                health_check_url="http://localhost:8000/health",
                dependencies=["kafka", "redis", "postgres"],
                startup_timeout=180,
                required_ports=[8000],
            )

            self.services["alert_processor"] = ServiceInfo(
                name="alert_processor",
                container_name=f"{self.compose_project_name}_alert_processor_1",
                health_check_url="http://localhost:8001/health",
                dependencies=["kafka", "redis", "postgres"],
                startup_timeout=120,
                required_ports=[8001],
            )

            # Resolve dependency order
            self.startup_order = self.dependency_manager.resolve_startup_order(
                {name: info.dependencies for name, info in self.services.items()}
            )

            self.logger.info(f"Initialized {len(self.services)} service definitions")
            self.logger.debug(f"Startup order: {' -> '.join(self.startup_order)}")

        except Exception as e:
            self.logger.error(f"Failed to initialize service definitions: {e}")
            raise HealthCheckError("orchestrator", f"Initialization failed: {e}")

    async def start_services(self, profile: ServiceProfile) -> Dict[str, ServiceInfo]:
        """
        Start services according to the specified profile.

        Args:
            profile: Service profile defining which services to start

        Returns:
            Dict mapping service names to their runtime information

        Raises:
            HealthCheckError: If service startup or health checking fails
        """
        services_to_start = self._get_services_for_profile(profile)
        self.logger.info(f"Starting services for profile {profile.value}: {services_to_start}")

        startup_start_time = time.time()

        try:
            # Generate Docker Compose configuration
            compose_config = self._generate_compose_config(services_to_start, profile)
            compose_file_path = await self._write_compose_file(compose_config)

            # Start services in dependency order
            for service_name in self.startup_order:
                if service_name not in services_to_start:
                    continue

                _ = self.services[service_name]
                await self._start_single_service(service_name, compose_file_path, profile)

            # Wait for all services to be healthy
            await self._wait_for_all_services_healthy(services_to_start)

            # Record startup metrics
            total_startup_time = time.time() - startup_start_time
            self.startup_metrics[profile.value] = {
                "total_startup_time": total_startup_time,
                "services_started": len(services_to_start),
                "timestamp": time.time(),
            }

            self.logger.info(f"Successfully started {len(services_to_start)} services in {total_startup_time:.2f}s")

            return {name: self.services[name] for name in services_to_start}

        except Exception as e:
            self.logger.error(f"Service startup failed: {e}")
            await self._cleanup_failed_startup(services_to_start)
            raise HealthCheckError("orchestrator", f"Service startup failed: {e}")

    async def _start_single_service(self, service_name: str, compose_file: Path, profile: ServiceProfile):
        """Start a single service with comprehensive error handling."""
        service_info = self.services[service_name]
        service_start_time = time.time()

        try:
            self.logger.debug(f"Starting service: {service_name}")
            service_info.state = ServiceState.STARTING

            # Start the container using Docker Compose
            await self._docker_compose_up(compose_file, service_name)

            # Wait for container to be running
            container = await self._wait_for_container(service_info.container_name, timeout=30)
            service_info.container_id = container.id

            # Perform health check with exponential backoff
            await self._wait_for_service_healthy(service_name)

            service_info.state = ServiceState.HEALTHY
            service_info.startup_time = time.time() - service_start_time
            self.running_services.add(service_name)

            self.logger.info(f"Service {service_name} started successfully in {service_info.startup_time:.2f}s")

        except Exception as e:
            service_info.state = ServiceState.FAILED
            self.logger.error(f"Failed to start service {service_name}: {e}")
            raise HealthCheckError(service_name, f"Startup failed: {e}")

    async def _wait_for_service_healthy(self, service_name: str):
        """Wait for service to be healthy with exponential backoff."""
        service_info = self.services[service_name]
        max_attempts = 20
        base_delay = 1.0
        max_delay = 30.0

        for attempt in range(max_attempts):
            try:
                service_info.health_check_attempts = attempt + 1

                health_status = await self.health_checker.check_service_health(
                    service_name,
                    service_info.health_check_url,
                    timeout=service_info.health_check_timeout,
                )

                if health_status == HealthStatus.HEALTHY:
                    service_info.last_health_check = time.time()
                    self.logger.debug(f"Service {service_name} health check passed")
                    return

                # Exponential backoff with jitter
                delay = min(base_delay * (2**attempt), max_delay)
                jitter = delay * 0.1 * (0.5 - asyncio.get_event_loop().time() % 1)
                await asyncio.sleep(delay + jitter)

                self.logger.debug(
                    f"Service {service_name} health check attempt {attempt + 1}/{max_attempts} "
                    f"failed, retrying in {delay:.1f}s"
                )

            except Exception as e:
                self.logger.warning(f"Health check error for {service_name}: {e}")

        service_info.state = ServiceState.UNHEALTHY
        raise HealthCheckError(
            service_name,
            f"Health check failed after {max_attempts} attempts",
            max_attempts,
        )

    async def _wait_for_all_services_healthy(self, services: List[str]):
        """Wait for all specified services to be healthy."""
        self.logger.info(f"Waiting for {len(services)} services to be healthy")

        # Use asyncio.gather for concurrent health checking
        health_check_tasks = [self._wait_for_service_healthy(service_name) for service_name in services]

        try:
            await asyncio.gather(*health_check_tasks)
            self.logger.info("All services are healthy")
        except Exception as e:
            self.logger.error(f"Health check failed for one or more services: {e}")
            raise

    def _get_services_for_profile(self, profile: ServiceProfile) -> List[str]:
        """Get list of services to start for the given profile."""
        if profile == ServiceProfile.FAST_INTEGRATION:
            return ["redis", "kafka", "zookeeper"]
        elif profile == ServiceProfile.FULL_INTEGRATION:
            return [
                "redis",
                "kafka",
                "zookeeper",
                "postgres",
                "clickhouse",
                "fraud_detector",
                "alert_processor",
            ]
        elif profile == ServiceProfile.PERFORMANCE_TESTING:
            return list(self.services.keys())  # All services
        elif profile == ServiceProfile.CHAOS_TESTING:
            return list(self.services.keys())  # All services with fault injection
        else:
            raise ValueError(f"Unknown service profile: {profile}")

    def _generate_compose_config(self, services: List[str], profile: ServiceProfile) -> Dict[str, Any]:
        """Generate Docker Compose configuration for specified services."""
        compose_config = {
            "version": "3.8",
            "networks": {"stream-sentinel-test": {"driver": "bridge"}},
            "services": {},
        }

        for service_name in services:
            if service_name not in self.services:
                continue

            service_info = self.services[service_name]
            compose_config["services"][service_name] = self._generate_service_config(
                service_name, service_info, profile
            )

        return compose_config

    def _generate_service_config(
        self, service_name: str, service_info: ServiceInfo, profile: ServiceProfile
    ) -> Dict[str, Any]:
        """Generate Docker Compose service configuration."""
        if service_name == "redis":
            return {
                "image": "redis:7-alpine",
                "container_name": service_info.container_name,
                "ports": ["6379:6379"],
                "networks": ["stream-sentinel-test"],
                "healthcheck": {
                    "test": ["CMD", "redis-cli", "ping"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 3,
                },
            }
        elif service_name == "kafka":
            return {
                "image": "confluentinc/cp-kafka:7.4.0",
                "container_name": service_info.container_name,
                "ports": ["9092:9092"],
                "networks": ["stream-sentinel-test"],
                "environment": {
                    "KAFKA_BROKER_ID": "1",
                    "KAFKA_ZOOKEEPER_CONNECT": f"{self.compose_project_name}_zookeeper_1:2181",
                    "KAFKA_ADVERTISED_LISTENERS": "PLAINTEXT://localhost:9092",
                    "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
                    "KAFKA_LOG_RETENTION_MS": "300000",  # 5 minutes for testing
                },
                "depends_on": ["zookeeper"],
                "healthcheck": {
                    "test": [
                        "CMD",
                        "kafka-broker-api-versions",
                        "--bootstrap-server=localhost:9092",
                    ],
                    "interval": "15s",
                    "timeout": "10s",
                    "retries": 5,
                },
            }
        elif service_name == "zookeeper":
            return {
                "image": "confluentinc/cp-zookeeper:7.4.0",
                "container_name": service_info.container_name,
                "ports": ["2181:2181"],
                "networks": ["stream-sentinel-test"],
                "environment": {
                    "ZOOKEEPER_CLIENT_PORT": "2181",
                    "ZOOKEEPER_TICK_TIME": "2000",
                },
                "healthcheck": {
                    "test": ["CMD", "zkCli.sh", "-server", "localhost:2181", "ls", "/"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 3,
                },
            }
        elif service_name == "postgres":
            return {
                "image": "postgres:15-alpine",
                "container_name": service_info.container_name,
                "ports": ["5432:5432"],
                "networks": ["stream-sentinel-test"],
                "environment": service_info.environment,
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U test_user -d test_db"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 5,
                },
            }
        elif service_name == "clickhouse":
            return {
                "image": "clickhouse/clickhouse-server:23.3-alpine",
                "container_name": service_info.container_name,
                "ports": ["8123:8123", "9000:9000"],
                "networks": ["stream-sentinel-test"],
                "healthcheck": {
                    "test": [
                        "CMD",
                        "wget",
                        "--no-verbose",
                        "--tries=1",
                        "--spider",
                        "http://localhost:8123/ping",
                    ],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 5,
                },
            }
        else:
            # Application services - placeholder for now
            return {
                "image": "alpine:latest",
                "container_name": service_info.container_name,
                "command": "sleep 3600",
                "networks": ["stream-sentinel-test"],
            }

    async def _write_compose_file(self, compose_config: Dict[str, Any]) -> Path:
        """Write Docker Compose configuration to file."""
        compose_file_path = self.config.temp_dir / f"docker-compose-{self.compose_project_name}.yml"
        compose_file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(compose_file_path, "w") as f:
            yaml.dump(compose_config, f, default_flow_style=False)

        self.logger.debug(f"Wrote Docker Compose file: {compose_file_path}")
        return compose_file_path

    async def _docker_compose_up(self, compose_file: Path, service_name: str):
        """Start service using Docker Compose."""
        cmd = [
            "docker-compose",
            "-f",
            str(compose_file),
            "-p",
            self.compose_project_name,
            "up",
            "-d",
            service_name,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise Exception(f"Docker Compose failed: {stderr.decode()}")

        self.logger.debug(f"Docker Compose output: {stdout.decode()}")

    async def _wait_for_container(self, container_name: str, timeout: int = 30) -> docker.models.containers.Container:
        """Wait for container to be running."""
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                container = self.docker_client.containers.get(container_name)
                if container.status == "running":
                    return container
            except docker.errors.NotFound:
                pass

            await asyncio.sleep(1)

        raise Exception(f"Container {container_name} not running after {timeout}s")

    async def _cleanup_failed_startup(self, services: List[str]):
        """Clean up resources after failed startup."""
        self.logger.warning("Cleaning up after failed service startup")

        try:
            await self.stop_services()
        except Exception as e:
            self.logger.error(f"Cleanup failed: {e}")

    async def stop_services(self):
        """Stop all running services and clean up resources."""
        if not self.running_services:
            self.logger.info("No services to stop")
            return

        self.logger.info(f"Stopping {len(self.running_services)} services")

        try:
            # Stop services in reverse dependency order
            for service_name in reversed(self.startup_order):
                if service_name in self.running_services:
                    await self._stop_single_service(service_name)

            # Clean up Docker Compose resources
            await self._docker_compose_down()

            # Reset state
            self.running_services.clear()
            for service_info in self.services.values():
                service_info.state = ServiceState.STOPPED
                service_info.container_id = None

            self.logger.info("All services stopped successfully")

        except Exception as e:
            self.logger.error(f"Service shutdown failed: {e}")
            raise

    async def _stop_single_service(self, service_name: str):
        """Stop a single service."""
        service_info = self.services[service_name]
        service_info.state = ServiceState.STOPPING

        try:
            if service_info.container_id:
                container = self.docker_client.containers.get(service_info.container_id)
                container.stop(timeout=10)
                self.logger.debug(f"Stopped service: {service_name}")

            self.running_services.discard(service_name)

        except Exception as e:
            self.logger.warning(f"Error stopping service {service_name}: {e}")

    async def _docker_compose_down(self):
        """Stop all Docker Compose services."""
        compose_files = list(self.config.temp_dir.glob(f"docker-compose-{self.compose_project_name}.yml"))

        for compose_file in compose_files:
            cmd = [
                "docker-compose",
                "-f",
                str(compose_file),
                "-p",
                self.compose_project_name,
                "down",
                "-v",
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            await process.communicate()
            compose_file.unlink()  # Clean up compose file

    @asynccontextmanager
    async def service_environment(self, profile: ServiceProfile):
        """Context manager for service lifecycle management."""
        services = None
        try:
            services = await self.start_services(profile)
            yield services
        finally:
            await self.stop_services()

    def get_service_metrics(self) -> Dict[str, Any]:
        """Get performance metrics for started services."""
        return {
            "startup_metrics": self.startup_metrics,
            "running_services": len(self.running_services),
            "service_states": {name: info.state.value for name, info in self.services.items()},
            "health_check_stats": {
                name: {
                    "attempts": info.health_check_attempts,
                    "last_check": info.last_health_check,
                    "startup_time": info.startup_time,
                }
                for name, info in self.services.items()
                if info.state != ServiceState.STOPPED
            },
        }
