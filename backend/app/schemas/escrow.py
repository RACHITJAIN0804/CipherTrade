"""Pydantic schemas for Escrow deal references."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.models import EscrowState


class EscrowDealCreate(BaseModel):
    offer_id: uuid.UUID
    amount_eth: Optional[float] = Field(None, gt=0)
    amount: Optional[float] = Field(None, gt=0)
    amount_base_units: Optional[str] = None
    currency: Optional[str] = None
    token_address: Optional[str] = None
    token_decimals: Optional[int] = None
    contract_version: Optional[str] = None
    deadline_ts: Optional[int] = Field(None, gt=0, description="Unix timestamp passed to fundDeal()")
    agreement_hash: Optional[str] = Field(None, max_length=66)


class CanonicalAgreementResponse(BaseModel):
    deal_id: Optional[uuid.UUID] = None
    offer_id: uuid.UUID
    agreement_hash: str = Field(..., pattern=r"^0x[0-9a-fA-F]{64}$")
    canonical_agreement: dict[str, Any]
    schema_version: str


class VerifyFundingRequest(BaseModel):
    tx_hash: str = Field(
        ...,
        min_length=66,
        max_length=66,
        pattern=r"^0x[0-9a-fA-F]{64}$",
        description="Ethereum transaction hash of the fundDeal() invocation (0x + 64 hex characters)",
    )


class EscrowDealRead(BaseModel):
    id: uuid.UUID
    offer_id: uuid.UUID
    buyer_id: uuid.UUID
    seller_id: uuid.UUID
    chain_deal_id: Optional[int]
    fund_tx_hash: Optional[str]
    amount_eth: float
    chain_id: Optional[int] = None
    token_address: Optional[str] = None
    token_symbol: str = "ETH"
    token_decimals: int = 18
    amount_base_units: Optional[str] = None
    fee_bps: Optional[int] = None
    treasury_address: Optional[str] = None
    agreement_version: int = 1
    previous_agreement_hash: Optional[str] = None
    deadline_ts: int
    agreement_hash: Optional[str]
    on_chain_state: Optional[EscrowState]
    contract_address: Optional[str] = None
    contract_version: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EscrowTransactionRead(EscrowDealRead):
    requirement_title: Optional[str] = None
    requirement_category: Optional[str] = None
    offer_terms: Optional[str] = None
    buyer_display_name: Optional[str] = None
    buyer_wallet_address: Optional[str] = None
    seller_display_name: Optional[str] = None
    seller_wallet_address: Optional[str] = None


class PaginatedEscrowDeals(BaseModel):
    items: list[EscrowTransactionRead]
    total: int
    page: int
    page_size: int


