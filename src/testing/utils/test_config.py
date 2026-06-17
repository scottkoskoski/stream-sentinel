"""
Integration Test Configuration Management

Provides centralized configuration management for multi-service integration testing
with environment-specific settings, resource management, and validation.

Key Features:
- Environment-aware configuration (development, CI, performance)
- Resource allocation and cleanup policies
- Service-specific configuration overrides
- Comprehensive validation and error reporting
"""

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Union


class TestEnvironment(Enum):
    """Test environment types with different resource requirements."""

    DEVELOPMENT = "development"  # Fast iteration, minimal resources
    CI_CD = "ci_cd"  # CI/CD pipeline, moderate resources
    PERFORMANCE = "performance"  # Full performance testing, maximum resources
    CHAOS = "chaos"  # Chaos testing with fault injection


@dataclass
class ResourceLimits:
    """Resource limits for test environment."""

    max_memory_mb: int = 2048
    max_cpu_cores: int = 4
    max_disk_gb: int = 10
    network_timeout_seconds: int = 30
    service_startup_timeout_seconds: int = 120


@dataclass
class ServiceConfig:
    """Configuration for individual service in tests."""

    enabled: bool = True
    health_check_timeout: int = 30
    startup_timeout: int = 120
    environment_variables: Dict[str, str] = field(default_factory=dict)
    port_overrides: Dict[str, int] = field(default_factory=dict)
    resource_limits: Optional[ResourceLimits] = None


class IntegrationTestConfig:
    """
    Centralized configuration manager for integration testing.

    Manages environment-specific settings, resource allocation,
    and service configuration for the testing framework.
    """

    def __init__(
        self,
        environment: Union[TestEnvironment, str] = TestEnvironment.DEVELOPMENT,
        temp_dir: Optional[Path] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):

        # Environment setup
        if isinstance(environment, str):
            environment = TestEnvironment(environment)
        self.environment = environment

        # Directory management
        if temp_dir is None:
            self.temp_dir = Path(tempfile.mkdtemp(prefix=f"stream-sentinel-test-{environment.value}-"))
        else:
            self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Logging setup
        self.logger = logging.getLogger(f"{__name__}.IntegrationTestConfig")

        # Load base configuration
        self._load_base_configuration()

        # Apply overrides
        if config_overrides:
            self._apply_config_overrides(config_overrides)

        # Validate configuration
        self._validate_configuration()

    def _load_base_configuration(self):
        """Load base configuration based on environment."""
        # Resource limits by environment
        if self.environment == TestEnvironment.DEVELOPMENT:
            self.resource_limits = ResourceLimits(
                max_memory_mb=1024,
                max_cpu_cores=2,
                max_disk_gb=5,
                network_timeout_seconds=15,
                service_startup_timeout_seconds=60,
            )
        elif self.environment == TestEnvironment.CI_CD:
            self.resource_limits = ResourceLimits(
                max_memory_mb=2048,
                max_cpu_cores=4,
                max_disk_gb=10,
                network_timeout_seconds=30,
                service_startup_timeout_seconds=120,
            )
        elif self.environment == TestEnvironment.PERFORMANCE:
            self.resource_limits = ResourceLimits(
                max_memory_mb=4096,
                max_cpu_cores=8,
                max_disk_gb=20,
                network_timeout_seconds=60,
                service_startup_timeout_seconds=300,
            )
        else:  # CHAOS
            self.resource_limits = ResourceLimits(
                max_memory_mb=4096,
                max_cpu_cores=8,
                max_disk_gb=20,
                network_timeout_seconds=120,
                service_startup_timeout_seconds=300,
            )

        # Test execution settings
        self.test_execution = {
            "parallel_workers": self._get_parallel_workers(),
            "test_timeout_minutes": self._get_test_timeout_minutes(),
            "cleanup_on_failure": True,
            "collect_logs_on_failure": True,
            "performance_monitoring": self.environment != TestEnvironment.DEVELOPMENT,
        }

        # Kafka configuration
        self.kafka = {
            "bootstrap_servers": "localhost:9092",
            "topic_prefix": f"test-{self.environment.value}-",
            "partition_count": (1 if self.environment == TestEnvironment.DEVELOPMENT else 3),
            "replication_factor": 1,
            "retention_ms": 300000,  # 5 minutes
            "cleanup_policy": "delete",
        }

        # Redis configuration
        self.redis = {
            "host": "localhost",
            "port": 6379,
            "db": self._get_redis_db_number(),
            "namespace": f"test:{self.environment.value}",
            "ttl_seconds": 3600,  # 1 hour
            "max_connections": 10,
        }

        # PostgreSQL configuration
        self.postgres = {
            "host": "localhost",
            "port": 5432,
            "database": f"test_{self.environment.value}",
            "username": "test_user",
            "password": "test_pass",
            "schema": f"test_{self.environment.value}",
            "max_connections": 5,
            "connection_timeout": 10,
        }

        # ClickHouse configuration
        self.clickhouse = {
            "host": "localhost",
            "port": 8123,
            "database": f"test_{self.environment.value}",
            "username": "default",
            "password": "",
            "table_prefix": f"test_{self.environment.value}_",
        }

        # Service-specific configurations
        self.services = {
            "redis": ServiceConfig(enabled=True, health_check_timeout=10, startup_timeout=30),
            "zookeeper": ServiceConfig(enabled=True, health_check_timeout=15, startup_timeout=45),
            "kafka": ServiceConfig(
                enabled=True,
                health_check_timeout=30,
                startup_timeout=90,
                environment_variables={
                    "KAFKA_LOG_RETENTION_MS": "300000",
                    "KAFKA_NUM_PARTITIONS": str(self.kafka["partition_count"]),
                },
            ),
            "postgres": ServiceConfig(
                enabled=True,
                health_check_timeout=15,
                startup_timeout=60,
                environment_variables={
                    "POSTGRES_DB": self.postgres["database"],
                    "POSTGRES_USER": self.postgres["username"],
                    "POSTGRES_PASSWORD": self.postgres["password"],
                },
            ),
            "clickhouse": ServiceConfig(enabled=True, health_check_timeout=20, startup_timeout=60),
            "fraud_detector": ServiceConfig(
                enabled=self.environment != TestEnvironment.DEVELOPMENT,
                health_check_timeout=30,
                startup_timeout=120,
            ),
            "alert_processor": ServiceConfig(
                enabled=self.environment != TestEnvironment.DEVELOPMENT,
                health_check_timeout=30,
                startup_timeout=120,
            ),
        }

        # Test data configuration
        self.test_data = {
            "base_transaction_count": self._get_base_transaction_count(),
            "fraud_rate": 0.03,  # 3% fraud rate matching IEEE-CIS
            "user_count": self._get_user_count(),
            "time_window_hours": 24,
            "scenario_timeout_seconds": 300,
        }

    def _get_parallel_workers(self) -> int:
        """Get number of parallel test workers based on environment."""
        if self.environment == TestEnvironment.DEVELOPMENT:
            return 1
        elif self.environment == TestEnvironment.CI_CD:
            return min(4, os.cpu_count() or 1)
        else:
            return min(8, os.cpu_count() or 1)

    def _get_test_timeout_minutes(self) -> int:
        """Get test timeout based on environment."""
        if self.environment == TestEnvironment.DEVELOPMENT:
            return 5
        elif self.environment == TestEnvironment.CI_CD:
            return 15
        elif self.environment == TestEnvironment.PERFORMANCE:
            return 60
        else:  # CHAOS
            return 120

    def _get_redis_db_number(self) -> int:
        """Get Redis database number to avoid conflicts."""
        if self.environment == TestEnvironment.DEVELOPMENT:
            return 1
        elif self.environment == TestEnvironment.CI_CD:
            return 2
        elif self.environment == TestEnvironment.PERFORMANCE:
            return 3
        else:  # CHAOS
            return 4

    def _get_base_transaction_count(self) -> int:
        """Get base transaction count for test scenarios."""
        if self.environment == TestEnvironment.DEVELOPMENT:
            return 100
        elif self.environment == TestEnvironment.CI_CD:
            return 1000
        elif self.environment == TestEnvironment.PERFORMANCE:
            return 10000
        else:  # CHAOS
            return 5000

    def _get_user_count(self) -> int:
        """Get number of simulated users for test scenarios."""
        if self.environment == TestEnvironment.DEVELOPMENT:
            return 10
        elif self.environment == TestEnvironment.CI_CD:
            return 50
        elif self.environment == TestEnvironment.PERFORMANCE:
            return 500
        else:  # CHAOS
            return 200

    def _apply_config_overrides(self, overrides: Dict[str, Any]):
        """Apply configuration overrides."""
        for key, value in overrides.items():
            if hasattr(self, key):
                if isinstance(getattr(self, key), dict) and isinstance(value, dict):
                    # Merge dictionaries
                    getattr(self, key).update(value)
                else:
                    # Direct assignment
                    setattr(self, key, value)
                self.logger.debug(f"Applied config override: {key} = {value}")
            else:
                self.logger.warning(f"Unknown config override: {key}")

    def _validate_configuration(self):
        """Validate configuration settings."""
        errors = []

        # Validate resource limits
        if self.resource_limits.max_memory_mb < 512:
            errors.append("Maximum memory must be at least 512MB")

        if self.resource_limits.max_cpu_cores < 1:
            errors.append("Maximum CPU cores must be at least 1")

        if self.resource_limits.max_disk_gb < 1:
            errors.append("Maximum disk space must be at least 1GB")

        # Validate timeouts
        if self.resource_limits.network_timeout_seconds < 5:
            errors.append("Network timeout must be at least 5 seconds")

        if self.resource_limits.service_startup_timeout_seconds < 30:
            errors.append("Service startup timeout must be at least 30 seconds")

        # Validate test data configuration
        if self.test_data["fraud_rate"] < 0 or self.test_data["fraud_rate"] > 1:
            errors.append("Fraud rate must be between 0 and 1")

        if self.test_data["base_transaction_count"] < 1:
            errors.append("Base transaction count must be at least 1")

        if self.test_data["user_count"] < 1:
            errors.append("User count must be at least 1")

        # Validate service configurations
        for service_name, service_config in self.services.items():
            if service_config.startup_timeout < 10:
                errors.append(f"Service {service_name} startup timeout too low")

            if service_config.health_check_timeout < 5:
                errors.append(f"Service {service_name} health check timeout too low")

        if errors:
            error_msg = "Configuration validation failed:\n" + "\n".join(f"  - {error}" for error in errors)
            raise ValueError(error_msg)

        self.logger.info(f"Configuration validated successfully for {self.environment.value} environment")

    def get_kafka_connection_config(self) -> Dict[str, Any]:
        """Get Kafka connection configuration."""
        return {
            "bootstrap.servers": self.kafka["bootstrap_servers"],
            "client.id": f"test-client-{self.environment.value}",
            "group.id": f"test-group-{self.environment.value}",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "api.version.request": True,
            "api.version.request.timeout.ms": self.resource_limits.network_timeout_seconds * 1000,
            "socket.timeout.ms": self.resource_limits.network_timeout_seconds * 1000,
        }

    def get_redis_connection_config(self) -> Dict[str, Any]:
        """Get Redis connection configuration."""
        return {
            "host": self.redis["host"],
            "port": self.redis["port"],
            "db": self.redis["db"],
            "socket_timeout": self.resource_limits.network_timeout_seconds,
            "socket_connect_timeout": self.resource_limits.network_timeout_seconds,
            "max_connections": self.redis["max_connections"],
        }

    def get_postgres_connection_config(self) -> Dict[str, Any]:
        """Get PostgreSQL connection configuration."""
        return {
            "host": self.postgres["host"],
            "port": self.postgres["port"],
            "database": self.postgres["database"],
            "user": self.postgres["username"],
            "password": self.postgres["password"],
            "connect_timeout": self.postgres["connection_timeout"],
            "command_timeout": self.resource_limits.network_timeout_seconds,
        }

    def get_clickhouse_connection_config(self) -> Dict[str, Any]:
        """Get ClickHouse connection configuration."""
        return {
            "host": self.clickhouse["host"],
            "port": self.clickhouse["port"],
            "database": self.clickhouse["database"],
            "user": self.clickhouse["username"],
            "password": self.clickhouse["password"],
            "send_receive_timeout": self.resource_limits.network_timeout_seconds,
        }

    def get_service_config(self, service_name: str) -> ServiceConfig:
        """Get configuration for specific service."""
        if service_name not in self.services:
            raise ValueError(f"Unknown service: {service_name}")
        return self.services[service_name]

    def is_service_enabled(self, service_name: str) -> bool:
        """Check if service is enabled in current environment."""
        service_config = self.get_service_config(service_name)
        return service_config.enabled

    def get_test_topic_name(self, base_name: str) -> str:
        """Get environment-specific topic name."""
        return f"{self.kafka['topic_prefix']}{base_name}"

    def get_redis_key(self, base_key: str) -> str:
        """Get namespaced Redis key."""
        return f"{self.redis['namespace']}:{base_key}"

    def get_postgres_table_name(self, base_name: str) -> str:
        """Get schema-qualified PostgreSQL table name."""
        return f"{self.postgres['schema']}.{base_name}"

    def get_clickhouse_table_name(self, base_name: str) -> str:
        """Get prefixed ClickHouse table name."""
        return f"{self.clickhouse['table_prefix']}{base_name}"

    def save_config(self, file_path: Optional[Path] = None) -> Path:
        """Save current configuration to file."""
        if file_path is None:
            file_path = self.temp_dir / "integration_test_config.json"

        config_dict = {
            "environment": self.environment.value,
            "resource_limits": {
                "max_memory_mb": self.resource_limits.max_memory_mb,
                "max_cpu_cores": self.resource_limits.max_cpu_cores,
                "max_disk_gb": self.resource_limits.max_disk_gb,
                "network_timeout_seconds": self.resource_limits.network_timeout_seconds,
                "service_startup_timeout_seconds": self.resource_limits.service_startup_timeout_seconds,
            },
            "test_execution": self.test_execution,
            "kafka": self.kafka,
            "redis": self.redis,
            "postgres": self.postgres,
            "clickhouse": self.clickhouse,
            "test_data": self.test_data,
            "services": {
                name: {
                    "enabled": config.enabled,
                    "health_check_timeout": config.health_check_timeout,
                    "startup_timeout": config.startup_timeout,
                    "environment_variables": config.environment_variables,
                    "port_overrides": config.port_overrides,
                }
                for name, config in self.services.items()
            },
        }

        with open(file_path, "w") as f:
            json.dump(config_dict, f, indent=2)

        self.logger.info(f"Configuration saved to {file_path}")
        return file_path

    def cleanup(self):
        """Clean up temporary resources."""
        try:
            if self.temp_dir.exists():
                import shutil

                shutil.rmtree(self.temp_dir, ignore_errors=True)
                self.logger.info(f"Cleaned up temporary directory: {self.temp_dir}")
        except Exception as e:
            self.logger.warning(f"Failed to cleanup temporary directory: {e}")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.cleanup()

    def __repr__(self) -> str:
        return f"IntegrationTestConfig(environment={self.environment.value}, temp_dir={self.temp_dir})"
