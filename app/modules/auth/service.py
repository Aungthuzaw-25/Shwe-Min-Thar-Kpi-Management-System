"""
Authentication Domain Service (Single Source of Truth)
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status
from fastapi.concurrency import run_in_threadpool
from datetime import datetime, timezone, timedelta
from jose import jwt, JWTError
from typing import Optional, Dict, Any

from app.modules.users.models import User
from app.modules.departments.models import UserDepartment
from app.modules.auth.models import LoginHistory, TokenBlacklist
from app.modules.users import password_repo
from app.core import security
from app.core.config import settings
from app.core.password_policy import validate_password_strength

DUMMY_PASSWORD_HASH = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjIQ6VSAca"

class AuthService:
    @staticmethod
    async def authenticate_user(
        db: AsyncSession, 
        email: str, 
        password: str, 
        ip_address: str = "Unknown", 
        user_agent: str = "Unknown"
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        stmt = select(User).options(
            selectinload(User.role),
            selectinload(User.position),
            selectinload(User.user_departments).selectinload(UserDepartment.department)
        ).where(User.email == email)
        
        result = await db.execute(stmt)
        user = result.scalars().first()

        if not user:
            await run_in_threadpool(security.verify_password, password, DUMMY_PASSWORD_HASH)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

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
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

        user.failed_login_attempts = 0
        user.is_locked = False
        user.lockout_until = None
        
        success_history = LoginHistory(user_id=user.id, ip_address=ip_address, user_agent=user_agent, is_success=True, attempted_at=now)
        db.add(success_history)
        await db.commit()

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

        token_data = {"sub": user.email, "user_id": str(user.id), "role": role_value, "level": user_level}
        access_token = security.create_access_token(data=token_data)
        refresh_token = security.create_refresh_token(data=token_data)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "must_change_password": bool(user.must_change_password),
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