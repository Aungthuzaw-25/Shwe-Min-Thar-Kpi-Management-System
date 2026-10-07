"""Store score values in KPI domain history.

Revision ID: 20260915_kpi_history_scores
Revises: oauth_user_provider_unique
"""

from alembic import op
import sqlalchemy as sa


revision = "20260915_kpi_history_scores"
down_revision = "oauth_user_provider_unique"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "kpi_history",
        sa.Column("old_weighted_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "kpi_history",
        sa.Column("new_weighted_score", sa.Float(), nullable=True),
    )


def downgrade():
    op.drop_column("kpi_history", "new_weighted_score")
    op.drop_column("kpi_history", "old_weighted_score")
