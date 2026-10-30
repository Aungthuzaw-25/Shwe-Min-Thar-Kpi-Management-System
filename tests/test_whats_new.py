"""What's-new page backend (Database design Stage 3).

Design: ~/workspace/your_files/agent-docs/database/
whats-new-page-design.md

Route functions are called directly (the period-test pattern);
permission gating goes through the real enforce_permission.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.system import WhatsNewEntry
from app.modules.system.whats_new_router import (
    create_whats_new_entry,
    delete_whats_new_entry,
    list_whats_new,
    publish_whats_new_entry,
    unpublish_whats_new_entry,
    update_whats_new_entry,
)
from app.modules.system.whats_new_schemas import (
    WhatsNewEntryCreate,
    WhatsNewEntryUpdate,
)
from app.modules.system.whats_new_seed import SEED_ENTRIES
from app.modules.users.models import Role

SA = SimpleNamespace(id=uuid4(), role=Role(name="Super Admin"))
ADMIN = SimpleNamespace(id=uuid4(), role=Role(name="Admin"))
HR = SimpleNamespace(id=uuid4(), role=Role(name="HR"))
EMP = SimpleNamespace(id=uuid4(), role=Role(name="Department User"))


def _run(coro_factory, tmp_path):
    db_file = tmp_path / "whatsnew.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    from app.database.base import BaseModel

    BaseModel.metadata.create_all(sync_engine)
    sync_engine.dispose()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}", poolclass=None
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _main():
        async with factory() as db:
            return await coro_factory(db)

    try:
        return asyncio.run(_main())
    finally:
        asyncio.run(engine.dispose())


async def _seed_published(db, title, visible_to, published_at):
    entry = WhatsNewEntry(
        title=title,
        body="စမ်းသပ်ချက်",
        visible_to=visible_to,
        status="PUBLISHED",
        published_at=published_at,
        is_deleted=False,
    )
    db.add(entry)
    await db.commit()
    return entry


async def _seed_draft(db, title="မူကြမ်း", link_path=None):
    entry = WhatsNewEntry(
        title=title,
        body="စမ်းသပ်ချက်",
        link_path=link_path,
        visible_to="ALL",
        status="DRAFT",
        published_at=None,
        is_deleted=False,
    )
    db.add(entry)
    await db.commit()
    return entry


def _titles(rows):
    return [row.title for row in rows]


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def test_publish_gating_non_sa_403_sa_200(tmp_path):
    async def _case(db):
        payload = WhatsNewEntryCreate(title="ခေါင်းစဉ်", body="အကြောင်း")
        for user in (ADMIN, HR, EMP):
            with pytest.raises(HTTPException) as excinfo:
                await create_whats_new_entry(
                    payload=payload, db=db, current_user=user
                )
            assert excinfo.value.status_code == 403
        created = await create_whats_new_entry(
            payload=payload, db=db, current_user=SA
        )
        assert created.status == "DRAFT"
        assert created.published_at is None
        for user in (ADMIN, HR, EMP):
            with pytest.raises(HTTPException) as excinfo:
                await publish_whats_new_entry(
                    entry_id=created.id, db=db, current_user=user
                )
            assert excinfo.value.status_code == 403
        published = await publish_whats_new_entry(
            entry_id=created.id, db=db, current_user=SA
        )
        assert published.status == "PUBLISHED"
        assert published.published_at is not None
        assert published.created_by_id == SA.id
        for user in (ADMIN, HR, EMP):
            with pytest.raises(HTTPException) as excinfo:
                await update_whats_new_entry(
                    entry_id=created.id,
                    payload=WhatsNewEntryUpdate(title="ပြင်"),
                    db=db,
                    current_user=user,
                )
            assert excinfo.value.status_code == 403

    _run(_case, tmp_path)


def test_draft_hidden_from_every_public_list(tmp_path):
    async def _case(db):
        await _seed_draft(db)
        for user in (SA, ADMIN, HR, EMP):
            rows = await list_whats_new(db=db, current_user=user, limit=100)
            assert _titles(rows) == []

    _run(_case, tmp_path)


def test_visibility_super_admin_entry(tmp_path):
    async def _case(db):
        await _seed_published(
            db, "SA သီးသန့်", "SUPER_ADMIN", datetime(2026, 10, 29, tzinfo=UTC)
        )
        assert _titles(await list_whats_new(db=db, current_user=SA, limit=100)) == [
            "SA သီးသန့်"
        ]
        for user in (ADMIN, HR, EMP):
            assert await list_whats_new(db=db, current_user=user, limit=100) == []

    _run(_case, tmp_path)


def test_visibility_admin_entry(tmp_path):
    async def _case(db):
        await _seed_published(
            db, "Admin မိသားစု", "ADMIN", datetime(2026, 10, 29, tzinfo=UTC)
        )
        for user in (SA, ADMIN, HR):
            assert _titles(
                await list_whats_new(db=db, current_user=user, limit=100)
            ) == ["Admin မိသားစု"]
        assert await list_whats_new(db=db, current_user=EMP, limit=100) == []

    _run(_case, tmp_path)


def test_visibility_employee_entry(tmp_path):
    async def _case(db):
        await _seed_published(
            db, "ဝန်ထမ်း", "EMPLOYEE", datetime(2026, 10, 29, tzinfo=UTC)
        )
        for user in (SA, EMP):
            assert _titles(
                await list_whats_new(db=db, current_user=user, limit=100)
            ) == ["ဝန်ထမ်း"]
        for user in (ADMIN, HR):
            assert await list_whats_new(db=db, current_user=user, limit=100) == []

    _run(_case, tmp_path)


def test_order_strictly_published_at_desc(tmp_path):
    async def _case(db):
        await _seed_published(
            db, "အဟောင်း", "ALL", datetime(2026, 10, 25, tzinfo=UTC)
        )
        await _seed_published(
            db, "အသစ်", "ALL", datetime(2026, 10, 29, tzinfo=UTC)
        )
        await _seed_published(
            db, "အလယ်", "ALL", datetime(2026, 10, 27, tzinfo=UTC)
        )
        rows = await list_whats_new(db=db, current_user=SA, limit=100)
        assert _titles(rows) == ["အသစ်", "အလယ်", "အဟောင်း"]
        stamps = [_aware(row.published_at) for row in rows]
        assert stamps == sorted(stamps, reverse=True)

    _run(_case, tmp_path)


def test_publish_unpublish_delete_lifecycle(tmp_path):
    async def _case(db):
        draft = await _seed_draft(db)
        published = await publish_whats_new_entry(
            entry_id=draft.id, db=db, current_user=SA
        )
        assert published.status == "PUBLISHED"
        assert published.published_at is not None
        assert _titles(await list_whats_new(db=db, current_user=EMP, limit=100)) == [
            "မူကြမ်း"
        ]
        unpublished = await unpublish_whats_new_entry(
            entry_id=draft.id, db=db, current_user=SA
        )
        assert unpublished.status == "DRAFT"
        assert unpublished.published_at is None
        assert await list_whats_new(db=db, current_user=EMP, limit=100) == []
        await publish_whats_new_entry(entry_id=draft.id, db=db, current_user=SA)
        deleted = await delete_whats_new_entry(
            entry_id=draft.id, db=db, current_user=SA
        )
        assert deleted.is_deleted is True
        assert await list_whats_new(db=db, current_user=SA, limit=100) == []
        with pytest.raises(HTTPException) as excinfo:
            await publish_whats_new_entry(
                entry_id=draft.id, db=db, current_user=SA
            )
        assert excinfo.value.status_code == 404

    _run(_case, tmp_path)


def test_link_validation_at_publish_and_published_edit(tmp_path):
    async def _case(db):
        bad = await _seed_draft(db, "လင့်ခ်မှား", link_path="https://evil.test/x")
        with pytest.raises(HTTPException) as excinfo:
            await publish_whats_new_entry(entry_id=bad.id, db=db, current_user=SA)
        assert excinfo.value.status_code == 422
        no_slash = await _seed_draft(db, "လင့်ခ်မှား၂", link_path="tasks")
        with pytest.raises(HTTPException) as excinfo:
            await publish_whats_new_entry(
                entry_id=no_slash.id, db=db, current_user=SA
            )
        assert excinfo.value.status_code == 422
        good = await _seed_draft(db, "လင့်ခ်မှန်", link_path="/tasks")
        published = await publish_whats_new_entry(
            entry_id=good.id, db=db, current_user=SA
        )
        assert published.status == "PUBLISHED"
        # Editing a PUBLISHED entry to a bad link is also 422,
        # and nothing changes.
        with pytest.raises(HTTPException) as excinfo:
            await update_whats_new_entry(
                entry_id=good.id,
                payload=WhatsNewEntryUpdate(link_path="https://evil.test/x"),
                db=db,
                current_user=SA,
            )
        assert excinfo.value.status_code == 422
        row = await db.get(WhatsNewEntry, good.id)
        assert row.link_path == "/tasks"
        assert row.status == "PUBLISHED"

    _run(_case, tmp_path)


def test_seed_links_are_internal_and_verified_routes(tmp_path):
    # Backend half of design §8 case 9: every seeded link is an
    # internal path on a route verified in web/src/routes/
    # dashboard.js. The live 200 check itself is QA's.
    verified = {
        "/tasks",
        "/superadmin/employee-kpi-results",
        "/superadmin/kpi",
        "/kpi-periods",
        "/superadmin/reports",
        "/superadmin/system-logs",
        "/system-settings",
    }
    assert SEED_ENTRIES, "seed list must not be empty"
    for entry in SEED_ENTRIES:
        assert entry["status"] == "PUBLISHED"
        assert entry["title"] and entry["body"]
        link = entry.get("link_path")
        if link is not None:
            assert link.startswith("/"), entry["title"]
            assert link in verified, entry["title"]


def test_teaser_latest_three_for_viewer_family(tmp_path):
    async def _case(db):
        for day in range(25, 30):
            await _seed_published(
                db, f"ALL {day}", "ALL", datetime(2026, 10, day, tzinfo=UTC)
            )
        await _seed_published(
            db, "SA သီးသန့်", "SUPER_ADMIN", datetime(2026, 10, 30, tzinfo=UTC)
        )
        emp_rows = await list_whats_new(db=db, current_user=EMP, limit=3)
        assert _titles(emp_rows) == ["ALL 29", "ALL 28", "ALL 27"]
        sa_rows = await list_whats_new(db=db, current_user=SA, limit=3)
        assert _titles(sa_rows) == ["SA သီးသန့်", "ALL 29", "ALL 28"]

    _run(_case, tmp_path)


def test_seed_is_idempotent_and_lists_newest_first(tmp_path):
    from app.modules.system.whats_new_seed import seed_whats_new

    async def _case(db):
        inserted, skipped = await seed_whats_new(db)
        assert inserted == len(SEED_ENTRIES) == 15
        assert skipped == 0
        inserted2, skipped2 = await seed_whats_new(db)
        assert inserted2 == 0
        assert skipped2 == 15
        rows = await list_whats_new(db=db, current_user=SA, limit=100)
        assert len(rows) == 15
        assert rows[0].title == SEED_ENTRIES[0]["title"]
        stamps = [_aware(row.published_at) for row in rows]
        assert stamps == sorted(stamps, reverse=True)

    _run(_case, tmp_path)


def test_deleted_and_draft_never_leak_into_manage_lookups(tmp_path):
    async def _case(db):
        draft = await _seed_draft(db)
        await delete_whats_new_entry(entry_id=draft.id, db=db, current_user=SA)
        for action in (
            lambda: publish_whats_new_entry(
                entry_id=draft.id, db=db, current_user=SA
            ),
            lambda: unpublish_whats_new_entry(
                entry_id=draft.id, db=db, current_user=SA
            ),
            lambda: delete_whats_new_entry(
                entry_id=draft.id, db=db, current_user=SA
            ),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await action()
            assert excinfo.value.status_code == 404

    _run(_case, tmp_path)
