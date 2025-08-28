"""
Database configuration management for hybrid PostgreSQL/ClickHouse persistence.

Provides connection pooling, environment-specific settings, and health monitoring
for both OLTP (PostgreSQL) and OLAP (ClickHouse) workloads.
"""

import os
import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional
from urllib.parse import quote_plus


@dataclass
class DatabaseConfig:
    """Base database configuration."""
    host: str
    port: int
    database: str
    username: str
    password: str
    
    def get_connection_string(self) -> str:
        """Generate connection string for the database."""
        raise NotImplementedError


@dataclass 
class PostgreSQLConfig(DatabaseConfig):
    """PostgreSQL-specific configuration with connection pooling settings."""
    
    # Connection pool settings
    min_connections: int = 5
    max_connections: int = 20
    connection_timeout: int = 30
    idle_timeout: int = 300
    
    # Performance tuning
    statement_timeout: int = 30000  # 30 seconds
    lock_timeout: int = 10000       # 10 seconds
    
    def get_connection_string(self) -> str:
        """Generate PostgreSQL connection string."""
        password_encoded = quote_plus(self.password)
        return (
            f"postgresql://{self.username}:{password_encoded}@"
            f"{self.host}:{self.port}/{self.database}"
            f"?connect_timeout={self.connection_timeout}"
            f"&application_name=stream-sentinel"
        )
    
    def get_sqlalchemy_config(self) -> Dict[str, Any]:
        """Get SQLAlchemy engine configuration."""
        return {
            'pool_size': self.min_connections,
            'max_overflow': self.max_connections - self.min_connections,
            'pool_timeout': self.connection_timeout,
            'pool_recycle': self.idle_timeout,
            'pool_pre_ping': True,
            'connect_args': {
                'options': f'-c statement_timeout={self.statement_timeout}'
                          f' -c lock_timeout={self.lock_timeout}'
            }
        }


@dataclass
class ClickHouseConfig(DatabaseConfig):
    """ClickHouse-specific configuration optimized for analytics workloads."""
    
    # Connection settings
    secure: bool = False
    compression: str = 'lz4'
    send_receive_timeout: int = 300
    
    # Batch processing settings
    insert_block_size: int = 1000000
    max_block_size: int = 65536
    max_insert_block_size: int = 1048576
    
    # Query optimization
    max_execution_time: int = 300
    max_memory_usage: int = 10000000000  # 10GB
    
    def get_connection_string(self) -> str:
        """Generate ClickHouse connection string."""
        protocol = "clickhouses" if self.secure else "clickhouse"
        return (
            f"{protocol}://{self.username}:{self.password}@"
            f"{self.host}:{self.port}/{self.database}"
        )
    
    def get_client_settings(self) -> Dict[str, Any]:
        """Get ClickHouse client settings for optimal performance."""
        return {
            'send_receive_timeout': self.send_receive_timeout,
            'insert_block_size': self.insert_block_size,
            'max_block_size': self.max_block_size,
            'max_insert_block_size': self.max_insert_block_size,
            'max_execution_time': self.max_execution_time,
            'max_memory_usage': self.max_memory_usage,
            'use_numpy': True,
            'compression': self.compression
        }


class PersistenceConfigManager:
    """Manages database configurations for different environments."""
    
    def __init__(self, environment: Optional[str] = None):
        self.environment = environment or os.getenv('ENVIRONMENT', 'development')
        self.logger = logging.getLogger(__name__)
        
    def get_postgresql_config(self) -> PostgreSQLConfig:
        """Get PostgreSQL configuration for current environment."""
        return PostgreSQLConfig(
            host=os.getenv('POSTGRES_HOST', 'localhost'),
            port=int(os.getenv('POSTGRES_PORT', '5432')),
            database=os.getenv('POSTGRES_DB', 'stream_sentinel'),
            username=os.getenv('POSTGRES_USER', 'stream_sentinel_user'),
            password=os.getenv('POSTGRES_PASSWORD', 'stream_sentinel_password'),
            min_connections=int(os.getenv('POSTGRES_MIN_CONNECTIONS', '5')),
            max_connections=int(os.getenv('POSTGRES_MAX_CONNECTIONS', '20')),
            connection_timeout=int(os.getenv('POSTGRES_CONNECTION_TIMEOUT', '30')),
            idle_timeout=int(os.getenv('POSTGRES_IDLE_TIMEOUT', '300'))
        )
    
    def get_clickhouse_config(self) -> ClickHouseConfig:
        """Get ClickHouse configuration for current environment."""
        return ClickHouseConfig(
            host=os.getenv('CLICKHOUSE_HOST', 'localhost'),
            port=int(os.getenv('CLICKHOUSE_PORT', '9000')),
            database=os.getenv('CLICKHOUSE_DB', 'stream_sentinel'),
            username=os.getenv('CLICKHOUSE_USER', 'stream_sentinel_user'),
            password=os.getenv('CLICKHOUSE_PASSWORD', 'stream_sentinel_password'),
            secure=os.getenv('CLICKHOUSE_SECURE', 'false').lower() == 'true',
            compression=os.getenv('CLICKHOUSE_COMPRESSION', 'lz4'),
            send_receive_timeout=int(os.getenv('CLICKHOUSE_TIMEOUT', '300')),
            insert_block_size=int(os.getenv('CLICKHOUSE_INSERT_BLOCK_SIZE', '1000000')),
            max_execution_time=int(os.getenv('CLICKHOUSE_MAX_EXECUTION_TIME', '300'))
        )
    
    def get_kafka_persistence_config(self) -> Dict[str, Any]:
        """Get Kafka configuration for persistence consumers."""
        return {
            'bootstrap_servers': os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092'),
            'group_id_prefix': 'stream-sentinel-persistence',
            'auto_offset_reset': 'earliest',
            'enable_auto_commit': True,
            'auto_commit_interval_ms': 1000,
            'max_poll_records': int(os.getenv('KAFKA_MAX_POLL_RECORDS', '500')),
            'fetch_min_bytes': int(os.getenv('KAFKA_FETCH_MIN_BYTES', '1024')),
            'fetch_max_wait_ms': int(os.getenv('KAFKA_FETCH_MAX_WAIT_MS', '500')),
            'session_timeout_ms': 30000,
            'heartbeat_interval_ms': 3000
        }
    
    def validate_configuration(self) -> bool:
        """Validate that all required configuration is present."""
        required_postgres_vars = [
            'POSTGRES_HOST', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD'
        ]
        required_clickhouse_vars = [
            'CLICKHOUSE_HOST', 'CLICKHOUSE_DB', 'CLICKHOUSE_USER', 'CLICKHOUSE_PASSWORD'
        ]
        
        missing_vars = []
        
        for var in required_postgres_vars:
            if not os.getenv(var):
                missing_vars.append(var)
                
        for var in required_clickhouse_vars:
            if not os.getenv(var):
                missing_vars.append(var)
        
        if missing_vars:
            self.logger.error(f"Missing required environment variables: {missing_vars}")
            return False
            
        return True
    
    def get_monitoring_config(self) -> Dict[str, Any]:
        """Get configuration for database health monitoring."""
        return {
            'health_check_interval': int(os.getenv('DB_HEALTH_CHECK_INTERVAL', '30')),
            'connection_pool_monitoring': os.getenv('DB_POOL_MONITORING', 'true').lower() == 'true',
            'query_performance_tracking': os.getenv('DB_QUERY_TRACKING', 'true').lower() == 'true',
            'slow_query_threshold': int(os.getenv('DB_SLOW_QUERY_THRESHOLD', '1000')),  # ms
            'enable_metrics_export': os.getenv('DB_METRICS_EXPORT', 'true').lower() == 'true'
        }


# Global configuration instance
config_manager = PersistenceConfigManager()


def get_database_configs() -> tuple[PostgreSQLConfig, ClickHouseConfig]:
    """Convenience function to get both database configurations."""
    return (
        config_manager.get_postgresql_config(),
        config_manager.get_clickhouse_config()
    )


def validate_environment() -> bool:
    """Validate that the current environment has all required configuration."""
    return config_manager.validate_configuration()