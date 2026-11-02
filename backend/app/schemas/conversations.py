"""Pydantic schemas for Conversation metadata."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ConversationCreate(BaseModel):
    recipient_id: uuid.UUID
    offer_id: Optional[uuid.UUID] = None


class ConversationRead(BaseModel):
    id: uuid.UUID
    initiator_id: uuid.UUID
    recipient_id: uuid.UUID
    offer_id: Optional[uuid.UUID]
    created_at: datetime

    model_config = {"from_attributes": True}

