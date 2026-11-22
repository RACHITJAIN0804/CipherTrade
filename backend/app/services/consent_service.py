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
from decimal import Decimal
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
    CommercialAgreementPayload,
    ConsentChallengeResponse,
    ConsentPartyDetail,
    ConsentStatusResponse,
    ConsentVerifyResponse,
    ContractFundingPayloadResponse,
    PartyConsentProofPayload,
)
from app.services import agreement_service

logger = logging.getLogger(__name__)

CONSENT_DOMAIN_NAME = "CipherTrade"
CONSENT_DOMAIN_VERSION = "1"
CONSENT_PRIMARY_TYPE = "AgreementConsent"

# Phase 5E: Authoritative 11-Field EIP-712 Schema
CONSENT_EIP712_TYPES = {
    "AgreementConsent": [
        {"name": "agreementHash", "type": "bytes32"},
        {"name": "offerId", "type": "string"},
        {"name": "dealId", "type": "string"},
        {"name": "buyer", "type": "address"},
        {"name": "seller", "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "deliveryDeadline", "type": "uint256"},
        {"name": "partyRole", "type": "string"},
        {"name": "signer", "type": "address"},
        {"name": "nonce", "type": "string"},
        {"name": "deadline", "type": "uint256"},
    ]
}


class ConflictError(ValueError):
    """Raised when an operation conflicts with existing resource state (HTTP 409)."""
    pass


class NotFoundError(ValueError):
    """Raised when a requested resource is not found (HTTP 404)."""
    pass


class ConfigurationError(RuntimeError):
    """Raised when required deployment or service configuration is missing or invalid (HTTP 503)."""
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_configured_v2_address(required: bool = False) -> Optional[str]:
    """
    Return checksummed CipherTradeEscrowV2 address if configured.
    Validates address format and raises ConfigurationError if configured but invalid.
    If required=True and address is missing or invalid, raises ConfigurationError.
    NEVER falls back to escrow_contract_address.
    """
    settings = get_settings()
    addr = getattr(settings, "escrow_v2_contract_address", None)
    if not addr or not str(addr).strip():
        if required:
            raise ConfigurationError(
                "CipherTradeEscrowV2 contract address is not configured. "
                "On-chain V2 deployment configuration is missing."
            )
        return None
    addr_str = str(addr).strip()
    if not Web3.is_address(addr_str):
        raise ConfigurationError(
            f"Configured escrow_v2_contract_address '{addr_str}' is not a valid Ethereum address."
        )
    return Web3.to_checksum_address(addr_str)


def get_consent_eip712_domain(
    chain_id: int, contract_address: Optional[str] = None
) -> dict[str, Any]:
    """Return standard EIP-712 domain separator dictionary for agreement consent."""
    domain: dict[str, Any] = {
        "name": CONSENT_DOMAIN_NAME,
        "version": CONSENT_DOMAIN_VERSION,
        "chainId": int(chain_id),
    }
    if contract_address:
        domain["verifyingContract"] = Web3.to_checksum_address(contract_address)
    return domain


def get_consent_eip712_types(include_verifying_contract: bool = True) -> dict[str, list[dict[str, str]]]:
    """Return EIP-712 type definition for AgreementConsent."""
    domain_fields = [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
    ]
    if include_verifying_contract:
        domain_fields.append({"name": "verifyingContract", "type": "address"})

    return {
        "EIP712Domain": domain_fields,
        "AgreementConsent": CONSENT_EIP712_TYPES["AgreementConsent"],
    }


def verify_consent_signature_11_fields(
    *,
    agreement_hash: str,
    offer_id: str,
    deal_id: str,
    buyer_address: str,
    seller_address: str,
    amount_wei: int,
    delivery_deadline: int,
    party_role: str,
    signer_address: str,
    nonce: str,
    deadline: int,
    signature: str,
    chain_id: int,
    contract_address: Optional[str] = None,
) -> bool:
    """
    Verify an EIP-712 signature against the authoritative 11-field AgreementConsent schema.
    Returns True if the recovered signer matches signer_address; False otherwise.
    """
    domain = {
        "name": CONSENT_DOMAIN_NAME,
        "version": CONSENT_DOMAIN_VERSION,
        "chainId": int(chain_id),
    }
    domain_types = [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
    ]
    if contract_address:
        domain["verifyingContract"] = Web3.to_checksum_address(contract_address)
        domain_types.append({"name": "verifyingContract", "type": "address"})

    full_message = {
        "types": {
            "EIP712Domain": domain_types,
            "AgreementConsent": CONSENT_EIP712_TYPES["AgreementConsent"],
        },
        "primaryType": CONSENT_PRIMARY_TYPE,
        "domain": domain,
        "message": {
            "agreementHash": agreement_hash,
            "offerId": str(offer_id),
            "dealId": str(deal_id),
            "buyer": Web3.to_checksum_address(buyer_address),
            "seller": Web3.to_checksum_address(seller_address),
            "amount": int(amount_wei),
            "deliveryDeadline": int(delivery_deadline),
            "partyRole": str(party_role),
            "signer": Web3.to_checksum_address(signer_address),
            "nonce": str(nonce),
            "deadline": int(deadline),
        },
    }

    try:
        signable_bytes = encode_typed_data(full_message=full_message)
        recovered = Account.recover_message(signable_bytes, signature=signature)
        return Web3.to_checksum_address(recovered) == Web3.to_checksum_address(signer_address)
    except Exception as exc:
        logger.warning("Consent signature recovery failed: %s", exc)
        return False


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

    # Look up buyer and seller wallets
    buyer = await db.get(User, offer.requirement.buyer_id)
    seller = await db.get(User, offer.seller_id)
    buyer_wallet = Web3.to_checksum_address(buyer.wallet_address) if buyer and buyer.wallet_address else None
    seller_wallet = Web3.to_checksum_address(seller.wallet_address) if seller and seller.wallet_address else None
    if not buyer_wallet or not seller_wallet:
        raise ValueError("Both buyer and seller must have verified wallet addresses linked before agreement consent can be generated.")

    amount_wei = int(Decimal(str(offer.price_eth)) * Decimal(10**18))
    delivery_deadline = int(offer.delivery_deadline)

    # 8. Build EIP-712 payload
    v2_addr = get_configured_v2_address()
    contract_addr = v2_addr if v2_addr else settings.escrow_contract_address
    domain = get_consent_eip712_domain(settings.chain_id, contract_addr)
    types = get_consent_eip712_types()
    message = {
        "agreementHash": authoritative_hash,
        "offerId": str(offer.id),
        "dealId": str(effective_deal_id) if effective_deal_id else "",
        "buyer": buyer_wallet,
        "seller": seller_wallet,
        "amount": amount_wei,
        "deliveryDeadline": delivery_deadline,
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
    buyer = await db.get(User, offer.requirement.buyer_id)
    seller = await db.get(User, offer.seller_id)
    buyer_wallet = Web3.to_checksum_address(buyer.wallet_address) if buyer and buyer.wallet_address else None
    seller_wallet = Web3.to_checksum_address(seller.wallet_address) if seller and seller.wallet_address else None
    if not buyer_wallet or not seller_wallet:
        raise ValueError("Both buyer and seller must have verified wallet addresses linked.")

    amount_wei = int(Decimal(str(offer.price_eth)) * Decimal(10**18))
    delivery_deadline = int(offer.delivery_deadline)

    v2_addr = get_configured_v2_address()
    verifying_contract = Web3.to_checksum_address(v2_addr if v2_addr else settings.escrow_contract_address)

    eip712_full_message = {
        "types": get_consent_eip712_types(),
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
            "buyer": buyer_wallet,
            "seller": seller_wallet,
            "amount": amount_wei,
            "deliveryDeadline": delivery_deadline,
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


async def get_contract_funding_payload(
    db: AsyncSession,
    deal_id: uuid.UUID,
    caller: User,
) -> ContractFundingPayloadResponse:
    """
    Construct a validated, contract-ready dual-consent funding payload for CipherTradeEscrowV2.

    Enforces all Phase 5E.2 security requirements:
      1. Retrieves the authoritative deal.
      2. Retrieves its canonical agreement & authoritative hash.
      3. Retrieves the buyer's verified consent.
      4. Retrieves the seller's verified consent.
      5. Confirms both parties belong to the same deal; ensures only the buyer retrieves the funding payload.
      6. Confirms both consents refer to the exact same agreement hash.
      7. Confirms offer ID and deal ID match authoritative database records.
      8. Confirms both signing wallet addresses match the verified wallet addresses of their respective users.
      9. Confirms both signatures were generated using the approved EIP-712 11-field schema and domain.
      10. Confirms both consent deadlines are still valid.
      11. Confirms consent nonces match actual stored challenge nonces.
      12. Confirms both consents are approved and not revoked, superseded, or otherwise invalidated.
      13. Constructs the exact CommercialAgreement.
      14. Constructs buyerConsent and sellerConsent using their actual stored signatures.
      15. Returns the complete contract-ready payload without mutating nonces or database state.
    """
    settings = get_settings()

    # 0. Strictly require valid V2 contract address before any payload processing.
    # NEVER fall back to legacy escrow_contract_address.
    v2_address = get_configured_v2_address(required=True)
    assert v2_address is not None

    # 1. Retrieve authoritative deal
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
        raise NotFoundError(f"Escrow deal '{deal_id}' not found.")

    offer = deal.offer
    if offer is None or offer.requirement is None:
        raise ValueError("Associated offer or requirement not found for deal.")

    # 2. Check authorization
    if caller.id != deal.buyer_id and caller.id != deal.seller_id:
        raise PermissionError("Access denied: You are not a participant in this deal.")

    if caller.id != deal.buyer_id:
        raise PermissionError("Access denied: Only the buyer can retrieve the funding-ready contract payload.")

    # Buyer wallet ownership check
    if not deal.buyer.wallet_address:
        raise ValueError("Buyer does not have a verified wallet address linked to their account.")
    if not caller.wallet_address or Web3.to_checksum_address(caller.wallet_address) != Web3.to_checksum_address(deal.buyer.wallet_address):
        raise ValueError("Caller wallet does not match registered buyer wallet.")

    if not deal.seller.wallet_address:
        raise ValueError("Seller does not have a verified wallet address linked to their account.")

    # On-chain state check
    if deal.on_chain_state is not None:
        raise ConflictError("Deal has already been funded on-chain.")

    # 3. Retrieve canonical agreement and hash
    _, authoritative_hash = agreement_service.generate_canonical_hash_for_entities(
        offer, offer.requirement, deal_id=deal.id
    )
    expected_hash = authoritative_hash.lower()

    if deal.agreement_hash and deal.agreement_hash.lower() != expected_hash:
        raise ValueError("Deal agreement hash does not match canonical agreement hash.")

    # 4. Retrieve buyer's and seller's verified consents
    stmt_consents = select(AgreementConsent).where(
        AgreementConsent.offer_id == deal.offer_id,
        func.lower(AgreementConsent.agreement_hash) == expected_hash,
    )
    consents = (await db.execute(stmt_consents)).scalars().all()

    buyer_consent = next((c for c in consents if c.party_role == "buyer"), None)
    seller_consent = next((c for c in consents if c.party_role == "seller"), None)

    if buyer_consent is None:
        raise ConflictError("Buyer has not provided agreement consent.")
    if seller_consent is None:
        raise ConflictError("Seller has not provided agreement consent.")

    # 5. Confirm parties belong to same deal
    if buyer_consent.user_id != deal.buyer_id:
        raise ValueError("Buyer consent user does not match deal buyer.")
    if seller_consent.user_id != deal.seller_id:
        raise ValueError("Seller consent user does not match deal seller.")

    # 6. Confirm agreement hash
    if buyer_consent.agreement_hash.lower() != expected_hash or seller_consent.agreement_hash.lower() != expected_hash:
        raise ValueError("Consent agreement hash does not match authoritative agreement terms.")

    # 7. Confirm offer ID and deal ID match
    if buyer_consent.offer_id != deal.offer_id or seller_consent.offer_id != deal.offer_id:
        raise ValueError("Consent offer ID does not match authoritative deal offer ID.")

    if buyer_consent.deal_id and buyer_consent.deal_id != deal.id:
        raise ValueError("Buyer consent deal ID does not match authoritative deal ID.")
    if seller_consent.deal_id and seller_consent.deal_id != deal.id:
        raise ValueError("Seller consent deal ID does not match authoritative deal ID.")

    # 8. Confirm signing wallets match current registered user wallets
    buyer_wallet = Web3.to_checksum_address(deal.buyer.wallet_address)
    seller_wallet = Web3.to_checksum_address(deal.seller.wallet_address)

    if Web3.to_checksum_address(buyer_consent.signer_wallet) != buyer_wallet:
        raise ValueError("Buyer consent signer wallet does not match registered buyer wallet.")
    if Web3.to_checksum_address(seller_consent.signer_wallet) != seller_wallet:
        raise ValueError("Seller consent signer wallet does not match registered seller wallet.")

    # 9. Confirm deadlines are still valid
    current_ts = int(time.time())
    if buyer_consent.deadline < current_ts:
        raise ValueError("Buyer consent has expired. Fresh consent is required before contract funding.")
    if seller_consent.deadline < current_ts:
        raise ValueError("Seller consent has expired. Fresh consent is required before contract funding.")

    # 10. Confirm nonces match actual stored challenges
    stmt_b_ch = select(AgreementConsentChallenge).where(
        AgreementConsentChallenge.nonce == buyer_consent.nonce,
        AgreementConsentChallenge.offer_id == deal.offer_id,
        AgreementConsentChallenge.user_id == deal.buyer_id,
    )
    b_ch = (await db.execute(stmt_b_ch)).scalar_one_or_none()
    if b_ch is None:
        raise ValueError("Authoritative challenge for buyer consent nonce not found.")
    if b_ch.used_at is None:
        raise ValueError("Buyer consent challenge was never completed.")

    stmt_s_ch = select(AgreementConsentChallenge).where(
        AgreementConsentChallenge.nonce == seller_consent.nonce,
        AgreementConsentChallenge.offer_id == deal.offer_id,
        AgreementConsentChallenge.user_id == deal.seller_id,
    )
    s_ch = (await db.execute(stmt_s_ch)).scalar_one_or_none()
    if s_ch is None:
        raise ValueError("Authoritative challenge for seller consent nonce not found.")
    if s_ch.used_at is None:
        raise ValueError("Seller consent challenge was never completed.")

    # Confirm neither consent was superseded by a newer challenge
    stmt_newer_b = select(AgreementConsentChallenge).where(
        AgreementConsentChallenge.offer_id == deal.offer_id,
        AgreementConsentChallenge.user_id == deal.buyer_id,
        AgreementConsentChallenge.created_at > buyer_consent.created_at,
    )
    if (await db.execute(stmt_newer_b)).scalars().first() is not None:
        raise ValueError("Buyer consent has been superseded by a newer consent challenge.")

    stmt_newer_s = select(AgreementConsentChallenge).where(
        AgreementConsentChallenge.offer_id == deal.offer_id,
        AgreementConsentChallenge.user_id == deal.seller_id,
        AgreementConsentChallenge.created_at > seller_consent.created_at,
    )
    if (await db.execute(stmt_newer_s)).scalars().first() is not None:
        raise ValueError("Seller consent has been superseded by a newer consent challenge.")

    # 11. Check chain ID
    if buyer_consent.chain_id != settings.chain_id:
        raise ValueError(f"Buyer consent was issued for chain {buyer_consent.chain_id}, expected {settings.chain_id}.")
    if seller_consent.chain_id != settings.chain_id:
        raise ValueError(f"Seller consent was issued for chain {seller_consent.chain_id}, expected {settings.chain_id}.")

    # 12. Cryptographic signature verification against 11-field schema on V2 domain
    amount_wei = int(Decimal(str(deal.amount_eth)) * Decimal(10**18))
    delivery_deadline = int(deal.deadline_ts)

    b_ok = verify_consent_signature_11_fields(
        agreement_hash=authoritative_hash,
        offer_id=str(deal.offer_id),
        deal_id=str(deal.id),
        buyer_address=buyer_wallet,
        seller_address=seller_wallet,
        amount_wei=amount_wei,
        delivery_deadline=delivery_deadline,
        party_role="buyer",
        signer_address=buyer_wallet,
        nonce=buyer_consent.nonce,
        deadline=buyer_consent.deadline,
        signature=buyer_consent.signature,
        chain_id=buyer_consent.chain_id,
        contract_address=v2_address,
    )
    if not b_ok:
        raise ValueError("Buyer signature is invalid or incompatible with the approved 11-field AgreementConsent schema. Please provide fresh consent.")

    s_ok = verify_consent_signature_11_fields(
        agreement_hash=authoritative_hash,
        offer_id=str(deal.offer_id),
        deal_id=str(deal.id),
        buyer_address=buyer_wallet,
        seller_address=seller_wallet,
        amount_wei=amount_wei,
        delivery_deadline=delivery_deadline,
        party_role="seller",
        signer_address=seller_wallet,
        nonce=seller_consent.nonce,
        deadline=seller_consent.deadline,
        signature=seller_consent.signature,
        chain_id=seller_consent.chain_id,
        contract_address=v2_address,
    )
    if not s_ok:
        raise ValueError("Seller signature is invalid or incompatible with the approved 11-field AgreementConsent schema. Please provide fresh consent.")

    # 13. Construct CommercialAgreement payload
    agreement_payload = CommercialAgreementPayload(
        agreementHash=authoritative_hash,
        offerId=str(deal.offer_id),
        dealId=str(deal.id),
        buyer=buyer_wallet,
        seller=seller_wallet,
        amount=str(amount_wei),
        deliveryDeadline=delivery_deadline,
    )

    # 14. Construct PartyConsentProof payloads
    buyer_proof_payload = PartyConsentProofPayload(
        nonce=buyer_consent.nonce,
        deadline=buyer_consent.deadline,
        signature=buyer_consent.signature,
    )

    seller_proof_payload = PartyConsentProofPayload(
        nonce=seller_consent.nonce,
        deadline=seller_consent.deadline,
        signature=seller_consent.signature,
    )

    # 15. Return complete contract-ready payload
    # Note: contract_address is strictly the configured V2 address (None if not yet configured)
    return ContractFundingPayloadResponse(
        deal_id=deal.id,
        agreement=agreement_payload,
        buyerConsent=buyer_proof_payload,
        sellerConsent=seller_proof_payload,
        contract_address=v2_address,
    )
