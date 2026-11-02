"""
Offer service: submit, view, accept/reject offers.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Offer,
    OfferStatus,
    Requirement,
    RequirementStatus,
    User,
    UserRole,
)
from app.schemas.offers import OfferCreate, OfferUpdate


async def create_offer(db: AsyncSession, seller: User, data: OfferCreate) -> Offer:
    if seller.primary_role != UserRole.SELLER:
        raise ValueError("Only sellers can submit offers.")

    # Verify the requirement exists and is active
    req_result = await db.execute(
        select(Requirement).where(
            Requirement.id == data.requirement_id,
            Requirement.status == RequirementStatus.ACTIVE,
        )
    )
    req = req_result.scalar_one_or_none()
    if req is None:
        raise ValueError("Requirement not found or not active.")

    # Prevent sellers from offering on their own (edge case)
    if req.buyer_id == seller.id:
        raise ValueError("Cannot offer on your own requirement.")

    offer = Offer(
        requirement_id=data.requirement_id,
        seller_id=seller.id,
        price_eth=data.price_eth,
        delivery_deadline=data.delivery_deadline,
        terms=data.terms,
    )
    db.add(offer)
    await db.commit()
    await db.refresh(offer)
    return offer


async def get_offer(db: AsyncSession, offer_id: uuid.UUID) -> Offer | None:
    result = await db.execute(select(Offer).where(Offer.id == offer_id))
    return result.scalar_one_or_none()


async def list_offers_for_requirement(
    db: AsyncSession,
    requirement_id: uuid.UUID,
    requester_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Offer], int]:
    """
    Return offers on a requirement.  Only the buyer who owns the requirement
    may list all offers; sellers may only list their own.
    """
    stmt = select(Offer).where(Offer.requirement_id == requirement_id)
    # Scope is enforced at the route layer (ownership check).

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(Offer.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return result.scalars().all(), total  # type: ignore[return-value]


async def list_seller_offers(
    db: AsyncSession,
    seller_id: uuid.UUID,
    status: Optional[OfferStatus] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Offer], int]:
    stmt = select(Offer).where(Offer.seller_id == seller_id)
    if status:
        stmt = stmt.where(Offer.status == status)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(Offer.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return result.scalars().all(), total  # type: ignore[return-value]


async def accept_offer(
    db: AsyncSession, offer: Offer, buyer_id: uuid.UUID, req_buyer_id: uuid.UUID
) -> Offer:
    if req_buyer_id != buyer_id:
        raise PermissionError("Only the buyer who owns this requirement can accept an offer.")
    if offer.status != OfferStatus.PENDING:
        raise ValueError(f"Offer is already {offer.status.value}.")
    offer.status = OfferStatus.ACCEPTED
    db.add(offer)
    await db.commit()
    await db.refresh(offer)
    return offer


async def reject_offer(
    db: AsyncSession, offer: Offer, buyer_id: uuid.UUID, req_buyer_id: uuid.UUID
) -> Offer:
    if req_buyer_id != buyer_id:
        raise PermissionError("Only the requirement owner can reject an offer.")
    if offer.status != OfferStatus.PENDING:
        raise ValueError(f"Offer is already {offer.status.value}.")
    offer.status = OfferStatus.REJECTED
    db.add(offer)
    await db.commit()
    await db.refresh(offer)
    return offer


async def withdraw_offer(
    db: AsyncSession, offer: Offer, seller_id: uuid.UUID
) -> Offer:
    if offer.seller_id != seller_id:
        raise PermissionError("Only the offer's seller can withdraw it.")
    if offer.status != OfferStatus.PENDING:
        raise ValueError(f"Offer is already {offer.status.value}.")
    offer.status = OfferStatus.WITHDRAWN
    db.add(offer)
    await db.commit()
    await db.refresh(offer)
    return offer


async def update_offer(
    db: AsyncSession, offer: Offer, seller_id: uuid.UUID, data: OfferUpdate
) -> Offer:
    if offer.seller_id != seller_id:
        raise PermissionError("Only the offer's seller can update it.")
    if offer.status != OfferStatus.PENDING:
        raise ValueError("Can only update a pending offer.")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(offer, field, value)
    db.add(offer)
    await db.commit()
    await db.refresh(offer)
    return offer

