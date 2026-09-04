"""Enforce one active aggregate KPI record per user and period.

Revision ID: 20260904_kpi_integrity
Revises: a1b2c3d4e5f6
"""

from alembic import op
import sqlalchemy as sa


revision = "20260904_kpi_integrity"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    # Never choose an authoritative record implicitly during deployment.
    # Existing duplicates require an explicit, audited business decision.
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM kpi_records
                WHERE is_deleted = FALSE
                GROUP BY user_id, period_type, period_start
                HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Cannot create active KPI uniqueness index: duplicate user/period records exist. '
                    'Produce an audited duplicate report and remediate explicitly before retrying.';
            END IF;
        END $$;
    """))
    op.create_index(
        "uq_active_kpi_record_user_period",
        "kpi_records",
        ["user_id", "period_type", "period_start"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )


def downgrade():
    op.drop_index("uq_active_kpi_record_user_period", table_name="kpi_records")
