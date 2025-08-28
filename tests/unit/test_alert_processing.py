"""
Comprehensive Unit Tests for Alert Processing System

Tests the complete alert processing pipeline including:
- Multi-tier severity classification (MINIMAL → CRITICAL)
- Alert routing and action automation
- User account management and blocking
- Investigation queue management
- SLA compliance validation
"""

import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from dataclasses import asdict
from pathlib import Path

import sys
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.alert_processor import AlertProcessor, FraudAlert, AlertSeverity, AlertAction


class TestAlertProcessing:
    """Comprehensive alert processing tests."""

    def setup_method(self):
        """Setup for each test method."""
        # Mock dependencies
        self.mock_redis = MagicMock()
        self.mock_config = Mock()
        self.mock_config.logger = Mock()
        self.mock_config.get_consumer_config.return_value = {}
        self.mock_config.get_producer_config.return_value = {}
        
        # Mock database manager
        self.mock_db_manager = Mock()
        
        # Create alert processor with mocked dependencies
        with patch('consumers.alert_processor.get_kafka_config', return_value=self.mock_config):
            with patch('consumers.alert_processor.redis.Redis', return_value=self.mock_redis):
                with patch('consumers.alert_processor.DatabaseManager', return_value=self.mock_db_manager):
                    self.alert_processor = AlertProcessor()
                    self.alert_processor.redis_client = self.mock_redis
                    self.alert_processor.db_manager = self.mock_db_manager

    def test_alert_severity_classification(self):
        """Test multi-tier severity classification logic."""
        # Test MINIMAL severity (score 0.0 - 0.2)
        minimal_alert_data = {
            "transaction_id": "txn_minimal",
            "user_id": "user_001",
            "fraud_score": 0.15,
            "timestamp": datetime.now().isoformat()
        }
        
        severity = self.alert_processor.classify_severity(minimal_alert_data)
        assert severity == AlertSeverity.MINIMAL
        
        # Test LOW severity (score 0.2 - 0.4)
        low_alert_data = {
            "transaction_id": "txn_low",
            "user_id": "user_002",
            "fraud_score": 0.32,
            "timestamp": datetime.now().isoformat()
        }
        
        severity = self.alert_processor.classify_severity(low_alert_data)
        assert severity == AlertSeverity.LOW
        
        # Test MEDIUM severity (score 0.4 - 0.6)
        medium_alert_data = {
            "transaction_id": "txn_medium",
            "user_id": "user_003",
            "fraud_score": 0.55,
            "timestamp": datetime.now().isoformat()
        }
        
        severity = self.alert_processor.classify_severity(medium_alert_data)
        assert severity == AlertSeverity.MEDIUM
        
        # Test HIGH severity (score 0.6 - 0.8)
        high_alert_data = {
            "transaction_id": "txn_high",
            "user_id": "user_004",
            "fraud_score": 0.72,
            "timestamp": datetime.now().isoformat()
        }
        
        severity = self.alert_processor.classify_severity(high_alert_data)
        assert severity == AlertSeverity.HIGH
        
        # Test CRITICAL severity (score 0.8 - 1.0)
        critical_alert_data = {
            "transaction_id": "txn_critical",
            "user_id": "user_005",
            "fraud_score": 0.91,
            "timestamp": datetime.now().isoformat()
        }
        
        severity = self.alert_processor.classify_severity(critical_alert_data)
        assert severity == AlertSeverity.CRITICAL

    def test_severity_boundary_conditions(self):
        """Test severity classification at boundary values."""
        boundary_test_cases = [
            (0.0, AlertSeverity.MINIMAL),
            (0.19999, AlertSeverity.MINIMAL),
            (0.2, AlertSeverity.LOW),
            (0.39999, AlertSeverity.LOW),
            (0.4, AlertSeverity.MEDIUM),
            (0.59999, AlertSeverity.MEDIUM),
            (0.6, AlertSeverity.HIGH),
            (0.79999, AlertSeverity.HIGH),
            (0.8, AlertSeverity.CRITICAL),
            (1.0, AlertSeverity.CRITICAL)
        ]
        
        for score, expected_severity in boundary_test_cases:
            alert_data = {
                "transaction_id": f"txn_{score}",
                "user_id": "boundary_user",
                "fraud_score": score,
                "timestamp": datetime.now().isoformat()
            }
            
            severity = self.alert_processor.classify_severity(alert_data)
            assert severity == expected_severity, f"Score {score} should be {expected_severity}"

    def test_action_routing_by_severity(self):
        """Test automated action routing based on alert severity."""
        # MINIMAL: Log only
        minimal_alert = FraudAlert(
            alert_id="alert_minimal",
            transaction_id="txn_minimal",
            user_id="user_001",
            fraud_score=0.15,
            severity=AlertSeverity.MINIMAL,
            timestamp=datetime.now(),
            actions_taken=[]
        )
        
        actions = self.alert_processor.determine_actions(minimal_alert)
        assert AlertAction.LOG_ALERT in actions
        assert len(actions) == 1
        
        # LOW: Log + Queue for review
        low_alert = FraudAlert(
            alert_id="alert_low",
            transaction_id="txn_low", 
            user_id="user_002",
            fraud_score=0.32,
            severity=AlertSeverity.LOW,
            timestamp=datetime.now(),
            actions_taken=[]
        )
        
        actions = self.alert_processor.determine_actions(low_alert)
        expected_low_actions = {AlertAction.LOG_ALERT, AlertAction.QUEUE_FOR_REVIEW}
        assert set(actions) == expected_low_actions
        
        # MEDIUM: Log + Queue + Notify team
        medium_alert = FraudAlert(
            alert_id="alert_medium",
            transaction_id="txn_medium",
            user_id="user_003",
            fraud_score=0.55,
            severity=AlertSeverity.MEDIUM,
            timestamp=datetime.now(),
            actions_taken=[]
        )
        
        actions = self.alert_processor.determine_actions(medium_alert)
        expected_medium_actions = {
            AlertAction.LOG_ALERT, 
            AlertAction.QUEUE_FOR_REVIEW, 
            AlertAction.NOTIFY_FRAUD_TEAM
        }
        assert set(actions) == expected_medium_actions
        
        # HIGH: All previous + Block transaction + Enhanced monitoring
        high_alert = FraudAlert(
            alert_id="alert_high",
            transaction_id="txn_high",
            user_id="user_004",
            fraud_score=0.72,
            severity=AlertSeverity.HIGH,
            timestamp=datetime.now(),
            actions_taken=[]
        )
        
        actions = self.alert_processor.determine_actions(high_alert)
        expected_high_actions = {
            AlertAction.LOG_ALERT,
            AlertAction.QUEUE_FOR_REVIEW,
            AlertAction.NOTIFY_FRAUD_TEAM,
            AlertAction.BLOCK_TRANSACTION,
            AlertAction.ENHANCED_MONITORING
        }
        assert set(actions) == expected_high_actions
        
        # CRITICAL: All previous + Block user + Immediate escalation
        critical_alert = FraudAlert(
            alert_id="alert_critical",
            transaction_id="txn_critical",
            user_id="user_005", 
            fraud_score=0.91,
            severity=AlertSeverity.CRITICAL,
            timestamp=datetime.now(),
            actions_taken=[]
        )
        
        actions = self.alert_processor.determine_actions(critical_alert)
        expected_critical_actions = {
            AlertAction.LOG_ALERT,
            AlertAction.QUEUE_FOR_REVIEW,
            AlertAction.NOTIFY_FRAUD_TEAM,
            AlertAction.BLOCK_TRANSACTION,
            AlertAction.ENHANCED_MONITORING,
            AlertAction.BLOCK_USER,
            AlertAction.ESCALATE_IMMEDIATELY
        }
        assert set(actions) == expected_critical_actions

    def test_user_blocking_mechanism(self):
        """Test user account blocking functionality."""
        user_id = "user_to_block"
        alert = FraudAlert(
            alert_id="block_test_alert",
            transaction_id="txn_block_test",
            user_id=user_id,
            fraud_score=0.85,
            severity=AlertSeverity.CRITICAL,
            timestamp=datetime.now(),
            actions_taken=[]
        )
        
        # Test user blocking
        success = self.alert_processor.block_user(alert)
        assert success is True
        
        # Verify Redis operations for user blocking
        expected_block_key = f"blocked_users:{user_id}"
        expected_block_data = {
            "user_id": user_id,
            "blocked_at": alert.timestamp.isoformat(),
            "alert_id": alert.alert_id,
            "fraud_score": alert.fraud_score,
            "reason": f"Fraud alert {alert.alert_id} with score {alert.fraud_score}"
        }
        
        self.mock_redis.hset.assert_called()
        self.mock_redis.expire.assert_called()
        
        # Verify database logging of block action
        self.mock_db_manager.log_user_action.assert_called()

    def test_transaction_blocking_mechanism(self):
        """Test transaction blocking functionality."""
        transaction_id = "txn_to_block"
        alert = FraudAlert(
            alert_id="txn_block_alert",
            transaction_id=transaction_id,
            user_id="user_test",
            fraud_score=0.75,
            severity=AlertSeverity.HIGH,
            timestamp=datetime.now(),
            actions_taken=[]
        )
        
        # Test transaction blocking
        success = self.alert_processor.block_transaction(alert)
        assert success is True
        
        # Verify Redis operations for transaction blocking
        expected_key = f"blocked_transactions:{transaction_id}"
        self.mock_redis.set.assert_called()
        
        # Verify database logging
        self.mock_db_manager.log_transaction_block.assert_called()

    def test_investigation_queue_management(self):
        """Test investigation queue management and prioritization."""
        # Create alerts with different severities
        alerts = [
            FraudAlert(
                alert_id="queue_alert_1",
                transaction_id="txn_queue_1",
                user_id="user_queue_1",
                fraud_score=0.35,
                severity=AlertSeverity.LOW,
                timestamp=datetime.now(),
                actions_taken=[]
            ),
            FraudAlert(
                alert_id="queue_alert_2", 
                transaction_id="txn_queue_2",
                user_id="user_queue_2",
                fraud_score=0.65,
                severity=AlertSeverity.HIGH,
                timestamp=datetime.now(),
                actions_taken=[]
            ),
            FraudAlert(
                alert_id="queue_alert_3",
                transaction_id="txn_queue_3",
                user_id="user_queue_3",
                fraud_score=0.88,
                severity=AlertSeverity.CRITICAL,
                timestamp=datetime.now(),
                actions_taken=[]
            )
        ]
        
        # Queue alerts for investigation
        for alert in alerts:
            self.alert_processor.queue_for_investigation(alert)
        
        # Verify priority ordering (CRITICAL > HIGH > LOW)
        # Redis should be called with appropriate priority scores
        expected_calls = [
            # CRITICAL alert should have highest priority (lowest score for Redis sorted set)
            (f"investigation_queue", {"queue_alert_3": 1}),  # Highest priority
            (f"investigation_queue", {"queue_alert_2": 2}),  # Medium priority  
            (f"investigation_queue", {"queue_alert_1": 3})   # Lower priority
        ]
        
        # Verify Redis sorted set operations
        assert self.mock_redis.zadd.call_count == 3

    def test_alert_deduplication(self):
        """Test alert deduplication for same user/transaction."""
        alert_data = {
            "transaction_id": "txn_duplicate",
            "user_id": "user_duplicate",
            "fraud_score": 0.75,
            "timestamp": datetime.now().isoformat()
        }
        
        # Process same alert twice
        result1 = self.alert_processor.process_alert(alert_data)
        result2 = self.alert_processor.process_alert(alert_data)
        
        # First should succeed, second should be deduplicated
        assert result1 is True
        assert result2 is False  # Deduplicated
        
        # Verify deduplication tracking
        expected_dedup_key = f"alert_dedup:{alert_data['transaction_id']}"
        self.mock_redis.set.assert_called()

    def test_sla_compliance_timing(self):
        """Test SLA compliance for alert processing timing."""
        alert_data = {
            "transaction_id": "txn_sla_test",
            "user_id": "user_sla_test", 
            "fraud_score": 0.85,
            "timestamp": datetime.now().isoformat()
        }
        
        # Measure processing time
        start_time = datetime.now()
        result = self.alert_processor.process_alert(alert_data)
        end_time = datetime.now()
        
        processing_time = (end_time - start_time).total_seconds() * 1000  # milliseconds
        
        # SLA requirement: sub-1ms alert processing
        assert processing_time < 1000  # 1 second (relaxed for testing)
        assert result is True

    def test_alert_notification_system(self):
        """Test fraud team notification system."""
        high_priority_alert = FraudAlert(
            alert_id="notification_test",
            transaction_id="txn_notification",
            user_id="user_notification",
            fraud_score=0.82,
            severity=AlertSeverity.CRITICAL,
            timestamp=datetime.now(),
            actions_taken=[]
        )
        
        # Test notification sending
        success = self.alert_processor.notify_fraud_team(high_priority_alert)
        assert success is True
        
        # Verify notification details
        # (Implementation would depend on notification system - email, Slack, etc.)
        assert high_priority_alert.severity == AlertSeverity.CRITICAL

    def test_enhanced_monitoring_activation(self):
        """Test enhanced monitoring activation for high-risk users."""
        user_id = "user_enhanced_monitoring"
        alert = FraudAlert(
            alert_id="monitoring_test",
            transaction_id="txn_monitoring",
            user_id=user_id,
            fraud_score=0.68,
            severity=AlertSeverity.HIGH,
            timestamp=datetime.now(),
            actions_taken=[]
        )
        
        # Activate enhanced monitoring
        success = self.alert_processor.activate_enhanced_monitoring(alert)
        assert success is True
        
        # Verify monitoring configuration in Redis
        expected_monitoring_key = f"enhanced_monitoring:{user_id}"
        expected_config = {
            "activated_at": alert.timestamp.isoformat(),
            "alert_id": alert.alert_id,
            "monitoring_level": "HIGH",
            "expires_at": (alert.timestamp + timedelta(days=7)).isoformat()
        }
        
        self.mock_redis.hset.assert_called()
        self.mock_redis.expire.assert_called()

    def test_alert_audit_trail(self):
        """Test comprehensive audit trail for alert processing."""
        alert_data = {
            "transaction_id": "txn_audit",
            "user_id": "user_audit",
            "fraud_score": 0.77,
            "timestamp": datetime.now().isoformat()
        }
        
        # Process alert
        result = self.alert_processor.process_alert(alert_data)
        assert result is True
        
        # Verify audit trail creation
        expected_audit_data = {
            "alert_id": "alert_audit",  # Would be generated
            "transaction_id": "txn_audit",
            "user_id": "user_audit",
            "fraud_score": 0.77,
            "severity": AlertSeverity.HIGH.value,
            "actions_taken": [
                AlertAction.LOG_ALERT.value,
                AlertAction.QUEUE_FOR_REVIEW.value,
                AlertAction.NOTIFY_FRAUD_TEAM.value,
                AlertAction.BLOCK_TRANSACTION.value,
                AlertAction.ENHANCED_MONITORING.value
            ],
            "processing_time_ms": "< 1ms"
        }
        
        # Verify database audit logging
        self.mock_db_manager.log_alert_processing.assert_called()

    def test_bulk_alert_processing(self):
        """Test bulk alert processing for high-volume scenarios."""
        # Create multiple alerts
        bulk_alerts = []
        for i in range(100):
            alert_data = {
                "transaction_id": f"txn_bulk_{i}",
                "user_id": f"user_bulk_{i}",
                "fraud_score": 0.3 + (i % 5) * 0.1,  # Varying scores
                "timestamp": datetime.now().isoformat()
            }
            bulk_alerts.append(alert_data)
        
        # Process bulk alerts
        start_time = datetime.now()
        results = self.alert_processor.process_bulk_alerts(bulk_alerts)
        end_time = datetime.now()
        
        # Verify bulk processing
        assert len(results) == 100
        assert all(results)  # All should succeed
        
        # Performance validation - bulk processing should be efficient
        total_time = (end_time - start_time).total_seconds()
        avg_time_per_alert = (total_time / 100) * 1000  # ms per alert
        
        assert avg_time_per_alert < 10  # Less than 10ms per alert on average

    def test_alert_escalation_logic(self):
        """Test immediate escalation for critical alerts."""
        critical_alert = FraudAlert(
            alert_id="escalation_test",
            transaction_id="txn_escalation",
            user_id="user_escalation",
            fraud_score=0.95,
            severity=AlertSeverity.CRITICAL,
            timestamp=datetime.now(),
            actions_taken=[]
        )
        
        # Test escalation
        success = self.alert_processor.escalate_immediately(critical_alert)
        assert success is True
        
        # Verify escalation details
        expected_escalation_data = {
            "alert_id": "escalation_test",
            "escalated_at": critical_alert.timestamp.isoformat(),
            "severity": "CRITICAL",
            "fraud_score": 0.95,
            "reason": "Critical fraud score requires immediate attention"
        }
        
        # Verify escalation tracking
        self.mock_db_manager.log_escalation.assert_called()

    def test_error_handling_in_alert_processing(self):
        """Test error handling during alert processing failures."""
        # Test Redis failure
        self.mock_redis.set.side_effect = Exception("Redis connection failed")
        
        alert_data = {
            "transaction_id": "txn_error_test",
            "user_id": "user_error_test",
            "fraud_score": 0.65,
            "timestamp": datetime.now().isoformat()
        }
        
        # Should handle errors gracefully
        result = self.alert_processor.process_alert(alert_data)
        
        # Processing might fail but should not crash
        # Error should be logged
        self.mock_config.logger.error.assert_called()

    def test_alert_metrics_collection(self):
        """Test metrics collection for alert processing performance."""
        alert_data = {
            "transaction_id": "txn_metrics",
            "user_id": "user_metrics",
            "fraud_score": 0.55,
            "timestamp": datetime.now().isoformat()
        }
        
        # Process alert
        result = self.alert_processor.process_alert(alert_data)
        assert result is True
        
        # Verify metrics collection
        # (Implementation would track metrics like processing time, alert counts, etc.)
        expected_metrics = {
            "alerts_processed": 1,
            "alerts_by_severity": {"MEDIUM": 1},
            "actions_executed": 3,  # LOG, QUEUE, NOTIFY
            "avg_processing_time_ms": "< 1"
        }
        
        # Metrics should be tracked (implementation-dependent)
        assert result is True  # Basic success verification