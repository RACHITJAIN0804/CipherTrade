"""
Escrow verification service.

Interfaces with the CipherTradeEscrow.sol contract via Web3.py to verify
on-chain escrow funding transactions and synchronize deal state.

SECURITY RULES ENFORCED:
  1. A deal is NEVER confirmed solely because the client submits a tx hash.
  2. The backend queries the Ethereum node directly for the receipt and transaction.
  3. Only a mined receipt with status=1 (success) AND verified DealFunded event is accepted.
  4. The transaction sender must match the authenticated buyer's registered wallet address.
  5. The recipient must match the configured escrow contract address.
  6. The event buyer, seller, amount (in wei using Decimal), deadline, and agreement hash
     must strictly match the database deal record.
  7. The contract state is cross-checked using getDeal(dealId).call().
  8. Transactions and on-chain deal IDs must be globally unique across all deals.
  9. Repeated verification of an already-verified deal with the same transaction hash is idempotent.
  10. The chain_deal_id, fund_tx_hash, and on_chain_state=FUNDED are only written
      inside an atomic database commit after all checks pass.
"""
from __future__ import annotations

import logging
import re
import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.config import get_settings
from app.core.blockchain import (
    get_escrow_contract,
    get_escrow_v2_contract,
    get_web3,
    load_contract_abi,
)
from app.models.models import EscrowDeal, EscrowState, Offer, Requirement, User
from app.schemas.escrow import EscrowDealCreate
from app.services import agreement_service

logger = logging.getLogger(__name__)


class ConflictError(ValueError):
    """Raised when a transaction hash or on-chain deal ID conflicts with another deal."""
    pass

TX_HASH_REGEX = re.compile(r"^0x[0-9a-fA-F]{64}$")


def _load_abi() -> list | None:
    """Load the contract ABI using the robust path resolver."""
    return load_contract_abi()


async def create_escrow_deal_record(
    db: AsyncSession,
    buyer_id: uuid.UUID,
    seller_id: uuid.UUID,
    data: EscrowDealCreate,
    offer: Optional[Offer] = None,
    requirement: Optional[Requirement] = None,
) -> EscrowDeal:
    """
    Create the off-chain EscrowDeal row linked to an accepted Offer.
    The agreement hash, amount, and deadline are derived authoritatively from the
    database Offer and Requirement records, rejecting any client-supplied tampering (Phase 5D.2).
    """
    from sqlalchemy.orm import selectinload

    if offer is None:
        stmt_offer = (
            select(Offer)
            .where(Offer.id == data.offer_id)
            .options(selectinload(Offer.requirement))
        )
        offer = (await db.execute(stmt_offer)).scalar_one_or_none()
        if offer is None:
            raise ValueError("Offer not found.")
        requirement = offer.requirement

    if requirement is None:
        stmt_req = select(Requirement).where(Requirement.id == offer.requirement_id)
        requirement = (await db.execute(stmt_req)).scalar_one_or_none()
        if requirement is None:
            raise ValueError("Requirement not found.")

    # 1. Authoritative canonical agreement hash computation (Phase 5D.2)
    _, authoritative_hash = agreement_service.generate_canonical_hash_for_entities(
        offer, requirement
    )

    # 2. Derive authoritative amounts and deadlines from accepted offer
    authoritative_amount = float(offer.price_eth)
    authoritative_deadline = int(offer.delivery_deadline)

    # 3. Explicitly reject unauthorized overrides of authoritative fields (Phase 5D.2)
    if data.agreement_hash is not None and data.agreement_hash.lower() != authoritative_hash.lower():
        raise ValueError(
            "Unauthorized override attempt: client-supplied agreement_hash does not match backend canonical agreement hash."
        )

    if data.amount_eth is not None and abs(data.amount_eth - authoritative_amount) > 1e-8:
        raise ValueError(
            f"Unauthorized override attempt: client-supplied amount_eth ({data.amount_eth}) does not match accepted offer price ({authoritative_amount})."
        )

    if data.deadline_ts is not None and data.deadline_ts != authoritative_deadline:
        raise ValueError(
            f"Unauthorized override attempt: client-supplied deadline_ts ({data.deadline_ts}) does not match accepted offer deadline ({authoritative_deadline})."
        )

    deal = EscrowDeal(
        offer_id=data.offer_id,
        buyer_id=buyer_id,
        seller_id=seller_id,
        amount_eth=authoritative_amount,
        deadline_ts=authoritative_deadline,
        agreement_hash=authoritative_hash,
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
    buyer: User,
) -> EscrowDeal:
    """
    Verify an on-chain funding transaction and update the deal record.

    Performs full independent verification against the Ethereum node.
    """
    settings = get_settings()

    if not settings.escrow_contract_address:
        raise ValueError(
            "Blockchain verification is not configured. "
            "Set ESCROW_CONTRACT_ADDRESS in environment."
        )

    # 1. Validate transaction hash format
    if not isinstance(tx_hash, str) or not TX_HASH_REGEX.match(tx_hash):
        raise ValueError("Invalid transaction hash format. Expected 0x followed by 64 hex characters.")

    clean_tx_hash = tx_hash.lower()

    # 2. Idempotency and state check
    if deal.on_chain_state == EscrowState.FUNDED and deal.fund_tx_hash == clean_tx_hash:
        logger.info("Deal %s already verified with tx %s (idempotent response)", deal.id, clean_tx_hash)
        return deal

    if deal.on_chain_state is not None and deal.on_chain_state != EscrowState.FUNDED:
        raise ValueError(f"Deal is already in state '{deal.on_chain_state.value}' and cannot be funded.")

    if deal.fund_tx_hash and deal.fund_tx_hash != clean_tx_hash:
        raise ValueError("Deal is already funded with a different transaction hash.")

    # 3. Authorization check
    if deal.buyer_id != buyer.id:
        raise PermissionError("Only the buyer who created this deal can verify its funding.")

    if not buyer.wallet_address:
        raise ValueError("Buyer does not have a registered wallet address. Please update your profile with your wallet address.")

    try:
        buyer_wallet = Web3.to_checksum_address(buyer.wallet_address)
    except Exception:
        raise ValueError("Buyer's registered wallet address is invalid.")

    # 4. Duplicate transaction hash check across deals
    stmt = select(EscrowDeal).where(EscrowDeal.fund_tx_hash == clean_tx_hash, EscrowDeal.id != deal.id)
    existing_deal = (await db.execute(stmt)).scalar_one_or_none()
    if existing_deal:
        raise ConflictError(f"Transaction hash is already associated with another escrow deal ({existing_deal.id}).")

    # 5. Connect to blockchain RPC
    w3 = get_web3()
    if not w3.is_connected():
        raise ValueError("Unable to connect to blockchain RPC node.")

    # 6. Verify chain ID
    try:
        current_chain_id = w3.eth.chain_id
    except Exception as exc:
        raise ValueError(f"Failed to query chain ID from RPC: {exc}")

    if current_chain_id != settings.chain_id:
        raise ValueError(f"Chain ID mismatch. Expected {settings.chain_id}, got {current_chain_id}.")

    # 7. Fetch transaction and receipt
    try:
        tx = w3.eth.get_transaction(clean_tx_hash)
    except Exception:
        raise ValueError("Transaction not found on chain.")

    if tx is None or tx.get("blockNumber") is None:
        raise ValueError("Transaction is still pending and has not been mined yet.")

    tx_chain_id = tx.get("chainId") if isinstance(tx, dict) or hasattr(tx, "get") else None
    if isinstance(tx_chain_id, int) and tx_chain_id != settings.chain_id:
        raise ValueError(f"Transaction chain ID mismatch. Expected {settings.chain_id}, got {tx_chain_id}.")

    try:
        receipt = w3.eth.get_transaction_receipt(clean_tx_hash)
    except Exception:
        raise ValueError("Transaction receipt not found on chain.")

    if receipt is None or (receipt.get("blockNumber") if isinstance(receipt, dict) or hasattr(receipt, "get") else getattr(receipt, "blockNumber", None)) is None:
        raise ValueError("Transaction is still pending.")

    # 8. Verify receipt status is success
    receipt_status = receipt.get("status") if isinstance(receipt, dict) or hasattr(receipt, "get") else getattr(receipt, "status", None)
    if receipt_status != 1:
        raise ValueError("Transaction failed on chain (status 0).")

    # 9. Verify destination contract address
    v1_address = Web3.to_checksum_address(settings.escrow_contract_address) if settings.escrow_contract_address else None
    v2_address = Web3.to_checksum_address(settings.escrow_v2_contract_address) if settings.escrow_v2_contract_address else None

    tx_to = tx.get("to") if isinstance(tx, dict) or hasattr(tx, "get") else getattr(tx, "to", None)
    if not tx_to:
        raise ValueError("Transaction destination is missing.")
    tx_to_checksum = Web3.to_checksum_address(tx_to)

    is_v2 = False
    if v2_address and tx_to_checksum == v2_address:
        is_v2 = True
        contract_address = v2_address
    elif v1_address and tx_to_checksum == v1_address:
        is_v2 = False
        contract_address = v1_address
    else:
        raise ValueError("Transaction destination does not match any configured escrow contract address.")

    # Also verify receipt destination contract address if present
    receipt_to = receipt.get("to") if isinstance(receipt, dict) or hasattr(receipt, "get") else getattr(receipt, "to", None)
    if receipt_to and Web3.to_checksum_address(receipt_to) != contract_address:
        raise ValueError("Receipt destination does not match the configured escrow contract address.")

    # 10. Verify transaction sender matches buyer wallet
    raw_from = tx.get("from") if isinstance(tx, dict) or hasattr(tx, "get") else getattr(tx, "from", None)
    if not raw_from:
        raise ValueError("Transaction has no sender.")
    tx_from = Web3.to_checksum_address(raw_from)
    if tx_from != buyer_wallet:
        raise ValueError("Transaction sender does not match the buyer's registered wallet address.")

    # 11. Decode DealFunded event
    if is_v2:
        contract = get_escrow_v2_contract(w3)
    else:
        contract = get_escrow_contract(w3)
    if contract is None:
        raise ValueError("Escrow contract could not be loaded or ABI artifact is missing.")

    try:
        events = contract.events.DealFunded().process_receipt(receipt)
    except Exception as exc:
        raise ValueError(f"Failed to decode events from receipt: {exc}")

    if not events:
        raise ValueError("No DealFunded event emitted in this transaction.")

    event_record = events[0]
    event_args = event_record["args"]

    if Web3.to_checksum_address(event_record["address"]) != contract_address:
        raise ValueError("DealFunded event was not emitted by the configured escrow contract.")

    # 12. Extract on-chain deal ID and ensure uniqueness
    if is_v2:
        chain_deal_id = int(event_args["chainDealId"])
        # Verify deal UUID mapping
        event_deal_uuid = str(event_args.get("dealUuid", ""))
        if event_deal_uuid != str(deal.id):
            raise ValueError(f"Event deal UUID ({event_deal_uuid}) does not match deal ID ({deal.id}).")
        expected_uuid_hash = Web3.keccak(text=str(deal.id))
        event_uuid_hash = event_args.get("dealUuidHash")
        if isinstance(event_uuid_hash, (bytes, bytearray)):
            if event_uuid_hash != expected_uuid_hash:
                raise ValueError("Event dealUuidHash mismatch.")
        elif str(event_uuid_hash).lower() != expected_uuid_hash.hex().lower():
            raise ValueError("Event dealUuidHash mismatch.")
    else:
        chain_deal_id = int(event_args["dealId"])

    stmt_chain = select(EscrowDeal).where(EscrowDeal.chain_deal_id == chain_deal_id, EscrowDeal.id != deal.id)
    other_chain_deal = (await db.execute(stmt_chain)).scalar_one_or_none()
    if other_chain_deal:
        raise ConflictError(f"On-chain deal ID {chain_deal_id} is already linked to another escrow deal ({other_chain_deal.id}).")

    # 13. Verify buyer address in event
    event_buyer = Web3.to_checksum_address(event_args["buyer"])
    if event_buyer != buyer_wallet:
        raise ValueError("Event buyer address does not match buyer's registered wallet.")

    # 14. Verify seller address in event against registered seller wallet
    event_seller = Web3.to_checksum_address(event_args["seller"])
    seller = await db.get(User, deal.seller_id)
    if not seller or not seller.wallet_address:
        raise ValueError(
            "Seller does not have a registered wallet address. "
            "The seller must connect and register their wallet before escrow funding can be verified."
        )
    if Web3.to_checksum_address(seller.wallet_address) != event_seller:
        raise ValueError(
            f"Event seller address ({event_seller}) does not match seller's registered wallet address ({seller.wallet_address})."
        )

    # 15. Verify amount using exact Decimal wei conversion
    expected_wei = int(Decimal(str(deal.amount_eth)) * Decimal(10**18))
    event_amount = int(event_args["amount"])
    if event_amount != expected_wei:
        raise ValueError(f"Amount mismatch. Deal amount is {expected_wei} wei, event has {event_amount} wei.")

    tx_value = int(tx["value"])
    if tx_value != expected_wei:
        raise ValueError(f"Transaction value mismatch. Expected {expected_wei} wei, got {tx_value} wei.")

    # 16. Verify deadline
    if is_v2:
        event_deadline = int(event_args["deliveryDeadline"])
    else:
        event_deadline = int(event_args["deadline"])
    if event_deadline != deal.deadline_ts:
        raise ValueError(f"Deadline mismatch. Deal deadline is {deal.deadline_ts}, event has {event_deadline}.")

    # 17. Verify agreement hash strictly against expected deal agreement_hash
    raw_event_hash = event_args["agreementHash"]
    event_hash_hex = ("0x" + raw_event_hash.hex()) if isinstance(raw_event_hash, (bytes, bytearray)) else str(raw_event_hash)

    # Re-calculate authoritative canonical agreement hash from database records (Phase 5D.2)
    from sqlalchemy.orm import selectinload
    stmt_offer = (
        select(Offer)
        .where(Offer.id == deal.offer_id)
        .options(selectinload(Offer.requirement))
    )
    offer = (await db.execute(stmt_offer)).scalar_one_or_none()
    if offer and offer.requirement:
        _, canonical_hash = agreement_service.generate_canonical_hash_for_entities(
            offer, offer.requirement
        )
        expected_hash = canonical_hash.lower()
        if deal.agreement_hash and deal.agreement_hash.lower() != expected_hash:
            raise ValueError("Deal record agreement hash does not match authoritative canonical agreement hash.")
    elif deal.agreement_hash:
        expected_hash = deal.agreement_hash.lower()
    else:
        raise ValueError(
            "Deal does not have an expected agreement hash recorded in the database. "
            "Verification cannot proceed without an authoritative expected agreement hash."
        )

    if not expected_hash.startswith("0x"):
        expected_hash = "0x" + expected_hash
    if event_hash_hex.lower() != expected_hash:
        raise ValueError(f"Agreement hash mismatch. Expected {expected_hash}, got {event_hash_hex}.")

    # 17.5. Verify cryptographic consent from both buyer and seller (Phase 5D.3)
    from app.services import consent_service
    has_both_consents = await consent_service.check_both_parties_consented(
        db, deal.offer_id, expected_hash
    )
    if not has_both_consents:
        raise PermissionError(
            "Escrow funding cannot be verified: both buyer and seller must cryptographically "
            "sign and consent to the canonical agreement terms prior to funding."
        )

    # 18. Cross-check stored deal on the smart contract getter getDeal(dealId)
    try:
        on_chain_deal = contract.functions.getDeal(chain_deal_id).call()
    except Exception as exc:
        raise ValueError(f"Failed to read deal from contract getDeal(): {exc}")

    if is_v2:
        # V2 Struct: (buyer, seller, amount, deadline, agreementHash, dealUuidHash, state, fundsWithdrawn)
        (oc_buyer, oc_seller, oc_amount, oc_deadline, oc_ag_hash, oc_uuid_hash, oc_state, oc_withdrawn) = on_chain_deal
        expected_uuid_hash = Web3.keccak(text=str(deal.id))
        if isinstance(oc_uuid_hash, (bytes, bytearray)):
            if oc_uuid_hash != expected_uuid_hash:
                raise ValueError("On-chain deal UUID hash does not match.")
        elif str(oc_uuid_hash).lower() != expected_uuid_hash.hex().lower():
            raise ValueError("On-chain deal UUID hash does not match.")
    else:
        # V1 Struct: (buyer, seller, amount, deadline, agreementHash, state, fundsWithdrawn)
        (oc_buyer, oc_seller, oc_amount, oc_deadline, oc_ag_hash, oc_state, oc_withdrawn) = on_chain_deal

    if Web3.to_checksum_address(oc_buyer) != buyer_wallet:
        raise ValueError("On-chain deal buyer does not match.")
    if Web3.to_checksum_address(oc_seller) != event_seller:
        raise ValueError("On-chain deal seller does not match.")
    if int(oc_amount) != expected_wei:
        raise ValueError("On-chain deal amount does not match expected wei.")
    if int(oc_deadline) != deal.deadline_ts:
        raise ValueError("On-chain deal deadline does not match.")
    if oc_state != 0:  # State.FUNDED
        raise ValueError(f"On-chain deal is not in FUNDED state (state={oc_state}).")
    if oc_withdrawn is not False:
        raise ValueError("On-chain deal indicates funds already withdrawn.")

    # 19. All checks passed — commit changes atomically with race-condition guard
    deal.chain_deal_id = chain_deal_id
    deal.fund_tx_hash = clean_tx_hash
    deal.on_chain_state = EscrowState.FUNDED

    try:
        await db.commit()
        await db.refresh(deal)
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Database integrity conflict: the transaction hash or on-chain deal ID is already associated with another deal."
        ) from exc

    logger.info("Successfully verified and marked deal %s as FUNDED (chain_deal_id=%s)", deal.id, chain_deal_id)
    return deal


def get_contract_info() -> dict:
    """Return publicly safe contract metadata for the frontend."""
    settings = get_settings()
    abi = _load_abi()
    return {
        "address": settings.escrow_contract_address or None,
        "rpc_url": settings.web3_rpc_url,
        "chain_id": settings.chain_id,
        "abi": abi,
        "configured": bool(settings.escrow_contract_address and abi),
    }
