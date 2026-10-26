import pytest

from app.core.config import Settings, settings


def _isolated_settings_kwargs():
    return {
        "PROJECT_NAME": "KPI",
        "VERSION": "test",
        "ALLOWED_ORIGINS": "https://app.example.com",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "REFRESH_TOKEN_EXPIRE_DAYS": 7,
        "USE_S3": False,
    }


def test_settings_are_loaded_from_environment():
    assert settings.PROJECT_NAME
    assert settings.VERSION
    assert settings.DATABASE_URL
    assert settings.JWT_SECRET_KEY


def test_jwt_configuration_uses_canonical_environment_names(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "canonical-test-secret")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "45")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("ALGORITHM", raising=False)
    monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)

    configured = Settings(_env_file=None, **_isolated_settings_kwargs())

    assert configured.JWT_SECRET_KEY == "canonical-test-secret"
    assert configured.JWT_ALGORITHM == "HS256"
    assert configured.JWT_ACCESS_TOKEN_EXPIRE_MINUTES == 45
    assert configured.SECRET_KEY == configured.JWT_SECRET_KEY


def test_legacy_jwt_environment_names_remain_supported(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("JWT_ALGORITHM", raising=False)
    monkeypatch.delenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)
    monkeypatch.setenv("SECRET_KEY", "legacy-test-secret")
    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "90")

    configured = Settings(_env_file=None, **_isolated_settings_kwargs())

    assert configured.JWT_SECRET_KEY == "legacy-test-secret"
    assert configured.JWT_ALGORITHM == "HS256"
    assert configured.JWT_ACCESS_TOKEN_EXPIRE_MINUTES == 90


def test_jwt_configuration_rejects_unsupported_algorithm(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("JWT_ALGORITHM", "HS384")
    settings_kwargs = _isolated_settings_kwargs()

    with pytest.raises(ValueError, match="JWT_ALGORITHM"):
        Settings(_env_file=None, **settings_kwargs)


def test_missing_production_jwt_secret_fails_closed():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(
            _env_file=None,
            PROJECT_NAME="KPI",
            VERSION="test",
            ENV="production",
            DATABASE_URL="postgresql+asyncpg://user:pass@db.example.com/kpi",
            DATABASE_SSL_REQUIRED=True,
            USE_S3=False,
            ALLOWED_ORIGINS="https://app.example.com",
            REFRESH_TOKEN_EXPIRE_DAYS=7,
        )


def test_allowed_origins_is_required_field():
    # Single source of truth is the environment: no code default may
    # silently CORS-block the real domain when the env var is forgotten.
    assert Settings.model_fields["ALLOWED_ORIGINS"].is_required()


def test_missing_allowed_origins_fails_fast(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    settings_kwargs = _isolated_settings_kwargs()
    settings_kwargs.pop("ALLOWED_ORIGINS")

    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
        Settings(_env_file=None, **settings_kwargs)


def test_blank_allowed_origins_fails_fast():
    settings_kwargs = _isolated_settings_kwargs()
    settings_kwargs["ALLOWED_ORIGINS"] = "   "

    with pytest.raises(ValueError, match="ALLOWED_ORIGINS must be set"):
        Settings(_env_file=None, **settings_kwargs)


def test_production_missing_allowed_origins_fails_at_startup(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    settings_kwargs = _isolated_settings_kwargs()
    settings_kwargs.pop("ALLOWED_ORIGINS")
    settings_kwargs["ENV"] = "production"
    settings_kwargs["DATABASE_SSL_REQUIRED"] = True

    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
        Settings(_env_file=None, **settings_kwargs)


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
    assert configured.SEED_SUPERADMIN_PASSWORD == "test-super-admin"  # noqa: S105 - synthetic test fixture; not a production credential
    assert configured.SEED_DEMO_PASSWORD == "test-demo"  # noqa: S105 - synthetic test fixture; not a production credential


def test_bootstrap_credentials_are_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_EMAIL", "bootstrap@example.com")
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_PASSWORD", "InitialPassword123!")

    configured = Settings()

    assert configured.BOOTSTRAP_SUPERADMIN_EMAIL == "bootstrap@example.com"
    assert configured.BOOTSTRAP_SUPERADMIN_PASSWORD == "InitialPassword123!"  # noqa: S105 - synthetic test fixture; not a production credential


def test_production_rejects_plaintext_s3_endpoint():
    configured = Settings.model_construct(
        ENV="production",
        JWT_SECRET_KEY="a" * 64,
        ALLOWED_ORIGINS="https://app.example.com",
        DATABASE_URL="postgresql+asyncpg://qa:qa@db.example.com/kpi",
        USE_S3=True,
        S3_ENDPOINT="http://minio:9000",
        S3_ACCESS_KEY="access",
        S3_SECRET_KEY="secret",  # noqa: S106 - synthetic test fixture; not a production credential
    )

    with pytest.raises(ValueError, match="S3_ENDPOINT must use HTTPS"):
        configured.validate_security_settings()


def test_production_requires_s3_storage():
    configured = Settings.model_construct(
        ENV="production",
        JWT_SECRET_KEY="a" * 64,
        ALLOWED_ORIGINS="https://app.example.com",
        DATABASE_URL="postgresql+asyncpg://qa:qa@db.example.com/kpi",
        USE_S3=False,
    )

    with pytest.raises(ValueError, match="USE_S3 must be enabled"):
        configured.validate_security_settings()


def test_production_requires_database_tls():
    configured = Settings.model_construct(
        ENV="production",
        JWT_SECRET_KEY="a" * 64,
        ALLOWED_ORIGINS="https://app.example.com",
        DATABASE_URL="postgresql+asyncpg://qa:qa@db.example.com/kpi",
        DATABASE_SSL_REQUIRED=False,
        USE_S3=False,
    )

    with pytest.raises(ValueError, match="DATABASE_SSL_REQUIRED"):
        configured.validate_security_settings()


def test_production_rejects_local_database_target():
    configured = Settings.model_construct(
        ENV="production",
        JWT_SECRET_KEY="a" * 64,
        ALLOWED_ORIGINS="https://app.example.com",
        DATABASE_URL="postgresql+asyncpg://qa:qa@127.0.0.1/kpi",
        USE_S3=False,
    )

    with pytest.raises(ValueError, match="remote TLS-protected"):
        configured.validate_security_settings()


def test_qa_accepts_only_the_local_qa_database_target():
    configured = Settings.model_construct(
        ENV="qa",
        DATABASE_URL="postgresql+asyncpg://kpi_qa:password@postgres-qa:5432/kpi_qa",
        QA_DATABASE_HOST="postgres-qa",
        QA_DATABASE_PORT=5432,
        QA_DATABASE_NAME="kpi_qa",
        USE_S3=False,
        ALLOWED_ORIGINS="https://app.example.com",
    )

    configured.validate_security_settings()


@pytest.mark.parametrize(
    "database_url,match",
    [
        (
            "postgresql+asyncpg://user:password@remote.example/neondb",
            "local QA PostgreSQL host",
        ),
        (
            "postgresql+asyncpg://kpi_qa:password@postgres-qa:5433/kpi_qa",
            "local QA PostgreSQL port",
        ),
        (
            "postgresql+asyncpg://kpi_qa:password@postgres-qa:5432/neondb",
            "local QA database name",
        ),
    ],
)
def test_qa_rejects_remote_or_mismatched_database_targets(database_url, match):
    configured = Settings.model_construct(
        ENV="qa",
        DATABASE_URL=database_url,
        QA_DATABASE_HOST="postgres-qa",
        QA_DATABASE_PORT=5432,
        QA_DATABASE_NAME="kpi_qa",
        USE_S3=False,
        ALLOWED_ORIGINS="https://app.example.com",
    )

    with pytest.raises(ValueError, match=match):
        configured.validate_security_settings()
