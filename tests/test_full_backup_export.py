"""Full data export (GET /api/v1/system/backup/full).

Runs the real export builder against an in-memory SQLite database
built from the actual model metadata, so the table list, credential
exclusions and chunked writer are tested against the real schema.
"""

import json
import os
import uuid
import zipfile
from datetime import datetime
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.main  # noqa: F401  (registers every model on Base.metadata)
from app.database.connection import Base
from app.models.audit_log import AuditLog
from app.modules.system import router as system_router
from app.modules.system.full_export import (
    EXCLUDED_COLUMNS,
    EXCLUDED_TABLES,
    build_full_export_zip,
    exportable_tables,
)

TABLES = Base.metadata.tables


@asynccontextmanager
async def _export_session():
    """A session on a fresh in-memory DB with the full real schema."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _seed(session):
    dept_id, user_id, role_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await session.execute(
        insert(TABLES["roles"]).values(
            id=role_id, name="Super Admin", description="root"
        )
    )
    await session.execute(
        insert(TABLES["departments"]).values(
            id=dept_id, name="Finance", priority=1
        )
    )
    await session.execute(
        insert(TABLES["users"]).values(
            id=user_id,
            email="boss@example.com",
            hashed_password="SECRET_PASSWORD_HASH",
            first_name="Boss",
            last_name="User",
            role_id=role_id,
            is_active=True,
        )
    )
    await session.execute(
        insert(TABLES["auth_sessions"]).values(
            id=uuid.uuid4(),
            user_id=user_id,
            refresh_token_hash="LIVE_REFRESH_TOKEN_HASH",
            expires_at=datetime(2030, 1, 1),
            last_used_at=datetime(2026, 1, 1),
        )
    )
    await session.execute(
        insert(TABLES["password_history"]).values(
            id=uuid.uuid4(),
            user_id=user_id,
            hashed_password="OLD_PASSWORD_HASH",
        )
    )
    await session.execute(
        insert(TABLES["token_blacklist"]).values(
            id=uuid.uuid4(), token_hash="REVOKED_TOKEN_HASH"
        )
    )
    await session.execute(
        insert(TABLES["system_settings"]).values(
            id=uuid.uuid4(),
            key="email",
            value={
                "from_name": "KPI",
                "active_provider": "resend",
                "providers": {"resend": {"enabled": True}},
            },
            description="email settings",
        )
    )
    await session.execute(
        insert(TABLES["system_settings"]).values(
            id=uuid.uuid4(),
            key="integrations",
            value={
                "endpoint": "https://example.com/hook",
                "webhook_secret": "whsec_SECRET999",
            },
            description="integration settings",
        )
    )
    await session.commit()
    return {"dept_id": dept_id, "user_id": user_id, "role_id": role_id}


def _read_zip(path):
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        payloads = {name: zf.read(name) for name in names}
    return names, payloads


# ---------------------------------------------------------------------------
# Export spec
# ---------------------------------------------------------------------------

def test_export_spec_covers_all_but_credential_tables():
    exported = {name for name, _table, _cols in exportable_tables()}
    assert exported == set(TABLES) - set(EXCLUDED_TABLES)
    assert set(EXCLUDED_TABLES) == {
        "auth_sessions",
        "password_history",
        "token_blacklist",
    }
    assert EXCLUDED_COLUMNS["users"] == {"hashed_password"}
    # The exported table count is a real inventory, not a sample.
    assert len(exported) >= 30


# ---------------------------------------------------------------------------
# Builder: contents, exclusions, redaction, chunking
# ---------------------------------------------------------------------------

async def test_build_export_zip_contents(tmp_path):
    async with _export_session() as session:
        ids = await _seed(session)
        zip_path = tmp_path / "export.zip"
        manifest = await build_full_export_zip(
            session,
            zip_path,
            settings_sanitizer=system_router._full_export_setting_value,
        )
        names, payloads = _read_zip(zip_path)

    blob = b"".join(payloads.values())

    # Manifest agrees with the builder's return value and the files.
    on_disk = json.loads(payloads["manifest.json"])
    assert on_disk == manifest
    assert manifest["tables"]["departments"] == 1
    assert manifest["tables"]["users"] == 1

    # Every exported table has a file; credential tables have none.
    for name in manifest["tables"]:
        assert f"tables/{name}.json" in names
    for excluded in EXCLUDED_TABLES:
        assert f"tables/{excluded}.json" not in names

    # No credential material anywhere in the archive.
    assert b"SECRET_PASSWORD_HASH" not in blob
    assert b"LIVE_REFRESH_TOKEN_HASH" not in blob
    assert b"OLD_PASSWORD_HASH" not in blob
    assert b"REVOKED_TOKEN_HASH" not in blob
    assert b"whsec_SECRET999" not in blob

    # Users export: the column itself is gone, the data is intact.
    users = json.loads(payloads["tables/users.json"])
    assert users[0]["email"] == "boss@example.com"
    assert "hashed_password" not in users[0]
    assert users[0]["id"] == str(ids["user_id"])

    # Settings export: secrets redacted, shape preserved.
    settings_rows = json.loads(payloads["tables/system_settings.json"])
    email_row = next(r for r in settings_rows if r["key"] == "email")
    assert email_row["value"]["active_provider"] == "resend"
    integrations_row = next(
        r for r in settings_rows if r["key"] == "integrations"
    )
    assert integrations_row["value"]["webhook_secret"] == "[REDACTED]"
    assert (
        integrations_row["value"]["endpoint"] == "https://example.com/hook"
    )


async def test_build_export_chunking_over_500_rows(tmp_path):
    async with _export_session() as session:
        ids = await _seed(session)
        rows = [
            {
                "id": uuid.uuid4(),
                "user_id": ids["user_id"],
                "title": f"n-{i}",
                "message": "hello",
                "notification_type": "INFO",
                "is_read": False,
            }
            for i in range(1100)
        ]
        await session.execute(insert(TABLES["notifications"]), rows)
        await session.commit()

        zip_path = tmp_path / "export.zip"
        manifest = await build_full_export_zip(session, zip_path)
        assert manifest["tables"]["notifications"] == 1100
        _names, payloads = _read_zip(zip_path)

    parsed = json.loads(payloads["tables/notifications.json"])
    assert len(parsed) == 1100  # streamed writer produced valid JSON


# ---------------------------------------------------------------------------
# Endpoint: permission gate + audit log
# ---------------------------------------------------------------------------

def _request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))


def _user(user_id, role_name="Super Admin"):
    role = SimpleNamespace(name=role_name) if role_name else None
    return SimpleNamespace(id=user_id, role=role)


async def test_endpoint_rejects_non_super_admin():
    async with _export_session() as session:
        ids = await _seed(session)
        for role_name in ("Admin", "HR", "L2", None):
            with pytest.raises(HTTPException) as exc_info:
                await system_router.generate_full_data_export(
                    _request(),
                    db=session,
                    current_user=_user(ids["user_id"], role_name),
                )
            assert exc_info.value.status_code == 403


async def test_endpoint_exports_and_writes_audit_log():
    async with _export_session() as session:
        ids = await _seed(session)
        response = await system_router.generate_full_data_export(
            _request(),
            db=session,
            current_user=_user(ids["user_id"]),
        )
        try:
            assert response.media_type == "application/zip"
            assert response.filename.startswith("full_data_export_")
            names, payloads = _read_zip(response.path)
            assert "manifest.json" in names
            assert b"SECRET_PASSWORD_HASH" not in b"".join(
                payloads.values()
            )
        finally:
            os.unlink(response.path)

        audit_rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "DATA_EXPORT")
            )
        ).scalars().all()
        assert len(audit_rows) == 1
        assert audit_rows[0].user_id == ids["user_id"]
        assert audit_rows[0].ip_address == "127.0.0.1"
        assert audit_rows[0].new_value["total_rows"] >= 3
