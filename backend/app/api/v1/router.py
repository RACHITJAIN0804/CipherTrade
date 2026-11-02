"""Aggregate all v1 endpoint routers."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    conversations,
    escrow,
    moderation,
    offers,
    requirements,
    users,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(requirements.router)
api_router.include_router(offers.router)
api_router.include_router(conversations.router)
api_router.include_router(moderation.router)
api_router.include_router(escrow.router)

