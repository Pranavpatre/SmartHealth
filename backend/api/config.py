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
    allowed_origins: str = (
        "http://localhost:3000,http://localhost:3001,"
        "https://predicare-dashboard.web.app,https://predicare-dashboard.firebaseapp.com,"
        "https://predicare-field-app.web.app,https://predicare-field-app.firebaseapp.com"
    )

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

    # Smart redistribution: only propose transfers within this radius (Project Pulse)
    redistribution_max_km: float = 15.0

    # Geofenced staff attendance (Project Pulse Module 1)
    geofence_radius_m: float = 200.0          # metres; check-in within = present
    # Consecutive days of zero attendance before the dashboard escalates.
    attendance_escalation_days: int = 3

    # Gemini
    gemini_api_key: str = ""

    # data.gov.in — real Indian public-health open data (state-level infra).
    # Sample key returns max 10 records; supply a personal key for full pulls.
    data_gov_api_key: str = "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b"
    data_gov_base_url: str = "https://api.data.gov.in/resource"
    # "State/UT-wise Number of Beds at PHC, CHC, SDH, DH and Medical Colleges (2023)"
    data_gov_state_beds_resource_id: str = "d133eac1-143f-4c1d-bdc4-b9dfd73ab78c"

    # Dev-only login bypass: in non-production, this OTP is always accepted for
    # any existing active user (SMS/WhatsApp delivery is not wired up locally).
    # Never honoured when environment == "production".
    dev_login_otp: str = "000000"

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
