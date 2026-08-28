from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.database.connection import get_db
# 🌟 FIX: Permission များကို တစ်ပါတည်း ဆွဲထုတ်ရန် Role ကိုပါ Import လုပ်ပါသည်
from app.modules.users.models import User, Role, Permission, RolePermission
from app.modules.auth.models import TokenBlacklist

# 🌟 KEY FIX: Swagger UI မှ Auth ဝင်ရန် လမ်းကြောင်းကို swagger-login သို့ ပြောင်းထားပါသည်
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/swagger-login", auto_error=False)

async def get_current_user(
    request: Request, 
    token: str = Depends(oauth2_scheme), 
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Cookie (Web), Header (Mobile App), နှင့် Swagger UI အားလုံးကို Support ပေးသော 
    Universal Authentication Function
    """
    # ၁။ Swagger UI သို့မဟုတ် Authorization Header မှ Token ကို အရင်ရှာမည်
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
        else:
            # ၂။ မရှိပါက Browser ၏ HttpOnly Cookie မှ access_token ကို ရှာမည်
            cookie_token = request.cookies.get("access_token")
            if cookie_token and cookie_token.startswith("Bearer "):
                token = cookie_token.split(" ")[1]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Not authenticated. Token missing."
        )

    # 🌟 SECURITY & PERFORMANCE FIX: Hacker မှ Token အမှားများဖြင့် DB ကို DoS မလုပ်နိုင်ရန် JWT ကို အရင် Decode လုပ်မည်
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                detail="Invalid token payload."
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Token expired or invalid."
        )

    # Decode အောင်မြင်မှသာ Blacklist ဝင်နေသလား စစ်ဆေးမည်
    is_blacklisted = await db.execute(select(TokenBlacklist).where(TokenBlacklist.token == token))
    if is_blacklisted.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Token has been revoked. Please log in again."
        )

    # 🌟 PERFORMANCE FIX (DBA): User ကို ဆွဲထုတ်စဉ် Role နှင့် ၎င်း၏ Permissions များကို 
    # တစ်ပါတည်း (Eager Load) ဆွဲထုတ်ထားခြင်းဖြင့် RequirePermission တွင် DB ထပ်ခေါ်စရာ မလိုတော့ပါ။
    stmt = select(User).options(
        selectinload(User.role).selectinload(Role.permissions)
    ).where(
        User.email == email, 
        User.is_deleted == False
    )
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="User not found or account disabled."
        )
        
    if user.is_locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Account is temporarily locked."
        )
        
    return user


# =====================================================================
# 🌟 ROLE-BASED ACCESS CONTROL (RBAC)
# =====================================================================
class RoleChecker:
    def __init__(self, allowed_roles: list[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: User = Depends(get_current_user)):
        if not user.role or user.role.name not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Operation not permitted. Insufficient role."
            )
        return user


# =====================================================================
# 🌟 GRANULAR PERMISSION SYSTEM (Optimized for High Performance)
# =====================================================================
class RequirePermission:
    """
    တိကျသော လုပ်ပိုင်ခွင့်များ (ဥပမာ - 'kpi.approve', 'report.export') ကို စစ်ဆေးရန်။
    Usage: @router.get("/", dependencies=[Depends(RequirePermission(["kpi.approve"]))])
    """
    def __init__(self, required_permissions: list[str]):
        self.required_permissions = required_permissions

    # 🌟 FIX: DB ကို ထပ်မံ Query လုပ်စရာ မလိုတော့ဘဲ get_current_user မှရလာသော Data ပေါ်မှ တိုက်ရိုက် စစ်ဆေးမည်
    async def __call__(self, current_user: User = Depends(get_current_user)):
        # Super Admin များသည် Permission စစ်ဆေးခြင်းကို ကျော်ဖြတ်နိုင်သည် (Bypass)
        if current_user.role and current_user.role.name in ["Super Admin"]:
            return current_user

        # User တွင် ရှိသော Permission များကို Eager Load မှတစ်ဆင့် ယူမည်
        user_perms = [p.code for p in current_user.role.permissions] if current_user.role else []

        # လိုအပ်သော Permission အနည်းဆုံး တစ်ခုရှိ/မရှိ စစ်ဆေးမည်
        has_permission = any(perm in user_perms for perm in self.required_permissions)
        
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permissions. Needs one of: {', '.join(self.required_permissions)}"
            )
            
        return current_user