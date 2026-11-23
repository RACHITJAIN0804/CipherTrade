"""
End-to-End Escrow Testing Against Live Anvil Node (Chain ID: 31337).

Covers:
1. Complete happy path: Requirement -> Offer -> Accept -> Escrow Deal ->
   On-chain fundDeal -> Backend verify-funding -> Buyer/Seller Dashboards (FUNDED) ->
   Buyer confirmDelivery -> Backend sync (CONFIRMED) -> Seller withdrawPayment ->
   ETH balance verification -> Backend sync (RELEASED) -> Terminal Dashboards.
2. Voluntary seller refund (refundBuyer -> REFUNDED).
3. Expired deal cancellation (evm_increaseTime -> cancelExpired -> CANCELLED).
4. Mutual dispute & resolution (raiseDispute -> DISPUTED -> resolveDispute -> RESOLVED).
5. Unauthorized access & role-restricted contract execution guards.
6. Idempotent verification & repeated sync safety.
"""
from __future__ import annotations

import time
from decimal import Decimal
import pytest
from httpx import AsyncClient
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data

from app.core.blockchain import get_escrow_contract, get_web3
from app.models.models import EscrowState


# Standard deterministic Anvil local development accounts
ANVIL_ACCOUNTS = {
    "arbitrator": {
        "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "pk": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    },
    "buyer": {
        "address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "pk": "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    },
    "seller": {
        "address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "pk": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    },
    "stranger": {
        "address": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
        "pk": "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
    },
}


async def _register_user(client: AsyncClient, email: str, role: str, wallet: str) -> str:
    """Register user, verify wallet ownership via SIWE, and return JWT access token."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "display_name": f"User {email.split('@')[0]}",
            "primary_role": role,
            "password": "ValidPass1!",
        },
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "ValidPass1!"},
    )
    token = login_resp.json()["access_token"]

    pk = None
    for acc in ANVIL_ACCOUNTS.values():
        if acc["address"].lower() == wallet.lower():
            pk = acc["pk"]
            break

    if pk:
        chal_resp = await client.post(
            "/api/v1/auth/wallet/challenge",
            json={"wallet_address": wallet},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert chal_resp.status_code == 200, chal_resp.text
        msg = chal_resp.json()["message"]
        sig = Account.from_key(pk).sign_message(encode_defunct(text=msg)).signature.hex()
        ver_resp = await client.post(
            "/api/v1/auth/wallet/verify",
            json={"message": msg, "signature": sig},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert ver_resp.status_code == 200, ver_resp.text

    return token



def _send_on_chain_tx(w3: Web3, sender_addr: str, sender_pk: str, contract_call, value_wei: int = 0) -> str:
    """Build, sign, send on-chain transaction, wait for receipt, and return tx_hash."""
    tx_params = {
        "from": sender_addr,
        "value": value_wei,
        "nonce": w3.eth.get_transaction_count(sender_addr),
        "gas": 350000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 31337,
    }
    raw_tx = contract_call.build_transaction(tx_params)
    signed_tx = w3.eth.account.sign_transaction(raw_tx, private_key=sender_pk)
    tx_hash_bytes = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=10)
    assert receipt.status == 1, f"On-chain transaction reverted! Status: {receipt.status}"
    return w3.to_hex(tx_hash_bytes)


async def _sign_mutual_consent(
    client: AsyncClient,
    deal_id: str,
    b_token: str,
    buyer_info: dict,
    s_token: str,
    seller_info: dict,
):
    """Obtain challenge and sign mutual EIP-712 agreement consent for buyer and seller."""
    for token, info in [(b_token, buyer_info), (s_token, seller_info)]:
        ch_resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert ch_resp.status_code == 200, ch_resp.text
        ch_data = ch_resp.json()
        eip712_msg = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "AgreementConsent": ch_data["types"]["AgreementConsent"],
            },
            "primaryType": "AgreementConsent",
            "domain": ch_data["domain"],
            "message": ch_data["message"],
        }
        raw_sig = Account.from_key(info["pk"]).sign_message(encode_typed_data(full_message=eip712_msg)).signature.hex()
        sig = ("0x" + raw_sig) if not raw_sig.startswith("0x") else raw_sig
        ver_resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/consent/verify",
            json={"nonce": ch_data["nonce"], "signature": sig},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert ver_resp.status_code == 200, ver_resp.text


@pytest.mark.asyncio
async def test_e2e_complete_buyer_seller_happy_path(client: AsyncClient):
    """
    E2E Scenario 1: Complete Happy Path
    Requirement -> Offer -> Acceptance -> Escrow Deal ->
    On-chain fundDeal -> Backend verify-funding -> Buyer/Seller Dashboards (FUNDED) ->
    Buyer confirmDelivery -> Backend sync (CONFIRMED) -> Seller withdrawPayment ->
    Pull-payment balance verification -> Backend sync (RELEASED) -> Terminal Dashboards.
    """
    w3 = get_web3()
    assert w3.is_connected(), "Anvil node must be connected at http://127.0.0.1:8545"
    contract = get_escrow_contract(w3)

    buyer_info = ANVIL_ACCOUNTS["buyer"]
    seller_info = ANVIL_ACCOUNTS["seller"]

    # 1. Register buyer and seller with matching Anvil addresses
    b_token = await _register_user(client, "b_happy@test.com", "buyer", buyer_info["address"])
    s_token = await _register_user(client, "s_happy@test.com", "seller", seller_info["address"])

    # 2. Buyer creates requirement
    req_resp = await client.post(
        "/api/v1/requirements",
        json={
            "title": "Decentralized Escrow Marketplace Audit",
            "description": "Comprehensive review of CipherTrade smart contracts.",
            "category": "security",
        },
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert req_resp.status_code == 201
    req_id = req_resp.json()["id"]

    # 3. Seller creates offer
    deadline = w3.eth.get_block("latest").timestamp + 86400 * 3
    amount_eth = 1.0
    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": amount_eth,
            "delivery_deadline": deadline,
            "terms": "Full vulnerability audit report with proofs.",
        },
        headers={"Authorization": f"Bearer {s_token}"},
    )
    assert offer_resp.status_code == 201
    offer_id = offer_resp.json()["id"]

    # 4. Buyer accepts offer
    accept_resp = await client.post(
        f"/api/v1/offers/{offer_id}/accept",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert accept_resp.status_code == 200

    # 5. Buyer creates backend escrow deal
    deal_resp = await client.post(
        "/api/v1/escrow/deals",
        json={
            "offer_id": offer_id,
        },
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert deal_resp.status_code == 201
    deal_data = deal_resp.json()
    deal_id = deal_data["id"]
    ag_hash = deal_data["agreement_hash"]
    assert deal_data["on_chain_state"] is None

    # Sign mutual cryptographic consent
    await _sign_mutual_consent(client, deal_id, b_token, buyer_info, s_token, seller_info)

    # 6. Execute real on-chain fundDeal() via Web3 from Buyer account
    value_wei = w3.to_wei(amount_eth, "ether")
    fund_fn = contract.functions.fundDeal(
        seller_info["address"],
        deadline,
        bytes.fromhex(ag_hash[2:]),
    )
    tx_hash = _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], fund_fn, value_wei=value_wei)
    assert tx_hash.startswith("0x")

    # 7. Backend verify-funding
    verify_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert verify_resp.status_code == 200
    verified_deal = verify_resp.json()
    assert verified_deal["on_chain_state"] == "FUNDED"
    assert verified_deal["fund_tx_hash"] == tx_hash
    assert verified_deal["contract_address"].lower() == contract.address.lower()
    assert verified_deal["contract_version"] == "v1"
    chain_deal_id = verified_deal["chain_deal_id"]
    assert chain_deal_id is not None

    # 8. Check Buyer Dashboard
    b_dash = await client.get("/api/v1/escrow/deals/buyer", headers={"Authorization": f"Bearer {b_token}"})
    assert b_dash.status_code == 200
    b_items = b_dash.json()["items"]
    assert any(d["id"] == deal_id and d["on_chain_state"] == "FUNDED" for d in b_items)

    # 9. Check Seller Dashboard
    s_dash = await client.get("/api/v1/escrow/deals/seller", headers={"Authorization": f"Bearer {s_token}"})
    assert s_dash.status_code == 200
    s_items = s_dash.json()["items"]
    assert any(d["id"] == deal_id and d["on_chain_state"] == "FUNDED" for d in s_items)

    # 10. Buyer confirms delivery on-chain: confirmDelivery(chain_deal_id)
    confirm_fn = contract.functions.confirmDelivery(chain_deal_id)
    _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], confirm_fn)

    # 11. Backend sync updates state to CONFIRMED
    sync_resp1 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert sync_resp1.status_code == 200
    assert sync_resp1.json()["on_chain_state"] == "CONFIRMED"

    # 12. Check seller pull-payment: record seller balance before withdrawal
    seller_bal_before = w3.eth.get_balance(seller_info["address"])

    # 13. Seller executes withdrawPayment(chain_deal_id)
    withdraw_fn = contract.functions.withdrawPayment(chain_deal_id)
    tx_params = {
        "from": seller_info["address"],
        "nonce": w3.eth.get_transaction_count(seller_info["address"]),
        "gas": 350000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 31337,
    }
    raw_tx = withdraw_fn.build_transaction(tx_params)
    signed_tx = w3.eth.account.sign_transaction(raw_tx, private_key=seller_info["pk"])
    tx_bytes = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_bytes)
    assert receipt.status == 1
    gas_cost = receipt.gasUsed * tx_params["gasPrice"]

    # 14. Verify ETH balance: seller balance increased by (amount_eth - gas_cost)
    seller_bal_after = w3.eth.get_balance(seller_info["address"])
    assert seller_bal_after == seller_bal_before + value_wei - gas_cost

    # 15. Backend sync updates final state to RELEASED
    sync_resp2 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {s_token}"},
    )
    assert sync_resp2.status_code == 200
    assert sync_resp2.json()["on_chain_state"] == "RELEASED"

    # 16. Verify terminal state in both dashboards
    b_final = (await client.get("/api/v1/escrow/deals/buyer", headers={"Authorization": f"Bearer {b_token}"})).json()
    assert any(d["id"] == deal_id and d["on_chain_state"] == "RELEASED" for d in b_final["items"])

    s_final = (await client.get("/api/v1/escrow/deals/seller", headers={"Authorization": f"Bearer {s_token}"})).json()
    assert any(d["id"] == deal_id and d["on_chain_state"] == "RELEASED" for d in s_final["items"])


@pytest.mark.asyncio
async def test_e2e_voluntary_seller_refund(client: AsyncClient):
    """
    E2E Scenario 2: Voluntary Seller Refund
    Funded Deal -> Seller calls refundBuyer() on-chain ->
    Buyer balance increases by refund amount -> Backend sync updates to REFUNDED.
    """
    w3 = get_web3()
    contract = get_escrow_contract(w3)
    buyer_info = ANVIL_ACCOUNTS["buyer"]
    seller_info = ANVIL_ACCOUNTS["seller"]

    b_token = await _register_user(client, "b_refund@test.com", "buyer", buyer_info["address"])
    s_token = await _register_user(client, "s_refund@test.com", "seller", seller_info["address"])

    # Create requirement, offer, and deal
    req_resp = await client.post(
        "/api/v1/requirements",
        json={"title": "Custom UI Widget", "description": "Need responsive component.", "category": "frontend"},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    req_id = req_resp.json()["id"]

    amount_eth = 0.5
    deadline = w3.eth.get_block("latest").timestamp + 86400 * 2
    offer_resp = await client.post(
        "/api/v1/offers",
        json={"requirement_id": req_id, "price_eth": amount_eth, "delivery_deadline": deadline, "terms": "React widget"},
        headers={"Authorization": f"Bearer {s_token}"},
    )
    offer_id = offer_resp.json()["id"]
    await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {b_token}"})

    ag_hash = "0x" + "22" * 32
    deal_resp = await client.post(
        "/api/v1/escrow/deals",
        json={"offer_id": offer_id},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert deal_resp.status_code == 201
    deal_data = deal_resp.json()
    deal_id = deal_data["id"]
    ag_hash = deal_data["agreement_hash"]

    # Sign mutual cryptographic consent
    await _sign_mutual_consent(client, deal_id, b_token, buyer_info, s_token, seller_info)

    # Fund deal on-chain
    value_wei = w3.to_wei(amount_eth, "ether")
    fund_fn = contract.functions.fundDeal(seller_info["address"], deadline, bytes.fromhex(ag_hash[2:]))
    tx_hash = _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], fund_fn, value_wei=value_wei)

    # Verify funding
    v_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    chain_deal_id = v_resp.json()["chain_deal_id"]

    # Record buyer balance before refund
    buyer_bal_before = w3.eth.get_balance(buyer_info["address"])

    # Seller calls refundBuyer(chain_deal_id)
    refund_fn = contract.functions.refundBuyer(chain_deal_id)
    _send_on_chain_tx(w3, seller_info["address"], seller_info["pk"], refund_fn)

    # Verify buyer balance increased by refunded amount
    buyer_bal_after = w3.eth.get_balance(buyer_info["address"])
    assert buyer_bal_after == buyer_bal_before + value_wei

    # Sync backend state
    sync_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert sync_resp.status_code == 200
    assert sync_resp.json()["on_chain_state"] == "REFUNDED"


@pytest.mark.asyncio
async def test_e2e_buyer_cancel_expired_deal(client: AsyncClient):
    """
    E2E Scenario 3: Cancel Expired Deal
    Deal deadline reached -> evm_increaseTime -> Buyer calls cancelExpired() on-chain ->
    Buyer receives refund -> Backend sync updates to CANCELLED.
    """
    w3 = get_web3()
    contract = get_escrow_contract(w3)
    buyer_info = ANVIL_ACCOUNTS["buyer"]
    seller_info = ANVIL_ACCOUNTS["seller"]

    b_token = await _register_user(client, "b_cancel@test.com", "buyer", buyer_info["address"])
    s_token = await _register_user(client, "s_cancel@test.com", "seller", seller_info["address"])

    # Create requirement, offer, and deal with short 10s deadline
    req_resp = await client.post(
        "/api/v1/requirements",
        json={"title": "Quick script", "description": "Short deadline task.", "category": "scripting"},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    req_id = req_resp.json()["id"]

    amount_eth = 0.25
    deadline = w3.eth.get_block("latest").timestamp + 30  # 30 seconds deadline from latest block
    offer_resp = await client.post(
        "/api/v1/offers",
        json={"requirement_id": req_id, "price_eth": amount_eth, "delivery_deadline": deadline, "terms": "Quick python script"},
        headers={"Authorization": f"Bearer {s_token}"},
    )
    offer_id = offer_resp.json()["id"]
    await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {b_token}"})

    ag_hash = "0x" + "33" * 32
    deal_resp = await client.post(
        "/api/v1/escrow/deals",
        json={"offer_id": offer_id},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert deal_resp.status_code == 201
    deal_data = deal_resp.json()
    deal_id = deal_data["id"]
    ag_hash = deal_data["agreement_hash"]

    # Sign mutual cryptographic consent
    await _sign_mutual_consent(client, deal_id, b_token, buyer_info, s_token, seller_info)

    # Fund on-chain
    value_wei = w3.to_wei(amount_eth, "ether")
    fund_fn = contract.functions.fundDeal(seller_info["address"], deadline, bytes.fromhex(ag_hash[2:]))
    tx_hash = _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], fund_fn, value_wei=value_wei)

    v_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    chain_deal_id = v_resp.json()["chain_deal_id"]

    # Advance Anvil block timestamp by 60 seconds to pass deadline
    w3.provider.make_request("evm_increaseTime", [60])
    w3.provider.make_request("evm_mine", [])

    # Record buyer balance before cancellation
    buyer_bal_before = w3.eth.get_balance(buyer_info["address"])

    # Buyer calls cancelExpired(chain_deal_id)
    cancel_fn = contract.functions.cancelExpired(chain_deal_id)
    tx_params = {
        "from": buyer_info["address"],
        "nonce": w3.eth.get_transaction_count(buyer_info["address"]),
        "gas": 350000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 31337,
    }
    raw_tx = cancel_fn.build_transaction(tx_params)
    signed_tx = w3.eth.account.sign_transaction(raw_tx, private_key=buyer_info["pk"])
    tx_bytes = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_bytes)
    assert receipt.status == 1
    gas_cost = receipt.gasUsed * tx_params["gasPrice"]

    # Verify buyer received ETH refund (minus gas)
    buyer_bal_after = w3.eth.get_balance(buyer_info["address"])
    assert buyer_bal_after == buyer_bal_before + value_wei - gas_cost

    # Backend sync updates to CANCELLED
    sync_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert sync_resp.status_code == 200
    assert sync_resp.json()["on_chain_state"] == "CANCELLED"


@pytest.mark.asyncio
async def test_e2e_dispute_and_resolution(client: AsyncClient):
    """
    E2E Scenario 4: Dispute & Arbitration Resolution
    Funded deal -> Seller raises dispute on-chain -> Backend sync updates to DISPUTED ->
    Arbitrator resolves dispute to seller -> Backend sync updates to RESOLVED.
    """
    w3 = get_web3()
    contract = get_escrow_contract(w3)
    buyer_info = ANVIL_ACCOUNTS["buyer"]
    seller_info = ANVIL_ACCOUNTS["seller"]
    arbitrator_info = ANVIL_ACCOUNTS["arbitrator"]

    b_token = await _register_user(client, "b_disp@test.com", "buyer", buyer_info["address"])
    s_token = await _register_user(client, "s_disp@test.com", "seller", seller_info["address"])

    req_resp = await client.post(
        "/api/v1/requirements",
        json={"title": "Disputed Delivery", "description": "Deliverable with issues.", "category": "consulting"},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    req_id = req_resp.json()["id"]

    amount_eth = 0.3
    deadline = w3.eth.get_block("latest").timestamp + 86400 * 3
    offer_resp = await client.post(
        "/api/v1/offers",
        json={"requirement_id": req_id, "price_eth": amount_eth, "delivery_deadline": deadline, "terms": "Consulting advisory"},
        headers={"Authorization": f"Bearer {s_token}"},
    )
    offer_id = offer_resp.json()["id"]
    await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {b_token}"})

    ag_hash = "0x" + "44" * 32
    deal_resp = await client.post(
        "/api/v1/escrow/deals",
        json={"offer_id": offer_id},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert deal_resp.status_code == 201
    deal_data = deal_resp.json()
    deal_id = deal_data["id"]
    ag_hash = deal_data["agreement_hash"]

    # Sign mutual cryptographic consent
    await _sign_mutual_consent(client, deal_id, b_token, buyer_info, s_token, seller_info)

    value_wei = w3.to_wei(amount_eth, "ether")
    fund_fn = contract.functions.fundDeal(seller_info["address"], deadline, bytes.fromhex(ag_hash[2:]))
    tx_hash = _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], fund_fn, value_wei=value_wei)

    v_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    chain_deal_id = v_resp.json()["chain_deal_id"]

    # 1. Seller raises dispute on-chain
    dispute_fn = contract.functions.raiseDispute(chain_deal_id)
    _send_on_chain_tx(w3, seller_info["address"], seller_info["pk"], dispute_fn)

    # 2. Backend sync updates to DISPUTED
    sync_resp1 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert sync_resp1.status_code == 200
    assert sync_resp1.json()["on_chain_state"] == "DISPUTED"

    # 3. Arbitrator resolves dispute in favor of seller: resolveDispute(chain_deal_id, toSeller=True)
    seller_bal_before = w3.eth.get_balance(seller_info["address"])
    resolve_fn = contract.functions.resolveDispute(chain_deal_id, True)
    _send_on_chain_tx(w3, arbitrator_info["address"], arbitrator_info["pk"], resolve_fn)

    # 4. Verify seller received disputed funds
    seller_bal_after = w3.eth.get_balance(seller_info["address"])
    assert seller_bal_after == seller_bal_before + value_wei

    # 5. Backend sync updates to RESOLVED
    sync_resp2 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert sync_resp2.status_code == 200
    assert sync_resp2.json()["on_chain_state"] == "RESOLVED"


@pytest.mark.asyncio
async def test_e2e_security_role_rejection_and_isolation(client: AsyncClient):
    """
    E2E Scenario 5: Security Rejections & Isolation
    - Third party cannot call confirmDelivery or withdrawPayment on-chain (reverts Unauthorized).
    - Third party cannot view deal detail or call sync on backend (403 Forbidden).
    - Idempotent verify-funding (submitting verified tx_hash returns 200 OK without double-crediting).
    - Repeated sync does not mutate or corrupt deal state.
    """
    w3 = get_web3()
    contract = get_escrow_contract(w3)
    buyer_info = ANVIL_ACCOUNTS["buyer"]
    seller_info = ANVIL_ACCOUNTS["seller"]
    stranger_info = ANVIL_ACCOUNTS["stranger"]

    b_token = await _register_user(client, "b_sec@test.com", "buyer", buyer_info["address"])
    s_token = await _register_user(client, "s_sec@test.com", "seller", seller_info["address"])
    stranger_token = await _register_user(client, "stranger_sec@test.com", "buyer", stranger_info["address"])

    req_resp = await client.post(
        "/api/v1/requirements",
        json={"title": "Security test item", "description": "Testing authorization.", "category": "security"},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    req_id = req_resp.json()["id"]

    amount_eth = 0.1
    deadline = w3.eth.get_block("latest").timestamp + 86400 * 2
    offer_resp = await client.post(
        "/api/v1/offers",
        json={"requirement_id": req_id, "price_eth": amount_eth, "delivery_deadline": deadline, "terms": "Security audit terms and conditions."},
        headers={"Authorization": f"Bearer {s_token}"},
    )
    offer_id = offer_resp.json()["id"]
    await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {b_token}"})

    ag_hash = "0x" + "55" * 32
    deal_resp = await client.post(
        "/api/v1/escrow/deals",
        json={"offer_id": offer_id},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert deal_resp.status_code == 201
    deal_data = deal_resp.json()
    deal_id = deal_data["id"]
    ag_hash = deal_data["agreement_hash"]

    # Sign mutual cryptographic consent
    await _sign_mutual_consent(client, deal_id, b_token, buyer_info, s_token, seller_info)

    # Fund on-chain
    value_wei = w3.to_wei(amount_eth, "ether")
    fund_fn = contract.functions.fundDeal(seller_info["address"], deadline, bytes.fromhex(ag_hash[2:]))
    tx_hash = _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], fund_fn, value_wei=value_wei)

    v_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    chain_deal_id = v_resp.json()["chain_deal_id"]

    # 1. On-Chain Security: Stranger cannot confirm delivery (reverts)
    with pytest.raises(Exception):
        confirm_fn = contract.functions.confirmDelivery(chain_deal_id)
        _send_on_chain_tx(w3, stranger_info["address"], stranger_info["pk"], confirm_fn)

    # 2. On-Chain Security: Stranger cannot withdraw payment (reverts)
    with pytest.raises(Exception):
        withdraw_fn = contract.functions.withdrawPayment(chain_deal_id)
        _send_on_chain_tx(w3, stranger_info["address"], stranger_info["pk"], withdraw_fn)

    # 3. Backend Security: Stranger cannot access deal details
    resp_detail = await client.get(
        f"/api/v1/escrow/{deal_id}",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert resp_detail.status_code == 403

    # 4. Backend Security: Stranger cannot sync deal
    resp_sync = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert resp_sync.status_code == 403

    # 5. Verification Idempotency: Repeating verify-funding with same tx_hash returns 200 OK
    repeat_verify = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert repeat_verify.status_code == 200
    assert repeat_verify.json()["on_chain_state"] == "FUNDED"

    # 6. Sync Idempotency: Repeated sync calls return identical state
    for _ in range(3):
        sync_repeat = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/sync",
            headers={"Authorization": f"Bearer {b_token}"},
        )
        assert sync_repeat.status_code == 200
        assert sync_repeat.json()["on_chain_state"] == "FUNDED"
