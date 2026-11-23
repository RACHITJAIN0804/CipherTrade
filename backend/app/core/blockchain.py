"""
Blockchain connectivity and contract helper module for CipherTrade.

Connects to the local/configured Ethereum RPC node (Anvil in development)
via Web3.py, reliably resolves ABI artifacts across execution directories,
and provides access to the CipherTradeEscrow contract.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List, Optional

from web3 import Web3
from web3.contract import Contract

from app.config import get_settings

logger = logging.getLogger(__name__)


def get_web3(rpc_url: Optional[str] = None) -> Web3:
    """Return a Web3 instance configured for the given or configured RPC URL."""
    settings = get_settings()
    url = rpc_url or settings.web3_rpc_url
    return Web3(Web3.HTTPProvider(url))


def resolve_abi_path(configured_path: Optional[str | Path] = None) -> Path:
    """
    Resolve the ABI file path reliably regardless of the current working directory.

    Searches:
      1. Exactly as specified (absolute or relative to CWD).
      2. Relative to the repository root (4 directories up from this module).
      3. Relative to the backend directory (3 directories up from this module).
      4. Stripping or prepending '../' or 'contracts/'.
    """
    settings = get_settings()
    path_to_check = configured_path or settings.escrow_abi_path
    path = Path(path_to_check)
    if path.exists():
        return path.resolve()

    # Module is at: <repo_root>/backend/app/core/blockchain.py
    core_dir = Path(__file__).resolve().parent
    app_dir = core_dir.parent
    backend_dir = app_dir.parent
    repo_root = backend_dir.parent

    candidates = [
        repo_root / path,
        backend_dir / path,
        repo_root / "contracts" / "out" / "CipherTradeEscrow.sol" / "CipherTradeEscrow.json",
        backend_dir.parent / "contracts" / "out" / "CipherTradeEscrow.sol" / "CipherTradeEscrow.json",
    ]

    clean_name = path.name
    if clean_name:
        candidates.append(repo_root / "contracts" / "out" / "CipherTradeEscrow.sol" / clean_name)
        candidates.append(repo_root / "contracts" / "out" / "CipherTradeEscrowV2.sol" / clean_name)

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    # Fallback to original path if not found
    return path


def load_v2_contract_abi() -> Optional[List[dict]]:
    """Load CipherTradeEscrowV2 ABI from the compiled Foundry artifact."""
    core_dir = Path(__file__).resolve().parent
    backend_dir = core_dir.parent.parent
    repo_root = backend_dir.parent
    candidates = [
        repo_root / "contracts" / "out" / "CipherTradeEscrowV2.sol" / "CipherTradeEscrowV2.json",
        backend_dir / "contracts" / "out" / "CipherTradeEscrowV2.sol" / "CipherTradeEscrowV2.json",
        Path("contracts/out/CipherTradeEscrowV2.sol/CipherTradeEscrowV2.json"),
    ]
    for c in candidates:
        if c.exists():
            try:
                with c.open("r", encoding="utf-8") as f:
                    artifact = json.load(f)
                return artifact.get("abi")
            except Exception as exc:
                logger.error("Failed to parse V2 contract ABI from %s: %s", c, exc)
                return None
    return None


def get_escrow_v2_contract(
    w3: Optional[Web3] = None,
    address: Optional[str] = None,
    abi: Optional[List[dict]] = None,
) -> Optional[Contract]:
    """Return a web3.eth.contract instance for CipherTradeEscrowV2."""
    settings = get_settings()
    contract_addr = address or settings.escrow_v2_contract_address
    if not contract_addr:
        return None
    contract_abi = abi or load_v2_contract_abi()
    if not contract_abi:
        return None
    client = w3 or get_web3()
    checksummed = Web3.to_checksum_address(contract_addr)
    return client.eth.contract(address=checksummed, abi=contract_abi)



def load_contract_abi(configured_path: Optional[str | Path] = None) -> Optional[List[dict]]:
    """
    Load the contract ABI from the compiled Foundry artifact.

    Returns None if the artifact does not exist or has no 'abi' key.
    """
    settings = get_settings()
    raw_path = configured_path or settings.escrow_contract_abi_path
    resolved = resolve_abi_path(raw_path)

    if not resolved.exists():
        logger.warning("Contract ABI file not found at %s (resolved from %s)", resolved, raw_path)
        return None

    try:
        with resolved.open("r", encoding="utf-8") as f:
            artifact = json.load(f)
        return artifact.get("abi")
    except Exception as exc:
        logger.error("Failed to parse contract ABI from %s: %s", resolved, exc)
        return None


def get_escrow_contract(
    w3: Optional[Web3] = None,
    address: Optional[str] = None,
    abi: Optional[List[dict]] = None,
) -> Optional[Contract]:
    """
    Return a web3.eth.contract instance for CipherTradeEscrow.

    Returns None if no contract address is configured or ABI cannot be loaded.
    """
    settings = get_settings()
    contract_addr = address or settings.escrow_contract_address
    if not contract_addr:
        return None

    contract_abi = abi or load_contract_abi()
    if not contract_abi:
        return None

    client = w3 or get_web3()
    checksummed = Web3.to_checksum_address(contract_addr)
    return client.eth.contract(address=checksummed, abi=contract_abi)


def check_blockchain_health() -> dict:
    """Check connectivity to RPC node and verify chain ID."""
    settings = get_settings()
    w3 = get_web3()
    connected = w3.is_connected()
    chain_id = None
    chain_id_matches = False

    if connected:
        try:
            chain_id = w3.eth.chain_id
            chain_id_matches = (chain_id == settings.chain_id)
        except Exception as exc:
            logger.warning("Error querying chain ID: %s", exc)

    return {
        "connected": connected,
        "rpc_url": settings.web3_rpc_url,
        "chain_id": chain_id,
        "expected_chain_id": settings.chain_id,
        "chain_id_matches": chain_id_matches,
        "contract_configured": bool(settings.escrow_contract_address),
        "contract_address": settings.escrow_contract_address,
    }
