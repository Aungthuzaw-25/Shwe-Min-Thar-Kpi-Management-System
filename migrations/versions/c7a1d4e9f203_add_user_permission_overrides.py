"""add user permission overrides

Revision ID: c7a1d4e9f203
Revises: 9d2c7e8a1b4f
"""
from alembic import op
import sqlalchemy as sa

revision = "c7a1d4e9f203"
down_revision = "9d2c7e8a1b4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_permission_overrides",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("permission_id", sa.UUID(), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="ALL"),
        sa.Column("department_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "permission_id", "department_id", name="uq_user_permission_scope"),
    )
    op.create_index("ix_user_permission_overrides_user_id", "user_permission_overrides", ["user_id"])
    op.create_index("ix_user_permission_overrides_permission_id", "user_permission_overrides", ["permission_id"])
    op.create_index("ix_user_permission_overrides_department_id", "user_permission_overrides", ["department_id"])


def downgrade() -> None:
    op.drop_index("ix_user_permission_overrides_department_id", table_name="user_permission_overrides")
    op.drop_index("ix_user_permission_overrides_permission_id", table_name="user_permission_overrides")
    op.drop_index("ix_user_permission_overrides_user_id", table_name="user_permission_overrides")
    op.drop_table("user_permission_overrides")
