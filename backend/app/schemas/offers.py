"""Pydantic schemas for Offers."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from decimal import Decimal
from pydantic import BaseModel, Field
from web3 import Web3

from app.config import get_settings
from app.core.token_registry import get_token_registry
from app.core.token_conversion import to_base_units, from_base_units
from app.models.models import OfferStatus


class OfferCreate(BaseModel):
    requirement_id: uuid.UUID
    price_eth: Optional[float] = Field(None, gt=0, description="Legacy price field (for ETH or compatibility)")
    amount: Optional[float] = Field(None, gt=0, description="Human-readable token amount")
    price: Optional[float] = Field(None, gt=0, description="Alias for amount")
    currency: Optional[str] = Field("ETH", max_length=10)
    token_address: Optional[str] = None
    token_decimals: Optional[int] = None
    delivery_deadline: int = Field(..., gt=0, description="Unix timestamp for delivery deadline")
    terms: str = Field(..., min_length=10, max_length=4096)

    def model_post_init(self, __context) -> None:  # type: ignore[override]
        amt = self.amount if self.amount is not None else (self.price if self.price is not None else self.price_eth)
        if amt is None:
            raise ValueError("Price amount is required.")

        # If both amount/price and price_eth are passed, verify equality
        if self.price_eth is not None and (self.amount is not None or self.price is not None):
            other = self.amount if self.amount is not None else self.price
            if Decimal(str(self.price_eth)) != Decimal(str(other)):
                raise ValueError("price_eth and amount must match if both are supplied.")

        curr = (self.currency or "ETH").strip().upper()
        registry = get_token_registry()
        chain_id = get_settings().chain_id
        meta = registry.get_token(chain_id, curr)

        # Reject caller-supplied override attempts
        if self.token_address is not None:
            if not Web3.is_address(self.token_address) or Web3.to_checksum_address(self.token_address) != meta.token_address:
                raise ValueError("Caller-supplied token_address does not match authoritative registry address")
        if self.token_decimals is not None and self.token_decimals != meta.decimals:
            raise ValueError(f"Caller-supplied token_decimals ({self.token_decimals}) does not match authoritative registry decimals ({meta.decimals})")

        # Validate precision using registry decimals
        base_units = to_base_units(amt, meta.decimals)

        # Ensure price_eth is populated with exact decimal representation
        derived_legacy = float(from_base_units(base_units, meta.decimals))
        object.__setattr__(self, "price_eth", derived_legacy)
        object.__setattr__(self, "currency", meta.symbol)
        object.__setattr__(self, "token_address", meta.token_address)
        object.__setattr__(self, "token_decimals", meta.decimals)


class OfferUpdate(BaseModel):
    price_eth: Optional[float] = Field(None, gt=0)
    delivery_deadline: Optional[int] = Field(None, gt=0)
    terms: Optional[str] = Field(None, min_length=10, max_length=4096)


class OfferRead(BaseModel):
    id: uuid.UUID
    requirement_id: uuid.UUID
    seller_id: uuid.UUID
    price_eth: float
    currency: str = "ETH"
    token_address: Optional[str] = None
    token_decimals: int = 18
    chain_id: Optional[int] = None
    price_base_units: Optional[str] = None
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

