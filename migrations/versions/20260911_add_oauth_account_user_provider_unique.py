"""Ensure one OAuth account per provider for each internal user."""

from alembic import op


revision = "oauth_user_provider_unique"
down_revision = "20260911_rename_oauth_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_oauth_account_user_provider",
        "oauth_accounts",
        ["user_id", "provider"],
    )


def downgrade():
    op.drop_constraint(
        "uq_oauth_account_user_provider",
        "oauth_accounts",
        type_="unique",
    )
