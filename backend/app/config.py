"""Application settings, loaded from environment variables and validated at startup."""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore")

    # --- general -----------------------------------------------------------
    app_name: str = "DB Pulse"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    # --- secrets (no defaults on purpose: startup fails if they are missing) ---
    secret_key: str = Field(min_length=32)
    encryption_key: str

    # --- internal MongoDB (DB Pulse's own metadata store) ------------------
    internal_mongo_uri: str
    internal_db_name: str = "dbpulse_internal"

    # --- demo mode: the *monitored* MongoDB that receives the demo dataset -----
    demo_mongo_uri: str = ""
    demo_db_name: str = "dbpulse_demo"

    # --- auth ---------------------------------------------------------------
    access_token_expire_minutes: int = Field(default=60, ge=1, le=1440)
    jwt_algorithm: str = "HS256"
    rate_limit_per_minute: int = Field(default=240, ge=1)
    auth_rate_limit_per_minute: int = Field(default=10, ge=1)

    # --- monitored-instance connection defaults ----------------------------
    mongo_connect_timeout_ms: int = Field(default=5000, ge=500)
    mongo_server_selection_timeout_ms: int = Field(default=5000, ge=500)

    # --- benchmark safety limits -------------------------------------------
    benchmark_max_concurrency: int = Field(default=16, ge=1)
    benchmark_max_iterations: int = Field(default=20000, ge=1)
    benchmark_max_duration_s: int = Field(default=300, ge=1)
    benchmark_op_timeout_ms: int = Field(default=30000, ge=100)
    benchmark_high_load_iterations: int = Field(default=2000, ge=1)
    benchmark_high_load_concurrency: int = Field(default=8, ge=1)

    # --- AI -----------------------------------------------------------------
    ai_provider: Literal["cohere", "local"] = "local"
    cohere_api_key: str = ""
    cohere_model: str = ""
    ai_timeout_s: int = Field(default=30, ge=1)

    # --- background work ----------------------------------------------------
    enable_inprocess_scheduler: bool = False
    scheduler_tick_s: int = Field(default=15, ge=1)
    celery_broker_url: str = "redis://localhost:6379/0"

    @field_validator("encryption_key")
    @classmethod
    def _valid_fernet_key(cls, v: str) -> str:
        try:
            raw = base64.urlsafe_b64decode(v.encode())
            if len(raw) != 32:
                raise ValueError
            Fernet(v.encode())
        except Exception as exc:
            raise ValueError(
                "ENCRYPTION_KEY must be a urlsafe base64 32-byte Fernet key. "
                "Generate one with: python scripts/init_env.py"
            ) from exc
        return v

    @model_validator(mode="after")
    def _production_checks(self) -> Settings:
        if self.environment == "production" and "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS must not contain '*' in production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cohere_configured(self) -> bool:
        return bool(self.cohere_api_key and self.cohere_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
