"""Super Admin TOTP (Google Authenticator) MFA endpoints.

Mounted under ``/api/v1/auth`` as ``/sa-mfa/*``:

- ``POST /sa-mfa/setup`` (authed SA): start enrollment — returns
  the otpauth URL (the frontend renders the QR) + manual key.
- ``POST /sa-mfa/confirm`` (authed SA): prove the first code.
- ``GET /sa-mfa/status`` (authed SA): enrolled / setup required.
- ``POST /sa-mfa/recover`` (public): TOTP re-entry for a logged-out
  or password-locked SA — one generic failure message for every
  failure, so nothing about the account leaks.
- ``POST /sa-mfa/reset-password`` (via_recovery session only):
  set a new password once, without the old password.
- ``POST /sa-mfa/reset-requests`` (public): phone-lost request.
- ``POST /sa-mfa/disable-requests`` (authed SA, self): ask a peer
  to confirm turning my MFA off.
- ``GET /sa-mfa/requests`` (SA-only): pending requests.
- ``POST /sa-mfa/requests/{id}/approve|reject`` (SA-only, never
  self): peer confirmation.
- ``POST /sa-mfa/users/{id}/disable`` (SA-only, another SA):
  turn another SA's MFA off directly (audited).

No printed backup codes, no email OTP anywhere here — email is
notices only. MFA config problems fail closed (503).
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core import security
from app.core.dependencies import RoleChecker, get_current_user
from app.core.password_policy import get_password_policy, validate_password_strength
from app.database.connection import get_db
from app.modules.auth import sa_mfa
from app.modules.auth.models import AuthSession, SAMfaRequest
from app.modules.auth.router import limiter
from app.modules.auth.service import AuthService
from app.modules.departments.models import UserDepartment
from app.modules.users import password_repo
from app.modules.users.models import Role, User

logger = logging.getLogger(__name__)

router = APIRouter()
super_admin_checker = RoleChecker(["Super Admin"])

GENERIC_RECOVERY_FAILURE = "Invalid email or authenticator code."
GENERIC_REQUEST_RECEIVED = "Request received. Another Super Admin will review it."


async def _load_sa_with_role(db: AsyncSession, user_id: UUID) -> User | None:
    result = await db.execute(
        select(User)
        .options(joinedload(User.role))
        .where(User.id == user_id, User.is_deleted == False)  # noqa: E712
    )
    return result.scalars().first()


async def _require_sa(db: AsyncSession, user_id: UUID) -> User:
    user = await _load_sa_with_role(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Super Admin not found.")
    if not sa_mfa.is_super_admin(user):
        raise HTTPException(status_code=403, detail="Not a Super Admin account.")
    return user


async def _resolve_request(db: AsyncSession, request_id: UUID) -> SAMfaRequest:
    result = await db.execute(select(SAMfaRequest).where(SAMfaRequest.id == request_id))
    req = result.scalars().first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    if req.status != "PENDING":
        raise HTTPException(status_code=400, detail="Request already resolved.")
    return req


async def _clear_mfa_and_notify(
    db: AsyncSession,
    target: User,
    *,
    audit_action: str,
    via: str,
    actor: User,
    confirmed_by: UUID | None = None,
) -> int:
    """Clear MFA + revoke ALL target sessions + audit + notice.

    The actor IS the confirmer on the peer-confirm path (the target
    can never approve their own request); confirmed_by records that
    explicitly in the audit details.
    """
    await sa_mfa.clear_totp_for_user(db, target.id)
    revoked = await sa_mfa.revoke_all_sessions(db, target.id)
    details: dict[str, object] = {"via": via, "sessions_revoked": revoked}
    if confirmed_by is not None:
        details["confirmed_by"] = str(confirmed_by)
    await sa_mfa.audit_mfa(
        db,
        actor_id=actor.id,
        action=audit_action,
        target_user_id=target.id,
        details=details,
    )
    await sa_mfa.send_notice_best_effort(
        target.email,
        "Authenticator MFA changed",
        "Your authenticator MFA was turned off. Sign in with your password "
        "and set up the authenticator again at your next login.",
    )
    return revoked


# ---------------------------------------------------------------------------
# Enrollment (first login)
# ---------------------------------------------------------------------------


@router.post("/sa-mfa/setup")
async def setup_totp(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(super_admin_checker),
):
    """Start (or restart an unconfirmed) TOTP enrollment for this SA."""
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


@router.post("/sa-mfa/confirm")
async def confirm_totp(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(super_admin_checker),
):
    """Prove the first code — only then is MFA actually on."""
    row = await sa_mfa.get_secret_row(db, current_user.id)
    if row is None or row.is_confirmed:
        raise HTTPException(
            status_code=400,
            detail="Run setup first." if row is None else "MFA is already enrolled.",
        )
    try:
        secret = sa_mfa.decrypt_secret(row.secret_encrypted)
        code_ok = sa_mfa.verify_code(secret, payload.get("code"))
    except sa_mfa.MFAConfigError:
        raise HTTPException(
            status_code=503, detail="Authenticator verification is not available right now."
        ) from None
    if not code_ok:
        raise HTTPException(status_code=400, detail="Invalid authenticator code.")
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
        "Authenticator MFA is now on for your Super Admin account.",
    )
    return {"message": "Authenticator MFA is now on."}


@router.get("/sa-mfa/status")
async def totp_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(super_admin_checker),
):
    enrolled = await sa_mfa.has_confirmed_totp(db, current_user.id)
    pending = await sa_mfa.pending_request(db, current_user.id)
    return {
        "enrolled": enrolled,
        "setup_required": not enrolled,
        "pending_request_kind": pending.kind if pending else None,
    }


# ---------------------------------------------------------------------------
# Recovery: TOTP re-entry (logged out / password-locked)
# ---------------------------------------------------------------------------


@router.post("/sa-mfa/recover")
@limiter.limit("5/minute")
async def recover_with_totp(
    request: Request,
    response: Response,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Re-enter with email + current authenticator code.

    One generic failure for every failure case (unknown email,
    non-SA, inactive, no MFA, frozen, wrong code, replayed code) —
    the caller learns nothing about the account. A successful
    recovery consumes its step (same mechanism as login), so the
    code cannot be replayed for a login or another recovery inside
    its window; a replay fails like any other failure and does not
    feed the freeze counter. On success: password lockout is
    cleared, all other sessions are revoked, and the new session
    is marked via_recovery (one password reset, no old password,
    then the flag clears).
    """
    email = (payload.get("email") or "").strip().lower()
    code = payload.get("code") or ""
    client_ip = request.client.host if request.client else "Unknown"
    user_agent = request.headers.get("user-agent", "Unknown")[:255]
    failure = HTTPException(status_code=401, detail=GENERIC_RECOVERY_FAILURE)

    user = await AuthService._find_user_for_authentication(db, email)
    if (
        not user
        or not user.is_active
        or user.is_deleted
        or not sa_mfa.is_super_admin(user)
    ):
        raise failure
    secret_row = await sa_mfa.confirmed_secret(db, user.id)
    if secret_row is None:
        raise failure
    try:
        if await sa_mfa.is_totp_frozen(db, user.id) is not None:
            raise failure
        secret = sa_mfa.decrypt_secret(secret_row.secret_encrypted)
        step = sa_mfa.matching_step(secret, code)
    except sa_mfa.MFAConfigError:
        raise HTTPException(
            status_code=503, detail="Authenticator verification is not available right now."
        ) from None
    if step is None:
        await sa_mfa.record_wrong_totp(db, user.id)
        await db.commit()
        raise failure
    # Strict no-reuse, same as login: a consumed step fails with
    # the same generic failure and does not feed the freeze counter.
    if not await sa_mfa.consume_totp_step(db, user.id, step):
        raise failure

    was_locked = bool(user.is_locked)
    await sa_mfa.clear_totp_attempts(db, user.id)
    user.failed_login_attempts = 0
    user.is_locked = False
    user.locked_until = None
    user.must_change_password = True
    revoked = await sa_mfa.revoke_other_sessions(db, user.id)
    await sa_mfa.audit_mfa(
        db,
        actor_id=user.id,
        action="SA_SESSIONS_REVOKED_ON_RECOVERY",
        target_user_id=user.id,
        details={"sessions_revoked": revoked},
    )
    await db.commit()
    auth_result = await AuthService.issue_authenticated_session(
        db, user, client_ip, user_agent, False, audit_action="LOGIN", via_recovery=True
    )
    await sa_mfa.audit_mfa(
        db,
        actor_id=user.id,
        action="SA_RECOVERY_LOGIN",
        target_user_id=user.id,
        details={"path": "totp", "lockout_bypassed": was_locked},
    )
    await db.commit()
    response.set_cookie(
        key="access_token", value=auth_result["access_token"], httponly=True, samesite="lax"
    )
    response.set_cookie(
        key="refresh_token", value=auth_result["refresh_token"], httponly=True, samesite="lax"
    )
    await sa_mfa.send_notice_best_effort(
        user.email,
        "Super Admin account recovered with authenticator",
        "Your Super Admin account was recovered with an authenticator code. "
        "All other sessions were signed out.",
    )
    return auth_result


@router.post("/sa-mfa/reset-password")
async def reset_password_via_recovery(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One-time password reset for a via_recovery session only.

    The session flag clears on use, so this cannot be replayed.
    No old password is needed — the TOTP recovery already proved
    identity — but strength + last-5 history rules still apply.
    """
    result = await db.execute(
        select(AuthSession).where(
            AuthSession.user_id == current_user.id,
            AuthSession.via_recovery == True,  # noqa: E712
            AuthSession.revoked_at.is_(None),
        )
    )
    recovery_session = result.scalars().first()
    if recovery_session is None:
        raise HTTPException(
            status_code=403, detail="This reset is only available after an authenticator recovery login."
        )
    new_password = payload.get("new_password") or ""
    if payload.get("confirm_new_password") is not None and payload["confirm_new_password"] != new_password:
        raise HTTPException(status_code=400, detail="New password confirmation does not match.")
    validate_password_strength(new_password, **(await get_password_policy(db)))
    if not await password_repo.check_password_history(db, current_user.id, new_password):
        raise HTTPException(
            status_code=400, detail="You cannot reuse any of your last 5 passwords. Please choose a new password."
        )
    hashed = await run_in_threadpool(security.get_password_hash, new_password)
    current_user.hashed_password = hashed
    current_user.local_password_enabled = True
    current_user.must_change_password = False
    current_user.password_changed_at = datetime.now(UTC).replace(tzinfo=None)
    recovery_session.via_recovery = False
    await password_repo.add_password_history(db, current_user.id, hashed, commit=False)
    revoked = await sa_mfa.revoke_other_sessions(db, current_user.id, keep_session_id=recovery_session.id)
    await sa_mfa.audit_mfa(
        db,
        actor_id=current_user.id,
        action="PASSWORD_CHANGED",
        target_user_id=current_user.id,
        details={"via_recovery": True, "sessions_revoked": revoked},
    )
    await db.commit()
    return {"message": "Password reset. Other sessions were signed out."}


# ---------------------------------------------------------------------------
# Peer-confirmed requests (RESET = phone lost, DISABLE = turn MFA off)
# ---------------------------------------------------------------------------


@router.post("/sa-mfa/reset-requests")
@limiter.limit("5/minute")
async def request_mfa_reset(
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Public phone-lost request. Always the same generic reply —
    whether the email is an SA, already has a pending request, or
    does not exist at all, the caller cannot tell."""
    email = (payload.get("email") or "").strip().lower()
    user = await AuthService._find_user_for_authentication(db, email)
    if (
        user
        and user.is_active
        and not user.is_deleted
        and sa_mfa.is_super_admin(user)
        and await sa_mfa.has_confirmed_totp(db, user.id)
        and await sa_mfa.pending_request(db, user.id) is None
    ):
        db.add(SAMfaRequest(user_id=user.id, kind="RESET", status="PENDING"))
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
                f"A Super Admin ({user.email}) requested an authenticator reset. "
                "Review the pending request before confirming.",
            )
    return {"message": GENERIC_REQUEST_RECEIVED}


@router.post("/sa-mfa/disable-requests")
async def request_own_mfa_disable(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(super_admin_checker),
):
    """An SA asks a peer to confirm turning their own MFA off.

    Own-MFA disable is never self-approved — same rule as reset.
    (To turn ANOTHER SA's MFA off directly, use
    POST /sa-mfa/users/{id}/disable instead.)
    """
    if not await sa_mfa.has_confirmed_totp(db, current_user.id):
        raise HTTPException(status_code=400, detail="MFA is not enrolled.")
    if await sa_mfa.pending_request(db, current_user.id) is not None:
        raise HTTPException(status_code=400, detail="A request is already pending.")
    db.add(SAMfaRequest(user_id=current_user.id, kind="DISABLE", status="PENDING"))
    await sa_mfa.audit_mfa(
        db,
        actor_id=current_user.id,
        action="SA_MFA_DISABLE_REQUESTED",
        target_user_id=current_user.id,
        details={},
    )
    await db.commit()
    return {"message": GENERIC_REQUEST_RECEIVED}


@router.get("/sa-mfa/requests")
async def list_mfa_requests(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(super_admin_checker),
):
    result = await db.execute(
        select(SAMfaRequest).where(SAMfaRequest.status == "PENDING")
    )
    rows = []
    for req in result.scalars().all():
        target = await _load_sa_with_role(db, req.user_id)
        rows.append(
            {
                "id": str(req.id),
                "user_id": str(req.user_id),
                "email": target.email if target else None,
                "kind": req.kind,
                "status": req.status,
                "requested_at": req.created_at.isoformat() if req.created_at else None,
            }
        )
    return rows


@router.post("/sa-mfa/requests/{request_id}/approve")
async def approve_mfa_request(
    request_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(super_admin_checker),
):
    """Peer confirmation. The target can never approve their own
    request — that is the whole point of the peer rule."""
    req = await _resolve_request(db, request_id)
    if req.user_id == current_user.id:
        raise HTTPException(
            status_code=403, detail="You cannot confirm your own MFA request. Another Super Admin must confirm it."
        )
    target = await _require_sa(db, req.user_id)
    req.status = "APPROVED"
    req.resolved_at = sa_mfa.utcnow()
    req.resolved_by = current_user.id
    audit_action = (
        "SA_MFA_RESET_CONFIRMED" if req.kind == "RESET" else "MFA_DISABLED"
    )
    await _clear_mfa_and_notify(
        db,
        target,
        audit_action=audit_action,
        via="peer-confirm",
        actor=current_user,
        confirmed_by=current_user.id,
    )
    await db.commit()
    return {"message": "MFA turned off. The account signs in with password only until it enrolls again."}


@router.post("/sa-mfa/requests/{request_id}/reject")
async def reject_mfa_request(
    request_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(super_admin_checker),
):
    req = await _resolve_request(db, request_id)
    if req.user_id == current_user.id:
        raise HTTPException(
            status_code=403, detail="You cannot resolve your own MFA request. Another Super Admin must review it."
        )
    await _require_sa(db, req.user_id)
    req.status = "REJECTED"
    req.resolved_at = sa_mfa.utcnow()
    req.resolved_by = current_user.id
    await sa_mfa.audit_mfa(
        db,
        actor_id=current_user.id,
        action="SA_MFA_REQUEST_REJECTED",
        target_user_id=req.user_id,
        details={"kind": req.kind},
    )
    await db.commit()
    return {"message": "Request rejected. MFA is unchanged."}


@router.post("/sa-mfa/users/{user_id}/disable")
async def disable_other_sa_mfa(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(super_admin_checker),
):
    """Turn ANOTHER SA's MFA off directly — an audited SA action.

    Own MFA can never be disabled this way; use a disable-request
    (peer confirm) for that.
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=403, detail="Use a disable request for your own MFA — a peer must confirm it."
        )
    target = await _require_sa(db, user_id)
    if not await sa_mfa.has_confirmed_totp(db, target.id):
        raise HTTPException(status_code=400, detail="MFA is not enrolled for this account.")
    await _clear_mfa_and_notify(
        db, target, audit_action="MFA_DISABLED", via="direct", actor=current_user
    )
    await db.commit()
    return {"message": "MFA turned off for that account. It signs in with password only until it enrolls again."}
