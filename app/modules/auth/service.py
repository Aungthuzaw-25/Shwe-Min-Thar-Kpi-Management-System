"""
Authentication Domain Service (Single Source of Truth)
"""
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core import security
from app.core.config import settings
from app.core.password_policy import get_password_expiry_days
from app.models.audit_log import AuditLog
from app.modules.auth.models import AuthSession, LoginHistory, PasswordHistory
from app.modules.departments.models import UserDepartment
from app.modules.users.models import User

INVALID_CREDENTIALS_MESSAGE = "Invalid email or password."

# Generate one random bcrypt hash per process for timing equalization on
# unknown and passwordless accounts. Never use a reusable credential hash.
TIMING_EQUALIZATION_HASH = security.pwd_context.handler("bcrypt").hash(
    secrets.token_urlsafe(32),
)

class AuthService:
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

        stmt = select(User).options(
            selectinload(User.role),
            selectinload(User.position),
            selectinload(User.user_departments).selectinload(UserDepartment.department)
        ).where(User.email == email).with_for_update()
        
        result = await db.execute(stmt)
        user = result.scalars().first()

        if not user:
            await run_in_threadpool(security.verify_password, password, TIMING_EQUALIZATION_HASH)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS_MESSAGE)

        if not user.is_active or user.is_deleted:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled. Please contact your administrator.")

        if user.is_locked:
            if user.lockout_until and user.lockout_until > now:
                remaining = int((user.lockout_until - now).total_seconds() / 60)
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Account locked. Try again in {remaining} minutes.")
            else:
                user.is_locked = False
                user.failed_login_attempts = 0
                user.lockout_until = None
                await db.commit()

        if not getattr(user, "local_password_enabled", True) or not user.hashed_password:
            await run_in_threadpool(security.verify_password, password, TIMING_EQUALIZATION_HASH)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS_MESSAGE)
        is_valid_password = await run_in_threadpool(security.verify_password, password, user.hashed_password)

        if not is_valid_password:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= 5:
                user.is_locked = True
                user.lockout_until = now + timedelta(minutes=30)
                
            failed_history = LoginHistory(user_id=user.id, ip_address=ip_address, user_agent=user_agent, is_success=False, attempted_at=now)
            db.add(failed_history)
            await db.commit()
            
            if user.is_locked:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account locked due to multiple failed login attempts.")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS_MESSAGE)

        user.failed_login_attempts = 0
        user.is_locked = False
        user.lockout_until = None
        expiry_days = await get_password_expiry_days(db)
        if expiry_days:
            latest_password = (await db.execute(
                select(PasswordHistory)
                .where(PasswordHistory.user_id == user.id)
                .order_by(PasswordHistory.created_at.desc())
                .limit(1)
            )).scalars().first()
            password_changed_at = getattr(latest_password, "created_at", None)
            if password_changed_at:
                age = now - password_changed_at
                if age >= timedelta(days=expiry_days):
                    user.must_change_password = True
        
        success_history = LoginHistory(user_id=user.id, ip_address=ip_address, user_agent=user_agent, is_success=True, attempted_at=now)
        db.add(success_history)
        db.add(AuditLog(
            user_id=user.id, action="LOGIN", table_name="auth_sessions",
            ip_address=ip_address, new_value={"success": True, "remember_me": remember_me},
        ))
        await db.commit()

        return await AuthService.issue_authenticated_session(
            db, user, ip_address, user_agent, remember_me, audit_action="LOGIN"
        )

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
        role_value = user.role.name if user.role else str(user.role_id)
        user_level = "L2"
        dept_name = "No Department"
        dept_id = None
        
        if user.user_departments and len(user.user_departments) > 0:
            ud = user.user_departments[0]
            user_level = ud.user_level
            dept_id = str(ud.department_id)
            if ud.department:
                dept_name = ud.department.name

        # Older callers/tests may provide a light-weight fake DB.  Real
        # sessions are always persisted when using SQLAlchemy.
        session = None
        if hasattr(db, "bind"):
            import hashlib
            device_id = hashlib.sha256(
                f"{user_agent}|{ip_address}".encode()
            ).hexdigest()[:32]
            active = (await db.execute(
                select(AuthSession).where(
                    AuthSession.user_id == user.id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > now,
                ).order_by(AuthSession.last_used_at.asc()).with_for_update()
            )).scalars().all()
            # A new login from the same device replaces its old session.
            for existing in active:
                if existing.device_id == device_id:
                    existing.revoked_at = now
                    db.add(AuditLog(
                        user_id=user.id, action="SESSION_REVOKED",
                        table_name="auth_sessions",
                        new_value={"reason": "new_login_same_device"},
                        ip_address=ip_address,
                    ))
            remaining = [item for item in active if item.device_id != device_id]
            for existing in remaining[:max(0, len(remaining) - 1)]:
                existing.revoked_at = now
                db.add(AuditLog(
                    user_id=user.id, action="SESSION_REVOKED",
                    table_name="auth_sessions",
                    new_value={"reason": "device_limit"},
                    ip_address=ip_address,
                ))
            session = AuthSession(
                user_id=user.id, device_id=device_id, user_agent=user_agent,
                ip_address=ip_address, remember_me=remember_me,
                expires_at=now + timedelta(
                    days=settings.REFRESH_TOKEN_EXPIRE_DAYS if remember_me else 1
                ),
                last_used_at=now, refresh_token_hash="pending",  # noqa: S106 - server-side placeholder, never a credential
            )
            db.add(session)
            await db.flush()

        token_data = {"sub": user.email, "user_id": str(user.id), "role": role_value, "level": user_level}
        if session is not None:
            token_data["sid"] = str(session.id)
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