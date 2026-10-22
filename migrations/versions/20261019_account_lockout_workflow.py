"""Add configurable account lockout and unlock requests.

Revision ID: 20261019_account_lockout
Revises: 20261016_db_query_indexes
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261019_account_lockout"
down_revision = "20261016_db_query_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("users", "lockout_until", new_column_name="locked_until")
    op.execute("UPDATE users SET is_locked = FALSE WHERE is_locked IS NULL")
    op.execute("UPDATE users SET failed_login_attempts = 0 WHERE failed_login_attempts IS NULL")
    op.alter_column(
        "users",
        "is_locked",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    )
    op.alter_column(
        "users",
        "failed_login_attempts",
        existing_type=sa.Integer(),
        nullable=False,
        server_default="0",
    )
    op.add_column("users", sa.Column("last_failed_ip", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("last_failed_at", sa.DateTime(), nullable=True))

    op.create_table(
        "user_security_settings",
        sa.Column("singleton_key", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("max_failed_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("lockout_duration_minutes", sa.Integer(), server_default="29", nullable=False),
        sa.Column("superadmin_lockout_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("max_failed_attempts >= 1", name="ck_user_security_max_failed_attempts"),
        sa.CheckConstraint("lockout_duration_minutes >= 1", name="ck_user_security_lockout_duration"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("singleton_key"),
    )

    op.create_table(
        "unlock_requests",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ip_address", sa.String(length=50), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('PENDING', 'RESOLVED')", name="ck_unlock_requests_status"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_unlock_requests_user_id", "unlock_requests", ["user_id"])
    op.create_index(
        "uq_unlock_requests_pending_user",
        "unlock_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade():
    op.drop_index("uq_unlock_requests_pending_user", table_name="unlock_requests")
    op.drop_index("ix_unlock_requests_user_id", table_name="unlock_requests")
    op.drop_table("unlock_requests")
    op.drop_table("user_security_settings")
    op.drop_column("users", "last_failed_at")
    op.drop_column("users", "last_failed_ip")
    op.alter_column("users", "locked_until", new_column_name="lockout_until")
    op.alter_column(
        "users", "failed_login_attempts", existing_type=sa.Integer(), nullable=True, server_default=None
    )
    op.alter_column(
        "users", "is_locked", existing_type=sa.Boolean(), nullable=True, server_default=None
    )
