"""
Unit and integration tests for Phase 6.2.2.3.4.1: Stable V3 Deal Identity & Bilateral Consent Lifecycle.

Covers:
  1. Stable deal identity (Tests 1-5)
  2. Agreement consistency (Tests 6-10)
  3. Authorization & role boundaries (Tests 11-14)
  4. Routing & version compatibility (Tests 15-18)
  5. Consent & funding lifecycle (Tests 19-21)
"""
from __future__ import annotations

import asyncio
import time
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.config import get_settings
from app.core.token_conversion import to_base_units
from app.core.token_registry import (
    CHAIN_ID_ANVIL,
    ETH_SENTINEL_ADDRESS,
    TokenRegistry,
    TrustLevel,
    get_token_registry,
)
from app.models.models import (
    AgreementConsent,
    EscrowDeal,
    EscrowState,
    Offer,
    OfferStatus,
    Requirement,
    User,
    UserRole,
)
from app.schemas.consent import (
    CommercialAgreementV3Payload,
    ConsentVerifyRequest,
)
from app.schemas.escrow import EscrowDealCreate
from app.schemas.offers import OfferCreate
from app.schemas.requirements import RequirementCreate
from app.services import (
    agreement_service,
    consent_service,
    escrow_service,
    offer_service,
    requirement_service,
)

BUYER_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
BUYER_WALLET = Web3.to_checksum_address("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")

SELLER_PK = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"
SELLER_WALLET = Web3.to_checksum_address("0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")

THIRD_PARTY_PK = "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6"
THIRD_PARTY_WALLET = Web3.to_checksum_address("0x90F79bf6EB2c4f870365E785982E1f101E93b906")

TEST_V1_CONTRACT = Web3.to_checksum_address("0x5FbDB2315678afecb367f032d93F642f64180aa3")
TEST_V2_CONTRACT = Web3.to_checksum_address("0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512")
TEST_V3_CONTRACT = Web3.to_checksum_address("0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0")
TEST_USDC_CONTRACT = Web3.to_checksum_address("0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9")
TEST_USDT_CONTRACT = Web3.to_checksum_address("0xDc64a140Aa3E981100a9becA4E685f962f0cF6C9")
TEST_TREASURY = Web3.to_checksum_address("0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65")


@pytest.fixture(autouse=True)
def configure_v3_test_settings(monkeypatch):
    """Configure settings for multi-token V3 consent lifecycle tests."""
    settings = get_settings()
    monkeypatch.setattr(settings, "chain_id", CHAIN_ID_ANVIL)
    monkeypatch.setattr(settings, "escrow_contract_address", TEST_V1_CONTRACT)
    monkeypatch.setattr(settings, "escrow_v2_contract_address", TEST_V2_CONTRACT)
    monkeypatch.setattr(settings, "escrow_v3_contract_address", TEST_V3_CONTRACT)
    monkeypatch.setattr(settings, "anvil_escrow_v3_contract_address", TEST_V3_CONTRACT)
    monkeypatch.setattr(settings, "anvil_usdc_contract_address", TEST_USDC_CONTRACT)
    monkeypatch.setattr(settings, "anvil_usdt_contract_address", TEST_USDT_CONTRACT)
    monkeypatch.setattr(settings, "treasury_address", TEST_TREASURY)
    monkeypatch.setattr(settings, "platform_fee_bps", 250)
    monkeypatch.setattr(settings, "token_verification_ttl_seconds", 300)

    registry = get_token_registry()
    registry.refresh_registry()
    for token in registry.get_all_tokens(CHAIN_ID_ANVIL):
        verified_token = token.with_verification(
            trust_level=TrustLevel.VERIFIED,
            on_chain_allowed=True,
            is_funding_permitted=token.operator_funding_permitted,
        )
        registry._tokens[(CHAIN_ID_ANVIL, token.symbol)] = verified_token
    yield settings


async def _create_test_users(db: AsyncSession) -> tuple[User, User, User]:
    """Create test buyer, seller, and third party users with verified wallets."""
    buyer = User(
        email=f"buyer_{uuid.uuid4().hex[:6]}@example.com",
        display_name="Test Buyer",
        primary_role=UserRole.BUYER,
        hashed_password="pw",
        wallet_address=BUYER_WALLET,
    )
    seller = User(
        email=f"seller_{uuid.uuid4().hex[:6]}@example.com",
        display_name="Test Seller",
        primary_role=UserRole.SELLER,
        hashed_password="pw",
        wallet_address=SELLER_WALLET,
    )
    third_party = User(
        email=f"third_{uuid.uuid4().hex[:6]}@example.com",
        display_name="Third Party",
        primary_role=UserRole.BUYER,
        hashed_password="pw",
        wallet_address=THIRD_PARTY_WALLET,
    )
    db.add_all([buyer, seller, third_party])
    await db.commit()
    await db.refresh(buyer)
    await db.refresh(seller)
    await db.refresh(third_party)
    return buyer, seller, third_party


async def _create_accepted_offer(
    db: AsyncSession, buyer: User, seller: User, currency: str = "USDC", amount: float = 50.0
) -> tuple[Requirement, Offer]:
    """Helper to create requirement and accepted offer."""
    req = await requirement_service.create_requirement(
        db,
        buyer,
        RequirementCreate(
            title=f"{currency} Job {uuid.uuid4().hex[:4]}",
            description="Detailed requirement description for V3 testing",
            category="Software",
            budget_min=amount * 0.5,
            budget_max=amount * 2.0,
            currency=currency,
        ),
    )
    offer = await offer_service.create_offer(
        db,
        seller,
        OfferCreate(
            requirement_id=req.id,
            amount=amount,
            currency=currency,
            delivery_deadline=int(time.time()) + 86400 * 7,
            terms="Comprehensive terms for V3 test verification",
        ),
    )
    offer.status = OfferStatus.ACCEPTED
    await db.commit()
    await db.refresh(offer)
    return req, offer


def _sign_challenge(challenge, pk: str) -> str:
    """Sign an EIP-712 challenge using eth_account."""
    signable = encode_typed_data(full_message=challenge.typed_data)
    sig = Account.from_key(pk).sign_message(signable).signature.hex()
    return ("0x" + sig) if not sig.startswith("0x") else sig


# ─────────────────────────────────────────────────────────────────────────────
# 1. Stable Deal Identity Tests (Tests 1-5)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_01_first_buyer_challenge_creates_persisted_v3_draft_deal(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=100.0)

    # Offer currently has no escrow_deal
    init_deal = (await db_session.execute(select(EscrowDeal).where(EscrowDeal.offer_id == offer.id))).scalar_one_or_none()
    assert init_deal is None

    # Buyer requests challenge without passing deal_id
    challenge = await consent_service.create_consent_challenge(db_session, buyer, offer.id)

    # Persisted draft deal now exists in DB
    stmt = select(EscrowDeal).where(EscrowDeal.offer_id == offer.id)
    deal = (await db_session.execute(stmt)).scalar_one_or_none()
    assert deal is not None
    assert str(deal.id) == challenge.message["dealId"]
    assert deal.contract_version == "v3"
    assert deal.token_symbol == "USDC"
    assert deal.amount_base_units == "100000000"
    assert deal.on_chain_state is None
    assert deal.chain_deal_id is None
    assert deal.fund_tx_hash is None


@pytest.mark.asyncio
async def test_02_seller_challenge_retrieves_same_draft_deal(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=50.0)

    # 1. Buyer requests challenge
    buyer_chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id)

    # 2. Seller requests challenge
    seller_chal = await consent_service.create_consent_challenge(db_session, seller, offer.id)

    # Both must reference the exact same deal UUID
    assert buyer_chal.message["dealId"] == seller_chal.message["dealId"]

    # Exactly one EscrowDeal row exists for this offer
    deals = (await db_session.execute(select(EscrowDeal).where(EscrowDeal.offer_id == offer.id))).scalars().all()
    assert len(deals) == 1
    assert str(deals[0].id) == buyer_chal.message["dealId"]


@pytest.mark.asyncio
async def test_03_repeated_buyer_challenge_is_idempotent(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=75.0)

    chal_1 = await consent_service.create_consent_challenge(db_session, buyer, offer.id)
    chal_2 = await consent_service.create_consent_challenge(db_session, buyer, offer.id)

    assert chal_1.message["dealId"] == chal_2.message["dealId"]
    assert chal_1.message["agreementHash"] == chal_2.message["agreementHash"]
    assert chal_1.nonce != chal_2.nonce  # Each challenge has its own single-use nonce

    deals = (await db_session.execute(select(EscrowDeal).where(EscrowDeal.offer_id == offer.id))).scalars().all()
    assert len(deals) == 1


@pytest.mark.asyncio
async def test_04_repeated_seller_challenge_is_idempotent(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=80.0)

    s_chal_1 = await consent_service.create_consent_challenge(db_session, seller, offer.id)
    s_chal_2 = await consent_service.create_consent_challenge(db_session, seller, offer.id)

    assert s_chal_1.message["dealId"] == s_chal_2.message["dealId"]
    assert s_chal_1.message["agreementHash"] == s_chal_2.message["agreementHash"]

    deals = (await db_session.execute(select(EscrowDeal).where(EscrowDeal.offer_id == offer.id))).scalars().all()
    assert len(deals) == 1


@pytest.mark.asyncio
async def test_05_create_escrow_deal_record_returns_existing_draft_deal(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=120.0)

    # 1. Challenge creates draft deal
    chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id)

    # 2. Buyer subsequently creates deal via POST /escrow/deals
    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id)
    )

    assert str(deal.id) == chal.message["dealId"]
    assert deal.agreement_hash.lower() == chal.message["agreementHash"].lower()

    deals = (await db_session.execute(select(EscrowDeal).where(EscrowDeal.offer_id == offer.id))).scalars().all()
    assert len(deals) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. Agreement Consistency Tests (Tests 6-10)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_06_both_parties_receive_exact_same_agreement_terms_and_hash(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=60.0)

    b_chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id)
    s_chal = await consent_service.create_consent_challenge(db_session, seller, offer.id)

    # Both parties receive identical commercial terms
    for field in [
        "agreementHash", "previousAgreementHash", "agreementVersion", "offerId",
        "dealId", "buyer", "seller", "token", "amount", "feeBps", "treasury", "deliveryDeadline"
    ]:
        assert b_chal.message[field] == s_chal.message[field], f"Mismatch in field {field}"

    # Only party-specific fields differ
    assert b_chal.message["partyRole"] == "buyer"
    assert s_chal.message["partyRole"] == "seller"
    assert b_chal.message["signer"] == BUYER_WALLET
    assert s_chal.message["signer"] == SELLER_WALLET


@pytest.mark.asyncio
async def test_07_persisted_terms_match_hash_v3_agreement_terms(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=35.0)

    chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id)
    deal = (await db_session.execute(select(EscrowDeal).where(EscrowDeal.offer_id == offer.id))).scalar_one()

    # Independently compute hash
    recomputed = agreement_service.hash_v3_agreement_terms(
        deal_id=str(deal.id),
        agreement_version=1,
        previous_agreement_hash="0x" + "0" * 64,
        offer_id=str(offer.id),
        buyer_address=BUYER_WALLET,
        seller_address=SELLER_WALLET,
        token_address=TEST_USDC_CONTRACT,
        amount_base_units=35000000,
        fee_bps=250,
        treasury_address=TEST_TREASURY,
        delivery_deadline=offer.delivery_deadline,
    )
    assert deal.agreement_hash.lower() == recomputed.lower()
    assert chal.message["agreementHash"].lower() == recomputed.lower()


@pytest.mark.asyncio
async def test_08_tampered_deal_agreement_hash_rejected_at_challenge(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=40.0)

    # Create draft deal
    deal = await escrow_service.get_or_create_draft_deal(db_session, offer.id, requesting_user=buyer)

    # Tamper agreement hash in database
    deal.agreement_hash = "0x" + "f" * 64
    await db_session.commit()

    # Next challenge generation detects discrepancy and rejects
    with pytest.raises(ValueError, match="does not match authoritative terms hash"):
        await consent_service.create_consent_challenge(db_session, buyer, offer.id)


@pytest.mark.asyncio
async def test_09_different_deal_uuids_cannot_share_consent(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req1, offer1 = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=10.0)
    req2, offer2 = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=10.0)

    # Deal 1 consent
    b_c1 = await consent_service.create_consent_challenge(db_session, buyer, offer1.id)
    b_sig1 = _sign_challenge(b_c1, BUYER_PK)
    await consent_service.verify_and_record_consent(
        db_session, buyer, offer1.id, b_c1.nonce, b_sig1
    )

    s_c1 = await consent_service.create_consent_challenge(db_session, seller, offer1.id)
    s_sig1 = _sign_challenge(s_c1, SELLER_PK)
    await consent_service.verify_and_record_consent(
        db_session, seller, offer1.id, s_c1.nonce, s_sig1
    )

    # Consents are valid for Deal 1
    deal1 = (await db_session.execute(select(EscrowDeal).where(EscrowDeal.offer_id == offer1.id))).scalar_one()
    has_consents_1 = await consent_service.check_both_parties_consented(
        db_session, offer1.id, deal1.agreement_hash, deal_id=deal1.id
    )
    assert has_consents_1 is True

    # Consents CANNOT authorize Deal 2
    deal2 = await escrow_service.get_or_create_draft_deal(db_session, offer2.id, requesting_user=buyer)
    has_consents_2 = await consent_service.check_both_parties_consented(
        db_session, offer2.id, deal2.agreement_hash, deal_id=deal2.id
    )
    assert has_consents_2 is False

    # Attempting to check deal 1's hash with deal 2's ID fails
    has_cross_deal = await consent_service.check_both_parties_consented(
        db_session, offer1.id, deal1.agreement_hash, deal_id=deal2.id
    )
    assert has_cross_deal is False


@pytest.mark.asyncio
async def test_10_tampered_signed_message_rejected(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=50.0)

    chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id)

    # Tamper amount in typed data message
    tampered_msg = dict(chal.typed_data)
    tampered_msg["message"] = dict(tampered_msg["message"])
    tampered_msg["message"]["amount"] = 99999999  # Tampered amount

    signable = encode_typed_data(full_message=tampered_msg)
    bad_sig = Account.from_key(BUYER_PK).sign_message(signable).signature.hex()

    with pytest.raises(ValueError, match="Signature is invalid"):
        await consent_service.verify_and_record_consent(
            db_session, buyer, offer.id, chal.nonce, "0x" + bad_sig
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Authorization & Role Boundaries (Tests 11-14)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_11_unrelated_user_cannot_request_consent_challenge(db_session: AsyncSession):
    buyer, seller, third_party = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=20.0)

    with pytest.raises(PermissionError, match="neither the buyer nor the seller"):
        await consent_service.create_consent_challenge(db_session, third_party, offer.id)


@pytest.mark.asyncio
async def test_12_unrelated_user_cannot_create_draft_deal(db_session: AsyncSession):
    buyer, seller, third_party = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=20.0)

    with pytest.raises(PermissionError, match="neither the buyer nor the seller"):
        await escrow_service.get_or_create_draft_deal(db_session, offer.id, requesting_user=third_party)


@pytest.mark.asyncio
async def test_13_user_cannot_verify_challenge_issued_to_other_party(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=25.0)

    # Issue challenge to buyer
    b_chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id)
    b_sig = _sign_challenge(b_chal, BUYER_PK)

    # Seller attempts to submit buyer's challenge nonce
    with pytest.raises(PermissionError, match="issued to a different user"):
        await consent_service.verify_and_record_consent(
            db_session, seller, offer.id, b_chal.nonce, b_sig
        )


@pytest.mark.asyncio
async def test_14_mismatched_wallet_signature_rejected(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=30.0)

    chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id)
    # Sign with third party private key instead of registered buyer key
    bad_sig = _sign_challenge(chal, THIRD_PARTY_PK)

    with pytest.raises(ValueError, match="Signature is invalid"):
        await consent_service.verify_and_record_consent(
            db_session, buyer, offer.id, chal.nonce, bad_sig
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Routing & Version Compatibility (Tests 15-18)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_15_erc20_usdt_routes_to_v3(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDT", amount=45.0)

    chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id)
    assert chal.domain["version"] == "3"
    assert chal.domain["verifyingContract"] == TEST_V3_CONTRACT
    assert chal.message["token"] == TEST_USDT_CONTRACT


@pytest.mark.asyncio
async def test_16_eth_routes_to_v3_when_v3_address_configured(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="ETH", amount=1.5)

    chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id)
    assert chal.domain["version"] == "3"
    assert chal.domain["verifyingContract"] == TEST_V3_CONTRACT
    assert chal.message["token"] == ETH_SENTINEL_ADDRESS
    assert chal.message["amount"] == 1500000000000000000


@pytest.mark.asyncio
async def test_17_explicit_v1_selection_remains_v1(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="ETH", amount=2.0)

    # Explicitly create V1 deal
    v1_deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id, contract_version="v1")
    )
    assert v1_deal.contract_version == "v1"

    # Challenge inherits persisted V1 deal version
    chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id, deal_id=v1_deal.id)
    assert chal.domain["version"] == "1"
    expected_v1 = Web3.to_checksum_address(get_settings().escrow_contract_address)
    assert chal.domain["verifyingContract"] == expected_v1


@pytest.mark.asyncio
async def test_18_explicit_v2_selection_remains_v2(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="ETH", amount=2.0)

    # Explicitly create V2 deal
    v2_deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id, contract_version="v2")
    )
    assert v2_deal.contract_version == "v2"

    chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id, deal_id=v2_deal.id)
    assert chal.domain["version"] == "1"
    assert chal.domain["verifyingContract"] == TEST_V2_CONTRACT


# ─────────────────────────────────────────────────────────────────────────────
# 5. Consent & Funding Lifecycle (Tests 19-21)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_19_one_consent_alone_cannot_authorize_funding(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=15.0)

    b_chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id)
    b_sig = _sign_challenge(b_chal, BUYER_PK)
    await consent_service.verify_and_record_consent(db_session, buyer, offer.id, b_chal.nonce, b_sig)

    deal = (await db_session.execute(select(EscrowDeal).where(EscrowDeal.offer_id == offer.id))).scalar_one()

    # Seller has not consented yet
    has_both = await consent_service.check_both_parties_consented(
        db_session, offer.id, deal.agreement_hash, deal_id=deal.id
    )
    assert has_both is False

    # Contract payload generation blocked
    with pytest.raises(Exception, match="Seller has not provided agreement consent"):
        await consent_service.get_contract_funding_payload(db_session, deal.id, buyer)


@pytest.mark.asyncio
async def test_20_both_matching_consents_authorize_funding_payload(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=15.0)

    b_chal = await consent_service.create_consent_challenge(db_session, buyer, offer.id)
    b_sig = _sign_challenge(b_chal, BUYER_PK)
    await consent_service.verify_and_record_consent(db_session, buyer, offer.id, b_chal.nonce, b_sig)

    s_chal = await consent_service.create_consent_challenge(db_session, seller, offer.id)
    s_sig = _sign_challenge(s_chal, SELLER_PK)
    await consent_service.verify_and_record_consent(db_session, seller, offer.id, s_chal.nonce, s_sig)

    deal = (await db_session.execute(select(EscrowDeal).where(EscrowDeal.offer_id == offer.id))).scalar_one()

    has_both = await consent_service.check_both_parties_consented(
        db_session, offer.id, deal.agreement_hash, deal_id=deal.id
    )
    assert has_both is True

    payload = await consent_service.get_contract_funding_payload(db_session, deal.id, buyer)
    assert payload.contract_version == "v3"
    assert payload.contract_address == TEST_V3_CONTRACT
    assert isinstance(payload.commercial_agreement, CommercialAgreementV3Payload)
    assert payload.commercial_agreement.dealId == str(deal.id)
    assert payload.commercial_agreement.amount == "15000000"


@pytest.mark.asyncio
async def test_21_funded_deal_rejects_new_pre_funding_consent_challenges(db_session: AsyncSession):
    buyer, seller, _ = await _create_test_users(db_session)
    req, offer = await _create_accepted_offer(db_session, buyer, seller, currency="USDC", amount=20.0)

    deal = await escrow_service.get_or_create_draft_deal(db_session, offer.id, requesting_user=buyer)
    deal.on_chain_state = EscrowState.FUNDED
    await db_session.commit()

    with pytest.raises(Exception, match="already been funded"):
        await consent_service.create_consent_challenge(db_session, buyer, offer.id)
