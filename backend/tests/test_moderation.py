"""Tests for report and block moderation endpoints."""
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
async def test_file_report(client: AsyncClient):
    token_a, _ = await _register_and_login(client, "reporter@test.com")
    _, user_b_id = await _register_and_login(client, "reported@test.com")

    resp = await client.post("/api/v1/moderation/reports", json={
        "reported_user_id": user_b_id,
        "reason": "This user sent spam messages repeatedly.",
    }, headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["reported_user_id"] == user_b_id
    assert body["status"] == "open"


@pytest.mark.asyncio
async def test_cannot_report_yourself(client: AsyncClient):
    token_a, user_a_id = await _register_and_login(client, "self_report@test.com")

    resp = await client.post("/api/v1/moderation/reports", json={
        "reported_user_id": user_a_id,
        "reason": "Reporting myself for some reason.",
    }, headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_my_reports(client: AsyncClient):
    token_a, _ = await _register_and_login(client, "list_reporter@test.com")
    _, user_b_id = await _register_and_login(client, "list_reported@test.com")

    await client.post("/api/v1/moderation/reports", json={
        "reported_user_id": user_b_id,
        "reason": "Repeated spam and harassment over multiple days.",
    }, headers={"Authorization": f"Bearer {token_a}"})

    resp = await client.get("/api/v1/moderation/reports", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_block_user(client: AsyncClient):
    token_a, _ = await _register_and_login(client, "blocker@test.com")
    _, user_b_id = await _register_and_login(client, "blockee@test.com")

    resp = await client.post("/api/v1/moderation/blocks", json={
        "blocked_user_id": user_b_id,
    }, headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 201
    assert resp.json()["blocked_id"] == user_b_id


@pytest.mark.asyncio
async def test_cannot_block_yourself(client: AsyncClient):
    token_a, user_a_id = await _register_and_login(client, "self_blocker@test.com")

    resp = await client.post("/api/v1/moderation/blocks", json={
        "blocked_user_id": user_a_id,
    }, headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_cannot_block_twice(client: AsyncClient):
    token_a, _ = await _register_and_login(client, "double_blocker@test.com")
    _, user_b_id = await _register_and_login(client, "double_blockee@test.com")

    payload = {"blocked_user_id": user_b_id}
    await client.post("/api/v1/moderation/blocks", json=payload, headers={"Authorization": f"Bearer {token_a}"})
    resp = await client.post("/api/v1/moderation/blocks", json=payload, headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_unblock_user(client: AsyncClient):
    token_a, _ = await _register_and_login(client, "unblock_a@test.com")
    _, user_b_id = await _register_and_login(client, "unblock_b@test.com")

    await client.post("/api/v1/moderation/blocks", json={"blocked_user_id": user_b_id},
                      headers={"Authorization": f"Bearer {token_a}"})

    resp = await client.delete(
        f"/api/v1/moderation/blocks/{user_b_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 204

    # Should now be gone
    blocks = await client.get("/api/v1/moderation/blocks", headers={"Authorization": f"Bearer {token_a}"})
    assert blocks.json() == []


@pytest.mark.asyncio
async def test_list_my_blocks(client: AsyncClient):
    token_a, _ = await _register_and_login(client, "list_blocker@test.com")
    _, user_b_id = await _register_and_login(client, "list_blockee@test.com")

    await client.post("/api/v1/moderation/blocks", json={"blocked_user_id": user_b_id},
                      headers={"Authorization": f"Bearer {token_a}"})

    resp = await client.get("/api/v1/moderation/blocks", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1

