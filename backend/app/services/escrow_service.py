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
    get_escrow_v3_contract,
    get_web3,
    load_contract_abi,
    load_v3_contract_abi,
)
from app.core.token_conversion import from_base_units, to_base_units
from app.core.token_registry import ETH_SENTINEL_ADDRESS, get_token_registry
from app.models.models import EscrowDeal, EscrowState, Offer, OfferStatus, Requirement, User
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


def resolve_contract_version(
    currency: str,
    explicit_version: Optional[str] = None,
    deal_version: Optional[str] = None,
) -> str:
    """
    Authoritatively resolve contract version (v1, v2, v3) for CipherTrade deals.

    Invariants enforced:
      1. Preserved deal identity: If deal_version is already persisted, never reclassify.
      2. Explicit operator/caller intent: If explicit_version is 'v1' or 'v2', preserve it.
      3. Multi-token asset requirement: All non-ETH (ERC-20) deals strictly require V3.
      4. Explicit V3 selection: If explicit_version is 'v3', route to V3.
      5. Configured V3 deployment: If escrow_v3_contract_address is set, new unversioned ETH deals route to V3.
      6. Configured V2 deployment: If escrow_v2_contract_address is set, route to V2.
      7. Default fallback: V1.
    """
    if deal_version:
        return deal_version.lower()

    curr = (currency or "ETH").strip().upper()
    exp = explicit_version.lower() if explicit_version else None

    if exp in ("v1", "v2"):
        if curr != "ETH":
            raise ValueError(f"ERC20 token '{curr}' is not supported on legacy {exp.upper()} contracts.")
        return exp

    if curr != "ETH" or exp == "v3":
        return "v3"

    settings = get_settings()
    has_v3 = bool(
        getattr(settings, "escrow_v3_contract_address", None)
        or getattr(settings, "anvil_escrow_v3_contract_address", None)
    )
    if has_v3:
        return "v3"
    elif getattr(settings, "escrow_v2_contract_address", None):
        return "v2"
    return "v1"


async def get_or_create_draft_deal(
    db: AsyncSession,
    offer_id: uuid.UUID,
    requesting_user: Optional[User] = None,
    explicit_version: Optional[str] = None,
) -> EscrowDeal:
    """
    Retrieve an existing EscrowDeal or create a stable V3/V2/V1 draft deal for an accepted offer.
    Guarantees that a draft deal has exactly one persisted UUID before consent is issued or signed.
    Enforces idempotency and concurrency protection against duplicate deal creation.
    """
    from sqlalchemy.orm import selectinload

    stmt_offer = (
        select(Offer)
        .where(Offer.id == offer_id)
        .options(
            selectinload(Offer.requirement).selectinload(Requirement.buyer),
            selectinload(Offer.seller),
            selectinload(Offer.escrow_deal),
        )
    )
    offer = (await db.execute(stmt_offer)).scalar_one_or_none()
    if offer is None:
        raise ValueError("Offer not found.")

    if offer.status != OfferStatus.ACCEPTED:
        raise ValueError("Only accepted offers can initiate or participate in escrow.")

    requirement = offer.requirement
    if requirement is None:
        raise ValueError("Parent requirement for offer not found.")

    buyer = requirement.buyer
    seller = offer.seller
    if buyer is None or seller is None:
        raise ValueError("Buyer or seller account not found.")

    if requesting_user is not None:
        if requesting_user.id != buyer.id and requesting_user.id != seller.id:
            raise PermissionError("Access denied: You are neither the buyer nor the seller of this offer.")

    # Idempotency: return existing deal if one has already been created for this offer
    stmt_existing = select(EscrowDeal).where(EscrowDeal.offer_id == offer.id)
    existing_deal = (await db.execute(stmt_existing)).scalar_one_or_none()
    if existing_deal is not None:
        return existing_deal

    settings = get_settings()
    currency = (offer.currency or getattr(offer, "token_symbol", None) or "ETH").strip().upper()
    chain_id = getattr(offer, "chain_id", None) or settings.chain_id

    contract_version = resolve_contract_version(
        currency=currency,
        explicit_version=explicit_version or getattr(offer, "contract_version", None),
    )
    is_v3 = (contract_version == "v3")

    # Resolve token metadata authoritatively
    registry = get_token_registry()
    if is_v3:
        meta = registry.ensure_verified_for_funding(chain_id, currency)
        v3_contract_address = registry.get_v3_contract_address(chain_id)
        fee_bps = int(getattr(settings, "platform_fee_bps", 250))
        raw_treasury = getattr(settings, "treasury_address", None) or "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"
        treasury_address = Web3.to_checksum_address(raw_treasury)
        decimals = meta.decimals
        token_address = meta.token_address
    else:
        meta = None
        v3_contract_address = None
        fee_bps = None
        treasury_address = None
        decimals = getattr(offer, "token_decimals", None) or 18
        token_address = getattr(offer, "token_address", None) or ETH_SENTINEL_ADDRESS

    # Authoritative base units & legacy amounts
    if offer.price_base_units:
        authoritative_base_units = str(offer.price_base_units)
    else:
        authoritative_base_units = str(to_base_units(offer.price_eth, decimals))

    if offer.price_eth is not None:
        authoritative_amount = float(offer.price_eth)
    else:
        authoritative_amount = float(from_base_units(authoritative_base_units, decimals))

    authoritative_deadline = int(offer.delivery_deadline)

    if is_v3:
        buyer_wallet = Web3.to_checksum_address(buyer.wallet_address) if buyer and buyer.wallet_address else None
        seller_wallet = Web3.to_checksum_address(seller.wallet_address) if seller and seller.wallet_address else None
        if not buyer_wallet or not seller_wallet:
            raise ValueError(
                "Both buyer and seller must have verified wallet addresses linked before agreement consent can be generated."
            )

        draft_deal_id = uuid.uuid4()
        canonical_hash = agreement_service.hash_v3_agreement_terms(
            deal_id=str(draft_deal_id),
            agreement_version=1,
            previous_agreement_hash="0x" + "0" * 64,
            offer_id=str(offer.id),
            buyer_address=buyer_wallet,
            seller_address=seller_wallet,
            token_address=token_address,
            amount_base_units=int(authoritative_base_units),
            fee_bps=fee_bps,
            treasury_address=treasury_address,
            delivery_deadline=authoritative_deadline,
        )

        deal = EscrowDeal(
            id=draft_deal_id,
            offer_id=offer.id,
            buyer_id=buyer.id,
            seller_id=seller.id,
            amount_eth=authoritative_amount,
            deadline_ts=authoritative_deadline,
            agreement_hash=canonical_hash,
            chain_id=chain_id,
            token_address=token_address,
            token_symbol=meta.symbol,
            token_decimals=decimals,
            amount_base_units=str(authoritative_base_units),
            fee_bps=fee_bps,
            treasury_address=treasury_address,
            agreement_version=1,
            previous_agreement_hash="0x0000000000000000000000000000000000000000000000000000000000000000",
            contract_version="v3",
            contract_address=v3_contract_address,
            on_chain_state=None,
        )
    else:
        _, authoritative_hash = agreement_service.generate_canonical_hash_for_entities(
            offer, requirement
        )
        deal = EscrowDeal(
            offer_id=offer.id,
            buyer_id=buyer.id,
            seller_id=seller.id,
            amount_eth=authoritative_amount,
            deadline_ts=authoritative_deadline,
            agreement_hash=authoritative_hash,
            contract_version=contract_version,
            on_chain_state=None,
        )

    db.add(deal)
    try:
        await db.commit()
        await db.refresh(deal)
        return deal
    except IntegrityError:
        await db.rollback()
        # Concurrent creation race: fetch existing persisted record
        stmt_winner = select(EscrowDeal).where(EscrowDeal.offer_id == offer.id)
        deal = (await db.execute(stmt_winner)).scalar_one()
        return deal


async def create_escrow_deal_record(
    db: AsyncSession,
    buyer_id: uuid.UUID,
    seller_id: uuid.UUID,
    data: EscrowDealCreate,
    offer: Optional[Offer] = None,
    requirement: Optional[Requirement] = None,
) -> EscrowDeal:
    """
    Create or retrieve the off-chain EscrowDeal row linked to an accepted Offer.
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

    # Idempotency check: if an escrow deal already exists, validate parameters and return it
    stmt_existing = select(EscrowDeal).where(EscrowDeal.offer_id == data.offer_id)
    existing_deal = (await db.execute(stmt_existing)).scalar_one_or_none()
    if existing_deal is not None:
        if data.currency is not None and data.currency.strip().upper() != existing_deal.token_symbol:
            raise ValueError(
                f"Unauthorized override attempt: client-supplied currency ({data.currency}) does not match existing deal currency ({existing_deal.token_symbol})."
            )
        if data.token_address is not None and existing_deal.token_address:
            if Web3.to_checksum_address(data.token_address) != Web3.to_checksum_address(existing_deal.token_address):
                raise ValueError("Unauthorized override attempt: client-supplied token_address does not match existing deal.")
        if data.amount_base_units is not None and existing_deal.amount_base_units:
            if str(data.amount_base_units) != str(existing_deal.amount_base_units):
                raise ValueError("Unauthorized override attempt: client-supplied amount_base_units does not match existing deal.")
        if data.agreement_hash is not None and existing_deal.agreement_hash:
            if data.agreement_hash.lower() != existing_deal.agreement_hash.lower():
                raise ValueError("Unauthorized override attempt: client-supplied agreement_hash does not match existing deal.")
        if data.deadline_ts is not None and data.deadline_ts != existing_deal.deadline_ts:
            raise ValueError("Unauthorized override attempt: client-supplied deadline_ts does not match existing deal.")
        return existing_deal

    settings = get_settings()
    currency = (offer.currency or getattr(offer, "token_symbol", None) or "ETH").strip().upper()
    chain_id = getattr(offer, "chain_id", None) or settings.chain_id

    contract_version = resolve_contract_version(
        currency=currency,
        explicit_version=getattr(data, "contract_version", None) or getattr(offer, "contract_version", None),
    )
    is_v3 = (contract_version == "v3")

    # Token metadata resolution and funding authorization
    registry = get_token_registry()
    if is_v3:
        meta = registry.ensure_verified_for_funding(chain_id, currency)
        v3_contract_address = registry.get_v3_contract_address(chain_id)
        fee_bps = int(getattr(settings, "platform_fee_bps", 250))
        raw_treasury = getattr(settings, "treasury_address", None) or "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"
        treasury_address = Web3.to_checksum_address(raw_treasury)
        decimals = meta.decimals
        token_address = meta.token_address
    else:
        meta = None
        v3_contract_address = None
        fee_bps = None
        treasury_address = None
        decimals = getattr(offer, "token_decimals", None) or 18
        token_address = getattr(offer, "token_address", None) or ETH_SENTINEL_ADDRESS

    # 1. Authoritative base units & legacy amounts
    if offer.price_base_units:
        authoritative_base_units = str(offer.price_base_units)
    else:
        authoritative_base_units = str(to_base_units(offer.price_eth, decimals))

    if offer.price_eth is not None:
        authoritative_amount = float(offer.price_eth)
    else:
        authoritative_amount = float(from_base_units(authoritative_base_units, decimals))

    authoritative_deadline = int(offer.delivery_deadline)

    # 2. Canonical agreement hash computation
    if is_v3:
        deal_id = uuid.uuid4()
        buyer = await db.get(User, buyer_id)
        seller = await db.get(User, seller_id)
        buyer_wallet = Web3.to_checksum_address(buyer.wallet_address) if buyer and buyer.wallet_address else None
        seller_wallet = Web3.to_checksum_address(seller.wallet_address) if seller and seller.wallet_address else None
        if not buyer_wallet or not seller_wallet:
            raise ValueError("Both buyer and seller must have verified wallet addresses linked before agreement consent can be generated.")

        canonical_hash = agreement_service.hash_v3_agreement_terms(
            deal_id=str(deal_id),
            agreement_version=1,
            previous_agreement_hash="0x" + "0" * 64,
            offer_id=str(offer.id),
            buyer_address=buyer_wallet,
            seller_address=seller_wallet,
            token_address=token_address,
            amount_base_units=int(authoritative_base_units),
            fee_bps=fee_bps,
            treasury_address=treasury_address,
            delivery_deadline=authoritative_deadline,
        )
    else:
        deal_id = None
        _, authoritative_hash = agreement_service.generate_canonical_hash_for_entities(
            offer, requirement
        )
        canonical_hash = authoritative_hash

    # 3. Explicitly reject unauthorized overrides of authoritative fields
    if data.agreement_hash is not None and data.agreement_hash.lower() != canonical_hash.lower():
        raise ValueError(
            "Unauthorized override attempt: client-supplied agreement_hash does not match backend canonical agreement hash."
        )

    if data.deadline_ts is not None and data.deadline_ts != authoritative_deadline:
        raise ValueError(
            f"Unauthorized override attempt: client-supplied deadline_ts ({data.deadline_ts}) does not match accepted offer deadline ({authoritative_deadline})."
        )

    if data.currency is not None and data.currency.strip().upper() != currency:
        raise ValueError(
            f"Unauthorized override attempt: client-supplied currency ({data.currency}) does not match accepted offer currency ({currency})."
        )

    if data.token_address is not None:
        if Web3.to_checksum_address(data.token_address) != Web3.to_checksum_address(token_address):
            raise ValueError(
                f"Unauthorized override attempt: client-supplied token_address ({data.token_address}) does not match authoritative token address ({token_address})."
            )

    if data.token_decimals is not None and data.token_decimals != decimals:
        raise ValueError(
            f"Unauthorized override attempt: client-supplied token_decimals ({data.token_decimals}) does not match authoritative token decimals ({decimals})."
        )

    if data.amount_base_units is not None:
        if str(data.amount_base_units) != str(authoritative_base_units):
            raise ValueError(
                f"Unauthorized override attempt: client-supplied amount_base_units ({data.amount_base_units}) does not match accepted offer price ({authoritative_base_units})."
            )

    if data.amount_eth is not None:
        supplied_bu = to_base_units(data.amount_eth, decimals)
        if supplied_bu != int(authoritative_base_units):
            raise ValueError(
                f"Unauthorized override attempt: client-supplied amount_eth ({data.amount_eth}) does not match accepted offer price ({authoritative_amount})."
            )

    if data.amount is not None:
        supplied_bu = to_base_units(data.amount, decimals)
        if supplied_bu != int(authoritative_base_units):
            raise ValueError(
                f"Unauthorized override attempt: client-supplied amount ({data.amount}) does not match accepted offer price ({authoritative_amount})."
            )

    if is_v3:
        deal = EscrowDeal(
            id=deal_id,
            offer_id=data.offer_id,
            buyer_id=buyer_id,
            seller_id=seller_id,
            amount_eth=authoritative_amount,
            deadline_ts=authoritative_deadline,
            agreement_hash=canonical_hash,
            chain_id=chain_id,
            token_address=token_address,
            token_symbol=meta.symbol,
            token_decimals=decimals,
            amount_base_units=str(authoritative_base_units),
            fee_bps=fee_bps,
            treasury_address=treasury_address,
            agreement_version=1,
            previous_agreement_hash="0x0000000000000000000000000000000000000000000000000000000000000000",
            contract_version="v3",
            contract_address=v3_contract_address,
            on_chain_state=None,
        )
    else:
        deal = EscrowDeal(
            offer_id=data.offer_id,
            buyer_id=buyer_id,
            seller_id=seller_id,
            amount_eth=authoritative_amount,
            deadline_ts=authoritative_deadline,
            agreement_hash=canonical_hash,
            contract_version=contract_version,
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

    if not (settings.escrow_contract_address or settings.escrow_v2_contract_address or settings.escrow_v3_contract_address or getattr(settings, "anvil_escrow_v3_contract_address", None)):
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
    v3_address = None
    try:
        registry = get_token_registry()
        v3_address = registry.get_v3_contract_address(settings.chain_id)
    except Exception:
        if getattr(settings, "escrow_v3_contract_address", None):
            v3_address = Web3.to_checksum_address(settings.escrow_v3_contract_address)

    tx_to = tx.get("to") if isinstance(tx, dict) or hasattr(tx, "get") else getattr(tx, "to", None)
    if not tx_to:
        raise ValueError("Transaction destination is missing.")
    tx_to_checksum = Web3.to_checksum_address(tx_to)

    is_v3 = False
    is_v2 = False
    if v3_address and tx_to_checksum == v3_address:
        is_v3 = True
        contract_address = v3_address
        contract_version = "v3"
    elif v2_address and tx_to_checksum == v2_address:
        is_v2 = True
        contract_address = v2_address
        contract_version = "v2"
    elif v1_address and tx_to_checksum == v1_address:
        is_v2 = False
        contract_address = v1_address
        contract_version = "v1"
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
    if is_v3:
        contract = get_escrow_v3_contract(w3, contract_address)
    elif is_v2:
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
    if is_v3 or is_v2:
        chain_deal_id = int(event_args["chainDealId"])
        # Verify deal UUID mapping
        if is_v2:
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

    # 14.5. Additional V3 Event Argument Checks
    if is_v3:
        event_token = Web3.to_checksum_address(event_args["token"])
        expected_token = Web3.to_checksum_address(deal.token_address or ETH_SENTINEL_ADDRESS)
        if event_token != expected_token:
            raise ValueError(f"Event token mismatch. Expected {expected_token}, got {event_token}.")

        if deal.fee_bps is not None and int(event_args["feeBps"]) != deal.fee_bps:
            raise ValueError(f"Fee BPS mismatch. Expected {deal.fee_bps}, got {event_args['feeBps']}.")

        if deal.treasury_address is not None:
            if Web3.to_checksum_address(event_args["treasury"]) != Web3.to_checksum_address(deal.treasury_address):
                raise ValueError(f"Event treasury mismatch. Expected {deal.treasury_address}, got {event_args['treasury']}.")

        expected_ag_version = getattr(deal, "agreement_version", 1) or 1
        if int(event_args.get("agreementVersion", 0)) != expected_ag_version:
            raise ValueError(f"Event agreementVersion mismatch. Expected {expected_ag_version}, got {event_args.get('agreementVersion')}.")
    else:
        expected_token = Web3.to_checksum_address(ETH_SENTINEL_ADDRESS)

    # 15. Verify amount using exact base units (removing hardcoded 10**18 Decimal math)
    expected_base_units = int(deal.amount_base_units) if deal.amount_base_units else int(Decimal(str(deal.amount_eth)) * Decimal(10**18))
    event_amount = int(event_args["amount"])
    if event_amount != expected_base_units:
        raise ValueError(f"Amount mismatch. Deal amount is {expected_base_units} base units, event has {event_amount} base units.")

    if is_v3:
        if expected_token == Web3.to_checksum_address(ETH_SENTINEL_ADDRESS):
            tx_value = int(tx.get("value", 0) if isinstance(tx, dict) or hasattr(tx, "get") else getattr(tx, "value", 0))
            if tx_value != expected_base_units:
                raise ValueError(f"Transaction value mismatch. Expected {expected_base_units} wei, got {tx_value} wei.")
        else:
            tx_value = int(tx.get("value", 0) if isinstance(tx, dict) or hasattr(tx, "get") else getattr(tx, "value", 0))
            if tx_value != 0:
                raise ValueError(f"Transaction value must be 0 for ERC20 token funding, got {tx_value}.")

            erc20_abi = [
                {
                    "anonymous": False,
                    "inputs": [
                        {"indexed": True, "name": "from", "type": "address"},
                        {"indexed": True, "name": "to", "type": "address"},
                        {"indexed": False, "name": "value", "type": "uint256"},
                    ],
                    "name": "Transfer",
                    "type": "event",
                }
            ]
            erc20_contract = w3.eth.contract(address=expected_token, abi=erc20_abi)
            try:
                transfer_events = erc20_contract.events.Transfer().process_receipt(receipt)
            except Exception as exc:
                raise ValueError(f"Failed to decode ERC20 Transfer events from receipt: {exc}")

            found_transfer = False
            for t_ev in transfer_events:
                t_args = t_ev["args"]
                if (
                    Web3.to_checksum_address(t_ev["address"]) == expected_token
                    and Web3.to_checksum_address(t_args["from"]) == buyer_wallet
                    and Web3.to_checksum_address(t_args["to"]) == contract_address
                    and int(t_args["value"]) == expected_base_units
                ):
                    found_transfer = True
                    break

            if not found_transfer:
                raise ValueError(
                    f"No matching ERC20 Transfer event found from buyer ({buyer_wallet}) to V3 contract ({contract_address}) for {expected_base_units} base units."
                )
    else:
        tx_value = int(tx["value"])
        if tx_value != expected_base_units:
            raise ValueError(f"Transaction value mismatch. Expected {expected_base_units} wei, got {tx_value} wei.")

    # 16. Verify deadline
    if is_v3 or is_v2:
        event_deadline = int(event_args["deliveryDeadline"])
    else:
        event_deadline = int(event_args["deadline"])
    if event_deadline != deal.deadline_ts:
        raise ValueError(f"Deadline mismatch. Deal deadline is {deal.deadline_ts}, event has {event_deadline}.")

    # 17. Verify agreement hash strictly against expected deal agreement_hash
    raw_event_hash = event_args["agreementHash"]
    event_hash_hex = ("0x" + raw_event_hash.hex()) if isinstance(raw_event_hash, (bytes, bytearray)) else str(raw_event_hash)

    if is_v3:
        # Re-calculate authoritative V3 canonical agreement hash from database records
        expected_hash = agreement_service.hash_v3_agreement_terms(
            deal_id=str(deal.id),
            agreement_version=deal.agreement_version or 1,
            previous_agreement_hash=deal.previous_agreement_hash or ("0x" + "0" * 64),
            offer_id=str(deal.offer_id),
            buyer_address=buyer_wallet,
            seller_address=event_seller,
            token_address=deal.token_address or ETH_SENTINEL_ADDRESS,
            amount_base_units=int(deal.amount_base_units),
            fee_bps=int(deal.fee_bps if deal.fee_bps is not None else getattr(settings, "platform_fee_bps", 250)),
            treasury_address=deal.treasury_address or getattr(settings, "treasury_address", None),
            delivery_deadline=int(deal.deadline_ts),
        ).lower()
        if deal.agreement_hash and deal.agreement_hash.lower() != expected_hash:
            raise ValueError("Deal record agreement hash does not match authoritative canonical agreement hash.")
    else:
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
        db, deal.offer_id, expected_hash, deal_id=deal.id
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

    if is_v3:
        # V3 Struct: (dealUuidHash, buyer, seller, token, amount, feeBps, treasury, deadline, state, fundsWithdrawn, agreementHash)
        if len(on_chain_deal) != 11:
            raise ValueError(f"Expected 11-field tuple for V3 getDeal(), got {len(on_chain_deal)} fields.")
        (oc_uuid_hash, oc_buyer, oc_seller, oc_token, oc_amount, oc_fee_bps, oc_treasury, oc_deadline, oc_state, oc_withdrawn, oc_ag_hash) = on_chain_deal
        expected_uuid_hash = Web3.keccak(text=str(deal.id))
        if isinstance(oc_uuid_hash, (bytes, bytearray)):
            if oc_uuid_hash != expected_uuid_hash:
                raise ValueError("On-chain deal UUID hash does not match.")
        elif str(oc_uuid_hash).lower() != expected_uuid_hash.hex().lower():
            raise ValueError("On-chain deal UUID hash does not match.")

        if Web3.to_checksum_address(oc_buyer) != buyer_wallet:
            raise ValueError("On-chain deal buyer does not match.")
        if Web3.to_checksum_address(oc_seller) != event_seller:
            raise ValueError("On-chain deal seller does not match.")
        if Web3.to_checksum_address(oc_token) != expected_token:
            raise ValueError("On-chain deal token does not match.")
        if int(oc_amount) != expected_base_units:
            raise ValueError("On-chain deal amount does not match expected base units.")
        if deal.fee_bps is not None and int(oc_fee_bps) != deal.fee_bps:
            raise ValueError("On-chain deal feeBps does not match.")
        if deal.treasury_address is not None and Web3.to_checksum_address(oc_treasury) != Web3.to_checksum_address(deal.treasury_address):
            raise ValueError("On-chain deal treasury does not match.")
        if int(oc_deadline) != deal.deadline_ts:
            raise ValueError("On-chain deal deadline does not match.")
        if oc_state != 0:  # State.FUNDED
            raise ValueError(f"On-chain deal is not in FUNDED state (state={oc_state}).")
        if oc_withdrawn is not False:
            raise ValueError("On-chain deal indicates funds already withdrawn.")

        raw_oc_hash = ("0x" + oc_ag_hash.hex()) if isinstance(oc_ag_hash, (bytes, bytearray)) else str(oc_ag_hash)
        if expected_hash.lower() != raw_oc_hash.lower():
            raise ValueError("On-chain deal agreement hash does not match.")

    elif is_v2:
        # V2 Struct: (buyer, seller, amount, deadline, agreementHash, dealUuidHash, state, fundsWithdrawn)
        (oc_buyer, oc_seller, oc_amount, oc_deadline, oc_ag_hash, oc_uuid_hash, oc_state, oc_withdrawn) = on_chain_deal
        expected_uuid_hash = Web3.keccak(text=str(deal.id))
        if isinstance(oc_uuid_hash, (bytes, bytearray)):
            if oc_uuid_hash != expected_uuid_hash:
                raise ValueError("On-chain deal UUID hash does not match.")
        elif str(oc_uuid_hash).lower() != expected_uuid_hash.hex().lower():
            raise ValueError("On-chain deal UUID hash does not match.")

        if Web3.to_checksum_address(oc_buyer) != buyer_wallet:
            raise ValueError("On-chain deal buyer does not match.")
        if Web3.to_checksum_address(oc_seller) != event_seller:
            raise ValueError("On-chain deal seller does not match.")
        if int(oc_amount) != expected_base_units:
            raise ValueError("On-chain deal amount does not match expected wei.")
        if int(oc_deadline) != deal.deadline_ts:
            raise ValueError("On-chain deal deadline does not match.")
        if oc_state != 0:  # State.FUNDED
            raise ValueError(f"On-chain deal is not in FUNDED state (state={oc_state}).")
        if oc_withdrawn is not False:
            raise ValueError("On-chain deal indicates funds already withdrawn.")
    else:
        # V1 Struct: (buyer, seller, amount, deadline, agreementHash, state, fundsWithdrawn)
        (oc_buyer, oc_seller, oc_amount, oc_deadline, oc_ag_hash, oc_state, oc_withdrawn) = on_chain_deal
        if Web3.to_checksum_address(oc_buyer) != buyer_wallet:
            raise ValueError("On-chain deal buyer does not match.")
        if Web3.to_checksum_address(oc_seller) != event_seller:
            raise ValueError("On-chain deal seller does not match.")
        if int(oc_amount) != expected_base_units:
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
    deal.contract_address = contract_address
    deal.contract_version = contract_version

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


STATE_INT_TO_ENUM: dict[int, EscrowState] = {
    0: EscrowState.FUNDED,
    1: EscrowState.CONFIRMED,
    2: EscrowState.RELEASED,
    3: EscrowState.REFUNDED,
    4: EscrowState.CANCELLED,
    5: EscrowState.DISPUTED,
    6: EscrowState.RESOLVED,
}

TERMINAL_STATES = {
    EscrowState.RELEASED,
    EscrowState.REFUNDED,
    EscrowState.CANCELLED,
    EscrowState.RESOLVED,
}


async def sync_deal_state(
    db: AsyncSession,
    deal_id: uuid.UUID,
    caller: User,
) -> EscrowDeal:
    """
    Synchronize cached deal state with on-chain smart contract (V1 or V2).

    Security and architectural rules enforced:
      1. Deal must exist and caller must be authorized (buyer or seller).
      2. Deal must be linked to an on-chain deal ID.
      3. Contract identity is resolved from trusted persisted metadata.
      4. For legacy records lacking contract metadata, inspects fund_tx_hash on-chain,
         validates the destination against configured V1/V2 addresses, and persists it.
      5. Strictly validates contract address and version: never silently falls back.
      6. Selects get_escrow_contract for V1 (7-tuple) or get_escrow_v2_contract for V2 (8-tuple).
      7. Validates on-chain dealUuidHash (for V2), buyer, seller, amount, and agreement hash.
      8. Protects terminal states from being downgraded.
    """
    from sqlalchemy.orm import selectinload

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
        raise ValueError("Deal not found.")

    if deal.buyer_id != caller.id and deal.seller_id != caller.id:
        raise PermissionError("Access denied.")

    if deal.chain_deal_id is None:
        raise ValueError("Deal is not linked to an on-chain deal ID.")

    settings = get_settings()
    w3 = get_web3()
    if not w3.is_connected():
        raise ValueError("Unable to connect to blockchain RPC node.")

    try:
        current_chain_id = w3.eth.chain_id
    except Exception as exc:
        raise ValueError(f"Failed to query chain ID from RPC: {exc}")

    if current_chain_id != settings.chain_id:
        raise ValueError(f"Chain ID mismatch. Expected {settings.chain_id}, got {current_chain_id}.")

    v1_address = Web3.to_checksum_address(settings.escrow_contract_address) if settings.escrow_contract_address else None
    v2_address = Web3.to_checksum_address(settings.escrow_v2_contract_address) if settings.escrow_v2_contract_address else None
    v3_address = None
    try:
        registry = get_token_registry()
        v3_address = registry.get_v3_contract_address(settings.chain_id)
    except Exception:
        if getattr(settings, "escrow_v3_contract_address", None):
            v3_address = Web3.to_checksum_address(settings.escrow_v3_contract_address)

    contract_version = deal.contract_version
    contract_address = Web3.to_checksum_address(deal.contract_address) if deal.contract_address else None

    # Safe backward-compatibility resolution for legacy records without contract metadata
    if not contract_version or not contract_address:
        if deal.fund_tx_hash:
            try:
                tx = w3.eth.get_transaction(deal.fund_tx_hash)
            except Exception as exc:
                raise ValueError(f"Failed to query historical funding transaction: {exc}")
            if not tx:
                raise ValueError("Historical funding transaction not found on chain.")
            tx_to = tx.get("to") if isinstance(tx, dict) or hasattr(tx, "get") else getattr(tx, "to", None)
            if not tx_to:
                raise ValueError("Historical transaction has no destination address.")
            tx_to_checksum = Web3.to_checksum_address(tx_to)
            if v3_address and tx_to_checksum == v3_address:
                contract_version = "v3"
                contract_address = v3_address
            elif v2_address and tx_to_checksum == v2_address:
                contract_version = "v2"
                contract_address = v2_address
            elif v1_address and tx_to_checksum == v1_address:
                contract_version = "v1"
                contract_address = v1_address
            else:
                raise ValueError(
                    f"Historical transaction destination ({tx_to_checksum}) does not match any configured contract address."
                )
            deal.contract_address = contract_address
            deal.contract_version = contract_version
            await db.commit()
            await db.refresh(deal)
        else:
            raise ValueError("Deal has missing contract metadata and no fund_tx_hash for resolution.")

    # Strict contract identity validation
    if contract_version == "v3":
        if not v3_address:
            raise ValueError("V3 escrow contract is not configured in backend settings.")
        if contract_address != v3_address:
            raise ValueError(f"Persisted contract address ({contract_address}) does not match configured V3 address ({v3_address}).")
        contract = get_escrow_v3_contract(w3, contract_address)
        if contract is None:
            raise ValueError("V3 escrow contract could not be loaded or ABI artifact is missing.")
    elif contract_version == "v2":
        if not v2_address:
            raise ValueError("V2 escrow contract is not configured in backend settings.")
        if contract_address != v2_address:
            raise ValueError(f"Persisted contract address ({contract_address}) does not match configured V2 address ({v2_address}).")
        contract = get_escrow_v2_contract(w3)
        if contract is None:
            raise ValueError("V2 escrow contract could not be loaded or ABI artifact is missing.")
    elif contract_version == "v1":
        if not v1_address:
            raise ValueError("V1 escrow contract is not configured in backend settings.")
        if contract_address != v1_address:
            raise ValueError(f"Persisted contract address ({contract_address}) does not match configured V1 address ({v1_address}).")
        contract = get_escrow_contract(w3)
        if contract is None:
            raise ValueError("V1 escrow contract could not be loaded or ABI artifact is missing.")
    else:
        raise ValueError(f"Unsupported contract version '{contract_version}'.")

    # Read on-chain deal state
    try:
        on_chain_raw = contract.functions.getDeal(deal.chain_deal_id).call()
    except Exception as exc:
        raise ValueError(f"Failed to read deal from contract getDeal(): {exc}")

    if contract_version == "v3":
        # V3 Deal struct: (dealUuidHash, buyer, seller, token, amount, feeBps, treasury, deadline, state, fundsWithdrawn, agreementHash)
        if len(on_chain_raw) != 11:
            raise ValueError(f"Expected 11-field tuple for V3 getDeal(), got {len(on_chain_raw)} fields.")
        (oc_uuid_hash, oc_buyer, oc_seller, oc_token, oc_amount, oc_fee_bps, oc_treasury, oc_deadline, oc_state, oc_withdrawn, oc_ag_hash) = on_chain_raw
        expected_uuid_hash = Web3.keccak(text=str(deal.id))
        if isinstance(oc_uuid_hash, (bytes, bytearray)):
            if oc_uuid_hash != expected_uuid_hash:
                raise ValueError("On-chain deal UUID hash does not match.")
        elif str(oc_uuid_hash).lower() != expected_uuid_hash.hex().lower():
            raise ValueError("On-chain deal UUID hash does not match.")
    elif contract_version == "v2":
        # V2 Deal struct: (buyer, seller, amount, deadline, agreementHash, dealUuidHash, state, fundsWithdrawn)
        if len(on_chain_raw) != 8:
            raise ValueError(f"Expected 8-field tuple for V2 getDeal(), got {len(on_chain_raw)} fields.")
        (oc_buyer, oc_seller, oc_amount, oc_deadline, oc_ag_hash, oc_uuid_hash, oc_state, oc_withdrawn) = on_chain_raw
        expected_uuid_hash = Web3.keccak(text=str(deal.id))
        if isinstance(oc_uuid_hash, (bytes, bytearray)):
            if oc_uuid_hash != expected_uuid_hash:
                raise ValueError("On-chain deal UUID hash does not match.")
        elif str(oc_uuid_hash).lower() != expected_uuid_hash.hex().lower():
            raise ValueError("On-chain deal UUID hash does not match.")
    else:
        # V1 Deal struct: (buyer, seller, amount, deadline, agreementHash, state, fundsWithdrawn)
        if len(on_chain_raw) != 7:
            raise ValueError(f"Expected 7-field tuple for V1 getDeal(), got {len(on_chain_raw)} fields.")
        (oc_buyer, oc_seller, oc_amount, oc_deadline, oc_ag_hash, oc_state, oc_withdrawn) = on_chain_raw

    # 1. Validate on-chain buyer against database record if buyer wallet is registered
    if deal.buyer and deal.buyer.wallet_address:
        if Web3.to_checksum_address(oc_buyer) != Web3.to_checksum_address(deal.buyer.wallet_address):
            raise ValueError(f"On-chain buyer ({oc_buyer}) does not match deal buyer ({deal.buyer.wallet_address}).")

    # 2. Validate on-chain seller against database record if seller wallet is registered
    if deal.seller and deal.seller.wallet_address:
        if Web3.to_checksum_address(oc_seller) != Web3.to_checksum_address(deal.seller.wallet_address):
            raise ValueError(f"On-chain seller ({oc_seller}) does not match deal seller ({deal.seller.wallet_address}).")

    # 3. Validate on-chain amount against deal amount (exact base units)
    expected_amount = int(deal.amount_base_units) if deal.amount_base_units else int(Decimal(str(deal.amount_eth)) * Decimal(10**18))
    if int(oc_amount) != expected_amount:
        raise ValueError(f"On-chain amount ({oc_amount}) does not match expected amount ({expected_amount}).")

    # 4. Validate agreement hash if present
    if deal.agreement_hash:
        raw_ag_hash = ("0x" + oc_ag_hash.hex()) if isinstance(oc_ag_hash, (bytes, bytearray)) else str(oc_ag_hash)
        expected_hash = deal.agreement_hash.lower()
        if not expected_hash.startswith("0x"):
            expected_hash = "0x" + expected_hash
        if raw_ag_hash.lower() != expected_hash:
            raise ValueError(f"On-chain agreement hash ({raw_ag_hash}) does not match expected ({expected_hash}).")

    # 5. Map Solidity integer state to EscrowState enum
    if oc_state not in STATE_INT_TO_ENUM:
        raise ValueError(f"Unknown on-chain state integer: {oc_state}")

    new_state = STATE_INT_TO_ENUM[oc_state]

    # 6. Terminal state protection: terminal states cannot be downgraded
    if deal.on_chain_state in TERMINAL_STATES and new_state not in TERMINAL_STATES:
        logger.warning(
            "Sync rejected: attempt to overwrite terminal state %s with %s for deal %s",
            deal.on_chain_state, new_state, deal.id
        )
        return deal

    if deal.on_chain_state != new_state:
        deal.on_chain_state = new_state
        await db.commit()
        await db.refresh(deal)

    return deal


def get_contract_info() -> dict:
    """Return publicly safe contract metadata for the frontend."""
    settings = get_settings()
    abi = _load_abi()
    v3_abi = None
    try:
        v3_abi = load_v3_contract_abi()
    except Exception:
        pass

    v3_address = None
    try:
        registry = get_token_registry()
        v3_address = registry.get_v3_contract_address(settings.chain_id)
    except Exception:
        if getattr(settings, "escrow_v3_contract_address", None):
            v3_address = Web3.to_checksum_address(settings.escrow_v3_contract_address)

    return {
        "address": settings.escrow_contract_address or None,
        "v2_address": settings.escrow_v2_contract_address or None,
        "v3_address": v3_address,
        "rpc_url": settings.web3_rpc_url,
        "chain_id": settings.chain_id,
        "abi": abi,
        "v3_abi": v3_abi,
        "configured": bool((settings.escrow_contract_address or v3_address) and (abi or v3_abi)),
    }
