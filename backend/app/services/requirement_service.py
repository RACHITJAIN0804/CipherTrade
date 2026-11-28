"""
Requirements (buyer listings) service.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Offer, OfferStatus, Requirement, RequirementStatus, User, UserRole
from app.schemas.requirements import RequirementCreate, RequirementUpdate


from app.config import get_settings
from app.core.token_registry import get_token_registry
from app.core.token_conversion import to_base_units


async def create_requirement(
    db: AsyncSession, buyer: User, data: RequirementCreate
) -> Requirement:
    if buyer.primary_role != UserRole.BUYER:
        raise ValueError("Only buyers can post requirements.")

    curr = (data.currency or "ETH").strip().upper()
    registry = get_token_registry()
    meta = registry.get_token(get_settings().chain_id, curr)

    b_min_base = to_base_units(data.budget_min, meta.decimals) if data.budget_min is not None else None
    b_max_base = to_base_units(data.budget_max, meta.decimals) if data.budget_max is not None else None

    req = Requirement(
        buyer_id=buyer.id,
        title=data.title,
        description=data.description,
        category=data.category,
        keywords=data.keywords,
        budget_min=data.budget_min,
        budget_max=data.budget_max,
        currency=meta.symbol,
        token_address=meta.token_address,
        token_decimals=meta.decimals,
        chain_id=meta.chain_id,
        budget_min_base_units=b_min_base,
        budget_max_base_units=b_max_base,
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return req


async def get_requirement(
    db: AsyncSession, requirement_id: uuid.UUID
) -> Requirement | None:
    result = await db.execute(
        select(Requirement).where(Requirement.id == requirement_id)
    )
    return result.scalar_one_or_none()


async def list_requirements(
    db: AsyncSession,
    *,
    q: Optional[str] = None,
    category: Optional[str] = None,
    buyer_id: Optional[uuid.UUID] = None,
    status: RequirementStatus = RequirementStatus.ACTIVE,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Requirement], int]:
    """
    Return paginated requirements with optional filters.

    :param q:        Full-text search on title, description, and keywords.
    :param category: Exact category match.
    :param buyer_id: Filter to a single buyer's requirements.
    :param status:   Defaults to ACTIVE only.
    """
    stmt = select(Requirement).where(Requirement.status == status)

    if buyer_id:
        stmt = stmt.where(Requirement.buyer_id == buyer_id)
    if category:
        stmt = stmt.where(Requirement.category.ilike(f"%{category}%"))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Requirement.title.ilike(pattern),
                Requirement.description.ilike(pattern),
                Requirement.keywords.ilike(pattern),
            )
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    stmt = (
        stmt.order_by(Requirement.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return result.scalars().all(), total  # type: ignore[return-value]


async def update_requirement(
    db: AsyncSession, req: Requirement, data: RequirementUpdate, requester_id: uuid.UUID
) -> Requirement:
    if req.buyer_id != requester_id:
        raise PermissionError("Not the owner of this requirement.")

    # Guard: prevent modifying requirement terms if an offer has already been accepted (Phase 5D.2)
    stmt_accepted = select(Offer).where(
        Offer.requirement_id == req.id,
        Offer.status == OfferStatus.ACCEPTED,
    )
    accepted_offer = (await db.execute(stmt_accepted)).scalar_one_or_none()
    if accepted_offer is not None:
        raise ValueError("Cannot modify requirement terms after an offer has been accepted.")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(req, field, value)
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return req


async def close_requirement(
    db: AsyncSession, req: Requirement, requester_id: uuid.UUID
) -> Requirement:
    if req.buyer_id != requester_id:
        raise PermissionError("Not the owner of this requirement.")
    req.status = RequirementStatus.CLOSED
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return req

