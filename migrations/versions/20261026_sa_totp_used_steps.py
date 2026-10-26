"""Strict no-reuse: steps consumed by password logins.

Table ``sa_totp_used_steps`` (user_id, step) with a unique pair —
one row per consumed login step, pruned past the accepted window
by the consume helper. Setup confirm never writes here.

Revision ID: 20261026_sa_totp_used_steps
Revises: 20261026_sa_recovery_totp
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "20261026_sa_totp_used_steps"
down_revision = "20261026_sa_recovery_totp"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sa_totp_used_steps",
        sa.Column("id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("user_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("step", sa.BigInteger(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "step", name="uq_sa_totp_used_steps_user_step"),
    )
    op.create_index("ix_sa_totp_used_steps_user_id", "sa_totp_used_steps", ["user_id"])


def downgrade():
    op.drop_index("ix_sa_totp_used_steps_user_id", table_name="sa_totp_used_steps")
    op.drop_table("sa_totp_used_steps")
