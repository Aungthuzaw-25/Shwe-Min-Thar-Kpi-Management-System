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
