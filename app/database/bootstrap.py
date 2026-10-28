"""Platform-independent initial Super Admin bootstrap."""

import logging
import os
from collections.abc import Callable

from fastapi import HTTPException
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.password_policy import validate_password_strength
from app.core.security import get_password_hash_async
from app.database.connection import AsyncSessionLocal
from app.database.seed import ensure_roles_and_permissions
from app.models.audit_log import AuditLog
from app.modules.users.models import Role, User

logger = logging.getLogger(__name__)

SUPER_ADMIN_ROLE_NAMES = ("Super Admin",)
BOOTSTRAP_ADVISORY_LOCK_KEY = 731943
email_adapter = TypeAdapter(EmailStr)


def _configured_bootstrap_values() -> tuple[str, str]:
    """BOOTSTRAP_* only. The old SEED_* fallback is removed on
    purpose: there is exactly one SA-creation path, and legacy
    SEED credentials must never silently create a Super Admin."""
    email = (os.getenv("BOOTSTRAP_SUPERADMIN_EMAIL")
             or getattr(settings, "BOOTSTRAP_SUPERADMIN_EMAIL", None) or "").strip()
    password = (os.getenv("BOOTSTRAP_SUPERADMIN_PASSWORD")
                or getattr(settings, "BOOTSTRAP_SUPERADMIN_PASSWORD", None) or "").strip()
    return email, password


def _validate_bootstrap_configuration(email: str, password: str) -> tuple[str, str]:
    if not email or not password:
        raise RuntimeError(
            "Initial Super Admin bootstrap configuration is incomplete. "
            "Set BOOTSTRAP_SUPERADMIN_EMAIL and BOOTSTRAP_SUPERADMIN_PASSWORD."
        )
    try:
        normalized_email = str(email_adapter.validate_python(email)).lower()
    except ValidationError as exc:
        raise RuntimeError("Initial Super Admin bootstrap email is invalid.") from exc
    try:
        validate_password_strength(password)
    except HTTPException as exc:
        raise RuntimeError(
            f"Initial Super Admin bootstrap password is invalid: {exc.detail}"
        ) from exc
    return normalized_email, password


async def _ensure_initial_super_admin(
    db: AsyncSession,
    email: str,
    password: str,
) -> bool:
    role_result = await db.execute(
        select(Role).where(Role.name.in_(SUPER_ADMIN_ROLE_NAMES))
    )
    super_admin_role = role_result.scalars().first()
    if super_admin_role is None:
        roles = await ensure_roles_and_permissions(db)
        super_admin_role = roles["Super Admin"]

    existing_result = await db.execute(
        select(User.id)
        .join(Role, User.role_id == Role.id)
        .where(
            Role.name.in_(SUPER_ADMIN_ROLE_NAMES),
        )
        .limit(1)
    )
    if existing_result.scalar_one_or_none() is not None:
        logger.info(
            "Initial Super Admin bootstrap skipped because a Super Admin already exists."
        )
        return False

    email_result = await db.execute(select(User.id).where(User.email == email))
    if email_result.scalar_one_or_none() is not None:
        raise RuntimeError(
            "Initial Super Admin bootstrap cannot use the configured email "
            "because an account with that email already exists."
        )

    user = User(
        email=email,
        first_name="Super",
        last_name="Admin",
        hashed_password=await get_password_hash_async(password),
        local_password_enabled=True,
        role_id=super_admin_role.id,
        must_change_password=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        AuditLog(
            user_id=None,
            action="BOOTSTRAP_SUPER_ADMIN",
            table_name="users",
            record_id=str(user.id),
            new_value={"email": email},
            ip_address="system",
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise RuntimeError(
            "Initial Super Admin bootstrap could not create the account because "
            "the configured email is already in use."
        ) from exc
    logger.info("Initial Super Admin account created.")
    return True


async def bootstrap_initial_super_admin(
    session_factory: Callable[[], AsyncSession] = AsyncSessionLocal,
) -> bool:
    """Ensure core RBAC and the configured Super Admin under a DB lock."""
    configured_email, configured_password = _configured_bootstrap_values()
    if not configured_email and not configured_password:
        logger.info(
            "Initial Super Admin bootstrap is not configured; continuing without account creation."
        )
        return False

    async with session_factory() as db:
        is_postgresql = db.get_bind().dialect.name == "postgresql"
        if is_postgresql:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": BOOTSTRAP_ADVISORY_LOCK_KEY},
            )

        email, password = _validate_bootstrap_configuration(
            configured_email,
            configured_password,
        )
        await ensure_roles_and_permissions(db)
        created = await _ensure_initial_super_admin(db, email, password)
        await db.commit()
        return created
