"""Pydantic schemas for Escrow deal references."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.models import EscrowState


class EscrowDealCreate(BaseModel):
    offer_id: uuid.UUID
    amount_eth: float = Field(..., gt=0)
    deadline_ts: int = Field(..., gt=0, description="Unix timestamp passed to fundDeal()")
    agreement_hash: Optional[str] = Field(None, max_length=66)


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
    deadline_ts: int
    agreement_hash: Optional[str]
    on_chain_state: Optional[EscrowState]
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


