"""
Unit Tests for Alert Processing System

Tests the alert processing pipeline including:
- Alert severity classification 
- Alert context enrichment
- Response action execution
- Audit trail management
- User risk profile updates
"""

import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from dataclasses import asdict
from pathlib import Path

import sys
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.alert_processor import AlertProcessor, AlertSeverity, AlertContext, AlertResponse, ResponseAction


class TestAlertProcessing:
    """Unit tests for alert processing functionality."""

    def setup_method(self):
        """Setup for each test method."""
        # Mock dependencies
        self.mock_redis = MagicMock()
        self.mock_config = Mock()
        self.mock_config.logger = Mock()
        self.mock_config.get_consumer_config.return_value = {"group.id": "test-group"}
        self.mock_config.get_producer_config.return_value = {}
        
        # Create alert processor with mocked dependencies
        with patch('consumers.alert_processor.get_kafka_config', return_value=self.mock_config):
            with patch('consumers.alert_processor.redis.Redis', return_value=self.mock_redis):
                with patch('consumers.alert_processor.Consumer'):
                    with patch('consumers.alert_processor.Producer'):
                        self.alert_processor = AlertProcessor()
                        self.alert_processor.redis_client = self.mock_redis

    def test_alert_severity_classification_low(self):
        """Test alert severity classification for low risk alerts."""
        # Test LOW severity alert
        alert = {
            "alert_id": "test_low_001",
            "user_id": "user_123",
            "fraud_score": 0.15,  # Low score
            "transaction_amount": 25.0,
            "timestamp": "2023-08-15T14:30:00"
        }
        
        severity = self.alert_processor.classify_alert_severity(alert)
        assert severity == AlertSeverity.LOW

    def test_alert_severity_classification_medium(self):
        """Test alert severity classification for medium risk alerts."""
        alert = {
            "alert_id": "test_medium_001",
            "user_id": "user_456",
            "fraud_score": 0.45,  # Medium score
            "transaction_amount": 150.0,
            "timestamp": "2023-08-15T14:30:00",
            "risk_factors": {
                "is_high_amount": True,
                "is_unusual_hour": True,  # 2 risk factors trigger medium
                "velocity_score": 3
            }
        }
        
        severity = self.alert_processor.classify_alert_severity(alert)
        assert severity == AlertSeverity.MEDIUM

    def test_alert_severity_classification_high(self):
        """Test alert severity classification for high risk alerts."""
        alert = {
            "alert_id": "test_high_001",
            "user_id": "user_789",
            "fraud_score": 0.75,  # High score (>= 0.7)
            "transaction_amount": 1000.0,
            "timestamp": "2023-08-15T14:30:00",
            "risk_factors": {
                "is_rapid_transaction": True,  # Required for HIGH classification
                "velocity_score": 20
            }
        }
        
        severity = self.alert_processor.classify_alert_severity(alert)
        assert severity == AlertSeverity.HIGH

    def test_alert_severity_classification_critical(self):
        """Test alert severity classification for critical alerts."""
        alert = {
            "alert_id": "test_critical_001",
            "user_id": "user_999",
            "fraud_score": 0.95,  # Critical score
            "transaction_amount": 5000.0,
            "timestamp": "2023-08-15T14:30:00"
        }
        
        severity = self.alert_processor.classify_alert_severity(alert)
        assert severity == AlertSeverity.CRITICAL

    def test_get_alert_context(self):
        """Test alert context enrichment."""
        # Mock Redis data for user profile
        self.mock_redis.hgetall.return_value = {
            "user_id": "user_123",
            "total_transactions": "50",
            "suspicious_activity_count": "2"
        }
        
        # Mock Redis data for alert history
        self.mock_redis.lrange.return_value = [
            json.dumps({"alert_id": "prev_001", "fraud_score": 0.3}),
            json.dumps({"alert_id": "prev_002", "fraud_score": 0.2})
        ]
        
        alert = {
            "alert_id": "test_context_001",
            "user_id": "user_123",
            "fraud_score": 0.6,
            "transaction_amount": 200.0,
            "timestamp": "2023-08-15T14:30:00"
        }
        
        context = self.alert_processor.get_alert_context(alert)
        
        assert isinstance(context, AlertContext)
        assert context.original_alert == alert
        assert "user_id" in context.user_risk_profile
        assert len(context.historical_alerts) >= 0
        assert context.recommended_action in ResponseAction
        assert 0.0 <= context.confidence_score <= 1.0

    def test_execute_response_action_log_only(self):
        """Test execution of log-only response action."""
        alert_context = AlertContext(
            original_alert={"alert_id": "test_001", "user_id": "user_123"},
            user_risk_profile={"risk_level": "low"},
            historical_alerts=[],
            transaction_pattern={"normal": True},
            recommended_action=ResponseAction.LOG_ONLY,
            confidence_score=0.8,
            enrichment_timestamp="2023-08-15T14:30:00"
        )
        
        response = self.alert_processor.execute_response_action(alert_context, ResponseAction.LOG_ONLY)
        
        assert isinstance(response, AlertResponse)
        assert response.action == ResponseAction.LOG_ONLY
        assert response.status == "completed"

    def test_execute_response_action_notify_team(self):
        """Test execution of team notification action."""
        alert_context = AlertContext(
            original_alert={"alert_id": "test_002", "user_id": "user_456", "fraud_score": 0.6},
            user_risk_profile={"risk_level": "medium"},
            historical_alerts=[],
            transaction_pattern={"suspicious": True},
            recommended_action=ResponseAction.NOTIFY_TEAM,
            confidence_score=0.7,
            enrichment_timestamp="2023-08-15T14:30:00"
        )
        
        response = self.alert_processor.execute_response_action(alert_context, ResponseAction.NOTIFY_TEAM)
        
        assert isinstance(response, AlertResponse)
        assert response.action == ResponseAction.NOTIFY_TEAM
        assert response.status in ["completed", "pending"]

    def test_execute_response_action_immediate_block(self):
        """Test execution of immediate block action."""
        alert_context = AlertContext(
            original_alert={"alert_id": "test_003", "user_id": "user_789", "fraud_score": 0.9},
            user_risk_profile={"risk_level": "high"},
            historical_alerts=[],
            transaction_pattern={"fraud_indicators": True},
            recommended_action=ResponseAction.IMMEDIATE_BLOCK,
            confidence_score=0.95,
            enrichment_timestamp="2023-08-15T14:30:00"
        )
        
        response = self.alert_processor.execute_response_action(alert_context, ResponseAction.IMMEDIATE_BLOCK)
        
        assert isinstance(response, AlertResponse)
        assert response.action == ResponseAction.IMMEDIATE_BLOCK
        assert "user_blocked" in response.details  # Key should be user_blocked, not just block

    def test_update_user_risk_profile(self):
        """Test user risk profile update after alert response."""
        alert_response = AlertResponse(
            alert_id="test_profile_001",
            response_id="resp_001",
            timestamp="2023-08-15T14:30:00",
            severity=AlertSeverity.HIGH,
            action=ResponseAction.IMMEDIATE_BLOCK,
            response_time_ms=150.0,
            details={"blocked": True}
        )
        
        user_id = "user_123"
        
        # Should not raise exception
        self.alert_processor.update_user_risk_profile(user_id, alert_response)
        
        # Verify Redis operations were called
        assert self.mock_redis.hincrby.called or self.mock_redis.hset.called

    def test_store_audit_trail(self):
        """Test audit trail storage."""
        alert_response = AlertResponse(
            alert_id="test_audit_001",
            response_id="resp_audit_001",
            timestamp="2023-08-15T14:30:00",
            severity=AlertSeverity.MEDIUM,
            action=ResponseAction.MANUAL_REVIEW,
            response_time_ms=200.0,
            details={"review_assigned": True},
            assignee="fraud_analyst_1"
        )
        
        # Should not raise exception
        self.alert_processor.store_audit_trail(alert_response)
        
        # Verify Redis operations were called for audit storage
        assert self.mock_redis.lpush.called or self.mock_redis.hset.called

    def test_publish_response(self):
        """Test response publication to Kafka."""
        alert_response = AlertResponse(
            alert_id="test_publish_001",
            response_id="resp_publish_001",
            timestamp="2023-08-15T14:30:00",
            severity=AlertSeverity.LOW,
            action=ResponseAction.LOG_ONLY,
            response_time_ms=50.0,
            details={"logged": True}
        )
        
        # Mock producer
        mock_producer = Mock()
        self.alert_processor.producer = mock_producer
        
        # Should not raise exception
        self.alert_processor.publish_response(alert_response)
        
        # Verify producer was called
        assert mock_producer.produce.called

    def test_process_alert_integration(self):
        """Test complete alert processing workflow."""
        # Mock Redis responses for user profile and history
        self.mock_redis.hgetall.return_value = {"user_id": "user_123", "total_transactions": "10"}
        self.mock_redis.lrange.return_value = []
        
        # Mock producer
        mock_producer = Mock()
        self.alert_processor.producer = mock_producer
        
        alert = {
            "alert_id": "test_integration_001",
            "user_id": "user_123",
            "fraud_score": 0.4,
            "transaction_amount": 100.0,
            "timestamp": "2023-08-15T14:30:00"
        }
        
        # Should not raise exception
        self.alert_processor.process_alert(alert)
        
        # Verify the workflow executed (producer should be called)
        assert mock_producer.produce.called

    def test_alert_response_to_dict(self):
        """Test AlertResponse serialization."""
        response = AlertResponse(
            alert_id="test_serialization_001",
            response_id="resp_serial_001",
            timestamp="2023-08-15T14:30:00",
            severity=AlertSeverity.HIGH,
            action=ResponseAction.AUTO_INVESTIGATE,
            response_time_ms=300.0,
            details={"investigation_started": True}
        )
        
        response_dict = response.to_dict()
        
        assert isinstance(response_dict, dict)
        assert response_dict["alert_id"] == "test_serialization_001"
        assert response_dict["severity"] == "high"  # Enum converted to string
        assert response_dict["action"] == "auto_investigate"  # Enum converted to string
        assert response_dict["response_time_ms"] == 300.0

    def test_alert_severity_enum_values(self):
        """Test AlertSeverity enum values."""
        assert AlertSeverity.LOW.value == "low"
        assert AlertSeverity.MEDIUM.value == "medium"
        assert AlertSeverity.HIGH.value == "high"
        assert AlertSeverity.CRITICAL.value == "critical"

    def test_response_action_enum_values(self):
        """Test ResponseAction enum values."""
        assert ResponseAction.LOG_ONLY.value == "log_only"
        assert ResponseAction.NOTIFY_TEAM.value == "notify_team"
        assert ResponseAction.MANUAL_REVIEW.value == "manual_review"
        assert ResponseAction.AUTO_INVESTIGATE.value == "auto_investigate"
        assert ResponseAction.IMMEDIATE_BLOCK.value == "immediate_block"
        assert ResponseAction.ESCALATE.value == "escalate"

    def test_exception_handling_in_classify_severity(self):
        """Test exception handling in alert severity classification."""
        # Alert with missing required fields
        invalid_alert = {
            "alert_id": "test_invalid_001"
            # Missing fraud_score and other required fields
        }
        
        # Should handle gracefully and return a default severity
        severity = self.alert_processor.classify_alert_severity(invalid_alert)
        assert severity in AlertSeverity

    def test_exception_handling_in_get_context(self):
        """Test exception handling in alert context retrieval."""
        # Mock Redis to raise exception
        self.mock_redis.hgetall.side_effect = Exception("Redis connection error")
        
        alert = {
            "alert_id": "test_exception_001",
            "user_id": "user_123",
            "fraud_score": 0.5
        }
        
        # Should handle gracefully
        context = self.alert_processor.get_alert_context(alert)
        assert isinstance(context, AlertContext)
        assert context.original_alert == alert