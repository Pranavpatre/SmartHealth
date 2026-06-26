"""Initial schema — core tables, enums, extensions, hypertables.

Revision ID: 0001
Revises:
Create Date: 2026-06-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Extensions
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    # ------------------------------------------------------------------
    # ENUM types
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TYPE facility_type AS ENUM (
            'PHC', 'CHC', 'SUB_CENTRE', 'DISTRICT_HOSPITAL'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE user_role AS ENUM (
            'FIELD_WORKER', 'PHC_ADMIN', 'DISTRICT_OFFICER',
            'STATE_ADMIN', 'SUPERADMIN'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE medicine_category AS ENUM (
            'ESSENTIAL', 'ANTIBIOTIC', 'VACCINE', 'ORS', 'ANALGESIC',
            'ANTIDIABETIC', 'ANTIHYPERTENSIVE', 'ANTIMALARIAL',
            'DIAGNOSTICS_KIT', 'REAGENT', 'EQUIPMENT', 'OTHER'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE prediction_type AS ENUM (
            'STOCKOUT', 'FOOTFALL', 'DIAGNOSTIC_SHORTAGE',
            'ANOMALY', 'HEALTH_SCORE'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE transfer_status AS ENUM (
            'PENDING', 'APPROVED', 'DEFERRED', 'IN_TRANSIT',
            'COMPLETED', 'CANCELLED'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE alert_severity AS ENUM ('INFO', 'WARNING', 'CRITICAL')
        """
    )
    op.execute(
        """
        CREATE TYPE alert_status AS ENUM (
            'OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'SNOOZED'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE procurement_status AS ENUM (
            'FLAGGED', 'REVIEWED', 'ORDERED', 'DELIVERED', 'CLOSED'
        )
        """
    )

    # ------------------------------------------------------------------
    # Reference / lookup tables
    # ------------------------------------------------------------------
    op.create_table(
        "states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(5), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
    )

    op.create_table(
        "districts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "state_id",
            sa.Integer(),
            sa.ForeignKey("states.id"),
            nullable=False,
        ),
        sa.Column("code", sa.String(10), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
    )

    # facilities — geometry column added via raw SQL after table creation
    # so PostGIS type syntax is handled natively by the DB.
    op.create_table(
        "facilities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "district_id",
            sa.Integer(),
            sa.ForeignKey("districts.id"),
            nullable=False,
        ),
        sa.Column("code", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "facility_type",
            sa.Enum(
                "PHC", "CHC", "SUB_CENTRE", "DISTRICT_HOSPITAL",
                name="facility_type",
                create_type=False,
            ),
            nullable=False,
        ),
        # location column: GEOMETRY(Point, 4326) — added via ALTER below
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column(
            "bed_capacity", sa.Integer(), nullable=False, server_default="10"
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    # Add PostGIS geometry column
    op.execute(
        "ALTER TABLE facilities ADD COLUMN location GEOMETRY(Point, 4326);"
    )

    op.create_index("facilities_district_idx", "facilities", ["district_id"])
    op.execute(
        "CREATE INDEX facilities_location_idx ON facilities USING GIST(location);"
    )

    # ------------------------------------------------------------------
    # Medicine & Supplies Catalogue
    # ------------------------------------------------------------------
    op.create_table(
        "medicines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("generic_name", sa.String(200), nullable=True),
        sa.Column(
            "category",
            sa.Enum(
                "ESSENTIAL", "ANTIBIOTIC", "VACCINE", "ORS", "ANALGESIC",
                "ANTIDIABETIC", "ANTIHYPERTENSIVE", "ANTIMALARIAL",
                "DIAGNOSTICS_KIT", "REAGENT", "EQUIPMENT", "OTHER",
                name="medicine_category",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "unit", sa.String(20), nullable=False, server_default="'units'"
        ),
        sa.Column(
            "reorder_level", sa.Integer(), nullable=False, server_default="50"
        ),
        sa.Column(
            "lead_time_days",
            sa.Integer(),
            nullable=False,
            server_default="7",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
    )

    # ------------------------------------------------------------------
    # Diagnostics catalogue
    # ------------------------------------------------------------------
    op.create_table(
        "diagnostic_tests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("unit", sa.String(20), server_default="'tests'"),
        sa.Column(
            "reorder_level", sa.Integer(), nullable=False, server_default="20"
        ),
    )

    # ------------------------------------------------------------------
    # Disease / Outbreak Calendar
    # ------------------------------------------------------------------
    op.create_table(
        "disease_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "district_id",
            sa.Integer(),
            sa.ForeignKey("districts.id"),
            nullable=False,
        ),
        sa.Column("disease_name", sa.String(100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    # ------------------------------------------------------------------
    # Users & RBAC
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facilities.id"),
            nullable=True,
        ),
        sa.Column(
            "district_id",
            sa.Integer(),
            sa.ForeignKey("districts.id"),
            nullable=True,
        ),
        sa.Column(
            "role",
            sa.Enum(
                "FIELD_WORKER", "PHC_ADMIN", "DISTRICT_OFFICER",
                "STATE_ADMIN", "SUPERADMIN",
                name="user_role",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(15), nullable=False, unique=True),
        sa.Column(
            "language_pref",
            sa.String(10),
            nullable=False,
            server_default="'hi'",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column(
            "id", sa.BigInteger(), primary_key=True, autoincrement=True
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("table_name", sa.String(100), nullable=True),
        sa.Column("record_id", sa.Text(), nullable=True),
        sa.Column("old_value", postgresql.JSONB(), nullable=True),
        sa.Column("new_value", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )

    # ------------------------------------------------------------------
    # Stock Inventory (FEFO batches)
    # ------------------------------------------------------------------
    op.create_table(
        "stock_batches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facilities.id"),
            nullable=False,
        ),
        sa.Column(
            "medicine_id",
            sa.Integer(),
            sa.ForeignKey("medicines.id"),
            nullable=False,
        ),
        sa.Column("batch_number", sa.String(50), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity >= 0", name="stock_batches_qty_check"),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "received_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )

    op.create_index(
        "stock_batches_facility_medicine",
        "stock_batches",
        ["facility_id", "medicine_id"],
    )
    op.create_index(
        "stock_batches_expiry", "stock_batches", ["expiry_date"]
    )

    # Aggregate view
    op.execute(
        """
        CREATE VIEW facility_stock AS
        SELECT
            facility_id,
            medicine_id,
            SUM(quantity)                                               AS total_quantity,
            MIN(expiry_date)                                            AS earliest_expiry,
            COUNT(*) FILTER (WHERE expiry_date < NOW() + INTERVAL '30 days')
                                                                        AS batches_expiring_soon
        FROM stock_batches
        WHERE quantity > 0
        GROUP BY facility_id, medicine_id;
        """
    )

    # ------------------------------------------------------------------
    # Daily Operational Snapshots (TimescaleDB hypertable)
    # ------------------------------------------------------------------
    op.create_table(
        "daily_snapshots",
        sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facilities.id"),
            nullable=False,
        ),
        sa.Column(
            "recorded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("opd_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ipd_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "emergency_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "beds_occupied", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("beds_available", sa.Integer(), nullable=True),
        sa.Column(
            "doctors_present",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "doctors_rostered",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "nurses_present",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "input_channel",
            sa.String(20),
            nullable=True,
            server_default="'app'",
        ),
        sa.Column("language_used", sa.String(10), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.execute(
        "SELECT create_hypertable('daily_snapshots', 'time', if_not_exists => TRUE);"
    )
    op.execute(
        "CREATE INDEX daily_snapshots_facility_time"
        " ON daily_snapshots(facility_id, time DESC);"
    )

    # ------------------------------------------------------------------
    # Diagnostic stock snapshots (TimescaleDB hypertable)
    # ------------------------------------------------------------------
    op.create_table(
        "diagnostic_stock_snapshots",
        sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facilities.id"),
            nullable=False,
        ),
        sa.Column(
            "test_id",
            sa.Integer(),
            sa.ForeignKey("diagnostic_tests.id"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "equipment_status",
            sa.String(20),
            nullable=True,
            server_default="'operational'",
        ),
        sa.Column(
            "recorded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.execute(
        "SELECT create_hypertable('diagnostic_stock_snapshots', 'time', if_not_exists => TRUE);"
    )

    # ------------------------------------------------------------------
    # AI Predictions
    # ------------------------------------------------------------------
    op.create_table(
        "ai_predictions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facilities.id"),
            nullable=False,
        ),
        sa.Column(
            "medicine_id",
            sa.Integer(),
            sa.ForeignKey("medicines.id"),
            nullable=True,
        ),
        sa.Column(
            "test_id",
            sa.Integer(),
            sa.ForeignKey("diagnostic_tests.id"),
            nullable=True,
        ),
        sa.Column(
            "prediction_type",
            sa.Enum(
                "STOCKOUT", "FOOTFALL", "DIAGNOSTIC_SHORTAGE",
                "ANOMALY", "HEALTH_SCORE",
                name="prediction_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "predicted_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "horizon_days", sa.Integer(), nullable=False, server_default="3"
        ),
        sa.Column("predicted_value", sa.Numeric(10, 2), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name="ai_predictions_confidence_check",
        ),
        sa.Column("reasoning", postgresql.JSONB(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("model_version", sa.String(50), nullable=True),
        sa.Column("actual_value", sa.Numeric(10, 2), nullable=True),
        sa.Column("worker_feedback", sa.String(20), nullable=True),
        sa.Column("feedback_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.execute(
        "CREATE INDEX ai_predictions_facility"
        " ON ai_predictions(facility_id, predicted_at DESC);"
    )
    op.execute(
        "CREATE INDEX ai_predictions_type"
        " ON ai_predictions(prediction_type, predicted_at DESC);"
    )

    # ------------------------------------------------------------------
    # Alerts & Notifications
    # ------------------------------------------------------------------
    op.create_table(
        "alerts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facilities.id"),
            nullable=False,
        ),
        sa.Column(
            "prediction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_predictions.id"),
            nullable=True,
        ),
        sa.Column(
            "severity",
            sa.Enum(
                "INFO", "WARNING", "CRITICAL",
                name="alert_severity",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN", "ACKNOWLEDGED", "RESOLVED", "SNOOZED",
                name="alert_status",
                create_type=False,
            ),
            nullable=False,
            server_default="'OPEN'",
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "acknowledged_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.execute(
        "CREATE INDEX alerts_facility_status"
        " ON alerts(facility_id, status, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX alerts_open ON alerts(status, severity) WHERE status = 'OPEN';"
    )

    op.create_table(
        "notifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "alert_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alerts.id"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("response", sa.String(20), nullable=True),
        sa.Column("response_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # ------------------------------------------------------------------
    # Resource Redistribution
    # ------------------------------------------------------------------
    op.create_table(
        "redistribution_plans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "district_id",
            sa.Integer(),
            sa.ForeignKey("districts.id"),
            nullable=False,
        ),
        sa.Column(
            "generated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "approved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "APPROVED", "DEFERRED", "IN_TRANSIT",
                "COMPLETED", "CANCELLED",
                name="transfer_status",
                create_type=False,
            ),
            nullable=False,
            server_default="'PENDING'",
        ),
        sa.Column("total_savings", sa.Numeric(12, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "redistribution_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("redistribution_plans.id"),
            nullable=False,
        ),
        sa.Column(
            "medicine_id",
            sa.Integer(),
            sa.ForeignKey("medicines.id"),
            nullable=True,
        ),
        sa.Column(
            "test_id",
            sa.Integer(),
            sa.ForeignKey("diagnostic_tests.id"),
            nullable=True,
        ),
        sa.Column(
            "from_facility",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facilities.id"),
            nullable=False,
        ),
        sa.Column(
            "to_facility",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facilities.id"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("distance_km", sa.Numeric(6, 2), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("estimated_saving", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "APPROVED", "DEFERRED", "IN_TRANSIT",
                "COMPLETED", "CANCELLED",
                name="transfer_status",
                create_type=False,
            ),
            nullable=False,
            server_default="'PENDING'",
        ),
        sa.Column(
            "trigger_prediction",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_predictions.id"),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------
    # Facility Health Scores (TimescaleDB hypertable)
    # ------------------------------------------------------------------
    op.create_table(
        "facility_health_scores",
        sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facilities.id"),
            nullable=False,
        ),
        sa.Column("medicine_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("doctor_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("bed_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("wait_time_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("diagnostics_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("overall_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("status", sa.String(10), nullable=True),
    )
    op.execute(
        "SELECT create_hypertable('facility_health_scores', 'time', if_not_exists => TRUE);"
    )

    # ------------------------------------------------------------------
    # Procurement Escalation
    # ------------------------------------------------------------------
    op.create_table(
        "procurement_flags",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "district_id",
            sa.Integer(),
            sa.ForeignKey("districts.id"),
            nullable=False,
        ),
        sa.Column(
            "medicine_id",
            sa.Integer(),
            sa.ForeignKey("medicines.id"),
            nullable=True,
        ),
        sa.Column(
            "test_id",
            sa.Integer(),
            sa.ForeignKey("diagnostic_tests.id"),
            nullable=True,
        ),
        sa.Column(
            "flagged_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "flagged_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "FLAGGED", "REVIEWED", "ORDERED", "DELIVERED", "CLOSED",
                name="procurement_status",
                create_type=False,
            ),
            nullable=False,
            server_default="'FLAGGED'",
        ),
        sa.Column("quantity_needed", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # WARNING: this is fully destructive — it drops the entire public
    # schema and recreates it empty.  All data and objects are lost.
    op.execute("DROP SCHEMA public CASCADE;")
    op.execute("CREATE SCHEMA public;")
