"""add is_deleted to kpi tables

Revision ID: a1b2c3d4e5f6
Revises: 51f24dd3e99c
Create Date: 2026-09-02 15:21:30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "51f24dd3e99c"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set:
    inspector = inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns(table_name)}


def upgrade():
    # Initial schema already includes is_deleted via BaseModel.
    # Keep this revision for databases created before that column existed.
    period_cols = _column_names("kpi_periods")
    assign_cols = _column_names("kpi_assignments")

    if "is_deleted" not in period_cols:
        op.add_column(
            "kpi_periods",
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
        op.alter_column("kpi_periods", "is_deleted", server_default=None)

    if "is_deleted" not in assign_cols:
        op.add_column(
            "kpi_assignments",
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
        op.alter_column("kpi_assignments", "is_deleted", server_default=None)


def downgrade():
    # Do not drop is_deleted: it is part of the shared BaseModel schema.
    pass
