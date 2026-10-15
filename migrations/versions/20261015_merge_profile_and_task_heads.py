"""Merge account profile and task review migration heads.

Revision ID: 20261015_merge_ui_heads
Revises: 20261014_avatar_updated_at, 20261015_task_review_evidence
"""

revision = "20261015_merge_ui_heads"
down_revision = ("20261014_avatar_updated_at", "20261015_task_review_evidence")
branch_labels = None
depends_on = None


def upgrade():
    # This intentional no-op merge revision combines the profile and task heads.
    pass


def downgrade():
    # This intentional no-op merge revision has no schema changes to reverse.
    pass
