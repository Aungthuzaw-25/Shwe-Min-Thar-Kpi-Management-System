"""add department priority

Revision ID: 9d2c7e8a1b4f
Revises: 483fbedc741e
"""
from alembic import op
import sqlalchemy as sa

revision = "9d2c7e8a1b4f"
down_revision = "483fbedc741e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("departments", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_departments_priority", "departments", ["priority"])
    op.alter_column("departments", "priority", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_departments_priority", table_name="departments")
    op.drop_column("departments", "priority")
