"""
Database abstraction layer for hybrid PostgreSQL/ClickHouse persistence architecture.

Provides high-level interfaces for OLTP operations (PostgreSQL) and OLAP analytics (ClickHouse)
with connection pooling, error handling, and performance monitoring.
"""

import logging
import time
from typing import Dict, List, Any, Optional, AsyncContextManager
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime, timezone
from dataclasses import asdict

import psycopg2
from psycopg2 import pool, sql
from psycopg2.extras import RealDictCursor, Json
from clickhouse_driver import Client as ClickHouseClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from .config import PostgreSQLConfig, ClickHouseConfig, get_database_configs
from .schemas import FraudAlert, TransactionRecord, AlertSeverity, AlertStatus, UserStatus


class DatabaseError(Exception):
    """Custom exception for database operations."""
    pass


class PostgreSQLManager:
    """Manages PostgreSQL connections and OLTP operations."""
    
    def __init__(self, config: PostgreSQLConfig):
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.PostgreSQLManager")
        
        # Connection pool for raw psycopg2 operations
        self._connection_pool = None
        
        # SQLAlchemy engine for ORM operations
        self._engine = None
        self._session_factory = None
        
        # Performance monitoring
        self._query_count = 0
        self._total_query_time = 0.0
        
        self._initialize_connections()
    
    def _initialize_connections(self):
        """Initialize database connections and pools."""
        try:
            # Create psycopg2 connection pool
            self._connection_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=self.config.min_connections,
                maxconn=self.config.max_connections,
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.username,
                password=self.config.password,
                cursor_factory=RealDictCursor
            )
            
            # Create SQLAlchemy engine
            self._engine = create_engine(
                self.config.get_connection_string(),
                **self.config.get_sqlalchemy_config(),
                poolclass=QueuePool,
                echo=False
            )
            
            self._session_factory = sessionmaker(bind=self._engine)
            
            self.logger.info("PostgreSQL connections initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize PostgreSQL connections: {e}")
            raise DatabaseError(f"PostgreSQL initialization failed: {e}")
    
    @contextmanager
    def get_connection(self):
        """Get a connection from the pool with automatic cleanup."""
        connection = None
        try:
            connection = self._connection_pool.getconn()
            yield connection
        except Exception as e:
            if connection:
                connection.rollback()
            raise DatabaseError(f"PostgreSQL operation failed: {e}")
        finally:
            if connection:
                self._connection_pool.putconn(connection)
    
    @contextmanager
    def get_session(self):
        """Get a SQLAlchemy session with automatic cleanup."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise DatabaseError(f"PostgreSQL session operation failed: {e}")
        finally:
            session.close()
    
    def insert_fraud_alert(self, alert: FraudAlert) -> str:
        """Insert a fraud alert and return the generated alert ID."""
        start_time = time.time()
        
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                insert_query = sql.SQL("""
                    INSERT INTO fraud_alerts (
                        transaction_id, user_id, severity, fraud_score, 
                        ml_prediction, business_rules_triggered, explanation
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING alert_id
                """)
                
                cursor.execute(insert_query, (
                    alert.transaction_id,
                    alert.user_id,
                    alert.severity.value,
                    alert.fraud_score,
                    alert.ml_prediction,
                    alert.business_rules_triggered,
                    Json(alert.explanation)
                ))
                
                alert_id = cursor.fetchone()['alert_id']
                conn.commit()
                
                self._update_metrics(time.time() - start_time)
                return str(alert_id)
    
    def update_alert_status(self, alert_id: str, status: AlertStatus, 
                           investigator_id: Optional[str] = None,
                           resolution_notes: Optional[str] = None) -> bool:
        """Update the status of a fraud alert."""
        start_time = time.time()
        
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                update_fields = ['status = %s', 'updated_at = CURRENT_TIMESTAMP']
                values = [status.value]
                
                if status == AlertStatus.INVESTIGATING:
                    update_fields.append('investigated_at = CURRENT_TIMESTAMP')
                elif status in [AlertStatus.RESOLVED, AlertStatus.FALSE_POSITIVE]:
                    update_fields.append('resolved_at = CURRENT_TIMESTAMP')
                
                if investigator_id:
                    update_fields.append('investigator_id = %s')
                    values.append(investigator_id)
                
                if resolution_notes:
                    update_fields.append('resolution_notes = %s')
                    values.append(resolution_notes)
                
                values.append(alert_id)
                
                update_query = sql.SQL(
                    f"UPDATE fraud_alerts SET {', '.join(update_fields)} WHERE alert_id = %s"
                )
                
                cursor.execute(update_query, values)
                rows_affected = cursor.rowcount
                conn.commit()
                
                self._update_metrics(time.time() - start_time)
                return rows_affected > 0
    
    def upsert_user_account(self, user_id: str, status: UserStatus = UserStatus.ACTIVE,
                           increment_alerts: bool = False, 
                           increment_high_severity: bool = False) -> bool:
        """Insert or update user account information."""
        start_time = time.time()
        
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                upsert_query = sql.SQL("""
                    INSERT INTO user_accounts (user_id, status, total_alerts, high_severity_alerts, last_alert_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        total_alerts = CASE WHEN %s THEN user_accounts.total_alerts + 1 ELSE EXCLUDED.total_alerts END,
                        high_severity_alerts = CASE WHEN %s THEN user_accounts.high_severity_alerts + 1 ELSE EXCLUDED.high_severity_alerts END,
                        last_alert_at = CASE WHEN %s OR %s THEN CURRENT_TIMESTAMP ELSE user_accounts.last_alert_at END,
                        updated_at = CURRENT_TIMESTAMP
                """)
                
                current_time = datetime.now(timezone.utc)
                
                cursor.execute(upsert_query, (
                    user_id, status.value, 1 if increment_alerts else 0, 
                    1 if increment_high_severity else 0, current_time,
                    increment_alerts, increment_high_severity, 
                    increment_alerts, increment_high_severity
                ))
                
                rows_affected = cursor.rowcount
                conn.commit()
                
                self._update_metrics(time.time() - start_time)
                return rows_affected > 0
    
    def log_audit_event(self, event_type: str, entity_type: str, entity_id: str,
                       action: str, actor_id: Optional[str] = None,
                       details: Optional[Dict[str, Any]] = None) -> bool:
        """Log an audit event for compliance tracking."""
        start_time = time.time()
        
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                insert_query = sql.SQL("""
                    INSERT INTO system_audit_log (
                        event_type, entity_type, entity_id, action, actor_id, details
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                """)
                
                cursor.execute(insert_query, (
                    event_type, entity_type, entity_id, action, actor_id, 
                    Json(details) if details else None
                ))
                
                rows_affected = cursor.rowcount
                conn.commit()
                
                self._update_metrics(time.time() - start_time)
                return rows_affected > 0
    
    def get_user_alert_summary(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get alert summary for a user."""
        start_time = time.time()
        
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                query = sql.SQL("""
                    SELECT ua.*, 
                           COUNT(fa.alert_id) as pending_alerts,
                           MAX(fa.fraud_score) as max_fraud_score
                    FROM user_accounts ua
                    LEFT JOIN fraud_alerts fa ON ua.user_id = fa.user_id AND fa.status = 'PENDING'
                    WHERE ua.user_id = %s
                    GROUP BY ua.user_id, ua.status, ua.total_alerts, ua.high_severity_alerts,
                             ua.last_alert_at, ua.blocked_at, ua.blocked_reason, 
                             ua.created_at, ua.updated_at
                """)
                
                cursor.execute(query, (user_id,))
                result = cursor.fetchone()
                
                self._update_metrics(time.time() - start_time)
                return dict(result) if result else None
    
    def health_check(self) -> bool:
        """Check database connectivity and basic functionality."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    return cursor.fetchone()[0] == 1
        except Exception as e:
            self.logger.error(f"PostgreSQL health check failed: {e}")
            return False
    
    def get_connection_pool_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics for monitoring."""
        if self._connection_pool:
            return {
                'total_connections': self._connection_pool.minconn + self._connection_pool.maxconn,
                'available_connections': len(self._connection_pool._pool),
                'used_connections': self._connection_pool.maxconn - len(self._connection_pool._pool)
            }
        return {}
    
    def _update_metrics(self, query_time: float):
        """Update query performance metrics."""
        self._query_count += 1
        self._total_query_time += query_time
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics for monitoring."""
        return {
            'query_count': self._query_count,
            'total_query_time': self._total_query_time,
            'average_query_time': self._total_query_time / max(self._query_count, 1),
            'connection_pool_stats': self.get_connection_pool_stats()
        }
    
    def close(self):
        """Close all database connections."""
        if self._connection_pool:
            self._connection_pool.closeall()
        if self._engine:
            self._engine.dispose()


class ClickHouseManager:
    """Manages ClickHouse connections and OLAP operations."""
    
    def __init__(self, config: ClickHouseConfig):
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.ClickHouseManager")
        
        self._client = None
        
        # Performance monitoring
        self._query_count = 0
        self._total_query_time = 0.0
        self._total_rows_inserted = 0
        
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize ClickHouse client."""
        try:
            self._client = ClickHouseClient(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.username,
                password=self.config.password,
                secure=self.config.secure,
                settings=self.config.get_client_settings()
            )
            
            # Test connection
            self._client.execute('SELECT 1')
            
            self.logger.info("ClickHouse client initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize ClickHouse client: {e}")
            raise DatabaseError(f"ClickHouse initialization failed: {e}")
    
    def insert_transaction_record(self, transaction: TransactionRecord) -> bool:
        """Insert a single transaction record."""
        return self.batch_insert_transactions([transaction])
    
    def batch_insert_transactions(self, transactions: List[TransactionRecord]) -> bool:
        """Batch insert transaction records for optimal performance."""
        if not transactions:
            return True
            
        start_time = time.time()
        
        try:
            data = [trans.to_clickhouse_dict() for trans in transactions]
            
            # Add Kafka metadata
            for record in data:
                record['kafka_timestamp'] = datetime.now(timezone.utc)
                record['kafka_partition'] = 0  # Will be set by actual Kafka consumer
                record['kafka_offset'] = 0     # Will be set by actual Kafka consumer
            
            self._client.execute(
                'INSERT INTO transaction_records VALUES',
                data,
                types_check=True
            )
            
            self._update_metrics(time.time() - start_time, len(transactions))
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to insert transaction records: {e}")
            return False
    
    def insert_fraud_features(self, transaction_id: str, user_id: str, 
                            features: Dict[str, float], 
                            feature_category: str = "realtime") -> bool:
        """Insert fraud detection features for a transaction."""
        start_time = time.time()
        
        try:
            current_time = datetime.now(timezone.utc)
            data = []
            
            for feature_name, feature_value in features.items():
                data.append({
                    'transaction_id': transaction_id,
                    'user_id': user_id,
                    'timestamp': current_time,
                    'feature_name': feature_name,
                    'feature_value': float(feature_value),
                    'feature_category': feature_category,
                    'computation_time_ms': 0,  # Will be set by calling code
                    'kafka_timestamp': current_time
                })
            
            self._client.execute(
                'INSERT INTO fraud_features VALUES',
                data,
                types_check=True
            )
            
            self._update_metrics(time.time() - start_time, len(data))
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to insert fraud features: {e}")
            return False
    
    def insert_detection_result(self, transaction_id: str, user_id: str,
                              ml_model_version: str, ml_prediction: float,
                              ml_confidence: float, business_rules_score: float,
                              final_fraud_score: float, features_used: List[str],
                              model_features: Dict[str, float],
                              business_rules_triggered: List[str]) -> bool:
        """Insert fraud detection result."""
        start_time = time.time()
        
        try:
            data = [{
                'transaction_id': transaction_id,
                'user_id': user_id,
                'timestamp': datetime.now(timezone.utc),
                'ml_model_version': ml_model_version,
                'ml_prediction': ml_prediction,
                'ml_confidence': ml_confidence,
                'business_rules_score': business_rules_score,
                'final_fraud_score': final_fraud_score,
                'prediction_time_ms': 0,  # Will be set by calling code
                'features_used': features_used,
                'model_features': model_features,
                'business_rules_triggered': business_rules_triggered,
                'kafka_timestamp': datetime.now(timezone.utc)
            }]
            
            self._client.execute(
                'INSERT INTO detection_results VALUES',
                data,
                types_check=True
            )
            
            self._update_metrics(time.time() - start_time, 1)
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to insert detection result: {e}")
            return False
    
    def insert_performance_metrics(self, metrics: List[Dict[str, Any]]) -> bool:
        """Batch insert performance metrics."""
        if not metrics:
            return True
            
        start_time = time.time()
        
        try:
            current_time = datetime.now(timezone.utc)
            
            # Ensure all metrics have required fields
            for metric in metrics:
                metric.setdefault('kafka_timestamp', current_time)
                metric.setdefault('additional_labels', {})
            
            self._client.execute(
                'INSERT INTO performance_metrics VALUES',
                metrics,
                types_check=True
            )
            
            self._update_metrics(time.time() - start_time, len(metrics))
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to insert performance metrics: {e}")
            return False
    
    def get_fraud_rate_by_hour(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """Get fraud rate aggregated by hour."""
        start_time = time.time()
        
        try:
            query = """
            SELECT 
                toStartOfHour(timestamp) as hour,
                count() as total_transactions,
                sum(is_fraud) as fraud_transactions,
                avg(fraud_score) as avg_fraud_score
            FROM transaction_records 
            WHERE timestamp >= %s AND timestamp < %s
            GROUP BY hour 
            ORDER BY hour
            """
            
            result = self._client.execute(query, [start_date, end_date])
            
            self._update_metrics(time.time() - start_time, 0)
            
            return [
                {
                    'hour': row[0],
                    'total_transactions': row[1],
                    'fraud_transactions': row[2],
                    'fraud_rate': row[2] / max(row[1], 1),
                    'avg_fraud_score': row[3]
                }
                for row in result
            ]
            
        except Exception as e:
            self.logger.error(f"Failed to get fraud rate by hour: {e}")
            return []
    
    def health_check(self) -> bool:
        """Check ClickHouse connectivity and basic functionality."""
        try:
            result = self._client.execute('SELECT 1')
            return result[0][0] == 1
        except Exception as e:
            self.logger.error(f"ClickHouse health check failed: {e}")
            return False
    
    def _update_metrics(self, query_time: float, rows_affected: int):
        """Update query performance metrics."""
        self._query_count += 1
        self._total_query_time += query_time
        self._total_rows_inserted += rows_affected
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics for monitoring."""
        return {
            'query_count': self._query_count,
            'total_query_time': self._total_query_time,
            'average_query_time': self._total_query_time / max(self._query_count, 1),
            'total_rows_inserted': self._total_rows_inserted
        }
    
    def close(self):
        """Close ClickHouse client."""
        if self._client:
            self._client.disconnect()


class PersistenceLayer:
    """High-level interface for hybrid database operations."""
    
    def __init__(self, postgres_config: Optional[PostgreSQLConfig] = None,
                 clickhouse_config: Optional[ClickHouseConfig] = None):
        if not postgres_config or not clickhouse_config:
            postgres_config, clickhouse_config = get_database_configs()
            
        self.postgres = PostgreSQLManager(postgres_config)
        self.clickhouse = ClickHouseManager(clickhouse_config)
        self.logger = logging.getLogger(f"{__name__}.PersistenceLayer")
    
    def persist_fraud_detection_result(self, 
                                     transaction: TransactionRecord,
                                     alert: FraudAlert,
                                     features: Dict[str, float],
                                     detection_metadata: Dict[str, Any]) -> bool:
        """Persist complete fraud detection result to both databases."""
        try:
            # Insert into ClickHouse for analytics
            self.clickhouse.insert_transaction_record(transaction)
            self.clickhouse.insert_fraud_features(
                transaction.transaction_id, 
                transaction.user_id, 
                features
            )
            self.clickhouse.insert_detection_result(
                transaction.transaction_id,
                transaction.user_id,
                detection_metadata.get('ml_model_version', 'unknown'),
                detection_metadata.get('ml_prediction', 0.0),
                detection_metadata.get('ml_confidence', 0.0),
                detection_metadata.get('business_rules_score', 0.0),
                alert.fraud_score,
                detection_metadata.get('features_used', []),
                detection_metadata.get('model_features', {}),
                alert.business_rules_triggered
            )
            
            # Insert into PostgreSQL for transactional integrity
            if alert.severity in [AlertSeverity.MEDIUM, AlertSeverity.HIGH, AlertSeverity.CRITICAL]:
                alert_id = self.postgres.insert_fraud_alert(alert)
                self.postgres.upsert_user_account(
                    alert.user_id,
                    increment_alerts=True,
                    increment_high_severity=(alert.severity in [AlertSeverity.HIGH, AlertSeverity.CRITICAL])
                )
                
                # Log audit event
                self.postgres.log_audit_event(
                    'fraud_detection', 'transaction', transaction.transaction_id,
                    'alert_generated', details={
                        'alert_id': alert_id,
                        'fraud_score': alert.fraud_score,
                        'severity': alert.severity.value
                    }
                )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to persist fraud detection result: {e}")
            return False
    
    def health_check(self) -> Dict[str, bool]:
        """Check health of both database systems."""
        return {
            'postgresql': self.postgres.health_check(),
            'clickhouse': self.clickhouse.health_check()
        }
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics from both database systems."""
        return {
            'postgresql': self.postgres.get_performance_metrics(),
            'clickhouse': self.clickhouse.get_performance_metrics()
        }
    
    def close(self):
        """Close all database connections."""
        self.postgres.close()
        self.clickhouse.close()


# Global persistence layer instance
_persistence_layer = None


def get_persistence_layer() -> PersistenceLayer:
    """Get global persistence layer instance."""
    global _persistence_layer
    if _persistence_layer is None:
        _persistence_layer = PersistenceLayer()
    return _persistence_layer


def close_persistence_layer():
    """Close global persistence layer instance."""
    global _persistence_layer
    if _persistence_layer:
        _persistence_layer.close()
        _persistence_layer = None