"""Add OAuth identities and explicit local credential state."""

from alembic import op
import sqlalchemy as sa


revision = "20260911_add_oauth_identities"
down_revision = "20260911_add_debug_log_level"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("users", "hashed_password", existing_type=sa.String(length=255), nullable=True)
    op.add_column(
        "users",
        sa.Column("local_password_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.create_check_constraint(
        "ck_users_local_password_consistency",
        "users",
        "(local_password_enabled = TRUE AND hashed_password IS NOT NULL) "
        "OR (local_password_enabled = FALSE AND hashed_password IS NULL)",
    )
    op.create_table(
        "user_identities",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), server_default="", nullable=False),
        sa.Column("provider_subject", sa.String(length=255), nullable=False),
        sa.Column("provider_email", sa.String(length=255), nullable=True),
        sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("linked_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "issuer", "tenant_id", "provider_subject",
            name="uq_user_identity_provider_subject",
        ),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])
    op.create_index("ix_user_identities_provider_email", "user_identities", ["provider_email"])
    op.create_index(
        "ix_user_identities_provider_lookup",
        "user_identities",
        ["provider", "issuer", "tenant_id"],
    )
    op.create_index("ix_user_identities_revoked_at", "user_identities", ["revoked_at"])


def downgrade():
    op.drop_index("ix_user_identities_revoked_at", table_name="user_identities")
    op.drop_index("ix_user_identities_provider_lookup", table_name="user_identities")
    op.drop_index("ix_user_identities_provider_email", table_name="user_identities")
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
    op.drop_constraint("ck_users_local_password_consistency", "users", type_="check")
    op.drop_column("users", "local_password_enabled")
    op.alter_column("users", "hashed_password", existing_type=sa.String(length=255), nullable=False)
