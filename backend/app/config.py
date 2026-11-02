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

    # Runtime
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/ciphertrade"

    # Auth
    secret_key: str = "CHANGE_ME_IN_PRODUCTION"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # CORS
    cors_origins: List[str] = ["http://localhost:5173"]

    # Blockchain (prepared for future Web3.py integration)
    escrow_contract_address: str = ""
    escrow_contract_abi_path: str = "contracts/out/CipherTradeEscrow.sol/CipherTradeEscrow.json"
    web3_rpc_url: str = "http://127.0.0.1:8545"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
