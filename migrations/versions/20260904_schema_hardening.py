"""Harden shared soft-delete columns and defaults.

Revision ID: 20260904_schema_hardening
Revises: 20260904_kpi_integrity
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260904_schema_hardening"
down_revision = "20260904_kpi_integrity"
branch_labels = None
depends_on = None


BASE_MODEL_TABLES = (
    "departments",
    "positions",
    "users",
    "roles",
    "permissions",
    "role_permissions",
    "user_departments",
    "user_permission_overrides",
    "kpi_categories",
    "kpi_level_settings",
    "kpi_formulas",
    "kpi_rules",
    "kpi_periods",
    "kpi_assignments",
    "kpi_records",
    "kpi_scoring_rule_items",
    "kpi_action_plans",
    "kpi_history",
    "targets",
    "tasks",
    "submission_batches",
    "task_evidence",
    "task_histories",
    "task_reviews",
    "approval_queues",
    "activity_timelines",
    "formula_audit_logs",
    "notifications",
    "system_settings",
    "system_event_logs",
)


def _existing_tables():
    inspector = inspect(op.get_bind())
    return set(inspector.get_table_names())


def upgrade():
    existing_tables = _existing_tables()
    for table_name in BASE_MODEL_TABLES:
        if table_name not in existing_tables:
            continue
        columns = {column["name"]: column for column in inspect(op.get_bind()).get_columns(table_name)}
        if "is_deleted" not in columns:
            continue

        table = sa.table(table_name, sa.column("is_deleted", sa.Boolean()))
        op.execute(
            table.update()
            .where(table.c.is_deleted.is_(None))
            .values(is_deleted=False)
        )
        op.alter_column(
            table_name,
            "is_deleted",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        )

    # The ORM requires this field for active formula selection. Backfill before
    # tightening nullability so existing databases remain upgradeable.
    if "kpi_formulas" in existing_tables:
        formula_table = sa.table("kpi_formulas", sa.column("is_active", sa.Boolean()))
        op.execute(
            formula_table.update()
            .where(formula_table.c.is_active.is_(None))
            .values(is_active=True)
        )
        op.alter_column(
            "kpi_formulas",
            "is_active",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        )


def downgrade():
    existing_tables = _existing_tables()
    if "kpi_formulas" in existing_tables:
        op.alter_column(
            "kpi_formulas",
            "is_active",
            existing_type=sa.Boolean(),
            nullable=True,
            server_default=None,
        )
    for table_name in BASE_MODEL_TABLES:
        if table_name in existing_tables:
            columns = {column["name"]: column for column in inspect(op.get_bind()).get_columns(table_name)}
            if "is_deleted" in columns:
                op.alter_column(
                    table_name,
                    "is_deleted",
                    existing_type=sa.Boolean(),
                    nullable=True,
                    server_default=None,
                )
