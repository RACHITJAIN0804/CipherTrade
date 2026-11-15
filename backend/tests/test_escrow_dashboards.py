"""Tests for Phase 5C.4 Buyer and Seller Escrow Transaction Dashboards."""
from __future__ import annotations

import time
import pytest
from httpx import AsyncClient


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
async def test_buyer_deals_listing_and_isolation(client: AsyncClient):
    buyer1_token = await _register_and_login(client, "b1_dash@test.com", "buyer")
    buyer2_token = await _register_and_login(client, "b2_dash@test.com", "buyer")
    seller_token = await _register_and_login(client, "s1_dash@test.com", "seller")

    # 1. Buyer 1 creates requirement
    req_resp = await client.post(
        "/api/v1/requirements",
        json={
            "title": "Smart Contract Audit Service",
            "description": "Comprehensive security audit of escrow contract.",
            "category": "security",
        },
        headers={"Authorization": f"Bearer {buyer1_token}"},
    )
    req_id = req_resp.json()["id"]

    # 2. Seller creates offer
    deadline = int(time.time()) + 86400 * 5
    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": 2.5,
            "delivery_deadline": deadline,
            "terms": "Full vulnerability report and remediations.",
        },
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    offer_id = offer_resp.json()["id"]

    # 3. Buyer 1 accepts offer & creates deal
    await client.post(
        f"/api/v1/offers/{offer_id}/accept",
        headers={"Authorization": f"Bearer {buyer1_token}"},
    )
    deal_resp = await client.post(
        "/api/v1/escrow/deals",
        json={
            "offer_id": offer_id,
            "amount_eth": 2.5,
            "deadline_ts": deadline,
            "agreement_hash": "0x" + "a" * 64,
        },
        headers={"Authorization": f"Bearer {buyer1_token}"},
    )
    assert deal_resp.status_code == 201

    # 4. Buyer 1 gets deals list -> exactly 1 deal with full transaction details
    b1_list_resp = await client.get(
        "/api/v1/escrow/deals/buyer",
        headers={"Authorization": f"Bearer {buyer1_token}"},
    )
    assert b1_list_resp.status_code == 200
    b1_data = b1_list_resp.json()
    assert b1_data["total"] == 1
    assert len(b1_data["items"]) == 1

    item = b1_data["items"][0]
    assert item["amount_eth"] == 2.5
    assert item["requirement_title"] == "Smart Contract Audit Service"
    assert item["requirement_category"] == "security"
    assert item["offer_terms"] == "Full vulnerability report and remediations."
    assert item["seller_display_name"] == "User s1_dash@test.com"

    # 5. Buyer 2 gets deals list -> exactly 0 deals (strict database-level user isolation)
    b2_list_resp = await client.get(
        "/api/v1/escrow/deals/buyer",
        headers={"Authorization": f"Bearer {buyer2_token}"},
    )
    assert b2_list_resp.status_code == 200
    b2_data = b2_list_resp.json()
    assert b2_data["total"] == 0
    assert len(b2_data["items"]) == 0


@pytest.mark.asyncio
async def test_seller_deals_listing_and_isolation(client: AsyncClient):
    buyer_token = await _register_and_login(client, "b_seller_dash@test.com", "buyer")
    seller1_token = await _register_and_login(client, "s1_seller_dash@test.com", "seller")
    seller2_token = await _register_and_login(client, "s2_seller_dash@test.com", "seller")

    # 1. Buyer creates requirement
    req_resp = await client.post(
        "/api/v1/requirements",
        json={
            "title": "Decentralized Oracle Integration",
            "description": "Feed price data into smart contract.",
            "category": "development",
        },
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    req_id = req_resp.json()["id"]

    # 2. Seller 1 creates offer
    deadline = int(time.time()) + 86400 * 7
    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": 3.0,
            "delivery_deadline": deadline,
            "terms": "Custom Chainlink oracle node setup.",
        },
        headers={"Authorization": f"Bearer {seller1_token}"},
    )
    offer_id = offer_resp.json()["id"]

    # 3. Buyer accepts & creates deal
    await client.post(
        f"/api/v1/offers/{offer_id}/accept",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    await client.post(
        "/api/v1/escrow/deals",
        json={
            "offer_id": offer_id,
            "amount_eth": 3.0,
            "deadline_ts": deadline,
            "agreement_hash": "0x" + "b" * 64,
        },
        headers={"Authorization": f"Bearer {buyer_token}"},
    )

    # 4. Seller 1 lists deals -> exactly 1 deal with buyer display name
    s1_resp = await client.get(
        "/api/v1/escrow/deals/seller",
        headers={"Authorization": f"Bearer {seller1_token}"},
    )
    assert s1_resp.status_code == 200
    s1_data = s1_resp.json()
    assert s1_data["total"] == 1
    assert len(s1_data["items"]) == 1
    assert s1_data["items"][0]["amount_eth"] == 3.0
    assert s1_data["items"][0]["buyer_display_name"] == "User b_seller_dash@test.com"
    assert s1_data["items"][0]["requirement_title"] == "Decentralized Oracle Integration"

    # 5. Seller 2 lists deals -> 0 deals (strict seller isolation)
    s2_resp = await client.get(
        "/api/v1/escrow/deals/seller",
        headers={"Authorization": f"Bearer {seller2_token}"},
    )
    assert s2_resp.status_code == 200
    assert s2_resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_escrow_deal_detail_permissions_and_sync(client: AsyncClient):
    buyer_token = await _register_and_login(client, "b_detail@test.com", "buyer")
    seller_token = await _register_and_login(client, "s_detail@test.com", "seller")
    stranger_token = await _register_and_login(client, "stranger@test.com", "buyer")

    # Create requirement, offer, and deal
    req_resp = await client.post(
        "/api/v1/requirements",
        json={
            "title": "Private Vault Contract",
            "description": "Multi-sig vault implementation.",
            "category": "security",
        },
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    req_id = req_resp.json()["id"]

    deadline = int(time.time()) + 86400 * 2
    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": 1.25,
            "delivery_deadline": deadline,
            "terms": "Production tested contract code.",
        },
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    offer_id = offer_resp.json()["id"]

    await client.post(
        f"/api/v1/offers/{offer_id}/accept",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    deal_resp = await client.post(
        "/api/v1/escrow/deals",
        json={
            "offer_id": offer_id,
            "amount_eth": 1.25,
            "deadline_ts": deadline,
            "agreement_hash": "0x" + "c" * 64,
        },
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    deal_id = deal_resp.json()["id"]

    # 1. Buyer can access deal detail
    b_resp = await client.get(
        f"/api/v1/escrow/{deal_id}",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert b_resp.status_code == 200
    assert b_resp.json()["requirement_title"] == "Private Vault Contract"

    # 2. Seller can access deal detail
    s_resp = await client.get(
        f"/api/v1/escrow/{deal_id}",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert s_resp.status_code == 200
    assert s_resp.json()["requirement_title"] == "Private Vault Contract"

    # 3. Third-party is blocked with 403 Forbidden
    o_resp = await client.get(
        f"/api/v1/escrow/{deal_id}",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert o_resp.status_code == 403

    # 4. Third-party calling sync endpoint is blocked with 403 Forbidden
    sync_stranger = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert sync_stranger.status_code == 403

    # 5. Calling sync on a deal without chain_deal_id returns 400 Bad Request
    sync_buyer = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/sync",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert sync_buyer.status_code == 400
    assert "not linked to an on-chain deal ID" in sync_buyer.json()["detail"]

