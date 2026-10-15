import hashlib
import uuid
from datetime import UTC, datetime, timedelta  # 🌟 IMPORT FIX: Added timezone

import jwt
from fastapi.concurrency import run_in_threadpool
from jwt import PyJWTError as JWTError
from passlib.context import CryptContext

from app.core.config import settings
from app.core.password_policy import MAX_PASSWORD_LENGTH

# ⚡ PERFORMANCE FIX: Reduced bcrypt rounds from 15 to 12. 
# Round 15 is exponentially slower and causes multi-second login delays. 
# Round 12 is the industry standard for balancing instant speed and strict security.
pwd_context = CryptContext(
    schemes=['argon2', 'bcrypt'],
    default='argon2',
    deprecated=['bcrypt'],
    argon2__type='ID',
    bcrypt__rounds=12 # 👈 Changed from 15 to 12
)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a credential without ever truncating user-controlled input."""
    if not isinstance(plain_password, str) or not isinstance(hashed_password, str):
        return False
    if len(plain_password) > MAX_PASSWORD_LENGTH:
        return False
    if pwd_context.identify(hashed_password) == "bcrypt" and len(plain_password.encode("utf-8")) > 72:
        # Bcrypt truncates after 72 bytes; reject rather than authenticate a prefix.
        return False
    try:
        # Passlib/bcrypt rejects inputs above bcrypt's 72-byte limit. That is
        # the desired outcome for legacy bcrypt hashes: accepting a prefix
        # would silently make two distinct credentials equivalent.
        return pwd_context.verify(plain_password, hashed_password)
    except (ValueError, TypeError):
        return False

def get_password_hash(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError('Password is required.')
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f'Password must be no more than {MAX_PASSWORD_LENGTH} characters long.')
    return pwd_context.hash(password)


async def get_password_hash_async(password: str) -> str:
    """Hash passwords without blocking the async request or startup event loop."""
    return await run_in_threadpool(get_password_hash, password)


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    """Verify passwords without blocking the async event loop."""
    return await run_in_threadpool(verify_password, plain_password, hashed_password)


def password_needs_rehash(hashed_password: str) -> bool:
    """Identify legacy bcrypt hashes for opportunistic Argon2id migration."""
    try:
        return pwd_context.needs_update(hashed_password)
    except (ValueError, TypeError):
        return False

TOKEN_TYPES = {"access", "refresh"}
REQUIRED_TOKEN_CLAIMS = ("sub", "type", "exp", "iat", "jti")
INVALID_TOKEN_MESSAGE = "Invalid token."


def _create_token(data: dict, token_type: str, expires_delta: timedelta) -> str:
    if token_type not in TOKEN_TYPES:
        raise ValueError("Unsupported token type.")
    subject = data.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("Token subject is required.")

    issued_at = datetime.now(UTC)
    to_encode = data.copy()
    # Server-controlled security claims always replace caller input.
    to_encode.update({
        "exp": issued_at + expires_delta,
        "iat": issued_at,
        "type": token_type,
        "jti": str(uuid.uuid4()),
    })
    return jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def create_access_token(data: dict) -> str:
    return _create_token(
        data,
        "access",
        timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(data: dict, remember_me: bool = True) -> str:
    days = settings.REFRESH_TOKEN_EXPIRE_DAYS if remember_me else 1
    return _create_token(data, "refresh", timedelta(days=days))


def _validate_token_payload(
    payload: dict, expected_type: str, *, require_session: bool
) -> dict:
    for claim in REQUIRED_TOKEN_CLAIMS:
        value = payload.get(claim)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise JWTError(INVALID_TOKEN_MESSAGE)
    if not isinstance(payload["sub"], str) or not isinstance(payload["jti"], str):
        raise JWTError(INVALID_TOKEN_MESSAGE)
    if payload.get("type") != expected_type:
        raise JWTError("Invalid token type.")
    if require_session:
        _validate_session_claims(payload)
    return payload


def _validate_session_claims(payload: dict) -> None:
    for claim in ("sid", "user_id"):
        value = payload.get(claim)
        if not isinstance(value, str) or not value.strip():
            raise JWTError(INVALID_TOKEN_MESSAGE)


def decode_token(
    token: str, expected_type: str, *, require_session: bool = False
) -> dict:
    """Verify a server-issued JWT and enforce its security claims."""
    if expected_type not in TOKEN_TYPES:
        raise ValueError("Unsupported token type.")
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )
    return _validate_token_payload(payload, expected_type, require_session=require_session)


def token_digest(token: str) -> str:
    """Return a non-reversible digest suitable for revocation/session lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()