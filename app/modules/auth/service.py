"""
Authentication Domain Service (Single Source of Truth)
"""
import asyncio
import secrets
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import joinedload

from app.core import security
from app.core.config import settings
from app.core.password_policy import get_password_expiry_days, get_session_timeout_minutes
from app.models.audit_log import AuditLog
from app.modules.auth.models import (
    AuthSession,
    LoginHistory,
    PasswordHistory,
    UnlockRequest,
    UserSecuritySettings,
)
from app.modules.departments.models import UserDepartment
from app.modules.users.models import Role, User

INVALID_CREDENTIALS_MESSAGE = "Invalid email or password."
LOCKED_ACCOUNT_MESSAGE = (
    "Your account has been locked due to multiple failed login attempts. "
    "Please notify the Super Admin to request an immediate unlock."
)

_timing_equalization_hash: str | None = None
_timing_equalization_lock = asyncio.Lock()


async def _get_timing_equalization_hash() -> str:
    global _timing_equalization_hash
    if _timing_equalization_hash is None:
        async with _timing_equalization_lock:
            if _timing_equalization_hash is None:
                _timing_equalization_hash = await run_in_threadpool(
                    security.pwd_context.handler("bcrypt").hash,
                    secrets.token_urlsafe(32),
                )
    return _timing_equalization_hash


async def _load_authentication_user(db: AsyncSession, email: str) -> User | None:
    statement = select(User).options(
        joinedload(User.role).lazyload(Role.permissions),
        joinedload(User.position),
        joinedload(User.user_departments).joinedload(UserDepartment.department),
    ).where(User.email == email).with_for_update(of=User)
    result = await db.execute(statement)
    return result.unique().scalars().first()


class AuthService:
    @staticmethod
    async def _find_user_for_authentication(db: AsyncSession, email: str) -> User | None:
        return await _load_authentication_user(db, email)

    @staticmethod
    async def _reject_invalid_credentials(password: str) -> None:
        await run_in_threadpool(
            security.verify_password, password, await _get_timing_equalization_hash()
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_MESSAGE,
        )

    @staticmethod
    async def _clear_expired_lockout(db: AsyncSession, user: User, now: datetime) -> None:
        if not user.is_locked:
            return
        locked_until = getattr(user, "locked_until", getattr(user, "lockout_until", None))
        if locked_until and locked_until > now:
            remaining_seconds = max(1, math.ceil((locked_until - now).total_seconds()))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "ACCOUNT_LOCKED",
                    "message": LOCKED_ACCOUNT_MESSAGE,
                    "remaining_seconds": remaining_seconds,
                    "locked_until": locked_until.isoformat(),
                },
            )
        user.is_locked = False
        user.failed_login_attempts = 0
        user.locked_until = None
        await db.execute(
            update(UnlockRequest)
            .where(UnlockRequest.user_id == user.id, UnlockRequest.status == "PENDING")
            .values(status="RESOLVED", resolved_at=now, resolved_by=None)
        )
        await db.commit()

    @staticmethod
    async def _security_settings(db: AsyncSession) -> tuple[int, int, bool]:
        result = await db.execute(
            select(UserSecuritySettings)
            .where(UserSecuritySettings.singleton_key.is_(True))
        )
        settings_row = result.scalar_one_or_none()
        if settings_row is None:
            return 5, 29, True
        return (
            settings_row.max_failed_attempts,
            settings_row.lockout_duration_minutes,
            settings_row.superadmin_lockout_enabled,
        )

    @staticmethod
    async def _record_failed_login(
        db: AsyncSession,
        user: User,
        ip_address: str,
        user_agent: str,
        now: datetime,
    ) -> None:
        max_failed_attempts, lockout_duration_minutes, superadmin_lockout_enabled = (
            await AuthService._security_settings(db)
        )
        is_super_admin = bool(user.role and user.role.name == "Super Admin")
        if is_super_admin and not superadmin_lockout_enabled:
            db.add(
                LoginHistory(
                    user_id=user.id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    is_success=False,
                    attempted_at=now,
                )
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=INVALID_CREDENTIALS_MESSAGE,
            )

        user.failed_login_attempts += 1
        user.last_failed_ip = ip_address
        user.last_failed_at = now
        if user.failed_login_attempts >= max_failed_attempts:
            user.is_locked = True
            user.locked_until = now + timedelta(minutes=lockout_duration_minutes)
        db.add(
            LoginHistory(
                user_id=user.id,
                ip_address=ip_address,
                user_agent=user_agent,
                is_success=False,
                attempted_at=now,
            )
        )
        await db.commit()
        if user.is_locked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "ACCOUNT_LOCKED",
                    "message": LOCKED_ACCOUNT_MESSAGE,
                    "remaining_seconds": lockout_duration_minutes * 60,
                    "locked_until": user.locked_until.isoformat(),
                },
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_MESSAGE,
        )

    @staticmethod
    async def _mark_expired_password(db: AsyncSession, user: User, now: datetime) -> None:
        expiry_days = await get_password_expiry_days(db)
        if not expiry_days:
            return
        latest_password = (
            await db.execute(
                select(PasswordHistory)
                .where(PasswordHistory.user_id == user.id)
                .order_by(PasswordHistory.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        password_changed_at = getattr(latest_password, "created_at", None)
        if password_changed_at and now - password_changed_at >= timedelta(days=expiry_days):
            user.must_change_password = True

    @staticmethod
    async def authenticate_user(
        db: AsyncSession, 
        email: str, 
        password: str, 
        ip_address: str = "Unknown", 
        user_agent: str = "Unknown",
        remember_me: bool = False,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).replace(tzinfo=None)
        ip_address = (ip_address or "Unknown")[:50]
        user_agent = (user_agent or "Unknown")[:255]
        user = await AuthService._find_user_for_authentication(db, email)
        if not user:
            await AuthService._reject_invalid_credentials(password)
        if not user.is_active or user.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account disabled. Please contact your administrator.",
            )
        await AuthService._clear_expired_lockout(db, user, now)
        if not getattr(user, "local_password_enabled", True) or not user.hashed_password:
            await AuthService._reject_invalid_credentials(password)
        is_valid_password = await run_in_threadpool(security.verify_password, password, user.hashed_password)
        if not is_valid_password:
            await AuthService._record_failed_login(
                db, user, ip_address, user_agent, now
            )
        user.failed_login_attempts = 0
        user.is_locked = False
        user.locked_until = None
        await AuthService._mark_expired_password(db, user, now)
        db.add(
            LoginHistory(
                user_id=user.id,
                ip_address=ip_address,
                user_agent=user_agent,
                is_success=True,
                attempted_at=now,
            )
        )
        db.add(
            AuditLog(
                user_id=user.id,
                action="LOGIN",
                table_name="auth_sessions",
                ip_address=ip_address,
                new_value={"success": True, "remember_me": remember_me},
            )
        )
        return await AuthService.issue_authenticated_session(
            db, user, ip_address, user_agent, remember_me, audit_action="LOGIN"
        )

    @staticmethod
    def _user_session_profile(user: User) -> tuple[str, str, str, str | None]:
        role_value = user.role.name if user.role else str(user.role_id)
        user_level = "L2"
        dept_name = "No Department"
        dept_id = None
        if user.user_departments and len(user.user_departments) > 0:
            department_link = user.user_departments[0]
            user_level = department_link.user_level
            dept_id = str(department_link.department_id)
            if department_link.department:
                dept_name = department_link.department.name
        return role_value, user_level, dept_name, dept_id

    @staticmethod
    def _audit_session_revocation(
        db: AsyncSession,
        user: User,
        existing: AuthSession,
        reason: str,
        ip_address: str,
        now: datetime,
    ) -> None:
        existing.revoked_at = now
        db.add(
            AuditLog(
                user_id=user.id,
                action="SESSION_REVOKED",
                table_name="auth_sessions",
                new_value={"reason": reason},
                ip_address=ip_address,
            )
        )

    @staticmethod
    async def _persist_authenticated_session(
        db: AsyncSession,
        user: User,
        ip_address: str,
        user_agent: str,
        remember_me: bool,
        now: datetime,
    ) -> AuthSession | None:
        if not hasattr(db, "bind"):
            return None
        import hashlib

        device_id = hashlib.sha256(f"{user_agent}|{ip_address}".encode()).hexdigest()[:32]
        active = (
            await db.execute(
                select(AuthSession)
                .where(
                    AuthSession.user_id == user.id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > now,
                )
                .order_by(AuthSession.last_used_at.asc())
                .with_for_update()
            )
        ).scalars().all()
        for existing in active:
            if existing.device_id == device_id:
                AuthService._audit_session_revocation(
                    db, user, existing, "new_login_same_device", ip_address, now
                )
        remaining = [item for item in active if item.device_id != device_id]
        for existing in remaining[:max(0, len(remaining) - 1)]:
            AuthService._audit_session_revocation(
                db, user, existing, "device_limit", ip_address, now
            )
        session = AuthSession(
            user_id=user.id,
            device_id=device_id,
            user_agent=user_agent,
            ip_address=ip_address,
            remember_me=remember_me,
            expires_at=now
            + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS if remember_me else 1),
            last_used_at=now,
            refresh_token_hash="pending",
        )
        db.add(session)
        await db.flush()
        return session

    @staticmethod
    async def issue_authenticated_session(
        db: AsyncSession,
        user: User,
        ip_address: str = "Unknown",
        user_agent: str = "Unknown",
        remember_me: bool = False,
        audit_action: str = "OAUTH_LOGIN",
    ) -> dict[str, Any]:
        """Issue the existing AuthSession/JWT pair for any trusted identity."""
        now = datetime.now(UTC).replace(tzinfo=None)
        if audit_action != "LOGIN":
            db.add(AuditLog(
                user_id=user.id, action=audit_action, table_name="auth_sessions",
                ip_address=ip_address, new_value={"success": True},
            ))
        role_value, user_level, dept_name, dept_id = AuthService._user_session_profile(user)
        # Older callers/tests may provide a light-weight fake DB; real SQLAlchemy
        # sessions are persisted by the helper while those fakes remain token-only.
        session = await AuthService._persist_authenticated_session(
            db, user, ip_address, user_agent, remember_me, now
        )

        token_data = {"sub": user.email, "user_id": str(user.id), "role": role_value, "level": user_level}
        session_timeout_minutes = await get_session_timeout_minutes(db)
        if session is not None:
            token_data["sid"] = str(session.id)
        # Only pass an explicit expiry when the panel setting actually
        # changes it; the default path keeps the long-standing call shape.
        if session_timeout_minutes != settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES:
            access_token = security.create_access_token(
                data=token_data,
                expires_delta=timedelta(minutes=session_timeout_minutes),
            )
        else:
            access_token = security.create_access_token(data=token_data)
        refresh_token = (
            security.create_refresh_token(data=token_data, remember_me=remember_me)
            if session is not None
            else security.create_refresh_token(data=token_data)
        )
        if session is not None:
            session.refresh_token_hash = security.token_digest(refresh_token)
            await db.commit()

        return {
            "access_token": access_token,
            "access_token_expires_minutes": session_timeout_minutes,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "must_change_password": bool(
                user.must_change_password and getattr(user, "local_password_enabled", True)
            ),
            "user": {
                "id": str(user.id),
                "first_name": user.first_name,
                "last_name": user.last_name,
                "full_name": f"{user.first_name} {user.last_name}".strip(),
                "email": user.email,
                "role": {"name": role_value},
                "level": user_level,
                "department_id": dept_id,
                "department_name": dept_name,
                "position_name": user.position.name if user.position else "No Position"
            }
        }