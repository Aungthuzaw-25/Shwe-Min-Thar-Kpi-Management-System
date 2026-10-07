"""Reconcile ORM metadata with the migrated schema.

Revision ID: 20260916_reconcile_model_schema
Revises: 20260915_kpi_history_scores
"""

from alembic import op


revision = "20260916_reconcile_model_schema"
down_revision = "20260915_kpi_history_scores"
branch_labels = None
depends_on = None


def upgrade():
    op.create_foreign_key(
        "fk_dept_head_id",
        "departments",
        "users",
        ["head_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_kpi_assignments_is_deleted",
        "kpi_assignments",
        ["is_deleted"],
    )
    op.create_index(
        "ix_kpi_periods_is_deleted",
        "kpi_periods",
        ["is_deleted"],
    )


def downgrade():
    op.drop_index("ix_kpi_periods_is_deleted", table_name="kpi_periods")
    op.drop_index("ix_kpi_assignments_is_deleted", table_name="kpi_assignments")
    op.drop_constraint("fk_dept_head_id", "departments", type_="foreignkey")
