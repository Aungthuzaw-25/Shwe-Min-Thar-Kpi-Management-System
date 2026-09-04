from datetime import datetime, timedelta, timezone # 🌟 IMPORT FIX: Added timezone
import uuid
from passlib.context import CryptContext
from jose import jwt
from app.core.config import settings

# ⚡ PERFORMANCE FIX: Reduced bcrypt rounds from 15 to 12. 
# Round 15 is exponentially slower and causes multi-second login delays. 
# Round 12 is the industry standard for balancing instant speed and strict security.
pwd_context = CryptContext(
    schemes=['bcrypt', 'argon2'], 
    deprecated='auto',
    bcrypt__rounds=12 # 👈 Changed from 15 to 12
)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    if isinstance(plain_password, str):
        plain_password = plain_password.encode('utf-8')
    if len(plain_password) > 72:
        plain_password = plain_password[:72]
    return pwd_context.verify(plain_password.decode('utf-8', errors='ignore'), hashed_password)

def get_password_hash(password: str) -> str:
    if isinstance(password, str):
        password = password.encode('utf-8')
    if len(password) > 72:
        password = password[:72]
    return pwd_context.hash(password.decode('utf-8', errors='ignore'))

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    # 🌟 FUTURE-PROOF FIX: datetime.utcnow() is deprecated in Python 3.12+
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({'exp': expire, 'type': 'access', 'jti': str(uuid.uuid4())})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    # 🌟 FUTURE-PROOF FIX: datetime.utcnow() is deprecated in Python 3.12+
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({'exp': expire, 'type': 'refresh', 'jti': str(uuid.uuid4())})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)