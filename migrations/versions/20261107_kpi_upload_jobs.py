"""KPI upload background-job receipts (F6).

Revision ID: 20261107_kpi_upload_jobs
Revises: 20261029_whats_new_entries

New table ONLY (no changes to existing tables). The job row is the
receipt for an async upload: the request stages the file to MinIO,
inserts a QUEUED row and returns 202; the scheduler pickup claims
it, runs the engine in one transaction, and reports the outcome on
a separate session so an engine rollback never erases the report.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "20261107_kpi_upload_jobs"
down_revision = "20261029_whats_new_entries"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "kpi_upload_jobs",
        sa.Column("id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("owner_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("owner_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="QUEUED"),
        sa.Column("source_object_key", sa.String(length=500), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("period_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("period_label", sa.String(length=100), nullable=True),
        sa.Column("rows_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.JSON(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # "My jobs" list (owner + newest first).
    op.create_index(
        "idx_kpi_upload_jobs_owner_created",
        "kpi_upload_jobs",
        ["owner_id", "created_at"],
    )
    # Pickup: oldest QUEUED first.
    op.create_index(
        "idx_kpi_upload_jobs_status_created",
        "kpi_upload_jobs",
        ["status", "created_at"],
    )
    # Stuck-job reclaim scan (RUNNING + stale heartbeat).
    op.create_index(
        "idx_kpi_upload_jobs_status_heartbeat",
        "kpi_upload_jobs",
        ["status", "last_heartbeat_at"],
    )


def downgrade():
    op.drop_index(
        "idx_kpi_upload_jobs_status_heartbeat", table_name="kpi_upload_jobs"
    )
    op.drop_index(
        "idx_kpi_upload_jobs_status_created", table_name="kpi_upload_jobs"
    )
    op.drop_index(
        "idx_kpi_upload_jobs_owner_created", table_name="kpi_upload_jobs"
    )
    op.drop_table("kpi_upload_jobs")
