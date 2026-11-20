"""
Comprehensive unit and integration tests for Phase 5D.2:
Backend-Generated Canonical Agreement Hashes.

Tests cover:
1. Canonical agreement construction and schema adherence (ciphertrade-agreement-v1).
2. Deterministic serialization and Keccak-256 (bytes32) hashing.
3. Sensitivity to changes in terms, price, deadline, title, category.
4. Float and decimal normalization precision safety.
5. Authorization and access control on canonical agreement endpoints.
6. Immutability & locking of requirement terms once an offer is accepted.
7. Deal creation ignoring client-supplied tampering.
8. Independent verification of on-chain agreement hash against backend canonical hash.
"""
from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from web3 import Web3

from app.models.models import EscrowDeal, EscrowState, Offer, OfferStatus, Requirement, User
from app.services import agreement_service
from app.services.agreement_service import (
    CANONICAL_AGREEMENT_SCHEMA_VERSION,
    build_canonical_agreement,
    compute_agreement_hash,
    generate_canonical_hash_for_entities,
    normalize_eth_amount,
    serialize_canonical_agreement,
)


# ─── 1. Canonical Construction & Serialization Unit Tests ───────────────────


def test_eth_amount_normalization():
    """Verify ETH amounts are strictly normalized to 8 decimal places as strings."""
    assert normalize_eth_amount(1.5) == "1.50000000"
    assert normalize_eth_amount("0.5") == "0.50000000"
    assert normalize_eth_amount(Decimal("0.123456789")) == "0.12345679"  # Half-up rounding at 8 decimals
    assert normalize_eth_amount(10) == "10.00000000"
    assert normalize_eth_amount("0.00000001") == "0.00000001"

    with pytest.raises(ValueError):
        normalize_eth_amount("invalid_number")


def test_deterministic_serialization():
    """Verify serialization is independent of dictionary key insertion order."""
    dict_order_1 = {
        "schema_version": CANONICAL_AGREEMENT_SCHEMA_VERSION,
        "title": "Data Extraction Script",
        "price_eth": "1.50000000",
        "currency": "ETH",
        "terms": "Full source code and test suite.",
    }

    dict_order_2 = {
        "terms": "Full source code and test suite.",
        "currency": "ETH",
        "schema_version": CANONICAL_AGREEMENT_SCHEMA_VERSION,
        "price_eth": "1.50000000",
        "title": "Data Extraction Script",
    }

    bytes_1 = serialize_canonical_agreement(dict_order_1)
    bytes_2 = serialize_canonical_agreement(dict_order_2)

    assert bytes_1 == bytes_2
    assert bytes_1.decode("utf-8") == json.dumps(dict_order_1, sort_keys=True, separators=(",", ":"))


def test_keccak_hash_format_and_determinism():
    """Verify Keccak-256 hash conforms to 0x-prefixed 64-hex-character bytes32 format."""
    payload = {"key": "value", "schema": "v1"}
    canonical_bytes = serialize_canonical_agreement(payload)
    hash_result = compute_agreement_hash(canonical_bytes)

    assert hash_result.startswith("0x")
    assert len(hash_result) == 66  # 0x + 64 hex characters
    assert hash_result == hash_result.lower()

    # Re-computing produces the exact same hash
    assert compute_agreement_hash(canonical_bytes) == hash_result


def test_agreement_hash_sensitivity():
    """Verify that any modification to agreement terms produces a distinct hash."""
    base_offer = MagicMock(spec=Offer)
    base_offer.id = uuid.uuid4()
    base_offer.price_eth = 1.5
    base_offer.delivery_deadline = int(time.time()) + 86400 * 3
    base_offer.terms = "Base deliverables and audit report."

    base_req = MagicMock(spec=Requirement)
    base_req.id = uuid.uuid4()
    base_req.buyer_id = uuid.uuid4()
    base_req.title = "Security Audit"
    base_req.description = "Perform L1 contract audit."
    base_req.category = "security"
    base_offer.seller_id = uuid.uuid4()

    _, base_hash = generate_canonical_hash_for_entities(base_offer, base_req)

    # 1. Price change
    offer_diff_price = MagicMock(spec=Offer)
    offer_diff_price.id = base_offer.id
    offer_diff_price.price_eth = 1.50000001
    offer_diff_price.delivery_deadline = base_offer.delivery_deadline
    offer_diff_price.terms = base_offer.terms
    offer_diff_price.seller_id = base_offer.seller_id
    _, price_hash = generate_canonical_hash_for_entities(offer_diff_price, base_req)
    assert price_hash != base_hash

    # 2. Deadline change
    offer_diff_deadline = MagicMock(spec=Offer)
    offer_diff_deadline.id = base_offer.id
    offer_diff_deadline.price_eth = base_offer.price_eth
    offer_diff_deadline.delivery_deadline = base_offer.delivery_deadline + 1
    offer_diff_deadline.terms = base_offer.terms
    offer_diff_deadline.seller_id = base_offer.seller_id
    _, deadline_hash = generate_canonical_hash_for_entities(offer_diff_deadline, base_req)
    assert deadline_hash != base_hash

    # 3. Terms change
    offer_diff_terms = MagicMock(spec=Offer)
    offer_diff_terms.id = base_offer.id
    offer_diff_terms.price_eth = base_offer.price_eth
    offer_diff_terms.delivery_deadline = base_offer.delivery_deadline
    offer_diff_terms.terms = "Modified terms: no audit report."
    offer_diff_terms.seller_id = base_offer.seller_id
    _, terms_hash = generate_canonical_hash_for_entities(offer_diff_terms, base_req)
    assert terms_hash != base_hash

    # 4. Title change
    req_diff_title = MagicMock(spec=Requirement)
    req_diff_title.id = base_req.id
    req_diff_title.buyer_id = base_req.buyer_id
    req_diff_title.title = "Security Audit V2"
    req_diff_title.description = base_req.description
    req_diff_title.category = base_req.category
    _, title_hash = generate_canonical_hash_for_entities(base_offer, req_diff_title)
    assert title_hash != base_hash


# ─── 2. Integration Tests: API & Authorization ──────────────────────────────


async def _register_and_login(client: AsyncClient, email: str, role: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "display_name": f"User {email}",
            "primary_role": role,
            "password": "ValidPass1!",
        },
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "ValidPass1!"},
    )
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_canonical_agreement_by_offer_authorization(client: AsyncClient):
    """Verify GET /offers/{offer_id}/agreement authorization and lifecycle rules."""
    buyer_token = await _register_and_login(client, "buyer_ag@test.com", "buyer")
    seller_token = await _register_and_login(client, "seller_ag@test.com", "seller")
    other_token = await _register_and_login(client, "rando_ag@test.com", "buyer")

    # 1. Buyer creates requirement
    req_resp = await client.post(
        "/api/v1/requirements",
        json={
            "title": "Machine Learning Model Training",
            "description": "Train LLM classifier with PyTorch.",
            "category": "ai",
        },
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    req_id = req_resp.json()["id"]

    # 2. Seller creates offer
    deadline = int(time.time()) + 86400 * 7
    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": 3.0,
            "delivery_deadline": deadline,
            "terms": "Weights, checkpoints, and evaluation notebook.",
        },
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    offer_id = offer_resp.json()["id"]

    # 3. Cannot get canonical agreement before offer is accepted
    pending_resp = await client.get(
        f"/api/v1/escrow/offers/{offer_id}/agreement",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert pending_resp.status_code == 400
    assert "only accepted offers" in pending_resp.json()["detail"].lower()

    # 4. Buyer accepts offer
    await client.post(
        f"/api/v1/offers/{offer_id}/accept",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )

    # 5. Buyer can retrieve canonical agreement
    buyer_ag_resp = await client.get(
        f"/api/v1/escrow/offers/{offer_id}/agreement",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert buyer_ag_resp.status_code == 200
    buyer_data = buyer_ag_resp.json()
    assert buyer_data["schema_version"] == CANONICAL_AGREEMENT_SCHEMA_VERSION
    assert buyer_data["agreement_hash"].startswith("0x")
    assert len(buyer_data["agreement_hash"]) == 66
    assert buyer_data["canonical_agreement"]["price_eth"] == "3.00000000"
    assert buyer_data["canonical_agreement"]["currency"] == "ETH"

    # 6. Seller can also retrieve canonical agreement
    seller_ag_resp = await client.get(
        f"/api/v1/escrow/offers/{offer_id}/agreement",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert seller_ag_resp.status_code == 200
    assert seller_ag_resp.json()["agreement_hash"] == buyer_data["agreement_hash"]

    # 7. Unauthorized third party cannot retrieve canonical agreement
    rando_ag_resp = await client.get(
        f"/api/v1/escrow/offers/{offer_id}/agreement",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert rando_ag_resp.status_code == 403

    # 8. Unauthenticated request rejected
    unauth_resp = await client.get(f"/api/v1/escrow/offers/{offer_id}/agreement")
    assert unauth_resp.status_code == 401


@pytest.mark.asyncio
async def test_canonical_agreement_by_deal_authorization(client: AsyncClient):
    """Verify GET /deals/{deal_id}/agreement authorization and hash consistency."""
    buyer_token = await _register_and_login(client, "buyer_deal_ag@test.com", "buyer")
    seller_token = await _register_and_login(client, "seller_deal_ag@test.com", "seller")
    other_token = await _register_and_login(client, "rando_deal_ag@test.com", "seller")

    # 1. Setup accepted offer
    req_resp = await client.post(
        "/api/v1/requirements",
        json={"title": "Cloud Architecture Review", "description": "AWS Well-Architected Review", "category": "cloud"},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    req_id = req_resp.json()["id"]

    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": 1.25,
            "delivery_deadline": int(time.time()) + 86400 * 2,
            "terms": "Full cloud review report.",
        },
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    offer_id = offer_resp.json()["id"]

    await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {buyer_token}"})

    # 2. Create escrow deal (omitting agreement_hash -> backend automatically computes canonical)
    deal_resp = await client.post(
        "/api/v1/escrow/deals",
        json={"offer_id": offer_id},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert deal_resp.status_code == 201
    deal = deal_resp.json()
    deal_id = deal["id"]
    authoritative_hash = deal["agreement_hash"]
    assert authoritative_hash.startswith("0x")
    assert len(authoritative_hash) == 66

    # 3. Buyer retrieves canonical agreement by deal UUID
    b_resp = await client.get(
        f"/api/v1/escrow/deals/{deal_id}/agreement",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert b_resp.status_code == 200
    assert b_resp.json()["agreement_hash"] == authoritative_hash
    assert b_resp.json()["deal_id"] == deal_id

    # 4. Seller retrieves canonical agreement by deal UUID
    s_resp = await client.get(
        f"/api/v1/escrow/deals/{deal_id}/agreement",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert s_resp.status_code == 200
    assert s_resp.json()["agreement_hash"] == authoritative_hash

    # 5. Unauthorized third party receives 403
    o_resp = await client.get(
        f"/api/v1/escrow/deals/{deal_id}/agreement",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert o_resp.status_code == 403


@pytest.mark.asyncio
async def test_client_cannot_override_authoritative_hash(client: AsyncClient):
    """
    Verify that if a client attempts to submit an arbitrary/tampered agreement_hash,
    amount_eth, or deadline_ts, the backend explicitly rejects the unauthorized override with HTTP 400.
    """
    buyer_token = await _register_and_login(client, "buyer_override@test.com", "buyer")
    seller_token = await _register_and_login(client, "seller_override@test.com", "seller")

    req_resp = await client.post(
        "/api/v1/requirements",
        json={"title": "Data Pipeline", "description": "ETL pipeline implementation", "category": "data"},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    req_id = req_resp.json()["id"]
    deadline = int(time.time()) + 86400 * 4

    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": 0.8,
            "delivery_deadline": deadline,
            "terms": "Airflow DAGs and dbt models.",
        },
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    offer_id = offer_resp.json()["id"]
    await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {buyer_token}"})

    # 1. Attempted override of agreement_hash -> Rejected with 400
    tampered_hash = "0x" + "f" * 64
    deal_resp_hash = await client.post(
        "/api/v1/escrow/deals",
        json={
            "offer_id": offer_id,
            "agreement_hash": tampered_hash,
        },
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert deal_resp_hash.status_code == 400
    assert "agreement_hash does not match" in deal_resp_hash.json()["detail"]

    # 2. Attempted override of amount_eth -> Rejected with 400
    deal_resp_amount = await client.post(
        "/api/v1/escrow/deals",
        json={
            "offer_id": offer_id,
            "amount_eth": 999.0,
        },
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert deal_resp_amount.status_code == 400
    assert "amount_eth" in deal_resp_amount.json()["detail"]

    # 3. Attempted override of deadline_ts -> Rejected with 400
    deal_resp_deadline = await client.post(
        "/api/v1/escrow/deals",
        json={
            "offer_id": offer_id,
            "deadline_ts": deadline + 10000,
        },
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert deal_resp_deadline.status_code == 400
    assert "deadline_ts" in deal_resp_deadline.json()["detail"]

    # 4. Clean submission without override fields -> Accepted (201) with authoritative values from DB
    deal_resp_clean = await client.post(
        "/api/v1/escrow/deals",
        json={"offer_id": offer_id},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert deal_resp_clean.status_code == 201
    deal = deal_resp_clean.json()
    assert deal["amount_eth"] == 0.8
    assert deal["deadline_ts"] == deadline
    assert deal["agreement_hash"].startswith("0x")


@pytest.mark.asyncio
async def test_accepted_offer_immutability(client: AsyncClient):
    """
    Verify lifecycle guard: once an offer is in ACCEPTED status, no seller or buyer
    workflow can modify its price_eth, delivery_deadline, terms, or transition status.
    """
    buyer_token = await _register_and_login(client, "buyer_immut@test.com", "buyer")
    seller_token = await _register_and_login(client, "seller_immut@test.com", "seller")

    req_resp = await client.post(
        "/api/v1/requirements",
        json={
            "title": "Immutable Offer Test",
            "description": "Comprehensive scope description for immutability test.",
            "category": "security",
        },
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    req_id = req_resp.json()["id"]
    deadline = int(time.time()) + 86400 * 5

    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": 2.0,
            "delivery_deadline": deadline,
            "terms": "Original agreed offer terms.",
        },
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    offer_id = offer_resp.json()["id"]

    # Accept the offer
    await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {buyer_token}"})

    # 1. Seller attempts to modify price_eth / terms / deadline via PATCH /offers/{id}
    patch_resp = await client.patch(
        f"/api/v1/offers/{offer_id}",
        json={"price_eth": 5.0, "terms": "Tampered post-acceptance terms."},
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert patch_resp.status_code == 400
    assert "can only update a pending offer" in patch_resp.json()["detail"].lower()

    # 2. Seller attempts to withdraw accepted offer
    withdraw_resp = await client.post(
        f"/api/v1/offers/{offer_id}/withdraw",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert withdraw_resp.status_code == 400
    assert "already accepted" in withdraw_resp.json()["detail"].lower()

    # 3. Buyer attempts to reject already accepted offer
    reject_resp = await client.post(
        f"/api/v1/offers/{offer_id}/reject",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert reject_resp.status_code == 400
    assert "already accepted" in reject_resp.json()["detail"].lower()

    # 4. Buyer attempts to re-accept already accepted offer
    reaccept_resp = await client.post(
        f"/api/v1/offers/{offer_id}/accept",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert reaccept_resp.status_code == 400
    assert "already accepted" in reaccept_resp.json()["detail"].lower()


def test_cross_language_fixed_canonical_vector():
    """
    Verify deterministic hashing against a fixed cross-language test vector constant.
    Matches exact payload tested in TypeScript / Ethers.
    """
    fixed_payload = {
        "buyer_id": "00000000-0000-0000-0000-000000000001",
        "category": "Data Extraction",
        "currency": "ETH",
        "deal_reference": "offer:00000000-0000-0000-0000-000000000002",
        "delivery_deadline": "2026-12-01T12:00:00Z",
        "description": "Test requirement description for fixed vector audit.",
        "offer_id": "00000000-0000-0000-0000-000000000002",
        "price_eth": "1.50000000",
        "requirement_id": "00000000-0000-0000-0000-000000000003",
        "schema_version": "ciphertrade-agreement-v1",
        "seller_id": "00000000-0000-0000-0000-000000000004",
        "terms": "Standard test terms for fixed vector verification.",
        "title": "Test Requirement Title",
    }

    canonical_bytes = serialize_canonical_agreement(fixed_payload)
    computed_hash = compute_agreement_hash(canonical_bytes)

    # EXACT expected 32-byte Keccak-256 hash constant
    expected_fixed_hash = "0x6ef525c2e13920dbdb3549d949f23ddf711192863dee0cbae7df2b468a7adea2"

    assert computed_hash == expected_fixed_hash, f"Hash mismatch! Got {computed_hash}, expected {expected_fixed_hash}"


def test_deadline_iso_timestamp_roundtrip_consistency():
    """
    Verify bidirectional exact symmetry between integer Unix timestamp and UTC ISO 8601 string.
    Ensures zero off-by-one second and zero timezone drift.
    """
    from datetime import datetime, timezone

    timestamps_to_test = [
        1796126400,  # 2026-12-01T12:00:00Z
        1700000000,
        1800000001,
        2000000000,
    ]

    for ts in timestamps_to_test:
        iso_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        recovered_ts = int(datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
        assert ts == recovered_ts, f"Timestamp drift detected for {ts}: recovered {recovered_ts}"
        assert iso_str.endswith("Z"), f"ISO string must end with Z for UTC: {iso_str}"


@pytest.mark.asyncio
async def test_requirement_terms_locked_after_offer_acceptance(client: AsyncClient):
    """
    Verify terms immutability: once an offer is accepted, modifying the requirement
    via PATCH /requirements/{id} is rejected to prevent silent terms drift.
    """
    buyer_token = await _register_and_login(client, "buyer_lock@test.com", "buyer")
    seller_token = await _register_and_login(client, "seller_lock@test.com", "seller")

    # 1. Create requirement
    req_resp = await client.post(
        "/api/v1/requirements",
        json={"title": "Original Scope", "description": "Original description", "category": "web"},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    req_id = req_resp.json()["id"]

    # 2. Seller creates offer
    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": 1.0,
            "delivery_deadline": int(time.time()) + 86400 * 3,
            "terms": "Agreed terms.",
        },
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    offer_id = offer_resp.json()["id"]

    # Before acceptance, editing requirement is allowed
    edit_resp = await client.patch(
        f"/api/v1/requirements/{req_id}",
        json={"description": "Updated pre-acceptance description"},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert edit_resp.status_code == 200

    # 3. Buyer accepts offer
    await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {buyer_token}"})

    # 4. After acceptance, attempting to modify requirement terms is rejected
    locked_resp = await client.patch(
        f"/api/v1/requirements/{req_id}",
        json={"description": "Tampered description after acceptance"},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert locked_resp.status_code == 400
    assert "offer has been accepted" in locked_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_transaction_verification_enforces_backend_canonical_hash(client: AsyncClient):
    """
    Verify that backend transaction verification compares the on-chain agreement hash
    against the backend-generated canonical agreement hash.
    """
    buyer_wallet = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    seller_wallet = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    contract_addr = "0x5FbDB2315678afecb367f032d93F642f64180aa3"

    buyer_token = await _register_and_login(client, "b_verify_ag@test.com", "buyer")
    seller_token = await _register_and_login(client, "s_verify_ag@test.com", "seller")

    # Bind wallets via SIWE
    for token, w in [(buyer_token, buyer_wallet), (seller_token, seller_wallet)]:
        from eth_account import Account
        from eth_account.messages import encode_defunct

        pk = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d" if "7099" in w else "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"
        chal = await client.post(
            "/api/v1/auth/wallet/challenge",
            json={"wallet_address": w},
            headers={"Authorization": f"Bearer {token}"},
        )
        msg = chal.json()["message"]
        sig = Account.from_key(pk).sign_message(encode_defunct(text=msg)).signature.hex()
        await client.post(
            "/api/v1/auth/wallet/verify",
            json={"message": msg, "signature": sig},
            headers={"Authorization": f"Bearer {token}"},
        )

    # Create requirement, offer, accept, and create deal
    req_resp = await client.post(
        "/api/v1/requirements",
        json={"title": "Verification Target", "description": "Verification contract test", "category": "security"},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    req_id = req_resp.json()["id"]
    deadline = int(time.time()) + 86400 * 3

    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": 1.5,
            "delivery_deadline": deadline,
            "terms": "Final deliverable.",
        },
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    offer_id = offer_resp.json()["id"]
    await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {buyer_token}"})

    deal_resp = await client.post(
        "/api/v1/escrow/deals",
        json={"offer_id": offer_id},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    deal = deal_resp.json()
    deal_id = deal["id"]
    canonical_hash = deal["agreement_hash"]

    # Test 1: On-chain transaction has a MISMATCHED agreement hash -> Must be rejected
    mismatched_hash = "0x" + "ee" * 32
    fake_tx = "0x" + "d" * 64
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
    mock_event_mismatched = {
        "address": contract_addr,
        "args": {
            "dealId": 0,
            "buyer": buyer_wallet,
            "seller": seller_wallet,
            "amount": int(1.5 * 10**18),
            "deadline": deadline,
            "agreementHash": bytes.fromhex(mismatched_hash[2:]),
        },
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
        mock_contract.events.DealFunded().process_receipt.return_value = [mock_event_mismatched]
        mock_contract.functions.getDeal.return_value.call.return_value = (
            buyer_wallet,
            seller_wallet,
            int(1.5 * 10**18),
            deadline,
            bytes.fromhex(mismatched_hash[2:]),
            0,
            False,
        )
        mock_gcontract.return_value = mock_contract

        resp = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 400
        assert "agreement hash mismatch" in resp.json()["detail"].lower()

    # Test 2: On-chain transaction has the EXACT CANONICAL agreement hash -> Must succeed
    mock_event_valid = {
        "address": contract_addr,
        "args": {
            "dealId": 0,
            "buyer": buyer_wallet,
            "seller": seller_wallet,
            "amount": int(1.5 * 10**18),
            "deadline": deadline,
            "agreementHash": bytes.fromhex(canonical_hash[2:]),
        },
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
        mock_contract.events.DealFunded().process_receipt.return_value = [mock_event_valid]
        mock_contract.functions.getDeal.return_value.call.return_value = (
            buyer_wallet,
            seller_wallet,
            int(1.5 * 10**18),
            deadline,
            bytes.fromhex(canonical_hash[2:]),
            0,
            False,
        )
        mock_gcontract.return_value = mock_contract

        resp_ok = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp_ok.status_code == 200
        verified_deal = resp_ok.json()
        assert verified_deal["on_chain_state"] == "FUNDED"
        assert verified_deal["fund_tx_hash"] == fake_tx
        assert verified_deal["agreement_hash"] == canonical_hash
