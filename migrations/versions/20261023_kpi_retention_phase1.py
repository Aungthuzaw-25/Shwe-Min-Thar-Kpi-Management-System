"""Departed-employee KPI retention, phase 1 (final design, 2026-10-23).

Goal: KPI records, finished tasks and audit logs stay in the live
data permanently. A departed employee's ``users`` row can eventually
be fully deleted while their finished work stays — attributable by
name and leaving date — and the account deletion itself is gated per
calendar year.

1. ``users`` gains a lifecycle ``status`` (ACTIVE / RESIGNED /
   TERMINATED / SUSPENDED, default ACTIVE) plus ``resigned_at``, so
   accounts are archived by status instead of being hard-deleted.
2. ``kpi_records`` gains ``employee_name`` / ``department_name``
   snapshot columns (same pattern as the scoring-rule item name
   snapshots), backfilled from the current users/departments rows;
   ``kpi_records.user_id`` becomes nullable with ON DELETE SET NULL,
   so records (and their ``kpi_history``) survive the user row.
3. ``tasks`` gains an ``owner_name`` snapshot (backfilled the same
   way); ``tasks.owner_id`` becomes nullable with ON DELETE SET NULL,
   so finished tasks survive their owner the same way.
4. ``audit_logs.user_id`` becomes SET NULL (the column was already
   nullable), so the audit trail survives a user removal.
5. Resigned-date snapshots: ``kpi_records.employee_resigned_at`` and
   ``tasks.owner_resigned_at`` (Date, nullable, no backfill — nobody
   has left under the new lifecycle yet). A record is calculated
   before the person leaves, so the offboarding flow stamps these
   when the account is archived; they power the "Resigned · <date>"
   badge on history views after the account row is gone.
6. The ACCOUNT-deletion lock has two halves. ``yearly_retention``
   (one row per calendar year) holds the backup half:
   ``backup_confirmed_at`` / ``backup_confirmed_by``. The download
   half lives in the new general ``report_downloads`` table — one
   row per download of any window kind (``report_type`` MONTHLY /
   QUARTERLY / YEARLY / CUSTOM, plus inclusive ``period_start`` /
   ``period_end`` dates, scope — ``is_company_wide`` or a single
   ``department_id`` with a ``department_name`` snapshot — and
   ``downloaded_by``; the download time is the row's
   ``created_at``). Only a YEARLY row whose window covers the full
   calendar year AND that is company-wide opens the lock;
   monthly/custom or department-scoped downloads never do. Records are never removed, so there is no removal
   tracking; each account deletion is written to the audit log by
   the code performing it.
7. Index ``idx_kpi_records_period_lookup`` (period_type,
   period_start) for the Super Admin year view, which scans records
   of a period type across a date range regardless of user.
7b. Download authorization is grant-based, not permission-based:
   new ``report_download_grants`` table — Super Admin grants a user
   download access per scope (COMPANY with no department, or one
   DEPARTMENT; the pairing is enforced by a check constraint).
   Grants carry ``granted_by`` and a revoke state (``is_active``,
   ``revoked_at``, ``revoked_by``); they die with the grantee's
   account or the department (CASCADE) and merely record the
   grantor (SET NULL). No permission-catalog seed is needed for
   downloads — checks read this table.
8b. Zenn's decisions on the remaining flagged FKs: history-bearing
   links become SET NULL (and nullable where they were not, which
   SET NULL requires) — ``kpi_action_plans.user_id``,
   ``kpi_assignments.user_id``, ``targets.created_by_id``,
   ``submission_batches.submitter_id``,
   ``approval_queues.reviewer_id``, ``task_reviews.reviewer_id``,
   ``users.delegated_to_id``; daily-activity rows die with the
   account — ``activity_timelines.user_id`` becomes CASCADE.
   (``task_evidence`` / ``task_histories`` detail removal at hard
   delete is endpoint behaviour, not schema.)
8. Re-hire support — a small departed-identity registry,
   ``departed_identities`` (original user id as a plain value, name,
   email, department list, resigned date), plus a
   ``departed_identity_id`` grouping key (FK, RESTRICT) on
   ``kpi_records`` and ``tasks``. The registry form was chosen over
   more per-record snapshot columns because grouping by snapshot
   text is not reliable: names repeat, and an email can be
   re-registered by a different person after the original account is
   deleted. The hard-delete flow creates the registry row and points
   the surviving records/tasks at it before deleting the user row,
   so per-identity counts and a later Relink are exact.
   (The system has no employee-code field; email + original user id
   are the identity keys.)

The snapshot columns stay nullable at the schema level on purpose:
this migration can deploy before the Stage-2 code that writes them
for new records, and a NOT NULL constraint would break record/task
creation in between. The backfill below fills every existing row
(every record/task still has a live user — the old FKs guaranteed
it), and Stage 2 keeps new rows filled; a later migration can
tighten the constraint once that is live.

The dropped FK constraints were created unnamed by the initial
migration, so PostgreSQL auto-named them ``<table>_<column>_fkey``;
the replacements get explicit names for future migrations.

Downgrade note: restoring NOT NULL on ``kpi_records.user_id`` /
``tasks.owner_id`` fails if rows were already nulled by a user
deletion — downgrade is a pre-deployment rollback path.

SQLite note: the batch FK/nullability changes rebuild the table on
SQLite; per SQLite's own schema-change procedure, run this migration
with ``PRAGMA foreign_keys=OFF`` there, or the rebuild would fire the
old CASCADE into child tables. On PostgreSQL the same operations are
plain in-place ALTERs and are unaffected.

Revision ID: 20261023_kpi_retention_phase1
Revises: 20261019_account_lockout
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261023_kpi_retention_phase1"
down_revision = "20261024_formula_periods"
branch_labels = None
depends_on = None

_DISPLAY_NAME = "TRIM(u.first_name || ' ' || u.last_name)"


def upgrade():
    # 1. users lifecycle
    op.add_column(
        "users",
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="ACTIVE",
            nullable=False,
        ),
    )
    op.add_column("users", sa.Column("resigned_at", sa.Date(), nullable=True))

    # 2. yearly retention gate table
    op.create_table(
        "yearly_retention",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("backup_confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("backup_confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["backup_confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("year"),
    )
    op.create_index("ix_yearly_retention_year", "yearly_retention", ["year"])

    # 2a. general report-download log (all window kinds)
    op.create_table(
        "report_downloads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_type", sa.String(length=20), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("is_company_wide", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("department_name", sa.String(length=255), nullable=True),
        sa.Column("downloaded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["downloaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_report_downloads_window",
        "report_downloads",
        ["report_type", "period_start", "period_end"],
    )
    op.create_index(
        "ix_report_downloads_department_id", "report_downloads", ["department_id"]
    )

    # 2c. report-download grants (per user, per scope)
    op.create_table(
        "report_download_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_type", sa.String(length=10), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "(scope_type = 'COMPANY' AND department_id IS NULL) "
            "OR (scope_type = 'DEPARTMENT' AND department_id IS NOT NULL)",
            name="ck_report_download_grants_scope_department",
        ),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_report_download_grants_user_id", "report_download_grants", ["user_id"]
    )

    # 2b. departed-identity registry (re-hire / relink support)
    op.create_table(
        "departed_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("department_name", sa.String(length=500), nullable=True),
        sa.Column("resigned_at", sa.Date(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("original_user_id"),
    )
    op.create_index(
        "ix_departed_identities_original_user_id",
        "departed_identities",
        ["original_user_id"],
    )
    op.create_index("ix_departed_identities_email", "departed_identities", ["email"])

    # 3. kpi_records snapshots + user link becomes SET NULL
    op.add_column(
        "kpi_records", sa.Column("employee_name", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "kpi_records",
        sa.Column("department_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "kpi_records", sa.Column("employee_resigned_at", sa.Date(), nullable=True)
    )
    op.create_index(
        "idx_kpi_records_period_lookup", "kpi_records", ["period_type", "period_start"]
    )
    # Display name exactly as the app builds it: "first_name last_name".
    op.execute(
        "UPDATE kpi_records SET employee_name = ("
        f"SELECT {_DISPLAY_NAME} FROM users u "
        "WHERE u.id = kpi_records.user_id)"
    )
    op.execute(
        "UPDATE kpi_records SET department_name = ("
        "SELECT d.name FROM departments d "
        "WHERE d.id = kpi_records.department_id) "
        "WHERE kpi_records.department_id IS NOT NULL"
    )
    op.add_column(
        "kpi_records",
        sa.Column("departed_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_kpi_records_departed_identity_id", "kpi_records", ["departed_identity_id"]
    )
    with op.batch_alter_table("kpi_records") as batch_op:
        batch_op.alter_column("user_id", nullable=True)
        batch_op.drop_constraint("kpi_records_user_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_kpi_records_user_id", "users", ["user_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_foreign_key(
            "fk_kpi_records_departed_identity",
            "departed_identities",
            ["departed_identity_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    # 4. tasks owner snapshot + owner link becomes SET NULL
    op.add_column(
        "tasks", sa.Column("owner_name", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "tasks", sa.Column("owner_resigned_at", sa.Date(), nullable=True)
    )
    op.execute(
        "UPDATE tasks SET owner_name = ("
        f"SELECT {_DISPLAY_NAME} FROM users u "
        "WHERE u.id = tasks.owner_id)"
    )
    op.add_column(
        "tasks",
        sa.Column("departed_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_tasks_departed_identity_id", "tasks", ["departed_identity_id"])
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.alter_column("owner_id", nullable=True)
        batch_op.drop_constraint("tasks_owner_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_tasks_owner_id", "users", ["owner_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_foreign_key(
            "fk_tasks_departed_identity",
            "departed_identities",
            ["departed_identity_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    # 5. audit trail survives user removal
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_constraint("audit_logs_user_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_audit_logs_user_id", "users", ["user_id"], ["id"], ondelete="SET NULL"
        )

    # 6. Zenn's FK decisions: history links SET NULL (+ nullable),
    #    activity rows CASCADE with the account
    with op.batch_alter_table("kpi_action_plans") as batch_op:
        batch_op.drop_constraint("kpi_action_plans_user_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_kpi_action_plans_user_id", "users", ["user_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.alter_column(
            "user_id", existing_type=postgresql.UUID(), nullable=True
        )

    with op.batch_alter_table("kpi_assignments") as batch_op:
        batch_op.drop_constraint("kpi_assignments_user_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_kpi_assignments_user_id", "users", ["user_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.alter_column(
            "user_id", existing_type=postgresql.UUID(), nullable=True
        )

    with op.batch_alter_table("targets") as batch_op:
        batch_op.drop_constraint("targets_created_by_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_targets_created_by_id", "users", ["created_by_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.alter_column(
            "created_by_id", existing_type=postgresql.UUID(), nullable=True
        )

    with op.batch_alter_table("submission_batches") as batch_op:
        batch_op.drop_constraint("submission_batches_submitter_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_submission_batches_submitter_id",
            "users",
            ["submitter_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.alter_column(
            "submitter_id", existing_type=postgresql.UUID(), nullable=True
        )

    with op.batch_alter_table("approval_queues") as batch_op:
        batch_op.drop_constraint("approval_queues_reviewer_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_approval_queues_reviewer_id",
            "users",
            ["reviewer_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.alter_column(
            "reviewer_id", existing_type=postgresql.UUID(), nullable=True
        )

    with op.batch_alter_table("task_reviews") as batch_op:
        batch_op.drop_constraint("task_reviews_reviewer_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_task_reviews_reviewer_id",
            "users",
            ["reviewer_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.alter_column(
            "reviewer_id", existing_type=postgresql.UUID(), nullable=True
        )

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("users_delegated_to_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_users_delegated_to_id",
            "users",
            ["delegated_to_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("activity_timelines") as batch_op:
        batch_op.drop_constraint("activity_timelines_user_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_activity_timelines_user_id",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade():
    with op.batch_alter_table("activity_timelines") as batch_op:
        batch_op.drop_constraint("fk_activity_timelines_user_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "activity_timelines_user_id_fkey", "users", ["user_id"], ["id"]
        )

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("fk_users_delegated_to_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "users_delegated_to_id_fkey", "users", ["delegated_to_id"], ["id"]
        )

    with op.batch_alter_table("task_reviews") as batch_op:
        batch_op.drop_constraint("fk_task_reviews_reviewer_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "task_reviews_reviewer_id_fkey", "users", ["reviewer_id"], ["id"]
        )
        batch_op.alter_column(
            "reviewer_id", existing_type=postgresql.UUID(), nullable=False
        )

    with op.batch_alter_table("approval_queues") as batch_op:
        batch_op.drop_constraint("fk_approval_queues_reviewer_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "approval_queues_reviewer_id_fkey", "users", ["reviewer_id"], ["id"]
        )
        batch_op.alter_column(
            "reviewer_id", existing_type=postgresql.UUID(), nullable=False
        )

    with op.batch_alter_table("submission_batches") as batch_op:
        batch_op.drop_constraint(
            "fk_submission_batches_submitter_id", type_="foreignkey"
        )
        batch_op.create_foreign_key(
            "submission_batches_submitter_id_fkey", "users", ["submitter_id"], ["id"]
        )
        batch_op.alter_column(
            "submitter_id", existing_type=postgresql.UUID(), nullable=False
        )

    with op.batch_alter_table("targets") as batch_op:
        batch_op.drop_constraint("fk_targets_created_by_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "targets_created_by_id_fkey", "users", ["created_by_id"], ["id"]
        )
        batch_op.alter_column(
            "created_by_id", existing_type=postgresql.UUID(), nullable=False
        )

    with op.batch_alter_table("kpi_assignments") as batch_op:
        batch_op.drop_constraint("fk_kpi_assignments_user_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "kpi_assignments_user_id_fkey", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.alter_column(
            "user_id", existing_type=postgresql.UUID(), nullable=False
        )

    with op.batch_alter_table("kpi_action_plans") as batch_op:
        batch_op.drop_constraint("fk_kpi_action_plans_user_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "kpi_action_plans_user_id_fkey", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.alter_column(
            "user_id", existing_type=postgresql.UUID(), nullable=False
        )

    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_constraint("fk_audit_logs_user_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "audit_logs_user_id_fkey", "users", ["user_id"], ["id"]
        )

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_constraint("fk_tasks_owner_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "tasks_owner_id_fkey", "users", ["owner_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.drop_constraint("fk_tasks_departed_identity", type_="foreignkey")
        batch_op.alter_column("owner_id", nullable=False)
    op.drop_index("ix_tasks_departed_identity_id", table_name="tasks")
    op.drop_column("tasks", "departed_identity_id")
    op.drop_column("tasks", "owner_resigned_at")
    op.drop_column("tasks", "owner_name")

    with op.batch_alter_table("kpi_records") as batch_op:
        batch_op.drop_constraint("fk_kpi_records_user_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "kpi_records_user_id_fkey", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.drop_constraint(
            "fk_kpi_records_departed_identity", type_="foreignkey"
        )
        batch_op.alter_column("user_id", nullable=False)
    op.drop_index("idx_kpi_records_period_lookup", table_name="kpi_records")
    op.drop_index("ix_kpi_records_departed_identity_id", table_name="kpi_records")
    op.drop_column("kpi_records", "departed_identity_id")
    op.drop_column("kpi_records", "employee_resigned_at")
    op.drop_column("kpi_records", "department_name")
    op.drop_column("kpi_records", "employee_name")

    op.drop_index(
        "ix_report_download_grants_user_id", table_name="report_download_grants"
    )
    op.drop_table("report_download_grants")
    op.drop_index("ix_report_downloads_department_id", table_name="report_downloads")
    op.drop_index("idx_report_downloads_window", table_name="report_downloads")
    op.drop_table("report_downloads")
    op.drop_index("ix_yearly_retention_year", table_name="yearly_retention")
    op.drop_table("yearly_retention")
    op.drop_index("ix_departed_identities_email", table_name="departed_identities")
    op.drop_index(
        "ix_departed_identities_original_user_id", table_name="departed_identities"
    )
    op.drop_table("departed_identities")

    op.drop_column("users", "resigned_at")
    op.drop_column("users", "status")
