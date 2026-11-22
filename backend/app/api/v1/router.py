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

from app.schemas.consent import ContractFundingPayloadResponse

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(requirements.router)
api_router.include_router(offers.router)
api_router.include_router(conversations.router)
api_router.include_router(moderation.router)
api_router.include_router(escrow.router)

# Route alias for /api/v1/deals/{deal_id}/contract-payload -> escrow.get_contract_funding_payload
api_router.add_api_route(
    "/deals/{deal_id}/contract-payload",
    escrow.get_contract_funding_payload,
    methods=["GET"],
    response_model=ContractFundingPayloadResponse,
    summary="Get verified dual-consent contract funding payload for fundDealWithConsent (route alias)",
    tags=["escrow"],
)

