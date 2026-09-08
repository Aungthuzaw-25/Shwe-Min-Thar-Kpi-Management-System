import pytest

from app.core.config import Settings, settings


def test_settings_are_loaded_from_environment():
    assert settings.PROJECT_NAME
    assert settings.VERSION
    assert settings.DATABASE_URL
    assert settings.SECRET_KEY


def test_fix_neon_url_removes_asyncpg_unsupported_query_params():
    raw = (
        "postgresql://neondb_owner:password@host.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )

    fixed = Settings.fix_neon_url(raw)

    assert fixed.startswith("postgresql+asyncpg://")
    assert "sslmode=require" not in fixed
    assert "channel_binding=require" not in fixed


def test_fix_neon_url_unwraps_nested_postgresql_scheme():
    raw = (
        "postgresql+asyncpg://postgresql://neondb_owner:password"
        "@host.neon.tech/neondb"
    )

    fixed = Settings.fix_neon_url(raw)

    assert fixed == "postgresql+asyncpg://neondb_owner:password@host.neon.tech/neondb"
 

def test_seed_passwords_are_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("SEED_SUPERADMIN_EMAIL", "superadmin@example.com")
    monkeypatch.setenv("SEED_SUPERADMIN_PASSWORD", "test-super-admin")
    monkeypatch.setenv("SEED_DEMO_PASSWORD", "test-demo")

    configured = Settings()

    assert configured.SEED_SUPERADMIN_EMAIL == "superadmin@example.com"
    assert configured.SEED_SUPERADMIN_PASSWORD == "test-super-admin"
    assert configured.SEED_DEMO_PASSWORD == "test-demo"


def test_bootstrap_credentials_are_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_EMAIL", "bootstrap@example.com")
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_PASSWORD", "InitialPassword123!")

    configured = Settings()

    assert configured.BOOTSTRAP_SUPERADMIN_EMAIL == "bootstrap@example.com"
    assert configured.BOOTSTRAP_SUPERADMIN_PASSWORD == "InitialPassword123!"


def test_production_rejects_plaintext_s3_endpoint():
    configured = Settings.model_construct(
        ENV="production",
        SECRET_KEY="a" * 64,
        ALLOWED_ORIGINS="https://app.example.com",
        USE_S3=True,
        S3_ENDPOINT="http://minio:9000",
        S3_ACCESS_KEY="access",
        S3_SECRET_KEY="secret",
    )

    with pytest.raises(ValueError, match="S3_ENDPOINT must use HTTPS"):
        configured.validate_security_settings()
