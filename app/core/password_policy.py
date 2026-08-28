import re
from fastapi import HTTPException
import secrets
import string

def validate_password_strength(password: str):
    if len(password) < 12:
        raise HTTPException(status_code=400, detail='Password must be at least 12 characters long')
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