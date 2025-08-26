# /stream-sentinel/src/consumers/alert_processor.py

"""
Alert Response System for Stream-Sentinel

This module implements the downstream alert processing system that consumes
fraud alerts, classifies them by severity, routes appropriate responses,
and maintains audit trails. It demonstrates complete end-to-end fraud
detection pipeline with business value delivery.

Key distributed systems concepts:
- Event-driven response automation
- Multi-channel alert routing and notification
- Audit trail persistence for compliance
- Response time tracking and SLA monitoring
- External system integration patterns
"""

import json
import time
import signal
import sys
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
from confluent_kafka import Consumer, Producer, KafkaError, KafkaException
import redis
import logging
from pathlib import Path

# Import our configuration system
sys.path.append(str(Path(__file__).parent.parent))
from kafka.config import get_kafka_config


class AlertSeverity(Enum):
    """Alert severity classification levels."""
    LOW = "low"
    MEDIUM = "medium" 
    HIGH = "high"
    CRITICAL = "critical"


class ResponseAction(Enum):
    """Possible response actions for alerts."""
    LOG_ONLY = "log_only"
    NOTIFY_TEAM = "notify_team"
    MANUAL_REVIEW = "manual_review"
    AUTO_INVESTIGATE = "auto_investigate"
    IMMEDIATE_BLOCK = "immediate_block"
    ESCALATE = "escalate"


@dataclass
class AlertResponse:
    """Response action taken for a fraud alert."""
    alert_id: str
    response_id: str
    timestamp: str
    severity: AlertSeverity
    action: ResponseAction
    response_time_ms: float
    details: Dict[str, Any]
    assignee: Optional[str] = None
    status: str = "pending"
    notes: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = asdict(self)
        result['severity'] = self.severity.value
        result['action'] = self.action.value
        return result


@dataclass 
class AlertContext:
    """Enhanced alert context with additional analysis."""
    original_alert: Dict[str, Any]
    user_risk_profile: Dict[str, Any]
    historical_alerts: List[Dict[str, Any]]
    transaction_pattern: Dict[str, Any]
    recommended_action: ResponseAction
    confidence_score: float
    enrichment_timestamp: str


class AlertProcessor:
    """
    Alert Response System Consumer
    
    Consumes fraud alerts from the fraud-alerts topic, classifies severity,
    routes appropriate responses, and maintains comprehensive audit trails
    for compliance and monitoring.
    """
    
    def __init__(self,
                 consumer_group: str = "alert-response-group",
                 notification_email: Optional[str] = None):
        """
        Initialize alert processing system.
        
        Args:
            consumer_group: Kafka consumer group for parallel processing
            notification_email: Email address for alert notifications
        """
        # Initialize Kafka configuration
        self.kafka_config = get_kafka_config()
        self.logger = self._setup_logging()
        self.consumer_group = consumer_group
        self.notification_email = notification_email or "fraud-team@company.com"
        
        # Topics
        self.input_topic = "fraud-alerts"
        self.output_topic = "alert-responses"
        
        # Initialize Kafka consumer and producer
        self.consumer = self._create_consumer()
        self.producer = self._create_producer()
        
        # Initialize Redis for alert history and context
        self.redis_client = self._create_redis_client()
        
        # Alert processing statistics
        self.processed_alerts = 0
        self.blocked_users = 0
        self.notifications_sent = 0
        self.start_time = time.time()
        
        # Response SLA targets (milliseconds)
        self.sla_targets = {
            AlertSeverity.CRITICAL: 1000,    # 1 second
            AlertSeverity.HIGH: 5000,        # 5 seconds  
            AlertSeverity.MEDIUM: 30000,     # 30 seconds
            AlertSeverity.LOW: 300000        # 5 minutes
        }
        
        # Graceful shutdown
        self.running = True
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info(
            f"AlertProcessor initialized - group: {consumer_group}, "
            f"notifications: {self.notification_email}"
        )
    
    def _setup_logging(self) -> logging.Logger:
        """Setup logging for alert processing operations."""
        logger = logging.getLogger("stream_sentinel.alert_processor")
        
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            
        return logger
    
    def _create_consumer(self) -> Consumer:
        """Create Kafka consumer for alert processing."""
        consumer_config = self.kafka_config.get_consumer_config(
            self.consumer_group, "alert_processor"
        )
        consumer = Consumer(consumer_config)
        
        # Subscribe to fraud alerts topic
        consumer.subscribe([self.input_topic])
        self.logger.info(f"Consumer subscribed to {self.input_topic}")
        
        return consumer
    
    def _create_producer(self) -> Producer:
        """Create Kafka producer for response publishing."""
        producer_config = self.kafka_config.get_producer_config("transaction")
        producer = Producer(producer_config)
        
        self.logger.info("Producer created for alert responses")
        return producer
    
    def _create_redis_client(self) -> redis.Redis:
        """Create Redis client for alert context and audit trail."""
        try:
            client = redis.Redis(
                host='localhost',
                port=6379,
                db=1,  # Use different database from fraud detector
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            
            # Test connection
            client.ping()
            self.logger.info("Redis client connected successfully")
            return client
            
        except redis.ConnectionError as e:
            self.logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    def _signal_handler(self, signum: int, frame) -> None:
        """Handle graceful shutdown signals."""
        self.logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.running = False
    
    def classify_alert_severity(self, alert: Dict[str, Any]) -> AlertSeverity:
        """
        Classify alert severity based on fraud score and risk factors.
        
        Args:
            alert: Fraud alert from fraud detection system
            
        Returns:
            AlertSeverity classification
        """
        fraud_score = alert.get('fraud_score', 0.0)
        risk_factors = alert.get('risk_factors', {})
        transaction_details = alert.get('transaction_details', {})
        
        # Critical: Very high fraud score with multiple risk factors
        if fraud_score >= 0.9:
            return AlertSeverity.CRITICAL
            
        # High: High fraud score or high-value transaction with risk factors
        if (fraud_score >= 0.7 or 
            (transaction_details.get('amount', 0) >= 1000 and fraud_score >= 0.5)):
            
            # Check for velocity fraud or unusual patterns
            if (risk_factors.get('is_rapid_transaction') or
                risk_factors.get('velocity_score', 0) > 15):
                return AlertSeverity.HIGH
        
        # Medium: Moderate fraud score with some risk indicators
        if fraud_score >= 0.4:
            risk_count = sum([
                risk_factors.get('is_high_amount', False),
                risk_factors.get('is_unusual_hour', False), 
                risk_factors.get('is_rapid_transaction', False),
                risk_factors.get('velocity_score', 0) > 5
            ])
            
            if risk_count >= 2:
                return AlertSeverity.MEDIUM
        
        # Low: Lower fraud scores or single risk factors
        return AlertSeverity.LOW
    
    def get_alert_context(self, alert: Dict[str, Any]) -> AlertContext:
        """
        Enrich alert with additional context from user history and patterns.
        
        Args:
            alert: Original fraud alert
            
        Returns:
            AlertContext with enriched information
        """
        user_id = alert.get('user_id')
        start_time = time.time()
        
        # Get user risk profile from Redis
        user_risk_profile = self._get_user_risk_profile(user_id)
        
        # Get historical alerts for this user (last 30 days)
        historical_alerts = self._get_user_alert_history(user_id, days=30)
        
        # Analyze transaction patterns
        transaction_pattern = self._analyze_transaction_pattern(alert, historical_alerts)
        
        # Determine recommended action based on context
        recommended_action, confidence_score = self._recommend_action(
            alert, user_risk_profile, historical_alerts, transaction_pattern
        )
        
        return AlertContext(
            original_alert=alert,
            user_risk_profile=user_risk_profile,
            historical_alerts=historical_alerts,
            transaction_pattern=transaction_pattern,
            recommended_action=recommended_action,
            confidence_score=confidence_score,
            enrichment_timestamp=datetime.now().isoformat()
        )
    
    def _get_user_risk_profile(self, user_id: str) -> Dict[str, Any]:
        """Get user risk profile from Redis or create default."""
        try:
            profile_data = self.redis_client.hgetall(f"user_risk:{user_id}")
            
            if profile_data:
                return {
                    "user_id": profile_data.get('user_id', user_id),
                    "risk_level": profile_data.get('risk_level', 'medium'),
                    "total_alerts": int(profile_data.get('total_alerts', 0)),
                    "confirmed_fraud_count": int(profile_data.get('confirmed_fraud_count', 0)),
                    "false_positive_count": int(profile_data.get('false_positive_count', 0)),
                    "last_alert_date": profile_data.get('last_alert_date'),
                    "account_age_days": int(profile_data.get('account_age_days', 30)),
                    "is_high_value_customer": profile_data.get('is_high_value_customer') == 'true'
                }
            else:
                # Create default risk profile for new user
                return {
                    "user_id": user_id,
                    "risk_level": "medium",
                    "total_alerts": 0,
                    "confirmed_fraud_count": 0,
                    "false_positive_count": 0,
                    "last_alert_date": None,
                    "account_age_days": 30,
                    "is_high_value_customer": False
                }
                
        except Exception as e:
            self.logger.error(f"Error getting user risk profile for {user_id}: {e}")
            return {"user_id": user_id, "risk_level": "medium", "total_alerts": 0}
    
    def _get_user_alert_history(self, user_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """Get historical alerts for user from Redis."""
        try:
            # Get alert IDs for user from sorted set (sorted by timestamp)
            cutoff_timestamp = (datetime.now() - timedelta(days=days)).timestamp()
            alert_ids = self.redis_client.zrangebyscore(
                f"user_alerts:{user_id}",
                cutoff_timestamp,
                '+inf'
            )
            
            # Get alert details
            alerts = []
            for alert_id in alert_ids:
                alert_data = self.redis_client.hgetall(f"alert:{alert_id}")
                if alert_data:
                    alerts.append({
                        "alert_id": alert_id,
                        "timestamp": alert_data.get('timestamp'),
                        "fraud_score": float(alert_data.get('fraud_score', 0)),
                        "severity": alert_data.get('severity', 'low'),
                        "action": alert_data.get('action', 'log_only'),
                        "status": alert_data.get('status', 'pending')
                    })
                    
            return alerts[:50]  # Limit to last 50 alerts
            
        except Exception as e:
            self.logger.error(f"Error getting alert history for {user_id}: {e}")
            return []
    
    def _analyze_transaction_pattern(self, alert: Dict[str, Any], 
                                   historical_alerts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze transaction patterns for additional context."""
        transaction_details = alert.get('transaction_details', {})
        risk_factors = alert.get('risk_factors', {})
        
        # Count recent alerts
        recent_alerts_24h = len([
            a for a in historical_alerts
            if self._is_within_hours(a.get('timestamp'), 24)
        ])
        
        recent_alerts_7d = len([
            a for a in historical_alerts 
            if self._is_within_hours(a.get('timestamp'), 24 * 7)
        ])
        
        # Calculate escalation indicators
        avg_fraud_score = sum(a.get('fraud_score', 0) for a in historical_alerts) / max(len(historical_alerts), 1)
        
        return {
            "recent_alerts_24h": recent_alerts_24h,
            "recent_alerts_7d": recent_alerts_7d,
            "avg_historical_fraud_score": avg_fraud_score,
            "is_repeat_offender": recent_alerts_7d >= 3,
            "is_escalating_pattern": alert.get('fraud_score', 0) > avg_fraud_score * 1.5,
            "transaction_amount": transaction_details.get('amount', 0),
            "velocity_score": risk_factors.get('velocity_score', 0)
        }
    
    def _is_within_hours(self, timestamp_str: Optional[str], hours: int) -> bool:
        """Check if timestamp is within specified hours."""
        if not timestamp_str:
            return False
            
        try:
            alert_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            cutoff = datetime.now() - timedelta(hours=hours)
            return alert_time >= cutoff
        except:
            return False
    
    def _recommend_action(self, alert: Dict[str, Any], 
                         user_risk_profile: Dict[str, Any],
                         historical_alerts: List[Dict[str, Any]],
                         transaction_pattern: Dict[str, Any]) -> Tuple[ResponseAction, float]:
        """
        Recommend response action based on alert context.
        
        Returns:
            Tuple of (ResponseAction, confidence_score)
        """
        fraud_score = alert.get('fraud_score', 0.0)
        user_risk_level = user_risk_profile.get('risk_level', 'medium')
        confirmed_fraud_count = user_risk_profile.get('confirmed_fraud_count', 0)
        is_repeat_offender = transaction_pattern.get('is_repeat_offender', False)
        recent_alerts_24h = transaction_pattern.get('recent_alerts_24h', 0)
        
        # Critical cases - immediate action required
        if fraud_score >= 0.9 or (fraud_score >= 0.7 and is_repeat_offender):
            return ResponseAction.IMMEDIATE_BLOCK, 0.95
        
        # High fraud score with concerning patterns
        if fraud_score >= 0.8:
            if confirmed_fraud_count > 0 or recent_alerts_24h >= 3:
                return ResponseAction.AUTO_INVESTIGATE, 0.90
            else:
                return ResponseAction.MANUAL_REVIEW, 0.85
        
        # Medium-high fraud scores
        if fraud_score >= 0.6:
            if user_risk_level == 'high' or recent_alerts_24h >= 2:
                return ResponseAction.MANUAL_REVIEW, 0.80
            else:
                return ResponseAction.NOTIFY_TEAM, 0.75
        
        # Lower fraud scores but concerning patterns
        if is_repeat_offender or recent_alerts_24h >= 5:
            return ResponseAction.ESCALATE, 0.70
        
        # Default action for lower risk alerts
        if fraud_score >= 0.3:
            return ResponseAction.NOTIFY_TEAM, 0.60
        else:
            return ResponseAction.LOG_ONLY, 0.50
    
    def execute_response_action(self, alert_context: AlertContext, 
                               severity: AlertSeverity) -> AlertResponse:
        """
        Execute the recommended response action.
        
        Args:
            alert_context: Enriched alert context
            severity: Classified alert severity
            
        Returns:
            AlertResponse with execution details
        """
        start_time = time.time()
        alert = alert_context.original_alert
        action = alert_context.recommended_action
        
        response_id = f"resp_{alert.get('alert_id', 'unknown')}_{int(start_time)}"
        
        # Execute action based on type
        execution_details = {}
        
        try:
            if action == ResponseAction.IMMEDIATE_BLOCK:
                execution_details = self._execute_immediate_block(alert, alert_context)
                
            elif action == ResponseAction.AUTO_INVESTIGATE:
                execution_details = self._execute_auto_investigate(alert, alert_context)
                
            elif action == ResponseAction.MANUAL_REVIEW:
                execution_details = self._execute_manual_review(alert, alert_context)
                
            elif action == ResponseAction.ESCALATE:
                execution_details = self._execute_escalate(alert, alert_context)
                
            elif action == ResponseAction.NOTIFY_TEAM:
                execution_details = self._execute_notify_team(alert, alert_context)
                
            else:  # LOG_ONLY
                execution_details = self._execute_log_only(alert, alert_context)
            
            execution_details['success'] = True
            
        except Exception as e:
            self.logger.error(f"Error executing action {action.value}: {e}")
            execution_details = {
                'success': False,
                'error': str(e),
                'action_attempted': action.value
            }
        
        response_time = (time.time() - start_time) * 1000  # Convert to milliseconds
        
        return AlertResponse(
            alert_id=alert.get('alert_id', 'unknown'),
            response_id=response_id,
            timestamp=datetime.now().isoformat(),
            severity=severity,
            action=action,
            response_time_ms=response_time,
            details=execution_details,
            status="completed" if execution_details.get('success') else "failed"
        )
    
    def _execute_immediate_block(self, alert: Dict[str, Any], 
                                context: AlertContext) -> Dict[str, Any]:
        """Execute immediate user blocking action."""
        user_id = alert.get('user_id')
        
        # Add user to blocked list in Redis
        self.redis_client.sadd("blocked_users", user_id)
        self.redis_client.hset(
            f"block:{user_id}",
            mapping={
                "blocked_at": datetime.now().isoformat(),
                "reason": "fraud_alert",
                "alert_id": alert.get('alert_id'),
                "fraud_score": alert.get('fraud_score', 0),
                "blocked_by": "auto_system"
            }
        )
        
        # Set expiration (24 hours for auto-blocks)
        self.redis_client.expire(f"block:{user_id}", 86400)
        
        self.blocked_users += 1
        
        # Send high-priority notification
        self._send_notification(
            f"IMMEDIATE BLOCK: User {user_id}",
            f"User {user_id} has been automatically blocked due to fraud score {alert.get('fraud_score', 0):.3f}. "
            f"Alert ID: {alert.get('alert_id')}",
            priority="critical"
        )
        
        self.logger.warning(
            f"IMMEDIATE BLOCK executed for user {user_id}, "
            f"fraud score: {alert.get('fraud_score', 0):.3f}"
        )
        
        return {
            "action": "immediate_block",
            "user_blocked": user_id,
            "block_duration_hours": 24,
            "notification_sent": True
        }
    
    def _execute_auto_investigate(self, alert: Dict[str, Any], 
                                 context: AlertContext) -> Dict[str, Any]:
        """Execute automatic investigation workflow."""
        user_id = alert.get('user_id')
        
        # Add to investigation queue in Redis
        investigation_data = {
            "alert_id": alert.get('alert_id'),
            "user_id": user_id,
            "fraud_score": alert.get('fraud_score', 0),
            "created_at": datetime.now().isoformat(),
            "status": "pending",
            "priority": "high",
            "assigned_to": "auto_investigator"
        }
        
        investigation_id = f"inv_{alert.get('alert_id')}_{int(time.time())}"
        self.redis_client.hset(f"investigation:{investigation_id}", mapping=investigation_data)
        self.redis_client.lpush("investigation_queue", investigation_id)
        
        # Set TTL for investigation (7 days)
        self.redis_client.expire(f"investigation:{investigation_id}", 604800)
        
        # Send notification to investigation team
        self._send_notification(
            f"Auto Investigation: User {user_id}",
            f"High-risk transaction detected for user {user_id}. "
            f"Fraud score: {alert.get('fraud_score', 0):.3f}. "
            f"Investigation ID: {investigation_id}",
            priority="high"
        )
        
        return {
            "action": "auto_investigate",
            "investigation_id": investigation_id,
            "queue_position": self.redis_client.llen("investigation_queue"),
            "notification_sent": True
        }
    
    def _execute_manual_review(self, alert: Dict[str, Any], 
                              context: AlertContext) -> Dict[str, Any]:
        """Execute manual review workflow."""
        user_id = alert.get('user_id')
        
        # Add to manual review queue
        review_data = {
            "alert_id": alert.get('alert_id'),
            "user_id": user_id,
            "fraud_score": alert.get('fraud_score', 0),
            "created_at": datetime.now().isoformat(),
            "status": "pending",
            "priority": "medium",
            "requires_human_review": True
        }
        
        review_id = f"review_{alert.get('alert_id')}_{int(time.time())}"
        self.redis_client.hset(f"review:{review_id}", mapping=review_data)
        self.redis_client.lpush("manual_review_queue", review_id)
        
        # Set TTL for review (3 days)
        self.redis_client.expire(f"review:{review_id}", 259200)
        
        return {
            "action": "manual_review",
            "review_id": review_id,
            "queue_position": self.redis_client.llen("manual_review_queue"),
            "notification_sent": False
        }
    
    def _execute_escalate(self, alert: Dict[str, Any], 
                         context: AlertContext) -> Dict[str, Any]:
        """Execute escalation to senior team."""
        user_id = alert.get('user_id')
        
        # Send escalation notification
        self._send_notification(
            f"ESCALATION: Pattern Alert for User {user_id}",
            f"Concerning pattern detected for user {user_id}. "
            f"Recent alerts: {context.transaction_pattern.get('recent_alerts_24h', 0)} in 24h. "
            f"Current fraud score: {alert.get('fraud_score', 0):.3f}. "
            f"Requires senior review.",
            priority="high"
        )
        
        return {
            "action": "escalate",
            "escalated_to": "senior_fraud_team",
            "notification_sent": True,
            "reason": "repeat_offender_pattern"
        }
    
    def _execute_notify_team(self, alert: Dict[str, Any], 
                            context: AlertContext) -> Dict[str, Any]:
        """Execute team notification."""
        user_id = alert.get('user_id')
        
        # Send standard team notification
        self._send_notification(
            f"Fraud Alert: User {user_id}",
            f"Moderate fraud alert for user {user_id}. "
            f"Fraud score: {alert.get('fraud_score', 0):.3f}. "
            f"Transaction amount: ${alert.get('transaction_details', {}).get('amount', 0):.2f}",
            priority="medium"
        )
        
        return {
            "action": "notify_team",
            "notification_sent": True,
            "channel": "email"
        }
    
    def _execute_log_only(self, alert: Dict[str, Any], 
                         context: AlertContext) -> Dict[str, Any]:
        """Execute logging-only action."""
        user_id = alert.get('user_id')
        
        self.logger.info(
            f"Low-risk fraud alert logged for user {user_id}, "
            f"fraud score: {alert.get('fraud_score', 0):.3f}"
        )
        
        return {
            "action": "log_only",
            "logged": True,
            "log_level": "info"
        }
    
    def _send_notification(self, subject: str, message: str, priority: str = "medium"):
        """
        Send notification via configured channels.
        
        Args:
            subject: Notification subject
            message: Notification message
            priority: Priority level (low/medium/high/critical)
        """
        try:
            # For now, just log the notification (could extend to email/Slack/etc)
            log_level = {
                "critical": logging.CRITICAL,
                "high": logging.ERROR,
                "medium": logging.WARNING,
                "low": logging.INFO
            }.get(priority, logging.INFO)
            
            self.logger.log(log_level, f"NOTIFICATION [{priority.upper()}]: {subject} - {message}")
            self.notifications_sent += 1
            
            # Store notification in Redis for audit trail
            notification_data = {
                "subject": subject,
                "message": message,
                "priority": priority,
                "sent_at": datetime.now().isoformat(),
                "recipient": self.notification_email,
                "status": "sent"
            }
            
            notification_id = f"notif_{int(time.time())}_{self.notifications_sent}"
            self.redis_client.hset(f"notification:{notification_id}", mapping=notification_data)
            self.redis_client.expire(f"notification:{notification_id}", 2592000)  # 30 days
            
        except Exception as e:
            self.logger.error(f"Failed to send notification: {e}")
    
    def update_user_risk_profile(self, user_id: str, alert_response: AlertResponse):
        """Update user risk profile based on alert response."""
        try:
            # Get current profile
            current_profile = self._get_user_risk_profile(user_id)
            
            # Update statistics
            current_profile['total_alerts'] += 1
            current_profile['last_alert_date'] = datetime.now().isoformat()
            
            # Adjust risk level based on recent activity
            if alert_response.severity == AlertSeverity.CRITICAL:
                current_profile['risk_level'] = 'high'
            elif alert_response.severity == AlertSeverity.HIGH and current_profile['total_alerts'] >= 3:
                current_profile['risk_level'] = 'high'
            elif current_profile['total_alerts'] >= 10:
                current_profile['risk_level'] = 'high'
            
            # Save updated profile
            self.redis_client.hset(f"user_risk:{user_id}", mapping=current_profile)
            self.redis_client.expire(f"user_risk:{user_id}", 7776000)  # 90 days
            
        except Exception as e:
            self.logger.error(f"Error updating user risk profile for {user_id}: {e}")
    
    def store_audit_trail(self, alert_response: AlertResponse):
        """Store alert response in audit trail."""
        try:
            # Store detailed response
            self.redis_client.hset(
                f"alert_response:{alert_response.response_id}",
                mapping=alert_response.to_dict()
            )
            self.redis_client.expire(f"alert_response:{alert_response.response_id}", 31536000)  # 1 year
            
            # Add to user's alert timeline
            alert_timestamp = datetime.fromisoformat(alert_response.timestamp).timestamp()
            self.redis_client.zadd(
                f"user_alerts:{alert_response.alert_id.split('_')[1] if '_' in alert_response.alert_id else 'unknown'}",
                {alert_response.response_id: alert_timestamp}
            )
            
        except Exception as e:
            self.logger.error(f"Error storing audit trail: {e}")
    
    def publish_response(self, alert_response: AlertResponse):
        """Publish alert response to Kafka topic."""
        try:
            response_message = {
                "response_id": alert_response.response_id,
                "alert_id": alert_response.alert_id,
                "timestamp": alert_response.timestamp,
                "severity": alert_response.severity.value,
                "action": alert_response.action.value,
                "response_time_ms": alert_response.response_time_ms,
                "status": alert_response.status,
                "details": alert_response.details
            }
            
            self.producer.produce(
                self.output_topic,
                key=alert_response.alert_id,
                value=json.dumps(response_message),
                callback=self._delivery_callback
            )
            
            # Poll for delivery callbacks
            self.producer.poll(0)
            
        except Exception as e:
            self.logger.error(f"Error publishing alert response: {e}")
    
    def _delivery_callback(self, err, msg):
        """Callback for message delivery confirmation."""
        if err is not None:
            self.logger.error(f"Failed to deliver alert response: {err}")
        else:
            self.logger.debug(
                f"Alert response delivered to {msg.topic()} [partition {msg.partition()}]"
            )
    
    def process_alert(self, alert: Dict[str, Any]):
        """
        Process a single fraud alert through the response pipeline.
        
        Args:
            alert: Fraud alert from fraud detection system
        """
        try:
            process_start_time = time.time()
            alert_id = alert.get('alert_id', 'unknown')
            
            # Step 1: Classify severity
            severity = self.classify_alert_severity(alert)
            
            # Step 2: Enrich with context
            alert_context = self.get_alert_context(alert)
            
            # Step 3: Execute response action
            alert_response = self.execute_response_action(alert_context, severity)
            
            # Step 4: Update user risk profile
            user_id = alert.get('user_id')
            if user_id:
                self.update_user_risk_profile(user_id, alert_response)
            
            # Step 5: Store audit trail
            self.store_audit_trail(alert_response)
            
            # Step 6: Publish response
            self.publish_response(alert_response)
            
            # Check SLA compliance
            total_processing_time = (time.time() - process_start_time) * 1000
            sla_target = self.sla_targets.get(severity, 300000)
            sla_met = total_processing_time <= sla_target
            
            self.processed_alerts += 1
            
            # Log processing results
            if not sla_met:
                self.logger.warning(
                    f"SLA MISS: Alert {alert_id} processed in {total_processing_time:.1f}ms "
                    f"(target: {sla_target}ms), severity: {severity.value}, "
                    f"action: {alert_response.action.value}"
                )
            else:
                self.logger.info(
                    f"Alert processed: {alert_id}, severity: {severity.value}, "
                    f"action: {alert_response.action.value}, "
                    f"time: {total_processing_time:.1f}ms"
                )
            
            # Log statistics every 100 alerts
            if self.processed_alerts % 100 == 0:
                elapsed = time.time() - self.start_time
                aps = self.processed_alerts / elapsed  # Alerts per second
                
                self.logger.info(
                    f"Alert processing stats - Processed: {self.processed_alerts}, "
                    f"Blocked users: {self.blocked_users}, "
                    f"Notifications: {self.notifications_sent}, "
                    f"APS: {aps:.1f}"
                )
                
        except Exception as e:
            self.logger.error(f"Error processing alert {alert.get('alert_id', 'unknown')}: {e}")
            self.logger.error(f"Alert data: {alert}")
    
    def run(self):
        """Main processing loop for alert response system."""
        self.logger.info("Starting alert response processor...")
        
        try:
            while self.running:
                # Poll for messages with timeout
                msg = self.consumer.poll(timeout=1.0)
                
                if msg is None:
                    continue
                
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    else:
                        self.logger.error(f"Kafka error: {msg.error()}")
                        break
                
                try:
                    # Parse alert from message
                    alert = json.loads(msg.value().decode('utf-8'))
                    
                    # Process alert through response pipeline
                    self.process_alert(alert)
                    
                    # Manually commit offset after successful processing
                    self.consumer.commit(msg)
                    
                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse alert JSON: {e}")
                    self.consumer.commit(msg)  # Skip bad message
                    
                except Exception as e:
                    self.logger.error(f"Error processing alert message: {e}")
                    # Don't commit - will retry message
                    
        except KafkaException as e:
            self.logger.error(f"Kafka exception: {e}")
            
        finally:
            self._cleanup()
    
    def _cleanup(self):
        """Cleanup resources during shutdown."""
        self.logger.info("Shutting down alert response processor...")
        
        # Final statistics
        elapsed = time.time() - self.start_time
        aps = self.processed_alerts / elapsed if elapsed > 0 else 0
        
        self.logger.info(
            f"Final statistics - Processed: {self.processed_alerts}, "
            f"Blocked users: {self.blocked_users}, "
            f"Notifications: {self.notifications_sent}, "
            f"Average APS: {aps:.1f}"
        )
        
        # Flush remaining messages
        if self.producer:
            self.producer.flush(timeout=10)
            
        # Close Kafka connections
        if self.consumer:
            self.consumer.close()
            
        # Close Redis connection
        if self.redis_client:
            self.redis_client.close()
        
        self.logger.info("Alert response processor shutdown complete")


def main():
    """Main entry point for alert response processor."""
    try:
        processor = AlertProcessor(
            consumer_group="alert-response-group",
            notification_email="fraud-team@company.com"
        )
        
        processor.run()
        
    except KeyboardInterrupt:
        print("\nShutdown requested by user")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()