"""Add DEBUG to the system log level enum.

Revision ID: 20260911_add_debug_log_level
Revises: 20260910_remove_smtp_passwords
"""

from alembic import op


revision = "20260911_add_debug_log_level"
down_revision = "20260910_remove_smtp_passwords"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE loglevel ADD VALUE IF NOT EXISTS 'DEBUG'")


def downgrade():
    # PostgreSQL enums cannot safely remove a value in place.
    pass
