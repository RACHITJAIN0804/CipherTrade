"""
Canonical agreement construction, deterministic serialization, and cryptographic hashing service.

Phase 5D.2: Backend-Generated Canonical Agreement Hashes (V1/V2).
Phase 6.2.2.3.3: V3 Canonical Agreement Hashing Matching Solidity V3 AGREEMENT_TERMS_TYPEHASH.

Establishes the backend as the authoritative source of truth for agreement term integrity:
- Normalizes accepted offer terms and parent requirement specifications.
- Implements deterministic UTF-8 JSON serialization for off-chain storage.
- Generates standard Keccak-256 hashes matching Solidity V1, V2, and V3 contracts.
"""
from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal
from typing import Any, Optional

from eth_abi import encode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from web3 import Web3

from app.core.numeric import validate_base_units_str
from app.core.token_registry import ETH_SENTINEL_ADDRESS, get_token_registry
from app.models.models import Offer, OfferStatus, Requirement

logger = logging.getLogger(__name__)

CANONICAL_AGREEMENT_SCHEMA_VERSION = "ciphertrade-agreement-v1"
CANONICAL_AGREEMENT_V3_SCHEMA_VERSION = "ciphertrade-agreement-v3"

# Solidity V3 Typehash:
# keccak256("AgreementTerms(string dealId,uint256 agreementVersion,bytes32 previousAgreementHash,string offerId,address buyer,address seller,address token,uint256 amount,uint256 feeBps,address treasury,uint256 deliveryDeadline)")
AGREEMENT_TERMS_TYPEHASH: bytes = Web3.keccak(
    text="AgreementTerms(string dealId,uint256 agreementVersion,bytes32 previousAgreementHash,string offerId,address buyer,address seller,address token,uint256 amount,uint256 feeBps,address treasury,uint256 deliveryDeadline)"
)
AGREEMENT_TERMS_TYPEHASH_HEX: str = Web3.to_hex(AGREEMENT_TERMS_TYPEHASH).lower()


def normalize_eth_amount(amount: Any) -> str:
    """
    Format ETH amount to 8 decimal places as a clean string without floating-point errors.
    e.g. Decimal('0.5') -> '0.50000000'
    """
    try:
        dec = Decimal(str(amount)).quantize(Decimal("0.00000001"))
        return f"{dec:.8f}"
    except Exception as exc:
        raise ValueError(f"Invalid ETH amount '{amount}': {exc}")


def build_canonical_agreement(
    offer: Offer,
    requirement: Requirement,
    deal_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """
    Construct the canonical agreement dictionary from authoritative database records (V1/V2 format).
    Client-supplied terms are NEVER trusted or mixed into this representation.
    """
    # In legacy models or test mocks without explicit currency, default safely to "ETH"
    raw_currency = getattr(offer, "currency", None)
    if isinstance(raw_currency, str) and raw_currency.strip():
        currency = raw_currency.strip().upper()
    else:
        currency = "ETH"
    return {
        "schema_version": CANONICAL_AGREEMENT_SCHEMA_VERSION,
        "deal_reference": f"offer:{offer.id}",
        "offer_id": str(offer.id),
        "requirement_id": str(requirement.id),
        "buyer_id": str(requirement.buyer_id),
        "seller_id": str(offer.seller_id),
        "title": (requirement.title or "").strip(),
        "description": (requirement.description or "").strip(),
        "category": (requirement.category or "").strip(),
        "price_eth": normalize_eth_amount(offer.price_eth),
        "currency": currency,
        "delivery_deadline": int(offer.delivery_deadline),
        "terms": (offer.terms or "").strip(),
    }


def serialize_canonical_agreement(canonical_dict: dict[str, Any]) -> bytes:
    """
    Deterministically serialize a canonical agreement dictionary to UTF-8 encoded JSON bytes.

    Determinism guarantees:
    - Keys sorted alphabetically (sort_keys=True)
    - Compact separators with no trailing or intra-key whitespace (separators=(',', ':'))
    - ensure_ascii=False for standard UTF-8 text representation
    - Independent of dictionary insertion order across Python / Node runtimes
    """
    serialized_str = json.dumps(
        canonical_dict,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return serialized_str.encode("utf-8")


def compute_agreement_hash(canonical_bytes: bytes) -> str:
    """
    Compute standard Keccak-256 cryptographic hash of canonical agreement bytes (V1/V2 JSON-based).
    Returns a 0x-prefixed 32-byte hex string (66 characters total) compatible with
    Solidity `bytes32`.
    """
    raw_hash = Web3.keccak(canonical_bytes)
    return Web3.to_hex(raw_hash).lower()


def generate_canonical_hash_for_entities(
    offer: Offer,
    requirement: Requirement,
    deal_id: Optional[uuid.UUID] = None,
) -> tuple[dict[str, Any], str]:
    """
    Build canonical agreement and generate its Keccak-256 hash for in-memory entities (V1/V2).
    Returns (canonical_dict, agreement_hash).
    """
    canonical_dict = build_canonical_agreement(offer, requirement, deal_id=deal_id)
    canonical_bytes = serialize_canonical_agreement(canonical_dict)
    agreement_hash = compute_agreement_hash(canonical_bytes)
    return canonical_dict, agreement_hash


# ─── V3 Agreement Hashing (Solidity ABI-Encoded Struct Hash) ─────────────────

def hash_v3_agreement_terms(
    deal_id: str | uuid.UUID,
    agreement_version: int,
    previous_agreement_hash: bytes | str,
    offer_id: str | uuid.UUID,
    buyer_address: str,
    seller_address: str,
    token_address: str,
    amount_base_units: int | str,
    fee_bps: int,
    treasury_address: str,
    delivery_deadline: int,
) -> str:
    """
    Compute the canonical V3 agreement hash matching CipherTradeEscrowV3.sol AGREEMENT_TERMS_TYPEHASH.
    
    Solidity calculation:
    keccak256(
        abi.encode(
            AGREEMENT_TERMS_TYPEHASH,
            keccak256(bytes(terms.dealId)),
            terms.agreementVersion,
            terms.previousAgreementHash,
            keccak256(bytes(terms.offerId)),
            terms.buyer,
            terms.seller,
            terms.token,
            terms.amount,
            terms.feeBps,
            terms.treasury,
            terms.deliveryDeadline
        )
    )
    
    Returns 0x-prefixed 32-byte hex string.
    """
    # 1. Clean string IDs and compute their keccak256 hashes
    deal_id_str = str(deal_id).strip()
    offer_id_str = str(offer_id).strip()
    hashed_deal_id = Web3.keccak(text=deal_id_str)
    hashed_offer_id = Web3.keccak(text=offer_id_str)

    # 2. previousAgreementHash (bytes32)
    if isinstance(previous_agreement_hash, str):
        clean_prev = previous_agreement_hash.strip()
        if clean_prev.startswith("0x") or clean_prev.startswith("0X"):
            clean_prev = clean_prev[2:]
        if not clean_prev:
            prev_bytes = b"\x00" * 32
        else:
            prev_bytes = bytes.fromhex(clean_prev)
    elif isinstance(previous_agreement_hash, (bytes, bytearray)):
        prev_bytes = bytes(previous_agreement_hash)
    else:
        raise ValueError(f"Invalid previous_agreement_hash type: {type(previous_agreement_hash)}")

    if len(prev_bytes) != 32:
        raise ValueError(f"previous_agreement_hash must be exactly 32 bytes, got {len(prev_bytes)}")

    # 3. Checksum addresses
    buyer_chk = Web3.to_checksum_address(buyer_address)
    seller_chk = Web3.to_checksum_address(seller_address)
    token_chk = Web3.to_checksum_address(token_address)
    treasury_chk = Web3.to_checksum_address(treasury_address)

    # 4. Numeric integers
    amount_int = int(validate_base_units_str(str(amount_base_units), "amount_base_units"))
    fee_bps_int = int(fee_bps)
    delivery_deadline_int = int(delivery_deadline)
    version_int = int(agreement_version)

    if fee_bps_int < 0 or fee_bps_int > 500:
        raise ValueError(f"fee_bps must be between 0 and 500 bps (5%), got {fee_bps_int}")
    if version_int < 1:
        raise ValueError(f"agreement_version must be >= 1, got {version_int}")

    # 5. ABI encode exactly matching EIP-712 struct hash encoding
    encoded = encode(
        [
            "bytes32",  # AGREEMENT_TERMS_TYPEHASH
            "bytes32",  # keccak256(bytes(dealId))
            "uint256",  # agreementVersion
            "bytes32",  # previousAgreementHash
            "bytes32",  # keccak256(bytes(offerId))
            "address",  # buyer
            "address",  # seller
            "address",  # token
            "uint256",  # amount
            "uint256",  # feeBps
            "address",  # treasury
            "uint256",  # deliveryDeadline
        ],
        [
            AGREEMENT_TERMS_TYPEHASH,
            hashed_deal_id,
            version_int,
            prev_bytes,
            hashed_offer_id,
            buyer_chk,
            seller_chk,
            token_chk,
            amount_int,
            fee_bps_int,
            treasury_chk,
            delivery_deadline_int,
        ],
    )

    terms_hash = Web3.keccak(encoded)
    return Web3.to_hex(terms_hash).lower()


def compute_deal_uuid_hash(deal_id: str | uuid.UUID) -> str:
    """
    Compute keccak256(bytes(dealId)) matching Solidity dealUuidHash.
    """
    deal_id_str = str(deal_id).strip()
    return Web3.to_hex(Web3.keccak(text=deal_id_str)).lower()


async def get_canonical_agreement_for_offer(
    db: AsyncSession,
    offer_id: uuid.UUID,
) -> tuple[dict[str, Any], str]:
    """
    Load authoritative Offer and Requirement from database, verify accepted state,
    and return (canonical_dict, agreement_hash).
    """
    stmt = (
        select(Offer)
        .where(Offer.id == offer_id)
        .options(selectinload(Offer.requirement))
    )
    offer = (await db.execute(stmt)).scalar_one_or_none()
    if offer is None:
        raise ValueError("Offer not found.")
    if offer.status != OfferStatus.ACCEPTED:
        raise ValueError(
            f"Canonical agreement cannot be generated for offer in '{offer.status.value}' state. "
            "Only accepted offers can form a binding agreement."
        )
    if offer.requirement is None:
        raise ValueError("Parent requirement for offer not found.")

    return generate_canonical_hash_for_entities(offer, offer.requirement)
