import jwt

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    verify_password,
)


def test_password_hash_round_trip():
    raw_password = "StrongPass!123"  # noqa: S105 - synthetic test fixture; not a production credential
    hashed = get_password_hash(raw_password)

    assert hashed != raw_password
    assert verify_password(raw_password, hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_access_and_refresh_tokens_include_expected_claims():
    access_token = create_access_token({"sub": "user@example.com"})
    refresh_token = create_refresh_token({"sub": "user@example.com"})

    access_payload = jwt.decode(
        access_token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )
    refresh_payload = jwt.decode(
        refresh_token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )

    assert access_payload["sub"] == "user@example.com"
    assert access_payload["type"] == "access"
    assert refresh_payload["sub"] == "user@example.com"
    assert refresh_payload["type"] == "refresh"
