from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ASYNC_POSTGRES_SCHEME = "postgresql+asyncpg://"

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
    DATABASE_SSL_REQUIRED: bool = True
    QA_DATABASE_HOST: str = "postgres-qa"
    QA_DATABASE_PORT: int = 5432
    QA_DATABASE_NAME: str = "kpi_qa"

    # 🌟 MinIO / S3 Storage Settings
    USE_S3: bool = True
    S3_ENDPOINT: str | None = None
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_BUCKET_NAME: str = "kpi-evidence"
    ALLOWED_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:8000"
    REDIS_URL: str | None = None
    OAUTH_ENABLED: bool = False
    OAUTH_PUBLIC_BASE_URL: str | None = None
    OAUTH_STATE_TTL_SECONDS: int = 600
    GOOGLE_OIDC_ENABLED: bool = False
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URI: str | None = None
    MICROSOFT_OIDC_ENABLED: bool = False
    MICROSOFT_CLIENT_ID: str | None = None
    MICROSOFT_CLIENT_SECRET: str | None = None
    MICROSOFT_TENANT_ID: str = "organizations"
    MICROSOFT_REDIRECT_URI: str | None = None
    ENABLE_SCHEDULER: bool = False
    RUN_SCHEDULER: bool = False
    SKIP_DATABASE_SEED: bool = False
    TIMEZONE: str = "Asia/Yangon"
    MAIL_ENABLED: bool = False
    MAIL_HOST: str | None = None
    MAIL_PORT: int = 587
    MAIL_USERNAME: str | None = None
    MAIL_PASSWORD: str | None = None
    MAIL_FROM: str | None = None
    MAIL_FROM_NAME: str = "Enterprise KPI Platform"
    MAIL_USE_TLS: bool = True
    BOOTSTRAP_SUPERADMIN_EMAIL: str | None = None
    BOOTSTRAP_SUPERADMIN_PASSWORD: str | None = None
    SEED_SUPERADMIN_EMAIL: str | None = None
    SEED_SUPERADMIN_PASSWORD: str | None = None
    # Backward-compatible read of the former variable name for local setups.
    SEED_SUPER_ADMIN_PASSWORD: str | None = None
    SEED_DEMO_PASSWORD: str | None = None
    SEED_L2_PASSWORD: str | None = None
    SEED_L0_PASSWORD: str | None = None
    SEED_L1_PASSWORD: str | None = None
    SEED_TEST_ACCOUNTS_ENABLED: bool = False
    SEED_TEST_SUPERADMIN_EMAIL: str | None = None
    SEED_TEST_SUPERADMIN_PASSWORD: str | None = None
    SEED_TEST_ADMIN_EMAIL: str | None = None
    SEED_TEST_ADMIN_PASSWORD: str | None = None
    SEED_TEST_FIRST_LOGIN_EMAIL: str | None = None
    SEED_TEST_FIRST_LOGIN_PASSWORD: str | None = None
    SEED_TEST_L0_PASSWORD: str | None = None
    SEED_TEST_L1_PASSWORD: str | None = None
    SEED_TEST_L2_PASSWORD: str | None = None

    @field_validator("ALGORITHM")
    @classmethod
    def validate_jwt_algorithm(cls, value: str) -> str:
        if value != "HS256":
            raise ValueError("ALGORITHM must be HS256.")
        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def fix_neon_url(cls, v: str) -> str:
        v = v.strip()

        # Some hosting dashboards can return a PostgreSQL URL nested inside
        # another PostgreSQL scheme; unwrap that extra scheme before parsing.
        for prefix in ("postgresql+asyncpg://postgresql://", "postgresql://postgresql://"):
            if v.startswith(prefix):
                v = ASYNC_POSTGRES_SCHEME + v[len(prefix):]
                break

        if v.startswith("postgres://"):
            v = v.replace("postgres://", ASYNC_POSTGRES_SCHEME, 1)
        elif v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
            v = v.replace("postgresql://", ASYNC_POSTGRES_SCHEME, 1)

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
            secret_key = self.SECRET_KEY.strip()
            if (
                len(secret_key) < 32
                or secret_key.lower() in {
                    "replace-with-super-secret-key-in-production",
                    "change-me",
                }
                or secret_key.upper().startswith("CHANGE_ME")
            ):
                raise ValueError("SECRET_KEY must be a non-placeholder value of at least 32 characters in production.")
            if not self.ALLOWED_ORIGINS.strip():
                raise ValueError("ALLOWED_ORIGINS must be configured in production.")
            if not self.DATABASE_SSL_REQUIRED:
                raise ValueError("DATABASE_SSL_REQUIRED must be enabled in production.")
            database_host = (urlparse(getattr(self, "DATABASE_URL", "")).hostname or "").lower()
            if database_host in {"", "localhost", "127.0.0.1", "::1"}:
                raise ValueError("Production DATABASE_URL must target a remote TLS-protected database.")
            if not self.USE_S3:
                raise ValueError("USE_S3 must be enabled in production.")
            if self.USE_S3:
                storage_scheme = urlparse(self.S3_ENDPOINT or "").scheme.lower()
                if storage_scheme != "https":
                    raise ValueError("S3_ENDPOINT must use HTTPS in production.")

        if self.ENV.lower() == "qa":
            database = urlparse(self.DATABASE_URL)
            database_host = (database.hostname or "").lower()
            expected_host = self.QA_DATABASE_HOST.strip().lower()
            database_name = database.path.lstrip("/")
            allowed_qa_hosts = {"localhost", "127.0.0.1", "::1", "postgres-qa"}
            allowed_qa_ports = {5432, 55432}
            if expected_host not in allowed_qa_hosts:
                    raise ValueError("QA_DATABASE_HOST must identify the local QA PostgreSQL service.")
            if self.QA_DATABASE_PORT not in allowed_qa_ports:
                    raise ValueError("QA_DATABASE_PORT must identify the local QA PostgreSQL service.")
            if database_host != expected_host:
                    raise ValueError(
                        "QA DATABASE_URL must target the configured local QA PostgreSQL host."
                    )
            if database.port != self.QA_DATABASE_PORT:
                    raise ValueError(
                        "QA DATABASE_URL must target the configured local QA PostgreSQL port."
                    )
            if database_name != self.QA_DATABASE_NAME:
                    raise ValueError(
                        "QA DATABASE_URL must target the configured local QA database name."
                    )

        return self

    # Resolve the project dotenv file from the source tree so startup does not
    # depend on the process working directory. Hosted environments can still
    # provide values through their injected environment variables.
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
    )

settings = Settings()