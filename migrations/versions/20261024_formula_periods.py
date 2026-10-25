"""Formula <-> period explicit links (kpi_formula_periods).

Formulas historically applied through a legacy ``effective_month``
("YYYY-MM") gate, which misfires for freshly created periods
(template downloads failing with "no effective user"). This adds an
explicit many-to-many link table between ``kpi_formulas`` and
``kpi_periods``.

Back-compat read rule (decided): only CURRENT links count. A
formula with current links applies to exactly those periods; a
formula with NO link rows at all keeps the legacy effective-month
rule unchanged, so existing formulas behave exactly as before
until someone links them. The (formula_id, period_id) pair is
unique — a pair links at most once.

Lifecycle (Zenn, 2026-10-24): a formula auto-closes when its
designated span ends. The closed state is its own marker —
``kpi_formulas.closed_at`` (NULL = open) — deliberately NOT the
workflow ``status`` enum (DRAFT/REVIEW/APPROVED/ACTIVE/ARCHIVED is
the authoring flow) and NOT the ``is_active`` admin switch. Closed
formulas are never deleted; their rules stay for later reuse.
Closing flips the formula's current links to history
(``kpi_formula_periods.is_current`` = false; rows are kept).
Reviving clears ``closed_at`` and requires NEW links — old links
never reactivate, and a once-linked formula with no current links
applies nowhere instead of falling back to the legacy rule. Link
rows are hard-removed only when either side is deleted (CASCADE
on both FKs). The pre-close warning needs no extra schema: the
scheduler reads ``kpi_periods.end_date`` over current links.

Chain note: this revision hangs off the current main head
``20261019_account_lockout`` as a third sibling next to
``20261023_idempotency_keys`` and ``20261023_kpi_retention_phase1``.
It touches only its own new table, so at integration Core can
re-chain it in any landing order with a one-line down_revision
change and no conflict.

Revision ID: 20261024_formula_periods
Revises: 20261019_account_lockout
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261024_formula_periods"
down_revision = "20261023_idempotency_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kpi_formula_periods",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("formula_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["formula_id"], ["kpi_formulas.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["period_id"], ["kpi_periods.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "formula_id", "period_id", name="uq_formula_period_link"
        ),
    )
    op.create_index(
        "ix_kpi_formula_periods_formula_id", "kpi_formula_periods", ["formula_id"]
    )
    op.create_index(
        "ix_kpi_formula_periods_period_id", "kpi_formula_periods", ["period_id"]
    )
    op.add_column(
        "kpi_formulas",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kpi_formulas", "closed_at")
    op.drop_index("ix_kpi_formula_periods_period_id", table_name="kpi_formula_periods")
    op.drop_index("ix_kpi_formula_periods_formula_id", table_name="kpi_formula_periods")
    op.drop_table("kpi_formula_periods")
