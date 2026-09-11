from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, model_validator
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

class Settings(BaseSettings):
    PROJECT_NAME: str
    VERSION: str
    
    # 🌟 ADDED: Explicit Environment Variable for main.py CORS Security check
    ENV: str = "development"
    
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    REFRESH_TOKEN_EXPIRE_DAYS: int

    # 🌟 MinIO / S3 Storage Settings
    USE_S3: bool = True
    S3_ENDPOINT: Optional[str] = None
    S3_ACCESS_KEY: Optional[str] = None
    S3_SECRET_KEY: Optional[str] = None
    S3_BUCKET_NAME: str = "kpi-evidence"
    ALLOWED_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:8000"
    REDIS_URL: Optional[str] = None
    OAUTH_ENABLED: bool = False
    OAUTH_PUBLIC_BASE_URL: Optional[str] = None
    OAUTH_STATE_TTL_SECONDS: int = 600
    GOOGLE_OIDC_ENABLED: bool = False
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: Optional[str] = None
    MICROSOFT_OIDC_ENABLED: bool = False
    MICROSOFT_CLIENT_ID: Optional[str] = None
    MICROSOFT_CLIENT_SECRET: Optional[str] = None
    MICROSOFT_TENANT_ID: str = "organizations"
    MICROSOFT_REDIRECT_URI: Optional[str] = None
    ENABLE_SCHEDULER: bool = False
    RUN_SCHEDULER: bool = False
    TIMEZONE: str = "Asia/Yangon"
    MAIL_ENABLED: bool = False
    MAIL_HOST: Optional[str] = None
    MAIL_PORT: int = 587
    MAIL_USERNAME: Optional[str] = None
    MAIL_PASSWORD: Optional[str] = None
    MAIL_FROM: Optional[str] = None
    MAIL_FROM_NAME: str = "Enterprise KPI Platform"
    MAIL_USE_TLS: bool = True
    BOOTSTRAP_SUPERADMIN_EMAIL: Optional[str] = None
    BOOTSTRAP_SUPERADMIN_PASSWORD: Optional[str] = None
    SEED_SUPERADMIN_EMAIL: Optional[str] = None
    SEED_SUPERADMIN_PASSWORD: Optional[str] = None
    # Backward-compatible read of the former variable name for local setups.
    SEED_SUPER_ADMIN_PASSWORD: Optional[str] = None
    SEED_DEMO_PASSWORD: Optional[str] = None
    SEED_L2_PASSWORD: Optional[str] = None
    SEED_L0_PASSWORD: Optional[str] = None
    SEED_L1_PASSWORD: Optional[str] = None

    @field_validator("DATABASE_URL")
    @classmethod
    def fix_neon_url(cls, v: str) -> str:
        v = v.strip()

        # Some hosting dashboards can return a PostgreSQL URL nested inside
        # another PostgreSQL scheme; unwrap that extra scheme before parsing.
        for prefix in ("postgresql+asyncpg://postgresql://", "postgresql://postgresql://"):
            if v.startswith(prefix):
                v = "postgresql+asyncpg://" + v[len(prefix):]
                break

        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)

        for pattern in (
            "?channel_binding=require",
            "&channel_binding=require",
            "?sslmode=require",
            "&sslmode=require",
            "?sslmode=disable",
            "&sslmode=disable",
        ):
            v = v.replace(pattern, "")

        if v.endswith("?") or v.endswith("&"):
            v = v.rstrip("?&")

        return v

    @model_validator(mode="after")
    def validate_security_settings(self):
        if self.USE_S3 and not all(
            (self.S3_ENDPOINT, self.S3_ACCESS_KEY, self.S3_SECRET_KEY)
        ):
            raise ValueError(
                "S3_ENDPOINT, S3_ACCESS_KEY, and S3_SECRET_KEY are required when USE_S3=true."
            )

        if self.ENV.lower() == "production":
            if self.SECRET_KEY.lower() in {
                "replace-with-super-secret-key-in-production",
                "change-me",
            }:
                raise ValueError("SECRET_KEY must be changed in production.")
            if not self.ALLOWED_ORIGINS.strip():
                raise ValueError("ALLOWED_ORIGINS must be configured in production.")
            if self.USE_S3:
                storage_scheme = urlparse(self.S3_ENDPOINT or "").scheme.lower()
                if storage_scheme != "https":
                    raise ValueError("S3_ENDPOINT must use HTTPS in production.")

        return self

    # Resolve the project dotenv file from the source tree so startup does not
    # depend on the process working directory. Hosted environments can still
    # provide values through their injected environment variables.
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
    )

settings = Settings()