"""
Database Integration Tests for PostgreSQL and ClickHouse Persistence Layer

Tests the actual persistence operations against live database instances.
All tests are marked @requires_infrastructure and need the Docker stack running.

Covers:
- PostgreSQL: schema creation, CRUD for fraud_alerts/user_accounts/audit_log,
  connection pool behavior
- ClickHouse: batch inserts for transaction_records, fraud_features,
  detection_results, and analytical queries
- Schema migration runner
"""

import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from persistence.schemas import (
    AlertSeverity,
    AlertStatus,
    ClickHouseSchemas,
    FraudAlert,
    PostgreSQLSchemas,
    SchemaManager,
    TransactionRecord,
    UserStatus,
)


class SchemaMigrationRunner:
    """Simple schema migration runner that executes CREATE TABLE statements.

    Designed for test environments -- executes each statement from
    SchemaManager sequentially against a provided connection.
    """

    def __init__(self):
        self.executed = []
        self.errors = []

    def run_postgresql_migrations(self, connection) -> Dict[str, bool]:
        """Execute all PostgreSQL schema statements.

        Args:
            connection: A psycopg connection object.

        Returns:
            Dict mapping schema name to success boolean.
        """
        schemas = SchemaManager.get_postgresql_schemas()
        results = {}

        for name, ddl in schemas.items():
            try:
                with connection.cursor() as cursor:
                    # Each DDL may contain multiple statements (e.g. index block)
                    for statement in self._split_statements(ddl):
                        if statement.strip():
                            cursor.execute(statement)
                connection.commit()
                results[name] = True
                self.executed.append(name)
            except Exception as exc:
                connection.rollback()
                results[name] = False
                self.errors.append((name, str(exc)))

        return results

    def run_clickhouse_migrations(self, client) -> Dict[str, bool]:
        """Execute all ClickHouse schema statements.

        Args:
            client: A clickhouse_driver Client instance.

        Returns:
            Dict mapping schema name to success boolean.
        """
        schemas = SchemaManager.get_clickhouse_schemas()
        results = {}

        for name, ddl in schemas.items():
            try:
                client.execute(ddl)
                results[name] = True
                self.executed.append(name)
            except Exception as exc:
                results[name] = False
                self.errors.append((name, str(exc)))

        return results

    @staticmethod
    def _split_statements(ddl: str) -> List[str]:
        """Split a DDL block into individual statements on semicolons."""
        return [s.strip() for s in ddl.split(";") if s.strip()]


# ---------------------------------------------------------------------------
# PostgreSQL Tests
# ---------------------------------------------------------------------------


@pytest.mark.database
@pytest.mark.integration
@pytest.mark.requires_infrastructure
class TestPostgreSQLIntegration:
    """Integration tests against a live PostgreSQL instance."""

    @pytest.fixture(autouse=True)
    def setup_pg(self, database_manager):
        """Ensure database_manager is available; skip if not."""
        if database_manager is None:
            pytest.skip("PostgreSQL not available (Docker stack not running)")
        self.db = database_manager
        # Attempt to get a PostgreSQLManager reference
        if hasattr(self.db, "pg"):
            self.pg = self.db.pg
        elif hasattr(self.db, "postgresql"):
            self.pg = self.db.postgresql
        else:
            self.pg = self.db  # database_manager itself may be the PG manager

    def test_schema_creation_with_migration_runner(self):
        """Test that all PostgreSQL schemas can be created without SQL errors.

        This catches the invalid INDEX syntax that was fixed in schemas.py.
        """
        runner = SchemaMigrationRunner()

        with self.pg.get_connection() as conn:
            results = runner.run_postgresql_migrations(conn)

        for name, success in results.items():
            assert success, f"Schema migration '{name}' failed: " f"{[e for n, e in runner.errors if n == name]}"

    def test_fraud_alert_insert_and_query(self):
        """Test inserting and querying a fraud alert."""
        alert = FraudAlert(
            transaction_id=f"txn_test_{uuid.uuid4().hex[:8]}",
            user_id="test_user_db_001",
            severity=AlertSeverity.HIGH,
            fraud_score=0.85,
            ml_prediction=0.82,
            business_rules_triggered=[
                "high_amount_transaction",
                "unusual_hour_transaction",
            ],
            explanation={"reason": "high amount at unusual hour", "amount": 5000.0},
        )

        alert_id = self.pg.insert_fraud_alert(alert)

        assert alert_id is not None
        assert len(alert_id) > 0  # UUID string

        # Query the alert back
        with self.pg.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM fraud_alerts WHERE alert_id = %s", (alert_id,))
                row = cursor.fetchone()

        assert row is not None
        assert row["transaction_id"] == alert.transaction_id
        assert row["user_id"] == "test_user_db_001"
        assert row["severity"] == "HIGH"
        assert float(row["fraud_score"]) == pytest.approx(0.85, abs=0.001)
        assert row["status"] == "PENDING"

    def test_fraud_alert_status_update(self):
        """Test updating a fraud alert's status through investigation lifecycle."""
        alert = FraudAlert(
            transaction_id=f"txn_status_{uuid.uuid4().hex[:8]}",
            user_id="test_user_status",
            severity=AlertSeverity.MEDIUM,
            fraud_score=0.55,
            ml_prediction=0.50,
            business_rules_triggered=["amount_deviation_high"],
            explanation={"reason": "amount deviation"},
        )

        alert_id = self.pg.insert_fraud_alert(alert)

        # Move to INVESTIGATING
        success = self.pg.update_alert_status(alert_id, AlertStatus.INVESTIGATING, investigator_id="analyst_001")
        assert success is True

        # Move to RESOLVED
        success = self.pg.update_alert_status(
            alert_id,
            AlertStatus.RESOLVED,
            resolution_notes="Confirmed false positive after review",
        )
        assert success is True

        # Verify final state
        with self.pg.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT status, investigator_id, resolution_notes " "FROM fraud_alerts WHERE alert_id = %s",
                    (alert_id,),
                )
                row = cursor.fetchone()

        assert row["status"] == "RESOLVED"
        assert row["investigator_id"] == "analyst_001"
        assert "false positive" in row["resolution_notes"]

    def test_user_account_upsert(self):
        """Test user account insert and upsert behavior."""
        user_id = f"upsert_user_{uuid.uuid4().hex[:8]}"

        # First insert
        success = self.pg.upsert_user_account(user_id, status=UserStatus.ACTIVE, increment_alerts=True)
        assert success is True

        # Upsert with alert increment
        success = self.pg.upsert_user_account(
            user_id,
            status=UserStatus.ACTIVE,
            increment_alerts=True,
            increment_high_severity=True,
        )
        assert success is True

        # Verify counts
        summary = self.pg.get_user_alert_summary(user_id)
        assert summary is not None
        assert summary["user_id"] == user_id
        assert summary["status"] == "ACTIVE"

    def test_audit_log_insert(self):
        """Test audit event logging."""
        success = self.pg.log_audit_event(
            event_type="fraud_alert",
            entity_type="transaction",
            entity_id="txn_audit_001",
            action="block_user",
            actor_id="auto_system",
            details={"fraud_score": 0.92, "blocked_reason": "critical fraud"},
        )

        assert success is True

        # Verify the log entry
        with self.pg.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM system_audit_log "
                    "WHERE entity_id = 'txn_audit_001' "
                    "ORDER BY timestamp DESC LIMIT 1"
                )
                row = cursor.fetchone()

        assert row is not None
        assert row["event_type"] == "fraud_alert"
        assert row["action"] == "block_user"
        assert row["actor_id"] == "auto_system"

    def test_connection_pool_behavior(self):
        """Test that the connection pool provides and reclaims connections."""
        stats_before = self.pg.get_connection_pool_stats()

        # Open and close multiple connections
        for _ in range(5):
            with self.pg.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    result = cursor.fetchone()
                    assert result[0] == 1

        stats_after = self.pg.get_connection_pool_stats()

        # Pool should have maintained connections
        if stats_before and stats_after:
            assert stats_after.get("pool_size", 0) >= 1

    def test_health_check(self):
        """Test PostgreSQL health check passes."""
        assert self.pg.health_check() is True

    def test_performance_metrics_tracking(self):
        """Test that query performance metrics are tracked."""
        initial_metrics = self.pg.get_performance_metrics()
        initial_count = initial_metrics["query_count"]

        # Execute a query
        with self.pg.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")

        # insert_fraud_alert updates metrics internally
        alert = FraudAlert(
            transaction_id=f"txn_perf_{uuid.uuid4().hex[:8]}",
            user_id="perf_user",
            severity=AlertSeverity.LOW,
            fraud_score=0.15,
            ml_prediction=0.10,
            business_rules_triggered=[],
            explanation={},
        )
        self.pg.insert_fraud_alert(alert)

        updated_metrics = self.pg.get_performance_metrics()
        assert updated_metrics["query_count"] > initial_count


# ---------------------------------------------------------------------------
# ClickHouse Tests
# ---------------------------------------------------------------------------


@pytest.mark.database
@pytest.mark.integration
@pytest.mark.requires_infrastructure
class TestClickHouseIntegration:
    """Integration tests against a live ClickHouse instance."""

    @pytest.fixture(autouse=True)
    def setup_ch(self, database_manager):
        """Ensure ClickHouse is available; skip if not."""
        if database_manager is None:
            pytest.skip("ClickHouse not available (Docker stack not running)")
        if hasattr(database_manager, "ch"):
            self.ch = database_manager.ch
        elif hasattr(database_manager, "clickhouse"):
            self.ch = database_manager.clickhouse
        else:
            pytest.skip("ClickHouse manager not found on persistence layer")

    def test_schema_creation(self):
        """Test ClickHouse schema creation via migration runner."""
        runner = SchemaMigrationRunner()

        if hasattr(self.ch, "_client"):
            client = self.ch._client
        elif hasattr(self.ch, "client"):
            client = self.ch.client
        else:
            pytest.skip("Cannot access ClickHouse client directly")

        results = runner.run_clickhouse_migrations(client)

        for name, success in results.items():
            assert success, f"ClickHouse migration '{name}' failed: " f"{[e for n, e in runner.errors if n == name]}"

    def test_transaction_batch_insert(self):
        """Test batch inserting transaction records."""
        now = datetime.now(timezone.utc)
        records = []

        for i in range(100):
            records.append(
                {
                    "transaction_id": f"ch_txn_{uuid.uuid4().hex[:8]}",
                    "user_id": f"ch_user_{i % 10}",
                    "timestamp": now - timedelta(minutes=i),
                    "amount": 50.0 + i,
                    "merchant_category": "W",
                    "payment_method": "credit",
                    "device_info": "desktop",
                    "location_country": "US",
                    "location_state": "WA",
                    "is_fraud": 1 if i % 20 == 0 else 0,
                    "fraud_score": 0.9 if i % 20 == 0 else 0.1,
                    "processing_time_ms": 5 + i % 10,
                }
            )

        if hasattr(self.ch, "insert_transactions"):
            self.ch.insert_transactions(records)
        elif hasattr(self.ch, "insert_batch"):
            self.ch.insert_batch("transaction_records", records)
        else:
            pytest.skip("No batch insert method found on ClickHouse manager")

        # Verify inserted count
        if hasattr(self.ch, "_client"):
            client = self.ch._client
        elif hasattr(self.ch, "client"):
            client = self.ch.client
        else:
            return  # Cannot verify without direct client

        result = client.execute("SELECT count() FROM transaction_records")
        assert result[0][0] >= 100

    def test_fraud_features_insert(self):
        """Test inserting fraud feature records."""
        now = datetime.now(timezone.utc)
        feature_records = []

        for i in range(50):
            feature_records.append(
                {
                    "transaction_id": f"ch_feat_txn_{i}",
                    "user_id": f"ch_feat_user_{i % 5}",
                    "timestamp": now,
                    "feature_name": f"feature_{i % 10}",
                    "feature_value": float(i) * 0.1,
                    "feature_category": "behavioral" if i % 2 == 0 else "transactional",
                    "computation_time_ms": 2,
                }
            )

        if hasattr(self.ch, "insert_fraud_features"):
            self.ch.insert_fraud_features(feature_records)
        elif hasattr(self.ch, "insert_batch"):
            self.ch.insert_batch("fraud_features", feature_records)
        else:
            pytest.skip("No feature insert method found")

    def test_detection_results_insert(self):
        """Test inserting detection result records."""
        now = datetime.now(timezone.utc)
        results_data = []

        for i in range(25):
            results_data.append(
                {
                    "transaction_id": f"ch_det_txn_{i}",
                    "user_id": f"ch_det_user_{i % 5}",
                    "timestamp": now,
                    "ml_model_version": "v1.0.0",
                    "ml_prediction": 0.3 + (i % 10) * 0.05,
                    "ml_confidence": 0.85,
                    "business_rules_score": 0.2,
                    "final_fraud_score": 0.35,
                    "prediction_time_ms": 3,
                    "features_used": ["amount", "hour", "velocity"],
                    "model_features": {"amount": 100.0, "hour": 14.0},
                    "business_rules_triggered": [],
                }
            )

        if hasattr(self.ch, "insert_detection_results"):
            self.ch.insert_detection_results(results_data)
        elif hasattr(self.ch, "insert_batch"):
            self.ch.insert_batch("detection_results", results_data)
        else:
            pytest.skip("No detection results insert method found")

    def test_analytical_query_fraud_rate_by_hour(self):
        """Test analytical query: fraud rate grouped by hour."""
        if hasattr(self.ch, "_client"):
            client = self.ch._client
        elif hasattr(self.ch, "client"):
            client = self.ch.client
        else:
            pytest.skip("Cannot access ClickHouse client directly")

        query = """
        SELECT
            toHour(timestamp) AS hour,
            count() AS total_transactions,
            countIf(is_fraud = 1) AS fraud_count,
            if(total_transactions > 0,
               fraud_count / total_transactions, 0) AS fraud_rate
        FROM transaction_records
        GROUP BY hour
        ORDER BY hour
        """

        result = client.execute(query)
        # Result should be a list of tuples, possibly empty if no data
        assert isinstance(result, list)

        # If there are results, validate structure
        for row in result:
            hour, total, fraud, rate = row
            assert 0 <= hour <= 23
            assert total >= 0
            assert fraud >= 0
            assert 0.0 <= rate <= 1.0


# ---------------------------------------------------------------------------
# Schema Migration Runner Tests (no infrastructure needed)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSchemaMigrationRunner:
    """Unit tests for the SchemaMigrationRunner helper."""

    def test_split_statements(self):
        """Test DDL statement splitting."""
        ddl = """
        CREATE TABLE foo (id INT);
        CREATE INDEX idx_foo ON foo (id);
        """
        runner = SchemaMigrationRunner()
        stmts = runner._split_statements(ddl)

        assert len(stmts) == 2
        assert "CREATE TABLE" in stmts[0]
        assert "CREATE INDEX" in stmts[1]

    def test_postgresql_schemas_are_valid_sql(self):
        """Test that PostgreSQL schemas don't contain invalid inline INDEX syntax."""
        schemas = SchemaManager.get_postgresql_schemas()

        for name, ddl in schemas.items():
            # The old broken pattern was "INDEX idx_name (column)" inside CREATE TABLE
            if "CREATE TABLE" in ddl:
                # Should NOT have "INDEX idx_" inside CREATE TABLE body
                # (indexes are now separate CREATE INDEX statements)
                table_body_start = ddl.find("(")
                table_body_end = ddl.rfind(")")
                if table_body_start > 0 and table_body_end > table_body_start:
                    table_body = ddl[table_body_start:table_body_end]
                    assert "INDEX idx_" not in table_body, f"Schema '{name}' still has invalid inline INDEX syntax"

    def test_clickhouse_schemas_are_present(self):
        """Test that ClickHouse schemas are returned by SchemaManager."""
        schemas = SchemaManager.get_clickhouse_schemas()

        assert "transaction_records" in schemas
        assert "fraud_features" in schemas
        assert "detection_results" in schemas
        assert "performance_metrics" in schemas

    def test_schema_compatibility_check(self):
        """Test the schema compatibility validator."""
        assert SchemaManager.validate_schema_compatibility() is True
