"""What's-new entries table (Database design Stage 3).

Revision ID: 20261029_whats_new_entries
Revises: 20261026_kpi_record_exceeded_categories
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "20261029_whats_new_entries"
down_revision = "20261026_kpi_record_exceeded_categories"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "whats_new_entries",
        sa.Column("id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("how_to", sa.Text(), nullable=True),
        sa.Column("link_path", sa.String(length=255), nullable=True),
        sa.Column("link_label", sa.String(length=100), nullable=True),
        sa.Column("image_path", sa.String(length=255), nullable=True),
        sa.Column("visible_to", sa.String(length=20), nullable=False, server_default="ALL"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="DRAFT"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", PG_UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Public-list index (status + published_at; DESC is in the
    # query's ORDER BY) and the visibility filter index.
    op.create_index(
        "ix_whats_new_entries_status_published",
        "whats_new_entries",
        ["status", "published_at"],
    )
    op.create_index(
        "ix_whats_new_entries_visible_to",
        "whats_new_entries",
        ["visible_to"],
    )


def downgrade():
    op.drop_index(
        "ix_whats_new_entries_visible_to", table_name="whats_new_entries"
    )
    op.drop_index(
        "ix_whats_new_entries_status_published",
        table_name="whats_new_entries",
    )
    op.drop_table("whats_new_entries")
