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


# ---------------------------------------------------------------------------
# Phase 5E.2: Contract-Ready Dual-Consent Funding Payload Schemas
# ---------------------------------------------------------------------------

class CommercialAgreementPayload(BaseModel):
    """
    Authoritative commercial terms strictly matching the Solidity CommercialAgreement struct.
    Field order & types:
      - bytes32 agreementHash
      - string offerId
      - string dealId
      - address payable buyer
      - address payable seller
      - uint256 amount (Wei as decimal string)
      - uint256 deliveryDeadline
    """
    agreementHash: str = Field(..., description="Keccak-256 canonical agreement terms hash (0x + 64 hex)")
    offerId: str = Field(..., description="Authoritative marketplace offer UUID string")
    dealId: str = Field(..., description="Authoritative marketplace escrow deal UUID string")
    buyer: str = Field(..., description="Registered buyer checksummed wallet address")
    seller: str = Field(..., description="Registered seller checksummed wallet address")
    amount: str = Field(..., description="Escrow deposit amount in Wei as decimal string to prevent precision loss")
    deliveryDeadline: int = Field(..., description="Unix timestamp deadline for seller delivery")

    model_config = {"from_attributes": True}


class PartyConsentProofPayload(BaseModel):
    """
    Cryptographic consent proof strictly matching the Solidity PartyConsentProof struct.
    Field order & types:
      - string nonce
      - uint256 deadline
      - bytes signature
    """
    nonce: str = Field(..., description="Single-use challenge nonce (64-character hex)")
    deadline: int = Field(..., description="Consent expiration timestamp")
    signature: str = Field(..., description="65-byte ECDSA signature (0x + 130 hex characters)")

    model_config = {"from_attributes": True}


class ContractFundingPayloadResponse(BaseModel):
    """
    Complete contract-ready dual-consent funding payload for fundDealWithConsent.
    """
    deal_id: uuid.UUID = Field(..., description="Authoritative escrow deal UUID")
    agreement: CommercialAgreementPayload = Field(..., description="Authoritative CommercialAgreement parameters")
    buyerConsent: PartyConsentProofPayload = Field(..., description="Buyer's cryptographic consent proof")
    sellerConsent: PartyConsentProofPayload = Field(..., description="Seller's cryptographic consent proof")
    contract_address: Optional[str] = Field(None, description="Configured CipherTradeEscrowV2 contract address (null if not yet deployed)")

    model_config = {"from_attributes": True}

