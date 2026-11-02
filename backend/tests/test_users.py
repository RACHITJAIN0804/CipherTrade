"""Tests for user search and profile endpoints."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _register_and_login(client: AsyncClient, email: str, display_name: str, role: str) -> str:
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "display_name": display_name,
        "primary_role": role,
        "password": "ValidPass1!",
    })
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": "ValidPass1!"})
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_search_buyers_by_display_name(client: AsyncClient):
    seller_token = await _register_and_login(client, "seller_search@test.com", "Bob Seller", "seller")
    await _register_and_login(client, "alice_buyer@test.com", "Alice Crypto", "buyer")
    await _register_and_login(client, "charlie_buyer@test.com", "Charlie Web3", "buyer")
    await _register_and_login(client, "dave_seller@test.com", "Dave Crypto", "seller")

    # Search for "Crypto" with role="buyer"
    resp = await client.get(
        "/api/v1/users?q=Crypto&role=buyer",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["display_name"] == "Alice Crypto"
    assert results[0]["primary_role"] == "buyer"


@pytest.mark.asyncio
async def test_update_profile(client: AsyncClient):
    token = await _register_and_login(client, "update_me@test.com", "Old Name", "buyer")

    resp = await client.patch(
        "/api/v1/users/me",
        json={"display_name": "New Name", "wallet_address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "New Name"
    assert body["wallet_address"] == "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


@pytest.mark.asyncio
async def test_view_public_profile(client: AsyncClient):
    token = await _register_and_login(client, "viewer@test.com", "Viewer User", "buyer")
    resp_target = await client.post("/api/v1/auth/register", json={
        "email": "target@test.com",
        "display_name": "Target Profile",
        "primary_role": "seller",
        "password": "ValidPass1!",
    })
    target_id = resp_target.json()["id"]

    resp = await client.get(f"/api/v1/users/{target_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Target Profile"

