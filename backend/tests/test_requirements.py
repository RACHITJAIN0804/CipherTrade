"""Tests for requirements (buyer listings) endpoints."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _register_and_login(client: AsyncClient, email: str, role: str) -> str:
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "display_name": f"User {role}",
        "primary_role": role,
        "password": "ValidPass1!",
    })
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": "ValidPass1!"})
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_buyer_can_post_requirement(client: AsyncClient):
    token = await _register_and_login(client, "buyer1@test.com", "buyer")
    resp = await client.post("/api/v1/requirements", json={
        "title": "I need a React developer",
        "description": "Build a landing page with animations.",
        "category": "web-development",
        "keywords": "react, typescript, tailwind",
        "budget_min": 0.5,
        "budget_max": 1.0,
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "I need a React developer"
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_seller_cannot_post_requirement(client: AsyncClient):
    token = await _register_and_login(client, "seller1@test.com", "seller")
    resp = await client.post("/api/v1/requirements", json={
        "title": "Bad seller listing",
        "description": "This should not be allowed for sellers.",
        "category": "testing",
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_requirements_unauthenticated(client: AsyncClient):
    resp = await client.get("/api/v1/requirements")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_and_search_requirements(client: AsyncClient):
    buyer_token = await _register_and_login(client, "buyer2@test.com", "buyer")
    seller_token = await _register_and_login(client, "seller2@test.com", "seller")

    await client.post("/api/v1/requirements", json={
        "title": "Python FastAPI developer wanted",
        "description": "Need someone to build an API.",
        "category": "backend",
        "keywords": "python, fastapi",
    }, headers={"Authorization": f"Bearer {buyer_token}"})

    await client.post("/api/v1/requirements", json={
        "title": "React frontend needed",
        "description": "Build a dashboard UI.",
        "category": "frontend",
    }, headers={"Authorization": f"Bearer {buyer_token}"})

    # Seller can search
    resp = await client.get(
        "/api/v1/requirements?q=fastapi",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert "FastAPI" in body["items"][0]["title"]


@pytest.mark.asyncio
async def test_update_own_requirement(client: AsyncClient):
    token = await _register_and_login(client, "buyer3@test.com", "buyer")
    create_resp = await client.post("/api/v1/requirements", json={
        "title": "Original title here",
        "description": "Original description content.",
        "category": "misc",
    }, headers={"Authorization": f"Bearer {token}"})
    req_id = create_resp.json()["id"]

    update_resp = await client.patch(
        f"/api/v1/requirements/{req_id}",
        json={"title": "Updated title now"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["title"] == "Updated title now"


@pytest.mark.asyncio
async def test_cannot_update_other_buyers_requirement(client: AsyncClient):
    token1 = await _register_and_login(client, "buyer4@test.com", "buyer")
    token2 = await _register_and_login(client, "buyer5@test.com", "buyer")
    create_resp = await client.post("/api/v1/requirements", json={
        "title": "Buyer four requirement",
        "description": "Should only be editable by buyer4.",
        "category": "test",
    }, headers={"Authorization": f"Bearer {token1}"})
    req_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/requirements/{req_id}",
        json={"title": "Hijacked title"},
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_close_requirement(client: AsyncClient):
    token = await _register_and_login(client, "buyer6@test.com", "buyer")
    create_resp = await client.post("/api/v1/requirements", json={
        "title": "Will be closed soon",
        "description": "Closing this requirement shortly.",
        "category": "test",
    }, headers={"Authorization": f"Bearer {token}"})
    req_id = create_resp.json()["id"]

    resp = await client.delete(
        f"/api/v1/requirements/{req_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_view_active_requirements_by_particular_buyer(client: AsyncClient):
    token_b1 = await _register_and_login(client, "buyer_specific_1@test.com", "buyer")
    token_b2 = await _register_and_login(client, "buyer_specific_2@test.com", "buyer")
    token_seller = await _register_and_login(client, "seller_viewing@test.com", "seller")

    # Get buyer1's id
    me_resp = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token_b1}"})
    buyer1_id = me_resp.json()["id"]

    # Buyer1 posts 2 requirements
    await client.post("/api/v1/requirements", json={
        "title": "Buyer 1 requirement alpha",
        "description": "Details about alpha project.",
        "category": "design",
    }, headers={"Authorization": f"Bearer {token_b1}"})

    await client.post("/api/v1/requirements", json={
        "title": "Buyer 1 requirement beta",
        "description": "Details about beta project.",
        "category": "design",
    }, headers={"Authorization": f"Bearer {token_b1}"})

    # Buyer2 posts 1 requirement
    await client.post("/api/v1/requirements", json={
        "title": "Buyer 2 requirement gamma",
        "description": "Details about gamma project.",
        "category": "design",
    }, headers={"Authorization": f"Bearer {token_b2}"})

    # Seller queries buyer1's active requirements
    resp = await client.get(
        f"/api/v1/requirements/buyer/{buyer1_id}",
        headers={"Authorization": f"Bearer {token_seller}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    for item in body["items"]:
        assert item["buyer_id"] == buyer1_id

