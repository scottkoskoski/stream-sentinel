-- PostgreSQL initialization script for Stream-Sentinel fraud detection system
-- This script creates the database schema for critical transactional data

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Create the main database user if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'stream_sentinel_user') THEN
        CREATE ROLE stream_sentinel_user WITH LOGIN PASSWORD 'stream_sentinel_password';
    END IF;
END
$$;

-- Grant necessary permissions
GRANT ALL PRIVILEGES ON DATABASE stream_sentinel TO stream_sentinel_user;
GRANT ALL ON SCHEMA public TO stream_sentinel_user;

-- Create fraud alerts table for investigation tracking
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
    resolution_notes TEXT
);

-- Create indexes for fraud_alerts
CREATE INDEX IF NOT EXISTS idx_fraud_alerts_user_id ON fraud_alerts (user_id);
CREATE INDEX IF NOT EXISTS idx_fraud_alerts_status ON fraud_alerts (status);
CREATE INDEX IF NOT EXISTS idx_fraud_alerts_severity ON fraud_alerts (severity);
CREATE INDEX IF NOT EXISTS idx_fraud_alerts_created_at ON fraud_alerts (created_at);
CREATE INDEX IF NOT EXISTS idx_fraud_alerts_transaction_id ON fraud_alerts (transaction_id);

-- Create user accounts table for user management
CREATE TABLE IF NOT EXISTS user_accounts (
    user_id VARCHAR(255) PRIMARY KEY,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'BLOCKED', 'SUSPENDED')),
    total_alerts INTEGER DEFAULT 0,
    high_severity_alerts INTEGER DEFAULT 0,
    last_alert_at TIMESTAMP WITH TIME ZONE,
    blocked_at TIMESTAMP WITH TIME ZONE,
    blocked_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for user_accounts
CREATE INDEX IF NOT EXISTS idx_user_accounts_status ON user_accounts (status);
CREATE INDEX IF NOT EXISTS idx_user_accounts_last_alert ON user_accounts (last_alert_at);
CREATE INDEX IF NOT EXISTS idx_user_accounts_blocked_at ON user_accounts (blocked_at);

-- Create model performance tracking table
CREATE TABLE IF NOT EXISTS model_performance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_version VARCHAR(100) NOT NULL,
    evaluation_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    metric_name VARCHAR(100) NOT NULL,
    metric_value DECIMAL(10,6) NOT NULL,
    data_period_start TIMESTAMP WITH TIME ZONE NOT NULL,
    data_period_end TIMESTAMP WITH TIME ZONE NOT NULL,
    sample_size INTEGER NOT NULL,
    metadata JSONB
);

-- Create indexes for model_performance
CREATE INDEX IF NOT EXISTS idx_model_performance_version_date ON model_performance (model_version, evaluation_date);
CREATE INDEX IF NOT EXISTS idx_model_performance_metric ON model_performance (metric_name, evaluation_date);

-- Create system audit log for compliance
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
    user_agent TEXT
);

-- Create indexes for system_audit_log
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON system_audit_log (timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_log_entity ON system_audit_log (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON system_audit_log (event_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON system_audit_log (actor_id);

-- Create function for automatic timestamp updates
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create triggers for automatic timestamp updates
DROP TRIGGER IF EXISTS update_fraud_alerts_updated_at ON fraud_alerts;
CREATE TRIGGER update_fraud_alerts_updated_at
    BEFORE UPDATE ON fraud_alerts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_user_accounts_updated_at ON user_accounts;
CREATE TRIGGER update_user_accounts_updated_at
    BEFORE UPDATE ON user_accounts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Grant all necessary permissions to the application user
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO stream_sentinel_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO stream_sentinel_user;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO stream_sentinel_user;

-- Set default privileges for future objects
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO stream_sentinel_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO stream_sentinel_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO stream_sentinel_user;