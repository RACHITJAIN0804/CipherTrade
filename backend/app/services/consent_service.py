"""
Cryptographic agreement consent service (EIP-712).

Phase 5D.3: Cryptographic Agreement Consent.

Enforces that both buyer and seller independently review and cryptographically
sign the exact same canonical agreement terms (agreement_hash) using EIP-712 typed-data
signatures before escrow funding can proceed.
"""
from __future__ import annotations

import logging
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from eth_account import Account
from eth_account.messages import encode_typed_data
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from web3 import Web3

from app.config import get_settings
from app.models.models import (
    AgreementConsent,
    AgreementConsentChallenge,
    EscrowDeal,
    Offer,
    OfferStatus,
    User,
)
from app.schemas.consent import (
    ConsentChallengeResponse,
    ConsentPartyDetail,
    ConsentStatusResponse,
    ConsentVerifyResponse,
)
from app.services import agreement_service

logger = logging.getLogger(__name__)

CONSENT_DOMAIN_NAME = "CipherTrade"
CONSENT_DOMAIN_VERSION = "1"
CONSENT_PRIMARY_TYPE = "AgreementConsent"

CONSENT_EIP712_TYPES = {
    "AgreementConsent": [
        {"name": "agreementHash", "type": "bytes32"},
        {"name": "offerId", "type": "string"},
        {"name": "dealId", "type": "string"},
        {"name": "partyRole", "type": "string"},
        {"name": "signer", "type": "address"},
        {"name": "nonce", "type": "string"},
        {"name": "deadline", "type": "uint256"},
    ]
}


class ConflictError(ValueError):
    """Raised when an operation conflicts with existing resource state (HTTP 409)."""
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_consent_eip712_domain(chain_id: int, contract_address: str) -> dict[str, Any]:
    """Return standard EIP-712 domain separator dictionary for agreement consent."""
    return {
        "name": CONSENT_DOMAIN_NAME,
        "version": CONSENT_DOMAIN_VERSION,
        "chainId": int(chain_id),
        "verifyingContract": Web3.to_checksum_address(contract_address),
    }


def get_consent_eip712_types() -> dict[str, list[dict[str, str]]]:
    """Return EIP-712 type definition for AgreementConsent."""
    return CONSENT_EIP712_TYPES


async def _resolve_offer_with_relations(
    db: AsyncSession, offer_id: uuid.UUID
) -> Offer:
    """Fetch Offer loaded with requirement and escrow_deal."""
    stmt = (
        select(Offer)
        .where(Offer.id == offer_id)
        .options(
            selectinload(Offer.requirement),
            selectinload(Offer.escrow_deal),
        )
    )
    offer = (await db.execute(stmt)).scalar_one_or_none()
    if offer is None:
        raise ValueError(f"Offer '{offer_id}' not found.")
    if offer.requirement is None:
        raise ValueError(f"Parent requirement for offer '{offer_id}' not found.")
    return offer


async def get_consent_status(
    db: AsyncSession,
    offer_id: uuid.UUID,
    deal_id: Optional[uuid.UUID] = None,
) -> ConsentStatusResponse:
    """
    Query the current cryptographic consent status for an offer / deal.
    """
    offer = await _resolve_offer_with_relations(db, offer_id)
    _, authoritative_hash = agreement_service.generate_canonical_hash_for_entities(
        offer, offer.requirement
    )
    expected_hash = authoritative_hash.lower()

    # Query all recorded consents for this offer
    stmt = select(AgreementConsent).where(
        AgreementConsent.offer_id == offer.id,
        func.lower(AgreementConsent.agreement_hash) == expected_hash,
    )
    consents = (await db.execute(stmt)).scalars().all()

    buyer_consent_record: Optional[AgreementConsent] = None
    seller_consent_record: Optional[AgreementConsent] = None

    for c in consents:
        if c.party_role == "buyer":
            buyer_consent_record = c
        elif c.party_role == "seller":
            seller_consent_record = c

    effective_deal_id = deal_id or (offer.escrow_deal.id if offer.escrow_deal else None)
    buyer_consented = buyer_consent_record is not None
    seller_consented = seller_consent_record is not None
    both_consented = buyer_consented and seller_consented

    return ConsentStatusResponse(
        offer_id=offer.id,
        deal_id=effective_deal_id,
        agreement_hash=authoritative_hash,
        buyer_consented=buyer_consented,
        seller_consented=seller_consented,
        both_consented=both_consented,
        can_fund=both_consented,
        buyer_consent=ConsentPartyDetail(
            user_id=buyer_consent_record.user_id,
            wallet_address=buyer_consent_record.signer_wallet,
            signed_at=buyer_consent_record.created_at,
            chain_id=buyer_consent_record.chain_id,
            agreement_hash=buyer_consent_record.agreement_hash,
        ) if buyer_consent_record else None,
        seller_consent=ConsentPartyDetail(
            user_id=seller_consent_record.user_id,
            wallet_address=seller_consent_record.signer_wallet,
            signed_at=seller_consent_record.created_at,
            chain_id=seller_consent_record.chain_id,
            agreement_hash=seller_consent_record.agreement_hash,
        ) if seller_consent_record else None,
    )


async def create_consent_challenge(
    db: AsyncSession,
    user: User,
    offer_id: uuid.UUID,
    deal_id: Optional[uuid.UUID] = None,
) -> ConsentChallengeResponse:
    """
    Generate an authoritative EIP-712 agreement consent challenge for an authenticated user.
    """
    settings = get_settings()
    offer = await _resolve_offer_with_relations(db, offer_id)

    # 1. Offer must be in ACCEPTED status (or have an existing deal)
    if offer.status != OfferStatus.ACCEPTED and offer.escrow_deal is None:
        raise ValueError("Cryptographic agreement consent is only available for accepted offers.")

    # 2. Determine party role and verify user authorization
    party_role: str
    if user.id == offer.requirement.buyer_id:
        party_role = "buyer"
    elif user.id == offer.seller_id:
        party_role = "seller"
    else:
        raise PermissionError("Access denied: You are neither the buyer nor the seller of this offer.")

    # 3. User must have a verified wallet address linked to their account
    if not user.wallet_address:
        raise ValueError(
            "User does not have a verified wallet address linked to their account. "
            "Please complete wallet verification (SIWE) first."
        )

    try:
        user_wallet = Web3.to_checksum_address(user.wallet_address)
    except Exception:
        raise ValueError("User's registered wallet address is invalid.")

    # 4. Check if user has already consented to this offer
    stmt_existing = select(AgreementConsent).where(
        AgreementConsent.offer_id == offer.id,
        AgreementConsent.party_role == party_role,
    )
    existing_consent = (await db.execute(stmt_existing)).scalar_one_or_none()
    if existing_consent is not None:
        raise ConflictError(f"Consent has already been recorded for this {party_role}.")

    # 5. Generate authoritative canonical agreement and hash
    _, authoritative_hash = agreement_service.generate_canonical_hash_for_entities(
        offer, offer.requirement
    )
    authoritative_hash = authoritative_hash.lower()

    # 6. Generate single-use nonce and expiration deadline (10 minutes)
    nonce = secrets.token_hex(32)
    current_ts = int(time.time())
    deadline_ts = current_ts + 600

    effective_deal_id = deal_id or (offer.escrow_deal.id if offer.escrow_deal else None)

    # 7. Persist challenge
    challenge = AgreementConsentChallenge(
        offer_id=offer.id,
        deal_id=effective_deal_id,
        user_id=user.id,
        party_role=party_role,
        wallet_address=user_wallet,
        agreement_hash=authoritative_hash,
        chain_id=settings.chain_id,
        nonce=nonce,
        deadline=deadline_ts,
    )
    db.add(challenge)
    await db.commit()

    # 8. Build EIP-712 payload
    domain = get_consent_eip712_domain(settings.chain_id, settings.escrow_contract_address)
    types = get_consent_eip712_types()
    message = {
        "agreementHash": authoritative_hash,
        "offerId": str(offer.id),
        "dealId": str(effective_deal_id) if effective_deal_id else "",
        "partyRole": party_role,
        "signer": user_wallet,
        "nonce": nonce,
        "deadline": deadline_ts,
    }

    return ConsentChallengeResponse(
        domain=domain,
        types=types,
        primary_type=CONSENT_PRIMARY_TYPE,
        message=message,
        nonce=nonce,
        deadline=deadline_ts,
    )


async def verify_and_record_consent(
    db: AsyncSession,
    user: User,
    offer_id: uuid.UUID,
    nonce: str,
    signature: str,
    deal_id: Optional[uuid.UUID] = None,
) -> ConsentVerifyResponse:
    """
    Atomically verify an EIP-712 agreement consent signature, consume the challenge nonce,
    and persist the cryptographic consent record.
    """
    settings = get_settings()

    # 1. Format checks
    if not isinstance(nonce, str) or len(nonce) != 64:
        raise ValueError("Invalid challenge nonce format.")
    if not isinstance(signature, str) or not signature.startswith("0x") or len(signature) != 132:
        raise ValueError("Invalid signature format. Expected 0x followed by 130 hex characters.")

    # 2. Check user wallet
    if not user.wallet_address:
        raise ValueError("User does not have a verified wallet address linked to their account.")
    try:
        user_wallet = Web3.to_checksum_address(user.wallet_address)
    except Exception:
        raise ValueError("User's registered wallet address is invalid.")

    # 3. Atomically consume challenge nonce
    now_dt = _now()
    current_ts = int(now_dt.timestamp())

    stmt_consume = (
        update(AgreementConsentChallenge)
        .where(
            AgreementConsentChallenge.nonce == nonce,
            AgreementConsentChallenge.user_id == user.id,
            AgreementConsentChallenge.offer_id == offer_id,
            AgreementConsentChallenge.used_at.is_(None),
            AgreementConsentChallenge.deadline >= current_ts,
        )
        .values(used_at=now_dt)
        .returning(AgreementConsentChallenge)
    )
    result = await db.execute(stmt_consume)
    challenge = result.scalar_one_or_none()

    if challenge is None:
        # Determine exact failure reason for clear audit logging
        stmt_check = select(AgreementConsentChallenge).where(
            AgreementConsentChallenge.nonce == nonce
        )
        existing_ch = (await db.execute(stmt_check)).scalar_one_or_none()
        if existing_ch is None:
            raise ValueError("Consent challenge nonce not found or invalid.")
        if existing_ch.user_id != user.id:
            raise PermissionError("Consent challenge was issued to a different user.")
        if existing_ch.offer_id != offer_id:
            raise ValueError("Consent challenge was issued for a different offer.")
        if existing_ch.used_at is not None:
            raise ValueError("Consent challenge nonce has already been consumed (replay attempt).")
        if existing_ch.deadline < current_ts:
            raise ValueError("Consent challenge has expired. Please request a new challenge.")
        raise ValueError("Invalid consent challenge.")

    # 4. Verify wallet address match
    if Web3.to_checksum_address(challenge.wallet_address) != user_wallet:
        raise ValueError("Challenge wallet address does not match user's current registered wallet.")

    # 5. Verify agreement hash has not drifted
    offer = await _resolve_offer_with_relations(db, offer_id)
    _, current_authoritative_hash = agreement_service.generate_canonical_hash_for_entities(
        offer, offer.requirement
    )
    if challenge.agreement_hash.lower() != current_authoritative_hash.lower():
        raise ValueError(
            "Agreement terms have changed since this challenge was created. "
            "Please review the updated agreement terms."
        )

    # 6. Verify chain ID
    if challenge.chain_id != settings.chain_id:
        raise ValueError(f"Chain ID mismatch. Challenge was issued for chain {challenge.chain_id}.")

    # 7. Reconstruct EIP-712 structured data and recover signer
    verifying_contract = Web3.to_checksum_address(settings.escrow_contract_address)
    eip712_full_message = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "AgreementConsent": [
                {"name": "agreementHash", "type": "bytes32"},
                {"name": "offerId", "type": "string"},
                {"name": "dealId", "type": "string"},
                {"name": "partyRole", "type": "string"},
                {"name": "signer", "type": "address"},
                {"name": "nonce", "type": "string"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": CONSENT_PRIMARY_TYPE,
        "domain": {
            "name": CONSENT_DOMAIN_NAME,
            "version": CONSENT_DOMAIN_VERSION,
            "chainId": challenge.chain_id,
            "verifyingContract": verifying_contract,
        },
        "message": {
            "agreementHash": challenge.agreement_hash,
            "offerId": str(challenge.offer_id),
            "dealId": str(challenge.deal_id) if challenge.deal_id else "",
            "partyRole": challenge.party_role,
            "signer": challenge.wallet_address,
            "nonce": challenge.nonce,
            "deadline": challenge.deadline,
        },
    }

    try:
        signable_bytes = encode_typed_data(full_message=eip712_full_message)
        recovered_address = Account.recover_message(signable_bytes, signature=signature)
    except Exception as exc:
        logger.warning("EIP-712 signature recovery failed: %s", exc)
        raise ValueError(f"Invalid EIP-712 signature: {exc}")

    if Web3.to_checksum_address(recovered_address) != user_wallet:
        raise ValueError(
            f"Cryptographic signature mismatch. Recovered signer ({recovered_address}) "
            f"does not match registered wallet ({user_wallet})."
        )

    # 8. Persist verified consent record
    consent = AgreementConsent(
        offer_id=challenge.offer_id,
        deal_id=challenge.deal_id or deal_id,
        user_id=user.id,
        party_role=challenge.party_role,
        signer_wallet=user_wallet,
        agreement_hash=challenge.agreement_hash,
        chain_id=challenge.chain_id,
        nonce=challenge.nonce,
        signature=signature,
        deadline=challenge.deadline,
        created_at=now_dt,
    )
    db.add(consent)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError(f"Consent has already been recorded for {challenge.party_role}.")

    # 9. Query updated mutual consent status
    both_consented = await check_both_parties_consented(
        db, challenge.offer_id, challenge.agreement_hash
    )

    return ConsentVerifyResponse(
        status="success",
        offer_id=challenge.offer_id,
        deal_id=challenge.deal_id or deal_id,
        party_role=challenge.party_role,
        signer_wallet=user_wallet,
        agreement_hash=challenge.agreement_hash,
        both_consented=both_consented,
        can_fund=both_consented,
    )


async def check_both_parties_consented(
    db: AsyncSession,
    offer_id: uuid.UUID,
    agreement_hash: str,
) -> bool:
    """
    Check if both buyer and seller have valid recorded consents for the specified agreement hash.
    Also validates that the recorded signer wallets match the current registered wallets.
    """
    if not agreement_hash.startswith("0x"):
        expected_hash = "0x" + agreement_hash.lower()
    else:
        expected_hash = agreement_hash.lower()

    offer = await _resolve_offer_with_relations(db, offer_id)

    # Load users
    buyer = await db.get(User, offer.requirement.buyer_id)
    seller = await db.get(User, offer.seller_id)

    if not buyer or not buyer.wallet_address or not seller or not seller.wallet_address:
        return False

    stmt = select(AgreementConsent).where(
        AgreementConsent.offer_id == offer_id,
        func.lower(AgreementConsent.agreement_hash) == expected_hash,
    )
    consents = (await db.execute(stmt)).scalars().all()

    buyer_consent = next((c for c in consents if c.party_role == "buyer"), None)
    seller_consent = next((c for c in consents if c.party_role == "seller"), None)

    if buyer_consent is None or seller_consent is None:
        return False

    # Verify signer wallets match current registered user wallets
    buyer_wallet_match = (
        Web3.to_checksum_address(buyer_consent.signer_wallet)
        == Web3.to_checksum_address(buyer.wallet_address)
    )
    seller_wallet_match = (
        Web3.to_checksum_address(seller_consent.signer_wallet)
        == Web3.to_checksum_address(seller.wallet_address)
    )

    return buyer_wallet_match and seller_wallet_match
