"""Remove SMTP passwords from system settings JSON.

Revision ID: 20260910_remove_smtp_passwords
Revises: 20260910_secure_auth_sessions
"""

from alembic import op


revision = "20260910_remove_smtp_passwords"
down_revision = "20260910_secure_auth_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE system_settings "
        "SET value = value - 'password' "
        "WHERE key = 'email' AND value ? 'password'"
    )


def downgrade():
    # SMTP passwords are intentionally non-recoverable and must remain external.
    pass
