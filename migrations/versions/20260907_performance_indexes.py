"""Add composite indexes for notification and department-scoped queries.

Revision ID: 20260907_performance_indexes
Revises: 20260904_composite_pks
"""

from alembic import op
import sqlalchemy as sa


revision = "20260907_performance_indexes"
down_revision = "20260904_composite_pks"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "kpi_assignments",
        "assigned_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        postgresql_using="assigned_at AT TIME ZONE 'UTC'",
    )
    op.create_index(
        "idx_notifications_user_deleted_created",
        "notifications",
        ["user_id", "is_deleted", "created_at"],
    )
    op.create_index(
        "idx_notifications_user_deleted_read",
        "notifications",
        ["user_id", "is_deleted", "is_read"],
    )
    op.create_index(
        "idx_user_departments_department_user_deleted",
        "user_departments",
        ["department_id", "user_id", "is_deleted"],
    )


def downgrade():
    op.alter_column(
        "kpi_assignments",
        "assigned_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        postgresql_using="assigned_at AT TIME ZONE 'UTC'",
    )
    op.drop_index(
        "idx_user_departments_department_user_deleted",
        table_name="user_departments",
    )
    op.drop_index(
        "idx_notifications_user_deleted_read",
        table_name="notifications",
    )
    op.drop_index(
        "idx_notifications_user_deleted_created",
        table_name="notifications",
    )
