"""Escrow deal endpoints and on-chain funding verification."""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.config import get_settings
from app.core.blockchain import get_escrow_contract, get_web3
from app.core.database import get_db
from app.models.models import EscrowDeal, EscrowState, Offer, OfferStatus, User
from app.schemas.escrow import (
    EscrowDealCreate,
    EscrowDealRead,
    EscrowTransactionRead,
    PaginatedEscrowDeals,
    VerifyFundingRequest,
)
from app.services.auth_deps import get_current_user
from app.services import escrow_service, offer_service

logger = logging.getLogger(__name__)

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


STATE_INT_TO_ENUM: dict[int, EscrowState] = {
    0: EscrowState.FUNDED,
    1: EscrowState.CONFIRMED,
    2: EscrowState.RELEASED,
    3: EscrowState.REFUNDED,
    4: EscrowState.CANCELLED,
    5: EscrowState.DISPUTED,
    6: EscrowState.RESOLVED,
}


def format_escrow_transaction(deal: EscrowDeal) -> EscrowTransactionRead:
    req_title = None
    req_category = None
    offer_terms = None
    if deal.offer:
        offer_terms = deal.offer.terms
        if deal.offer.requirement:
            req_title = deal.offer.requirement.title
            req_category = deal.offer.requirement.category

    buyer_name = deal.buyer.display_name if deal.buyer else None
    buyer_wallet = deal.buyer.wallet_address if deal.buyer else None
    seller_name = deal.seller.display_name if deal.seller else None
    seller_wallet = deal.seller.wallet_address if deal.seller else None

    return EscrowTransactionRead(
        id=deal.id,
        offer_id=deal.offer_id,
        buyer_id=deal.buyer_id,
        seller_id=deal.seller_id,
        chain_deal_id=deal.chain_deal_id,
        fund_tx_hash=deal.fund_tx_hash,
        amount_eth=float(deal.amount_eth),
        deadline_ts=deal.deadline_ts,
        agreement_hash=deal.agreement_hash,
        on_chain_state=deal.on_chain_state,
        created_at=deal.created_at,
        updated_at=deal.updated_at,
        requirement_title=req_title,
        requirement_category=req_category,
        offer_terms=offer_terms,
        buyer_display_name=buyer_name,
        buyer_wallet_address=buyer_wallet,
        seller_display_name=seller_name,
        seller_wallet_address=seller_wallet,
    )


@router.get(
    "/deals/buyer",
    response_model=PaginatedEscrowDeals,
    summary="List escrow deals for the authenticated buyer",
)
async def list_buyer_deals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    deal_state: EscrowState | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedEscrowDeals:
    """Return paginated list of escrow transactions where current user is the buyer."""
    query = (
        select(EscrowDeal)
        .where(EscrowDeal.buyer_id == current_user.id)
        .options(
            selectinload(EscrowDeal.offer).selectinload(Offer.requirement),
            selectinload(EscrowDeal.buyer),
            selectinload(EscrowDeal.seller),
        )
        .order_by(EscrowDeal.created_at.desc())
    )
    if deal_state is not None:
        query = query.where(EscrowDeal.on_chain_state == deal_state)

    count_query = select(func.count()).select_from(EscrowDeal).where(EscrowDeal.buyer_id == current_user.id)
    if deal_state is not None:
        count_query = count_query.where(EscrowDeal.on_chain_state == deal_state)
    total = (await db.execute(count_query)).scalar_one()

    items_query = query.offset((page - 1) * page_size).limit(page_size)
    deals = (await db.execute(items_query)).scalars().all()

    return PaginatedEscrowDeals(
        items=[format_escrow_transaction(d) for d in deals],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/deals/seller",
    response_model=PaginatedEscrowDeals,
    summary="List escrow deals for the authenticated seller",
)
async def list_seller_deals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    deal_state: EscrowState | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedEscrowDeals:
    """Return paginated list of escrow transactions where current user is the seller."""
    query = (
        select(EscrowDeal)
        .where(EscrowDeal.seller_id == current_user.id)
        .options(
            selectinload(EscrowDeal.offer).selectinload(Offer.requirement),
            selectinload(EscrowDeal.buyer),
            selectinload(EscrowDeal.seller),
        )
        .order_by(EscrowDeal.created_at.desc())
    )
    if deal_state is not None:
        query = query.where(EscrowDeal.on_chain_state == deal_state)

    count_query = select(func.count()).select_from(EscrowDeal).where(EscrowDeal.seller_id == current_user.id)
    if deal_state is not None:
        count_query = count_query.where(EscrowDeal.on_chain_state == deal_state)
    total = (await db.execute(count_query)).scalar_one()

    items_query = query.offset((page - 1) * page_size).limit(page_size)
    deals = (await db.execute(items_query)).scalars().all()

    return PaginatedEscrowDeals(
        items=[format_escrow_transaction(d) for d in deals],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/deals/{deal_id}/sync",
    response_model=EscrowTransactionRead,
    summary="Synchronize cached deal state with on-chain smart contract",
)
async def sync_deal_state(
    deal_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EscrowTransactionRead:
    """Read the latest on-chain state from getDeal() and update cached on_chain_state."""
    stmt = (
        select(EscrowDeal)
        .where(EscrowDeal.id == deal_id)
        .options(
            selectinload(EscrowDeal.offer).selectinload(Offer.requirement),
            selectinload(EscrowDeal.buyer),
            selectinload(EscrowDeal.seller),
        )
    )
    deal = (await db.execute(stmt)).scalar_one_or_none()
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deal not found.")
    if deal.buyer_id != current_user.id and deal.seller_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    if deal.chain_deal_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deal is not linked to an on-chain deal ID.",
        )

    settings = get_settings()
    if not settings.escrow_contract_address:
        return format_escrow_transaction(deal)

    try:
        w3 = get_web3()
        if not w3.is_connected() or w3.eth.chain_id != settings.chain_id:
            logger.warning("Blockchain node not reachable or chain ID mismatch in sync_deal_state.")
            return format_escrow_transaction(deal)

        contract = get_escrow_contract(w3)
        if not contract:
            return format_escrow_transaction(deal)

        on_chain_raw = contract.functions.getDeal(deal.chain_deal_id).call()
        # Struct: (buyer, seller, amount, deadline, agreementHash, state, fundsWithdrawn)
        (oc_buyer, oc_seller, oc_amount, oc_deadline, oc_ag_hash, oc_state, oc_withdrawn) = on_chain_raw

        # 1. Validate on-chain buyer against database record if buyer wallet is registered
        if deal.buyer and deal.buyer.wallet_address:
            if Web3.to_checksum_address(oc_buyer) != Web3.to_checksum_address(deal.buyer.wallet_address):
                logger.error(
                    "Sync abort: on-chain buyer %s does not match DB buyer %s",
                    oc_buyer, deal.buyer.wallet_address
                )
                return format_escrow_transaction(deal)

        # 2. Validate on-chain seller against database record if seller wallet is registered
        if deal.seller and deal.seller.wallet_address:
            if Web3.to_checksum_address(oc_seller) != Web3.to_checksum_address(deal.seller.wallet_address):
                logger.error(
                    "Sync abort: on-chain seller %s does not match DB seller %s",
                    oc_seller, deal.seller.wallet_address
                )
                return format_escrow_transaction(deal)

        # 3. Validate on-chain amount against deal amount
        expected_wei = int(Decimal(str(deal.amount_eth)) * Decimal("10") ** 18)
        if int(oc_amount) != expected_wei:
            logger.error("Sync abort: on-chain amount %s != expected wei %s", oc_amount, expected_wei)
            return format_escrow_transaction(deal)

        # 4. Validate agreement hash if present
        if deal.agreement_hash:
            raw_ag_hash = ("0x" + oc_ag_hash.hex()) if isinstance(oc_ag_hash, (bytes, bytearray)) else str(oc_ag_hash)
            expected_hash = deal.agreement_hash.lower()
            if not expected_hash.startswith("0x"):
                expected_hash = "0x" + expected_hash
            if raw_ag_hash.lower() != expected_hash:
                logger.error("Sync abort: on-chain agreement hash %s != expected %s", raw_ag_hash, expected_hash)
                return format_escrow_transaction(deal)

        # 5. Map Solidity integer state to EscrowState enum
        if oc_state in STATE_INT_TO_ENUM:
            new_state = STATE_INT_TO_ENUM[oc_state]

            # 6. Terminal state protection: terminal states cannot be downgraded
            TERMINAL_STATES = {
                EscrowState.RELEASED,
                EscrowState.REFUNDED,
                EscrowState.CANCELLED,
                EscrowState.RESOLVED,
            }
            if deal.on_chain_state in TERMINAL_STATES and new_state not in TERMINAL_STATES:
                logger.warning(
                    "Sync rejected: attempt to overwrite terminal state %s with %s for deal %s",
                    deal.on_chain_state, new_state, deal.id
                )
                return format_escrow_transaction(deal)

            if deal.on_chain_state != new_state:
                deal.on_chain_state = new_state
                await db.commit()
                await db.refresh(deal)
    except Exception as exc:
        logger.warning("Blockchain sync query failed for deal %s: %s", deal.id, exc)

    return format_escrow_transaction(deal)


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


@router.get("/{deal_id}", response_model=EscrowTransactionRead, summary="Get escrow deal details")
async def get_deal(
    deal_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EscrowTransactionRead:
    stmt = (
        select(EscrowDeal)
        .where(EscrowDeal.id == deal_id)
        .options(
            selectinload(EscrowDeal.offer).selectinload(Offer.requirement),
            selectinload(EscrowDeal.buyer),
            selectinload(EscrowDeal.seller),
        )
    )
    result = await db.execute(stmt)
    deal = result.scalar_one_or_none()
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deal not found.")
    if deal.buyer_id != current_user.id and deal.seller_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    return format_escrow_transaction(deal)
