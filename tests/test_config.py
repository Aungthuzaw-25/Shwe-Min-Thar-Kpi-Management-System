from app.core.config import Settings, settings


def test_settings_are_loaded_from_environment():
    assert settings.PROJECT_NAME
    assert settings.VERSION
    assert settings.DATABASE_URL
    assert settings.SECRET_KEY


def test_fix_neon_url_keeps_neon_ssl_settings_and_uses_asyncpg():
    raw = (
        "postgresql://user:pass@host.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )

    fixed = Settings.fix_neon_url(raw)

    assert fixed.startswith("postgresql+asyncpg://")
    assert "sslmode=require" in fixed
    assert "channel_binding=require" not in fixed
