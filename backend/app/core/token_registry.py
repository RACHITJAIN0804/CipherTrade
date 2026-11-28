"""
Authoritative Token Registry and On-Chain Verification for CipherTrade V3.

Provides immutable token metadata, chain-specific token configurations,
and on-chain verification for ETH, USDC, and project-controlled Mock USDT.
Hardened in Phase 6.2.2.3.3:
- Configurable verification TTL (verified_at timestamp).
- Explicit FAILED trust state for active verification failures.
- Preservation of operator enablement and funding restrictions across verifications.
- Removal of implicit fallback for Sepolia USDC.
- Decoupled historical settlement isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from app.config import Settings, get_settings
from app.core.blockchain import get_web3

logger = logging.getLogger(__name__)

# Zero-address sentinel for native ETH
ETH_SENTINEL_ADDRESS: str = "0x0000000000000000000000000000000000000000"

# Standard minimal ERC-20 metadata ABI for decimals and symbol
MINIMAL_ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
]

# Minimal V3 Escrow ABI for checking token allowlist
V3_ALLOWLIST_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "token", "type": "address"}],
        "name": "isTokenAllowed",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    }
]


class TokenType(str, Enum):
    NATIVE = "NATIVE"
    ERC20 = "ERC20"


class TrustLevel(str, Enum):
    CONFIGURED = "CONFIGURED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class TokenRegistryError(ValueError):
    """Base exception for Token Registry operations."""
    pass


class UnsupportedChainError(TokenRegistryError):
    """Raised when an unsupported chain ID is requested or encountered."""
    pass


class UnsupportedSymbolError(TokenRegistryError):
    """Raised when an unsupported currency symbol is requested."""
    pass


class TokenConfigurationError(TokenRegistryError):
    """Raised when configuration for a token is missing, invalid, or mismatched."""
    pass


class VerificationError(TokenRegistryError):
    """Raised when on-chain token or contract verification fails."""
    pass


@dataclass(frozen=True)
class TokenMetadata:
    """
    Immutable metadata for a supported currency on a specific blockchain network.
    """
    symbol: str
    name: str
    chain_id: int
    token_address: str  # Checksummed address or zero-address sentinel
    decimals: int
    token_type: TokenType
    is_enabled: bool = True
    operator_funding_permitted: Optional[bool] = None  # Operator intent from trusted config (immutable across verifications)
    is_funding_permitted: bool = True                  # Derived active funding eligibility
    on_chain_allowed: bool = False                     # On-chain V3 allowlist status
    trust_level: TrustLevel = TrustLevel.CONFIGURED    # Verification trust state
    verification_failure_reason: Optional[str] = None
    verified_at: Optional[float] = None                # Wall-clock timestamp (Unix seconds) for logging/diagnostics
    verified_at_monotonic: Optional[float] = None      # Monotonic timestamp (time.monotonic()) for TTL expiration

    def __post_init__(self) -> None:
        if self.operator_funding_permitted is None:
            object.__setattr__(self, "operator_funding_permitted", self.is_funding_permitted)

        if self.token_type == TokenType.NATIVE:
            if self.token_address != ETH_SENTINEL_ADDRESS:
                raise ValueError(
                    f"Native token {self.symbol} must use ETH_SENTINEL_ADDRESS ({ETH_SENTINEL_ADDRESS}), "
                    f"got {self.token_address}"
                )
        else:
            if not Web3.is_address(self.token_address):
                raise ValueError(f"Invalid token address: {self.token_address}")
            # Ensure address is checksummed
            checksummed = Web3.to_checksum_address(self.token_address)
            if self.token_address != checksummed:
                # frozen dataclass mutation requires object.__setattr__
                object.__setattr__(self, "token_address", checksummed)

        if self.decimals < 0 or self.decimals > 255:
            raise ValueError(f"Decimals must be between 0 and 255, got {self.decimals}")

    def with_verification(
        self,
        trust_level: TrustLevel,
        failure_reason: Optional[str] = None,
        is_funding_permitted: Optional[bool] = None,
        on_chain_allowed: Optional[bool] = None,
        verified_at: Optional[float] = None,
        verified_at_monotonic: Optional[float] = None,
        verification_failure_reason: Optional[str] = None,
    ) -> TokenMetadata:
        """Create a new immutable TokenMetadata with updated trust state while strictly preserving operator intent."""
        effective_failure_reason = failure_reason or verification_failure_reason
        if trust_level == TrustLevel.VERIFIED:
            now_wall = time.time()
            now_mono = time.monotonic()
            if verified_at is not None and verified_at_monotonic is None:
                # If explicit wall-clock verified_at was provided, synchronize monotonic offset
                offset = now_wall - verified_at
                resolved_mono = now_mono - offset
                resolved_wall = verified_at
            else:
                resolved_wall = verified_at if verified_at is not None else now_wall
                resolved_mono = verified_at_monotonic if verified_at_monotonic is not None else now_mono
        else:
            resolved_wall = None
            resolved_mono = None

        return TokenMetadata(
            symbol=self.symbol,
            name=self.name,
            chain_id=self.chain_id,
            token_address=self.token_address,
            decimals=self.decimals,
            token_type=self.token_type,
            is_enabled=self.is_enabled,
            operator_funding_permitted=self.operator_funding_permitted,
            is_funding_permitted=self.is_funding_permitted if is_funding_permitted is None else is_funding_permitted,
            on_chain_allowed=self.on_chain_allowed if on_chain_allowed is None else on_chain_allowed,
            trust_level=trust_level,
            verification_failure_reason=effective_failure_reason,
            verified_at=resolved_wall,
            verified_at_monotonic=resolved_mono,
        )

    def is_verification_expired(self, ttl_seconds: Optional[int] = None) -> bool:
        """
        Check if verification has expired past the given TTL using the monotonic clock.
        If never verified or verified_at_monotonic is None, returns True.
        """
        if self.trust_level != TrustLevel.VERIFIED or self.verified_at_monotonic is None:
            return True
        ttl = ttl_seconds if ttl_seconds is not None else get_settings().token_verification_ttl_seconds
        now_mono = time.monotonic()
        return (now_mono - self.verified_at_monotonic) > ttl

    def can_fund(self, ttl_seconds: Optional[int] = None) -> bool:
        """
        Check if new funding is permitted for this token.
        Derives eligibility from:
        - Operator-enabled token (is_enabled=True)
        - Operator-permitted funding (operator_funding_permitted=True)
        - Derived active funding permission (is_funding_permitted=True)
        - On-chain allowlist status (on_chain_allowed=True)
        - VERIFIED trust level
        - Fresh non-expired verification (within ttl_seconds using monotonic clock)
        - No verification failure reason
        """
        if not self.is_enabled:
            return False
        if not self.operator_funding_permitted:
            return False
        if not self.is_funding_permitted:
            return False
        if self.trust_level != TrustLevel.VERIFIED:
            return False
        if not self.on_chain_allowed:
            return False
        if self.verification_failure_reason is not None:
            return False
        if self.is_verification_expired(ttl_seconds):
            return False
        return True


@dataclass(frozen=True)
class HistoricalTokenInfo:
    """
    Persisted settlement metadata for an existing funded deal.
    Settlement operations (withdrawal, refund, dispute resolution) use persisted deal metadata
    and do not require active funding permission, current registry enablement, or verification TTL.
    """
    symbol: str
    token_address: str
    decimals: int
    chain_id: int
    contract_version: str
    contract_address: str


# Canonical Supported Chain IDs
CHAIN_ID_ANVIL: int = 31337
CHAIN_ID_SEPOLIA: int = 11155111
SUPPORTED_CHAIN_IDS = {CHAIN_ID_ANVIL, CHAIN_ID_SEPOLIA}

# Official Circle USDC deployment on Sepolia
SEPOLIA_USDC_OFFICIAL: str = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"


class TokenRegistry:
    """
    Authoritative in-memory registry of supported tokens and on-chain verifier.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings: Settings = settings or get_settings()
        self._tokens: Dict[Tuple[int, str], TokenMetadata] = {}
        self._load_configured_tokens()

    def _load_configured_tokens(self) -> None:
        """
        Populate initial CONFIGURED metadata from settings.
        Missing required addresses produce an explicit unavailable state rather than fallback.
        """
        self._tokens.clear()

        # ── Anvil (31337) ──────────────────────────────────────────────────────────
        # ETH
        self._tokens[(CHAIN_ID_ANVIL, "ETH")] = TokenMetadata(
            symbol="ETH",
            name="Ether",
            chain_id=CHAIN_ID_ANVIL,
            token_address=ETH_SENTINEL_ADDRESS,
            decimals=18,
            token_type=TokenType.NATIVE,
            is_enabled=True,
            is_funding_permitted=True,
            trust_level=TrustLevel.CONFIGURED,
        )

        # Anvil USDC
        anvil_usdc = getattr(self.settings, "anvil_usdc_contract_address", None)
        if anvil_usdc and Web3.is_address(anvil_usdc):
            self._tokens[(CHAIN_ID_ANVIL, "USDC")] = TokenMetadata(
                symbol="USDC",
                name="USD Coin (Anvil Mock)",
                chain_id=CHAIN_ID_ANVIL,
                token_address=Web3.to_checksum_address(anvil_usdc),
                decimals=6,
                token_type=TokenType.ERC20,
                is_enabled=True,
                is_funding_permitted=True,
                trust_level=TrustLevel.CONFIGURED,
            )

        # Anvil USDT (Project-controlled mock, void-return)
        anvil_usdt = getattr(self.settings, "anvil_usdt_contract_address", None)
        if anvil_usdt and Web3.is_address(anvil_usdt):
            self._tokens[(CHAIN_ID_ANVIL, "USDT")] = TokenMetadata(
                symbol="USDT",
                name="Tether USD (CipherTrade Mock)",
                chain_id=CHAIN_ID_ANVIL,
                token_address=Web3.to_checksum_address(anvil_usdt),
                decimals=6,
                token_type=TokenType.ERC20,
                is_enabled=True,
                is_funding_permitted=True,
                trust_level=TrustLevel.CONFIGURED,
            )

        # ── Sepolia (11155111) ────────────────────────────────────────────────────
        # ETH
        self._tokens[(CHAIN_ID_SEPOLIA, "ETH")] = TokenMetadata(
            symbol="ETH",
            name="Ether",
            chain_id=CHAIN_ID_SEPOLIA,
            token_address=ETH_SENTINEL_ADDRESS,
            decimals=18,
            token_type=TokenType.NATIVE,
            is_enabled=True,
            is_funding_permitted=True,
            trust_level=TrustLevel.CONFIGURED,
        )

        # Sepolia USDC - Configured Address only (NO implicit module-level fallback)
        sepolia_usdc = getattr(self.settings, "sepolia_usdc_contract_address", None)
        if sepolia_usdc and Web3.is_address(sepolia_usdc):
            self._tokens[(CHAIN_ID_SEPOLIA, "USDC")] = TokenMetadata(
                symbol="USDC",
                name="USD Coin",
                chain_id=CHAIN_ID_SEPOLIA,
                token_address=Web3.to_checksum_address(sepolia_usdc),
                decimals=6,
                token_type=TokenType.ERC20,
                is_enabled=True,
                is_funding_permitted=True,
                trust_level=TrustLevel.CONFIGURED,
            )

        # Sepolia USDT - Project-controlled Mock only
        sepolia_usdt = getattr(self.settings, "sepolia_usdt_contract_address", None)
        if sepolia_usdt and Web3.is_address(sepolia_usdt):
            self._tokens[(CHAIN_ID_SEPOLIA, "USDT")] = TokenMetadata(
                symbol="USDT",
                name="Tether USD (CipherTrade Mock)",
                chain_id=CHAIN_ID_SEPOLIA,
                token_address=Web3.to_checksum_address(sepolia_usdt),
                decimals=6,
                token_type=TokenType.ERC20,
                is_enabled=True,
                is_funding_permitted=True,
                trust_level=TrustLevel.CONFIGURED,
            )

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """Normalize symbol string by stripping whitespace and converting to uppercase."""
        if not symbol or not isinstance(symbol, str):
            raise UnsupportedSymbolError(f"Invalid symbol type: {type(symbol)}")
        normalized = symbol.strip().upper()
        if not normalized:
            raise UnsupportedSymbolError("Symbol cannot be empty")
        return normalized

    def get_token(self, chain_id: int, symbol: str) -> TokenMetadata:
        """
        Lookup authoritative token metadata by (chain_id, symbol).
        Rejects unsupported chains and symbols.
        Never trusts caller-supplied token addresses or decimals.
        """
        if chain_id not in SUPPORTED_CHAIN_IDS:
            raise UnsupportedChainError(
                f"Unsupported chain_id: {chain_id}. Supported chains: {sorted(list(SUPPORTED_CHAIN_IDS))}"
            )

        norm_symbol = self.normalize_symbol(symbol)
        key = (chain_id, norm_symbol)
        if key not in self._tokens:
            raise UnsupportedSymbolError(
                f"Token symbol '{norm_symbol}' is not configured on chain_id {chain_id}"
            )

        return self._tokens[key]

    def get_all_tokens(self, chain_id: Optional[int] = None) -> List[TokenMetadata]:
        """Return all configured tokens, optionally filtered by chain ID."""
        if chain_id is not None:
            if chain_id not in SUPPORTED_CHAIN_IDS:
                raise UnsupportedChainError(f"Unsupported chain_id: {chain_id}")
            return [t for (cid, _), t in self._tokens.items() if cid == chain_id]
        return list(self._tokens.values())

    def get_v3_contract_address(self, chain_id: int) -> str:
        """
        Get the configured CipherTradeEscrowV3 contract address for the given chain ID.
        """
        if chain_id not in SUPPORTED_CHAIN_IDS:
            raise UnsupportedChainError(f"Unsupported chain_id: {chain_id}")

        if chain_id == CHAIN_ID_ANVIL:
            addr = getattr(self.settings, "escrow_v3_contract_address", None) or getattr(
                self.settings, "anvil_escrow_v3_contract_address", None
            )
        elif chain_id == CHAIN_ID_SEPOLIA:
            addr = getattr(self.settings, "sepolia_escrow_v3_contract_address", None)
        else:
            addr = None

        if not addr or not Web3.is_address(addr):
            raise TokenConfigurationError(
                f"CipherTradeEscrowV3 address is not configured or invalid for chain_id {chain_id}"
            )

        return Web3.to_checksum_address(addr)

    def verify_token_on_chain(
        self,
        chain_id: int,
        symbol: str,
        w3: Optional[Web3] = None,
        v3_escrow_address: Optional[str] = None,
    ) -> TokenMetadata:
        """
        Execute on-chain verification for a token:
        1. Connected RPC chain_id matches expected chain_id.
        2. Configured V3 contract address contains deployed bytecode.
        3. Configured token address contains deployed bytecode (for ERC20).
        4. Token decimals() matches registry metadata (for ERC20).
        5. V3 contract isTokenAllowed(tokenAddress) returns True.
        6. Native ETH verifies chain & V3 contract allowlist for sentinel address.

        Security & Invariants (Phase 6.2.2.3.3):
        - On success: transitions to VERIFIED, sets fresh verified_at timestamp.
        - Operator preservation (H-02): is_funding_permitted remains FALSE if operator disabled it!
          Only set to True if operator permitted it AND on-chain allowlist allows it.
        - On failure: transitions to FAILED, sets verification_failure_reason, clears verified_at,
          and revokes is_funding_permitted (sets to False).
        """
        current_meta = self.get_token(chain_id, symbol)
        client = w3 or get_web3()

        try:
            # Check RPC connectivity
            if not client.is_connected():
                raise VerificationError("Web3 RPC provider is not connected")

            # 1. Chain ID check
            rpc_chain_id = client.eth.chain_id
            if rpc_chain_id != chain_id:
                raise VerificationError(
                    f"Connected RPC chain ID ({rpc_chain_id}) does not match expected chain ID ({chain_id})"
                )

            # 2. V3 Escrow Contract verification
            v3_addr = v3_escrow_address or self.get_v3_contract_address(chain_id)
            v3_code = client.eth.get_code(v3_addr)
            if not v3_code or v3_code == b"" or v3_code == b"\x00" or v3_code.hex() in ("0x", "0x0"):
                raise VerificationError(
                    f"CipherTradeEscrowV3 contract at {v3_addr} has no deployed bytecode on chain {chain_id}"
                )

            v3_contract = client.eth.contract(address=v3_addr, abi=V3_ALLOWLIST_ABI)

            if current_meta.token_type == TokenType.NATIVE:
                # Native ETH verification
                # Check V3 allowlist for ETH sentinel
                is_allowed = v3_contract.functions.isTokenAllowed(ETH_SENTINEL_ADDRESS).call()
                if not is_allowed:
                    raise VerificationError(
                        f"Native ETH sentinel ({ETH_SENTINEL_ADDRESS}) is not allowed in V3 contract at {v3_addr}"
                    )

            else:
                # ERC20 Verification
                token_addr = current_meta.token_address
                # 3. Token Bytecode check
                token_code = client.eth.get_code(token_addr)
                if not token_code or token_code == b"" or token_code == b"\x00" or token_code.hex() in ("0x", "0x0"):
                    raise VerificationError(
                        f"ERC20 token {current_meta.symbol} at {token_addr} has no deployed bytecode on chain {chain_id}"
                    )

                # 4. Decimals check
                erc20_contract = client.eth.contract(address=token_addr, abi=MINIMAL_ERC20_ABI)
                try:
                    on_chain_decimals = erc20_contract.functions.decimals().call()
                except Exception as exc:
                    raise VerificationError(
                        f"Failed to query decimals() on {current_meta.symbol} at {token_addr}: {exc}"
                    ) from exc

                if on_chain_decimals != current_meta.decimals:
                    raise VerificationError(
                        f"Decimals mismatch for {current_meta.symbol}: registry has {current_meta.decimals}, "
                        f"on-chain reports {on_chain_decimals}"
                    )

                # Optional diagnostic check: symbol()
                try:
                    on_chain_symbol = erc20_contract.functions.symbol().call()
                    if on_chain_symbol and on_chain_symbol.strip().upper() != current_meta.symbol:
                        logger.warning(
                            "Diagnostic symbol check mismatch: token at %s reports symbol '%s', expected '%s'",
                            token_addr,
                            on_chain_symbol,
                            current_meta.symbol,
                        )
                except Exception as sym_err:
                    logger.debug("Optional on-chain symbol() query failed for %s: %s", token_addr, sym_err)

                # 5. V3 allowlist check
                try:
                    is_allowed = v3_contract.functions.isTokenAllowed(token_addr).call()
                except Exception as exc:
                    raise VerificationError(
                        f"Failed to check isTokenAllowed({token_addr}) on V3 escrow {v3_addr}: {exc}"
                    ) from exc

                if not is_allowed:
                    raise VerificationError(
                        f"Token {current_meta.symbol} ({token_addr}) is not allowed for funding in V3 contract {v3_addr}"
                    )

            # Verification passed:
            is_allowed = True
            funding_permitted = bool(
                current_meta.operator_funding_permitted
                and current_meta.is_enabled
                and is_allowed
            )
            now_wall = time.time()
            now_mono = time.monotonic()

            verified_meta = current_meta.with_verification(
                trust_level=TrustLevel.VERIFIED,
                failure_reason=None,
                is_funding_permitted=funding_permitted,
                on_chain_allowed=is_allowed,
                verified_at=now_wall,
                verified_at_monotonic=now_mono,
            )
            self._tokens[(chain_id, current_meta.symbol)] = verified_meta
            logger.info("Successfully verified token %s on chain %d (funding_permitted=%s)", current_meta.symbol, chain_id, funding_permitted)
            return verified_meta

        except Exception as exc:
            failure_reason = str(exc)
            logger.warning(
                "Verification failed for token %s on chain %d: %s",
                current_meta.symbol,
                chain_id,
                failure_reason,
            )
            # Active failure transitions to TrustLevel.FAILED and revokes derived funding eligibility
            # Operator settings (operator_funding_permitted, is_enabled) are strictly preserved!
            failed_meta = current_meta.with_verification(
                trust_level=TrustLevel.FAILED,
                failure_reason=failure_reason,
                is_funding_permitted=False,
                on_chain_allowed=False,
                verified_at=None,
                verified_at_monotonic=None,
            )
            self._tokens[(chain_id, current_meta.symbol)] = failed_meta
            return failed_meta

    def ensure_verified_for_funding(
        self,
        chain_id: int,
        symbol: str,
        w3: Optional[Web3] = None,
        ttl_seconds: Optional[int] = None,
    ) -> TokenMetadata:
        """
        Ensure token metadata is VERIFIED and within the verification TTL window.
        If expired, unverified, or FAILED, executes a fresh verification before authorizing funding.
        Raises VerificationError if verification fails or can_fund() is False.
        """
        meta = self.get_token(chain_id, symbol)
        if meta.trust_level != TrustLevel.VERIFIED or meta.is_verification_expired(ttl_seconds):
            logger.info("Token %s on chain %d verification expired or pending; executing fresh verification", symbol, chain_id)
            meta = self.verify_token_on_chain(chain_id, symbol, w3=w3)

        if not meta.can_fund(ttl_seconds):
            reason = meta.verification_failure_reason or "Funding not permitted by operator or verification state"
            raise VerificationError(f"Token {symbol} on chain {chain_id} cannot fund: {reason}")

        return meta

    def verify_all_configured_tokens(
        self,
        chain_id: int,
        w3: Optional[Web3] = None,
    ) -> Dict[str, TokenMetadata]:
        """
        Verify all configured tokens on a chain.
        Returns a mapping of symbol -> TokenMetadata.
        Failed tokens are kept in FAILED state with is_funding_permitted=False.
        """
        results = {}
        for token in self.get_all_tokens(chain_id):
            verified = self.verify_token_on_chain(chain_id, token.symbol, w3=w3)
            results[token.symbol] = verified
        return results

    def refresh_registry(self) -> None:
        """
        Reload initial configured metadata from settings, resetting any cached verification.
        Used after allowlist changes or contract redeployments.
        """
        self._load_configured_tokens()

    @staticmethod
    def get_settlement_token_info(
        symbol: str,
        token_address: str,
        decimals: int,
        chain_id: int,
        contract_version: str,
        contract_address: str,
    ) -> HistoricalTokenInfo:
        """
        Construct settlement metadata for an existing funded deal.
        Does not query current registry funding permissions, active status, or TTL.
        """
        norm_symbol = TokenRegistry.normalize_symbol(symbol)
        norm_token_addr = (
            ETH_SENTINEL_ADDRESS
            if norm_symbol == "ETH" or token_address == ETH_SENTINEL_ADDRESS
            else Web3.to_checksum_address(token_address)
        )
        norm_contract_addr = Web3.to_checksum_address(contract_address)

        return HistoricalTokenInfo(
            symbol=norm_symbol,
            token_address=norm_token_addr,
            decimals=decimals,
            chain_id=chain_id,
            contract_version=contract_version,
            contract_address=norm_contract_addr,
        )


_registry_instance: Optional[TokenRegistry] = None


def get_token_registry() -> TokenRegistry:
    """Singleton accessor for authoritative TokenRegistry."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = TokenRegistry()
    return _registry_instance
