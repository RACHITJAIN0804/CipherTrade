"""
Escrow verification service.

This service prepares the layer that will interface with the
CipherTradeEscrow.sol contract via Web3.py.

SECURITY RULES (enforced now and when Web3.py is wired):
  1. A deal is NEVER confirmed solely because the frontend submits a tx hash.
  2. The backend queries the Ethereum node directly for the receipt.
  3. Only a receipt with status=1 (success) AND a verified DealFunded event
     is accepted as proof of funding.
  4. The chain_deal_id and on_chain_state are only written after verification.

The contract ABI path and RPC URL are read from environment variables
(ESCROW_CONTRACT_ADDRESS, ESCROW_CONTRACT_ABI_PATH, WEB3_RPC_URL).

Web3.py integration will be added in Phase 5.  This module exposes the
full interface now so downstream code can depend on it without changes.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.models import EscrowDeal, EscrowState, Offer, OfferStatus
from app.schemas.escrow import EscrowDealCreate

logger = logging.getLogger(__name__)


def _load_abi() -> list | None:
    """
    Load the contract ABI from the compiled artefact.

    Returns None when no contract is configured (development without a
    running node).
    """
    settings = get_settings()
    if not settings.escrow_contract_address:
        logger.warning("ESCROW_CONTRACT_ADDRESS not configured; blockchain calls disabled.")
        return None

    abi_path = Path(settings.escrow_contract_abi_path)
    if not abi_path.exists():
        logger.warning("ABI file not found at %s; blockchain calls disabled.", abi_path)
        return None

    with abi_path.open() as f:
        artefact = json.load(f)
    # Foundry output has the ABI at artefact["abi"]
    return artefact.get("abi")


async def create_escrow_deal_record(
    db: AsyncSession,
    buyer_id: uuid.UUID,
    seller_id: uuid.UUID,
    data: EscrowDealCreate,
) -> EscrowDeal:
    """
    Create the off-chain EscrowDeal row linked to an accepted Offer.

    The on_chain_state is left NULL until verify_funding_transaction() confirms
    the transaction on-chain.
    """
    deal = EscrowDeal(
        offer_id=data.offer_id,
        buyer_id=buyer_id,
        seller_id=seller_id,
        amount_eth=data.amount_eth,
        deadline_ts=data.deadline_ts,
        agreement_hash=data.agreement_hash,
        on_chain_state=None,
    )
    db.add(deal)
    await db.commit()
    await db.refresh(deal)
    return deal


async def verify_funding_transaction(
    db: AsyncSession,
    deal: EscrowDeal,
    tx_hash: str,
) -> EscrowDeal:
    """
    Verify a funding transaction and update the deal record.

    Phase 3 stub: raises NotImplementedError until Web3.py is connected.

    When implemented this will:
      1. Call eth_getTransactionReceipt(tx_hash) on the configured RPC node.
      2. Assert receipt.status == 1 (tx succeeded).
      3. Decode the DealFunded event from the logs.
      4. Assert event.buyer == deal.buyer's wallet_address.
      5. Assert event.seller == deal.seller's wallet_address.
      6. Assert event.amount matches deal.amount_eth (in Wei).
      7. Assert event.agreementHash matches deal.agreement_hash.
      8. Write chain_deal_id, fund_tx_hash, and on_chain_state=FUNDED.
    """
    settings = get_settings()
    if not settings.escrow_contract_address:
        raise NotImplementedError(
            "Blockchain verification is not yet configured. "
            "Set ESCROW_CONTRACT_ADDRESS and WEB3_RPC_URL in .env to enable."
        )
    # Web3.py integration goes here in Phase 5.
    raise NotImplementedError("Web3.py integration pending (Phase 5).")


def get_contract_info() -> dict:
    """
    Return publicly safe contract metadata for the frontend.

    The ABI is returned so the frontend can construct call data locally
    (e.g. for MetaMask prompts) without needing a separate endpoint.
    """
    settings = get_settings()
    abi = _load_abi()
    return {
        "address": settings.escrow_contract_address or None,
        "rpc_url": settings.web3_rpc_url,
        "abi": abi,
        "configured": bool(settings.escrow_contract_address and abi),
    }

