"""Super Admin TOTP recovery: secrets + attempts + peer requests.

New tables ``sa_totp_secrets`` (Fernet-encrypted TOTP secret, one
per SA, confirmed only after the first verified code),
``sa_totp_attempts`` (5-wrong-codes -> 1-hour freeze, per account)
and ``sa_mfa_requests`` (peer-confirmed RESET/DISABLE requests, one
PENDING per target), plus ``auth_sessions.via_recovery`` marking
sessions created by a TOTP recovery login (those sessions may reset
the SA password once without the old password).

Revision ID: 20261026_sa_recovery_totp
Revises: 20261025_tasks_outcome_snapshot
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "20261026_sa_recovery_totp"
down_revision = "20261025_tasks_outcome_snapshot"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sa_totp_secrets",
        sa.Column("id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("user_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("secret_encrypted", sa.String(length=512), nullable=False),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_sa_totp_secrets_user_id", "sa_totp_secrets", ["user_id"])
    op.create_table(
        "sa_totp_attempts",
        sa.Column("id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("user_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("frozen_until", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_sa_totp_attempts_user_id", "sa_totp_attempts", ["user_id"])
    op.create_table(
        "sa_mfa_requests",
        sa.Column("id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("user_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", PG_UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("kind IN ('RESET', 'DISABLE')", name="ck_sa_mfa_requests_kind"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_sa_mfa_requests_status",
        ),
    )
    op.create_index("ix_sa_mfa_requests_user_id", "sa_mfa_requests", ["user_id"])
    op.create_index(
        "uq_sa_mfa_requests_pending_user", "sa_mfa_requests", ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
        sqlite_where=sa.text("status = 'PENDING'"),
    )
    op.add_column(
        "auth_sessions",
        sa.Column("via_recovery", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade():
    op.drop_column("auth_sessions", "via_recovery")
    op.drop_index("uq_sa_mfa_requests_pending_user", table_name="sa_mfa_requests")
    op.drop_index("ix_sa_mfa_requests_user_id", table_name="sa_mfa_requests")
    op.drop_table("sa_mfa_requests")
    op.drop_index("ix_sa_totp_attempts_user_id", table_name="sa_totp_attempts")
    op.drop_table("sa_totp_attempts")
    op.drop_index("ix_sa_totp_secrets_user_id", table_name="sa_totp_secrets")
    op.drop_table("sa_totp_secrets")
