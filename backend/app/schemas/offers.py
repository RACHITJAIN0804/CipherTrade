"""Pydantic schemas for Offers."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.models import OfferStatus


class OfferCreate(BaseModel):
    requirement_id: uuid.UUID
    price_eth: float = Field(..., gt=0, description="Price in ETH")
    delivery_deadline: int = Field(..., gt=0, description="Unix timestamp for delivery deadline")
    terms: str = Field(..., min_length=10, max_length=4096)


class OfferUpdate(BaseModel):
    price_eth: Optional[float] = Field(None, gt=0)
    delivery_deadline: Optional[int] = Field(None, gt=0)
    terms: Optional[str] = Field(None, min_length=10, max_length=4096)


class OfferRead(BaseModel):
    id: uuid.UUID
    requirement_id: uuid.UUID
    seller_id: uuid.UUID
    price_eth: float
    delivery_deadline: int
    terms: str
    status: OfferStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaginatedOffers(BaseModel):
    items: list[OfferRead]
    total: int
    page: int
    page_size: int

