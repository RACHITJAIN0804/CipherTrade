"""Pydantic schemas for cryptographic agreement consent (EIP-712)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ConsentPartyDetail(BaseModel):
    user_id: uuid.UUID
    wallet_address: str
    signed_at: datetime
    chain_id: int
    agreement_hash: str

    model_config = {"from_attributes": True}


class ConsentStatusResponse(BaseModel):
    offer_id: uuid.UUID
    deal_id: Optional[uuid.UUID] = None
    agreement_hash: str
    buyer_consented: bool
    seller_consented: bool
    both_consented: bool
    can_fund: bool
    buyer_consent: Optional[ConsentPartyDetail] = None
    seller_consent: Optional[ConsentPartyDetail] = None


class ConsentChallengeResponse(BaseModel):
    domain: dict[str, Any]
    types: dict[str, list[dict[str, str]]]
    primary_type: str = "AgreementConsent"
    message: dict[str, Any]
    nonce: str
    deadline: int


class ConsentVerifyRequest(BaseModel):
    nonce: str = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
        description="64-character hexadecimal challenge nonce",
    )
    signature: str = Field(
        ...,
        min_length=132,
        max_length=132,
        pattern=r"^0x[0-9a-fA-F]{130}$",
        description="EIP-712 signature (0x + 130 hex characters)",
    )


class ConsentVerifyResponse(BaseModel):
    status: str = "success"
    offer_id: uuid.UUID
    deal_id: Optional[uuid.UUID] = None
    party_role: str
    signer_wallet: str
    agreement_hash: str
    both_consented: bool
    can_fund: bool
