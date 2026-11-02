"""Pydantic schemas for Reports and Blocks."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.models import ReportStatus


# ─── Reports ──────────────────────────────────────────────────────────────────

class ReportCreate(BaseModel):
    reported_user_id: uuid.UUID
    reason: str = Field(..., min_length=10, max_length=1024)


class ReportRead(BaseModel):
    id: uuid.UUID
    reporter_id: uuid.UUID
    reported_user_id: uuid.UUID
    reason: str
    status: ReportStatus
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Blocks ───────────────────────────────────────────────────────────────────

class BlockCreate(BaseModel):
    blocked_user_id: uuid.UUID


class BlockRead(BaseModel):
    id: uuid.UUID
    blocker_id: uuid.UUID
    blocked_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}

