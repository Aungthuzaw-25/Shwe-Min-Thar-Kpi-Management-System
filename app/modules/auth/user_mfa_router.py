"""Optional TOTP (Google Authenticator) MFA for Admin and HR users.

Mounted under ``/api/v1/auth`` as ``/user-mfa/*``:

- ``POST /user-mfa/setup`` (authed Admin/HR): start enrollment —
  returns the otpauth URL (the frontend renders the QR) + manual key.
- ``POST /user-mfa/confirm`` (authed Admin/HR): prove the first code.
- ``GET /user-mfa/status`` (authed Admin/HR): enrolled / setup required.
- ``POST /user-mfa/disable`` (authed Admin/HR, self): turn my own MFA
  off — requires the current authenticator code (possession proof).
- ``POST /user-mfa/reset-requests`` (public): authenticator-lost
  request — an SA approves it in the SA-only request flow, exactly
  like an SA reset (peer confirm, never self-approved).

This reuses the SA TOTP machinery in ``sa_mfa`` (same Fernet key,
same verification, same 5-wrong -> 1-hour freeze, same tables) — it
is opt-in only: unenrolled Admin/HR users log in exactly as today,
and an enrolled user is asked for the code on every password login,
exactly like an enrolled SA. The SA-only gates are untouched: only
SAs list/approve requests, only SAs disable another account's MFA,
and the SA recovery endpoints refuse non-SA callers.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import RoleChecker
from app.database.connection import get_db
from app.modules.auth import sa_mfa
from app.modules.auth.router import limiter
from app.modules.auth.service import AuthService
from app.modules.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter()
admin_hr_checker = RoleChecker(["Admin", "HR"])

GENERIC_REQUEST_RECEIVED = "Request received. Another Super Admin will review it."


# ---------------------------------------------------------------------------
# Enrollment (opt-in, Profile Security area)
# ---------------------------------------------------------------------------


@router.post("/user-mfa/setup")
async def setup_totp(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_hr_checker),
):
    """Start (or restart an unconfirmed) TOTP enrollment for this user."""
    try:
        if await sa_mfa.has_confirmed_totp(db, current_user.id):
            raise HTTPException(status_code=400, detail="MFA is already enrolled.")
        secret = sa_mfa.generate_secret()
        encrypted = sa_mfa.encrypt_secret(secret)
    except sa_mfa.MFAConfigError:
        raise HTTPException(
            status_code=503, detail="Authenticator setup is not available right now."
        ) from None
    row = await sa_mfa.get_secret_row(db, current_user.id)
    if row is None:
        row = sa_mfa.SATotpSecret(
            user_id=current_user.id,
            secret_encrypted=encrypted,
            is_confirmed=False,
        )
        db.add(row)
    else:
        row.secret_encrypted = encrypted
        row.is_confirmed = False
        row.confirmed_at = None
    await db.commit()
    return {
        "otpauth_url": sa_mfa.otpauth_url(secret, current_user.email),
        "manual_key": secret,
        "message": "Scan the QR code with Google Authenticator, then confirm with the current code.",
    }


@router.post("/user-mfa/confirm")
async def confirm_totp(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_hr_checker),
):
    """Prove the first code — only then is MFA actually on."""
    row = await sa_mfa.get_secret_row(db, current_user.id)
    if row is None or row.is_confirmed:
        raise HTTPException(
            status_code=400,
            detail="Run setup first." if row is None else "MFA is already enrolled.",
        )
    # Freeze (same as login): 5 wrong codes freeze TOTP verification
    # for 1 hour for this account only. A frozen code fails with the
    # same generic error as a wrong code — the caller learns nothing.
    if await sa_mfa.is_totp_frozen(db, current_user.id) is not None:
        raise HTTPException(status_code=400, detail="Invalid authenticator code.")
    try:
        secret = sa_mfa.decrypt_secret(row.secret_encrypted)
        code_ok = sa_mfa.verify_code(secret, payload.get("code"))
    except sa_mfa.MFAConfigError:
        raise HTTPException(
            status_code=503, detail="Authenticator verification is not available right now."
        ) from None
    if not code_ok:
        await sa_mfa.record_wrong_totp(db, current_user.id)
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid authenticator code.")
    await sa_mfa.clear_totp_attempts(db, current_user.id)
    row.is_confirmed = True
    row.confirmed_at = sa_mfa.utcnow()
    await sa_mfa.audit_mfa(
        db,
        actor_id=current_user.id,
        action="MFA_ENABLED",
        target_user_id=current_user.id,
        details={},
    )
    await db.commit()
    await sa_mfa.send_notice_best_effort(
        current_user.email,
        "Authenticator MFA enrolled",
        "Authenticator MFA is now on for your account.",
    )
    return {"message": "Authenticator MFA is now on."}


@router.get("/user-mfa/status")
async def totp_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_hr_checker),
):
    enrolled = await sa_mfa.has_confirmed_totp(db, current_user.id)
    pending = await sa_mfa.pending_request(db, current_user.id)
    return {
        "enrolled": enrolled,
        "setup_required": not enrolled,
        "pending_request_kind": pending.kind if pending else None,
    }


@router.post("/user-mfa/disable")
async def disable_own_totp(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_hr_checker),
):
    """Turn my own MFA off — requires the current authenticator code.

    This is the guard that replaces peer-confirm for optional MFA:
    the caller is already logged in (password + code at login), and
    the code proves they still hold the authenticator. A stolen
    password alone cannot reach this endpoint (login would demand
    the code), and a stolen session alone cannot pass it (no code).
    All sessions are revoked, so the caller signs in again
    password-only after this call.
    """
    row = await sa_mfa.get_secret_row(db, current_user.id)
    if row is None or not row.is_confirmed:
        raise HTTPException(status_code=400, detail="MFA is not enrolled.")
    # Freeze (same as login): 5 wrong codes freeze TOTP verification
    # for 1 hour for this account only. A frozen code fails with the
    # same generic error as a wrong code — the caller learns nothing.
    if await sa_mfa.is_totp_frozen(db, current_user.id) is not None:
        raise HTTPException(status_code=400, detail="Invalid authenticator code.")
    try:
        secret = sa_mfa.decrypt_secret(row.secret_encrypted)
        code_ok = sa_mfa.verify_code(secret, payload.get("code"))
    except sa_mfa.MFAConfigError:
        raise HTTPException(
            status_code=503, detail="Authenticator verification is not available right now."
        ) from None
    if not code_ok:
        await sa_mfa.record_wrong_totp(db, current_user.id)
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid authenticator code.")
    await sa_mfa.clear_totp_attempts(db, current_user.id)
    await sa_mfa.clear_totp_for_user(db, current_user.id)
    revoked = await sa_mfa.revoke_all_sessions(db, current_user.id)
    await sa_mfa.audit_mfa(
        db,
        actor_id=current_user.id,
        action="MFA_DISABLED",
        target_user_id=current_user.id,
        details={"via": "self-disable-with-code", "sessions_revoked": revoked},
    )
    await db.commit()
    await sa_mfa.send_notice_best_effort(
        current_user.email,
        "Authenticator MFA turned off",
        "Your authenticator MFA was turned off. Sign in with your password "
        "and set up the authenticator again if you want it back.",
    )
    return {"message": "Authenticator MFA is now off. Sign in with your password."}


# ---------------------------------------------------------------------------
# Recovery: authenticator-lost request (an SA approves, as for SAs)
# ---------------------------------------------------------------------------


@router.post("/user-mfa/reset-requests")
@limiter.limit("5/minute")
async def request_mfa_reset(
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Public authenticator-lost request for an enrolled Admin/HR user.

    Always the same generic reply — whether the email is an enrolled
    Admin/HR user, already has a pending request, or does not exist
    at all, the caller cannot tell. An SA reviews the pending
    request in the SA-only flow (GET /sa-mfa/requests,
    POST /sa-mfa/requests/{id}/approve) — never self-approved, since
    the approver is always an SA and the target is never one.
    """
    email = (payload.get("email") or "").strip().lower()
    user = await AuthService._find_user_for_authentication(db, email)
    if (
        user
        and user.is_active
        and not user.is_deleted
        and sa_mfa.is_admin_or_hr(user)
        and await sa_mfa.has_confirmed_totp(db, user.id)
        and await sa_mfa.pending_request(db, user.id) is None
    ):
        db.add(sa_mfa.SAMfaRequest(user_id=user.id, kind="RESET", status="PENDING"))
        await sa_mfa.audit_mfa(
            db,
            actor_id=user.id,
            action="SA_MFA_RESET_REQUESTED",
            target_user_id=user.id,
            details={},
        )
        await db.commit()
        for peer_email in await sa_mfa.other_active_sa_emails(db, user.id):
            await sa_mfa.send_notice_best_effort(
                peer_email,
                "Authenticator reset requested",
                f"An Admin/HR user ({user.email}) requested an authenticator reset. "
                "Review the pending request before confirming.",
            )
    return {"message": GENERIC_REQUEST_RECEIVED}
