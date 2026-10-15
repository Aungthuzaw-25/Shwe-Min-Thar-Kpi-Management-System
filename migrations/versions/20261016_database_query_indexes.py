"""Add composite indexes for frequent dashboard and workflow queries.

Revision ID: 20261016_db_query_indexes
Revises: 20261015_merge_ui_heads
"""

from alembic import op

revision = "20261016_db_query_indexes"
down_revision = "20261015_merge_ui_heads"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "idx_users_role_deleted_active",
        "users",
        ["role_id", "is_deleted", "is_active"],
    )
    op.create_index(
        "idx_login_history_user_attempted",
        "login_history",
        ["user_id", "attempted_at"],
    )
    op.create_index(
        "idx_kpi_records_user_period_deleted",
        "kpi_records",
        ["user_id", "kpi_period_id", "is_deleted"],
    )
    op.create_index(
        "idx_kpi_assignments_user_deleted_created",
        "kpi_assignments",
        ["user_id", "is_deleted", "created_at"],
    )
    op.create_index(
        "idx_kpi_assignments_user_period_deleted",
        "kpi_assignments",
        ["user_id", "kpi_period_id", "is_deleted"],
    )
    op.create_index(
        "idx_tasks_status_deleted_due_date",
        "tasks",
        ["status", "is_deleted", "due_date"],
    )
    op.create_index(
        "idx_approval_queues_reviewer_created",
        "approval_queues",
        ["reviewer_id", "created_at"],
    )
    op.create_index(
        "idx_audit_logs_created_id",
        "audit_logs",
        ["created_at", "id"],
    )
    op.create_index(
        "idx_audit_logs_action_created_id",
        "audit_logs",
        ["action", "created_at", "id"],
    )


def downgrade():
    op.drop_index("idx_audit_logs_action_created_id", table_name="audit_logs")
    op.drop_index("idx_audit_logs_created_id", table_name="audit_logs")
    op.drop_index("idx_approval_queues_reviewer_created", table_name="approval_queues")
    op.drop_index("idx_tasks_status_deleted_due_date", table_name="tasks")
    op.drop_index(
        "idx_kpi_assignments_user_period_deleted",
        table_name="kpi_assignments",
    )
    op.drop_index(
        "idx_kpi_assignments_user_deleted_created",
        table_name="kpi_assignments",
    )
    op.drop_index("idx_kpi_records_user_period_deleted", table_name="kpi_records")
    op.drop_index("idx_login_history_user_attempted", table_name="login_history")
    op.drop_index("idx_users_role_deleted_active", table_name="users")
