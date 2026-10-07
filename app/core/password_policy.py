import re
import secrets
import string

from fastapi import HTTPException
from sqlalchemy import select

from app.models.system import SystemSetting

DEFAULT_PASSWORD_POLICY = {
    "minimum_password_length": 12,
    "require_complexity": True,
    "force_password_change_days": 90,
}

# Bound input before handing it to a password-hashing backend. This is large
# enough for passphrases while preventing accidental or hostile oversized input.
MAX_PASSWORD_LENGTH = 256

# This is intentionally a small, high-confidence deny list. It supplements,
# rather than replaces, the configurable length/complexity policy below.
COMMON_WEAK_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789",
    "qwerty", "qwerty123", "admin", "admin123", "letmein",
    "welcome", "welcome1", "changeme", "change_me",
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
    if not isinstance(password, str) or not password:
        raise HTTPException(status_code=400, detail="Password is required.")
    if password.strip().casefold() in COMMON_WEAK_PASSWORDS:
        raise HTTPException(status_code=400, detail="Password is too commonly used.")
    if password.isspace():
        raise HTTPException(status_code=400, detail="Password cannot contain only whitespace.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be no more than {MAX_PASSWORD_LENGTH} characters long",
        )
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