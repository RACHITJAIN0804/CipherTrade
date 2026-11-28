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


from app.config import get_settings
from app.core.token_registry import get_token_registry
from app.core.token_conversion import to_base_units, from_base_units


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

    curr = (data.currency or (req.currency if req.currency else "ETH")).strip().upper()
    if req.currency and req.currency.strip().upper() != curr:
        raise ValueError(
            f"Offer currency '{curr}' does not match requirement currency '{req.currency}'."
        )

    registry = get_token_registry()
    chain_id = get_settings().chain_id
    meta = registry.get_token(chain_id, curr)

    amt = data.amount if data.amount is not None else (data.price if data.price is not None else data.price_eth)
    if amt is None:
        raise ValueError("Price amount is required.")

    base_units = to_base_units(amt, meta.decimals)
    derived_legacy_price = float(from_base_units(base_units, meta.decimals))

    offer = Offer(
        requirement_id=data.requirement_id,
        seller_id=seller.id,
        price_eth=derived_legacy_price,
        delivery_deadline=data.delivery_deadline,
        terms=data.terms,
        currency=meta.symbol,
        token_address=meta.token_address,
        token_decimals=meta.decimals,
        chain_id=meta.chain_id,
        price_base_units=base_units,
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

