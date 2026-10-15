"""Explicitly confirmed password recovery for the allowlisted QA administrator."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
from collections.abc import Callable

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.password_policy import get_password_policy, validate_password_strength
from app.core.security import get_password_hash
from app.database.connection import AsyncSessionLocal
from app.modules.users import password_repo
from app.modules.users.models import Role, User

logger = logging.getLogger(__name__)

CONFIRMATION_TEXT = "RESET QA PASSWORD"
QA_ACCOUNT_ALLOWLIST = {
    "qa.superadmin@example": "Super Admin",
}


class RecoveryFailure(RuntimeError):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def validate_recovery_target(target_email: str, configuration=settings) -> tuple[str, str]:
    """Fail closed unless configuration and the exact QA target are allowed."""
    if configuration.ENV.strip().lower() != "qa":
        raise RecoveryFailure("TARGET_VALIDATION_FAILED")

    try:
        database_url = make_url(configuration.DATABASE_URL)
    except (TypeError, ValueError) as exc:
        raise RecoveryFailure("TARGET_VALIDATION_FAILED") from exc

    if (
        database_url.database != "kpi_qa"
        or database_url.host != "postgres-qa"
        or database_url.port != 5432
        or configuration.QA_DATABASE_NAME != "kpi_qa"
        or configuration.QA_DATABASE_HOST != "postgres-qa"
        or configuration.QA_DATABASE_PORT != 5432
    ):
        raise RecoveryFailure("TARGET_VALIDATION_FAILED")

    normalized_email = target_email.strip().lower()
    expected_role = QA_ACCOUNT_ALLOWLIST.get(normalized_email)
    if expected_role is None:
        raise RecoveryFailure("TARGET_VALIDATION_FAILED")
    return normalized_email, expected_role


async def _load_recovery_target(db: AsyncSession, email: str, expected_role: str) -> User:
    try:
        target_row = (
            await db.execute(
                select(User, Role.name)
                .join(Role, User.role_id == Role.id)
                .where(User.email == email)
                .with_for_update()
            )
        ).first()
    except SQLAlchemyError as exc:
        raise RecoveryFailure("DATABASE_CONNECTION_FAILED") from exc
    if target_row is None:
        raise RecoveryFailure("TARGET_ACCOUNT_NOT_FOUND")
    target, actual_role = target_row
    if target.is_deleted:
        raise RecoveryFailure("TARGET_VALIDATION_FAILED")
    if actual_role != expected_role:
        raise RecoveryFailure("ROLE_VALIDATION_FAILED")
    return target


async def _validate_recovery_password(
    db: AsyncSession, target: User, new_password: str
) -> None:
    try:
        validate_password_strength(new_password, **(await get_password_policy(db)))
    except HTTPException as exc:
        raise RecoveryFailure("PASSWORD_POLICY_FAILED") from exc
    except SQLAlchemyError as exc:
        raise RecoveryFailure("DATABASE_TRANSACTION_FAILED") from exc
    except Exception as exc:
        raise RecoveryFailure("PASSWORD_POLICY_FAILED") from exc

    try:
        password_is_new = await password_repo.check_password_history(
            db, target.id, new_password
        )
    except Exception as exc:
        raise RecoveryFailure("PASSWORD_HISTORY_CHECK_FAILED") from exc
    if not password_is_new:
        raise RecoveryFailure("PASSWORD_HISTORY_CHECK_FAILED")


def _confirm_recovery(
    confirm: Callable[[], str] | None, confirmation: str | None
) -> None:
    try:
        operator_confirmation = confirm() if confirm is not None else confirmation
    except Exception as exc:
        raise RecoveryFailure("CONFIRMATION_FAILED") from exc
    if operator_confirmation != CONFIRMATION_TEXT:
        raise RecoveryFailure("CONFIRMATION_FAILED")


async def _persist_recovery(db: AsyncSession, target: User, new_password: str) -> None:
    try:
        hashed_password = get_password_hash(new_password)
        target.hashed_password = hashed_password
        target.local_password_enabled = True
        target.must_change_password = True

        await db.execute(
            text(
                "UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP "
                "WHERE user_id = :user_id AND revoked_at IS NULL"
            ),
            {"user_id": str(target.id)},
        )
        await db.execute(
            text(
                "UPDATE oauth_accounts SET revoked_at = CURRENT_TIMESTAMP "
                "WHERE user_id = :user_id AND revoked_at IS NULL"
            ),
            {"user_id": str(target.id)},
        )
        await password_repo.add_password_history(
            db, target.id, hashed_password, commit=False
        )
    except Exception as exc:
        raise RecoveryFailure("DATABASE_TRANSACTION_FAILED") from exc


async def recover_qa_password(
    target_email: str,
    new_password: str,
    confirmation: str | None,
    *,
    confirm: Callable[[], str] | None = None,
    session_factory: Callable[[], AsyncSession] = AsyncSessionLocal,
    configuration=settings,
) -> None:
    try:
        email, expected_role = validate_recovery_target(target_email, configuration)
    except RecoveryFailure:
        raise
    except Exception as exc:
        raise RecoveryFailure("TARGET_VALIDATION_FAILED") from exc
    if confirm is None and confirmation != CONFIRMATION_TEXT:
        raise RecoveryFailure("CONFIRMATION_FAILED")

    try:
        session_context = session_factory()
        async with session_context as db:
            async with db.begin():
                target = await _load_recovery_target(db, email, expected_role)
                await _validate_recovery_password(db, target, new_password)
                _confirm_recovery(confirm, confirmation)
                await _persist_recovery(db, target, new_password)
    except RecoveryFailure:
        raise
    except SQLAlchemyError as exc:
        raise RecoveryFailure("DATABASE_TRANSACTION_FAILED") from exc
    except Exception as exc:
        raise RecoveryFailure("UNEXPECTED_RECOVERY_ERROR") from exc

    logger.info("QA password recovery completed for allowlisted account %s.", email)


def _prompt_confirmation(email: str, role_name: str) -> str:
    print(
        f"Environment: QA\nDatabase: kpi_qa\nTarget: {email}\n"
        f"Role: {role_name}\nThe account password will be replaced."
    )
    return input(f'Type "{CONFIRMATION_TEXT}" to continue: ')


async def _run_interactive(target_email: str) -> None:
    try:
        email, role_name = validate_recovery_target(target_email)
    except RecoveryFailure:
        raise
    except Exception as exc:
        raise RecoveryFailure("TARGET_VALIDATION_FAILED") from exc
    password = getpass.getpass("New QA account password: ")
    confirmation_password = getpass.getpass("Re-enter new QA account password: ")
    if password != confirmation_password:
        raise RecoveryFailure("PASSWORD_POLICY_FAILED")
    await recover_qa_password(
        email,
        password,
        None,
        confirm=lambda: _prompt_confirmation(email, role_name),
    )
    password = ""
    confirmation_password = ""
    print("QA password recovery completed.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset one explicitly allowlisted account in the isolated QA database."
    )
    parser.add_argument("--email", required=True, help="Exact allowlisted QA account email.")
    args = parser.parse_args()
    try:
        asyncio.run(_run_interactive(args.email))
    except RecoveryFailure as exc:
        logger.exception("QA_RECOVERY_ERROR=%s", exc.category)
        return 1
    except Exception as exc:
        logger.exception(
            "QA_RECOVERY_ERROR=UNEXPECTED_RECOVERY_ERROR exception_type=%s",
            type(exc).__name__,
            exc_info=False,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
