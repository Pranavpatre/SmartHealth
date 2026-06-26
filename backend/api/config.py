from functools import lru_cache
from typing import List, Literal, Union

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    environment: Literal["development", "staging", "production"] = "development"
    secret_key: str = "dev-secret-key-change-in-production"
    # Stored as a comma-separated string so pydantic-settings won't try to JSON-parse it.
    # Consumers should use the `cors_origins` property which returns List[str].
    allowed_origins: str = "http://localhost:3000,http://localhost:3001"

    # Database
    database_url: str = "postgresql+asyncpg://smarthealth:smarthealth@localhost:5432/smarthealth"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Sentry
    sentry_dsn: str = ""

    # WhatsApp Cloud API
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = "default_verify_token"
    whatsapp_api_version: str = "v19.0"

    # Gemini
    gemini_api_key: str = ""

    # JWT
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    algorithm: str = "HS256"

    # Celery
    celery_broker_url: str = ""
    celery_result_backend: str = ""

    # ML artefacts path
    ml_artefacts_path: str = "/app/ml-models/artefacts"

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def celery_broker(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def celery_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
