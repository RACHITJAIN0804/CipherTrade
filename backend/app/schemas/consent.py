"""Pydantic schemas for cryptographic agreement consent (EIP-712)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional, Union

from pydantic import BaseModel, Field, field_validator


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

    @property
    def typed_data(self) -> dict[str, Any]:
        """Return the complete EIP-712 structured data dict for signable messages."""
        return {
            "types": self.types,
            "primaryType": self.primary_type,
            "domain": self.domain,
            "message": self.message,
        }

    @property
    def challenge_id(self) -> str:
        """Alias nonce as challenge_id for convenience in client/test abstractions."""
        return self.nonce


class ConsentVerifyRequest(BaseModel):
    nonce: Optional[str] = Field(
        None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
        description="64-character hexadecimal challenge nonce",
    )
    challenge_id: Optional[str] = None
    signature: str = Field(
        ...,
        min_length=132,
        max_length=132,
        pattern=r"^0x[0-9a-fA-F]{130}$",
        description="EIP-712 signature (0x + 130 hex characters)",
    )

    @field_validator("signature", mode="before")
    @classmethod
    def normalize_signature(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_str = v.strip()
            if len(v_str) == 130 and not v_str.startswith("0x"):
                return "0x" + v_str
        return v

    def model_post_init(self, __context: Any) -> None:
        if not self.nonce and self.challenge_id:
            self.nonce = self.challenge_id
        if not self.nonce:
            raise ValueError("Challenge nonce (or challenge_id) is required.")


class ConsentVerifyResponse(BaseModel):
    status: str = "success"
    verified: bool = True
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
    Authoritative commercial terms strictly matching the Solidity CommercialAgreement struct (V2).
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

    @property
    def amount_wei(self) -> int:
        return int(self.amount)

    model_config = {"from_attributes": True}


class CommercialAgreementV3Payload(BaseModel):
    """
    Authoritative commercial terms strictly matching the Solidity CommercialAgreementV3 struct.
    Field order & types:
      - bytes32 agreementHash
      - bytes32 previousAgreementHash
      - uint256 agreementVersion
      - string offerId
      - string dealId
      - address buyer
      - address seller
      - address token
      - uint256 amount
      - uint256 feeBps
      - address treasury
      - uint256 deliveryDeadline
    """
    agreementHash: str = Field(..., description="Keccak-256 canonical agreement terms hash (0x + 64 hex)")
    previousAgreementHash: str = Field(..., description="Prior agreement hash or 32 zero bytes")
    agreementVersion: int = Field(1, ge=1, description="Agreement version (starts at 1)")
    offerId: str = Field(..., description="Authoritative marketplace offer UUID string")
    dealId: str = Field(..., description="Authoritative marketplace escrow deal UUID string")
    buyer: str = Field(..., description="Registered buyer checksummed wallet address")
    seller: str = Field(..., description="Registered seller checksummed wallet address")
    token: str = Field(..., description="Token contract address or zero address sentinel for ETH")
    amount: str = Field(..., description="Escrow deposit amount in base units as decimal string")
    feeBps: int = Field(..., ge=0, le=500, description="Platform fee in basis points")
    treasury: str = Field(..., description="Platform treasury fee recipient address")
    deliveryDeadline: int = Field(..., description="Unix timestamp deadline for seller delivery")

    @property
    def fee_bps(self) -> int:
        return self.feeBps

    @field_validator("amount", mode="after")
    @classmethod
    def wrap_amount(cls, v: Any) -> Any:
        from app.core.token_conversion import BaseUnitStr
        return BaseUnitStr(str(v))

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
    agreement: Union[CommercialAgreementV3Payload, CommercialAgreementPayload] = Field(
        ..., description="Authoritative CommercialAgreement parameters"
    )
    buyerConsent: PartyConsentProofPayload = Field(..., description="Buyer's cryptographic consent proof")
    sellerConsent: PartyConsentProofPayload = Field(..., description="Seller's cryptographic consent proof")
    contract_address: Optional[str] = Field(None, description="Configured escrow contract address")
    contract_version: Optional[str] = Field("v2", description="Target escrow contract version ('v2' or 'v3')")

    @property
    def commercial_agreement(self) -> Union[CommercialAgreementV3Payload, CommercialAgreementPayload]:
        return self.agreement

    model_config = {"from_attributes": True}

