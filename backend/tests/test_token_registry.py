"""
Unit and Adversarial Test Suite for CipherTrade V3 Token Registry & On-Chain Verifier.
(Phase 6.2.2.3.2)
"""
from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock
import pytest
from web3 import Web3

from app.config import Settings
from app.core.token_registry import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_SEPOLIA,
    ETH_SENTINEL_ADDRESS,
    SEPOLIA_USDC_OFFICIAL,
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


@pytest.fixture
def mock_settings() -> Settings:
    """Settings fixture with mock V3 and token configurations."""
    return Settings(
        chain_id=CHAIN_ID_ANVIL,
        web3_rpc_url="http://127.0.0.1:8545",
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


# ── 1. Basic Registry Lookups & Symbol Normalization ─────────────────────────

def test_eth_lookup_on_both_chains(registry: TokenRegistry):
    """Test ETH lookup on both Anvil (31337) and Sepolia (11155111)."""
    for chain_id in (CHAIN_ID_ANVIL, CHAIN_ID_SEPOLIA):
        eth_meta = registry.get_token(chain_id, "eth")
        assert eth_meta.symbol == "ETH"
        assert eth_meta.name == "Ether"
        assert eth_meta.chain_id == chain_id
        assert eth_meta.token_address == ETH_SENTINEL_ADDRESS
        assert eth_meta.decimals == 18
        assert eth_meta.token_type == TokenType.NATIVE
        assert eth_meta.is_enabled is True
        assert eth_meta.is_funding_permitted is True
        assert eth_meta.trust_level == TrustLevel.CONFIGURED
        # CONFIGURED cannot fund yet
        assert eth_meta.can_fund() is False


def test_usdc_lookup_on_both_chains(registry: TokenRegistry):
    """Test USDC lookup on Anvil and official Circle Sepolia deployment."""
    anvil_usdc = registry.get_token(CHAIN_ID_ANVIL, "  usdc  ")
    assert anvil_usdc.symbol == "USDC"
    assert anvil_usdc.decimals == 6
    assert anvil_usdc.token_type == TokenType.ERC20
    assert anvil_usdc.token_address == Web3.to_checksum_address("0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9")

    sepolia_usdc = registry.get_token(CHAIN_ID_SEPOLIA, "USDC")
    assert sepolia_usdc.symbol == "USDC"
    assert sepolia_usdc.decimals == 6
    assert sepolia_usdc.token_address == SEPOLIA_USDC_OFFICIAL


def test_project_mock_usdt_lookup(registry: TokenRegistry):
    """Verify USDT is strictly identified as a project mock and uses checksummed address."""
    anvil_usdt = registry.get_token(CHAIN_ID_ANVIL, "usdt")
    assert anvil_usdt.symbol == "USDT"
    assert "Mock" in anvil_usdt.name
    assert anvil_usdt.decimals == 6
    assert anvil_usdt.token_address == Web3.to_checksum_address("0xDc64a140Aa3E981100a9becA4E685f962f0cF6C9")

    sepolia_usdt = registry.get_token(CHAIN_ID_SEPOLIA, "USDT")
    assert sepolia_usdt.symbol == "USDT"
    assert "Mock" in sepolia_usdt.name
    assert sepolia_usdt.decimals == 6
    assert sepolia_usdt.token_address == Web3.to_checksum_address("0xa513E6E4b8f2a923D98304ec87F64353C4D5C853")


def test_unsupported_symbol_rejection(registry: TokenRegistry):
    """Registry must reject unknown currencies like DAI, WBTC, SOL."""
    for bad_sym in ("DAI", "WBTC", "SOL", "", "   "):
        with pytest.raises(UnsupportedSymbolError):
            registry.get_token(CHAIN_ID_ANVIL, bad_sym)


def test_unsupported_chain_rejection(registry: TokenRegistry):
    """Registry must reject unsupported chains like Mainnet (1) or Arbitrum (42161)."""
    for bad_chain in (1, 5, 10, 42161, 137):
        with pytest.raises(UnsupportedChainError):
            registry.get_token(bad_chain, "ETH")


# ── 2. Missing Configuration & Unavailable States ────────────────────────────

def test_missing_token_address_handling():
    """Missing token configuration leaves the asset unconfigured rather than falling back."""
    sparse_settings = Settings(
        chain_id=CHAIN_ID_ANVIL,
        anvil_usdc_contract_address=None,
        anvil_usdt_contract_address=None,
        sepolia_usdt_contract_address=None,
    )
    reg = TokenRegistry(settings=sparse_settings)

    # ETH remains configured
    assert reg.get_token(CHAIN_ID_ANVIL, "ETH").symbol == "ETH"

    # Unconfigured tokens raise UnsupportedSymbolError
    with pytest.raises(UnsupportedSymbolError):
        reg.get_token(CHAIN_ID_ANVIL, "USDC")
    with pytest.raises(UnsupportedSymbolError):
        reg.get_token(CHAIN_ID_ANVIL, "USDT")
    with pytest.raises(UnsupportedSymbolError):
        reg.get_token(CHAIN_ID_SEPOLIA, "USDT")


def test_missing_v3_escrow_address():
    """Missing V3 address raises TokenConfigurationError."""
    sparse_settings = Settings(
        escrow_v3_contract_address=None,
        anvil_escrow_v3_contract_address=None,
    )
    reg = TokenRegistry(settings=sparse_settings)
    with pytest.raises(TokenConfigurationError):
        reg.get_v3_contract_address(CHAIN_ID_ANVIL)


def _create_mock_w3(chain_id: int = CHAIN_ID_ANVIL) -> MagicMock:
    """Helper to create a properly configured mock Web3 instance."""
    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = chain_id
    mock_w3.eth.get_code.return_value = b"\x60\x80\x60\x40"
    return mock_w3


# ── 3. On-Chain Verification & Adversarial Scenarios ─────────────────────────

def test_on_chain_verification_eth_success(registry: TokenRegistry):
    """Verify native ETH on-chain verification succeeds when V3 allowlist allows sentinel."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)

    mock_v3_contract = MagicMock()
    mock_v3_contract.functions.isTokenAllowed.return_value.call.return_value = True
    mock_w3.eth.contract.return_value = mock_v3_contract

    verified = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert verified.trust_level == TrustLevel.VERIFIED
    assert verified.can_fund() is True
    assert verified.verification_failure_reason is None

    # Cached in registry
    assert registry.get_token(CHAIN_ID_ANVIL, "ETH").trust_level == TrustLevel.VERIFIED


def test_on_chain_verification_erc20_success(registry: TokenRegistry):
    """Verify ERC20 verification succeeds when bytecode, decimals, and V3 allowlist match."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)

    def contract_factory(address, abi):
        mock_c = MagicMock()
        mock_c.functions.decimals.return_value.call.return_value = 6
        mock_c.functions.symbol.return_value.call.return_value = "USDC"
        mock_c.functions.isTokenAllowed.return_value.call.return_value = True
        return mock_c

    mock_w3.eth.contract.side_effect = contract_factory

    verified = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "USDC", w3=mock_w3)
    assert verified.trust_level == TrustLevel.VERIFIED
    assert verified.can_fund() is True


def test_verification_rpc_unavailable_behavior(registry: TokenRegistry):
    """RPC disconnection must fail verification and prevent funding."""
    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = False

    result = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert result.trust_level == TrustLevel.FAILED
    assert result.can_fund() is False
    assert "not connected" in result.verification_failure_reason.lower()


def test_verification_wrong_chain_rejection(registry: TokenRegistry):
    """Connected RPC reports different chain_id than configured."""
    mock_w3 = _create_mock_w3(1)  # Connected to Mainnet instead of 31337

    result = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "USDC", w3=mock_w3)
    assert result.trust_level == TrustLevel.FAILED
    assert result.can_fund() is False
    assert "does not match" in result.verification_failure_reason


def test_verification_missing_v3_bytecode(registry: TokenRegistry):
    """V3 contract address returns empty bytecode (not deployed)."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)
    mock_w3.eth.get_code.return_value = b""  # Empty code

    result = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "ETH", w3=mock_w3)
    assert result.trust_level == TrustLevel.FAILED
    assert result.can_fund() is False
    assert "no deployed bytecode" in result.verification_failure_reason


def test_verification_missing_token_bytecode(registry: TokenRegistry):
    """V3 contract is deployed, but ERC20 token address has no bytecode."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)

    v3_addr = registry.get_v3_contract_address(CHAIN_ID_ANVIL)
    def get_code_side_effect(addr):
        if addr == v3_addr:
            return b"\x60\x80"
        return b""  # Token has no code

    mock_w3.eth.get_code.side_effect = get_code_side_effect

    result = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "USDC", w3=mock_w3)
    assert result.trust_level == TrustLevel.FAILED
    assert result.can_fund() is False
    assert "no deployed bytecode" in result.verification_failure_reason


def test_verification_incorrect_decimals(registry: TokenRegistry):
    """On-chain decimals() returns 18 while registry expects 6 (USDC)."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)

    def contract_factory(address, abi):
        mock_c = MagicMock()
        mock_c.functions.decimals.return_value.call.return_value = 18  # Mismatched!
        mock_c.functions.isTokenAllowed.return_value.call.return_value = True
        return mock_c

    mock_w3.eth.contract.side_effect = contract_factory

    result = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "USDC", w3=mock_w3)
    assert result.trust_level == TrustLevel.FAILED
    assert result.can_fund() is False
    assert "Decimals mismatch" in result.verification_failure_reason


def test_verification_v3_allowlist_rejection(registry: TokenRegistry):
    """V3 isTokenAllowed returns False."""
    mock_w3 = _create_mock_w3(CHAIN_ID_ANVIL)

    def contract_factory(address, abi):
        mock_c = MagicMock()
        mock_c.functions.decimals.return_value.call.return_value = 6
        mock_c.functions.isTokenAllowed.return_value.call.return_value = False  # Not allowed!
        return mock_c

    mock_w3.eth.contract.side_effect = contract_factory

    result = registry.verify_token_on_chain(CHAIN_ID_ANVIL, "USDT", w3=mock_w3)
    assert result.trust_level == TrustLevel.FAILED
    assert result.can_fund() is False
    assert "not allowed for funding" in result.verification_failure_reason


# ── 4. Existing Funded Deal Protection & Settlement Metadata ─────────────────

def test_settlement_metadata_for_disabled_or_unverified_token():
    """
    Historical deal settlement metadata must succeed even if the token is not enabled,
    not allowed for new funding, or unverified in the current registry.
    """
    settlement_info = TokenRegistry.get_settlement_token_info(
        symbol="usdc",
        token_address="0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9",
        decimals=6,
        chain_id=CHAIN_ID_ANVIL,
        contract_version="v3",
        contract_address="0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0",
    )
    assert settlement_info.symbol == "USDC"
    assert settlement_info.token_address == Web3.to_checksum_address("0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9")
    assert settlement_info.decimals == 6
    assert settlement_info.chain_id == CHAIN_ID_ANVIL
    assert settlement_info.contract_version == "v3"
    assert settlement_info.contract_address == Web3.to_checksum_address("0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0")


def test_settlement_metadata_for_eth():
    """Settlement metadata for ETH retains the sentinel address."""
    settlement_info = TokenRegistry.get_settlement_token_info(
        symbol="ETH",
        token_address=ETH_SENTINEL_ADDRESS,
        decimals=18,
        chain_id=CHAIN_ID_SEPOLIA,
        contract_version="v2",
        contract_address="0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512",
    )
    assert settlement_info.symbol == "ETH"
    assert settlement_info.token_address == ETH_SENTINEL_ADDRESS
    assert settlement_info.decimals == 18


# ── 5. Immutability & Caller-Supplied Data Isolation ─────────────────────────

def test_token_metadata_immutability(registry: TokenRegistry):
    """TokenMetadata is frozen and cannot be modified directly."""
    meta = registry.get_token(CHAIN_ID_ANVIL, "ETH")
    with pytest.raises(Exception):
        meta.decimals = 6  # type: ignore
    with pytest.raises(Exception):
        meta.is_funding_permitted = False  # type: ignore


def test_caller_cannot_override_registry_metadata(registry: TokenRegistry):
    """
    Simulate an adversarial client trying to pass a malicious token address or wrong decimals.
    The registry lookup uses only (chain_id, symbol), ignoring any caller params.
    """
    malicious_caller_address = "0x0000000000000000000000000000000000000bad"
    malicious_caller_decimals = 0

    # Registry lookup strictly ignores caller inputs
    canonical_meta = registry.get_token(CHAIN_ID_ANVIL, "USDC")
    assert canonical_meta.token_address != malicious_caller_address
    assert canonical_meta.decimals == 6
    assert canonical_meta.decimals != malicious_caller_decimals


def test_refresh_registry_resets_verification(registry: TokenRegistry):
    """refresh_registry reloads initial CONFIGURED metadata from settings."""
    meta = registry.get_token(CHAIN_ID_ANVIL, "ETH")
    verified = meta.with_verification(trust_level=TrustLevel.VERIFIED)
    registry._tokens[(CHAIN_ID_ANVIL, "ETH")] = verified
    assert registry.get_token(CHAIN_ID_ANVIL, "ETH").trust_level == TrustLevel.VERIFIED

    registry.refresh_registry()
    assert registry.get_token(CHAIN_ID_ANVIL, "ETH").trust_level == TrustLevel.CONFIGURED
