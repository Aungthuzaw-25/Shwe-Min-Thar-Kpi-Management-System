"""Widen alembic_version.version_num to VARCHAR(255).

Root cause (deploy blocker, 2026-10-28): alembic's default version
table stores version_num as VARCHAR(32), but the revision id
``20261026_kpi_record_exceeded_categories`` is 39 characters, so
stamping it fails with ``value too long for type character
varying(32)``. Because ``migrations/env.py`` runs all migrations
inside a single transaction, that stamp failure rolls back the
whole upgrade run, leaving the DB stuck at an old revision and the
app in a restart loop.

This is the root-cause fix, NOT a rename of the long revision:
renaming would only postpone the same failure to the next long id.
Widening once to 255 leaves generous headroom for any future id.

Chain position: this revision sits between
``20261026_sa_totp_used_steps`` and
``20261026_kpi_record_exceeded_categories``, so the column is
widened BEFORE the long id is ever stamped.

Note on the manual fast path: if the column was already widened
by hand (Neon SQL Editor ALTER + restart), this migration is a
harmless re-application of the same type — no conflict.

Downgrade note: narrowing back to VARCHAR(32) FAILS on any
database where a version longer than 32 characters is already
stamped (i.e. after ``20261026_kpi_record_exceeded_categories``
has applied). That failure is expected — do not downgrade past
this revision on such databases; downgrade only the widen itself
on databases that never stamped the long id.

Revision ID: 20261028_widen_alembic_ver
Revises: 20261026_sa_totp_used_steps
"""

import sqlalchemy as sa
from alembic import op

revision = "20261028_widen_alembic_ver"
down_revision = "20261026_sa_totp_used_steps"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=255),
    )


def downgrade():
    # Fails if a >32-char version is already stamped (see docstring).
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=255),
        type_=sa.String(length=32),
    )
