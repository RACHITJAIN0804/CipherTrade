"""Tests for offer submission and management."""
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


async def _create_requirement(client: AsyncClient, token: str) -> str:
    resp = await client.post("/api/v1/requirements", json={
        "title": "Need a blockchain dev",
        "description": "Build a Solidity escrow contract.",
        "category": "blockchain",
    }, headers={"Authorization": f"Bearer {token}"})
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_seller_can_submit_offer(client: AsyncClient):
    buyer_token = await _register_and_login(client, "b_offer1@test.com", "buyer")
    seller_token = await _register_and_login(client, "s_offer1@test.com", "seller")
    req_id = await _create_requirement(client, buyer_token)

    deadline = int(time.time()) + 86400 * 7
    resp = await client.post("/api/v1/offers", json={
        "requirement_id": req_id,
        "price_eth": 0.5,
        "delivery_deadline": deadline,
        "terms": "I will build the escrow contract in 7 days with full tests.",
    }, headers={"Authorization": f"Bearer {seller_token}"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["price_eth"] == 0.5


@pytest.mark.asyncio
async def test_buyer_cannot_submit_offer(client: AsyncClient):
    buyer_token = await _register_and_login(client, "b_offer2@test.com", "buyer")
    req_id = await _create_requirement(client, buyer_token)

    deadline = int(time.time()) + 86400
    resp = await client.post("/api/v1/offers", json={
        "requirement_id": req_id,
        "price_eth": 1.0,
        "delivery_deadline": deadline,
        "terms": "Buyer tries to submit an offer illegally.",
    }, headers={"Authorization": f"Bearer {buyer_token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_buyer_can_view_their_offers(client: AsyncClient):
    buyer_token = await _register_and_login(client, "b_offer3@test.com", "buyer")
    seller_token = await _register_and_login(client, "s_offer3@test.com", "seller")
    req_id = await _create_requirement(client, buyer_token)

    deadline = int(time.time()) + 86400
    await client.post("/api/v1/offers", json={
        "requirement_id": req_id,
        "price_eth": 0.8,
        "delivery_deadline": deadline,
        "terms": "I will deliver within 24 hours with documentation.",
    }, headers={"Authorization": f"Bearer {seller_token}"})

    resp = await client.get(
        f"/api/v1/offers/requirement/{req_id}",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_other_user_cannot_view_offers(client: AsyncClient):
    buyer_token = await _register_and_login(client, "b_offer4@test.com", "buyer")
    other_token = await _register_and_login(client, "b_offer4b@test.com", "buyer")
    req_id = await _create_requirement(client, buyer_token)

    resp = await client.get(
        f"/api/v1/offers/requirement/{req_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_buyer_can_accept_offer(client: AsyncClient):
    buyer_token = await _register_and_login(client, "b_offer5@test.com", "buyer")
    seller_token = await _register_and_login(client, "s_offer5@test.com", "seller")
    req_id = await _create_requirement(client, buyer_token)

    deadline = int(time.time()) + 86400
    offer_resp = await client.post("/api/v1/offers", json={
        "requirement_id": req_id,
        "price_eth": 0.3,
        "delivery_deadline": deadline,
        "terms": "Complete delivery in one day with all assets.",
    }, headers={"Authorization": f"Bearer {seller_token}"})
    offer_id = offer_resp.json()["id"]

    accept_resp = await client.post(
        f"/api/v1/offers/{offer_id}/accept",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert accept_resp.status_code == 200
    assert accept_resp.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_buyer_can_reject_offer(client: AsyncClient):
    buyer_token = await _register_and_login(client, "b_offer6@test.com", "buyer")
    seller_token = await _register_and_login(client, "s_offer6@test.com", "seller")
    req_id = await _create_requirement(client, buyer_token)

    deadline = int(time.time()) + 86400
    offer_resp = await client.post("/api/v1/offers", json={
        "requirement_id": req_id,
        "price_eth": 0.9,
        "delivery_deadline": deadline,
        "terms": "Three weeks delivery with full source code and documentation.",
    }, headers={"Authorization": f"Bearer {seller_token}"})
    offer_id = offer_resp.json()["id"]

    reject_resp = await client.post(
        f"/api/v1/offers/{offer_id}/reject",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_seller_can_withdraw_offer(client: AsyncClient):
    buyer_token = await _register_and_login(client, "b_offer7@test.com", "buyer")
    seller_token = await _register_and_login(client, "s_offer7@test.com", "seller")
    req_id = await _create_requirement(client, buyer_token)

    deadline = int(time.time()) + 86400
    offer_resp = await client.post("/api/v1/offers", json={
        "requirement_id": req_id,
        "price_eth": 0.4,
        "delivery_deadline": deadline,
        "terms": "I can deliver in two days with all deliverables included.",
    }, headers={"Authorization": f"Bearer {seller_token}"})
    offer_id = offer_resp.json()["id"]

    withdraw_resp = await client.post(
        f"/api/v1/offers/{offer_id}/withdraw",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert withdraw_resp.status_code == 200
    assert withdraw_resp.json()["status"] == "withdrawn"


@pytest.mark.asyncio
async def test_cannot_accept_already_accepted_offer(client: AsyncClient):
    buyer_token = await _register_and_login(client, "b_offer8@test.com", "buyer")
    seller_token = await _register_and_login(client, "s_offer8@test.com", "seller")
    req_id = await _create_requirement(client, buyer_token)

    deadline = int(time.time()) + 86400
    offer_resp = await client.post("/api/v1/offers", json={
        "requirement_id": req_id,
        "price_eth": 0.6,
        "delivery_deadline": deadline,
        "terms": "Full delivery with tests and documentation within 48 hours.",
    }, headers={"Authorization": f"Bearer {seller_token}"})
    offer_id = offer_resp.json()["id"]

    await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {buyer_token}"})
    resp = await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {buyer_token}"})
    assert resp.status_code == 400

