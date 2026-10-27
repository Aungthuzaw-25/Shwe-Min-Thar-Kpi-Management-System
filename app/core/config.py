import base64
from pathlib import Path
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ASYNC_POSTGRES_SCHEME = "postgresql+asyncpg://"
JWT_SIGNING_ALGORITHMS = frozenset({"HS256"})


def _is_valid_fernet_key(value: str) -> bool:
    """Fernet keys are 44 urlsafe-base64 chars encoding exactly 32 bytes.

    This is the same shape check Fernet itself performs; the cryptography
    library remains the authority at MFA use time (sa_mfa._fernet).
    """
    if len(value) != 44:
        return False
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception:
        return False
    return len(raw) == 32

class Settings(BaseSettings):
    PROJECT_NAME: str
    VERSION: str
    
    # No default on purpose, like ALLOWED_ORIGINS: the environment must
    # declare itself. If ENV is forgotten, the app refuses to start
    # instead of silently skipping the production validator below.
    # Development copies .env.example (ENV=development); Docker sets
    # ENV=production in the image; Railway prod must set ENV=production.
    ENV: str

    DATABASE_URL: str
    JWT_SECRET_KEY: str = Field(
        validation_alias=AliasChoices("JWT_SECRET_KEY", "SECRET_KEY")
    )
    JWT_ALGORITHM: str = Field(
        default="HS256",
        validation_alias=AliasChoices("JWT_ALGORITHM", "ALGORITHM"),
    )
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=30,
        gt=0,
        validation_alias=AliasChoices(
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "ACCESS_TOKEN_EXPIRE_MINUTES"
        ),
    )
    REFRESH_TOKEN_EXPIRE_DAYS: int
    # SA TOTP (Google Authenticator) secret encryption key — a
    # Fernet key (generate with: python -c "from cryptography.fernet
    # import Fernet; print(Fernet.generate_key().decode())").
    # If unset, MFA setup/verification fail closed (503). This key
    # is NOT the JWT secret: losing it means every SA must
    # re-enroll via the peer-reset flow, so back it up with the
    # other production secrets.
    TOTP_ENCRYPTION_KEY: str | None = None
    DATABASE_SSL_REQUIRED: bool = True
    # Per-statement ceiling for asyncpg (seconds). One statement may
    # never run longer than this, so a runaway query cannot hold a
    # pooled connection forever. Long jobs (bulk KPI calculation) are
    # many statements, not one, so they are not cut off by this.
    DB_COMMAND_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0)
    # How long an Idempotency-Key is remembered (hours). Within this
    # window a retried submit returns the original result instead of
    # executing twice; after it, the key counts as never seen.
    IDEMPOTENCY_TTL_HOURS: int = Field(default=24, gt=0)
    QA_DATABASE_HOST: str = "postgres-qa"
    QA_DATABASE_PORT: int = 5432
    QA_DATABASE_NAME: str = "kpi_qa"

    # 🌟 MinIO / S3 Storage Settings
    USE_S3: bool = True
    S3_ENDPOINT: str | None = None
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_BUCKET_NAME: str = "kpi-evidence"
    # No default on purpose: CORS origins come from the environment
    # (.env) only. A localhost default here used to silently CORS-block
    # the real production domain whenever the env var was forgotten.
    ALLOWED_ORIGINS: str
    REDIS_URL: str | None = None
    OAUTH_ENABLED: bool = False
    APP_BASE_URL: str | None = None
    OAUTH_STATE_TTL_SECONDS: int = 600
    GOOGLE_OIDC_ENABLED: bool = False
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URI: str | None = None
    MICROSOFT_OIDC_ENABLED: bool = False
    MICROSOFT_CLIENT_ID: str | None = None
    MICROSOFT_CLIENT_SECRET: str | None = None
    MICROSOFT_TENANT: str = "common"
    MICROSOFT_TENANT_ID: str | None = None
    MICROSOFT_REDIRECT_URI: str | None = None
    ENABLE_SCHEDULER: bool = False
    RUN_SCHEDULER: bool = False
    SKIP_DATABASE_SEED: bool = False
    TIMEZONE: str = "Asia/Yangon"
    RESEND_API_KEY: str | None = None
    SMTP_PASSWORD: str | None = None
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

    @property
    def SECRET_KEY(self) -> str:
        """Backward-compatible read alias; new code uses JWT_SECRET_KEY."""
        return self.JWT_SECRET_KEY

    @property
    def ALGORITHM(self) -> str:
        """Backward-compatible read alias; new code uses JWT_ALGORITHM."""
        return self.JWT_ALGORITHM

    @property
    def ACCESS_TOKEN_EXPIRE_MINUTES(self) -> int:
        """Backward-compatible read alias for legacy integrations."""
        return self.JWT_ACCESS_TOKEN_EXPIRE_MINUTES

    @field_validator("JWT_ALGORITHM")
    @classmethod
    def validate_jwt_algorithm(cls, value: str) -> str:
        if value not in JWT_SIGNING_ALGORITHMS:
            raise ValueError(
                "JWT_ALGORITHM must be one of the explicitly supported application algorithms."
            )
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

        if v.endswith(("?", "&")):
            v = v.rstrip("?&")

        return v

    @model_validator(mode="after")
    def validate_security_settings(self):
        # Fail fast in EVERY environment (not just production): a
        # missing or blank value must never fall back to anything.
        if not self.ALLOWED_ORIGINS.strip():
            raise ValueError(
                "ALLOWED_ORIGINS must be set in the environment (.env) to a "
                "non-empty comma-separated list of origins; no default is "
                "provided. Development example: http://localhost:8000. "
                "Production must list the real domain(s)."
            )
        if self.USE_S3 and not all(
            (self.S3_ENDPOINT, self.S3_ACCESS_KEY, self.S3_SECRET_KEY)
        ):
            raise ValueError(
                "S3_ENDPOINT, S3_ACCESS_KEY, and S3_SECRET_KEY are required when USE_S3=true."
            )

        if self.ENV.lower() == "production":
            self._validate_production_security()

        if self.ENV.lower() == "qa":
            self._validate_qa_database()

        return self

    def _validate_production_security(self) -> None:
        secret_key = self.JWT_SECRET_KEY.strip()
        if (
            len(secret_key) < 32
            or secret_key.lower() in {
                "replace-with-super-secret-key-in-production",
                "change-me",
            }
            or secret_key.upper().startswith("CHANGE_ME")
        ):
            raise ValueError(
                "JWT_SECRET_KEY must be a non-placeholder value of at least 32 characters in production."
            )
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
        # Last on purpose: earlier production checks must keep failing with
        # their own message first (existing tests pin that order). A missing
        # or malformed TOTP key must also fail at startup — otherwise prod
        # would deploy fine and SA Authenticator MFA would only 503 at the
        # first SA login/enrollment attempt.
        totp_key = (self.TOTP_ENCRYPTION_KEY or "").strip()
        if not totp_key:
            raise ValueError(
                "TOTP_ENCRYPTION_KEY must be set in production: SA Authenticator "
                "MFA cannot be set up or verified without it."
            )
        if not _is_valid_fernet_key(totp_key):
            raise ValueError(
                "TOTP_ENCRYPTION_KEY must be a valid Fernet key in production "
                "(generate with: python -c \"from cryptography.fernet import "
                "Fernet; print(Fernet.generate_key().decode())\")."
            )

    def _validate_qa_database(self) -> None:
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

    # Resolve the project dotenv file from the source tree so startup does not
    # depend on the process working directory. Hosted environments can still
    # provide values through their injected environment variables.
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
    )

settings = Settings()