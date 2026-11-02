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

