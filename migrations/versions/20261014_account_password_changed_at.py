"""Persist the last successful local password change timestamp.

Revision ID: 20261014_account_password_changed_at
Revises: 20261014_user_account_profile
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_account_password_changed_at"
down_revision = "20261014_user_account_profile"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("users", "password_changed_at")
