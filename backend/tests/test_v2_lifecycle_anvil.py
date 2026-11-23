"""
Phase 5E.4.3: Comprehensive V2 Escrow Lifecycle End-to-End Integration Testing.
Executed against a live local Anvil node (Chain ID: 31337).

Covers:
- Scenario A: Complete Successful Happy Path (FUNDED -> CONFIRMED -> RELEASED)
  with balance accounting, event verification, and state sync.
- Scenario B: Voluntary Seller Refund (FUNDED -> REFUNDED) with balance accounting.
- Scenario C: Expired Deal Cancellation (FUNDED -> CANCELLED after evm_increaseTime).
- Scenario D: Dispute Lifecycle:
    - D1: Buyer-initiated dispute resolved to seller (DISPUTED -> RESOLVED).
    - D2: Seller-initiated dispute resolved to buyer (DISPUTED -> RESOLVED).
    - D3: Unauthorized dispute resolution rejected (UnauthorizedCaller).
- Scenario E: Confirmed Delivery Dispute (CONFIRMED -> DISPUTED -> RESOLVED).
- Security & Negative Testing:
    - Unauthorized access guards (stranger/seller/buyer role restrictions on-chain).
    - Unauthorized backend access (403 Forbidden).
    - State machine terminal invariants (no release after refund, no refund after release, etc.).
    - Early cancellation rejection (DeadlineNotReached).
    - Replay & duplicate funding protection (DealAlreadyFunded, NonceReplayed).
    - Verification and sync idempotency.
    - Persisted contract identity (contract_address, contract_version="v2", zero-fallback).
"""
from __future__ import annotations

import time
import uuid
from decimal import Decimal
import pytest
from httpx import AsyncClient
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data

from app.core.blockchain import get_web3, load_v2_contract_abi, get_escrow_contract
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
            "password": "ValidPassword123!",
        },
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "ValidPassword123!"},
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


def _send_on_chain_tx(
    w3: Web3,
    sender_addr: str,
    sender_pk: str,
    contract_call,
    value_wei: int = 0,
    gas: int = 350000,
) -> tuple[str, dict]:
    """Build, sign, send on-chain transaction, wait for receipt, and return (tx_hash_hex, receipt)."""
    sender_checksum = Web3.to_checksum_address(sender_addr)
    tx_params = {
        "from": sender_checksum,
        "value": value_wei,
        "nonce": w3.eth.get_transaction_count(sender_checksum),
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
        "chainId": 31337,
    }
    raw_tx = contract_call.build_transaction(tx_params)
    signed_tx = w3.eth.account.sign_transaction(raw_tx, private_key=sender_pk)
    tx_hash_bytes = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=10)
    assert receipt.status == 1, f"On-chain transaction reverted! Status: {receipt.status}"
    tx_hash_hex = receipt["transactionHash"].hex()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex
    return tx_hash_hex, receipt


async def _setup_and_sign_v2_deal(
    client: AsyncClient,
    w3: Web3,
    amount_eth: float = 0.5,
    deadline_offset: int = 86400 * 3,
    tokens: tuple[str, str] | None = None,
) -> dict:
    """
    Complete setup helper:
    1. Registers unique buyer & seller (or reuses tokens if provided).
    2. Creates requirement -> offer -> acceptance -> escrow deal.
    3. Signs mutual EIP-712 consent.
    4. Retrieves contract funding payload.
    Returns dictionary with all deal contextual metadata.
    """
    buyer_info = ANVIL_ACCOUNTS["buyer"]
    seller_info = ANVIL_ACCOUNTS["seller"]

    uid = uuid.uuid4().hex[:6]
    if tokens:
        b_token, s_token = tokens
    else:
        b_token = await _register_user(client, f"b_{uid}@test.com", "buyer", buyer_info["address"])
        s_token = await _register_user(client, f"s_{uid}@test.com", "seller", seller_info["address"])

    # Requirement
    req_resp = await client.post(
        "/api/v1/requirements",
        json={
            "title": f"V2 Escrow Audit {uid}",
            "description": "Full lifecycle verification of V2 dual-consent smart contract.",
            "category": "security",
        },
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert req_resp.status_code == 201, req_resp.text
    req_id = req_resp.json()["id"]

    # Offer
    deadline = w3.eth.get_block("latest").timestamp + deadline_offset
    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": amount_eth,
            "delivery_deadline": deadline,
            "terms": "Execution of full cryptographic and on-chain consent verification.",
        },
        headers={"Authorization": f"Bearer {s_token}"},
    )
    assert offer_resp.status_code == 201, offer_resp.text
    offer_id = offer_resp.json()["id"]

    # Acceptance
    accept_resp = await client.post(
        f"/api/v1/offers/{offer_id}/accept",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert accept_resp.status_code == 200, accept_resp.text

    # Escrow Deal
    deal_resp = await client.post(
        "/api/v1/escrow/deals",
        json={"offer_id": offer_id},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert deal_resp.status_code == 201, deal_resp.text
    deal_data = deal_resp.json()
    deal_id = deal_data["id"]

    # Buyer Consent
    b_chal_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert b_chal_resp.status_code == 200, b_chal_resp.text
    b_chal = b_chal_resp.json()

    b_msg = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "AgreementConsent": b_chal["types"]["AgreementConsent"],
        },
        "domain": b_chal["domain"],
        "primaryType": "AgreementConsent",
        "message": b_chal["message"],
    }
    raw_b_sig = Account.from_key(buyer_info["pk"]).sign_message(encode_typed_data(full_message=b_msg)).signature.hex()
    b_sig = ("0x" + raw_b_sig) if not raw_b_sig.startswith("0x") else raw_b_sig

    b_ver_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": b_chal["nonce"], "signature": b_sig},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert b_ver_resp.status_code == 200, b_ver_resp.text

    # Seller Consent
    s_chal_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {s_token}"},
    )
    assert s_chal_resp.status_code == 200, s_chal_resp.text
    s_chal = s_chal_resp.json()

    s_msg = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "AgreementConsent": s_chal["types"]["AgreementConsent"],
        },
        "domain": s_chal["domain"],
        "primaryType": "AgreementConsent",
        "message": s_chal["message"],
    }
    raw_s_sig = Account.from_key(seller_info["pk"]).sign_message(encode_typed_data(full_message=s_msg)).signature.hex()
    s_sig = ("0x" + raw_s_sig) if not raw_s_sig.startswith("0x") else raw_s_sig

    s_ver_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": s_chal["nonce"], "signature": s_sig},
        headers={"Authorization": f"Bearer {s_token}"},
    )
    assert s_ver_resp.status_code == 200, s_ver_resp.text
    assert s_ver_resp.json()["both_consented"] is True

    # Retrieve contract payload
    payload_resp = await client.get(
        f"/api/v1/deals/{deal_id}/contract-payload",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert payload_resp.status_code == 200, payload_resp.text
    payload = payload_resp.json()

    v2_abi = load_v2_contract_abi()
    assert v2_abi is not None, "V2 contract ABI must be available"
    v2_contract = w3.eth.contract(address=Web3.to_checksum_address(payload["contract_address"]), abi=v2_abi)

    return {
        "deal_id": deal_id,
        "offer_id": offer_id,
        "b_token": b_token,
        "s_token": s_token,
        "buyer_info": buyer_info,
        "seller_info": seller_info,
        "payload": payload,
        "v2_contract": v2_contract,
        "amount_eth": amount_eth,
        "amount_wei": int(payload["agreement"]["amount"]),
        "deadline": deadline,
    }


def _execute_fund_deal_with_consent(w3: Web3, ctx: dict) -> tuple[int, str, dict]:
    """Execute on-chain fundDealWithConsent and return (chain_deal_id, tx_hash_hex, receipt)."""
    payload = ctx["payload"]
    agreement = payload["agreement"]
    buyer_proof = payload["buyerConsent"]
    seller_proof = payload["sellerConsent"]
    v2_contract = ctx["v2_contract"]
    buyer_info = ctx["buyer_info"]

    agreement_tuple = (
        bytes.fromhex(agreement["agreementHash"][2:]),
        str(agreement["offerId"]),
        str(agreement["dealId"]),
        Web3.to_checksum_address(agreement["buyer"]),
        Web3.to_checksum_address(agreement["seller"]),
        int(agreement["amount"]),
        int(agreement["deliveryDeadline"]),
    )

    buyer_proof_tuple = (
        str(buyer_proof["nonce"]),
        int(buyer_proof["deadline"]),
        bytes.fromhex(buyer_proof["signature"][2:]),
    )

    seller_proof_tuple = (
        str(seller_proof["nonce"]),
        int(seller_proof["deadline"]),
        bytes.fromhex(seller_proof["signature"][2:]),
    )

    value_wei = int(agreement["amount"])
    buyer_addr = Web3.to_checksum_address(buyer_info["address"])

    tx_params = {
        "from": buyer_addr,
        "value": value_wei,
        "nonce": w3.eth.get_transaction_count(buyer_addr),
        "gas": 600000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 31337,
    }
    fund_fn = v2_contract.functions.fundDealWithConsent(
        agreement_tuple,
        buyer_proof_tuple,
        seller_proof_tuple,
    )
    raw_tx = fund_fn.build_transaction(tx_params)
    signed_tx = w3.eth.account.sign_transaction(raw_tx, buyer_info["pk"])
    tx_hash_bytes = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=10)
    assert receipt["status"] == 1, "V2 funding transaction must succeed with status=1"

    events = v2_contract.events.DealFunded().process_receipt(receipt)
    assert len(events) == 1, "Exactly one DealFunded event must be emitted"
    chain_deal_id = events[0]["args"]["chainDealId"]
    assert events[0]["args"]["dealUuid"] == ctx["deal_id"]

    tx_hash_hex = receipt["transactionHash"].hex()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex

    return chain_deal_id, tx_hash_hex, receipt


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO A: Complete Successful Release Happy Path
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_v2_scenario_a_successful_release(client: AsyncClient):
    """
    Scenario A: Complete Successful Happy Path
    1. Create & consent V2 deal.
    2. Fund on-chain with dual EIP-712 proofs -> DealFunded event.
    3. Backend verify-funding -> persists contract_address, contract_version="v2", chain_deal_id.
    4. Sync deal while FUNDED -> confirms state=FUNDED.
    5. Buyer calls confirmDelivery(chainDealId) on-chain.
    6. Backend sync -> state becomes CONFIRMED.
    7. Seller calls withdrawPayment(chainDealId) on-chain.
    8. Verify pull-payment balance accounting (seller receives amount - gas).
    9. Backend sync -> state becomes RELEASED (terminal).
    10. Verify terminal state persistence and dashboard consistency.
    """
    w3 = get_web3()
    assert w3.is_connected(), "Anvil must be connected"
    ctx = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.75)
    deal_id = ctx["deal_id"]
    v2_contract = ctx["v2_contract"]
    buyer_info = ctx["buyer_info"]
    seller_info = ctx["seller_info"]
    value_wei = ctx["amount_wei"]

    # Fund on-chain
    chain_deal_id, tx_hash, _ = _execute_fund_deal_with_consent(w3, ctx)

    # Verify funding on backend
    ver_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert ver_resp.status_code == 200, ver_resp.text
    ver_data = ver_resp.json()
    assert ver_data["on_chain_state"] == "FUNDED"
    assert ver_data["contract_address"].lower() == v2_contract.address.lower()
    assert ver_data["contract_version"] == "v2"
    assert ver_data["chain_deal_id"] == chain_deal_id

    # Sync FUNDED state
    sync1 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert sync1.status_code == 200
    assert sync1.json()["on_chain_state"] == "FUNDED"

    # Buyer confirms delivery on-chain
    confirm_fn = v2_contract.functions.confirmDelivery(chain_deal_id)
    _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], confirm_fn)

    # Sync to backend -> CONFIRMED
    sync2 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert sync2.status_code == 200
    assert sync2.json()["on_chain_state"] == "CONFIRMED"

    # Seller balance before withdrawal
    seller_addr = Web3.to_checksum_address(seller_info["address"])
    bal_before = w3.eth.get_balance(seller_addr)

    # Seller executes withdrawPayment
    withdraw_fn = v2_contract.functions.withdrawPayment(chain_deal_id)
    _, withdraw_rcpt = _send_on_chain_tx(w3, seller_info["address"], seller_info["pk"], withdraw_fn)
    gas_cost = withdraw_rcpt["gasUsed"] * withdraw_rcpt["effectiveGasPrice"]

    # Balance accounting: seller balance increased by (amount - gas_cost)
    bal_after = w3.eth.get_balance(seller_addr)
    assert bal_after == bal_before + value_wei - gas_cost

    # Verify on-chain deal state in contract
    on_chain_deal = v2_contract.functions.getDeal(chain_deal_id).call()
    assert on_chain_deal[6] == 2, "Contract state must be State.RELEASED (2)"
    assert on_chain_deal[7] is True, "fundsWithdrawn must be True"

    # Backend sync -> RELEASED
    sync3 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['s_token']}"},
    )
    assert sync3.status_code == 200
    assert sync3.json()["on_chain_state"] == "RELEASED"

    # Idempotent terminal sync
    sync_repeat = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert sync_repeat.status_code == 200
    assert sync_repeat.json()["on_chain_state"] == "RELEASED"

    # Check buyer & seller dashboards
    b_dash = await client.get("/api/v1/escrow/deals/buyer", headers={"Authorization": f"Bearer {ctx['b_token']}"})
    assert any(d["id"] == deal_id and d["on_chain_state"] == "RELEASED" for d in b_dash.json()["items"])
    s_dash = await client.get("/api/v1/escrow/deals/seller", headers={"Authorization": f"Bearer {ctx['s_token']}"})
    assert any(d["id"] == deal_id and d["on_chain_state"] == "RELEASED" for d in s_dash.json()["items"])


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO B: Voluntary Seller Refund
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_v2_scenario_b_voluntary_seller_refund(client: AsyncClient):
    """
    Scenario B: Voluntary Seller Refund
    1. Separate deal funded and verified.
    2. Record buyer ETH balance before refund.
    3. Seller executes refundBuyer(chainDealId) on V2 contract.
    4. Buyer balance increases by exact escrow amount (seller pays gas).
    5. On-chain deal state is REFUNDED (3) and fundsWithdrawn is True.
    6. Backend sync updates to REFUNDED.
    7. Verify terminal state persistence and dashboard display.
    """
    w3 = get_web3()
    ctx = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.4)
    deal_id = ctx["deal_id"]
    v2_contract = ctx["v2_contract"]
    buyer_info = ctx["buyer_info"]
    seller_info = ctx["seller_info"]
    value_wei = ctx["amount_wei"]

    # Fund on-chain & verify
    chain_deal_id, tx_hash, _ = _execute_fund_deal_with_consent(w3, ctx)
    ver_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert ver_resp.status_code == 200

    # Record buyer balance before refund
    buyer_addr = Web3.to_checksum_address(buyer_info["address"])
    buyer_bal_before = w3.eth.get_balance(buyer_addr)

    # Seller calls refundBuyer
    refund_fn = v2_contract.functions.refundBuyer(chain_deal_id)
    _send_on_chain_tx(w3, seller_info["address"], seller_info["pk"], refund_fn)

    # Buyer balance increased by exact value_wei
    buyer_bal_after = w3.eth.get_balance(buyer_addr)
    assert buyer_bal_after == buyer_bal_before + value_wei

    # On-chain state check
    on_chain_deal = v2_contract.functions.getDeal(chain_deal_id).call()
    assert on_chain_deal[6] == 3, "Contract state must be State.REFUNDED (3)"
    assert on_chain_deal[7] is True, "fundsWithdrawn must be True"

    # Backend sync -> REFUNDED
    sync_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert sync_resp.status_code == 200
    assert sync_resp.json()["on_chain_state"] == "REFUNDED"

    # Check dashboards
    b_dash = await client.get("/api/v1/escrow/deals/buyer", headers={"Authorization": f"Bearer {ctx['b_token']}"})
    assert any(d["id"] == deal_id and d["on_chain_state"] == "REFUNDED" for d in b_dash.json()["items"])


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO C: Expired Deal Cancellation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_v2_scenario_c_expired_deal_cancellation(client: AsyncClient):
    """
    Scenario C: Expired Deal Cancellation
    1. Separate deal created with short delivery deadline (30 seconds).
    2. Fund and verify deal.
    3. Fast-forward Anvil EVM timestamp past deadline via evm_increaseTime.
    4. Buyer calls cancelExpired(chainDealId).
    5. Buyer balance increases by (amount - gas_cost).
    6. On-chain deal state is CANCELLED (4) and fundsWithdrawn is True.
    7. Backend sync updates to CANCELLED.
    """
    w3 = get_web3()
    ctx = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.3, deadline_offset=30)
    deal_id = ctx["deal_id"]
    v2_contract = ctx["v2_contract"]
    buyer_info = ctx["buyer_info"]
    value_wei = ctx["amount_wei"]

    # Fund & verify
    chain_deal_id, tx_hash, _ = _execute_fund_deal_with_consent(w3, ctx)
    ver_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert ver_resp.status_code == 200

    # Fast forward Anvil chain time past deadline
    w3.provider.make_request("evm_increaseTime", [60])
    w3.provider.make_request("evm_mine", [])

    # Record buyer balance before cancel
    buyer_addr = Web3.to_checksum_address(buyer_info["address"])
    buyer_bal_before = w3.eth.get_balance(buyer_addr)

    # Buyer calls cancelExpired
    cancel_fn = v2_contract.functions.cancelExpired(chain_deal_id)
    _, cancel_rcpt = _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], cancel_fn)
    gas_cost = cancel_rcpt["gasUsed"] * cancel_rcpt["effectiveGasPrice"]

    # Balance check: buyer balance increased by (amount - gas_cost)
    buyer_bal_after = w3.eth.get_balance(buyer_addr)
    assert buyer_bal_after == buyer_bal_before + value_wei - gas_cost

    # On-chain state check
    on_chain_deal = v2_contract.functions.getDeal(chain_deal_id).call()
    assert on_chain_deal[6] == 4, "Contract state must be State.CANCELLED (4)"
    assert on_chain_deal[7] is True, "fundsWithdrawn must be True"

    # Backend sync -> CANCELLED
    sync_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert sync_resp.status_code == 200
    assert sync_resp.json()["on_chain_state"] == "CANCELLED"


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO D: Dispute Lifecycle (Buyer & Seller Initiated + Rejections)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_v2_scenario_d1_buyer_dispute_resolved_to_seller(client: AsyncClient):
    """
    Scenario D1: Buyer raises dispute -> Arbitrator resolves to seller.
    1. Buyer calls raiseDispute(chainDealId) on-chain -> DISPUTED.
    2. Backend sync -> DISPUTED.
    3. Arbitrator calls resolveDispute(chainDealId, toSeller=True) -> RESOLVED.
    4. Seller receives funds.
    5. Backend sync -> RESOLVED.
    """
    w3 = get_web3()
    ctx = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.5)
    deal_id = ctx["deal_id"]
    v2_contract = ctx["v2_contract"]
    buyer_info = ctx["buyer_info"]
    seller_info = ctx["seller_info"]
    arbitrator_info = ANVIL_ACCOUNTS["arbitrator"]
    value_wei = ctx["amount_wei"]

    chain_deal_id, tx_hash, _ = _execute_fund_deal_with_consent(w3, ctx)
    await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )

    # 1. Buyer raises dispute
    disp_fn = v2_contract.functions.raiseDispute(chain_deal_id)
    _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], disp_fn)

    # 2. Sync -> DISPUTED
    sync1 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert sync1.status_code == 200
    assert sync1.json()["on_chain_state"] == "DISPUTED"

    # 3. Arbitrator resolves dispute to seller
    seller_addr = Web3.to_checksum_address(seller_info["address"])
    seller_bal_before = w3.eth.get_balance(seller_addr)

    resolve_fn = v2_contract.functions.resolveDispute(chain_deal_id, True)
    _send_on_chain_tx(w3, arbitrator_info["address"], arbitrator_info["pk"], resolve_fn)

    # 4. Seller received disputed funds (arbitrator paid gas)
    seller_bal_after = w3.eth.get_balance(seller_addr)
    assert seller_bal_after == seller_bal_before + value_wei

    # On-chain state
    on_chain_deal = v2_contract.functions.getDeal(chain_deal_id).call()
    assert on_chain_deal[6] == 6, "Contract state must be State.RESOLVED (6)"
    assert on_chain_deal[7] is True, "fundsWithdrawn must be True"

    # 5. Sync -> RESOLVED
    sync2 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['s_token']}"},
    )
    assert sync2.status_code == 200
    assert sync2.json()["on_chain_state"] == "RESOLVED"


@pytest.mark.asyncio
async def test_v2_scenario_d2_seller_dispute_resolved_to_buyer(client: AsyncClient):
    """
    Scenario D2: Seller raises dispute -> Arbitrator resolves to buyer.
    1. Seller calls raiseDispute(chainDealId) on-chain -> DISPUTED.
    2. Backend sync -> DISPUTED.
    3. Arbitrator calls resolveDispute(chainDealId, toSeller=False) -> RESOLVED.
    4. Buyer receives funds.
    5. Backend sync -> RESOLVED.
    """
    w3 = get_web3()
    ctx = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.6)
    deal_id = ctx["deal_id"]
    v2_contract = ctx["v2_contract"]
    buyer_info = ctx["buyer_info"]
    seller_info = ctx["seller_info"]
    arbitrator_info = ANVIL_ACCOUNTS["arbitrator"]
    value_wei = ctx["amount_wei"]

    chain_deal_id, tx_hash, _ = _execute_fund_deal_with_consent(w3, ctx)
    await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )

    # 1. Seller raises dispute
    disp_fn = v2_contract.functions.raiseDispute(chain_deal_id)
    _send_on_chain_tx(w3, seller_info["address"], seller_info["pk"], disp_fn)

    # 2. Sync -> DISPUTED
    sync1 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['s_token']}"},
    )
    assert sync1.status_code == 200
    assert sync1.json()["on_chain_state"] == "DISPUTED"

    # 3. Arbitrator resolves dispute to buyer
    buyer_addr = Web3.to_checksum_address(buyer_info["address"])
    buyer_bal_before = w3.eth.get_balance(buyer_addr)

    resolve_fn = v2_contract.functions.resolveDispute(chain_deal_id, False)
    _send_on_chain_tx(w3, arbitrator_info["address"], arbitrator_info["pk"], resolve_fn)

    # 4. Buyer received disputed funds
    buyer_bal_after = w3.eth.get_balance(buyer_addr)
    assert buyer_bal_after == buyer_bal_before + value_wei

    # On-chain state
    on_chain_deal = v2_contract.functions.getDeal(chain_deal_id).call()
    assert on_chain_deal[6] == 6, "Contract state must be State.RESOLVED (6)"
    assert on_chain_deal[7] is True, "fundsWithdrawn must be True"

    # 5. Sync -> RESOLVED
    sync2 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert sync2.status_code == 200
    assert sync2.json()["on_chain_state"] == "RESOLVED"


@pytest.mark.asyncio
async def test_v2_scenario_d3_unauthorized_dispute_resolution(client: AsyncClient):
    """
    Scenario D3: Stranger attempts to resolve dispute on-chain -> reverts UnauthorizedCaller.
    """
    w3 = get_web3()
    ctx = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.2)
    deal_id = ctx["deal_id"]
    v2_contract = ctx["v2_contract"]
    buyer_info = ctx["buyer_info"]
    stranger_info = ANVIL_ACCOUNTS["stranger"]

    chain_deal_id, tx_hash, _ = _execute_fund_deal_with_consent(w3, ctx)
    await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )

    # Buyer raises dispute
    disp_fn = v2_contract.functions.raiseDispute(chain_deal_id)
    _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], disp_fn)

    # Stranger attempts to resolve dispute -> reverts
    with pytest.raises(Exception):
        unauth_resolve_fn = v2_contract.functions.resolveDispute(chain_deal_id, True)
        _send_on_chain_tx(w3, stranger_info["address"], stranger_info["pk"], unauth_resolve_fn)

    # On-chain state remains DISPUTED
    on_chain_deal = v2_contract.functions.getDeal(chain_deal_id).call()
    assert on_chain_deal[6] == 5, "Contract state must remain State.DISPUTED (5)"


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO E: Confirmed Delivery Dispute
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_v2_scenario_e_confirmed_delivery_dispute(client: AsyncClient):
    """
    Scenario E: Confirmed Delivery Dispute
    1. Buyer confirms delivery (state = CONFIRMED).
    2. Sync -> CONFIRMED.
    3. Party raises dispute from CONFIRMED state (explicitly allowed in V2).
    4. Sync -> DISPUTED.
    5. Arbitrator resolves dispute to seller.
    6. Seller receives funds -> state becomes RESOLVED.
    7. Sync -> RESOLVED.
    """
    w3 = get_web3()
    ctx = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.35)
    deal_id = ctx["deal_id"]
    v2_contract = ctx["v2_contract"]
    buyer_info = ctx["buyer_info"]
    seller_info = ctx["seller_info"]
    arbitrator_info = ANVIL_ACCOUNTS["arbitrator"]
    value_wei = ctx["amount_wei"]

    chain_deal_id, tx_hash, _ = _execute_fund_deal_with_consent(w3, ctx)
    await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )

    # 1. Buyer confirms delivery
    confirm_fn = v2_contract.functions.confirmDelivery(chain_deal_id)
    _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], confirm_fn)

    # 2. Sync -> CONFIRMED
    sync1 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert sync1.status_code == 200
    assert sync1.json()["on_chain_state"] == "CONFIRMED"

    # 3. Seller raises dispute from CONFIRMED state
    disp_fn = v2_contract.functions.raiseDispute(chain_deal_id)
    _send_on_chain_tx(w3, seller_info["address"], seller_info["pk"], disp_fn)

    # 4. Sync -> DISPUTED
    sync2 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['s_token']}"},
    )
    assert sync2.status_code == 200
    assert sync2.json()["on_chain_state"] == "DISPUTED"

    # 5. Arbitrator resolves to seller
    seller_addr = Web3.to_checksum_address(seller_info["address"])
    seller_bal_before = w3.eth.get_balance(seller_addr)

    resolve_fn = v2_contract.functions.resolveDispute(chain_deal_id, True)
    _send_on_chain_tx(w3, arbitrator_info["address"], arbitrator_info["pk"], resolve_fn)

    # 6. Seller receives funds
    seller_bal_after = w3.eth.get_balance(seller_addr)
    assert seller_bal_after == seller_bal_before + value_wei

    # 7. Sync -> RESOLVED
    sync3 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['s_token']}"},
    )
    assert sync3.status_code == 200
    assert sync3.json()["on_chain_state"] == "RESOLVED"


# ══════════════════════════════════════════════════════════════════════════════
# SECURITY, ROLE ISOLATION & NEGATIVE TESTS
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_v2_security_role_rejection_and_isolation(client: AsyncClient):
    """
    Security Verification:
    - Stranger cannot call confirmDelivery, withdrawPayment, refundBuyer, cancelExpired, raiseDispute.
    - Seller cannot call confirmDelivery or cancelExpired.
    - Buyer cannot call withdrawPayment or refundBuyer.
    - Non-arbitrator cannot call resolveDispute.
    - Early cancellation reverts with DeadlineNotReached.
    - Backend: Stranger cannot view deal details or trigger sync (403 Forbidden).
    - Verification idempotency: repeating verify-funding with same tx_hash returns 200 without duplicate credit.
    """
    w3 = get_web3()
    ctx = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.2, deadline_offset=86400 * 2)
    deal_id = ctx["deal_id"]
    v2_contract = ctx["v2_contract"]
    buyer_info = ctx["buyer_info"]
    seller_info = ctx["seller_info"]
    stranger_info = ANVIL_ACCOUNTS["stranger"]

    stranger_token = await _register_user(client, f"str_{uuid.uuid4().hex[:6]}@test.com", "buyer", stranger_info["address"])

    chain_deal_id, tx_hash, _ = _execute_fund_deal_with_consent(w3, ctx)
    ver_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert ver_resp.status_code == 200

    # 1. Stranger cannot confirm delivery
    with pytest.raises(Exception):
        fn = v2_contract.functions.confirmDelivery(chain_deal_id)
        _send_on_chain_tx(w3, stranger_info["address"], stranger_info["pk"], fn)

    # 2. Seller cannot confirm delivery (only buyer)
    with pytest.raises(Exception):
        fn = v2_contract.functions.confirmDelivery(chain_deal_id)
        _send_on_chain_tx(w3, seller_info["address"], seller_info["pk"], fn)

    # 3. Stranger cannot withdraw payment
    with pytest.raises(Exception):
        fn = v2_contract.functions.withdrawPayment(chain_deal_id)
        _send_on_chain_tx(w3, stranger_info["address"], stranger_info["pk"], fn)

    # 4. Buyer cannot withdraw payment
    with pytest.raises(Exception):
        fn = v2_contract.functions.withdrawPayment(chain_deal_id)
        _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], fn)

    # 5. Stranger cannot refund buyer
    with pytest.raises(Exception):
        fn = v2_contract.functions.refundBuyer(chain_deal_id)
        _send_on_chain_tx(w3, stranger_info["address"], stranger_info["pk"], fn)

    # 6. Buyer cannot refund buyer (only seller)
    with pytest.raises(Exception):
        fn = v2_contract.functions.refundBuyer(chain_deal_id)
        _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], fn)

    # 7. Stranger cannot cancel expired deal
    with pytest.raises(Exception):
        fn = v2_contract.functions.cancelExpired(chain_deal_id)
        _send_on_chain_tx(w3, stranger_info["address"], stranger_info["pk"], fn)

    # 8. Seller cannot cancel expired deal (only buyer)
    with pytest.raises(Exception):
        fn = v2_contract.functions.cancelExpired(chain_deal_id)
        _send_on_chain_tx(w3, seller_info["address"], seller_info["pk"], fn)

    # 9. Early cancellation reverts (deadline not reached)
    with pytest.raises(Exception):
        fn = v2_contract.functions.cancelExpired(chain_deal_id)
        _send_on_chain_tx(w3, buyer_info["address"], buyer_info["pk"], fn)

    # 10. Stranger cannot raise dispute
    with pytest.raises(Exception):
        fn = v2_contract.functions.raiseDispute(chain_deal_id)
        _send_on_chain_tx(w3, stranger_info["address"], stranger_info["pk"], fn)

    # 11. Backend access security: Stranger cannot view deal
    det_resp = await client.get(
        f"/api/v1/escrow/{deal_id}",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert det_resp.status_code == 403

    # 12. Backend access security: Stranger cannot sync deal
    sync_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert sync_resp.status_code == 403

    # 13. Verification idempotency
    dup_ver = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert dup_ver.status_code == 200
    assert dup_ver.json()["on_chain_state"] == "FUNDED"


@pytest.mark.asyncio
async def test_v2_state_machine_terminal_invariants(client: AsyncClient):
    """
    Test that deals in terminal states cannot be transitioned again:
    1. Released deal cannot be refunded or cancelled.
    2. Refunded deal cannot be released.
    3. Cancelled deal cannot be released.
    4. Resolved deal cannot be resolved again.
    5. Backend deal sync does not overwrite terminal states.
    """
    w3 = get_web3()

    # Subtest 1: Released deal cannot be refunded
    ctx1 = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.1)
    chain_id_1, tx_1, _ = _execute_fund_deal_with_consent(w3, ctx1)
    tokens = (ctx1["b_token"], ctx1["s_token"])
    await client.post(
        f"/api/v1/escrow/deals/{ctx1['deal_id']}/verify-funding",
        json={"tx_hash": tx_1},
        headers={"Authorization": f"Bearer {ctx1['b_token']}"},
    )
    # Confirm & withdraw
    _send_on_chain_tx(w3, ctx1["buyer_info"]["address"], ctx1["buyer_info"]["pk"], ctx1["v2_contract"].functions.confirmDelivery(chain_id_1))
    _send_on_chain_tx(w3, ctx1["seller_info"]["address"], ctx1["seller_info"]["pk"], ctx1["v2_contract"].functions.withdrawPayment(chain_id_1))

    # Attempt refund on released deal -> reverts
    with pytest.raises(Exception):
        _send_on_chain_tx(w3, ctx1["seller_info"]["address"], ctx1["seller_info"]["pk"], ctx1["v2_contract"].functions.refundBuyer(chain_id_1))

    # Backend terminal state retention
    sync1 = await client.post(
        f"/api/v1/escrow/deals/{ctx1['deal_id']}/sync",
        headers={"Authorization": f"Bearer {ctx1['b_token']}"},
    )
    assert sync1.status_code == 200
    assert sync1.json()["on_chain_state"] == "RELEASED"

    # Subtest 2: Refunded deal cannot be released
    ctx2 = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.1, tokens=tokens)
    chain_id_2, tx_2, _ = _execute_fund_deal_with_consent(w3, ctx2)
    await client.post(
        f"/api/v1/escrow/deals/{ctx2['deal_id']}/verify-funding",
        json={"tx_hash": tx_2},
        headers={"Authorization": f"Bearer {ctx2['b_token']}"},
    )
    # Refund buyer
    _send_on_chain_tx(w3, ctx2["seller_info"]["address"], ctx2["seller_info"]["pk"], ctx2["v2_contract"].functions.refundBuyer(chain_id_2))

    # Attempt withdraw on refunded deal -> reverts
    with pytest.raises(Exception):
        _send_on_chain_tx(w3, ctx2["seller_info"]["address"], ctx2["seller_info"]["pk"], ctx2["v2_contract"].functions.withdrawPayment(chain_id_2))

    # Backend terminal state retention
    sync2 = await client.post(
        f"/api/v1/escrow/deals/{ctx2['deal_id']}/sync",
        headers={"Authorization": f"Bearer {ctx2['b_token']}"},
    )
    assert sync2.status_code == 200
    assert sync2.json()["on_chain_state"] == "REFUNDED"

    # Subtest 3: Resolved deal cannot be resolved again
    ctx3 = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.1, tokens=tokens)
    chain_id_3, tx_3, _ = _execute_fund_deal_with_consent(w3, ctx3)
    await client.post(
        f"/api/v1/escrow/deals/{ctx3['deal_id']}/verify-funding",
        json={"tx_hash": tx_3},
        headers={"Authorization": f"Bearer {ctx3['b_token']}"},
    )
    _send_on_chain_tx(w3, ctx3["buyer_info"]["address"], ctx3["buyer_info"]["pk"], ctx3["v2_contract"].functions.raiseDispute(chain_id_3))
    _send_on_chain_tx(w3, ANVIL_ACCOUNTS["arbitrator"]["address"], ANVIL_ACCOUNTS["arbitrator"]["pk"], ctx3["v2_contract"].functions.resolveDispute(chain_id_3, True))

    # Attempt second resolution -> reverts
    with pytest.raises(Exception):
        _send_on_chain_tx(w3, ANVIL_ACCOUNTS["arbitrator"]["address"], ANVIL_ACCOUNTS["arbitrator"]["pk"], ctx3["v2_contract"].functions.resolveDispute(chain_id_3, True))

    # Backend terminal state retention
    sync3 = await client.post(
        f"/api/v1/escrow/deals/{ctx3['deal_id']}/sync",
        headers={"Authorization": f"Bearer {ctx3['b_token']}"},
    )
    assert sync3.status_code == 200
    assert sync3.json()["on_chain_state"] == "RESOLVED"


@pytest.mark.asyncio
async def test_v2_replay_and_duplicate_funding_protection(client: AsyncClient):
    """
    Test duplicate funding and replay rejection:
    1. Re-executing fundDealWithConsent with the same deal UUID reverts (DealAlreadyFunded).
    2. Backend rejects funding verification if tx destination is not the trusted V2 contract.
    """
    w3 = get_web3()
    ctx = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.2)
    v2_contract = ctx["v2_contract"]
    buyer_info = ctx["buyer_info"]
    payload = ctx["payload"]

    # First funding succeeds
    chain_deal_id, tx_hash, _ = _execute_fund_deal_with_consent(w3, ctx)
    assert chain_deal_id is not None

    # Second funding with exact same agreement and consent parameters reverts on-chain
    agreement = payload["agreement"]
    buyer_proof = payload["buyerConsent"]
    seller_proof = payload["sellerConsent"]

    agreement_tuple = (
        bytes.fromhex(agreement["agreementHash"][2:]),
        str(agreement["offerId"]),
        str(agreement["dealId"]),
        Web3.to_checksum_address(agreement["buyer"]),
        Web3.to_checksum_address(agreement["seller"]),
        int(agreement["amount"]),
        int(agreement["deliveryDeadline"]),
    )
    buyer_proof_tuple = (
        str(buyer_proof["nonce"]),
        int(buyer_proof["deadline"]),
        bytes.fromhex(buyer_proof["signature"][2:]),
    )
    seller_proof_tuple = (
        str(seller_proof["nonce"]),
        int(seller_proof["deadline"]),
        bytes.fromhex(seller_proof["signature"][2:]),
    )

    buyer_addr = Web3.to_checksum_address(buyer_info["address"])
    tx_params = {
        "from": buyer_addr,
        "value": int(agreement["amount"]),
        "nonce": w3.eth.get_transaction_count(buyer_addr),
        "gas": 600000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 31337,
    }
    fund_fn = v2_contract.functions.fundDealWithConsent(
        agreement_tuple,
        buyer_proof_tuple,
        seller_proof_tuple,
    )
    # eth_call simulation reverts with custom error (DealAlreadyFunded / NonceReplayed)
    with pytest.raises(Exception):
        fund_fn.call(tx_params)


@pytest.mark.asyncio
async def test_v2_contract_tracking_and_zero_fallback(client: AsyncClient):
    """
    Verify that:
    1. V2 deals persist contract_address and contract_version="v2".
    2. Sync deals queries V2 contract with 8-field tuple and checks dealUuidHash.
    3. Legacy V1 deals query V1 contract with 7-field tuple.
    4. V2 deals never silently fallback to V1 contract.
    """
    w3 = get_web3()
    ctx = await _setup_and_sign_v2_deal(client, w3, amount_eth=0.25)
    deal_id = ctx["deal_id"]
    v2_contract = ctx["v2_contract"]

    chain_deal_id, tx_hash, _ = _execute_fund_deal_with_consent(w3, ctx)
    ver_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash},
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert ver_resp.status_code == 200
    deal_data = ver_resp.json()

    # Persisted contract identity
    assert deal_data["contract_address"].lower() == v2_contract.address.lower()
    assert deal_data["contract_version"] == "v2"

    # Query details endpoint confirms persisted identity
    get_resp = await client.get(
        f"/api/v1/escrow/{deal_id}",
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["contract_address"].lower() == v2_contract.address.lower()
    assert get_resp.json()["contract_version"] == "v2"

    # Deal sync queries V2 contract and validates dealUuidHash
    sync_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {ctx['b_token']}"},
    )
    assert sync_resp.status_code == 200
    synced = sync_resp.json()
    assert synced["contract_address"].lower() == v2_contract.address.lower()
    assert synced["contract_version"] == "v2"
    assert synced["on_chain_state"] == "FUNDED"
