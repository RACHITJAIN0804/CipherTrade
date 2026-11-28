"""Pydantic schemas for Requirements (buyer listings)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.models import RequirementStatus
from app.config import get_settings
from app.core.token_registry import get_token_registry
from app.core.token_conversion import to_base_units
from web3 import Web3


class RequirementCreate(BaseModel):
    title: str = Field(..., min_length=5, max_length=256)
    description: str = Field(..., min_length=10, max_length=8192)
    category: str = Field(..., min_length=2, max_length=128)
    keywords: Optional[str] = Field(None, max_length=512)
    budget_min: Optional[float] = Field(None, ge=0)
    budget_max: Optional[float] = Field(None, ge=0)
    currency: Optional[str] = Field("ETH", max_length=10)
    token_address: Optional[str] = None
    token_decimals: Optional[int] = None

    def model_post_init(self, __context) -> None:  # type: ignore[override]
        if self.budget_min is not None and self.budget_max is not None:
            if self.budget_max < self.budget_min:
                raise ValueError("budget_max must be >= budget_min")

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

        # Validate amount precision using registry's token decimals
        if self.budget_min is not None:
            to_base_units(self.budget_min, meta.decimals)
        if self.budget_max is not None:
            to_base_units(self.budget_max, meta.decimals)


class RequirementUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=5, max_length=256)
    description: Optional[str] = Field(None, min_length=10, max_length=8192)
    category: Optional[str] = Field(None, min_length=2, max_length=128)
    keywords: Optional[str] = Field(None, max_length=512)
    budget_min: Optional[float] = Field(None, ge=0)
    budget_max: Optional[float] = Field(None, ge=0)
    currency: Optional[str] = Field(None, max_length=10)
    token_address: Optional[str] = None
    token_decimals: Optional[int] = None
    status: Optional[RequirementStatus] = None

    def model_post_init(self, __context) -> None:  # type: ignore[override]
        if self.budget_min is not None and self.budget_max is not None:
            if self.budget_max < self.budget_min:
                raise ValueError("budget_max must be >= budget_min")
        if self.currency is not None:
            curr = self.currency.strip().upper()
            registry = get_token_registry()
            chain_id = get_settings().chain_id
            meta = registry.get_token(chain_id, curr)
            if self.token_address is not None:
                if not Web3.is_address(self.token_address) or Web3.to_checksum_address(self.token_address) != meta.token_address:
                    raise ValueError("Caller-supplied token_address does not match authoritative registry address")
            if self.token_decimals is not None and self.token_decimals != meta.decimals:
                raise ValueError("Caller-supplied token_decimals does not match authoritative registry decimals")
            if self.budget_min is not None:
                to_base_units(self.budget_min, meta.decimals)
            if self.budget_max is not None:
                to_base_units(self.budget_max, meta.decimals)


class RequirementRead(BaseModel):
    id: uuid.UUID
    buyer_id: uuid.UUID
    title: str
    description: str
    category: str
    keywords: Optional[str]
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    currency: str = "ETH"
    token_address: Optional[str] = None
    token_decimals: Optional[int] = None
    chain_id: Optional[int] = None
    budget_min_base_units: Optional[str] = None
    budget_max_base_units: Optional[str] = None
    status: RequirementStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaginatedRequirements(BaseModel):
    items: list[RequirementRead]
    total: int
    page: int
    page_size: int

