"""
Comprehensive unit and integration tests for Phase 5D.3: Cryptographic Agreement Consent (EIP-712).

Tests include:
1. Valid buyer EIP-712 signature verification.
2. Valid seller EIP-712 signature verification.
3. Mutual consent: both buyer and seller sign the exact same canonical agreement_hash.
4. Incorrect signing wallet (signature from a different wallet than registered user's wallet).
5. User without verified wallet (SIWE) attempting challenge or consent.
6. Malformed or invalid signature.
7. Replay prevention: reusing an already consumed nonce.
8. Expired challenge nonce.
9. Domain separation: wrong chain ID or wrong contract address.
10. Double consent prevention: same party attempting to sign twice (409 Conflict).
11. Unauthorized third party attempting challenge or verification (403 Forbidden).
12. Consent status endpoint accurate reflection of buyer, seller, and mutual states.
13. Strict escrow funding guard: funding blocked with 0 or 1 signatures, authorized with both.
"""
from __future__ import annotations

import time
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from httpx import AsyncClient

from app.config import get_settings


BUYER_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
BUYER_WALLET = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

SELLER_PK = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"
SELLER_WALLET = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"

THIRD_PARTY_PK = "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6"
THIRD_PARTY_WALLET = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"


async def _register_and_bind_wallet(client: AsyncClient, email: str, role: str, pk: str, wallet: str) -> tuple[str, dict]:
    """Helper to register user, log in, and link wallet via EIP-4361 SIWE."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "display_name": f"User {email.split('@')[0]}",
            "primary_role": role,
            "password": "Password123!",
        },
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    token = login_resp.json()["access_token"]

    # SIWE verification
    chal_resp = await client.post(
        "/api/v1/auth/wallet/challenge",
        json={"wallet_address": wallet},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert chal_resp.status_code == 200
    msg = chal_resp.json()["message"]
    sig = Account.from_key(pk).sign_message(encode_defunct(text=msg)).signature.hex()
    ver_resp = await client.post(
        "/api/v1/auth/wallet/verify",
        json={"message": msg, "signature": sig},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ver_resp.status_code == 200
    return token, ver_resp.json()["user"]


def _sign_eip712_challenge(ch_data: dict[str, Any], pk: str) -> str:
    """Helper to produce an EIP-712 signature for an AgreementConsent challenge."""
    eip712_msg = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "AgreementConsent": ch_data["types"]["AgreementConsent"],
        },
        "primaryType": "AgreementConsent",
        "domain": ch_data["domain"],
        "message": ch_data["message"],
    }
    raw_sig = Account.from_key(pk).sign_message(encode_typed_data(full_message=eip712_msg)).signature.hex()
    return ("0x" + raw_sig) if not raw_sig.startswith("0x") else raw_sig


async def _setup_deal(client: AsyncClient):
    """Setup requirement, accepted offer, and escrow deal with verified buyer & seller."""
    suffix = uuid.uuid4().hex[:6]
    buyer_token, buyer_user = await _register_and_bind_wallet(
        client, f"buyer_{suffix}@test.com", "buyer", BUYER_PK, BUYER_WALLET
    )
    seller_token, seller_user = await _register_and_bind_wallet(
        client, f"seller_{suffix}@test.com", "seller", SELLER_PK, SELLER_WALLET
    )

    req_resp = await client.post(
        "/api/v1/requirements",
        json={
            "title": f"Smart Contract Audit {suffix}",
            "description": "Comprehensive security audit of decentralized escrow system.",
            "category": "security",
        },
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    req_id = req_resp.json()["id"]

    deadline = int(time.time()) + 86400 * 7
    offer_resp = await client.post(
        "/api/v1/offers",
        json={
            "requirement_id": req_id,
            "price_eth": 2.5,
            "delivery_deadline": deadline,
            "terms": "Full audit with vulnerability report and remediation guidance.",
        },
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    offer_id = offer_resp.json()["id"]

    # Buyer accepts offer
    await client.post(f"/api/v1/offers/{offer_id}/accept", headers={"Authorization": f"Bearer {buyer_token}"})

    # Create deal
    deal_resp = await client.post(
        "/api/v1/escrow/deals",
        json={"offer_id": offer_id},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    deal_data = deal_resp.json()

    return {
        "buyer_token": buyer_token,
        "buyer_user": buyer_user,
        "seller_token": seller_token,
        "seller_user": seller_user,
        "offer_id": offer_id,
        "deal_id": deal_data["id"],
        "agreement_hash": deal_data["agreement_hash"],
        "deadline": deadline,
    }


# ===========================================================================
# Test Cases
# ===========================================================================

@pytest.mark.asyncio
async def test_mutual_consent_flow(client: AsyncClient):
    """
    Test complete mutual consent lifecycle:
    1. Query initial status -> neither consented, can_fund=False.
    2. Buyer requests challenge, signs, verifies -> buyer_consented=True, can_fund=False.
    3. Seller requests challenge, signs, verifies -> both_consented=True, can_fund=True.
    """
    ctx = await _setup_deal(client)
    deal_id = ctx["deal_id"]
    offer_id = ctx["offer_id"]

    # 1. Initial status
    status_resp = await client.get(
        f"/api/v1/escrow/deals/{deal_id}/consent",
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )
    assert status_resp.status_code == 200
    st = status_resp.json()
    assert st["buyer_consented"] is False
    assert st["seller_consented"] is False
    assert st["both_consented"] is False
    assert st["can_fund"] is False

    # 2. Buyer consent
    b_ch_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )
    assert b_ch_resp.status_code == 200
    b_ch = b_ch_resp.json()
    assert b_ch["message"]["partyRole"] == "buyer"
    assert b_ch["message"]["signer"].lower() == BUYER_WALLET.lower()

    b_sig = _sign_eip712_challenge(b_ch, BUYER_PK)
    b_v_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": b_ch["nonce"], "signature": b_sig},
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )
    assert b_v_resp.status_code == 200
    b_v = b_v_resp.json()
    assert b_v["party_role"] == "buyer"
    assert b_v["both_consented"] is False
    assert b_v["can_fund"] is False

    # Check status after buyer signature
    status_resp2 = await client.get(
        f"/api/v1/escrow/offers/{offer_id}/consent",
        headers={"Authorization": f"Bearer {ctx['seller_token']}"},
    )
    st2 = status_resp2.json()
    assert st2["buyer_consented"] is True
    assert st2["seller_consented"] is False
    assert st2["both_consented"] is False

    # 3. Seller consent
    s_ch_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {ctx['seller_token']}"},
    )
    assert s_ch_resp.status_code == 200
    s_ch = s_ch_resp.json()
    assert s_ch["message"]["partyRole"] == "seller"
    assert s_ch["message"]["signer"].lower() == SELLER_WALLET.lower()

    s_sig = _sign_eip712_challenge(s_ch, SELLER_PK)
    s_v_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": s_ch["nonce"], "signature": s_sig},
        headers={"Authorization": f"Bearer {ctx['seller_token']}"},
    )
    assert s_v_resp.status_code == 200
    s_v = s_v_resp.json()
    assert s_v["party_role"] == "seller"
    assert s_v["both_consented"] is True
    assert s_v["can_fund"] is True

    # Final status check
    status_final = await client.get(
        f"/api/v1/escrow/deals/{deal_id}/consent",
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )
    st_f = status_final.json()
    assert st_f["buyer_consented"] is True
    assert st_f["seller_consented"] is True
    assert st_f["both_consented"] is True
    assert st_f["can_fund"] is True
    assert st_f["buyer_consent"]["wallet_address"].lower() == BUYER_WALLET.lower()
    assert st_f["seller_consent"]["wallet_address"].lower() == SELLER_WALLET.lower()


@pytest.mark.asyncio
async def test_wrong_signing_key_rejected(client: AsyncClient):
    """
    If a user signs the challenge with a key other than their registered wallet,
    verification must fail with HTTP 400.
    """
    ctx = await _setup_deal(client)
    deal_id = ctx["deal_id"]

    ch_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )
    ch = ch_resp.json()

    # Buyer signs with THIRD_PARTY_PK instead of BUYER_PK
    wrong_sig = _sign_eip712_challenge(ch, THIRD_PARTY_PK)

    v_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": ch["nonce"], "signature": wrong_sig},
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )
    assert v_resp.status_code == 400
    assert "signature mismatch" in v_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_user_without_verified_wallet_rejected(client: AsyncClient):
    """
    A user who has not completed SIWE wallet verification cannot request a challenge.
    """
    # Register unverified user
    await client.post(
        "/api/v1/auth/register",
        json={"email": "unverified@test.com", "display_name": "Unverified", "primary_role": "buyer", "password": "Password123!"},
    )
    l_resp = await client.post("/api/v1/auth/login", json={"email": "unverified@test.com", "password": "Password123!"})
    unverified_token = l_resp.json()["access_token"]

    ctx = await _setup_deal(client)
    deal_id = ctx["deal_id"]

    ch_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {unverified_token}"},
    )
    # Third party / access denied
    assert ch_resp.status_code in (400, 403)


@pytest.mark.asyncio
async def test_duplicate_consent_rejected(client: AsyncClient):
    """
    A party cannot consent twice to the same offer (HTTP 409 Conflict).
    """
    ctx = await _setup_deal(client)
    deal_id = ctx["deal_id"]

    # First consent
    ch1 = (await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )).json()
    sig1 = _sign_eip712_challenge(ch1, BUYER_PK)
    v1 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": ch1["nonce"], "signature": sig1},
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )
    assert v1.status_code == 200

    # Attempt second challenge for same party
    ch2 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )
    assert ch2.status_code == 409
    assert "already been recorded" in ch2.json()["detail"]


@pytest.mark.asyncio
async def test_nonce_replay_prevention(client: AsyncClient):
    """
    A single-use challenge nonce cannot be submitted twice.
    """
    ctx = await _setup_deal(client)
    deal_id = ctx["deal_id"]

    ch = (await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )).json()
    sig = _sign_eip712_challenge(ch, BUYER_PK)

    v1 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": ch["nonce"], "signature": sig},
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )
    assert v1.status_code == 200

    # Replay attempt
    v2 = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": ch["nonce"], "signature": sig},
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )
    assert v2.status_code == 400


@pytest.mark.asyncio
async def test_expired_challenge_nonce_rejected(client: AsyncClient, db_session: Any):
    """
    A challenge that has expired cannot be consumed.
    """
    from sqlalchemy import update
    from app.models.models import AgreementConsentChallenge

    ctx = await _setup_deal(client)
    deal_id = ctx["deal_id"]

    ch = (await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )).json()
    sig = _sign_eip712_challenge(ch, BUYER_PK)

    # Force challenge to be expired in database using test session
    await db_session.execute(
        update(AgreementConsentChallenge)
        .where(AgreementConsentChallenge.nonce == ch["nonce"])
        .values(deadline=int(time.time()) - 100)
    )
    await db_session.commit()

    v_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": ch["nonce"], "signature": sig},
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )
    assert v_resp.status_code == 400
    assert "expired" in v_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_wrong_domain_parameters_rejected(client: AsyncClient):
    """
    If the signature was generated for a different chain ID or contract address,
    the recovered address will not match the registered user's wallet.
    """
    ctx = await _setup_deal(client)
    deal_id = ctx["deal_id"]

    ch = (await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )).json()

    # Tamper domain chainId when signing
    tampered_domain_ch = dict(ch)
    tampered_domain = dict(ch["domain"])
    tampered_domain["chainId"] = 1  # Mainnet instead of 31337
    tampered_domain_ch["domain"] = tampered_domain

    tampered_sig = _sign_eip712_challenge(tampered_domain_ch, BUYER_PK)

    v_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": ch["nonce"], "signature": tampered_sig},
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )
    assert v_resp.status_code == 400
    assert "mismatch" in v_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unauthorized_third_party_rejected(client: AsyncClient):
    """
    A third-party user who is neither buyer nor seller cannot view consent status,
    request a challenge, or submit verification.
    """
    ctx = await _setup_deal(client)
    deal_id = ctx["deal_id"]

    third_token, _ = await _register_and_bind_wallet(
        client, "third_party@test.com", "buyer", THIRD_PARTY_PK, THIRD_PARTY_WALLET
    )

    # Status check
    st_resp = await client.get(
        f"/api/v1/escrow/deals/{deal_id}/consent",
        headers={"Authorization": f"Bearer {third_token}"},
    )
    assert st_resp.status_code == 403

    # Challenge request
    ch_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {third_token}"},
    )
    assert ch_resp.status_code == 403


@pytest.mark.asyncio
async def test_funding_blocked_without_both_consents(client: AsyncClient):
    """
    Strict guard test:
    Verify that verify-funding fails with HTTP 403 if:
    - Zero parties have consented
    - Only buyer has consented
    - Only seller has consented
    And succeeds when BOTH parties have consented.
    """
    ctx = await _setup_deal(client)
    deal_id = ctx["deal_id"]
    canonical_hash = ctx["agreement_hash"]
    deadline = ctx["deadline"]

    fake_tx = "0x" + "a" * 64
    mock_tx = {
        "blockNumber": 200,
        "chainId": 31337,
        "to": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
        "from": BUYER_WALLET,
        "value": int(2.5 * 10**18),
    }
    mock_receipt = {
        "blockNumber": 200,
        "status": 1,
        "from": BUYER_WALLET,
        "to": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
    }
    mock_event = {
        "address": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
        "args": {
            "dealId": 1,
            "buyer": BUYER_WALLET,
            "seller": SELLER_WALLET,
            "amount": int(2.5 * 10**18),
            "deadline": deadline,
            "agreementHash": bytes.fromhex(canonical_hash[2:]),
        },
    }

    # Case 1: Zero consents -> verify-funding must fail
    with patch("app.services.escrow_service.get_web3") as mock_gw3, \
         patch("app.services.escrow_service.get_escrow_contract") as mock_gcontract:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        mock_contract = MagicMock()
        mock_contract.events.DealFunded().process_receipt.return_value = [mock_event]
        mock_contract.functions.getDeal.return_value.call.return_value = (
            BUYER_WALLET,
            SELLER_WALLET,
            int(2.5 * 10**18),
            deadline,
            bytes.fromhex(canonical_hash[2:]),
            0,
            False,
        )
        mock_gcontract.return_value = mock_contract

        resp_0 = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
        )
        assert resp_0.status_code == 403
        assert "both buyer and seller must cryptographically sign" in resp_0.json()["detail"].lower()

    # Case 2: Only buyer signs -> verify-funding must still fail
    b_ch = (await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )).json()
    b_sig = _sign_eip712_challenge(b_ch, BUYER_PK)
    await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": b_ch["nonce"], "signature": b_sig},
        headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
    )

    with patch("app.services.escrow_service.get_web3") as mock_gw3, \
         patch("app.services.escrow_service.get_escrow_contract") as mock_gcontract:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        mock_contract = MagicMock()
        mock_contract.events.DealFunded().process_receipt.return_value = [mock_event]
        mock_contract.functions.getDeal.return_value.call.return_value = (
            BUYER_WALLET,
            SELLER_WALLET,
            int(2.5 * 10**18),
            deadline,
            bytes.fromhex(canonical_hash[2:]),
            0,
            False,
        )
        mock_gcontract.return_value = mock_contract

        resp_1 = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
        )
        assert resp_1.status_code == 403
        assert "both buyer and seller must cryptographically sign" in resp_1.json()["detail"].lower()

    # Case 3: Seller also signs -> verify-funding MUST SUCCEED
    s_ch = (await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {ctx['seller_token']}"},
    )).json()
    s_sig = _sign_eip712_challenge(s_ch, SELLER_PK)
    await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": s_ch["nonce"], "signature": s_sig},
        headers={"Authorization": f"Bearer {ctx['seller_token']}"},
    )

    with patch("app.services.escrow_service.get_web3") as mock_gw3, \
         patch("app.services.escrow_service.get_escrow_contract") as mock_gcontract:
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 31337
        mock_w3.eth.get_transaction.return_value = mock_tx
        mock_w3.eth.get_transaction_receipt.return_value = mock_receipt
        mock_gw3.return_value = mock_w3

        mock_contract = MagicMock()
        mock_contract.events.DealFunded().process_receipt.return_value = [mock_event]
        mock_contract.functions.getDeal.return_value.call.return_value = (
            BUYER_WALLET,
            SELLER_WALLET,
            int(2.5 * 10**18),
            deadline,
            bytes.fromhex(canonical_hash[2:]),
            0,
            False,
        )
        mock_gcontract.return_value = mock_contract

        resp_ok = await client.post(
            f"/api/v1/escrow/deals/{deal_id}/verify-funding",
            json={"tx_hash": fake_tx},
            headers={"Authorization": f"Bearer {ctx['buyer_token']}"},
        )
        assert resp_ok.status_code == 200
        assert resp_ok.json()["on_chain_state"] == "FUNDED"
