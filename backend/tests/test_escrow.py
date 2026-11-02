"""Tests for escrow deal references and contract metadata."""
from __future__ import annotations

import time
import pytest
from httpx import AsyncClient


async def _register_and_login(client: AsyncClient, email: str, role: str) -> str:
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "display_name": f"User {email}",
        "primary_role": role,
        "password": "ValidPass1!",
    })
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": "ValidPass1!"})
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_contract_info(client: AsyncClient):
    token = await _register_and_login(client, "buyer_escrow_info@test.com", "buyer")
    resp = await client.get("/api/v1/escrow/contract-info", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert "rpc_url" in body
    assert "configured" in body


@pytest.mark.asyncio
async def test_create_and_get_escrow_deal(client: AsyncClient):
    buyer_token = await _register_and_login(client, "buyer_deal@test.com", "buyer")
    seller_token = await _register_and_login(client, "seller_deal@test.com", "seller")
    other_token = await _register_and_login(client, "rando_deal@test.com", "buyer")

    # 1. Buyer creates requirement
    req_resp = await client.post("/api/v1/requirements", json={
        "title": "Escrow test requirement",
        "description": "Full contract test for escrow deal references.",
        "category": "blockchain",
    }, headers={"Authorization": f"Bearer {buyer_token}"})
    req_id = req_resp.json()["id"]

    # 2. Seller submits offer
    deadline = int(time.time()) + 86400 * 3
    offer_resp = await client.post("/api/v1/offers", json={
        "requirement_id": req_id,
        "price_eth": 1.5,
        "delivery_deadline": deadline,
        "terms": "Escrow test delivery in three days.",
    }, headers={"Authorization": f"Bearer {seller_token}"})
    offer_id = offer_resp.json()["id"]

    # 3. Cannot create escrow deal for pending offer
    bad_deal_resp = await client.post("/api/v1/escrow/deals", json={
        "offer_id": offer_id,
        "amount_eth": 1.5,
        "deadline_ts": deadline,
    }, headers={"Authorization": f"Bearer {buyer_token}"})
    assert bad_deal_resp.status_code == 400

    # 4. Buyer accepts offer
    await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {buyer_token}"})

    # 5. Buyer creates escrow deal record
    deal_resp = await client.post("/api/v1/escrow/deals", json={
        "offer_id": offer_id,
        "amount_eth": 1.5,
        "deadline_ts": deadline,
        "agreement_hash": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
    }, headers={"Authorization": f"Bearer {buyer_token}"})
    assert deal_resp.status_code == 201
    deal = deal_resp.json()
    assert deal["amount_eth"] == 1.5
    deal_id = deal["id"]

    # 6. Buyer and seller can view the deal
    resp_b = await client.get(f"/api/v1/escrow/{deal_id}", headers={"Authorization": f"Bearer {buyer_token}"})
    assert resp_b.status_code == 200

    resp_s = await client.get(f"/api/v1/escrow/{deal_id}", headers={"Authorization": f"Bearer {seller_token}"})
    assert resp_s.status_code == 200

    # 7. Unauthorized third party cannot view the deal
    resp_o = await client.get(f"/api/v1/escrow/{deal_id}", headers={"Authorization": f"Bearer {other_token}"})
    assert resp_o.status_code == 403

