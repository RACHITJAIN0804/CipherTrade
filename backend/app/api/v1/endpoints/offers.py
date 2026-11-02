"""Offer endpoints."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import OfferStatus, User, UserRole
from app.schemas.offers import OfferCreate, OfferRead, OfferUpdate, PaginatedOffers
from app.services.auth_deps import get_current_user
from app.services import offer_service, requirement_service

router = APIRouter(prefix="/offers", tags=["offers"])


@router.post(
    "",
    response_model=OfferRead,
    status_code=status.HTTP_201_CREATED,
    summary="Submit an offer on a requirement (seller only)",
)
async def create_offer(
    data: OfferCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OfferRead:
    if current_user.primary_role != UserRole.SELLER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only sellers can submit offers.",
        )
    try:
        offer = await offer_service.create_offer(db, current_user, data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return OfferRead.model_validate(offer)


@router.get("/me", response_model=PaginatedOffers, summary="List my offers (seller)")
async def list_my_offers(
    offer_status: OfferStatus | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedOffers:
    items, total = await offer_service.list_seller_offers(
        db, current_user.id, status=offer_status, page=page, page_size=page_size
    )
    return PaginatedOffers(
        items=[OfferRead.model_validate(o) for o in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/requirement/{requirement_id}",
    response_model=PaginatedOffers,
    summary="List offers on a requirement (buyer only)",
)
async def list_offers_for_requirement(
    requirement_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedOffers:
    req = await requirement_service.get_requirement(db, requirement_id)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found.")
    if req.buyer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the buyer who posted this requirement can view its offers.",
        )
    items, total = await offer_service.list_offers_for_requirement(
        db, requirement_id, current_user.id, page=page, page_size=page_size
    )
    return PaginatedOffers(
        items=[OfferRead.model_validate(o) for o in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{offer_id}", response_model=OfferRead, summary="Get a single offer")
async def get_offer(
    offer_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OfferRead:
    offer = await offer_service.get_offer(db, offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found.")
    # Access: seller or the buyer of the linked requirement
    req = await requirement_service.get_requirement(db, offer.requirement_id)
    if offer.seller_id != current_user.id and (req is None or req.buyer_id != current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    return OfferRead.model_validate(offer)


@router.patch("/{offer_id}", response_model=OfferRead, summary="Update my offer (seller)")
async def update_offer(
    offer_id: uuid.UUID,
    data: OfferUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OfferRead:
    offer = await offer_service.get_offer(db, offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found.")
    try:
        updated = await offer_service.update_offer(db, offer, current_user.id, data)
    except (PermissionError, ValueError) as exc:
        code = status.HTTP_403_FORBIDDEN if isinstance(exc, PermissionError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc))
    return OfferRead.model_validate(updated)


@router.post("/{offer_id}/accept", response_model=OfferRead, summary="Accept an offer (buyer)")
async def accept_offer(
    offer_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OfferRead:
    offer = await offer_service.get_offer(db, offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found.")
    req = await requirement_service.get_requirement(db, offer.requirement_id)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found.")
    try:
        updated = await offer_service.accept_offer(db, offer, current_user.id, req.buyer_id)
    except (PermissionError, ValueError) as exc:
        code = status.HTTP_403_FORBIDDEN if isinstance(exc, PermissionError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc))
    return OfferRead.model_validate(updated)


@router.post("/{offer_id}/reject", response_model=OfferRead, summary="Reject an offer (buyer)")
async def reject_offer(
    offer_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OfferRead:
    offer = await offer_service.get_offer(db, offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found.")
    req = await requirement_service.get_requirement(db, offer.requirement_id)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found.")
    try:
        updated = await offer_service.reject_offer(db, offer, current_user.id, req.buyer_id)
    except (PermissionError, ValueError) as exc:
        code = status.HTTP_403_FORBIDDEN if isinstance(exc, PermissionError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc))
    return OfferRead.model_validate(updated)


@router.post("/{offer_id}/withdraw", response_model=OfferRead, summary="Withdraw my offer (seller)")
async def withdraw_offer(
    offer_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OfferRead:
    offer = await offer_service.get_offer(db, offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found.")
    try:
        updated = await offer_service.withdraw_offer(db, offer, current_user.id)
    except (PermissionError, ValueError) as exc:
        code = status.HTTP_403_FORBIDDEN if isinstance(exc, PermissionError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc))
    return OfferRead.model_validate(updated)

