"""Store per-category over-achievement flags on KPI records.

Revision ID: 20261026_kpi_record_exceeded_categories
Revises: 20261025_tasks_outcome_snapshot
"""

import sqlalchemy as sa
from alembic import op


revision = "20261026_kpi_record_exceeded_categories"
down_revision = "20261026_sa_totp_used_steps"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("kpi_records", sa.Column("exceeded_categories", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("kpi_records", "exceeded_categories")
