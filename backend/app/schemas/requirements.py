"""Pydantic schemas for Requirements (buyer listings)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.models import RequirementStatus


class RequirementCreate(BaseModel):
    title: str = Field(..., min_length=5, max_length=256)
    description: str = Field(..., min_length=10, max_length=8192)
    category: str = Field(..., min_length=2, max_length=128)
    keywords: Optional[str] = Field(None, max_length=512)
    budget_min: Optional[float] = Field(None, ge=0)
    budget_max: Optional[float] = Field(None, ge=0)

    def model_post_init(self, __context) -> None:  # type: ignore[override]
        if self.budget_min is not None and self.budget_max is not None:
            if self.budget_max < self.budget_min:
                raise ValueError("budget_max must be >= budget_min")


class RequirementUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=5, max_length=256)
    description: Optional[str] = Field(None, min_length=10, max_length=8192)
    category: Optional[str] = Field(None, min_length=2, max_length=128)
    keywords: Optional[str] = Field(None, max_length=512)
    budget_min: Optional[float] = Field(None, ge=0)
    budget_max: Optional[float] = Field(None, ge=0)
    status: Optional[RequirementStatus] = None


class RequirementRead(BaseModel):
    id: uuid.UUID
    buyer_id: uuid.UUID
    title: str
    description: str
    category: str
    keywords: Optional[str]
    budget_min: Optional[float]
    budget_max: Optional[float]
    status: RequirementStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaginatedRequirements(BaseModel):
    items: list[RequirementRead]
    total: int
    page: int
    page_size: int

