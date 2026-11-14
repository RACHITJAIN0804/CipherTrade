"""Escrow deal endpoints and on-chain funding verification."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import EscrowDeal, OfferStatus, User
from app.schemas.escrow import EscrowDealCreate, EscrowDealRead, VerifyFundingRequest
from app.services.auth_deps import get_current_user
from app.services import escrow_service, offer_service

router = APIRouter(prefix="/escrow", tags=["escrow"])


@router.get("/contract-info", summary="Get deployed contract address and ABI")
async def contract_info(_: User = Depends(get_current_user)) -> dict:
    """
    Return the escrow contract address and ABI for the frontend wallet integration.
    The frontend uses this to construct fundDeal() call data for MetaMask.
    """
    return escrow_service.get_contract_info()


@router.post(
    "/deals",
    response_model=EscrowDealRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an escrow deal record for an accepted offer",
)
async def create_deal(
    data: EscrowDealCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EscrowDealRead:
    # Verify the offer is accepted and the caller is the buyer
    offer = await offer_service.get_offer(db, data.offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found.")
    if offer.status != OfferStatus.ACCEPTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only accepted offers can initiate an escrow deal.",
        )
    from app.services.requirement_service import get_requirement
    req = await get_requirement(db, offer.requirement_id)
    if req is None or req.buyer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the buyer of this requirement can initiate escrow.",
        )

    # Idempotency check: if an escrow deal already exists for this offer, return it
    stmt = select(EscrowDeal).where(EscrowDeal.offer_id == data.offer_id)
    existing_deal = (await db.execute(stmt)).scalar_one_or_none()
    if existing_deal is not None:
        return EscrowDealRead.model_validate(existing_deal)

    deal = await escrow_service.create_escrow_deal_record(
        db,
        buyer_id=current_user.id,
        seller_id=offer.seller_id,
        data=data,
    )
    return EscrowDealRead.model_validate(deal)


@router.get(
    "/deals/by-offer/{offer_id}",
    response_model=EscrowDealRead,
    summary="Get escrow deal record by offer ID",
)
async def get_deal_by_offer(
    offer_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EscrowDealRead:
    stmt = select(EscrowDeal).where(EscrowDeal.offer_id == offer_id)
    deal = (await db.execute(stmt)).scalar_one_or_none()
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escrow deal not found for this offer.")
    if deal.buyer_id != current_user.id and deal.seller_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    return EscrowDealRead.model_validate(deal)


@router.post(
    "/deals/{deal_id}/verify-funding",
    response_model=EscrowDealRead,
    summary="Verify on-chain funding transaction for an escrow deal",
)
async def verify_funding(
    deal_id: uuid.UUID,
    data: VerifyFundingRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EscrowDealRead:
    """
    Independently verify an on-chain fundDeal transaction receipt and event logs.
    Only the buyer can verify funding for their deal.
    """
    result = await db.execute(select(EscrowDeal).where(EscrowDeal.id == deal_id))
    deal = result.scalar_one_or_none()
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deal not found.")

    if deal.buyer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the buyer can verify funding for this deal.",
        )

    try:
        updated_deal = await escrow_service.verify_funding_transaction(
            db=db,
            deal=deal,
            tx_hash=data.tx_hash,
            buyer=current_user,
        )
    except escrow_service.ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return EscrowDealRead.model_validate(updated_deal)


@router.get("/{deal_id}", response_model=EscrowDealRead, summary="Get escrow deal details")
async def get_deal(
    deal_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EscrowDealRead:
    result = await db.execute(select(EscrowDeal).where(EscrowDeal.id == deal_id))
    deal = result.scalar_one_or_none()
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deal not found.")
    if deal.buyer_id != current_user.id and deal.seller_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    return EscrowDealRead.model_validate(deal)
