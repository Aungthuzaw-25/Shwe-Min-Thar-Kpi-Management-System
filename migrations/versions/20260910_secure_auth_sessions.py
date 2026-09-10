"""Store refresh sessions and hashed revocation credentials.

Revision ID: 20260910_secure_auth_sessions
Revises: 20260908_tracking_indexes
"""

from alembic import op
import sqlalchemy as sa


revision = "20260910_secure_auth_sessions"
down_revision = "20260908_tracking_indexes"
branch_labels = None
depends_on = None


def upgrade():
    # Preserve existing revocations while removing plaintext bearer tokens.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.add_column("token_blacklist", sa.Column("token_hash", sa.String(64), nullable=True))
    op.execute(
        "UPDATE token_blacklist SET token_hash = encode(digest(token, 'sha256'), 'hex') "
        "WHERE token_hash IS NULL"
    )
    op.alter_column("token_blacklist", "token_hash", nullable=False)
    op.create_index("ix_token_blacklist_token_hash", "token_blacklist", ["token_hash"], unique=True)
    op.drop_index("ix_token_blacklist_token", table_name="token_blacklist")
    op.drop_column("token_blacklist", "token")

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("device_id", sa.String(128), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("ip_address", sa.String(50), nullable=True),
        sa.Column("remember_me", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refresh_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_refresh_token_hash", "auth_sessions", ["refresh_token_hash"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_index("ix_auth_sessions_revoked_at", "auth_sessions", ["revoked_at"])
    op.create_index(
        "idx_auth_sessions_active_user", "auth_sessions",
        ["user_id", "revoked_at", "expires_at"],
    )


def downgrade():
    op.drop_index("idx_auth_sessions_active_user", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_revoked_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_refresh_token_hash", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.add_column("token_blacklist", sa.Column("token", sa.String(), nullable=True))
    # A downgrade cannot recover plaintext credentials; revoked rows remain
    # represented by an empty legacy value and should be treated as revoked.
    op.drop_index("ix_token_blacklist_token_hash", table_name="token_blacklist")
    op.drop_column("token_blacklist", "token_hash")
    op.create_index("ix_token_blacklist_token", "token_blacklist", ["token"], unique=True)

