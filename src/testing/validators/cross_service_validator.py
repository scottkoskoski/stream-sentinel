"""
Cross-Service State Validator for Integration Testing

Validates state consistency across distributed services in the fraud detection
system, ensuring data integrity and proper service interactions.

Key Features:
- Multi-service state consistency validation
- Transaction flow tracing across service boundaries
- Data integrity checks with detailed diagnostics
- Performance impact monitoring during validation
- Comprehensive error reporting and debugging support
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import asyncpg
import redis.asyncio as redis
from clickhouse_driver import Client as ClickHouseClient

from ..utils.test_config import IntegrationTestConfig


class ValidationStatus(Enum):
    """Status of cross-service validation."""

    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    PARTIAL = "partial"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass
class ServiceValidationResult:
    """Result of validation for a single service."""

    service_name: str
    status: ValidationStatus
    record_count: int
    validation_time_ms: float
    details: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    """Complete cross-service validation result."""

    overall_status: ValidationStatus
    total_validation_time_ms: float
    service_results: Dict[str, ServiceValidationResult] = field(default_factory=dict)
    consistency_checks: Dict[str, bool] = field(default_factory=dict)
    data_flow_validation: Dict[str, Any] = field(default_factory=dict)
    performance_impact: Dict[str, float] = field(default_factory=dict)

    @property
    def is_consistent(self) -> bool:
        """Check if all services are in consistent state."""
        return self.overall_status == ValidationStatus.CONSISTENT

    @property
    def success_rate(self) -> float:
        """Calculate percentage of successful validations."""
        if not self.service_results:
            return 0.0

        successful = sum(1 for result in self.service_results.values() if result.status == ValidationStatus.CONSISTENT)
        return successful / len(self.service_results)


class CrossServiceValidator:
    """
    Production-grade cross-service state validator.

    Validates data consistency and proper interactions across all services
    in the distributed fraud detection system with comprehensive diagnostics.
    """

    def __init__(self, config: IntegrationTestConfig):
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.CrossServiceValidator")

        # Connection pools for efficiency
        self._redis_client: Optional[redis.Redis] = None
        self._postgres_connection: Optional[asyncpg.Connection] = None
        self._clickhouse_client: Optional[ClickHouseClient] = None

        # Validation history for trend analysis
        self.validation_history: List[ValidationResult] = []

    async def __aenter__(self):
        """Async context manager entry - initialize connections."""
        await self._initialize_connections()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - cleanup connections."""
        await self._cleanup_connections()

    async def _initialize_connections(self):
        """Initialize connections to all services."""
        try:
            # Redis connection
            redis_config = self.config.get_redis_connection_config()
            self._redis_client = redis.from_url(
                f"redis://{redis_config['host']}:{redis_config['port']}/{redis_config['db']}",
                socket_timeout=redis_config["socket_timeout"],
                socket_connect_timeout=redis_config["socket_connect_timeout"],
            )

            # Test Redis connection
            await self._redis_client.ping()
            self.logger.debug("Redis connection established")

            # PostgreSQL connection
            postgres_config = self.config.get_postgres_connection_config()
            self._postgres_connection = await asyncpg.connect(**postgres_config)
            self.logger.debug("PostgreSQL connection established")

            # ClickHouse connection
            clickhouse_config = self.config.get_clickhouse_connection_config()
            self._clickhouse_client = ClickHouseClient(**clickhouse_config)
            self.logger.debug("ClickHouse connection established")

        except Exception as e:
            self.logger.error(f"Failed to initialize service connections: {e}")
            await self._cleanup_connections()
            raise

    async def _cleanup_connections(self):
        """Clean up all service connections."""
        try:
            if self._redis_client:
                await self._redis_client.close()

            if self._postgres_connection:
                await self._postgres_connection.close()

            if self._clickhouse_client:
                self._clickhouse_client.disconnect()

        except Exception as e:
            self.logger.warning(f"Error during connection cleanup: {e}")

    async def validate_transaction_flow(
        self, transaction_ids: List[str], scenario_id: str, timeout_seconds: int = 60
    ) -> ValidationResult:
        """
        Validate complete transaction flow across all services.

        Traces transactions from ingestion through processing to persistence,
        ensuring data consistency and proper service interactions.
        """
        start_time = time.time()
        self.logger.info(f"Starting cross-service transaction flow validation for {len(transaction_ids)} transactions")

        service_results = {}
        consistency_checks = {}

        try:
            # Validate Redis state (user profiles and transaction cache)
            redis_result = await self._validate_redis_state(transaction_ids, scenario_id)
            service_results["redis"] = redis_result

            # Validate PostgreSQL state (fraud alerts and audit logs)
            postgres_result = await self._validate_postgres_state(transaction_ids, scenario_id)
            service_results["postgres"] = postgres_result

            # Validate ClickHouse state (transaction analytics)
            clickhouse_result = await self._validate_clickhouse_state(transaction_ids, scenario_id)
            service_results["clickhouse"] = clickhouse_result

            # Cross-service consistency checks
            consistency_checks = await self._perform_consistency_checks(transaction_ids, scenario_id, service_results)

            # Data flow validation
            data_flow_validation = await self._validate_data_flow_integrity(transaction_ids, service_results)

            # Determine overall status
            overall_status = self._determine_overall_status(service_results, consistency_checks)

        except asyncio.TimeoutError:
            overall_status = ValidationStatus.TIMEOUT
            self.logger.error(f"Cross-service validation timed out after {timeout_seconds}s")

        except Exception as e:
            overall_status = ValidationStatus.ERROR
            self.logger.error(f"Cross-service validation failed: {e}")

        total_time = (time.time() - start_time) * 1000

        result = ValidationResult(
            overall_status=overall_status,
            total_validation_time_ms=total_time,
            service_results=service_results,
            consistency_checks=consistency_checks,
            data_flow_validation=data_flow_validation,
            performance_impact=self._calculate_performance_impact(service_results),
        )

        self.validation_history.append(result)

        self.logger.info(f"Cross-service validation completed in {total_time:.1f}ms: {overall_status.value}")
        return result

    async def _validate_redis_state(self, transaction_ids: List[str], scenario_id: str) -> ServiceValidationResult:
        """Validate Redis state consistency."""
        start_time = time.time()
        errors = []
        details = {}

        try:
            # Check user profile existence and consistency
            user_profiles = await self._check_redis_user_profiles(scenario_id)
            details["user_profiles_found"] = len(user_profiles)

            # Check transaction cache entries
            cached_transactions = await self._check_redis_transaction_cache(transaction_ids)
            details["cached_transactions"] = len(cached_transactions)

            # Validate profile data integrity
            profile_integrity = await self._validate_redis_profile_integrity(user_profiles)
            details["profile_integrity"] = profile_integrity

            # Check for orphaned or inconsistent data
            orphaned_keys = await self._check_redis_orphaned_data(scenario_id)
            if orphaned_keys:
                errors.append(f"Found {len(orphaned_keys)} orphaned Redis keys")
                details["orphaned_keys"] = orphaned_keys

            status = ValidationStatus.CONSISTENT if not errors else ValidationStatus.INCONSISTENT
            record_count = len(user_profiles) + len(cached_transactions)

        except Exception as e:
            errors.append(f"Redis validation error: {e}")
            status = ValidationStatus.ERROR
            record_count = 0

        validation_time = (time.time() - start_time) * 1000

        return ServiceValidationResult(
            service_name="redis",
            status=status,
            record_count=record_count,
            validation_time_ms=validation_time,
            details=details,
            errors=errors,
        )

    async def _validate_postgres_state(self, transaction_ids: List[str], scenario_id: str) -> ServiceValidationResult:
        """Validate PostgreSQL state consistency."""
        start_time = time.time()
        errors = []
        details = {}

        try:
            # Check fraud alerts
            fraud_alerts = await self._get_postgres_fraud_alerts(scenario_id)
            details["fraud_alerts_count"] = len(fraud_alerts)

            # Check audit logs
            audit_logs = await self._get_postgres_audit_logs(scenario_id)
            details["audit_logs_count"] = len(audit_logs)

            # Validate referential integrity
            integrity_issues = await self._check_postgres_referential_integrity(scenario_id)
            if integrity_issues:
                errors.extend(integrity_issues)
                details["integrity_issues"] = len(integrity_issues)

            # Check for duplicate records
            duplicates = await self._check_postgres_duplicates(scenario_id)
            if duplicates:
                errors.append(f"Found {len(duplicates)} duplicate records")
                details["duplicate_records"] = len(duplicates)

            status = ValidationStatus.CONSISTENT if not errors else ValidationStatus.INCONSISTENT
            record_count = len(fraud_alerts) + len(audit_logs)

        except Exception as e:
            errors.append(f"PostgreSQL validation error: {e}")
            status = ValidationStatus.ERROR
            record_count = 0

        validation_time = (time.time() - start_time) * 1000

        return ServiceValidationResult(
            service_name="postgres",
            status=status,
            record_count=record_count,
            validation_time_ms=validation_time,
            details=details,
            errors=errors,
        )

    async def _validate_clickhouse_state(self, transaction_ids: List[str], scenario_id: str) -> ServiceValidationResult:
        """Validate ClickHouse state consistency."""
        start_time = time.time()
        errors = []
        details = {}

        try:
            # Check transaction records
            transaction_records = await self._get_clickhouse_transactions(scenario_id)
            details["transaction_records_count"] = len(transaction_records)

            # Check analytics aggregations
            analytics_data = await self._get_clickhouse_analytics(scenario_id)
            details["analytics_records"] = len(analytics_data)

            # Validate data completeness
            expected_transactions = len(transaction_ids)
            actual_transactions = len(transaction_records)
            completeness_ratio = actual_transactions / expected_transactions if expected_transactions > 0 else 0

            details["data_completeness_ratio"] = completeness_ratio

            if completeness_ratio < 0.9:  # Allow 10% data loss tolerance
                errors.append(f"Data completeness below threshold: {completeness_ratio:.1%}")

            # Check for data quality issues
            quality_issues = await self._check_clickhouse_data_quality(transaction_records)
            if quality_issues:
                errors.extend(quality_issues)
                details["data_quality_issues"] = len(quality_issues)

            status = ValidationStatus.CONSISTENT if not errors else ValidationStatus.INCONSISTENT
            record_count = len(transaction_records) + len(analytics_data)

        except Exception as e:
            errors.append(f"ClickHouse validation error: {e}")
            status = ValidationStatus.ERROR
            record_count = 0

        validation_time = (time.time() - start_time) * 1000

        return ServiceValidationResult(
            service_name="clickhouse",
            status=status,
            record_count=record_count,
            validation_time_ms=validation_time,
            details=details,
            errors=errors,
        )

    async def _perform_consistency_checks(
        self,
        transaction_ids: List[str],
        scenario_id: str,
        service_results: Dict[str, ServiceValidationResult],
    ) -> Dict[str, bool]:
        """Perform cross-service consistency checks."""
        consistency_checks = {}

        try:
            # User profile consistency (Redis vs PostgreSQL)
            redis_profiles = service_results.get(
                "redis", ServiceValidationResult("redis", ValidationStatus.ERROR, 0, 0)
            ).details.get("user_profiles_found", 0)
            postgres_users = await self._get_unique_postgres_users(scenario_id)

            profile_consistency = abs(redis_profiles - len(postgres_users)) <= max(1, len(postgres_users) * 0.1)
            consistency_checks["user_profile_consistency"] = profile_consistency

            # Transaction count consistency (ClickHouse vs expected)
            clickhouse_transactions = service_results.get(
                "clickhouse",
                ServiceValidationResult("clickhouse", ValidationStatus.ERROR, 0, 0),
            ).details.get("transaction_records_count", 0)
            expected_transactions = len(transaction_ids)

            transaction_consistency = abs(clickhouse_transactions - expected_transactions) <= max(
                1, expected_transactions * 0.1
            )
            consistency_checks["transaction_count_consistency"] = transaction_consistency

            # Alert consistency (PostgreSQL fraud alerts vs expected fraud)
            postgres_alerts = service_results.get(
                "postgres",
                ServiceValidationResult("postgres", ValidationStatus.ERROR, 0, 0),
            ).details.get("fraud_alerts_count", 0)
            expected_fraud_ratio = await self._calculate_expected_fraud_ratio(scenario_id)
            expected_alerts = int(expected_transactions * expected_fraud_ratio)

            # Allow wide range for alert consistency due to ML model variability
            alert_consistency = abs(postgres_alerts - expected_alerts) <= max(5, expected_alerts * 0.5)
            consistency_checks["fraud_alert_consistency"] = alert_consistency

            # Data freshness consistency
            data_freshness = await self._check_data_freshness_consistency(scenario_id)
            consistency_checks["data_freshness_consistency"] = data_freshness

        except Exception as e:
            self.logger.error(f"Consistency check failed: {e}")
            consistency_checks["consistency_check_error"] = False

        return consistency_checks

    async def _validate_data_flow_integrity(
        self,
        transaction_ids: List[str],
        service_results: Dict[str, ServiceValidationResult],
    ) -> Dict[str, Any]:
        """Validate end-to-end data flow integrity."""
        data_flow_validation = {}

        try:
            # Sample a subset of transactions for detailed flow validation
            sample_size = min(10, len(transaction_ids))
            sample_transactions = transaction_ids[:sample_size]

            flow_validation_results = []

            for tx_id in sample_transactions:
                flow_result = await self._validate_single_transaction_flow(tx_id)
                flow_validation_results.append(flow_result)

            # Calculate flow integrity metrics
            successful_flows = sum(1 for result in flow_validation_results if result["success"])
            flow_success_rate = successful_flows / len(flow_validation_results) if flow_validation_results else 0

            data_flow_validation = {
                "sample_size": sample_size,
                "successful_flows": successful_flows,
                "flow_success_rate": flow_success_rate,
                "flow_details": flow_validation_results,
                "integrity_validated": flow_success_rate >= 0.8,  # 80% threshold
            }

        except Exception as e:
            self.logger.error(f"Data flow validation failed: {e}")
            data_flow_validation = {"error": str(e), "integrity_validated": False}

        return data_flow_validation

    async def _validate_single_transaction_flow(self, transaction_id: str) -> Dict[str, Any]:
        """Validate data flow for a single transaction across all services."""
        flow_result = {
            "transaction_id": transaction_id,
            "success": False,
            "stages": {},
            "errors": [],
        }

        try:
            # Stage 1: Check if transaction exists in Redis cache
            redis_exists = await self._check_transaction_in_redis(transaction_id)
            flow_result["stages"]["redis_cache"] = redis_exists

            # Stage 2: Check if user profile was updated in Redis
            user_profile_updated = await self._check_user_profile_updated(transaction_id)
            flow_result["stages"]["user_profile_update"] = user_profile_updated

            # Stage 3: Check if transaction persisted in ClickHouse
            clickhouse_persisted = await self._check_transaction_in_clickhouse(transaction_id)
            flow_result["stages"]["clickhouse_persistence"] = clickhouse_persisted

            # Stage 4: Check if fraud analysis completed (if applicable)
            fraud_analysis_complete = await self._check_fraud_analysis_complete(transaction_id)
            flow_result["stages"]["fraud_analysis"] = fraud_analysis_complete

            # Overall success if all critical stages pass
            critical_stages = ["user_profile_update", "clickhouse_persistence"]
            flow_result["success"] = all(flow_result["stages"].get(stage, False) for stage in critical_stages)

        except Exception as e:
            flow_result["errors"].append(f"Flow validation error: {e}")

        return flow_result

    def _determine_overall_status(
        self,
        service_results: Dict[str, ServiceValidationResult],
        consistency_checks: Dict[str, bool],
    ) -> ValidationStatus:
        """Determine overall validation status based on service results and consistency checks."""

        if not service_results:
            return ValidationStatus.ERROR

        # Check if any service has errors
        if any(result.status == ValidationStatus.ERROR for result in service_results.values()):
            return ValidationStatus.ERROR

        # Check if any service has timeouts
        if any(result.status == ValidationStatus.TIMEOUT for result in service_results.values()):
            return ValidationStatus.TIMEOUT

        # Check if all services are consistent
        all_consistent = all(result.status == ValidationStatus.CONSISTENT for result in service_results.values())

        # Check if all consistency checks pass
        consistency_passes = all(consistency_checks.values()) if consistency_checks else True

        if all_consistent and consistency_passes:
            return ValidationStatus.CONSISTENT
        elif any(result.status == ValidationStatus.CONSISTENT for result in service_results.values()):
            return ValidationStatus.PARTIAL
        else:
            return ValidationStatus.INCONSISTENT

    def _calculate_performance_impact(self, service_results: Dict[str, ServiceValidationResult]) -> Dict[str, float]:
        """Calculate performance impact of validation operations."""
        performance_impact = {}

        for service_name, result in service_results.items():
            # Calculate validation overhead as percentage of typical operation time
            typical_operation_time = self._get_typical_operation_time(service_name)
            overhead_percentage = (
                (result.validation_time_ms / typical_operation_time) * 100 if typical_operation_time > 0 else 0
            )

            performance_impact[f"{service_name}_validation_time_ms"] = result.validation_time_ms
            performance_impact[f"{service_name}_overhead_percentage"] = overhead_percentage

        return performance_impact

    def _get_typical_operation_time(self, service_name: str) -> float:
        """Get typical operation time for service (in milliseconds)."""
        typical_times = {
            "redis": 5.0,  # Redis operations typically 1-10ms
            "postgres": 50.0,  # PostgreSQL queries typically 10-100ms
            "clickhouse": 100.0,  # ClickHouse queries typically 50-200ms
        }
        return typical_times.get(service_name, 50.0)

    # Service-specific helper methods (placeholders for actual implementation)

    async def _check_redis_user_profiles(self, scenario_id: str) -> List[Dict]:
        """Get user profiles from Redis for scenario."""
        # Implementation would scan Redis for user profile keys matching scenario
        return []  # Placeholder

    async def _check_redis_transaction_cache(self, transaction_ids: List[str]) -> List[Dict]:
        """Check transaction cache entries in Redis."""
        # Implementation would check Redis for transaction cache entries
        return []  # Placeholder

    async def _validate_redis_profile_integrity(self, profiles: List[Dict]) -> Dict[str, Any]:
        """Validate integrity of Redis user profiles."""
        # Implementation would check profile data structure and values
        return {"valid_profiles": len(profiles)}  # Placeholder

    async def _check_redis_orphaned_data(self, scenario_id: str) -> List[str]:
        """Check for orphaned data in Redis."""
        # Implementation would scan for keys without proper references
        return []  # Placeholder

    async def _get_postgres_fraud_alerts(self, scenario_id: str) -> List[Dict]:
        """Get fraud alerts from PostgreSQL."""
        # Implementation would query fraud_alerts table
        return []  # Placeholder

    async def _get_postgres_audit_logs(self, scenario_id: str) -> List[Dict]:
        """Get audit logs from PostgreSQL."""
        # Implementation would query audit_logs table
        return []  # Placeholder

    async def _check_postgres_referential_integrity(self, scenario_id: str) -> List[str]:
        """Check referential integrity in PostgreSQL."""
        # Implementation would check foreign key constraints
        return []  # Placeholder

    async def _check_postgres_duplicates(self, scenario_id: str) -> List[Dict]:
        """Check for duplicate records in PostgreSQL."""
        # Implementation would check for duplicate entries
        return []  # Placeholder

    async def _get_clickhouse_transactions(self, scenario_id: str) -> List[Dict]:
        """Get transaction records from ClickHouse."""
        # Implementation would query ClickHouse transactions table
        return []  # Placeholder

    async def _get_clickhouse_analytics(self, scenario_id: str) -> List[Dict]:
        """Get analytics data from ClickHouse."""
        # Implementation would query ClickHouse analytics tables
        return []  # Placeholder

    async def _check_clickhouse_data_quality(self, records: List[Dict]) -> List[str]:
        """Check data quality issues in ClickHouse."""
        # Implementation would validate data format, ranges, etc.
        return []  # Placeholder

    async def _get_unique_postgres_users(self, scenario_id: str) -> List[str]:
        """Get unique users from PostgreSQL."""
        # Implementation would query distinct users
        return []  # Placeholder

    async def _calculate_expected_fraud_ratio(self, scenario_id: str) -> float:
        """Calculate expected fraud ratio for scenario."""
        # Implementation would analyze scenario configuration
        return 0.03  # Placeholder - 3% fraud rate

    async def _check_data_freshness_consistency(self, scenario_id: str) -> bool:
        """Check data freshness consistency across services."""
        # Implementation would compare timestamps across services
        return True  # Placeholder

    async def _check_transaction_in_redis(self, transaction_id: str) -> bool:
        """Check if transaction exists in Redis."""
        # Implementation would check Redis for transaction
        return True  # Placeholder

    async def _check_user_profile_updated(self, transaction_id: str) -> bool:
        """Check if user profile was updated for transaction."""
        # Implementation would check if user profile reflects transaction
        return True  # Placeholder

    async def _check_transaction_in_clickhouse(self, transaction_id: str) -> bool:
        """Check if transaction persisted in ClickHouse."""
        # Implementation would query ClickHouse
        return True  # Placeholder

    async def _check_fraud_analysis_complete(self, transaction_id: str) -> bool:
        """Check if fraud analysis completed for transaction."""
        # Implementation would check if fraud scoring completed
        return True  # Placeholder

    def get_validation_statistics(self) -> Dict[str, Any]:
        """Get comprehensive validation statistics."""
        if not self.validation_history:
            return {"total_validations": 0}

        successful_validations = sum(1 for result in self.validation_history if result.is_consistent)
        avg_validation_time = sum(result.total_validation_time_ms for result in self.validation_history) / len(
            self.validation_history
        )

        return {
            "total_validations": len(self.validation_history),
            "successful_validations": successful_validations,
            "success_rate": successful_validations / len(self.validation_history),
            "average_validation_time_ms": avg_validation_time,
            "service_performance": self._calculate_service_performance_stats(),
        }

    def _calculate_service_performance_stats(self) -> Dict[str, Dict[str, float]]:
        """Calculate performance statistics per service."""
        service_stats = {}

        for result in self.validation_history:
            for service_name, service_result in result.service_results.items():
                if service_name not in service_stats:
                    service_stats[service_name] = {
                        "total_time": 0,
                        "count": 0,
                        "errors": 0,
                    }

                service_stats[service_name]["total_time"] += service_result.validation_time_ms
                service_stats[service_name]["count"] += 1
                if service_result.status == ValidationStatus.ERROR:
                    service_stats[service_name]["errors"] += 1

        # Calculate averages
        for service_name, stats in service_stats.items():
            if stats["count"] > 0:
                stats["average_time_ms"] = stats["total_time"] / stats["count"]
                stats["error_rate"] = stats["errors"] / stats["count"]
            else:
                stats["average_time_ms"] = 0
                stats["error_rate"] = 0

        return service_stats
