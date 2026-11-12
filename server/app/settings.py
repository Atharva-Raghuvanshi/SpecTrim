"""Env-driven settings (requirements.md §4)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://app:app@postgres:5432/qo"
    api_bearer_token: str = "change-me"
    groq_api_key: str = ""
    models_config: str = "config/models.yaml"
    active_config: str = "single_model"
    spend_cap_usd_per_day: float = 10.0
    max_cost_usd_per_session: float = 0.50
    max_questions_per_round: int = 3
    lease_seconds: int = 60
    lease_renew_seconds: int = 20
    rate_limit_per_min: int = 30
    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
