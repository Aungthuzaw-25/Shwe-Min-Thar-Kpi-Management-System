"""Add indexes for task review and evidence lookups.

Revision ID: 20261015_task_review_evidence
Revises: 20261008_resend_email_settings
"""

from alembic import op


revision = "20261015_task_review_evidence"
down_revision = "20261008_resend_email_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "idx_task_reviews_task_level_created",
        "task_reviews",
        ["task_id", "reviewer_level", "created_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_task_reviews_task_id",
        "task_reviews",
        ["task_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_task_reviews_reviewer_id",
        "task_reviews",
        ["reviewer_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_task_evidence_task_id",
        "task_evidence",
        ["task_id"],
        if_not_exists=True,
    )


def downgrade():
    op.drop_index("ix_task_evidence_task_id", table_name="task_evidence", if_exists=True)
    op.drop_index("ix_task_reviews_reviewer_id", table_name="task_reviews", if_exists=True)
    op.drop_index("ix_task_reviews_task_id", table_name="task_reviews", if_exists=True)
    op.drop_index("idx_task_reviews_task_level_created", table_name="task_reviews")
