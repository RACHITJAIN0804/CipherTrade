"""Comprehensive test suite for Phase 5B: Escrow Funding Verification."""
from __future__ import annotations

import time
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from web3 import Web3

from app.core.blockchain import (
    check_blockchain_health,
    get_escrow_contract,
    get_web3,
    load_contract_abi,
    resolve_abi_path,
)
from app.models.models import EscrowDeal, EscrowState, User


async def _register_login_and_set_wallet(
    client: AsyncClient, email: str, role: str, wallet: str | None = None
) -> tuple[str, dict]:
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "display_name": f"User {email.split('@')[0]}",
            "primary_role": role,
            "password": "ValidPassword123!",
        },
    )
    user_info = reg.json()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "ValidPassword123!"},
    )
    token = login.json()["access_token"]
    if wallet:
        patch_resp = await client.patch(
            "/api/v1/users/me",
            json={"wallet_address": wallet},
            headers={"Authorization": f"Bearer {token}"},
        )
        user_info = patch_resp.json()
    return token, user_info


async def _create_deal(
    client: AsyncClient,
    buyer_token: str,
    seller_token: str,
    amount_eth: float = 1.5,
    deadline_offset: int = 86400 * 3,
    agreement_hash: str = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
) -> tuple[dict, int]:
    # 1. Buyer creates requirement
    req_resp = await client.post(
        "/api/v1/requirements",
        json={
            "title": f"Test Req {uuid.uuid4().hex[:8]}",
            "description": "Integration test requirement for Phase 5B escrow.",
            "category": "blockchain",
        },
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert req_resp.status_code == 201
    req_id = req_resp.json()["id"]

    # 2. Seller makes offer
    deadline = int(time.time()) + deadline_offset
    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": amount_eth,
            "delivery_deadline": deadline,
            "terms": "Phase 5B escrow verification terms.",
        },
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert offer_resp.status_code == 201
    offer_id = offer_resp.json()["id"]

    # 3. Buyer accepts offer
    acc_resp = await client.post(
        f"/api/v1/offers/{offer_id}/accept",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert acc_resp.status_code == 200

    # 4. Buyer creates escrow deal
    deal_resp = await client.post(
        "/api/v1/escrow/deals",
        json={
            "offer_id": offer_id,
            "amount_eth": amount_eth,
            "deadline_ts": deadline,
            "agreement_hash": agreement_hash,
        },
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert deal_resp.status_code == 201
    return deal_resp.json(), deadline


# ─── Unit / Health Tests ──────────────────────────────────────────────────────

def test_resolve_abi_and_load():
    abi_path = resolve_abi_path()
    assert abi_path.is_file()
    abi = load_contract_abi()
    assert isinstance(abi, list)
    assert len(abi) > 0
    names = [item.get("name") for item in abi if item.get("type") == "function"]
    assert "fundDeal" in names
    assert "getDeal" in names


def test_blockchain_health():
    health = check_blockchain_health()
    assert health["connected"] is True
    assert health["chain_id"] == 31337
    assert health["contract_address"] == "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    assert health["contract_configured"] is True


# ─── Format & Authorization Tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_funding_invalid_tx_format(client: AsyncClient):
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_inv@test.com", "buyer")
    seller_token, _ = await _register_login_and_set_wallet(client, "s_inv@test.com", "seller")
    deal, _ = await _create_deal(client, buyer_token, seller_token)
    deal_id = deal["id"]

    for bad_hash in ["invalid-hash", "0x1234", "0x" + "g" * 64, "12345678901234567890"]:
        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": bad_hash},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_verify_funding_unauthorized_user(client: AsyncClient):
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_unauth@test.com", "buyer")
    seller_token, _ = await _register_login_and_set_wallet(client, "s_unauth@test.com", "seller")
    other_token, _ = await _register_login_and_set_wallet(client, "other_unauth@test.com", "buyer")
    deal, _ = await _create_deal(client, buyer_token, seller_token)
    deal_id = deal["id"]
    fake_tx = "0x" + "a" * 64

    # Seller cannot verify funding
    resp_seller = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": fake_tx},
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert resp_seller.status_code == 403

    # Random third party cannot verify funding
    resp_other = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": fake_tx},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp_other.status_code == 403


@pytest.mark.asyncio
async def test_verify_funding_no_buyer_wallet(client: AsyncClient):
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_nowallet@test.com", "buyer", wallet=None)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_nowallet@test.com", "seller", wallet="0x" + "2" * 40)
    deal, _ = await _create_deal(client, buyer_token, seller_token)
    deal_id = deal["id"]

    fake_tx = "0x" + "b" * 64
    resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": fake_tx},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 400
    assert "registered wallet address" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_funding_pending_or_missing_tx(client: AsyncClient):
    buyer_wallet = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_missingtx@test.com", "buyer", wallet=buyer_wallet)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_missingtx@test.com", "seller", wallet="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")
    deal, _ = await _create_deal(client, buyer_token, seller_token)
    deal_id = deal["id"]

    nonexistent_tx = "0x" + "9" * 64
    resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": nonexistent_tx},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 400
    assert "not found on chain" in resp.json()["detail"].lower()


# ─── Mocked Failure Condition Tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_funding_failed_receipt(client: AsyncClient):
    buyer_wallet = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    contract_addr = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_failrcpt@test.com", "buyer", wallet=buyer_wallet)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_failrcpt@test.com", "seller", wallet="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")
    deal, _ = await _create_deal(client, buyer_token, seller_token)
    deal_id = deal["id"]
    fake_tx = "0x" + "c" * 64

    mock_tx = {
        "blockNumber": 100,
        "chainId": 31337,
        "to": contract_addr,
        "from": buyer_wallet,
        "value": int(1.5 * 10**18),
    }
    mock_receipt = {
        "blockNumber": 100,
        "status": 0,  # Reverted on chain
        "from": buyer_wallet,
        "to": contract_addr,
    }
    with patch("app.services.escrow_service.get_web3") as mock_gw3:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 400
        assert "transaction failed on chain" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_funding_wrong_chain_id(client: AsyncClient):
    buyer_wallet = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_wrchain@test.com", "buyer", wallet=buyer_wallet)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_wrchain@test.com", "seller", wallet="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")
    deal, _ = await _create_deal(client, buyer_token, seller_token)
    deal_id = deal["id"]
    fake_tx = "0x" + "d" * 64

    with patch("app.services.escrow_service.get_web3") as mock_gw3:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 1  # Node reports chain ID 1
        mock_gw3.return_value = mock_w3

        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 400
        assert "chain id mismatch" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_funding_wrong_destination(client: AsyncClient):
    buyer_wallet = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    contract_addr = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    wrong_contract = "0x1111111111111111111111111111111111111111"
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_wrdest@test.com", "buyer", wallet=buyer_wallet)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_wrdest@test.com", "seller", wallet="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")
    deal, _ = await _create_deal(client, buyer_token, seller_token)
    deal_id = deal["id"]
    fake_tx = "0x" + "e" * 64

    mock_tx = {
        "blockNumber": 100,
        "chainId": 31337,
        "to": wrong_contract,
        "from": buyer_wallet,
        "value": int(1.5 * 10**18),
    }
    mock_receipt = {
        "blockNumber": 100,
        "status": 1,
        "from": buyer_wallet,
        "to": wrong_contract,
    }
    with patch("app.services.escrow_service.get_web3") as mock_gw3:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 400
        assert "destination does not match" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_funding_wrong_sender(client: AsyncClient):
    buyer_wallet = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    contract_addr = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    wrong_sender = "0x9999999999999999999999999999999999999999"
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_wrsnd@test.com", "buyer", wallet=buyer_wallet)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_wrsnd@test.com", "seller", wallet="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")
    deal, _ = await _create_deal(client, buyer_token, seller_token)
    deal_id = deal["id"]
    fake_tx = "0x" + "f" * 64

    mock_tx = {
        "blockNumber": 100,
        "chainId": 31337,
        "to": contract_addr,
        "from": wrong_sender,
        "value": int(1.5 * 10**18),
    }
    mock_receipt = {
        "blockNumber": 100,
        "status": 1,
        "from": wrong_sender,
        "to": contract_addr,
    }
    with patch("app.services.escrow_service.get_web3") as mock_gw3:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 400
        assert "transaction sender does not match" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_funding_missing_event(client: AsyncClient):
    buyer_wallet = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    contract_addr = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_msgevt@test.com", "buyer", wallet=buyer_wallet)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_msgevt@test.com", "seller", wallet="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")
    deal, _ = await _create_deal(client, buyer_token, seller_token)
    deal_id = deal["id"]
    fake_tx = "0x" + "1" * 64

    mock_tx = {
        "blockNumber": 100,
        "chainId": 31337,
        "to": contract_addr,
        "from": buyer_wallet,
        "value": int(1.5 * 10**18),
    }
    mock_receipt = {
        "blockNumber": 100,
        "status": 1,
        "from": buyer_wallet,
        "to": contract_addr,
    }
    with patch("app.services.escrow_service.get_web3") as mock_gw3, \
         patch("app.services.escrow_service.get_escrow_contract") as mock_gcontract:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        mock_contract = MagicMock()
        mock_contract.events.DealFunded.return_value.process_receipt.return_value = []  # No event
        mock_gcontract.return_value = mock_contract

        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 400
        assert "no dealfunded event" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_funding_parameter_mismatches(client: AsyncClient):
    buyer_wallet = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    seller_wallet = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_mismatch@test.com", "buyer", wallet=buyer_wallet)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_mismatch@test.com", "seller", wallet=seller_wallet)
    agreement_hash = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    deal, deadline = await _create_deal(
        client, buyer_token, seller_token, amount_eth=1.5, agreement_hash=agreement_hash
    )
    deal_id = deal["id"]
    fake_tx = "0x" + "2" * 64
    contract_addr = "0x5FbDB2315678afecb367f032d93F642f64180aa3"

    mock_tx = {
        "blockNumber": 100,
        "chainId": 31337,
        "to": contract_addr,
        "from": buyer_wallet,
        "value": int(1.5 * 10**18),
    }
    mock_receipt = {
        "blockNumber": 100,
        "status": 1,
        "from": buyer_wallet,
        "to": contract_addr,
    }

    # Case 1: Amount mismatch
    with patch("app.services.escrow_service.get_web3") as mock_gw3, \
         patch("app.services.escrow_service.get_escrow_contract") as mock_gcontract:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        mock_contract = MagicMock()
        mock_contract.events.DealFunded.return_value.process_receipt.return_value = [{
            "address": contract_addr,
            "args": {
                "dealId": 999,
                "buyer": buyer_wallet,
                "seller": seller_wallet,
                "amount": int(1.0 * 10**18),  # 1.0 ETH instead of 1.5 ETH
                "deadline": deadline,
                "agreementHash": bytes.fromhex(agreement_hash[2:]),
            }
        }]
        mock_gcontract.return_value = mock_contract

        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 400
        assert "amount mismatch" in resp.json()["detail"].lower()

    # Case 2: Deadline mismatch
    with patch("app.services.escrow_service.get_web3") as mock_gw3, \
         patch("app.services.escrow_service.get_escrow_contract") as mock_gcontract:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        mock_contract = MagicMock()
        mock_contract.events.DealFunded.return_value.process_receipt.return_value = [{
            "address": contract_addr,
            "args": {
                "dealId": 999,
                "buyer": buyer_wallet,
                "seller": seller_wallet,
                "amount": int(1.5 * 10**18),
                "deadline": deadline + 1000,  # Mismatched deadline
                "agreementHash": bytes.fromhex(agreement_hash[2:]),
            }
        }]
        mock_gcontract.return_value = mock_contract

        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 400
        assert "deadline mismatch" in resp.json()["detail"].lower()

    # Case 3: Agreement hash mismatch
    with patch("app.services.escrow_service.get_web3") as mock_gw3, \
         patch("app.services.escrow_service.get_escrow_contract") as mock_gcontract:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        mock_contract = MagicMock()
        mock_contract.events.DealFunded.return_value.process_receipt.return_value = [{
            "address": contract_addr,
            "args": {
                "dealId": 999,
                "buyer": buyer_wallet,
                "seller": seller_wallet,
                "amount": int(1.5 * 10**18),
                "deadline": deadline,
                "agreementHash": b"\x00" * 32,  # Different agreement hash
            }
        }]
        mock_gcontract.return_value = mock_contract

        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 400
        assert "agreement hash mismatch" in resp.json()["detail"].lower()


# ─── Live On-Chain Integration Tests (Anvil) ───────────────────────────────────

@pytest.mark.asyncio
async def test_live_anvil_funding_verification_and_idempotency(client: AsyncClient):
    """
    Full end-to-end integration test against the running Anvil local node.
    Performs real smart contract transaction, confirms receipt on-chain,
    verifies funding through the backend API endpoint, and checks idempotency.
    """
    w3 = get_web3()
    assert w3.is_connected() is True

    # Anvil standard accounts
    # Account 1 = Buyer
    buyer_addr = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    buyer_pk = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    # Account 2 = Seller
    seller_addr = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"

    buyer_token, _ = await _register_login_and_set_wallet(client, "b_live@test.com", "buyer", wallet=buyer_addr)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_live@test.com", "seller", wallet=seller_addr)

    amount_eth = 0.5
    agreement_hash_hex = "0x" + "ab" * 32
    deal, deadline = await _create_deal(
        client,
        buyer_token,
        seller_token,
        amount_eth=amount_eth,
        deadline_offset=86400 * 2,
        agreement_hash=agreement_hash_hex,
    )
    deal_id = deal["id"]

    # Execute real on-chain transaction calling fundDeal
    contract = get_escrow_contract(w3)
    fund_fn = contract.functions.fundDeal(
        seller_addr,
        deadline,
        bytes.fromhex(agreement_hash_hex[2:]),
    )
    tx_params = {
        "from": buyer_addr,
        "value": w3.to_wei(amount_eth, "ether"),
        "nonce": w3.eth.get_transaction_count(buyer_addr),
        "gas": 300000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 31337,
    }
    raw_tx = fund_fn.build_transaction(tx_params)
    signed_tx = w3.eth.account.sign_transaction(raw_tx, private_key=buyer_pk)
    tx_hash_bytes = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    tx_hash = w3.to_hex(tx_hash_bytes)

    # Wait for block inclusion on Anvil
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=10)
    assert receipt["status"] == 1

    # Verify funding via backend endpoint
    verify_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert verify_resp.status_code == 200
    verified_deal = verify_resp.json()
    assert verified_deal["on_chain_state"] == "FUNDED"
    assert verified_deal["fund_tx_hash"].lower() == tx_hash.lower()
    assert verified_deal["chain_deal_id"] is not None
    chain_deal_id = verified_deal["chain_deal_id"]

    # Verify get deal endpoint reflects the funded state
    deal_get = await client.get(
        f"/api/v1/escrow/{deal_id}",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert deal_get.status_code == 200
    assert deal_get.json()["on_chain_state"] == "FUNDED"
    assert deal_get.json()["chain_deal_id"] == chain_deal_id

    # Test idempotency: repeating verification with the exact same tx_hash succeeds
    re_verify_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert re_verify_resp.status_code == 200
    assert re_verify_resp.json()["on_chain_state"] == "FUNDED"

    # Test duplicate transaction check: another deal cannot use this same tx_hash
    other_deal, _ = await _create_deal(client, buyer_token, seller_token, amount_eth=amount_eth)
    dup_tx_resp = await client.post(
        f"/api/v1/escrow/deals/{other_deal['id']}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert dup_tx_resp.status_code == 409
    assert "already associated" in dup_tx_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_funding_no_seller_wallet(client: AsyncClient):
    """Ensure verification fails if seller has not established a registered wallet."""
    buyer_wallet = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    contract_addr = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_nosellw@test.com", "buyer", wallet=buyer_wallet)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_nosellw@test.com", "seller", wallet=None)
    deal, deadline = await _create_deal(client, buyer_token, seller_token)
    deal_id = deal["id"]
    fake_tx = "0x" + "7" * 64

    mock_tx = {
        "blockNumber": 100,
        "chainId": 31337,
        "to": contract_addr,
        "from": buyer_wallet,
        "value": int(1.5 * 10**18),
    }
    mock_receipt = {
        "blockNumber": 100,
        "status": 1,
        "from": buyer_wallet,
        "to": contract_addr,
    }
    with patch("app.services.escrow_service.get_web3") as mock_gw3, \
         patch("app.services.escrow_service.get_escrow_contract") as mock_gcontract:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        mock_contract = MagicMock()
        mock_contract.events.DealFunded.return_value.process_receipt.return_value = [{
            "address": contract_addr,
            "args": {
                "dealId": 999,
                "buyer": buyer_wallet,
                "seller": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
                "amount": int(1.5 * 10**18),
                "deadline": deadline,
                "agreementHash": bytes.fromhex("1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"),
            }
        }]
        mock_gcontract.return_value = mock_contract

        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 400
        assert "seller does not have a registered wallet address" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_funding_seller_wallet_mismatch(client: AsyncClient):
    """Ensure verification fails if event seller address differs from seller's registered wallet."""
    buyer_wallet = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    seller_registered_wallet = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    contract_addr = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_sellmismatch@test.com", "buyer", wallet=buyer_wallet)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_sellmismatch@test.com", "seller", wallet=seller_registered_wallet)
    deal, deadline = await _create_deal(client, buyer_token, seller_token)
    deal_id = deal["id"]
    fake_tx = "0x" + "8" * 64

    mock_tx = {
        "blockNumber": 100,
        "chainId": 31337,
        "to": contract_addr,
        "from": buyer_wallet,
        "value": int(1.5 * 10**18),
    }
    mock_receipt = {
        "blockNumber": 100,
        "status": 1,
        "from": buyer_wallet,
        "to": contract_addr,
    }
    with patch("app.services.escrow_service.get_web3") as mock_gw3, \
         patch("app.services.escrow_service.get_escrow_contract") as mock_gcontract:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        mock_contract = MagicMock()
        # Event has a different seller address than seller_registered_wallet
        mock_contract.events.DealFunded.return_value.process_receipt.return_value = [{
            "address": contract_addr,
            "args": {
                "dealId": 999,
                "buyer": buyer_wallet,
                "seller": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
                "amount": int(1.5 * 10**18),
                "deadline": deadline,
                "agreementHash": bytes.fromhex("1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"),
            }
        }]
        mock_gcontract.return_value = mock_contract

        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 400
        assert "does not match seller's registered wallet address" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_funding_missing_expected_agreement_hash(client: AsyncClient):
    """Ensure verification fails if deal does not have an agreement hash established in DB."""
    buyer_wallet = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    seller_wallet = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    contract_addr = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    buyer_token, _ = await _register_login_and_set_wallet(client, "b_nohash@test.com", "buyer", wallet=buyer_wallet)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_nohash@test.com", "seller", wallet=seller_wallet)
    deal, deadline = await _create_deal(client, buyer_token, seller_token, agreement_hash=None)
    deal_id = deal["id"]
    fake_tx = "0x" + "6" * 64

    mock_tx = {
        "blockNumber": 100,
        "chainId": 31337,
        "to": contract_addr,
        "from": buyer_wallet,
        "value": int(1.5 * 10**18),
    }
    mock_receipt = {
        "blockNumber": 100,
        "status": 1,
        "from": buyer_wallet,
        "to": contract_addr,
    }
    with patch("app.services.escrow_service.get_web3") as mock_gw3, \
         patch("app.services.escrow_service.get_escrow_contract") as mock_gcontract:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        mock_contract = MagicMock()
        mock_contract.events.DealFunded.return_value.process_receipt.return_value = [{
            "address": contract_addr,
            "args": {
                "dealId": 999,
                "buyer": buyer_wallet,
                "seller": seller_wallet,
                "amount": int(1.5 * 10**18),
                "deadline": deadline,
                "agreementHash": bytes.fromhex("1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"),
            }
        }]
        mock_gcontract.return_value = mock_contract

        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 400
        assert "expected agreement hash" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_funding_duplicate_chain_deal_id_conflict(client: AsyncClient):
    """Ensure linking the same on-chain deal ID to multiple deals is rejected with 409 Conflict."""
    buyer_wallet = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    seller_wallet = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    contract_addr = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    ag_hash = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"

    buyer_token, _ = await _register_login_and_set_wallet(client, "b_dupchain@test.com", "buyer", wallet=buyer_wallet)
    seller_token, _ = await _register_login_and_set_wallet(client, "s_dupchain@test.com", "seller", wallet=seller_wallet)

    deal1, deadline1 = await _create_deal(client, buyer_token, seller_token, agreement_hash=ag_hash)
    deal2, deadline2 = await _create_deal(client, buyer_token, seller_token, agreement_hash=ag_hash)

    tx1 = "0x" + "3" * 64
    tx2 = "0x" + "4" * 64

    # Setup mock for first deal with chain_deal_id = 777
    with patch("app.services.escrow_service.get_web3") as mock_gw3, \
         patch("app.services.escrow_service.get_escrow_contract") as mock_gcontract:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = {
            "blockNumber": 100, "chainId": 31337, "to": contract_addr, "from": buyer_wallet, "value": int(1.5 * 10**18)
        }
        mock_w3.eth.get_transaction_receipt.return_value = {
            "blockNumber": 100, "status": 1, "from": buyer_wallet, "to": contract_addr
        }
        mock_gw3.return_value = mock_w3

        mock_contract = MagicMock()
        mock_contract.events.DealFunded.return_value.process_receipt.return_value = [{
            "address": contract_addr,
            "args": {
                "dealId": 777,
                "buyer": buyer_wallet,
                "seller": seller_wallet,
                "amount": int(1.5 * 10**18),
                "deadline": deadline1,
                "agreementHash": bytes.fromhex(ag_hash[2:]),
            }
        }]
        mock_contract.functions.getDeal.return_value.call.return_value = (
            buyer_wallet, seller_wallet, int(1.5 * 10**18), deadline1, bytes.fromhex(ag_hash[2:]), 0, False
        )
        mock_gcontract.return_value = mock_contract

        resp1 = await client.post(
            f"/api/v1/escrow/deals/{deal1['id']}/verify-funding",
            json={"tx_hash": tx1},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp1.status_code == 200

        # Now try to verify deal2 using different tx2 but returning the SAME chain_deal_id (777)
        resp2 = await client.post(
            f"/api/v1/escrow/deals/{deal2['id']}/verify-funding",
            json={"tx_hash": tx2},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp2.status_code == 409
        assert "already linked" in resp2.json()["detail"].lower()
