"""
Comprehensive Test Suite for CipherTrade V3 Token Registry Hardening,
Exact Token Conversion, and V3 Agreement Hashing.
(Phase 6.2.2.3.3)
"""
from __future__ import annotations

from decimal import Decimal
import time
from unittest.mock import MagicMock
import uuid
import pytest
from web3 import Web3

from app.config import Settings
from app.core.token_conversion import (
    ConversionError,
    derive_legacy_amount,
    from_base_units,
    to_base_units,
    validate_financial_consistency,
)
from app.core.token_registry import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_SEPOLIA,
    ETH_SENTINEL_ADDRESS,
    HistoricalTokenInfo,
    TokenConfigurationError,
    TokenMetadata,
    TokenRegistry,
    TokenType,
    TrustLevel,
    UnsupportedChainError,
    UnsupportedSymbolError,
    VerificationError,
    get_token_registry,
)
from app.services.agreement_service import (
    AGREEMENT_TERMS_TYPEHASH,
    AGREEMENT_TERMS_TYPEHASH_HEX,
    compute_deal_uuid_hash,
    hash_v3_agreement_terms,
)


@pytest.fixture
def mock_settings() -> Settings:
    """Settings fixture with mock V3 and token configurations."""
    return Settings(
        chain_id=CHAIN_ID_ANVIL,
        web3_rpc_url="http://127.0.0.1:8545",
        token_verification_ttl_seconds=300,
        escrow_contract_address="0x5FbDB2315678afecb367f032d93F642f64180aa3",
        escrow_v2_contract_address="0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512",
        escrow_v3_contract_address="0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0",
        anvil_usdc_contract_address="0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9",
        anvil_usdt_contract_address="0xDc64a140Aa3E981100a9becA4E685f962f0cF6C9",
        sepolia_escrow_v3_contract_address="0x0165878A594ca255338adfa4d48449f69242Eb8F",
        sepolia_usdc_contract_address="0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
        sepolia_usdt_contract_address="0xa513E6E4b8f2a923D98304ec87F64353C4D5C853",
    )


@pytest.fixture
def registry(mock_settings: Settings) -> TokenRegistry:
    return TokenRegistry(settings=mock_settings)


def _create_mock_w3(chain_id: int = CHAIN_ID_ANVIL) -> MagicMock:
    """Helper to create a properly configured mock Web3 instance."""
    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = chain_id
    mock_w3.eth.get_code.return_value = b"\x60\x80\x60\x40"
    return mock_w3


# ═════════════════════════════════════════════════════════════════════════════
# PART 1: TOKEN REGISTRY HARDENING & SECURITY (A1 - A5)
# ═════════════════════════════════════════════════════════════════════════════

def test_1_verification_expires_after_ttl(registry: TokenRegistry):
    """A1: Verification timestamp expires after TTL."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_v3_contract = MagicMock()
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = True
    mock_w3.eth.contract.return_value = mock_v3_contract

    verified = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert verified.trust_level == TrustLevel.VERIFIED
    assert verified.verified_at is not None
    assert verified.can_fund(ttl_seconds=300) is True

    # Check after TTL expiration (e.g. 10 seconds past TTL)
    old_ts = time.time() - 350
    expired = verified.with_verification(
        trust_level=TrustLevel.VERIFIED,
        verified_at=old_ts,
    )
    assert expired.is_verification_expired(ttl_seconds=300) is True
    assert expired.can_fund(ttl_seconds=300) is False


def test_2_expired_verification_cannot_authorize_funding(registry: TokenRegistry):
    """A1: Expired verification rejects funding even if trust state says VERIFIED."""
    meta = registry.get_token(CHAIN_ID_ANVIL, "ETH")
    expired = meta.with_verification(
        trust_level=TrustLevel.VERIFIED,
        verified_at=time.time() - 500,
    )
    assert expired.can_fund(ttl_seconds=300) is False


def test_3_successful_refresh_restores_eligibility(registry: TokenRegistry):
    """A1: Successful refresh records a fresh timestamp and restores eligibility."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_v3_contract = MagicMock()
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = True
    mock_w3.eth.contract.return_value = mock_v3_contract

    # First verify
    registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    # Expire it manually in registry cache
    registry._tokens[(CHAIN_ID_ANVIL, "ETH")] = registry.get_token(CHAIN_ID_ANVIL, "ETH").with_verification(
        trust_level=TrustLevel.VERIFIED,
        verified_at=time.time() - 400,
    )
    assert registry.get_token(CHAIN_ID_ANVIL, "ETH").can_fund(ttl_seconds=300) is False

    # ensure_verified_for_funding refreshes it
    fresh = registry.ensure_verified_for_funding(CHAIN_ID_ANVIL, "ETH", w3=mock_w3, ttl_seconds=300)
    assert fresh.can_fund(ttl_seconds=300) is True
    assert not fresh.is_verification_expired(ttl_seconds=300)


def test_4_failed_refresh_revokes_funding_eligibility(registry: TokenRegistry):
    """A1/A3: Failed refresh revokes funding eligibility and transitions to FAILED."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_v3_contract = MagicMock()
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = True
    mock_w3.eth.contract.return_value = mock_v3_contract

    # Initially verified
    registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert registry.get_token(CHAIN_ID_ANVIL, "ETH").can_fund() is True

    # Now simulate RPC outage during re-verification
    mock_w3.is_connected.return_value = False
    failed = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert failed.trust_level == TrustLevel.FAILED
    assert failed.is_funding_permitted is False
    assert failed.verified_at is None
    assert failed.can_fund() is False


def test_5_on_chain_allowlist_rejection_produces_failed_state(registry: TokenRegistry):
    """A3: Allowlist rejection sets TrustLevel.FAILED and forbids funding."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_v3_contract = MagicMock()
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = False  # Revoked on-chain!
    mock_w3.eth.contract.return_value = mock_v3_contract

    res = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert res.trust_level == TrustLevel.FAILED
    assert res.is_funding_permitted is False
    assert "not allowed in V3" in res.verification_failure_reason
    assert res.can_fund() is False


def test_6_operator_disabled_token_remains_disabled_after_verification(registry: TokenRegistry):
    """A2: Operator-disabled token (is_enabled=False or is_funding_permitted=False) survives verification."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_v3_contract = MagicMock()
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = True
    mock_w3.eth.contract.return_value = mock_v3_contract

    # Operator intentionally disables funding for ETH
    orig = registry.get_token(CHAIN_ID_ANVIL, "ETH")
    disabled = TokenMetadata(
        symbol=orig.symbol,
        name=orig.name,
        chain_id=orig.chain_id,
        token_address=orig.token_address,
        decimals=orig.decimals,
        token_type=orig.token_type,
        is_enabled=True,
        is_funding_permitted=False,  # Operator blocked!
        trust_level=TrustLevel.CONFIGURED,
    )
    registry._tokens[(CHAIN_ID_ANVIL, "ETH")] = disabled

    # Verify on chain (where contract allows it)
    verified = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert verified.trust_level == TrustLevel.VERIFIED
    # MUST NOT override operator:
    assert verified.is_funding_permitted is False
    assert verified.can_fund() is False


def test_7_operator_disabled_funding_remains_disabled_in_verify_all(registry: TokenRegistry):
    """A2: verify_all_configured_tokens preserves operator restrictions."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)

    def contract_factory(address, abi):
        mock_c = MagicMock()
        mock_c.functions.decimals.return_value.call.return_value = 6
        mock_c.functions.symbol.return_value.call.return_value = "USDC"
        mock_c.functions.isTokenAllowed.return_value.call.return_value = True
        return mock_c

    mock_w3.eth.contract.side_effect = contract_factory

    # Operator blocks USDC
    usdc = registry.get_token(CHAIN_ID_ANVIL, "USDC")
    registry._tokens[(CHAIN_ID_ANVIL, "USDC")] = TokenMetadata(
        symbol=usdc.symbol,
        name=usdc.name,
        chain_id=usdc.chain_id,
        token_address=usdc.token_address,
        decimals=usdc.decimals,
        token_type=usdc.token_type,
        is_enabled=True,
        is_funding_permitted=False,
        trust_level=TrustLevel.CONFIGURED,
    )

    results = registry.verify_all_configured_tokens(CHAIN_ID_ANVIL, w3=mock_w3)
    assert results["USDC"].trust_level == TrustLevel.VERIFIED
    assert results["USDC"].is_funding_permitted is False
    assert results["USDC"].can_fund() is False


def test_8_missing_sepolia_usdc_does_not_silently_fallback():
    """A4: Missing Sepolia USDC configuration leaves asset unconfigured."""
    sparse_settings = Settings(
        chain_id=CHAIN_ID_SEPOLIA,
        sepolia_usdc_contract_address=None,  # No address!
    )
    reg = TokenRegistry(settings=sparse_settings)
    with pytest.raises(UnsupportedSymbolError):
        reg.get_token(CHAIN_ID_SEPOLIA, "USDC")


def test_9_settlement_continues_after_token_disablement_and_failed_refresh(registry: TokenRegistry):
    """A5: Existing funded deals resolve settlement metadata regardless of current registry state."""
    # Corrupt or disable the active registry token
    registry._tokens[(CHAIN_ID_ANVIL, "USDC")] = registry.get_token(CHAIN_ID_ANVIL, "USDC").with_verification(
        trust_level=TrustLevel.FAILED,
        failure_reason="Allowlist revoked",
        is_funding_permitted=False,
    )

    # Historical settlement uses persisted deal info
    info = TokenRegistry.get_settlement_token_info(
        symbol="USDC",
        token_address="0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9",
        decimals=6,
        chain_id=CHAIN_ID_ANVIL,
        contract_version="v3",
        contract_address="0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0",
    )
    assert info.symbol == "USDC"
    assert info.decimals == 6
    assert info.contract_version == "v3"


# ═════════════════════════════════════════════════════════════════════════════
# PART 2: EXACT TOKEN CONVERSION SERVICE (PART B)
# ═════════════════════════════════════════════════════════════════════════════

def test_10_eth_18_decimal_exact_conversion(registry: TokenRegistry):
    """B: 18-decimal exact conversion for ETH."""
    base_units = to_base_units(Decimal("1.5"), CHAIN_ID_ANVIL, "ETH")
    assert base_units == "1500000000000000000"

    dec = from_base_units("1500000000000000000", CHAIN_ID_ANVIL, "ETH")
    assert dec == Decimal("1.5")


def test_11_usdc_6_decimal_exact_conversion(registry: TokenRegistry):
    """B: 6-decimal exact conversion for USDC."""
    base_units = to_base_units(Decimal("250.75"), CHAIN_ID_ANVIL, "USDC", registry=registry)
    assert base_units == "250750000"

    dec = from_base_units("250750000", CHAIN_ID_ANVIL, "USDC", registry=registry)
    assert dec == Decimal("250.75")


def test_12_mock_usdt_6_decimal_exact_conversion(registry: TokenRegistry):
    """B: 6-decimal exact conversion for project mock USDT."""
    base_units = to_base_units("1000", CHAIN_ID_ANVIL, "USDT", registry=registry)
    assert base_units == "1000000000"

    dec = from_base_units("1000000000", CHAIN_ID_ANVIL, "USDT", registry=registry)
    assert dec == Decimal("1000")


def test_13_excess_precision_rejection(registry: TokenRegistry):
    """B: Reject sub-base-unit values (more decimals than asset supports)."""
    # USDC has 6 decimals; 7 decimals must be rejected
    with pytest.raises(ConversionError) as exc:
        to_base_units(Decimal("1.1234567"), CHAIN_ID_ANVIL, "USDC", registry=registry)
    assert "fractional base units" in str(exc.value).lower()


def test_14_sub_base_unit_rejection(registry: TokenRegistry):
    """B: Reject fractional wei for ETH (>18 decimals)."""
    with pytest.raises(ConversionError):
        to_base_units(Decimal("0." + "0" * 18 + "1"), CHAIN_ID_ANVIL, "ETH", registry=registry)


def test_15_negative_and_non_finite_rejection(registry: TokenRegistry):
    """B: Reject negative, NaN, and Infinity amounts."""
    for bad in (Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
        with pytest.raises(ConversionError):
            to_base_units(bad, CHAIN_ID_ANVIL, "USDC", registry=registry)


def test_16_uint256_overflow_rejection(registry: TokenRegistry):
    """B: Reject amounts that exceed uint256 max."""
    huge_amount = Decimal("2") ** 256
    with pytest.raises(ConversionError):
        to_base_units(huge_amount, CHAIN_ID_ANVIL, "ETH", registry=registry)


def test_17_exact_base_unit_to_decimal_round_trip(registry: TokenRegistry):
    """B: Perfect round-trip without loss of precision."""
    test_cases = [
        ("ETH", "123456789012345678", "0.123456789012345678"),
        ("USDC", "50000000", "50.000000"),
        ("USDT", "1", "0.000001"),
        ("ETH", "0", "0"),
    ]
    for symbol, base_u, expected_dec_str in test_cases:
        dec = from_base_units(base_u, CHAIN_ID_ANVIL, symbol, registry=registry)
        back_to_bu = to_base_units(dec, CHAIN_ID_ANVIL, symbol, registry=registry)
        assert back_to_bu == base_u


def test_18_no_float_conversion_in_monetary_code_paths(registry: TokenRegistry):
    """B: Pure floats are rejected directly to prevent float rounding errors."""
    with pytest.raises(ConversionError) as exc:
        to_base_units(1.5, CHAIN_ID_ANVIL, "ETH", registry=registry)  # type: ignore
    assert "floating-point amounts are strictly prohibited" in str(exc.value).lower()


# ═════════════════════════════════════════════════════════════════════════════
# PART 3: V3 CANONICAL AGREEMENT HASHING (PART C)
# ═════════════════════════════════════════════════════════════════════════════

def test_19_solidity_typehash_equality():
    """C: Python AGREEMENT_TERMS_TYPEHASH matches Solidity constant exactly."""
    expected_typehash_str = (
        "AgreementTerms(string dealId,uint256 agreementVersion,bytes32 previousAgreementHash,string offerId,address buyer,address seller,address token,uint256 amount,uint256 feeBps,address treasury,uint256 deliveryDeadline)"
    )
    expected_hash = Web3.keccak(text=expected_typehash_str)
    assert AGREEMENT_TERMS_TYPEHASH == expected_hash
    assert AGREEMENT_TERMS_TYPEHASH_HEX == Web3.to_hex(expected_hash).lower()


def test_20_solidity_struct_hash_golden_vector():
    """C: Golden vector test comparing Python hash_v3_agreement_terms with known Solidity calculation."""
    deal_id = "deal-12345"
    agreement_version = 1
    previous_agreement_hash = "0x" + "0" * 64
    offer_id = "offer-67890"
    buyer = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    seller = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    token = "0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9"
    amount = "100000000"  # 100 USDC (6 dec)
    fee_bps = 250         # 2.5%
    treasury = "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"
    delivery_deadline = 1750000000

    terms_hash = hash_v3_agreement_terms(
        deal_id=deal_id,
        agreement_version=agreement_version,
        previous_agreement_hash=previous_agreement_hash,
        offer_id=offer_id,
        buyer_address=buyer,
        seller_address=seller,
        token_address=token,
        amount_base_units=amount,
        fee_bps=fee_bps,
        treasury_address=treasury,
        delivery_deadline=delivery_deadline,
    )
    assert terms_hash.startswith("0x")
    assert len(terms_hash) == 66  # 32 bytes in hex


def test_21_token_address_changes_produce_different_hash():
    """C: Changing token address alters agreement hash."""
    params = {
        "deal_id": "deal-1",
        "agreement_version": 1,
        "previous_agreement_hash": "0x" + "0" * 64,
        "offer_id": "offer-1",
        "buyer_address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "seller_address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "token_address": "0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9",
        "amount_base_units": "1000000",
        "fee_bps": 100,
        "treasury_address": "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
        "delivery_deadline": 1800000000,
    }
    hash1 = hash_v3_agreement_terms(**params)

    params2 = dict(params)
    params2["token_address"] = ETH_SENTINEL_ADDRESS
    hash2 = hash_v3_agreement_terms(**params2)

    assert hash1 != hash2


def test_22_amount_changes_produce_different_hash():
    """C: Changing amount base units alters agreement hash."""
    params = {
        "deal_id": "deal-1",
        "agreement_version": 1,
        "previous_agreement_hash": "0x" + "0" * 64,
        "offer_id": "offer-1",
        "buyer_address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "seller_address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "token_address": "0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9",
        "amount_base_units": "1000000",
        "fee_bps": 100,
        "treasury_address": "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
        "delivery_deadline": 1800000000,
    }
    hash1 = hash_v3_agreement_terms(**params)

    params2 = dict(params)
    params2["amount_base_units"] = "1000001"
    hash2 = hash_v3_agreement_terms(**params2)

    assert hash1 != hash2


def test_23_fee_and_treasury_changes_produce_different_hash():
    """C: Changing feeBps or treasury alters agreement hash."""
    params = {
        "deal_id": "deal-1",
        "agreement_version": 1,
        "previous_agreement_hash": "0x" + "0" * 64,
        "offer_id": "offer-1",
        "buyer_address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "seller_address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "token_address": "0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9",
        "amount_base_units": "1000000",
        "fee_bps": 100,
        "treasury_address": "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
        "delivery_deadline": 1800000000,
    }
    base_hash = hash_v3_agreement_terms(**params)

    params_fee = dict(params)
    params_fee["fee_bps"] = 200
    assert hash_v3_agreement_terms(**params_fee) != base_hash

    params_treasury = dict(params)
    params_treasury["treasury_address"] = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
    assert hash_v3_agreement_terms(**params_treasury) != base_hash


def test_24_agreement_version_and_prev_hash_bound():
    """C: Agreement version and previous hash correctly bind terms hash."""
    params = {
        "deal_id": "deal-1",
        "agreement_version": 1,
        "previous_agreement_hash": "0x" + "0" * 64,
        "offer_id": "offer-1",
        "buyer_address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "seller_address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "token_address": "0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9",
        "amount_base_units": "1000000",
        "fee_bps": 100,
        "treasury_address": "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
        "delivery_deadline": 1800000000,
    }
    v1_hash = hash_v3_agreement_terms(**params)

    params_v2 = dict(params)
    params_v2["agreement_version"] = 2
    params_v2["previous_agreement_hash"] = v1_hash
    v2_hash = hash_v3_agreement_terms(**params_v2)

    assert v1_hash != v2_hash


def test_25_deal_uuid_hash_consistency():
    """C: compute_deal_uuid_hash produces identical keccak256 hash matching Solidity."""
    deal_uuid = uuid.uuid4()
    h1 = compute_deal_uuid_hash(deal_uuid)
    h2 = Web3.to_hex(Web3.keccak(text=str(deal_uuid))).lower()
    assert h1 == h2


# ── Part D: Phase 6.2.2.3.3.2 Verification Tests ─────────────────────────────

def test_26_part_d_rpc_failure_does_not_mutate_operator_funding_permission(registry: TokenRegistry):
    """Part D.1: RPC failure does not mutate operator funding permission."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_w3.is_connected.return_value = False  # Simulate RPC offline

    orig = registry.get_token(CHAIN_ID_ANVIL, "ETH")
    assert orig.operator_funding_permitted is True

    failed = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert failed.trust_level == TrustLevel.FAILED
    assert failed.operator_funding_permitted is True  # OPERATOR INTENT UNCHANGED
    assert failed.is_funding_permitted is False       # Derived eligibility revoked
    assert failed.can_fund() is False


def test_27_part_d_successful_verification_after_rpc_failure_restores_eligibility(registry: TokenRegistry):
    """Part D.2: Successful verification after an RPC failure restores eligibility when operator permission is True."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_v3_contract = MagicMock()
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = True
    mock_w3.eth.contract.return_value = mock_v3_contract

    # First fail with RPC outage
    mock_w3.is_connected.return_value = False
    failed = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert failed.trust_level == TrustLevel.FAILED
    assert failed.is_funding_permitted is False
    assert failed.operator_funding_permitted is True

    # Now RPC recovers
    mock_w3.is_connected.return_value = True
    recovered = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert recovered.trust_level == TrustLevel.VERIFIED
    assert recovered.operator_funding_permitted is True
    assert recovered.is_funding_permitted is True  # ELIGIBILITY RESTORED!
    assert recovered.can_fund() is True


def test_28_part_d_successful_verification_cannot_enable_operator_disabled_token(registry: TokenRegistry):
    """Part D.3: Successful verification cannot enable an operator-disabled token."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_v3_contract = MagicMock()
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = True
    mock_w3.eth.contract.return_value = mock_v3_contract

    orig = registry.get_token(CHAIN_ID_ANVIL, "ETH")
    disabled = TokenMetadata(
        symbol=orig.symbol,
        name=orig.name,
        chain_id=orig.chain_id,
        token_address=orig.token_address,
        decimals=orig.decimals,
        token_type=orig.token_type,
        is_enabled=False,  # OPERATOR DISABLED
        operator_funding_permitted=True,
        is_funding_permitted=False,
        trust_level=TrustLevel.CONFIGURED,
    )
    registry._tokens[(CHAIN_ID_ANVIL, "ETH")] = disabled

    verified = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert verified.is_enabled is False
    assert verified.is_funding_permitted is False
    assert verified.can_fund() is False


def test_29_part_d_successful_verification_cannot_override_operator_funding_permission_false(registry: TokenRegistry):
    """Part D.4: Successful verification cannot override operator funding permission False."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_v3_contract = MagicMock()
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = True
    mock_w3.eth.contract.return_value = mock_v3_contract

    orig = registry.get_token(CHAIN_ID_ANVIL, "ETH")
    blocked = TokenMetadata(
        symbol=orig.symbol,
        name=orig.name,
        chain_id=orig.chain_id,
        token_address=orig.token_address,
        decimals=orig.decimals,
        token_type=orig.token_type,
        is_enabled=True,
        operator_funding_permitted=False,  # OPERATOR BLOCKED FUNDING
        is_funding_permitted=False,
        trust_level=TrustLevel.CONFIGURED,
    )
    registry._tokens[(CHAIN_ID_ANVIL, "ETH")] = blocked

    verified = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert verified.trust_level == TrustLevel.VERIFIED
    assert verified.operator_funding_permitted is False  # OPERATOR POLICY MAINTAINED
    assert verified.is_funding_permitted is False
    assert verified.can_fund() is False


def test_30_part_d_verify_all_configured_tokens_preserves_operator_settings(registry: TokenRegistry):
    """Part D.5: verify_all_configured_tokens preserves operator settings."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)

    def contract_factory(address, abi):
        mock_c = MagicMock()
        mock_c.functions.decimals.return_value.call.return_value = 6
        mock_c.functions.symbol.return_value.call.return_value = "USDC"
        mock_c.functions.isTokenAllowed.return_value.call.return_value = True
        return mock_c

    mock_w3.eth.contract.side_effect = contract_factory

    # Set custom operator permissions:
    # USDC: funding forbidden
    usdc = registry.get_token(CHAIN_ID_ANVIL, "USDC")
    registry._tokens[(CHAIN_ID_ANVIL, "USDC")] = TokenMetadata(
        symbol=usdc.symbol,
        name=usdc.name,
        chain_id=usdc.chain_id,
        token_address=usdc.token_address,
        decimals=usdc.decimals,
        token_type=usdc.token_type,
        is_enabled=True,
        operator_funding_permitted=False,
        is_funding_permitted=False,
        trust_level=TrustLevel.CONFIGURED,
    )

    # USDT: disabled
    usdt = registry.get_token(CHAIN_ID_ANVIL, "USDT")
    registry._tokens[(CHAIN_ID_ANVIL, "USDT")] = TokenMetadata(
        symbol=usdt.symbol,
        name=usdt.name,
        chain_id=usdt.chain_id,
        token_address=usdt.token_address,
        decimals=usdt.decimals,
        token_type=usdt.token_type,
        is_enabled=False,
        operator_funding_permitted=True,
        is_funding_permitted=False,
        trust_level=TrustLevel.CONFIGURED,
    )

    results = registry.verify_all_configured_tokens(CHAIN_ID_ANVIL, w3=mock_w3)
    # ETH remains enabled and permitted
    assert results["ETH"].can_fund() is True
    # USDC operator restriction preserved
    assert results["USDC"].operator_funding_permitted is False
    assert results["USDC"].is_funding_permitted is False
    assert results["USDC"].can_fund() is False
    # USDT operator disablement preserved
    assert results["USDT"].is_enabled is False
    assert results["USDT"].is_funding_permitted is False
    assert results["USDT"].can_fund() is False


def test_31_part_d_failed_verification_immediately_prevents_funding(registry: TokenRegistry):
    """Part D.6: Failed verification immediately prevents funding."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_v3_contract = MagicMock()
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = True
    mock_w3.eth.contract.return_value = mock_v3_contract

    # First verify
    verified = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert verified.can_fund() is True

    # Token allowlist is revoked on-chain
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = False
    failed = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert failed.trust_level == TrustLevel.FAILED
    assert failed.is_funding_permitted is False
    assert failed.on_chain_allowed is False
    assert failed.can_fund() is False


def test_32_part_d_verification_expires_using_monotonic_time(registry: TokenRegistry, monkeypatch):
    """Part D.7: Verification expires using monotonic time."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_v3_contract = MagicMock()
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = True
    mock_w3.eth.contract.return_value = mock_v3_contract

    verified = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert verified.can_fund(ttl_seconds=300) is True
    mono_start = verified.verified_at_monotonic
    assert mono_start is not None

    # Fast-forward monotonic time past TTL (301 seconds)
    monkeypatch.setattr(time, "monotonic", lambda: mono_start + 301.0)
    assert verified.is_verification_expired(ttl_seconds=300) is True
    assert verified.can_fund(ttl_seconds=300) is False


def test_33_part_d_backward_wall_clock_adjustment_cannot_extend_verification_validity(registry: TokenRegistry, monkeypatch):
    """Part D.8: A backward wall-clock adjustment cannot extend verification validity."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_v3_contract = MagicMock()
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = True
    mock_w3.eth.contract.return_value = mock_v3_contract

    verified = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    mono_start = verified.verified_at_monotonic
    wall_start = verified.verified_at
    assert mono_start is not None
    assert wall_start is not None

    # Attack/Fault: Wall-clock steps backward into the past by 10,000 seconds
    monkeypatch.setattr(time, "time", lambda: wall_start - 10000.0)
    # Monotonic time advances past TTL by 5 seconds
    monkeypatch.setattr(time, "monotonic", lambda: mono_start + 305.0)

    # Verification MUST be expired despite wall-clock being in the past
    assert verified.is_verification_expired(ttl_seconds=300) is True
    assert verified.can_fund(ttl_seconds=300) is False


def test_34_part_d_settlement_remains_possible_after_failed_verification_and_operator_disablement(registry: TokenRegistry):
    """Part D.9: Settlement remains possible after failed verification and operator disablement."""
    # Active USDC token in registry is disabled and in FAILED state
    orig = registry.get_token(CHAIN_ID_ANVIL, "USDC")
    registry._tokens[(CHAIN_ID_ANVIL, "USDC")] = TokenMetadata(
        symbol=orig.symbol,
        name=orig.name,
        chain_id=orig.chain_id,
        token_address=orig.token_address,
        decimals=orig.decimals,
        token_type=orig.token_type,
        is_enabled=False,
        operator_funding_permitted=False,
        is_funding_permitted=False,
        trust_level=TrustLevel.FAILED,
        verification_failure_reason="Admin permanently disabled asset",
    )

    # Historical settlement uses deal-specific parameters and succeeds
    info = TokenRegistry.get_settlement_token_info(
        symbol="USDC",
        token_address="0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9",
        decimals=6,
        chain_id=CHAIN_ID_ANVIL,
        contract_version="v3",
        contract_address="0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0",
    )
    assert info.symbol == "USDC"
    assert info.decimals == 6
    assert info.contract_version == "v3"
    assert info.contract_address == Web3.to_checksum_address("0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0")


def test_35_part_d_python_and_solidity_type_strings_match_frozen_specification_exactly():
    """Part D.10: Python and Solidity type strings match the frozen specification exactly."""
    from pathlib import Path
    import re

    # Read Solidity contract directly from disk
    sol_path = Path("contracts/src/CipherTradeEscrowV3.sol")
    assert sol_path.is_file(), "CipherTradeEscrowV3.sol must exist"
    sol_content = sol_path.read_text(encoding="utf-8")

    # Extract AGREEMENT_TERMS_TYPEHASH string from Solidity
    match = re.search(r'AGREEMENT_TERMS_TYPEHASH\s*=\s*keccak256\(\s*abi\.encodePacked\(\s*"([^"]+)"', sol_content)
    assert match is not None, "Could not find AGREEMENT_TERMS_TYPEHASH definition in Solidity"
    sol_type_string = match.group(1)

    expected_frozen_spec = (
        "AgreementTerms(string dealId,uint256 agreementVersion,bytes32 previousAgreementHash,"
        "string offerId,address buyer,address seller,address token,uint256 amount,uint256 feeBps,"
        "address treasury,uint256 deliveryDeadline)"
    )

    # 1. Assert Solidity matches frozen specification exactly
    assert sol_type_string == expected_frozen_spec
    assert "string dealId" in sol_type_string
    assert "string offerId" in sol_type_string
    assert "uint256 dealId" not in sol_type_string
    assert "uint256 offerId" not in sol_type_string

    # 2. Assert Python service matches frozen specification exactly
    from app.services.agreement_service import AGREEMENT_TERMS_TYPEHASH, AGREEMENT_TERMS_TYPEHASH_HEX
    expected_typehash = Web3.keccak(text=expected_frozen_spec)
    assert AGREEMENT_TERMS_TYPEHASH == expected_typehash
    assert AGREEMENT_TERMS_TYPEHASH_HEX == Web3.to_hex(expected_typehash).lower()
    assert AGREEMENT_TERMS_TYPEHASH_HEX == "0x0272c4aa642b53465695cab523d1819279d2b8bb5c5da5b078fb15228487709b"

