"""
Comprehensive Test Suite for CipherTrade V3 Multi-Token API, Consent & Escrow Service Integration.
(Phase 6.2.2.3.4)

Contains 35 thorough integration tests across:
  1. API & token metadata (Tests 1-8)
  2. Funding authorization (Tests 9-14)
  3. Consent & hashing (Tests 15-21)
  4. Escrow verification (Tests 22-30)
  5. Historical compatibility & safety (Tests 31-35)
"""
from __future__ import annotations

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

from app.config import Settings, get_settings
from app.core.token_conversion import from_base_units, to_base_units
from app.core.token_registry import (
    CHAIN_ID_ANVIL,
    ETH_SENTINEL_ADDRESS,
    TokenMetadata,
    TokenRegistry,
    TokenType,
    TrustLevel,
    VerificationError,
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
from app.schemas.consent import CommercialAgreementV3Payload, ConsentVerifyRequest
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
def configure_test_settings(monkeypatch):
    """Configure settings for multi-token tests."""
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

    # Initialize registry
    registry = get_token_registry()
    registry.refresh_registry()
    yield settings


def _verify_all_in_registry(registry: TokenRegistry):
    """Mark all configured tokens as verified for testing."""
    for token in registry.get_all_tokens(CHAIN_ID_ANVIL):
        verified_token = token.with_verification(
            trust_level=TrustLevel.VERIFIED,
            on_chain_allowed=True,
            is_funding_permitted=token.operator_funding_permitted,
        )
        registry._tokens[(CHAIN_ID_ANVIL, token.symbol)] = verified_token


async def _create_test_users(db: AsyncSession) -> tuple[User, User]:
    """Create test buyer and seller with registered wallets."""
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
    db.add_all([buyer, seller])
    await db.commit()
    await db.refresh(buyer)
    await db.refresh(seller)
    return buyer, seller


# ─────────────────────────────────────────────────────────────────────────────
# 1. API & Token Metadata (Tests 1-8)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_01_requirement_creation_eth_derives_zero_address_and_base_units(db_session: AsyncSession):
    buyer, _ = await _create_test_users(db_session)
    data = RequirementCreate(
        title="ETH Requirement",
        description="Testing ETH defaults",
        category="Software",
        budget_min=0.1,
        budget_max=1.0,
        currency="ETH",
    )
    req = await requirement_service.create_requirement(db_session, buyer, data)
    assert req.currency == "ETH"
    assert req.token_address == ETH_SENTINEL_ADDRESS
    assert req.token_decimals == 18
    assert req.budget_min_base_units == "100000000000000000"
    assert req.budget_max_base_units == "1000000000000000000"


@pytest.mark.asyncio
async def test_02_requirement_creation_usdc_derives_authoritative_address_and_base_units(db_session: AsyncSession):
    buyer, _ = await _create_test_users(db_session)
    data = RequirementCreate(
        title="USDC Requirement",
        description="Testing USDC defaults",
        category="Software",
        budget_min=10.5,
        budget_max=50.0,
        currency="USDC",
    )
    req = await requirement_service.create_requirement(db_session, buyer, data)
    assert req.currency == "USDC"
    assert req.token_address == TEST_USDC_CONTRACT
    assert req.token_decimals == 6
    assert req.budget_min_base_units == "10500000"
    assert req.budget_max_base_units == "50000000"


@pytest.mark.asyncio
async def test_03_requirement_creation_usdt_mock_derives_authoritative_address_and_base_units(db_session: AsyncSession):
    buyer, _ = await _create_test_users(db_session)
    data = RequirementCreate(
        title="USDT Requirement",
        description="Testing USDT mock",
        category="Software",
        budget_min=5.0,
        budget_max=20.0,
        currency="USDT",
    )
    req = await requirement_service.create_requirement(db_session, buyer, data)
    assert req.currency == "USDT"
    assert req.token_address == TEST_USDT_CONTRACT
    assert req.token_decimals == 6
    assert req.budget_min_base_units == "5000000"
    assert req.budget_max_base_units == "20000000"


def test_04_requirement_creation_unsupported_symbol_rejected():
    with pytest.raises(ValueError, match=r"(not configured|not supported|Unsupported)"):
        RequirementCreate(
            title="DAI Requirement",
            description="Testing unsupported DAI",
            category="Software",
            budget_min=1.0,
            budget_max=10.0,
            currency="DAI",
        )


def test_05_requirement_creation_caller_overridden_token_address_rejected():
    with pytest.raises(ValueError, match="does not match authoritative registry address"):
        RequirementCreate(
            title="USDC Override Address",
            description="Trying to override token_address",
            category="Software",
            budget_min=10.0,
            budget_max=50.0,
            currency="USDC",
            token_address="0x1111111111111111111111111111111111111111",
        )


def test_06_requirement_creation_caller_overridden_decimals_rejected():
    with pytest.raises(ValueError, match="does not match authoritative registry decimals"):
        RequirementCreate(
            title="USDC Override Decimals",
            description="Trying to override decimals",
            category="Software",
            budget_min=10.0,
            budget_max=50.0,
            currency="USDC",
            token_decimals=18,
        )


def test_07_requirement_creation_fractional_base_unit_excess_precision_rejected():
    with pytest.raises(ValueError, match="fractional base units"):
        RequirementCreate(
            title="USDC Excess Precision",
            description="Too many decimal places",
            category="Software",
            budget_min=0.0000001,  # 7 decimals for 6-decimal token
            budget_max=10.0,
            currency="USDC",
        )


@pytest.mark.asyncio
async def test_08_offer_creation_enforces_matching_currency_and_derives_base_units(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    req_data = RequirementCreate(
        title="USDC Job",
        description="Software development",
        category="Software",
        budget_min=10.0,
        budget_max=100.0,
        currency="USDC",
    )
    req = await requirement_service.create_requirement(db_session, buyer, req_data)

    # 1. Currency mismatch rejected
    offer_bad = OfferCreate(
        requirement_id=req.id,
        amount=25.0,
        currency="ETH",
        delivery_deadline=int(time.time()) + 86400,
        terms="Detailed offer terms for testing",
    )
    with pytest.raises(ValueError, match="does not match requirement currency"):
        await offer_service.create_offer(db_session, seller, offer_bad)

    # 2. Currency match accepted
    offer_good = OfferCreate(
        requirement_id=req.id,
        amount=25.5,
        currency="USDC",
        delivery_deadline=int(time.time()) + 86400,
        terms="Detailed offer terms for testing",
    )
    offer = await offer_service.create_offer(db_session, seller, offer_good)
    assert offer.currency == "USDC"
    assert offer.token_address == TEST_USDC_CONTRACT
    assert offer.token_decimals == 6
    assert offer.price_base_units == "25500000"
    assert offer.price_eth == Decimal("25.5")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Funding Authorization (Tests 9-14)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_09_native_eth_with_verified_registry_creates_v3_deal_successfully(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="ETH Req", description="Detailed requirement description for testing", category="Software", budget_min=0.5, budget_max=1.5, currency="ETH"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=1.0, currency="ETH", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id, contract_version="v3"), offer=offer, requirement=req
    )
    assert deal.contract_version == "v3"
    assert deal.token_symbol == "ETH"
    assert deal.token_address == ETH_SENTINEL_ADDRESS
    assert deal.token_decimals == 18
    assert deal.amount_base_units == "1000000000000000000"
    assert deal.contract_address == TEST_V3_CONTRACT


@pytest.mark.asyncio
async def test_10_erc20_usdc_with_verified_registry_creates_v3_deal(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=20.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )
    assert deal.contract_version == "v3"
    assert deal.token_symbol == "USDC"
    assert deal.token_address == TEST_USDC_CONTRACT
    assert deal.token_decimals == 6
    assert deal.amount_base_units == "20000000"


@pytest.mark.asyncio
async def test_11_erc20_usdt_with_verified_registry_creates_v3_deal(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDT Req", description="Detailed requirement description for testing", category="Software", budget_min=5.0, budget_max=20.0, currency="USDT"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=10.0, currency="USDT", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )
    assert deal.contract_version == "v3"
    assert deal.token_symbol == "USDT"
    assert deal.token_address == TEST_USDT_CONTRACT
    assert deal.token_decimals == 6
    assert deal.amount_base_units == "10000000"


@pytest.mark.asyncio
async def test_12_offer_with_unverified_token_expired_ttl_fails_deal_creation(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    # Mark USDC verified but expired (verified 600s ago, TTL is 300s)
    usdc = registry.get_token(CHAIN_ID_ANVIL, "USDC")
    expired_usdc = usdc.with_verification(
        trust_level=TrustLevel.VERIFIED,
        on_chain_allowed=True,
        is_funding_permitted=True,
        verified_at=time.time() - 600,
        verified_at_monotonic=time.monotonic() - 600,
    )
    registry._tokens[(CHAIN_ID_ANVIL, "USDC")] = expired_usdc

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=20.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    # ensure_verified_for_funding will try to re-verify on-chain and fail because RPC is offline
    with patch("app.core.token_registry.get_web3") as mock_w3_fn:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = False
        mock_w3_fn.return_value = mock_w3
        with pytest.raises(VerificationError):
            await escrow_service.create_escrow_deal_record(
                db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
            )


@pytest.mark.asyncio
async def test_13_offer_with_unverified_token_never_verified_fails_deal_creation(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    registry.refresh_registry()  # All tokens CONFIGURED, trust_level != VERIFIED

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=20.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    with patch("app.core.token_registry.get_web3") as mock_w3_fn:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = False
        mock_w3_fn.return_value = mock_w3
        with pytest.raises(VerificationError):
            await escrow_service.create_escrow_deal_record(
                db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
            )


@pytest.mark.asyncio
async def test_14_offer_with_funding_disabled_token_fails_deal_creation(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    # Mark token verified, but operator funding permitted is False
    usdc = registry.get_token(CHAIN_ID_ANVIL, "USDC")
    disabled_usdc = TokenMetadata(
        symbol=usdc.symbol,
        name=usdc.name,
        chain_id=usdc.chain_id,
        token_address=usdc.token_address,
        decimals=usdc.decimals,
        token_type=usdc.token_type,
        is_enabled=True,
        operator_funding_permitted=False,
        is_funding_permitted=False,
        on_chain_allowed=True,
        trust_level=TrustLevel.VERIFIED,
        verified_at=time.time(),
        verified_at_monotonic=time.monotonic(),
    )
    registry._tokens[(CHAIN_ID_ANVIL, "USDC")] = disabled_usdc

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=20.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    with pytest.raises(VerificationError, match="cannot fund"):
        await escrow_service.create_escrow_deal_record(
            db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Consent & Hashing (Tests 15-21)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_15_v3_consent_challenge_includes_16_fields_with_correct_types(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=25.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    challenge = await consent_service.create_consent_challenge(db_session, buyer, offer.id, deal.id)
    msg = challenge.typed_data["message"]

    expected_fields = [
        "agreementHash", "previousAgreementHash", "agreementVersion", "offerId", "dealId",
        "buyer", "seller", "token", "amount", "feeBps", "treasury", "deliveryDeadline",
        "partyRole", "signer", "nonce", "deadline",
    ]
    assert len(expected_fields) == 16
    for f in expected_fields:
        assert f in msg, f"Field {f} missing from message"

    assert msg["agreementVersion"] == 1
    assert msg["token"] == TEST_USDC_CONTRACT
    assert msg["amount"] == 25000000
    assert msg["feeBps"] == 250
    assert msg["treasury"] == TEST_TREASURY


@pytest.mark.asyncio
async def test_16_v3_consent_challenge_generates_matching_eip712_domain_version_3(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=25.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    challenge = await consent_service.create_consent_challenge(db_session, buyer, offer.id, deal.id)
    domain = challenge.typed_data["domain"]
    assert domain["name"] == "CipherTrade"
    assert domain["version"] == "3"
    assert domain["chainId"] == CHAIN_ID_ANVIL
    assert domain["verifyingContract"] == TEST_V3_CONTRACT


@pytest.mark.asyncio
async def test_17_v3_consent_verification_accepts_valid_buyer_and_seller_signatures(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=25.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    # Buyer signs
    b_challenge = await consent_service.create_consent_challenge(db_session, buyer, offer.id, deal.id)
    b_signable = encode_typed_data(full_message=b_challenge.typed_data)
    b_sig = Account.sign_message(b_signable, private_key=BUYER_PK).signature.hex()
    b_res = await consent_service.verify_and_record_consent(
        db_session, buyer, offer.id, ConsentVerifyRequest(challenge_id=b_challenge.challenge_id, signature=b_sig), deal.id
    )
    assert b_res.verified is True
    assert b_res.party_role == "buyer"

    # Seller signs
    s_challenge = await consent_service.create_consent_challenge(db_session, seller, offer.id, deal.id)
    s_signable = encode_typed_data(full_message=s_challenge.typed_data)
    s_sig = Account.sign_message(s_signable, private_key=SELLER_PK).signature.hex()
    s_res = await consent_service.verify_and_record_consent(
        db_session, seller, offer.id, ConsentVerifyRequest(challenge_id=s_challenge.challenge_id, signature=s_sig), deal.id
    )
    assert s_res.verified is True
    assert s_res.party_role == "seller"

    has_both = await consent_service.check_both_parties_consented(db_session, offer.id, deal.agreement_hash)
    assert has_both is True


@pytest.mark.asyncio
async def test_18_v3_consent_verification_rejects_signature_with_wrong_token_address(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=25.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    b_challenge = await consent_service.create_consent_challenge(db_session, buyer, offer.id, deal.id)
    # Tamper typed data before signing: change token address
    tampered_data = dict(b_challenge.typed_data)
    tampered_data["message"] = dict(tampered_data["message"])
    tampered_data["message"]["token"] = ETH_SENTINEL_ADDRESS
    tampered_signable = encode_typed_data(full_message=tampered_data)
    bad_sig = Account.sign_message(tampered_signable, private_key=BUYER_PK).signature.hex()

    with pytest.raises(ValueError, match="does not match expected signer"):
        await consent_service.verify_and_record_consent(
            db_session, buyer, offer.id, ConsentVerifyRequest(challenge_id=b_challenge.challenge_id, signature=bad_sig), deal.id
        )


@pytest.mark.asyncio
async def test_19_v3_consent_verification_rejects_signature_with_wrong_amount_base_units(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=25.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    b_challenge = await consent_service.create_consent_challenge(db_session, buyer, offer.id, deal.id)
    # Tamper amount in typed data before signing
    tampered_data = dict(b_challenge.typed_data)
    tampered_data["message"] = dict(tampered_data["message"])
    tampered_data["message"]["amount"] = 99999999
    tampered_signable = encode_typed_data(full_message=tampered_data)
    bad_sig = Account.sign_message(tampered_signable, private_key=BUYER_PK).signature.hex()

    with pytest.raises(ValueError, match="does not match expected signer"):
        await consent_service.verify_and_record_consent(
            db_session, buyer, offer.id, ConsentVerifyRequest(challenge_id=b_challenge.challenge_id, signature=bad_sig), deal.id
        )


@pytest.mark.asyncio
async def test_20_funding_payload_returns_commercial_agreement_v3_payload_for_v3_deal(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=25.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    # Consent both parties
    b_c = await consent_service.create_consent_challenge(db_session, buyer, offer.id, deal.id)
    b_sig = Account.sign_message(encode_typed_data(full_message=b_c.typed_data), private_key=BUYER_PK).signature.hex()
    await consent_service.verify_and_record_consent(db_session, buyer, offer.id, ConsentVerifyRequest(challenge_id=b_c.challenge_id, signature=b_sig), deal.id)

    s_c = await consent_service.create_consent_challenge(db_session, seller, offer.id, deal.id)
    s_sig = Account.sign_message(encode_typed_data(full_message=s_c.typed_data), private_key=SELLER_PK).signature.hex()
    await consent_service.verify_and_record_consent(db_session, seller, offer.id, ConsentVerifyRequest(challenge_id=s_c.challenge_id, signature=s_sig), deal.id)

    payload_resp = await consent_service.get_contract_funding_payload(db_session, buyer, offer.id, deal.id)
    assert payload_resp.contract_version == "v3"
    assert isinstance(payload_resp.commercial_agreement, CommercialAgreementV3Payload)
    assert payload_resp.commercial_agreement.token == TEST_USDC_CONTRACT
    assert payload_resp.commercial_agreement.amount == 25000000
    assert payload_resp.commercial_agreement.fee_bps == 250


@pytest.mark.asyncio
async def test_21_funding_payload_returns_legacy_commercial_agreement_payload_for_v1_v2_deal(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    settings = get_settings()

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="ETH Req", description="Detailed requirement description for testing", category="Software", budget_min=0.5, budget_max=2.0, currency="ETH"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=1.0, currency="ETH", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    # Create explicit V2 deal
    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id, contract_version="v2"), offer=offer, requirement=req
    )

    # Consent both parties
    b_c = await consent_service.create_consent_challenge(db_session, buyer, offer.id, deal.id)
    b_sig = Account.sign_message(encode_typed_data(full_message=b_c.typed_data), private_key=BUYER_PK).signature.hex()
    await consent_service.verify_and_record_consent(db_session, buyer, offer.id, ConsentVerifyRequest(challenge_id=b_c.challenge_id, signature=b_sig), deal.id)

    s_c = await consent_service.create_consent_challenge(db_session, seller, offer.id, deal.id)
    s_sig = Account.sign_message(encode_typed_data(full_message=s_c.typed_data), private_key=SELLER_PK).signature.hex()
    await consent_service.verify_and_record_consent(db_session, seller, offer.id, ConsentVerifyRequest(challenge_id=s_c.challenge_id, signature=s_sig), deal.id)

    payload_resp = await consent_service.get_contract_funding_payload(db_session, buyer, offer.id, deal.id)
    assert payload_resp.contract_version == "v2"
    assert hasattr(payload_resp.commercial_agreement, "amount_wei")
    assert payload_resp.commercial_agreement.amount_wei == 1000000000000000000


# ─────────────────────────────────────────────────────────────────────────────
# 4. Escrow Verification (Tests 22-30)
# ─────────────────────────────────────────────────────────────────────────────

def _setup_mock_web3_v3(
    deal: EscrowDeal,
    tx_val: int,
    event_token: str,
    event_amount: int,
    transfer_events: list | None = None,
    getdeal_token: str | None = None,
    getdeal_amount: int | None = None,
):
    """Setup mock Web3 environment for V3 verification."""
    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = CHAIN_ID_ANVIL

    tx_hash = "0x" + "a" * 64
    mock_tx = {
        "hash": tx_hash,
        "from": BUYER_WALLET,
        "to": TEST_V3_CONTRACT,
        "value": tx_val,
        "blockNumber": 12345,
        "chainId": CHAIN_ID_ANVIL,
    }
    mock_w3.eth.get_transaction.return_value = mock_tx

    mock_receipt = {
        "transactionHash": tx_hash,
        "blockNumber": 12345,
        "status": 1,
        "to": TEST_V3_CONTRACT,
    }
    mock_w3.eth.get_transaction_receipt.return_value = mock_receipt

    # Mock contract events
    mock_contract = MagicMock()
    v3_event = {
        "address": TEST_V3_CONTRACT,
        "args": {
            "chainDealId": 1,
            "dealUuidHash": Web3.keccak(text=str(deal.id)),
            "buyer": BUYER_WALLET,
            "seller": SELLER_WALLET,
            "token": event_token,
            "amount": event_amount,
            "feeBps": deal.fee_bps,
            "treasury": deal.treasury_address,
            "deliveryDeadline": deal.deadline_ts,
            "agreementHash": deal.agreement_hash,
            "agreementVersion": getattr(deal, "agreement_version", 1) or 1,
        },
    }
    mock_contract.events.DealFunded().process_receipt.return_value = [v3_event]

    # getDeal return tuple (11 fields)
    gd_token = getdeal_token or event_token
    gd_amount = getdeal_amount or event_amount
    mock_contract.functions.getDeal(1).call.return_value = (
        Web3.keccak(text=str(deal.id)),
        BUYER_WALLET,
        SELLER_WALLET,
        gd_token,
        gd_amount,
        deal.fee_bps,
        deal.treasury_address,
        deal.deadline_ts,
        0,  # State.FUNDED
        False,  # fundsWithdrawn
        deal.agreement_hash,
    )

    # Mock ERC20 Transfer events contract
    mock_erc20_contract = MagicMock()
    mock_erc20_contract.events.Transfer().process_receipt.return_value = transfer_events or []

    def contract_factory(address=None, abi=None):
        if Web3.to_checksum_address(address) == TEST_V3_CONTRACT:
            return mock_contract
        return mock_erc20_contract

    mock_w3.eth.contract = contract_factory
    return mock_w3, tx_hash


@pytest.mark.asyncio
async def test_22_successful_v3_eth_deal_funding_verification(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="ETH Req", description="Detailed requirement description for testing", category="Software", budget_min=0.5, budget_max=1.5, currency="ETH"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=1.0, currency="ETH", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id, contract_version="v3"), offer=offer, requirement=req
    )

    # Consent
    for u, pk in [(buyer, BUYER_PK), (seller, SELLER_PK)]:
        c = await consent_service.create_consent_challenge(db_session, u, offer.id, deal.id)
        sig = Account.sign_message(encode_typed_data(full_message=c.typed_data), private_key=pk).signature.hex()
        await consent_service.verify_and_record_consent(db_session, u, offer.id, ConsentVerifyRequest(challenge_id=c.challenge_id, signature=sig), deal.id)

    mock_w3, tx_hash = _setup_mock_web3_v3(deal, tx_val=1000000000000000000, event_token=ETH_SENTINEL_ADDRESS, event_amount=1000000000000000000)

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        verified_deal = await escrow_service.verify_funding_transaction(db_session, deal, tx_hash, buyer)
        assert verified_deal.on_chain_state == EscrowState.FUNDED
        assert verified_deal.fund_tx_hash == tx_hash.lower()


@pytest.mark.asyncio
async def test_23_successful_v3_usdc_deal_funding_verification(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=20.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    for u, pk in [(buyer, BUYER_PK), (seller, SELLER_PK)]:
        c = await consent_service.create_consent_challenge(db_session, u, offer.id, deal.id)
        sig = Account.sign_message(encode_typed_data(full_message=c.typed_data), private_key=pk).signature.hex()
        await consent_service.verify_and_record_consent(db_session, u, offer.id, ConsentVerifyRequest(challenge_id=c.challenge_id, signature=sig), deal.id)

    transfer_event = {
        "address": TEST_USDC_CONTRACT,
        "args": {
            "from": BUYER_WALLET,
            "to": TEST_V3_CONTRACT,
            "value": 20000000,
        },
    }
    mock_w3, tx_hash = _setup_mock_web3_v3(deal, tx_val=0, event_token=TEST_USDC_CONTRACT, event_amount=20000000, transfer_events=[transfer_event])

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        verified_deal = await escrow_service.verify_funding_transaction(db_session, deal, tx_hash, buyer)
        assert verified_deal.on_chain_state == EscrowState.FUNDED


@pytest.mark.asyncio
async def test_24_successful_v3_usdt_deal_funding_verification(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDT Req", description="Detailed requirement description for testing", category="Software", budget_min=5.0, budget_max=20.0, currency="USDT"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=10.0, currency="USDT", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    for u, pk in [(buyer, BUYER_PK), (seller, SELLER_PK)]:
        c = await consent_service.create_consent_challenge(db_session, u, offer.id, deal.id)
        sig = Account.sign_message(encode_typed_data(full_message=c.typed_data), private_key=pk).signature.hex()
        await consent_service.verify_and_record_consent(db_session, u, offer.id, ConsentVerifyRequest(challenge_id=c.challenge_id, signature=sig), deal.id)

    transfer_event = {
        "address": TEST_USDT_CONTRACT,
        "args": {
            "from": BUYER_WALLET,
            "to": TEST_V3_CONTRACT,
            "value": 10000000,
        },
    }
    mock_w3, tx_hash = _setup_mock_web3_v3(deal, tx_val=0, event_token=TEST_USDT_CONTRACT, event_amount=10000000, transfer_events=[transfer_event])

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        verified_deal = await escrow_service.verify_funding_transaction(db_session, deal, tx_hash, buyer)
        assert verified_deal.on_chain_state == EscrowState.FUNDED


@pytest.mark.asyncio
async def test_25_erc20_funding_with_non_zero_tx_value_rejected(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=20.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    for u, pk in [(buyer, BUYER_PK), (seller, SELLER_PK)]:
        c = await consent_service.create_consent_challenge(db_session, u, offer.id, deal.id)
        sig = Account.sign_message(encode_typed_data(full_message=c.typed_data), private_key=pk).signature.hex()
        await consent_service.verify_and_record_consent(db_session, u, offer.id, ConsentVerifyRequest(challenge_id=c.challenge_id, signature=sig), deal.id)

    # tx_val = 500 (non-zero)
    mock_w3, tx_hash = _setup_mock_web3_v3(deal, tx_val=500, event_token=TEST_USDC_CONTRACT, event_amount=20000000)

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        with pytest.raises(ValueError, match="Transaction value must be 0 for ERC20 token funding"):
            await escrow_service.verify_funding_transaction(db_session, deal, tx_hash, buyer)


@pytest.mark.asyncio
async def test_26_erc20_funding_without_transfer_event_rejected(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=20.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    for u, pk in [(buyer, BUYER_PK), (seller, SELLER_PK)]:
        c = await consent_service.create_consent_challenge(db_session, u, offer.id, deal.id)
        sig = Account.sign_message(encode_typed_data(full_message=c.typed_data), private_key=pk).signature.hex()
        await consent_service.verify_and_record_consent(db_session, u, offer.id, ConsentVerifyRequest(challenge_id=c.challenge_id, signature=sig), deal.id)

    # No transfer events in receipt logs
    mock_w3, tx_hash = _setup_mock_web3_v3(deal, tx_val=0, event_token=TEST_USDC_CONTRACT, event_amount=20000000, transfer_events=[])

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        with pytest.raises(ValueError, match="No matching ERC20 Transfer event found"):
            await escrow_service.verify_funding_transaction(db_session, deal, tx_hash, buyer)


@pytest.mark.asyncio
async def test_27_erc20_funding_with_transfer_event_from_wrong_sender_rejected(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=20.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    for u, pk in [(buyer, BUYER_PK), (seller, SELLER_PK)]:
        c = await consent_service.create_consent_challenge(db_session, u, offer.id, deal.id)
        sig = Account.sign_message(encode_typed_data(full_message=c.typed_data), private_key=pk).signature.hex()
        await consent_service.verify_and_record_consent(db_session, u, offer.id, ConsentVerifyRequest(challenge_id=c.challenge_id, signature=sig), deal.id)

    transfer_event = {
        "address": TEST_USDC_CONTRACT,
        "args": {
            "from": THIRD_PARTY_WALLET,  # Wrong sender!
            "to": TEST_V3_CONTRACT,
            "value": 20000000,
        },
    }
    mock_w3, tx_hash = _setup_mock_web3_v3(deal, tx_val=0, event_token=TEST_USDC_CONTRACT, event_amount=20000000, transfer_events=[transfer_event])

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        with pytest.raises(ValueError, match="No matching ERC20 Transfer event found"):
            await escrow_service.verify_funding_transaction(db_session, deal, tx_hash, buyer)


@pytest.mark.asyncio
async def test_28_erc20_funding_with_transfer_event_to_wrong_recipient_rejected(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=20.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    for u, pk in [(buyer, BUYER_PK), (seller, SELLER_PK)]:
        c = await consent_service.create_consent_challenge(db_session, u, offer.id, deal.id)
        sig = Account.sign_message(encode_typed_data(full_message=c.typed_data), private_key=pk).signature.hex()
        await consent_service.verify_and_record_consent(db_session, u, offer.id, ConsentVerifyRequest(challenge_id=c.challenge_id, signature=sig), deal.id)

    transfer_event = {
        "address": TEST_USDC_CONTRACT,
        "args": {
            "from": BUYER_WALLET,
            "to": THIRD_PARTY_WALLET,  # Wrong recipient!
            "value": 20000000,
        },
    }
    mock_w3, tx_hash = _setup_mock_web3_v3(deal, tx_val=0, event_token=TEST_USDC_CONTRACT, event_amount=20000000, transfer_events=[transfer_event])

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        with pytest.raises(ValueError, match="No matching ERC20 Transfer event found"):
            await escrow_service.verify_funding_transaction(db_session, deal, tx_hash, buyer)


@pytest.mark.asyncio
async def test_29_erc20_funding_with_transfer_event_amount_mismatch_rejected(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=20.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    for u, pk in [(buyer, BUYER_PK), (seller, SELLER_PK)]:
        c = await consent_service.create_consent_challenge(db_session, u, offer.id, deal.id)
        sig = Account.sign_message(encode_typed_data(full_message=c.typed_data), private_key=pk).signature.hex()
        await consent_service.verify_and_record_consent(db_session, u, offer.id, ConsentVerifyRequest(challenge_id=c.challenge_id, signature=sig), deal.id)

    transfer_event = {
        "address": TEST_USDC_CONTRACT,
        "args": {
            "from": BUYER_WALLET,
            "to": TEST_V3_CONTRACT,
            "value": 19999999,  # Off by 1 base unit!
        },
    }
    mock_w3, tx_hash = _setup_mock_web3_v3(deal, tx_val=0, event_token=TEST_USDC_CONTRACT, event_amount=20000000, transfer_events=[transfer_event])

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        with pytest.raises(ValueError, match="No matching ERC20 Transfer event found"):
            await escrow_service.verify_funding_transaction(db_session, deal, tx_hash, buyer)


@pytest.mark.asyncio
async def test_30_v3_getdeal_state_mismatch_rejected(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    _verify_all_in_registry(registry)

    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="USDC Req", description="Detailed requirement description for testing", category="Software", budget_min=10.0, budget_max=50.0, currency="USDC"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=20.0, currency="USDC", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id), offer=offer, requirement=req
    )

    for u, pk in [(buyer, BUYER_PK), (seller, SELLER_PK)]:
        c = await consent_service.create_consent_challenge(db_session, u, offer.id, deal.id)
        sig = Account.sign_message(encode_typed_data(full_message=c.typed_data), private_key=pk).signature.hex()
        await consent_service.verify_and_record_consent(db_session, u, offer.id, ConsentVerifyRequest(challenge_id=c.challenge_id, signature=sig), deal.id)

    transfer_event = {
        "address": TEST_USDC_CONTRACT,
        "args": {
            "from": BUYER_WALLET,
            "to": TEST_V3_CONTRACT,
            "value": 20000000,
        },
    }
    # getDeal returns wrong token address
    mock_w3, tx_hash = _setup_mock_web3_v3(
        deal,
        tx_val=0,
        event_token=TEST_USDC_CONTRACT,
        event_amount=20000000,
        transfer_events=[transfer_event],
        getdeal_token=TEST_USDT_CONTRACT,
    )

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        with pytest.raises(ValueError, match="On-chain deal token does not match"):
            await escrow_service.verify_funding_transaction(db_session, deal, tx_hash, buyer)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Historical Compatibility & Safety (Tests 31-35)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_31_legacy_v1_deal_created_and_verified_without_token_fields(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="Legacy V1", description="Detailed requirement description for testing", category="Software", budget_min=0.5, budget_max=1.5, currency="ETH"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=1.0, currency="ETH", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id, contract_version="v1"), offer=offer, requirement=req
    )
    assert deal.contract_version == "v1"

    # Consent
    for u, pk in [(buyer, BUYER_PK), (seller, SELLER_PK)]:
        c = await consent_service.create_consent_challenge(db_session, u, offer.id, deal.id)
        sig = Account.sign_message(encode_typed_data(full_message=c.typed_data), private_key=pk).signature.hex()
        await consent_service.verify_and_record_consent(db_session, u, offer.id, ConsentVerifyRequest(challenge_id=c.challenge_id, signature=sig), deal.id)

    # Mock V1 verification
    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = CHAIN_ID_ANVIL
    tx_hash = "0x" + "b" * 64
    mock_w3.eth.get_transaction.return_value = {
        "hash": tx_hash, "from": BUYER_WALLET, "to": TEST_V1_CONTRACT, "value": 1000000000000000000, "blockNumber": 100, "chainId": CHAIN_ID_ANVIL
    }
    mock_w3.eth.get_transaction_receipt.return_value = {
        "transactionHash": tx_hash, "blockNumber": 100, "status": 1, "to": TEST_V1_CONTRACT
    }
    mock_contract = MagicMock()
    mock_contract.events.DealFunded().process_receipt.return_value = [{
        "address": TEST_V1_CONTRACT,
        "args": {
            "dealId": 1,
            "buyer": BUYER_WALLET,
            "seller": SELLER_WALLET,
            "amount": 1000000000000000000,
            "deadline": deal.deadline_ts,
            "agreementHash": deal.agreement_hash,
        }
    }]
    mock_contract.functions.getDeal(1).call.return_value = (
        BUYER_WALLET, SELLER_WALLET, 1000000000000000000, deal.deadline_ts, deal.agreement_hash, 0, False
    )
    mock_w3.eth.contract = lambda address, abi: mock_contract

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        verified_deal = await escrow_service.verify_funding_transaction(db_session, deal, tx_hash, buyer)
        assert verified_deal.on_chain_state == EscrowState.FUNDED
        assert verified_deal.contract_version == "v1"


@pytest.mark.asyncio
async def test_32_legacy_v2_deal_created_and_verified_with_deal_uuid_hash_and_delivery_deadline(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    req = await requirement_service.create_requirement(
        db_session, buyer, RequirementCreate(
            title="Legacy V2", description="Detailed requirement description for testing", category="Software", budget_min=0.5, budget_max=1.5, currency="ETH"
        )
    )
    offer = await offer_service.create_offer(
        db_session, seller, OfferCreate(
            requirement_id=req.id, amount=1.0, currency="ETH", delivery_deadline=int(time.time()) + 86400, terms="Detailed offer terms for testing"
        )
    )
    offer.status = OfferStatus.ACCEPTED
    await db_session.commit()

    deal = await escrow_service.create_escrow_deal_record(
        db_session, buyer.id, seller.id, EscrowDealCreate(offer_id=offer.id, contract_version="v2"), offer=offer, requirement=req
    )
    assert deal.contract_version == "v2"

    # Consent
    for u, pk in [(buyer, BUYER_PK), (seller, SELLER_PK)]:
        c = await consent_service.create_consent_challenge(db_session, u, offer.id, deal.id)
        sig = Account.sign_message(encode_typed_data(full_message=c.typed_data), private_key=pk).signature.hex()
        await consent_service.verify_and_record_consent(db_session, u, offer.id, ConsentVerifyRequest(challenge_id=c.challenge_id, signature=sig), deal.id)

    # Mock V2 verification
    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = CHAIN_ID_ANVIL
    tx_hash = "0x" + "c" * 64
    mock_w3.eth.get_transaction.return_value = {
        "hash": tx_hash, "from": BUYER_WALLET, "to": TEST_V2_CONTRACT, "value": 1000000000000000000, "blockNumber": 101, "chainId": CHAIN_ID_ANVIL
    }
    mock_w3.eth.get_transaction_receipt.return_value = {
        "transactionHash": tx_hash, "blockNumber": 101, "status": 1, "to": TEST_V2_CONTRACT
    }
    mock_contract = MagicMock()
    mock_contract.events.DealFunded().process_receipt.return_value = [{
        "address": TEST_V2_CONTRACT,
        "args": {
            "chainDealId": 1,
            "dealUuid": str(deal.id),
            "dealUuidHash": Web3.keccak(text=str(deal.id)),
            "buyer": BUYER_WALLET,
            "seller": SELLER_WALLET,
            "amount": 1000000000000000000,
            "deliveryDeadline": deal.deadline_ts,
            "agreementHash": deal.agreement_hash,
        }
    }]
    mock_contract.functions.getDeal(1).call.return_value = (
        BUYER_WALLET, SELLER_WALLET, 1000000000000000000, deal.deadline_ts, deal.agreement_hash, Web3.keccak(text=str(deal.id)), 0, False
    )
    mock_w3.eth.contract = lambda address, abi: mock_contract

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        verified_deal = await escrow_service.verify_funding_transaction(db_session, deal, tx_hash, buyer)
        assert verified_deal.on_chain_state == EscrowState.FUNDED
        assert verified_deal.contract_version == "v2"


@pytest.mark.asyncio
async def test_33_historical_deal_state_sync_succeeds_even_if_token_now_unverified_or_expired(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    registry = get_token_registry()
    # Deal was funded in the past with V3 USDC
    deal = EscrowDeal(
        offer_id=uuid.uuid4(),
        buyer_id=buyer.id,
        seller_id=seller.id,
        amount_eth=10.0,
        deadline_ts=int(time.time()) + 86400,
        agreement_hash="0x" + "1" * 64,
        chain_deal_id=1,
        fund_tx_hash="0x" + "f" * 64,
        contract_version="v3",
        contract_address=TEST_V3_CONTRACT,
        token_symbol="USDC",
        token_address=TEST_USDC_CONTRACT,
        token_decimals=6,
        amount_base_units="10000000",
        fee_bps=250,
        treasury_address=TEST_TREASURY,
        on_chain_state=EscrowState.FUNDED,
    )
    db_session.add(deal)
    await db_session.commit()
    await db_session.refresh(deal)

    # Invalidate registry: set token trust to FAILED and expired
    registry._tokens[(CHAIN_ID_ANVIL, "USDC")] = registry.get_token(CHAIN_ID_ANVIL, "USDC").with_verification(
        trust_level=TrustLevel.FAILED,
        on_chain_allowed=False,
        is_funding_permitted=False,
        verification_failure_reason="Token paused on-chain",
    )

    # Mock contract getDeal returning CONFIRMED (1)
    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = CHAIN_ID_ANVIL
    mock_contract = MagicMock()
    mock_contract.functions.getDeal(1).call.return_value = (
        Web3.keccak(text=str(deal.id)),
        BUYER_WALLET,
        SELLER_WALLET,
        TEST_USDC_CONTRACT,
        10000000,
        250,
        TEST_TREASURY,
        deal.deadline_ts,
        1,  # State.CONFIRMED
        False,
        deal.agreement_hash,
    )
    mock_w3.eth.contract = lambda address, abi: mock_contract

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        # sync_deal_state should succeed without throwing VerificationError because settlement is isolated!
        synced_deal = await escrow_service.sync_deal_state(db_session, deal.id, buyer)
        assert synced_deal.on_chain_state == EscrowState.CONFIRMED


@pytest.mark.asyncio
async def test_34_terminal_state_protection_preserved_for_v3_deals(db_session: AsyncSession):
    buyer, seller = await _create_test_users(db_session)
    deal = EscrowDeal(
        offer_id=uuid.uuid4(),
        buyer_id=buyer.id,
        seller_id=seller.id,
        amount_eth=10.0,
        deadline_ts=int(time.time()) + 86400,
        agreement_hash="0x" + "1" * 64,
        chain_deal_id=1,
        fund_tx_hash="0x" + "f" * 64,
        contract_version="v3",
        contract_address=TEST_V3_CONTRACT,
        token_symbol="USDC",
        token_address=TEST_USDC_CONTRACT,
        token_decimals=6,
        amount_base_units="10000000",
        fee_bps=250,
        treasury_address=TEST_TREASURY,
        on_chain_state=EscrowState.RELEASED,  # Terminal state!
    )
    db_session.add(deal)
    await db_session.commit()
    await db_session.refresh(deal)

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = CHAIN_ID_ANVIL
    mock_contract = MagicMock()
    # Contract reports 0 (FUNDED) due to temporary node lag
    mock_contract.functions.getDeal(1).call.return_value = (
        Web3.keccak(text=str(deal.id)),
        BUYER_WALLET,
        SELLER_WALLET,
        TEST_USDC_CONTRACT,
        10000000,
        250,
        TEST_TREASURY,
        deal.deadline_ts,
        0,  # State.FUNDED
        False,
        deal.agreement_hash,
    )
    mock_w3.eth.contract = lambda address, abi: mock_contract

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        synced_deal = await escrow_service.sync_deal_state(db_session, deal.id, buyer)
        # Must protect terminal state
        assert synced_deal.on_chain_state == EscrowState.RELEASED


def test_35_no_floating_point_precision_loss_micro_amounts():
    # 0.000001 USDC = exactly 1 base unit
    micro_amount = Decimal("0.000001")
    bu = to_base_units(micro_amount, decimals=6)
    assert bu == 1
    assert isinstance(bu, int)

    recovered = from_base_units(bu, decimals=6)
    assert recovered == Decimal("0.000001")

    # Reject fractional base units
    with pytest.raises(ValueError, match="Fractional base units are not allowed"):
        to_base_units(Decimal("0.0000001"), decimals=6)
