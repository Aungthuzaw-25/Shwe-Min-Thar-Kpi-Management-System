"""Add the idempotency_keys table for submit idempotency.

One row per (user, Idempotency-Key) submit intent: the row starts as
``in_progress`` before the handler runs and becomes ``completed`` with
the stored response, so a retried submit replays the original result
instead of executing twice. Rows expire at ``expires_at`` and are then
treated as never seen. The schema matches ``app.models.idempotency``
exactly (same columns, unique constraint, and index names).

Revision ID: 20261023_idempotency_keys
Revises: 20261019_account_lockout
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261023_idempotency_keys"
down_revision = "20261019_account_lockout"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key", name="uq_idempotency_user_key"),
    )
    op.create_index(
        "ix_idempotency_keys_user_id", "idempotency_keys", ["user_id"]
    )
    op.create_index(
        "idx_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"]
    )


def downgrade():
    op.drop_index("idx_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_index("ix_idempotency_keys_user_id", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
