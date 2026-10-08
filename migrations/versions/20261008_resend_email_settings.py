"""Move support email into the existing email system setting.

Revision ID: 20261008_resend_email_settings
Revises: 20260916_reconcile_model_schema
"""

from datetime import UTC, datetime
import uuid

import sqlalchemy as sa
from alembic import op


revision = "20261008_resend_email_settings"
down_revision = "20260916_reconcile_model_schema"
branch_labels = None
depends_on = None


DEFAULT_FROM_NAME = "Shwe Min Thar KPI Management System"


def _settings_table():
    return sa.table(
        "system_settings",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("key", sa.String(100)),
        sa.column("value", sa.JSON()),
        sa.column("is_deleted", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )


def _setting(bind, table, key):
    return bind.execute(
        sa.select(table.c.id, table.c.value).where(table.c.key == key)
    ).mappings().first()


def upgrade():
    bind = op.get_bind()
    table = _settings_table()
    email_row = _setting(bind, table, "email")
    general_row = _setting(bind, table, "general")
    email_value = dict(email_row["value"] or {}) if email_row else {}
    general_value = dict(general_row["value"] or {}) if general_row else {}

    support_email = email_value.get("support_email") or general_value.get("support_email")
    for secret_field in ("password", "api_key", "resend_api_key"):
        email_value.pop(secret_field, None)
    email_value["email_provider"] = "resend"
    email_value.setdefault("from_email", None)
    email_value.setdefault("from_name", DEFAULT_FROM_NAME)
    email_value["support_email"] = support_email
    if email_row:
        bind.execute(
            table.update().where(table.c.id == email_row["id"]).values(value=email_value)
        )
    else:
        now = datetime.now(UTC).replace(tzinfo=None)
        bind.execute(
            table.insert().values(
                id=uuid.uuid4(),
                key="email",
                value=email_value,
                is_deleted=False,
                created_at=now,
                updated_at=None,
            )
        )

    if general_row and "support_email" in general_value:
        general_value.pop("support_email")
        bind.execute(
            table.update().where(table.c.id == general_row["id"]).values(value=general_value)
        )


def downgrade():
    bind = op.get_bind()
    table = _settings_table()
    email_row = _setting(bind, table, "email")
    general_row = _setting(bind, table, "general")
    if not email_row:
        return

    email_value = dict(email_row["value"] or {})
    support_email = email_value.pop("support_email", None)
    if support_email is not None:
        general_value = dict(general_row["value"] or {}) if general_row else {}
        general_value["support_email"] = support_email
        if general_row:
            bind.execute(
                table.update().where(table.c.id == general_row["id"]).values(value=general_value)
            )
        else:
            now = datetime.now(UTC).replace(tzinfo=None)
            bind.execute(
                table.insert().values(
                    id=uuid.uuid4(),
                    key="general",
                    value=general_value,
                    is_deleted=False,
                    created_at=now,
                    updated_at=None,
                )
            )
    bind.execute(
        table.update().where(table.c.id == email_row["id"]).values(value=email_value)
    )
