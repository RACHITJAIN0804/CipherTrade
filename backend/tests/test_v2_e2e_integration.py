"""
End-to-End V2 Dual-Consent Escrow Integration Test against Live Anvil Node (Chain ID: 31337).

Verifies the entire Phase 5E.3 lifecycle:
1. Register buyer and seller with verified Anvil wallets (SIWE).
2. Buyer creates Requirement -> Seller creates Offer -> Buyer accepts Offer.
3. Backend creates authoritative EscrowDeal linked to accepted Offer.
4. Buyer and Seller both sign canonical 11-field EIP-712 AgreementConsent challenges.
5. Buyer retrieves contract-ready funding payload from GET /api/v1/deals/{deal_id}/contract-payload.
6. Buyer submits fundDealWithConsent to deployed CipherTradeEscrowV2 on Anvil.
7. Transaction receipt confirms on-chain execution and DealFunded event emission.
8. Backend POST /api/v1/escrow/deals/{deal_id}/verify-funding independently verifies the V2 transaction.
9. Database deal record is atomically marked as FUNDED with chainDealId and fund_tx_hash.
10. Querying GET /api/v1/escrow/deals/{deal_id} confirms FUNDED state.
"""
from __future__ import annotations

import time
import uuid
import pytest
from httpx import AsyncClient
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data

from app.core.blockchain import get_web3, load_v2_contract_abi
from app.models.models import EscrowState

# Standard deterministic Anvil local development accounts
ANVIL_ACCOUNTS = {
    "buyer": {
        "address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "pk": "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    },
    "seller": {
        "address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "pk": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
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


@pytest.mark.asyncio
async def test_v2_dual_consent_end_to_end(client: AsyncClient):
    w3 = get_web3()
    assert w3.is_connected(), "Anvil node must be connected at http://127.0.0.1:8545"

    buyer_info = ANVIL_ACCOUNTS["buyer"]
    seller_info = ANVIL_ACCOUNTS["seller"]

    # 1. Register buyer and seller with matching Anvil addresses
    b_token = await _register_user(client, f"b_{uuid.uuid4().hex[:6]}@test.com", "buyer", buyer_info["address"])
    s_token = await _register_user(client, f"s_{uuid.uuid4().hex[:6]}@test.com", "seller", seller_info["address"])

    # 2. Buyer creates requirement
    req_resp = await client.post(
        "/api/v1/requirements",
        json={
            "title": "CipherTrade V2 End-to-End Escrow Audit",
            "description": "Full lifecycle verification of V2 dual-consent smart contract.",
            "category": "security",
        },
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert req_resp.status_code == 201
    req_id = req_resp.json()["id"]

    # 3. Seller creates offer
    deadline = w3.eth.get_block("latest").timestamp + 86400 * 3
    amount_eth = 0.5
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
        json={"offer_id": offer_id},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert deal_resp.status_code == 201, deal_resp.text
    deal_data = deal_resp.json()
    deal_id = deal_data["id"]

    # 6. Both parties sign EIP-712 consent challenges
    # Buyer consent
    b_chal_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert b_chal_resp.status_code == 200
    b_chal = b_chal_resp.json()

    # Sign EIP-712 typed data
    b_data_to_sign = {
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
    raw_b_sig = Account.from_key(buyer_info["pk"]).sign_message(encode_typed_data(full_message=b_data_to_sign)).signature.hex()
    b_sig = ("0x" + raw_b_sig) if not raw_b_sig.startswith("0x") else raw_b_sig

    b_ver_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": b_chal["nonce"], "signature": b_sig},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert b_ver_resp.status_code == 200

    # Seller consent
    s_chal_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {s_token}"},
    )
    assert s_chal_resp.status_code == 200
    s_chal = s_chal_resp.json()

    s_data_to_sign = {
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
    raw_s_sig = Account.from_key(seller_info["pk"]).sign_message(encode_typed_data(full_message=s_data_to_sign)).signature.hex()
    s_sig = ("0x" + raw_s_sig) if not raw_s_sig.startswith("0x") else raw_s_sig

    s_ver_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": s_chal["nonce"], "signature": s_sig},
        headers={"Authorization": f"Bearer {s_token}"},
    )
    assert s_ver_resp.status_code == 200
    assert s_ver_resp.json()["both_consented"] is True

    # 7. Retrieve contract-ready funding payload from GET /api/v1/deals/{deal_id}/contract-payload
    payload_resp = await client.get(
        f"/api/v1/deals/{deal_id}/contract-payload",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert payload_resp.status_code == 200, payload_resp.text
    payload = payload_resp.json()

    v2_address = payload["contract_address"]
    assert v2_address.lower() == "0xe7f1725e7734ce288f8367e1bb143e90bb3f0512"

    agreement = payload["agreement"]
    buyer_proof = payload["buyerConsent"]
    seller_proof = payload["sellerConsent"]

    # 8. Submit fundDealWithConsent on-chain to CipherTradeEscrowV2
    v2_abi = load_v2_contract_abi()
    assert v2_abi is not None, "CipherTradeEscrowV2 ABI must be loaded"

    v2_contract = w3.eth.contract(address=Web3.to_checksum_address(v2_address), abi=v2_abi)

    # Format structs for contract call
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

    # Build and sign transaction from Buyer
    buyer_addr = Web3.to_checksum_address(buyer_info["address"])
    tx_dict = v2_contract.functions.fundDealWithConsent(
        agreement_tuple,
        buyer_proof_tuple,
        seller_proof_tuple,
    ).build_transaction({
        "from": buyer_addr,
        "value": value_wei,
        "nonce": w3.eth.get_transaction_count(buyer_addr),
        "gas": 600000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 31337,
    })

    signed_tx = w3.eth.account.sign_transaction(tx_dict, buyer_info["pk"])
    tx_hash_bytes = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=10)

    assert receipt["status"] == 1, "V2 funding transaction must succeed with status=1"

    # Decode DealFunded event
    events = v2_contract.events.DealFunded().process_receipt(receipt)
    assert len(events) == 1, "Exactly one DealFunded event must be emitted"
    chain_deal_id = events[0]["args"]["chainDealId"]
    emitted_deal_uuid = events[0]["args"]["dealUuid"]
    assert emitted_deal_uuid == deal_id, "Emitted deal UUID must match backend deal ID"

    tx_hash_hex = receipt["transactionHash"].hex()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex

    # 9. Verify transaction with backend POST /api/v1/escrow/deals/{deal_id}/verify-funding
    verify_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/verify-funding",
        json={"tx_hash": tx_hash_hex},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert verify_resp.status_code == 200, verify_resp.text
    verified_deal = verify_resp.json()

    assert verified_deal["chain_deal_id"] == chain_deal_id
    assert verified_deal["fund_tx_hash"].lower() == tx_hash_hex.lower()
    assert verified_deal["on_chain_state"] == "FUNDED"

    # 10. Query deal state via GET
    get_deal_resp = await client.get(
        f"/api/v1/escrow/{deal_id}",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert get_deal_resp.status_code == 200
    deal_record = get_deal_resp.json()
    assert deal_record["chain_deal_id"] == chain_deal_id
    assert deal_record["on_chain_state"] == "FUNDED"
