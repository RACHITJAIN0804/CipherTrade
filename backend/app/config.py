from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    environment: str = "development"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/ciphertrade"

    secret_key: str = "CHANGE_ME_IN_PRODUCTION"
    access_token_expire_minutes: int = 60

    cors_origins: List[str] = ["http://localhost:5173"]

    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
