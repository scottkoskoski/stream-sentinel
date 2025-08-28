"""
Database schema definitions for hybrid PostgreSQL/ClickHouse persistence architecture.

PostgreSQL: Critical transactional data requiring ACID compliance
ClickHouse: High-volume analytics data optimized for time-series queries
"""

from enum import Enum
from typing import Dict, Any
from dataclasses import dataclass
from datetime import datetime


class AlertSeverity(Enum):
    MINIMAL = "MINIMAL"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertStatus(Enum):
    PENDING = "PENDING"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class UserStatus(Enum):
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    SUSPENDED = "SUSPENDED"


@dataclass
class PostgreSQLSchemas:
    """PostgreSQL table schemas for critical transactional data."""
    
    FRAUD_ALERTS = """
    CREATE TABLE IF NOT EXISTS fraud_alerts (
        alert_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        transaction_id VARCHAR(255) NOT NULL,
        user_id VARCHAR(255) NOT NULL,
        severity VARCHAR(20) NOT NULL CHECK (severity IN ('MINIMAL', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
        status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'INVESTIGATING', 'RESOLVED', 'FALSE_POSITIVE')),
        fraud_score DECIMAL(5,4) NOT NULL CHECK (fraud_score >= 0 AND fraud_score <= 1),
        ml_prediction DECIMAL(5,4) NOT NULL CHECK (ml_prediction >= 0 AND ml_prediction <= 1),
        business_rules_triggered TEXT[],
        explanation JSONB,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        investigated_at TIMESTAMP WITH TIME ZONE,
        resolved_at TIMESTAMP WITH TIME ZONE,
        investigator_id VARCHAR(255),
        resolution_notes TEXT,
        
        -- Indexes for common queries
        INDEX idx_fraud_alerts_user_id (user_id),
        INDEX idx_fraud_alerts_status (status),
        INDEX idx_fraud_alerts_severity (severity),
        INDEX idx_fraud_alerts_created_at (created_at),
        INDEX idx_fraud_alerts_transaction_id (transaction_id)
    );
    """
    
    USER_ACCOUNTS = """
    CREATE TABLE IF NOT EXISTS user_accounts (
        user_id VARCHAR(255) PRIMARY KEY,
        status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'BLOCKED', 'SUSPENDED')),
        total_alerts INTEGER DEFAULT 0,
        high_severity_alerts INTEGER DEFAULT 0,
        last_alert_at TIMESTAMP WITH TIME ZONE,
        blocked_at TIMESTAMP WITH TIME ZONE,
        blocked_reason TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        
        -- Indexes for user management queries
        INDEX idx_user_accounts_status (status),
        INDEX idx_user_accounts_last_alert (last_alert_at),
        INDEX idx_user_accounts_blocked_at (blocked_at)
    );
    """
    
    MODEL_PERFORMANCE = """
    CREATE TABLE IF NOT EXISTS model_performance (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        model_version VARCHAR(100) NOT NULL,
        evaluation_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        metric_name VARCHAR(100) NOT NULL,
        metric_value DECIMAL(10,6) NOT NULL,
        data_period_start TIMESTAMP WITH TIME ZONE NOT NULL,
        data_period_end TIMESTAMP WITH TIME ZONE NOT NULL,
        sample_size INTEGER NOT NULL,
        metadata JSONB,
        
        -- Composite index for time-series queries
        INDEX idx_model_performance_version_date (model_version, evaluation_date),
        INDEX idx_model_performance_metric (metric_name, evaluation_date)
    );
    """
    
    SYSTEM_AUDIT_LOG = """
    CREATE TABLE IF NOT EXISTS system_audit_log (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        event_type VARCHAR(100) NOT NULL,
        entity_type VARCHAR(100) NOT NULL,
        entity_id VARCHAR(255) NOT NULL,
        action VARCHAR(100) NOT NULL,
        actor_id VARCHAR(255),
        timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        details JSONB,
        ip_address INET,
        user_agent TEXT,
        
        -- Indexes for audit queries
        INDEX idx_audit_log_timestamp (timestamp),
        INDEX idx_audit_log_entity (entity_type, entity_id),
        INDEX idx_audit_log_event_type (event_type),
        INDEX idx_audit_log_actor (actor_id)
    );
    """
    
    # Trigger for updating timestamps
    UPDATE_TIMESTAMP_TRIGGER = """
    CREATE OR REPLACE FUNCTION update_updated_at_column()
    RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = CURRENT_TIMESTAMP;
        RETURN NEW;
    END;
    $$ language 'plpgsql';
    """
    
    FRAUD_ALERTS_TRIGGER = """
    CREATE OR REPLACE TRIGGER update_fraud_alerts_updated_at
        BEFORE UPDATE ON fraud_alerts
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """
    
    USER_ACCOUNTS_TRIGGER = """
    CREATE OR REPLACE TRIGGER update_user_accounts_updated_at
        BEFORE UPDATE ON user_accounts
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """


@dataclass
class ClickHouseSchemas:
    """ClickHouse table schemas for high-volume analytics data."""
    
    TRANSACTION_RECORDS = """
    CREATE TABLE IF NOT EXISTS transaction_records (
        transaction_id String,
        user_id String,
        timestamp DateTime64(3),
        amount Decimal64(4),
        merchant_category String,
        payment_method String,
        device_info String,
        location_country String,
        location_state String,
        is_fraud UInt8,
        fraud_score Float64,
        processing_time_ms UInt32,
        kafka_timestamp DateTime64(3),
        kafka_partition UInt16,
        kafka_offset UInt64
    ) ENGINE = MergeTree()
    PARTITION BY toYYYYMM(timestamp)
    ORDER BY (user_id, timestamp)
    TTL timestamp + INTERVAL 2 YEAR
    SETTINGS index_granularity = 8192;
    """
    
    FRAUD_FEATURES = """
    CREATE TABLE IF NOT EXISTS fraud_features (
        transaction_id String,
        user_id String,
        timestamp DateTime64(3),
        feature_name String,
        feature_value Float64,
        feature_category String,
        computation_time_ms UInt16,
        kafka_timestamp DateTime64(3)
    ) ENGINE = MergeTree()
    PARTITION BY (toYYYYMM(timestamp), feature_category)
    ORDER BY (user_id, timestamp, feature_name)
    TTL timestamp + INTERVAL 1 YEAR
    SETTINGS index_granularity = 8192;
    """
    
    DETECTION_RESULTS = """
    CREATE TABLE IF NOT EXISTS detection_results (
        transaction_id String,
        user_id String,
        timestamp DateTime64(3),
        ml_model_version String,
        ml_prediction Float64,
        ml_confidence Float64,
        business_rules_score Float64,
        final_fraud_score Float64,
        prediction_time_ms UInt16,
        features_used Array(String),
        model_features Map(String, Float64),
        business_rules_triggered Array(String),
        kafka_timestamp DateTime64(3)
    ) ENGINE = MergeTree()
    PARTITION BY toYYYYMM(timestamp)
    ORDER BY (timestamp, user_id)
    TTL timestamp + INTERVAL 2 YEAR
    SETTINGS index_granularity = 8192;
    """
    
    PERFORMANCE_METRICS = """
    CREATE TABLE IF NOT EXISTS performance_metrics (
        timestamp DateTime64(3),
        metric_name String,
        metric_value Float64,
        component String,
        instance_id String,
        additional_labels Map(String, String),
        kafka_timestamp DateTime64(3)
    ) ENGINE = MergeTree()
    PARTITION BY toYYYYMM(timestamp)
    ORDER BY (metric_name, timestamp)
    TTL timestamp + INTERVAL 6 MONTH
    SETTINGS index_granularity = 8192;
    """


# Schema validation and migration utilities
class SchemaManager:
    """Manages database schema creation and migrations."""
    
    @staticmethod
    def get_postgresql_schemas() -> Dict[str, str]:
        """Get all PostgreSQL table creation statements."""
        return {
            'fraud_alerts': PostgreSQLSchemas.FRAUD_ALERTS,
            'user_accounts': PostgreSQLSchemas.USER_ACCOUNTS,
            'model_performance': PostgreSQLSchemas.MODEL_PERFORMANCE,
            'system_audit_log': PostgreSQLSchemas.SYSTEM_AUDIT_LOG,
            'update_timestamp_function': PostgreSQLSchemas.UPDATE_TIMESTAMP_TRIGGER,
            'fraud_alerts_trigger': PostgreSQLSchemas.FRAUD_ALERTS_TRIGGER,
            'user_accounts_trigger': PostgreSQLSchemas.USER_ACCOUNTS_TRIGGER
        }
    
    @staticmethod
    def get_clickhouse_schemas() -> Dict[str, str]:
        """Get all ClickHouse table creation statements."""
        return {
            'transaction_records': ClickHouseSchemas.TRANSACTION_RECORDS,
            'fraud_features': ClickHouseSchemas.FRAUD_FEATURES,
            'detection_results': ClickHouseSchemas.DETECTION_RESULTS,
            'performance_metrics': ClickHouseSchemas.PERFORMANCE_METRICS
        }
    
    @staticmethod
    def validate_schema_compatibility() -> bool:
        """Validate that schemas are compatible with application logic."""
        # Add validation logic for schema consistency
        return True


# Data models for type safety
@dataclass
class FraudAlert:
    """Data model for fraud alert records."""
    transaction_id: str
    user_id: str
    severity: AlertSeverity
    fraud_score: float
    ml_prediction: float
    business_rules_triggered: list
    explanation: Dict[str, Any]
    status: AlertStatus = AlertStatus.PENDING
    alert_id: str = None
    created_at: datetime = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database insertion."""
        return {
            'transaction_id': self.transaction_id,
            'user_id': self.user_id,
            'severity': self.severity.value,
            'status': self.status.value,
            'fraud_score': self.fraud_score,
            'ml_prediction': self.ml_prediction,
            'business_rules_triggered': self.business_rules_triggered,
            'explanation': self.explanation
        }


@dataclass
class TransactionRecord:
    """Data model for transaction records."""
    transaction_id: str
    user_id: str
    timestamp: datetime
    amount: float
    merchant_category: str
    payment_method: str
    device_info: str
    location_country: str
    location_state: str
    is_fraud: bool
    fraud_score: float
    processing_time_ms: int
    
    def to_clickhouse_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for ClickHouse insertion."""
        return {
            'transaction_id': self.transaction_id,
            'user_id': self.user_id,
            'timestamp': self.timestamp,
            'amount': self.amount,
            'merchant_category': self.merchant_category,
            'payment_method': self.payment_method,
            'device_info': self.device_info,
            'location_country': self.location_country,
            'location_state': self.location_state,
            'is_fraud': 1 if self.is_fraud else 0,
            'fraud_score': self.fraud_score,
            'processing_time_ms': self.processing_time_ms
        }