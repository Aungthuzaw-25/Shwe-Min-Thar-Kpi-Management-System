import re
from fastapi import HTTPException
import secrets
import string
from sqlalchemy import select

from app.models.system import SystemSetting

DEFAULT_PASSWORD_POLICY = {
    "minimum_password_length": 12,
    "require_complexity": True,
    "force_password_change_days": 90,
}

async def get_password_policy(db):
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "security")
    )
    setting = result.scalars().first()
    setting_value = getattr(setting, "value", None)
    value = setting_value if isinstance(setting_value, dict) else {}
    minimum_length = value.get(
        "minimum_password_length", value.get("min_length", DEFAULT_PASSWORD_POLICY["minimum_password_length"])
    )
    complexity = value.get("require_complexity", DEFAULT_PASSWORD_POLICY["require_complexity"])
    try:
        minimum_length = int(minimum_length)
    except (TypeError, ValueError):
        minimum_length = DEFAULT_PASSWORD_POLICY["minimum_password_length"]
    return {
        "minimum_length": max(8, minimum_length),
        "require_complexity": complexity is not False,
    }


async def get_password_expiry_days(db):
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "security")
    )
    setting = result.scalars().first()
    setting_value = getattr(setting, "value", None)
    value = setting_value if isinstance(setting_value, dict) else {}
    raw_days = value.get(
        "force_password_change_days",
        90 if value.get("password_expiry", False) else 0,
    )
    try:
        return max(0, int(raw_days))
    except (TypeError, ValueError):
        return 0


def validate_password_strength(
    password: str,
    minimum_length: int = DEFAULT_PASSWORD_POLICY["minimum_password_length"],
    require_complexity: bool = DEFAULT_PASSWORD_POLICY["require_complexity"],
):
    if len(password) < minimum_length:
        raise HTTPException(status_code=400, detail=f'Password must be at least {minimum_length} characters long')
    if not require_complexity:
        return True
    if not re.search(r'[A-Z]', password):
        raise HTTPException(status_code=400, detail='Password must contain at least one uppercase letter')
    if not re.search(r'[a-z]', password):
        raise HTTPException(status_code=400, detail='Password must contain at least one lowercase letter')
    if not re.search(r'[0-9]', password):
        raise HTTPException(status_code=400, detail='Password must contain at least one number')
    if not re.search(r'[!@#$%^&*(),.?"{}|<>]', password):
        raise HTTPException(status_code=400, detail='Password must contain at least one special character')
    return True

# 🌟 အသစ်: Strong Password ကို အလိုအလျောက် ဖန်တီးပေးသည့် Function
def generate_strong_password(length=12) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pwd = ''.join(secrets.choice(alphabet) for _ in range(length))
        try:
            validate_password_strength(pwd)
            return pwd # Policy နှင့် ကိုက်ညီမှသာ Return ပြန်မည်
        except HTTPException:
            continue