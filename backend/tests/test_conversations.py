"""Tests for conversation initiation and block enforcement."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _register_and_login(client: AsyncClient, email: str, role: str = "buyer") -> tuple[str, str]:
    resp = await client.post("/api/v1/auth/register", json={
        "email": email,
        "display_name": f"User {email}",
        "primary_role": role,
        "password": "ValidPass1!",
    })
    user_id = resp.json()["id"]
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": "ValidPass1!"})
    return login.json()["access_token"], user_id


@pytest.mark.asyncio
async def test_initiate_conversation(client: AsyncClient):
    token_a, _ = await _register_and_login(client, "alice_conv@test.com")
    _, user_b_id = await _register_and_login(client, "bob_conv@test.com", "seller")

    resp = await client.post(
        "/api/v1/conversations",
        json={"recipient_id": user_b_id},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["recipient_id"] == user_b_id
    conv_id = body["id"]

    # Re-calling should return the existing conversation
    resp_again = await client.post(
        "/api/v1/conversations",
        json={"recipient_id": user_b_id},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_again.status_code == 201
    assert resp_again.json()["id"] == conv_id


@pytest.mark.asyncio
async def test_cannot_start_conversation_with_self(client: AsyncClient):
    token_a, user_a_id = await _register_and_login(client, "self_conv@test.com")

    resp = await client.post(
        "/api/v1/conversations",
        json={"recipient_id": user_a_id},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_blocked_user_cannot_initiate_conversation(client: AsyncClient):
    token_a, user_a_id = await _register_and_login(client, "blocker_conv@test.com")
    token_b, _ = await _register_and_login(client, "blocked_conv@test.com", "seller")

    # User A blocks User B
    resp_block = await client.post(
        "/api/v1/moderation/blocks",
        json={"blocked_user_id": _},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_block.status_code == 201

    # Blocked User B tries to initiate a conversation with User A -> should be forbidden (403)
    resp_conv = await client.post(
        "/api/v1/conversations",
        json={"recipient_id": user_a_id},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_conv.status_code == 403
    assert "cannot initiate a conversation" in resp_conv.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_and_get_conversations(client: AsyncClient):
    token_a, user_a_id = await _register_and_login(client, "user_a_list@test.com")
    token_b, user_b_id = await _register_and_login(client, "user_b_list@test.com", "seller")

    create_resp = await client.post(
        "/api/v1/conversations",
        json={"recipient_id": user_b_id},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    conv_id = create_resp.json()["id"]

    # Both participants can list
    resp_a = await client.get("/api/v1/conversations", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_a.status_code == 200
    assert len(resp_a.json()) == 1

    resp_b = await client.get("/api/v1/conversations", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.status_code == 200
    assert len(resp_b.json()) == 1

    # Details
    detail = await client.get(f"/api/v1/conversations/{conv_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert detail.status_code == 200
    assert detail.json()["id"] == conv_id

