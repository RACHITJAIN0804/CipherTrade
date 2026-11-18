"""
Unit and integration tests for EIP-4361 SIWE Wallet Ownership Verification (Phase 5D.1).

Covers all 12 required test cases:
 1. Valid SIWE signature & link
 2. Invalid signature
 3. Incorrect wallet address in message vs challenge
 4. Incorrect domain
 5. Incorrect chain ID
 6. Expired challenge
 7. Reused nonce (replay protection)
 8. Nonce belonging to another user
 9. Wallet already linked to another account
10. Concurrent verification race condition
11. Unauthenticated challenge request (401)
12. Unauthenticated verification request (401)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
import siwe

from app.models.models import WalletChallenge
from app.config import get_settings


# ─── Test Helpers ─────────────────────────────────────────────────────────────

async def _register_and_login(client: AsyncClient, email: str, name: str = "Test User") -> tuple[str, dict]:
    """Helper to register and login a test user, returning (jwt_token, user_dict)."""
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "display_name": name,
            "primary_role": "buyer",
            "password": "ValidPassword123!",
        },
    )
    assert reg.status_code == 201, reg.text
    user_info = reg.json()

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "ValidPassword123!"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    return token, user_info


def _sign_siwe_message(message: str, private_key: str) -> str:
    """Sign an EIP-4361 SIWE message using eth_account."""
    signable = encode_defunct(text=message)
    signed = Account.from_key(private_key).sign_message(signable)
    return signed.signature.hex()


# ─── 12 Required Test Cases ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_01_valid_siwe_challenge_and_verification(client: AsyncClient):
    """1. Valid SIWE challenge generation and signature verification links wallet."""
    token, user = await _register_and_login(client, "siwe_valid@test.com", "Valid User")
    acct = Account.create()
    checksum_addr = Web3.to_checksum_address(acct.address)

    # Step A: Request Challenge
    resp_chal = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": checksum_addr},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_chal.status_code == 200, resp_chal.text
    chal_data = resp_chal.json()
    assert "message" in chal_data
    assert "nonce" in chal_data
    assert chal_data["nonce"] in chal_data["message"]
    assert checksum_addr in chal_data["message"]

    # Step B: Sign challenge with private key
    sig = _sign_siwe_message(chal_data["message"], acct.key.hex())

    # Step C: Submit signature
    resp_ver = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": chal_data["message"], "signature": sig},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_ver.status_code == 200, resp_ver.text
    ver_data = resp_ver.json()
    assert ver_data["success"] is True
    assert ver_data["wallet_address"] == checksum_addr
    assert ver_data["user"]["wallet_address"] == checksum_addr

    # Step D: Confirm profile has wallet linked
    me_resp = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["wallet_address"] == checksum_addr


@pytest.mark.asyncio
async def test_02_invalid_signature_rejected(client: AsyncClient):
    """2. Submitting an invalid cryptographic signature must be rejected."""
    token, _ = await _register_and_login(client, "siwe_badsig@test.com", "Bad Sig")
    acct = Account.create()
    wrong_acct = Account.create()

    resp_chal = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
        headers={"Authorization": f"Bearer {token}"},
    )
    chal_data = resp_chal.json()

    # Sign with a DIFFERENT private key
    bad_sig = _sign_siwe_message(chal_data["message"], wrong_acct.key.hex())

    resp_ver = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": chal_data["message"], "signature": bad_sig},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_ver.status_code == 400
    assert "signature" in resp_ver.json()["detail"].lower()


@pytest.mark.asyncio
async def test_03_address_mismatch_rejected(client: AsyncClient):
    """3. Tampered message wallet address vs challenge address must be rejected."""
    token, _ = await _register_and_login(client, "siwe_addrmismatch@test.com", "Addr Mismatch")
    acct1 = Account.create()
    acct2 = Account.create()

    resp_chal = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct1.address},
        headers={"Authorization": f"Bearer {token}"},
    )
    chal_data = resp_chal.json()

    # Tamper message to replace acct1 address with acct2 address
    tampered_msg = chal_data["message"].replace(
        Web3.to_checksum_address(acct1.address),
        Web3.to_checksum_address(acct2.address),
    )
    sig2 = _sign_siwe_message(tampered_msg, acct2.key.hex())

    resp_ver = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": tampered_msg, "signature": sig2},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_ver.status_code == 400
    assert "address" in resp_ver.json()["detail"].lower()


@pytest.mark.asyncio
async def test_04_domain_mismatch_rejected(client: AsyncClient):
    """4. Message with mismatched domain or invalid format must be rejected."""
    token, _ = await _register_and_login(client, "siwe_domain@test.com", "Domain User")
    acct = Account.create()

    resp_chal = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
        headers={"Authorization": f"Bearer {token}"},
    )
    chal_data = resp_chal.json()

    # Parse message to find its actual domain, then replace with attacker-domain.evil
    orig_siwe = siwe.SiweMessage.from_message(chal_data["message"])
    tampered_msg = chal_data["message"].replace(orig_siwe.domain, "attacker-domain.evil")
    sig = _sign_siwe_message(tampered_msg, acct.key.hex())

    resp_ver = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": tampered_msg, "signature": sig},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_ver.status_code == 400



@pytest.mark.asyncio
async def test_05_chain_id_mismatch_rejected(client: AsyncClient):
    """5. Challenge message with unexpected Chain ID must be rejected."""
    token, _ = await _register_and_login(client, "siwe_chainid@test.com", "Chain ID User")
    acct = Account.create()

    resp_chal = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
        headers={"Authorization": f"Bearer {token}"},
    )
    chal_data = resp_chal.json()

    # Tamper chain ID in SIWE message (e.g. from 31337 to 1)
    settings = get_settings()
    tampered_msg = chal_data["message"].replace(f"Chain ID: {settings.chain_id}", "Chain ID: 1")
    sig = _sign_siwe_message(tampered_msg, acct.key.hex())

    resp_ver = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": tampered_msg, "signature": sig},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_ver.status_code == 400
    assert "chain id" in resp_ver.json()["detail"].lower()


@pytest.mark.asyncio
async def test_06_expired_challenge_rejected(client: AsyncClient, db_session):
    """6. Expired challenge must be rejected."""
    token, _ = await _register_and_login(client, "siwe_expired@test.com", "Expired User")
    acct = Account.create()

    resp_chal = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
        headers={"Authorization": f"Bearer {token}"},
    )
    chal_data = resp_chal.json()

    # Manually backdate the challenge in DB
    from sqlalchemy import select
    stmt = select(WalletChallenge).where(WalletChallenge.nonce == chal_data["nonce"])
    res = await db_session.execute(stmt)
    db_chal = res.scalar_one_or_none()
    assert db_chal is not None
    db_chal.expires_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    await db_session.commit()

    sig = _sign_siwe_message(chal_data["message"], acct.key.hex())

    resp_ver = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": chal_data["message"], "signature": sig},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_ver.status_code == 400
    assert "expired" in resp_ver.json()["detail"].lower()


@pytest.mark.asyncio
async def test_07_replay_nonce_reuse_rejected(client: AsyncClient):
    """7. Replaying an already-consumed challenge nonce must be rejected."""
    token, _ = await _register_and_login(client, "siwe_replay@test.com", "Replay User")
    acct = Account.create()

    resp_chal = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
        headers={"Authorization": f"Bearer {token}"},
    )
    chal_data = resp_chal.json()
    sig = _sign_siwe_message(chal_data["message"], acct.key.hex())

    # First verification: succeeds
    resp_ver1 = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": chal_data["message"], "signature": sig},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_ver1.status_code == 200

    # Second verification with exact same message and signature (replay): rejected
    resp_ver2 = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": chal_data["message"], "signature": sig},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_ver2.status_code == 400
    assert "consumed" in resp_ver2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_08_nonce_belonging_to_another_user_rejected(client: AsyncClient):
    """8. Submitting a challenge nonce issued to a different user must return 403."""
    token_a, _ = await _register_and_login(client, "user_a@test.com", "User A")
    token_b, _ = await _register_and_login(client, "user_b@test.com", "User B")
    acct = Account.create()

    # User A requests challenge
    resp_chal = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    chal_data = resp_chal.json()
    sig = _sign_siwe_message(chal_data["message"], acct.key.hex())

    # User B attempts to verify User A's challenge
    resp_ver = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": chal_data["message"], "signature": sig},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_ver.status_code == 403
    assert "not issued to this" in resp_ver.json()["detail"].lower()


@pytest.mark.asyncio
async def test_09_wallet_already_linked_to_another_account_rejected(client: AsyncClient):
    """9. A wallet already linked to User A cannot be requested or linked by User B."""
    token_a, _ = await _register_and_login(client, "owner_a@test.com", "Owner A")
    token_b, _ = await _register_and_login(client, "intruder_b@test.com", "Intruder B")
    acct = Account.create()

    # User A successfully verifies wallet
    chal_a = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    msg_a = chal_a.json()["message"]
    sig_a = _sign_siwe_message(msg_a, acct.key.hex())
    ver_a = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": msg_a, "signature": sig_a},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert ver_a.status_code == 200

    # User B attempts to request challenge for the already-linked wallet: 409 Conflict
    chal_b = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert chal_b.status_code == 409
    assert "already linked" in chal_b.json()["detail"].lower()


@pytest.mark.asyncio
async def test_10_concurrent_verification_race_condition(client: AsyncClient):
    """10. Concurrent verification requests with same nonce prevent duplicate consumption.

    SQLite Note: Under test runs with SQLite, write transactions are serialized at database level,
    while in production PostgreSQL row-level locks and atomic UPDATE conditionals are used.
    In both environments, the atomic UPDATE ensures that exactly one concurrent request consumes
    the nonce and links the account, while any competing concurrent request receives a clean 400 error.
    """
    token, _ = await _register_and_login(client, "race_nonce@test.com", "Race Nonce")
    acct = Account.create()

    # User requests challenge
    chal = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert chal.status_code == 200
    msg = chal.json()["message"]
    sig = _sign_siwe_message(msg, acct.key.hex())

    # Fire two concurrent verification requests with the EXACT SAME message & signature
    res1, res2 = await asyncio.gather(
        client.post(
            "/api/v1/auth/wallet/verify",
            json={"message": msg, "signature": sig},
            headers={"Authorization": f"Bearer {token}"},
        ),
        client.post(
            "/api/v1/auth/wallet/verify",
            json={"message": msg, "signature": sig},
            headers={"Authorization": f"Bearer {token}"},
        ),
    )

    status_codes = sorted([res1.status_code, res2.status_code])
    assert status_codes == [200, 400], f"Expected exactly one 200 and one 400, got: {status_codes}"
    failed_res = res1 if res1.status_code == 400 else res2
    assert "consumed" in failed_res.json()["detail"].lower() or "replay" in failed_res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_11_unauthenticated_challenge_rejected(client: AsyncClient):
    """11. Unauthenticated challenge requests must return 401 Unauthorized."""
    acct = Account.create()
    resp = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_12_unauthenticated_verify_rejected(client: AsyncClient):
    """12. Unauthenticated verify requests must return 401 Unauthorized."""
    resp = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": "dummy message", "signature": "0x" + "0" * 130},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_13_untrusted_siwe_domain_rejected(client: AsyncClient):
    """13. Untrusted Host header domain must be rejected before challenge creation/verification."""
    token, _ = await _register_and_login(client, "untrusted_domain@test.com", "Untrusted Domain")
    acct = Account.create()

    # Untrusted Host header on challenge request
    resp_chal = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
        headers={
            "Authorization": f"Bearer {token}",
            "Host": "evil-phishing-site.com",
        },
    )
    assert resp_chal.status_code == 400
    assert "untrusted host domain" in resp_chal.json()["detail"].lower()

    # Untrusted Host header on verify request
    resp_ver = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": "dummy message long enough for pydantic", "signature": "0x" + "0" * 130},
        headers={
            "Authorization": f"Bearer {token}",
            "Host": "evil-phishing-site.com",
        },
    )
    assert resp_ver.status_code == 400
    assert "untrusted host domain" in resp_ver.json()["detail"].lower()


@pytest.mark.asyncio
async def test_14_expired_challenge_cleanup_utility(db_session):
    """14. cleanup_expired_challenges removes expired unconsumed challenges without affecting active or consumed."""
    from sqlalchemy import select
    from app.services.wallet_service import cleanup_expired_challenges
    from app.models.models import User, UserRole, WalletChallenge
    from app.core.security import hash_password
    import uuid

    # Create dummy user
    u = User(
        id=uuid.uuid4(),
        email="cleanup_test@example.com",
        display_name="Cleanup User",
        hashed_password=hash_password("password"),
        primary_role=UserRole.BUYER,
    )
    db_session.add(u)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    old_time = now - timedelta(hours=3)

    # 1. Expired and unconsumed (should be deleted)
    c_expired = WalletChallenge(
        id=uuid.uuid4(),
        user_id=u.id,
        wallet_address="0x" + "1" * 40,
        nonce="nonce_expired_12345",
        message="msg",
        chain_id=31337,
        expires_at=old_time,
        used_at=None,
        created_at=old_time - timedelta(minutes=5),
    )
    # 2. Expired but consumed (should be PRESERVED for audit trail)
    c_consumed = WalletChallenge(
        id=uuid.uuid4(),
        user_id=u.id,
        wallet_address="0x" + "2" * 40,
        nonce="nonce_consumed_12345",
        message="msg",
        chain_id=31337,
        expires_at=old_time,
        used_at=old_time,
        created_at=old_time - timedelta(minutes=5),
    )
    # 3. Active / unexpired (should be PRESERVED)
    c_active = WalletChallenge(
        id=uuid.uuid4(),
        user_id=u.id,
        wallet_address="0x" + "3" * 40,
        nonce="nonce_active_12345",
        message="msg",
        chain_id=31337,
        expires_at=now + timedelta(minutes=10),
        used_at=None,
        created_at=now,
    )

    db_session.add_all([c_expired, c_consumed, c_active])
    await db_session.commit()

    # Run cleanup with older_than=1 hour
    deleted_count = await cleanup_expired_challenges(db_session, older_than=timedelta(hours=1))
    assert deleted_count == 1

    # Verify state of remaining challenges
    stmt = select(WalletChallenge)
    remaining = (await db_session.execute(stmt)).scalars().all()
    remaining_nonces = {c.nonce for c in remaining}
    assert "nonce_expired_12345" not in remaining_nonces
    assert "nonce_consumed_12345" in remaining_nonces
    assert "nonce_active_12345" in remaining_nonces


@pytest.mark.asyncio
async def test_15_concurrent_claim_same_wallet_conflict(client: AsyncClient):
    """15. Concurrent verification of two users attempting to claim the exact same wallet address."""
    token_a, _ = await _register_and_login(client, "concurrent_a@test.com", "User A")
    token_b, _ = await _register_and_login(client, "concurrent_b@test.com", "User B")
    acct = Account.create()

    # Both users request challenge for the same candidate wallet
    chal_a = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert chal_a.status_code == 200
    msg_a = chal_a.json()["message"]
    sig_a = _sign_siwe_message(msg_a, acct.key.hex())

    chal_b = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": acct.address},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert chal_b.status_code == 200
    msg_b = chal_b.json()["message"]
    sig_b = _sign_siwe_message(msg_b, acct.key.hex())

    # Concurrently submit both verifications
    res_a, res_b = await asyncio.gather(
        client.post(
            "/api/v1/auth/wallet/verify",
            json={"message": msg_a, "signature": sig_a},
            headers={"Authorization": f"Bearer {token_a}"},
        ),
        client.post(
            "/api/v1/auth/wallet/verify",
            json={"message": msg_b, "signature": sig_b},
            headers={"Authorization": f"Bearer {token_b}"},
        ),
    )

    statuses = sorted([res_a.status_code, res_b.status_code])
    assert statuses == [200, 409], f"Expected one 200 and one 409 Conflict, got: {statuses}"
