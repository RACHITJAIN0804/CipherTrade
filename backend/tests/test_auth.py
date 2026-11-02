"""Tests for authentication endpoints."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_buyer(client: AsyncClient):
    resp = await client.post("/api/v1/auth/register", json={
        "email": "buyer@example.com",
        "display_name": "Alice Buyer",
        "primary_role": "buyer",
        "password": "Secur3Pass!",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "buyer@example.com"
    assert body["primary_role"] == "buyer"
    assert "hashed_password" not in body
    assert "password" not in body


@pytest.mark.asyncio
async def test_register_seller(client: AsyncClient):
    resp = await client.post("/api/v1/auth/register", json={
        "email": "seller@example.com",
        "display_name": "Bob Seller",
        "primary_role": "seller",
        "password": "Secur3Pass!",
    })
    assert resp.status_code == 201
    assert resp.json()["primary_role"] == "seller"


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    payload = {
        "email": "dup@example.com",
        "display_name": "Dup",
        "primary_role": "buyer",
        "password": "Secur3Pass!",
    }
    await client.post("/api/v1/auth/register", json=payload)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_register_weak_password(client: AsyncClient):
    resp = await client.post("/api/v1/auth/register", json={
        "email": "weak@example.com",
        "display_name": "Weak",
        "primary_role": "buyer",
        "password": "short",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_all_digits_password(client: AsyncClient):
    resp = await client.post("/api/v1/auth/register", json={
        "email": "digits@example.com",
        "display_name": "Digits",
        "primary_role": "buyer",
        "password": "12345678",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    await client.post("/api/v1/auth/register", json={
        "email": "login@example.com",
        "display_name": "Login User",
        "primary_role": "buyer",
        "password": "ValidPass1!",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "login@example.com",
        "password": "ValidPass1!",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    await client.post("/api/v1/auth/register", json={
        "email": "loginfail@example.com",
        "display_name": "Fail User",
        "primary_role": "buyer",
        "password": "ValidPass1!",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "loginfail@example.com",
        "password": "WrongPassword",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient):
    resp = await client.post("/api/v1/auth/login", json={
        "email": "nobody@example.com",
        "password": "ValidPass1!",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_without_token(client: AsyncClient):
    resp = await client.get("/api/v1/users/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_me(client: AsyncClient):
    await client.post("/api/v1/auth/register", json={
        "email": "me@example.com",
        "display_name": "Me User",
        "primary_role": "buyer",
        "password": "ValidPass1!",
    })
    login = await client.post("/api/v1/auth/login", json={
        "email": "me@example.com",
        "password": "ValidPass1!",
    })
    token = login.json()["access_token"]
    resp = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"

