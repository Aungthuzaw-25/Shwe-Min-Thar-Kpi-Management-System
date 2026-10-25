"""Add tasks.outcome_snapshot (Retention Stage 2 completion).

Stage 2 hard delete copies a completed task's outcome facts into
this JSON snapshot (``{completed_at, result}`` plus the task title
and owner name) BEFORE purging the task's history/review trail;
the snapshot row then lives one year past hard delete. The model
column existed without DDL — without this migration a database
upgraded to Stage 2 has no column for the code to write to.

Revision ID: 20261025_tasks_outcome_snapshot
Revises: 20261023_kpi_retention_phase1
"""

import sqlalchemy as sa
from alembic import op

revision = "20261025_tasks_outcome_snapshot"
down_revision = "20261023_kpi_retention_phase1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("outcome_snapshot", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("tasks", "outcome_snapshot")
