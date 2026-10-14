"""Track credential/provider reauthentication per auth session.

Revision ID: 20261014_auth_session_reauthentication
Revises: 20261014_account_password_changed_at
"""

import sqlalchemy as sa
from alembic import op


revision = "20261014_auth_session_reauthentication"
down_revision = "20261014_account_password_changed_at"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "auth_sessions",
        sa.Column("last_reauthenticated_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("auth_sessions", "last_reauthenticated_at")
