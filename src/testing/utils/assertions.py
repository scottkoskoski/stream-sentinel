"""
Eventually Consistent Assertions for Distributed Systems Testing

Provides assertion utilities that handle eventual consistency patterns common
in distributed systems, with exponential backoff, detailed diagnostics, and
performance monitoring.

Key Features:
- Exponential backoff with jitter for distributed system assertions
- Cross-service state validation with detailed diagnostics
- Performance tracking and timeout analysis
- Comprehensive error reporting for debugging failures
"""

import asyncio
import inspect
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union


class AssertionStatus(Enum):
    """Status of assertion attempt."""

    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class AssertionAttempt:
    """Record of a single assertion attempt."""

    attempt_number: int
    timestamp: float
    duration_ms: float
    status: AssertionStatus
    result: Any = None
    error_message: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AssertionResult:
    """Complete result of eventually consistent assertion."""

    success: bool
    total_attempts: int
    total_duration_ms: float
    attempts: List[AssertionAttempt] = field(default_factory=list)
    final_result: Any = None
    timeout_reached: bool = False
    error_message: Optional[str] = None

    @property
    def average_attempt_duration_ms(self) -> float:
        """Calculate average duration per attempt."""
        if not self.attempts:
            return 0.0
        return sum(attempt.duration_ms for attempt in self.attempts) / len(self.attempts)

    @property
    def success_rate(self) -> float:
        """Calculate success rate of attempts."""
        if not self.attempts:
            return 0.0
        successes = sum(1 for attempt in self.attempts if attempt.status == AssertionStatus.SUCCESS)
        return successes / len(self.attempts)


class AssertionTimeout(Exception):
    """Raised when assertion times out."""

    def __init__(
        self,
        assertion_name: str,
        timeout_seconds: int,
        attempts: int,
        last_error: str = "",
    ):
        self.assertion_name = assertion_name
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self.last_error = last_error

        message = f"Assertion '{assertion_name}' timed out after {timeout_seconds}s " f"({attempts} attempts)"
        if last_error:
            message += f". Last error: {last_error}"

        super().__init__(message)


class EventuallyConsistentAssertions:
    """
    Assertion utilities for eventually consistent distributed systems.

    Provides sophisticated assertion methods that handle timing issues,
    network delays, and eventual consistency patterns common in
    distributed fraud detection systems.
    """

    def __init__(self, default_timeout: int = 30, default_interval: float = 1.0):
        self.default_timeout = default_timeout
        self.default_interval = default_interval
        self.logger = logging.getLogger(f"{__name__}.EventuallyConsistentAssertions")

        # Performance tracking
        self.assertion_history: List[AssertionResult] = []
        self.total_assertions = 0
        self.successful_assertions = 0

    async def eventually_assert(
        self,
        assertion_func: Callable[[], Union[bool, Awaitable[bool]]],
        assertion_name: str = "assertion",
        timeout: Optional[int] = None,
        interval: Optional[float] = None,
        exponential_backoff: bool = True,
        max_interval: float = 10.0,
        context: Optional[Dict[str, Any]] = None,
    ) -> AssertionResult:
        """
        Assert that a condition becomes true within the timeout period.

        Args:
            assertion_func: Function that returns True when assertion passes
            assertion_name: Descriptive name for the assertion
            timeout: Maximum time to wait (seconds)
            interval: Initial interval between attempts (seconds)
            exponential_backoff: Whether to use exponential backoff
            max_interval: Maximum interval between attempts
            context: Additional context for debugging

        Returns:
            AssertionResult with detailed information about the assertion

        Raises:
            AssertionTimeout: If assertion does not pass within timeout
        """
        timeout = timeout or self.default_timeout
        interval = interval or self.default_interval
        context = context or {}

        start_time = time.time()
        attempts = []
        attempt_number = 0
        current_interval = interval

        self.logger.debug(f"Starting eventually consistent assertion: {assertion_name}")

        while time.time() - start_time < timeout:
            attempt_number += 1
            attempt_start = time.time()

            try:
                # Execute assertion function
                if inspect.iscoroutinefunction(assertion_func):
                    result = await assertion_func()
                else:
                    result = assertion_func()

                attempt_duration = (time.time() - attempt_start) * 1000

                if result:
                    # Assertion succeeded
                    attempt = AssertionAttempt(
                        attempt_number=attempt_number,
                        timestamp=attempt_start,
                        duration_ms=attempt_duration,
                        status=AssertionStatus.SUCCESS,
                        result=result,
                        context=context.copy(),
                    )
                    attempts.append(attempt)

                    total_duration = (time.time() - start_time) * 1000
                    assertion_result = AssertionResult(
                        success=True,
                        total_attempts=attempt_number,
                        total_duration_ms=total_duration,
                        attempts=attempts,
                        final_result=result,
                    )

                    self._record_assertion_success(assertion_name, assertion_result)

                    self.logger.debug(
                        f"Assertion '{assertion_name}' succeeded after "
                        f"{attempt_number} attempts ({total_duration:.1f}ms)"
                    )

                    return assertion_result

                else:
                    # Assertion failed, record attempt and continue
                    attempt = AssertionAttempt(
                        attempt_number=attempt_number,
                        timestamp=attempt_start,
                        duration_ms=attempt_duration,
                        status=AssertionStatus.FAILURE,
                        result=result,
                        error_message=f"Assertion returned {result}",
                        context=context.copy(),
                    )
                    attempts.append(attempt)

            except Exception as e:
                # Exception during assertion execution
                attempt_duration = (time.time() - attempt_start) * 1000
                attempt = AssertionAttempt(
                    attempt_number=attempt_number,
                    timestamp=attempt_start,
                    duration_ms=attempt_duration,
                    status=AssertionStatus.ERROR,
                    error_message=str(e),
                    context=context.copy(),
                )
                attempts.append(attempt)

                self.logger.debug(f"Assertion '{assertion_name}' attempt {attempt_number} failed: {e}")

            # Check if we have time for another attempt
            elapsed = time.time() - start_time
            if elapsed + current_interval >= timeout:
                break

            # Wait before next attempt
            await asyncio.sleep(current_interval)

            # Update interval for exponential backoff
            if exponential_backoff:
                jitter = current_interval * 0.1 * (0.5 - random.random())
                current_interval = min(current_interval * 1.5 + jitter, max_interval)

        # Timeout reached
        total_duration = (time.time() - start_time) * 1000
        last_error = attempts[-1].error_message if attempts else "No attempts made"

        assertion_result = AssertionResult(
            success=False,
            total_attempts=attempt_number,
            total_duration_ms=total_duration,
            attempts=attempts,
            timeout_reached=True,
            error_message=f"Assertion timed out after {timeout}s",
        )

        self._record_assertion_failure(assertion_name, assertion_result)

        raise AssertionTimeout(assertion_name, timeout, attempt_number, last_error or "")

    async def assert_cross_service_state(
        self,
        checks: Dict[str, Callable[[], Union[bool, Awaitable[bool]]]],
        assertion_name: str = "cross_service_state",
        timeout: Optional[int] = None,
        require_all: bool = True,
    ) -> AssertionResult:
        """
        Assert state consistency across multiple services.

        Args:
            checks: Dict mapping service names to assertion functions
            assertion_name: Descriptive name for the assertion
            timeout: Maximum time to wait
            require_all: If True, all checks must pass; if False, any check passing is sufficient

        Returns:
            AssertionResult with detailed cross-service validation results
        """
        timeout = timeout or self.default_timeout

        async def combined_assertion():
            results = {}

            for service_name, check_func in checks.items():
                try:
                    if inspect.iscoroutinefunction(check_func):
                        result = await check_func()
                    else:
                        result = check_func()
                    results[service_name] = result
                except Exception as e:
                    self.logger.debug(f"Cross-service check failed for {service_name}: {e}")
                    results[service_name] = False

            if require_all:
                return all(results.values()), results
            else:
                return any(results.values()), results

        context = {
            "service_count": len(checks),
            "services": list(checks.keys()),
            "require_all": require_all,
        }

        async def assertion_wrapper():
            success, results = await combined_assertion()
            # Store results in context for debugging
            context["service_results"] = results
            return success

        return await self.eventually_assert(assertion_wrapper, assertion_name, timeout=timeout, context=context)

    async def assert_data_consistency(
        self,
        data_sources: Dict[str, Callable[[], Union[Any, Awaitable[Any]]]],
        comparison_func: Callable[[Dict[str, Any]], bool],
        assertion_name: str = "data_consistency",
        timeout: Optional[int] = None,
    ) -> AssertionResult:
        """
        Assert data consistency across multiple data sources.

        Args:
            data_sources: Dict mapping source names to data retrieval functions
            comparison_func: Function that takes dict of source data and returns True if consistent
            assertion_name: Descriptive name for the assertion
            timeout: Maximum time to wait

        Returns:
            AssertionResult with detailed consistency validation results
        """
        timeout = timeout or self.default_timeout

        async def consistency_assertion():
            data = {}

            for source_name, data_func in data_sources.items():
                try:
                    if inspect.iscoroutinefunction(data_func):
                        result = await data_func()
                    else:
                        result = data_func()
                    data[source_name] = result
                except Exception as e:
                    self.logger.debug(f"Data retrieval failed for {source_name}: {e}")
                    # Return failure if any data source fails
                    return False

            try:
                return comparison_func(data)
            except Exception as e:
                self.logger.debug(f"Data consistency comparison failed: {e}")
                return False

        context = {
            "data_source_count": len(data_sources),
            "data_sources": list(data_sources.keys()),
        }

        return await self.eventually_assert(consistency_assertion, assertion_name, timeout=timeout, context=context)

    async def assert_message_flow(
        self,
        producer_func: Callable[[], Union[str, Awaitable[str]]],
        consumer_func: Callable[[str], Union[bool, Awaitable[bool]]],
        assertion_name: str = "message_flow",
        timeout: Optional[int] = None,
    ) -> AssertionResult:
        """
        Assert that a message flows correctly from producer to consumer.

        Args:
            producer_func: Function that produces a message and returns message ID
            consumer_func: Function that checks if message was consumed (takes message ID)
            assertion_name: Descriptive name for the assertion
            timeout: Maximum time to wait

        Returns:
            AssertionResult with message flow validation results
        """
        timeout = timeout or self.default_timeout
        message_id = None

        async def message_flow_assertion():
            nonlocal message_id

            # Produce message if not already done
            if message_id is None:
                try:
                    if inspect.iscoroutinefunction(producer_func):
                        message_id = await producer_func()
                    else:
                        message_id = producer_func()

                    if not message_id:
                        return False

                except Exception as e:
                    self.logger.debug(f"Message production failed: {e}")
                    return False

            # Check if message was consumed
            try:
                if inspect.iscoroutinefunction(consumer_func):
                    return await consumer_func(message_id)
                else:
                    return consumer_func(message_id)
            except Exception as e:
                self.logger.debug(f"Message consumption check failed: {e}")
                return False

        context = {"message_id": message_id}

        result = await self.eventually_assert(message_flow_assertion, assertion_name, timeout=timeout, context=context)

        # Update context with final message ID
        if message_id:
            result.attempts[-1].context["message_id"] = message_id

        return result

    async def assert_performance_sla(
        self,
        operation_func: Callable[[], Union[Any, Awaitable[Any]]],
        max_latency_ms: float,
        min_throughput_ops_per_sec: Optional[float] = None,
        assertion_name: str = "performance_sla",
        sample_count: int = 10,
    ) -> AssertionResult:
        """
        Assert that an operation meets performance SLA requirements.

        Args:
            operation_func: Function to test for performance
            max_latency_ms: Maximum acceptable latency in milliseconds
            min_throughput_ops_per_sec: Minimum acceptable throughput
            assertion_name: Descriptive name for the assertion
            sample_count: Number of samples to collect

        Returns:
            AssertionResult with performance validation results
        """
        latencies = []
        errors = []

        # Collect performance samples
        for i in range(sample_count):
            start_time = time.time()

            try:
                if inspect.iscoroutinefunction(operation_func):
                    await operation_func()
                else:
                    operation_func()

                latency_ms = (time.time() - start_time) * 1000
                latencies.append(latency_ms)

            except Exception as e:
                errors.append(str(e))
                self.logger.debug(f"Performance test operation failed: {e}")

        if not latencies:
            # All operations failed
            result = AssertionResult(
                success=False,
                total_attempts=1,
                total_duration_ms=0,
                error_message=f"All {sample_count} operations failed",
            )
            return result

        # Calculate performance metrics
        avg_latency = sum(latencies) / len(latencies)
        max_observed_latency = max(latencies)
        total_time_seconds = sum(latencies) / 1000
        actual_throughput = len(latencies) / total_time_seconds if total_time_seconds > 0 else 0

        # Check SLA compliance
        latency_ok = max_observed_latency <= max_latency_ms
        throughput_ok = min_throughput_ops_per_sec is None or actual_throughput >= min_throughput_ops_per_sec

        sla_met = latency_ok and throughput_ok

        context = {
            "sample_count": len(latencies),
            "error_count": len(errors),
            "avg_latency_ms": avg_latency,
            "max_latency_ms": max_observed_latency,
            "required_max_latency_ms": max_latency_ms,
            "actual_throughput_ops_per_sec": actual_throughput,
            "required_min_throughput_ops_per_sec": min_throughput_ops_per_sec,
            "latency_sla_met": latency_ok,
            "throughput_sla_met": throughput_ok,
        }

        attempt = AssertionAttempt(
            attempt_number=1,
            timestamp=time.time(),
            duration_ms=sum(latencies),
            status=AssertionStatus.SUCCESS if sla_met else AssertionStatus.FAILURE,
            result=sla_met,
            context=context,
        )

        result = AssertionResult(
            success=sla_met,
            total_attempts=1,
            total_duration_ms=sum(latencies),
            attempts=[attempt],
            final_result=sla_met,
        )

        if not sla_met:
            error_details = []
            if not latency_ok:
                error_details.append(f"Latency SLA failed: {max_observed_latency:.1f}ms > {max_latency_ms:.1f}ms")
            if not throughput_ok:
                error_details.append(
                    f"Throughput SLA failed: {actual_throughput:.1f} < {min_throughput_ops_per_sec:.1f} ops/sec"
                )
            result.error_message = "; ".join(error_details)

        if sla_met:
            self._record_assertion_success(assertion_name, result)
        else:
            self._record_assertion_failure(assertion_name, result)

        return result

    def _record_assertion_success(self, assertion_name: str, result: AssertionResult):
        """Record successful assertion for statistics."""
        self.total_assertions += 1
        self.successful_assertions += 1
        self.assertion_history.append(result)

        self.logger.debug(
            f"Assertion '{assertion_name}' succeeded "
            f"({self.successful_assertions}/{self.total_assertions} success rate)"
        )

    def _record_assertion_failure(self, assertion_name: str, result: AssertionResult):
        """Record failed assertion for statistics."""
        self.total_assertions += 1
        self.assertion_history.append(result)

        self.logger.warning(
            f"Assertion '{assertion_name}' failed "
            f"({self.successful_assertions}/{self.total_assertions} success rate)"
        )

    def get_assertion_statistics(self) -> Dict[str, Any]:
        """Get comprehensive assertion statistics."""
        if not self.assertion_history:
            return {"total_assertions": 0, "success_rate": 0.0}

        successful = sum(1 for result in self.assertion_history if result.success)
        total_duration = sum(result.total_duration_ms for result in self.assertion_history)

        return {
            "total_assertions": len(self.assertion_history),
            "successful_assertions": successful,
            "failed_assertions": len(self.assertion_history) - successful,
            "success_rate": successful / len(self.assertion_history),
            "average_duration_ms": total_duration / len(self.assertion_history),
            "total_duration_ms": total_duration,
            "average_attempts_per_assertion": sum(result.total_attempts for result in self.assertion_history)
            / len(self.assertion_history),
        }

    def reset_statistics(self):
        """Reset assertion statistics."""
        self.assertion_history.clear()
        self.total_assertions = 0
        self.successful_assertions = 0
