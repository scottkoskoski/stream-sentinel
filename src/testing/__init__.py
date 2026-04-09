"""
Multi-Service Integration Testing Framework for Stream-Sentinel

Enterprise-grade testing infrastructure for distributed fraud detection system.
Provides service orchestration, data factory systems, and cross-service validation.

Architecture:
- ServiceOrchestrator: Manages Docker Compose service lifecycle and health
- TestDataFactory: Generates IEEE-CIS based realistic fraud scenarios
- CrossServiceValidator: Validates state consistency across persistence layers
- PerformanceMonitor: Built-in SLA validation and regression detection

Design Principles:
- Production-grade error handling and recovery
- Clean separation of concerns with intuitive interfaces
- Scalable patterns for horizontal test execution
- Comprehensive observability and debugging support
"""

from .factories.test_data_factory import FraudScenario, TestDataFactory
from .orchestrator.service_orchestrator import ServiceOrchestrator, ServiceProfile
from .utils.assertions import EventuallyConsistentAssertions
from .utils.test_config import IntegrationTestConfig
from .validators.cross_service_validator import CrossServiceValidator

__all__ = [
    "ServiceOrchestrator",
    "ServiceProfile",
    "TestDataFactory",
    "FraudScenario",
    "CrossServiceValidator",
    "IntegrationTestConfig",
    "EventuallyConsistentAssertions",
]

__version__ = "1.0.0"
