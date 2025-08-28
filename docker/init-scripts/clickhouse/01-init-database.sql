-- ClickHouse initialization script for Stream-Sentinel fraud detection system
-- This script creates the database schema for high-volume analytics data

-- Create the main database
CREATE DATABASE IF NOT EXISTS stream_sentinel;
USE stream_sentinel;

-- Transaction records table - stores all processed transactions
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
    kafka_timestamp DateTime64(3) DEFAULT now64(),
    kafka_partition UInt16,
    kafka_offset UInt64
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (user_id, timestamp)
TTL timestamp + INTERVAL 2 YEAR
SETTINGS index_granularity = 8192;

-- Fraud features table - stores real-time engineered features
CREATE TABLE IF NOT EXISTS fraud_features (
    transaction_id String,
    user_id String,
    timestamp DateTime64(3),
    feature_name String,
    feature_value Float64,
    feature_category String,
    computation_time_ms UInt16,
    kafka_timestamp DateTime64(3) DEFAULT now64()
) ENGINE = MergeTree()
PARTITION BY (toYYYYMM(timestamp), feature_category)
ORDER BY (user_id, timestamp, feature_name)
TTL timestamp + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

-- Detection results table - stores ML model predictions and business rule outcomes
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
    kafka_timestamp DateTime64(3) DEFAULT now64()
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, user_id)
TTL timestamp + INTERVAL 2 YEAR
SETTINGS index_granularity = 8192;

-- Performance metrics table - stores system performance data
CREATE TABLE IF NOT EXISTS performance_metrics (
    timestamp DateTime64(3),
    metric_name String,
    metric_value Float64,
    component String,
    instance_id String,
    additional_labels Map(String, String),
    kafka_timestamp DateTime64(3) DEFAULT now64()
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (metric_name, timestamp)
TTL timestamp + INTERVAL 6 MONTH
SETTINGS index_granularity = 8192;

-- Create materialized views for common analytics queries

-- Hourly fraud rate aggregation
CREATE MATERIALIZED VIEW IF NOT EXISTS fraud_rate_hourly
ENGINE = AggregatingMergeTree()
ORDER BY (hour, user_id)
POPULATE AS
SELECT
    toStartOfHour(timestamp) as hour,
    user_id,
    countState() as transaction_count,
    sumState(is_fraud) as fraud_count,
    avgState(fraud_score) as avg_fraud_score
FROM transaction_records
GROUP BY hour, user_id;

-- Daily user activity summary
CREATE MATERIALIZED VIEW IF NOT EXISTS user_activity_daily
ENGINE = AggregatingMergeTree()
ORDER BY (date, user_id)
POPULATE AS
SELECT
    toDate(timestamp) as date,
    user_id,
    countState() as transaction_count,
    sumState(amount) as total_amount,
    uniqState(merchant_category) as unique_merchants,
    maxState(fraud_score) as max_fraud_score
FROM transaction_records
GROUP BY date, user_id;

-- Model performance tracking
CREATE MATERIALIZED VIEW IF NOT EXISTS model_accuracy_hourly
ENGINE = AggregatingMergeTree()
ORDER BY (hour, ml_model_version)
POPULATE AS
SELECT
    toStartOfHour(timestamp) as hour,
    ml_model_version,
    countState() as prediction_count,
    avgState(ml_prediction) as avg_prediction,
    avgState(ml_confidence) as avg_confidence,
    sumState(if(ml_prediction > 0.5 AND is_fraud = 1, 1, 0)) as true_positives,
    sumState(if(ml_prediction <= 0.5 AND is_fraud = 0, 1, 0)) as true_negatives,
    sumState(if(ml_prediction > 0.5 AND is_fraud = 0, 1, 0)) as false_positives,
    sumState(if(ml_prediction <= 0.5 AND is_fraud = 1, 1, 0)) as false_negatives
FROM detection_results dr
JOIN transaction_records tr ON dr.transaction_id = tr.transaction_id
GROUP BY hour, ml_model_version;

-- Create dictionary for efficient lookups of user status
CREATE DICTIONARY IF NOT EXISTS user_status_dict (
    user_id String,
    status String,
    blocked_at DateTime64(3),
    total_alerts UInt32
)
PRIMARY KEY user_id
SOURCE(POSTGRESQL(
    host 'postgres'
    port 5432
    user 'stream_sentinel_user'
    password 'stream_sentinel_password'
    db 'stream_sentinel'
    table 'user_accounts'
))
LAYOUT(HASHED())
LIFETIME(MIN 300 MAX 600);

-- Grant necessary permissions (ClickHouse uses a different permission model)
-- These would typically be handled through ClickHouse's built-in access control