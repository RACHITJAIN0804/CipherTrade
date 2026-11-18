"""Pydantic schemas for EIP-4361 SIWE Wallet Ownership Verification."""
from __future__ import annotations

from datetime import datetime
import re

from pydantic import BaseModel, Field, field_validator
from web3 import Web3

from app.schemas.users import UserRead


ETH_ADDRESS_REGEX = re.compile(r"^0x[a-fA-F0-9]{40}$")
ETH_SIGNATURE_REGEX = re.compile(r"^0x[a-fA-F0-9]{130}$")


class WalletChallengeRequest(BaseModel):
    wallet_address: str = Field(..., description="Candidate Ethereum wallet address (0x...)")

    @field_validator("wallet_address")
    @classmethod
    def validate_wallet_address(cls, v: str) -> str:
        v = v.strip()
        if not ETH_ADDRESS_REGEX.match(v):
            raise ValueError("Invalid Ethereum wallet address format. Must be a 40-hex-char address starting with '0x'.")
        try:
            return Web3.to_checksum_address(v)
        except Exception as exc:
            raise ValueError(f"Invalid Ethereum address: {exc}")


class WalletChallengeResponse(BaseModel):
    message: str = Field(..., description="EIP-4361 formatted SIWE challenge string")
    nonce: str = Field(..., description="Cryptographically random unique challenge nonce")
    expires_at: datetime = Field(..., description="Expiration timestamp (UTC)")


class WalletVerifyRequest(BaseModel):
    message: str = Field(..., min_length=20, description="Full SIWE challenge message that was signed")
    signature: str = Field(..., min_length=130, max_length=134, description="Hex-encoded ECDSA signature (0x...)")

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("0x"):
            v = f"0x{v}"
        if not ETH_SIGNATURE_REGEX.match(v):
            raise ValueError("Invalid Ethereum signature format. Must be a 130-hex-char signature starting with '0x'.")
        return v


class WalletVerifyResponse(BaseModel):
    success: bool
    wallet_address: str
    user: UserRead

