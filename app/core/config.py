from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, model_validator
from typing import Optional

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
    ENABLE_SCHEDULER: bool = False
    TIMEZONE: str = "Asia/Yangon"

    @field_validator("DATABASE_URL")
    @classmethod
    def fix_neon_url(cls, v: str) -> str:
        v = v.replace("sslmode=require", "ssl=require")
        v = v.replace("&channel_binding=require", "")
        v = v.replace("?channel_binding=require", "")
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

        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()