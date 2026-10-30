"""What's-new entries: public list + SA manage (Database design
Stage 3).

Public list: every authenticated role sees its family view
(status PUBLISHED, not soft-deleted, published_at DESC).
Super Admin sees ALL entries regardless of visible_to
(author view). Manage (create/publish/edit/unpublish/
soft-delete) is gated by the EXISTING permission
system.settings.edit — no new permission, no role-name
check (SA-effective in practice today).

Link rule: link_path must start with '/' (internal only).
Validated at PUBLISH and when EDITING a published entry,
so a bad link can never go live; drafts may hold any
link_path (hidden, fixable before publish). Route
existence is NOT runtime-validated (a stale link is a
content bug, checked by QA for seed entries).

Teaser data path: the public list with ?limit=3 is the
dashboard teaser (latest 3 for the viewer's family);
Frontend renders it + "See all" to the /whats-new page.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.dependencies import enforce_permission, get_current_user
from app.database.connection import get_db
from app.models.system import WhatsNewEntry, WhatsNewVisibility
from app.modules.system.router import SYSTEM_SETTINGS_EDIT_PERMISSION
from app.modules.system.whats_new_schemas import (
    WhatsNewEntryCreate,
    WhatsNewEntryResponse,
    WhatsNewEntryUpdate,
)
from app.modules.users.models import User

router = APIRouter(prefix="/system/whats-new", tags=["What's New"])

# Role names in this codebase: there is no "Employee" role —
# Department Users (L0/L1/L2) are the employee family.
_ROLE_TO_FAMILY = {
    "Admin": WhatsNewVisibility.ADMIN,
    "HR": WhatsNewVisibility.ADMIN,
    "Department User": WhatsNewVisibility.EMPLOYEE,
}


def _viewer_family(current_user: User) -> WhatsNewVisibility | None:
    """The viewer's family, or None for Super Admin (sees all)."""
    role_name = getattr(getattr(current_user, "role", None), "name", None)
    if role_name == "Super Admin":
        return None
    return _ROLE_TO_FAMILY.get(role_name)


def _validate_link_path(link_path: str | None) -> None:
    if link_path is not None and not link_path.startswith("/"):
        raise HTTPException(
            status_code=422,
            detail="link_path must be an internal path starting with '/'.",
        )


async def _load_manageable(
    db: AsyncSession, entry_id: UUID
) -> WhatsNewEntry:
    entry = (
        await db.execute(
            select(WhatsNewEntry).where(
                WhatsNewEntry.id == entry_id,
                WhatsNewEntry.is_deleted == False,  # noqa: E712
            )
        )
    ).scalars().first()
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found.")
    return entry


@router.get("/", response_model=list[WhatsNewEntryResponse])
async def list_whats_new(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=100, ge=1, le=100),
):
    """Public list: published, not deleted, viewer's family,
    newest first. Pass limit=3 for the dashboard teaser."""
    family = _viewer_family(current_user)
    stmt = select(WhatsNewEntry).where(
        WhatsNewEntry.status == "PUBLISHED",
        WhatsNewEntry.is_deleted == False,  # noqa: E712
    )
    if family is not None:
        stmt = stmt.where(
            WhatsNewEntry.visible_to.in_(["ALL", family.value])
        )
    stmt = stmt.order_by(
        WhatsNewEntry.published_at.desc(),
        WhatsNewEntry.created_at.desc(),
        WhatsNewEntry.id,
    ).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return rows


@router.post("/", response_model=WhatsNewEntryResponse)
async def create_whats_new_entry(
    payload: WhatsNewEntryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a DRAFT (hidden from all lists until published)."""
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    entry = WhatsNewEntry(
        title=payload.title,
        body=payload.body,
        how_to=payload.how_to,
        link_path=payload.link_path,
        link_label=payload.link_label,
        image_path=payload.image_path,
        visible_to=payload.visible_to.value,
        status="DRAFT",
        published_at=None,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


@router.post("/{entry_id}/publish", response_model=WhatsNewEntryResponse)
async def publish_whats_new_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Publish: stamps published_at = now (display date + order
    key) and records the publisher. Re-publish re-stamps."""
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    entry = await _load_manageable(db, entry_id)
    _validate_link_path(entry.link_path)
    entry.status = "PUBLISHED"
    entry.published_at = datetime.now(UTC)
    entry.created_by_id = current_user.id
    await db.commit()
    await db.refresh(entry)
    return entry


@router.post("/{entry_id}/unpublish", response_model=WhatsNewEntryResponse)
async def unpublish_whats_new_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Unpublish: back to DRAFT, published_at cleared."""
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    entry = await _load_manageable(db, entry_id)
    entry.status = "DRAFT"
    entry.published_at = None
    await db.commit()
    await db.refresh(entry)
    return entry


@router.put("/{entry_id}", response_model=WhatsNewEntryResponse)
async def update_whats_new_entry(
    entry_id: UUID,
    payload: WhatsNewEntryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edit draft or published. A published entry stays
    published; updated_at bumps. Editing a published entry's
    link to a non-internal path is 422 with nothing changed."""
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    entry = await _load_manageable(db, entry_id)
    changes = payload.model_dump(exclude_unset=True)
    if entry.status == "PUBLISHED" and "link_path" in changes:
        _validate_link_path(changes["link_path"])
    for field, value in changes.items():
        if field == "visible_to" and value is not None:
            value = value.value
        setattr(entry, field, value)
    await db.commit()
    await db.refresh(entry)
    return entry


@router.delete("/{entry_id}", response_model=WhatsNewEntryResponse)
async def delete_whats_new_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft-delete: excluded everywhere, no restore UI in v1."""
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    entry = await _load_manageable(db, entry_id)
    entry.is_deleted = True
    await db.commit()
    await db.refresh(entry)
    return entry
