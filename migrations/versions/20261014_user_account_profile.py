"""Add optional profile contact and avatar fields.

Revision ID: 20261014_user_account_profile
Revises: 20261008_resend_email_settings
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_user_account_profile"
down_revision = "20261008_resend_email_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("phone_number", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("bio", sa.String(length=500), nullable=True))
    op.add_column("users", sa.Column("avatar_path", sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column("users", "avatar_path")
    op.drop_column("users", "bio")
    op.drop_column("users", "phone_number")
