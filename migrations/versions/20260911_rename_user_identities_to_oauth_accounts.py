"""Rename the canonical OAuth identity table.

The existing user_identities table already contains the complete external
identity model. Rename it in place so existing linked accounts and audit
history are preserved without introducing a second identity table.
"""

from alembic import op


revision = "20260911_rename_oauth_accounts"
down_revision = "20260911_add_oauth_identities"
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table("user_identities", "oauth_accounts")
    op.execute(
        "ALTER TABLE oauth_accounts "
        "RENAME CONSTRAINT uq_user_identity_provider_subject "
        "TO uq_oauth_account_provider_subject"
    )
    op.execute(
        "ALTER INDEX ix_user_identities_user_id "
        "RENAME TO ix_oauth_accounts_user_id"
    )
    op.execute(
        "ALTER INDEX ix_user_identities_provider_email "
        "RENAME TO ix_oauth_accounts_provider_email"
    )
    op.execute(
        "ALTER INDEX ix_user_identities_provider_lookup "
        "RENAME TO ix_oauth_accounts_provider_lookup"
    )
    op.execute(
        "ALTER INDEX ix_user_identities_revoked_at "
        "RENAME TO ix_oauth_accounts_revoked_at"
    )


def downgrade():
    op.execute(
        "ALTER INDEX ix_oauth_accounts_revoked_at "
        "RENAME TO ix_user_identities_revoked_at"
    )
    op.execute(
        "ALTER INDEX ix_oauth_accounts_provider_lookup "
        "RENAME TO ix_user_identities_provider_lookup"
    )
    op.execute(
        "ALTER INDEX ix_oauth_accounts_provider_email "
        "RENAME TO ix_user_identities_provider_email"
    )
    op.execute(
        "ALTER INDEX ix_oauth_accounts_user_id "
        "RENAME TO ix_user_identities_user_id"
    )
    op.execute(
        "ALTER TABLE oauth_accounts "
        "RENAME CONSTRAINT uq_oauth_account_provider_subject "
        "TO uq_user_identity_provider_subject"
    )
    op.rename_table("oauth_accounts", "user_identities")
