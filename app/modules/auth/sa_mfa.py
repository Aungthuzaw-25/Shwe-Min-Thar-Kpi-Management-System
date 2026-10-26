"""Super Admin TOTP (Google Authenticator) MFA + peer-confirmed recovery.

Pure TOTP (RFC 6238) with the standard library only — no new
dependency. A TOTP secret is generated at SA first login, shown as
a QR code by the frontend (the backend returns the otpauth URL +
manual key; it never renders images), and confirmed by the first
verified code. Afterwards every password login for that SA also
needs the current 6-digit code, and a TOTP code alone re-enters a
logged-out or password-locked SA (audit + rate limit, never a
silent bypass).

Secrets are stored ENCRYPTED (Fernet, key from the
``TOTP_ENCRYPTION_KEY`` server env var). If the key is missing or
bad, MFA endpoints fail CLOSED (503) — they never skip MFA.
Phone loss is handled only by another SA: a RESET request that a
peer must confirm. An SA may also ask a peer to confirm turning
their own MFA off (DISABLE request), or another SA may turn an
SA's MFA off directly as an audited action. Printed backup codes
and email OTP do not exist in this flow — email is notices only.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.background_jobs.notification_jobs import send_email_notification
from app.core.config import settings
from app.database.bootstrap import SUPER_ADMIN_ROLE_NAMES
from app.models.audit_log import AuditLog
from app.modules.auth.models import (
    AuthSession,
    SAMfaRequest,
    SATotpAttempt,
    SATotpSecret,
    SATotpUsedStep,
)
from app.modules.users.models import Role, User

logger = logging.getLogger(__name__)

TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
TOTP_WINDOW_STEPS = 1  # accept previous / current / next step
MAX_TOTP_WRONG = 5
TOTP_FREEZE_MINUTES = 60
TOTP_ISSUER = "Shwe Min Thar KPI"


class MFAConfigError(Exception):
    """TOTP encryption is not configured or the key is unusable."""


def utcnow() -> datetime:
    # Naive UTC, matching every other timestamp in this codebase
    # (PG/SQLite DateTime columns store naive UTC here).
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# TOTP cryptography (stdlib only)
# ---------------------------------------------------------------------------


def _fernet() -> Fernet:
    key = getattr(settings, "TOTP_ENCRYPTION_KEY", None)
    if not key:
        raise MFAConfigError(
            "TOTP_ENCRYPTION_KEY is not set — MFA cannot be set up or verified."
        )
    try:
        return Fernet(key)
    except Exception as exc:  # malformed key value
        raise MFAConfigError("TOTP_ENCRYPTION_KEY is not a valid Fernet key.") from exc


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(secret_encrypted: str) -> str:
    try:
        return _fernet().decrypt(secret_encrypted.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise MFAConfigError("Stored TOTP secret cannot be decrypted.") from exc


def generate_secret() -> str:
    """A fresh 160-bit secret, base32 — what Authenticator scans."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def otpauth_url(secret: str, account_email: str) -> str:
    label = f"{quote(TOTP_ISSUER)}:{quote(account_email)}"
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret}&issuer={quote(TOTP_ISSUER)}"
        f"&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
    )


def _totp_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    msg = struct.pack(">Q", step)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (
        (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF)
        % (10 ** TOTP_DIGITS)
    )
    return str(code).zfill(TOTP_DIGITS)


def normalize_code(raw: str | None) -> str:
    if not raw:
        return ""
    return raw.strip().replace(" ", "").replace("-", "")


def matching_step(secret: str, raw_code: str, now: float | None = None) -> int | None:
    """The 30-second step a 6-digit code matches, or None.

    Accepts previous/current/next step (clock drift tolerance).
    """
    code = normalize_code(raw_code)
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    current = int((now if now is not None else time.time()) // TOTP_PERIOD_SECONDS)
    for step in range(current - TOTP_WINDOW_STEPS, current + TOTP_WINDOW_STEPS + 1):
        if hmac.compare_digest(_totp_at(secret, step), code):
            return step
    return None


def verify_code(secret: str, raw_code: str, now: float | None = None) -> bool:
    """Verify-only check (setup confirm, recovery): does the code
    match, with no tracking. Only the password-login path consumes
    steps (see consume_totp_step) — that is what keeps enroll ->
    immediate-login working: confirm verifies code X without
    consuming its step, so the first login with code X still passes.
    """
    return matching_step(secret, raw_code, now=now) is not None


async def consume_totp_step(db: AsyncSession, user_id: UUID, step: int) -> bool:
    """Consume a step for a login or recovery use (strict no-reuse).

    Returns False when the step was already consumed by an earlier
    login or recovery — the presented code is then rejected even
    inside its window. Steps older than the accepted window are
    pruned first, so the table stays tiny. The (user_id, step)
    unique pair fails a concurrent double-use closed: the losing
    insert hits the constraint inside a savepoint, leaving the
    outer transaction untouched. Setup confirm never consumes.
    """
    current = int(time.time() // TOTP_PERIOD_SECONDS)
    await db.execute(
        delete(SATotpUsedStep).where(
            SATotpUsedStep.user_id == user_id,
            SATotpUsedStep.step < current - TOTP_WINDOW_STEPS,
        )
    )
    try:
        async with db.begin_nested():
            db.add(SATotpUsedStep(user_id=user_id, step=step))
    except IntegrityError:
        return False
    return True


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def is_super_admin(user: User) -> bool:
    return getattr(user.role, "name", None) in SUPER_ADMIN_ROLE_NAMES


async def count_active_super_admins(db: AsyncSession, exclude_user_id: UUID | None = None) -> int:
    stmt = (
        select(func.count(User.id))
        .select_from(User)
        .join(Role, User.role_id == Role.id)
        .where(
            User.is_deleted == False,  # noqa: E712
            User.is_active == True,  # noqa: E712
            Role.name.in_(list(SUPER_ADMIN_ROLE_NAMES)),
        )
    )
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


async def get_secret_row(db: AsyncSession, user_id: UUID) -> SATotpSecret | None:
    result = await db.execute(select(SATotpSecret).where(SATotpSecret.user_id == user_id))
    return result.scalars().first()


async def confirmed_secret(db: AsyncSession, user_id: UUID) -> SATotpSecret | None:
    row = await get_secret_row(db, user_id)
    return row if row and row.is_confirmed else None


async def has_confirmed_totp(db: AsyncSession, user_id: UUID) -> bool:
    return await confirmed_secret(db, user_id) is not None


async def mfa_setup_required(db: AsyncSession, user: User) -> bool:
    """True for a Super Admin whose TOTP is not enrolled yet."""
    if not is_super_admin(user):
        return False
    return not await has_confirmed_totp(db, user.id)


async def single_sa_warning(db: AsyncSession, user: User) -> bool:
    """True when the caller is an SA and only one active SA exists."""
    if not is_super_admin(user):
        return False
    return await count_active_super_admins(db) == 1


async def other_active_sa_emails(db: AsyncSession, exclude_user_id: UUID) -> list[str]:
    stmt = (
        select(User.email)
        .join(Role, User.role_id == Role.id)
        .where(
            User.is_deleted == False,  # noqa: E712
            User.is_active == True,  # noqa: E712
            Role.name.in_(list(SUPER_ADMIN_ROLE_NAMES)),
            User.id != exclude_user_id,
        )
    )
    result = await db.execute(stmt)
    return [row for row in result.scalars().all() if row]


async def audit_mfa(
    db: AsyncSession,
    *,
    actor_id: UUID | None,
    action: str,
    target_user_id: UUID,
    details: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            user_id=actor_id,
            action=action,
            table_name="users",
            record_id=str(target_user_id),
            old_value={},
            new_value=details or {},
        )
    )


async def revoke_all_sessions(db: AsyncSession, user_id: UUID) -> int:
    """Revoke every session of an account (peer reset / disable)."""
    result = await db.execute(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    )
    count = 0
    for session in result.scalars().all():
        session.revoked_at = utcnow()
        count += 1
    return count


async def revoke_other_sessions(db: AsyncSession, user_id: UUID, keep_session_id: UUID | None = None) -> int:
    result = await db.execute(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    )
    count = 0
    for session in result.scalars().all():
        if keep_session_id is not None and session.id == keep_session_id:
            continue
        session.revoked_at = utcnow()
        count += 1
    return count


async def clear_totp_for_user(db: AsyncSession, user_id: UUID) -> None:
    """Remove an SA's TOTP secret + attempt row (reset / disable)."""
    await db.execute(delete(SATotpSecret).where(SATotpSecret.user_id == user_id))
    await db.execute(delete(SATotpAttempt).where(SATotpAttempt.user_id == user_id))


async def send_notice_best_effort(email: str, title: str, message: str) -> None:
    try:
        await send_email_notification(email, title, message)
    except Exception as exc:  # notices must never break the flow
        logger.warning("MFA notice email failed for %s: %s", email, exc)


# ---------------------------------------------------------------------------
# Freeze (5 wrong codes -> 1 hour, per account, TOTP entry only)
# ---------------------------------------------------------------------------


async def get_attempt_row(db: AsyncSession, user_id: UUID) -> SATotpAttempt | None:
    result = await db.execute(select(SATotpAttempt).where(SATotpAttempt.user_id == user_id))
    return result.scalars().first()


async def is_totp_frozen(db: AsyncSession, user_id: UUID) -> datetime | None:
    row = await get_attempt_row(db, user_id)
    if not row or not row.frozen_until:
        return None
    now = utcnow()
    frozen = row.frozen_until
    if frozen.tzinfo is not None:
        frozen = frozen.replace(tzinfo=None)
    if frozen > now:
        return frozen
    # Expired freeze: a fresh start, not a permanent lock.
    row.failed_attempts = 0
    row.frozen_until = None
    return None


async def record_wrong_totp(db: AsyncSession, user_id: UUID) -> None:
    row = await get_attempt_row(db, user_id)
    if row is None:
        row = SATotpAttempt(user_id=user_id, failed_attempts=0)
        db.add(row)
    row.failed_attempts = (row.failed_attempts or 0) + 1
    if row.failed_attempts >= MAX_TOTP_WRONG:
        row.frozen_until = utcnow() + timedelta(minutes=TOTP_FREEZE_MINUTES)


async def clear_totp_attempts(db: AsyncSession, user_id: UUID) -> None:
    row = await get_attempt_row(db, user_id)
    if row is not None:
        row.failed_attempts = 0
        row.frozen_until = None


async def pending_request(db: AsyncSession, user_id: UUID) -> SAMfaRequest | None:
    result = await db.execute(
        select(SAMfaRequest).where(
            SAMfaRequest.user_id == user_id,
            SAMfaRequest.status == "PENDING",
        )
    )
    return result.scalars().first()
