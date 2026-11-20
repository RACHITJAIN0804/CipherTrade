"""
Canonical agreement construction, deterministic serialization, and cryptographic hashing service.

Phase 5D.2: Backend-Generated Canonical Agreement Hashes.

Establishes the backend as the authoritative source of truth for agreement term integrity:
- Normalizes accepted offer terms and parent requirement specifications.
- Implements deterministic UTF-8 JSON serialization with alphabetically sorted keys and compact separators.
- Generates standard Keccak-256 (32-byte bytes32 hex) hashes matching Solidity contract parameters.
"""
from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from web3 import Web3

from app.models.models import Offer, OfferStatus, Requirement

logger = logging.getLogger(__name__)

CANONICAL_AGREEMENT_SCHEMA_VERSION = "ciphertrade-agreement-v1"


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
    Construct the canonical agreement dictionary from authoritative database records.
    Client-supplied terms are NEVER trusted or mixed into this representation.
    """
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
        "currency": "ETH",
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
    Compute standard Keccak-256 cryptographic hash of canonical agreement bytes.
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
    Build canonical agreement and generate its Keccak-256 hash for in-memory entities.
    Returns (canonical_dict, agreement_hash).
    """
    canonical_dict = build_canonical_agreement(offer, requirement, deal_id=deal_id)
    canonical_bytes = serialize_canonical_agreement(canonical_dict)
    agreement_hash = compute_agreement_hash(canonical_bytes)
    return canonical_dict, agreement_hash


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
