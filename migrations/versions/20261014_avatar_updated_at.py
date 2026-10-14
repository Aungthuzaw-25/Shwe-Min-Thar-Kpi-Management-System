"""Track profile avatar changes for cache invalidation.

Revision ID: 20261014_avatar_updated_at
Revises: 20261014_auth_session_reauthentication
"""

import sqlalchemy as sa
from alembic import op


revision = "20261014_avatar_updated_at"
down_revision = "20261014_auth_session_reauthentication"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("avatar_updated_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("users", "avatar_updated_at")
