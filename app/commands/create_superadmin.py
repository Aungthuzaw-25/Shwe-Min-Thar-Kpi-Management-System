"""Explicit, one-time production Super Admin bootstrap command."""

from __future__ import annotations

import asyncio
import getpass
import logging
from typing import NoReturn

from fastapi import HTTPException
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.password_policy import validate_password_strength
from app.core.security import get_password_hash
from app.database.connection import AsyncSessionLocal
from app.modules.users.models import Role, User
from app.utils.audit import log_audit_trail

logger = logging.getLogger(__name__)

SUCCESS = 0
INVALID_INPUT = 1
USER_ALREADY_EXISTS = 2
SUPER_ADMIN_ALREADY_EXISTS = 3
ROLE_CONFIGURATION_ERROR = 4
DATABASE_ERROR = 5
CONFIGURATION_ERROR = 6
UNEXPECTED_ERROR = 7

SUPER_ADMIN_ROLE_NAMES = ("Super Admin", "System Super Admin")
BOOTSTRAP_LOCK_KEY = 731943
EMAIL_ADAPTER = TypeAdapter(EmailStr)


def _validate_input(email: str, password: str, confirmation: str) -> tuple[str, str]:
    normalized_email = email.strip().lower()
    if not normalized_email or not password:
        raise ValueError("Email and password are required.")
    try:
        normalized_email = str(EMAIL_ADAPTER.validate_python(normalized_email))
    except ValidationError as exc:
        raise ValueError("Enter a valid email address.") from exc
    if password != confirmation:
        raise ValueError("Passwords do not match.")
    try:
        validate_password_strength(password)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc
    return normalized_email, password


async def _create_initial_super_admin(
    db,
    email: str,
    password: str,
    confirmation: str,
) -> int:
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": BOOTSTRAP_LOCK_KEY})

    role_result = await db.execute(select(Role).where(Role.name.in_(SUPER_ADMIN_ROLE_NAMES)))
    roles = role_result.scalars().all()
    if not roles:
        return ROLE_CONFIGURATION_ERROR
    role = next((item for item in roles if item.name == "Super Admin"), roles[0])

    super_admin_result = await db.execute(
        select(func.count(User.id))
        .join(Role, User.role_id == Role.id)
        .where(Role.name.in_(SUPER_ADMIN_ROLE_NAMES))
    )
    if (super_admin_result.scalar_one() or 0) > 0:
        return SUPER_ADMIN_ALREADY_EXISTS

    normalized_email, validated_password = _validate_input(email, password, confirmation)
    existing_result = await db.execute(select(User.id).where(User.email == normalized_email))
    if existing_result.scalar_one_or_none() is not None:
        return USER_ALREADY_EXISTS

    sa_user = User(
        email=normalized_email,
        first_name="System",
        last_name="Super Admin",
        hashed_password=get_password_hash(validated_password),
        role_id=role.id,
        is_active=True,
        must_change_password=True,
    )
    db.add(sa_user)
    await db.flush()
    audit_entry = await log_audit_trail(
        db,
        None,
        "CREATE_SUPER_ADMIN",
        "users",
        str(sa_user.id),
        None,
        {"email": normalized_email, "source": "initial_bootstrap"},
        commit=False,
    )
    if audit_entry is None:
        raise SQLAlchemyError("Unable to record the bootstrap audit entry.")
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return USER_ALREADY_EXISTS
    return SUCCESS


async def _bootstrap_status() -> int | None:
    """Check the database before prompting for credentials."""
    async with AsyncSessionLocal() as db:
        try:
            bind = db.get_bind()
            if bind.dialect.name == "postgresql":
                await db.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": BOOTSTRAP_LOCK_KEY},
                )
            result = await db.execute(
                select(func.count(User.id))
                .join(Role, User.role_id == Role.id)
                .where(Role.name.in_(SUPER_ADMIN_ROLE_NAMES))
            )
            return SUPER_ADMIN_ALREADY_EXISTS if (result.scalar_one() or 0) > 0 else None
        except SQLAlchemyError:
            await db.rollback()
            logger.exception("Unable to check initial Super Admin provisioning status.")
            return DATABASE_ERROR


async def run_bootstrap(email: str, password: str, confirmation: str) -> int:
    async with AsyncSessionLocal() as db:
        try:
            result = await _create_initial_super_admin(db, email, password, confirmation)
            if result != SUCCESS:
                await db.rollback()
            return result
        except ValueError:
            await db.rollback()
            return INVALID_INPUT
        except SQLAlchemyError:
            await db.rollback()
            logger.exception("Initial Super Admin provisioning failed due to a database error.")
            return DATABASE_ERROR
        except Exception:
            await db.rollback()
            logger.exception("Initial Super Admin provisioning failed unexpectedly.")
            return UNEXPECTED_ERROR


def _prompt_nonempty(prompt: str) -> str:
    value = input(prompt).strip()
    if not value:
        raise ValueError("Email and password are required.")
    return value


def main() -> NoReturn:
    try:
        status = asyncio.run(_bootstrap_status())
        if status == SUPER_ADMIN_ALREADY_EXISTS:
            print("Super Admin already exists. No changes were made.")
            raise SystemExit(SUPER_ADMIN_ALREADY_EXISTS)
        if status == DATABASE_ERROR:
            print("Database error. No changes were made.")
            raise SystemExit(DATABASE_ERROR)
        email = _prompt_nonempty("Super Admin email: ")
        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Confirm password: ")
        exit_code = asyncio.run(run_bootstrap(email, password, confirmation))
    except (EOFError, KeyboardInterrupt):
        print("\nProvisioning cancelled.")
        raise SystemExit(INVALID_INPUT)
    except ValueError as exc:
        print(str(exc))
        raise SystemExit(INVALID_INPUT)
    except Exception:
        logger.exception("Unable to start initial Super Admin provisioning.")
        print("Unable to provision Super Admin.")
        raise SystemExit(UNEXPECTED_ERROR)

    messages = {
        SUCCESS: "Super Admin created successfully.",
        USER_ALREADY_EXISTS: "A user with that email already exists. No changes were made.",
        SUPER_ADMIN_ALREADY_EXISTS: "Super Admin already exists. No changes were made.",
        ROLE_CONFIGURATION_ERROR: "Super Admin role is not configured.",
        DATABASE_ERROR: "Database error. No changes were made.",
        INVALID_INPUT: "Invalid input. No changes were made.",
        UNEXPECTED_ERROR: "Unexpected provisioning error. No changes were made.",
    }
    print(messages.get(exit_code, "Provisioning failed. No changes were made."))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()