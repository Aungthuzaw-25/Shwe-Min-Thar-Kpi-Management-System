"""Add indexes used by user and process-tracking queries.

Revision ID: 20260908_tracking_indexes
Revises: 20260907_performance_indexes
"""

from alembic import op


revision = "20260908_tracking_indexes"
down_revision = "20260907_performance_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "idx_task_histories_task_deleted_created",
        "task_histories",
        ["task_id", "is_deleted", "created_at"],
    )
    op.create_index(
        "idx_activity_timelines_user_deleted_created",
        "activity_timelines",
        ["user_id", "is_deleted", "created_at"],
    )
    op.create_index(
        "idx_user_departments_user_department_deleted",
        "user_departments",
        ["user_id", "department_id", "is_deleted"],
    )


def downgrade():
    op.drop_index(
        "idx_user_departments_user_department_deleted",
        table_name="user_departments",
    )
    op.drop_index(
        "idx_activity_timelines_user_deleted_created",
        table_name="activity_timelines",
    )
    op.drop_index(
        "idx_task_histories_task_deleted_created",
        table_name="task_histories",
    )
