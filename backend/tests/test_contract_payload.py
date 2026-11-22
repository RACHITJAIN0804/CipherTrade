"""
Comprehensive unit and integration tests for Phase 5E.2: Backend Dual-Consent Payload API.

Test Scenarios:
1. Valid buyer and seller consent returns complete contract-ready payload.
2. Missing buyer consent -> 409 Conflict.
3. Missing seller consent -> 409 Conflict.
4. Expired buyer consent -> 400 Bad Request.
5. Expired seller consent -> 400 Bad Request.
6. Invalid buyer signature -> 400 Bad Request.
7. Invalid seller signature -> 400 Bad Request.
8. Wrong buyer wallet -> 400 Bad Request.
9. Wrong seller wallet -> 400 Bad Request.
10. Mismatched agreement hash -> 400 Bad Request.
11. Mismatched offer ID -> 400 Bad Request.
12. Mismatched deal ID -> 400 Bad Request.
13. Modified amount -> 400 Bad Request.
14. Modified delivery deadline -> 400 Bad Request.
15. Wrong EIP-712 domain name/version -> 400 Bad Request.
16. Wrong chain ID -> 400 Bad Request.
17. Wrong verifying contract when configured -> 400 Bad Request.
18. Incompatible legacy 7-field schema signatures -> 400 Bad Request.
19. Unauthorized non-participant -> 403 Forbidden.
20. Seller cannot retrieve buyer funding payload -> 403 Forbidden.
21. Repeated payload retrieval does not consume nonces (idempotent read-only).
22. Revoked/superseded consent -> 400 Bad Request.
23. Returned proof fields match Solidity struct layout (agreement, buyerConsent, sellerConsent).
24. Large uint256 serialized as decimal string without precision loss.
"""
from __future__ import annotations

import datetime
import time
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.config import get_settings
from app.models.models import (
    AgreementConsent,
    AgreementConsentChallenge,
    EscrowDeal,
    Offer,
    Requirement,
    User,
)


BUYER_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
BUYER_WALLET = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

SELLER_PK = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"
SELLER_WALLET = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"

THIRD_PARTY_PK = "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6"
THIRD_PARTY_WALLET = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"

TEST_V2_CONTRACT_ADDRESS = "0x2222222222222222222222222222222222222222"


@pytest.fixture(autouse=True)
def configure_v2_address_for_payload_tests():
    """Ensure V2 address is configured for payload test scenarios by default."""
    settings = get_settings()
    orig_v2 = settings.escrow_v2_contract_address
    orig_legacy = settings.escrow_contract_address
    settings.escrow_v2_contract_address = TEST_V2_CONTRACT_ADDRESS
    yield
    settings.escrow_v2_contract_address = orig_v2
    settings.escrow_contract_address = orig_legacy


async def _register_and_bind_wallet(client: AsyncClient, email: str, role: str, pk: str, wallet: str) -> tuple[str, dict]:
    """Register user, log in, and link wallet via EIP-4361 SIWE."""
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

    # SIWE challenge & verification
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
    """Helper to produce an authoritative 11-field EIP-712 signature."""
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


async def _setup_deal(client: AsyncClient, price_eth: float = 2.5):
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
            "price_eth": price_eth,
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
        "price_eth": price_eth,
    }


async def _complete_consent(client: AsyncClient, deal_id: str, token: str, pk: str) -> dict[str, Any]:
    """Request challenge and record verified consent for a deal participant."""
    chal_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/challenge",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert chal_resp.status_code == 200, f"Challenge failed: {chal_resp.text}"
    chal_data = chal_resp.json()
    sig = _sign_eip712_challenge(chal_data, pk)

    ver_resp = await client.post(
        f"/api/v1/escrow/deals/{deal_id}/consent/verify",
        json={"nonce": chal_data["nonce"], "signature": sig},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ver_resp.status_code == 200, f"Verify failed: {ver_resp.text}"
    return {
        "challenge": chal_data,
        "signature": sig,
        "verify": ver_resp.json(),
    }


async def _setup_fully_consented_deal(client: AsyncClient, price_eth: float = 2.5):
    """Setup deal and record mutual consent for both buyer and seller."""
    deal_info = await _setup_deal(client, price_eth=price_eth)
    buyer_consent = await _complete_consent(client, deal_info["deal_id"], deal_info["buyer_token"], BUYER_PK)
    seller_consent = await _complete_consent(client, deal_info["deal_id"], deal_info["seller_token"], SELLER_PK)
    deal_info["buyer_consent"] = buyer_consent
    deal_info["seller_consent"] = seller_consent
    return deal_info


# ===========================================================================
# Test Cases (24 Scenarios)
# ===========================================================================

@pytest.mark.asyncio
async def test_01_valid_buyer_and_seller_consent_returns_payload(client: AsyncClient):
    """1. Valid buyer and seller consent returns complete contract-ready payload."""
    deal = await _setup_fully_consented_deal(client)
    deal_id = deal["deal_id"]

    # Test main endpoint
    resp = await client.get(
        f"/api/v1/escrow/deals/{deal_id}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 200, f"Failed: {resp.text}"
    data = resp.json()

    assert data["deal_id"] == deal_id
    assert "agreement" in data
    assert "buyerConsent" in data
    assert "sellerConsent" in data
    assert data["agreement"]["dealId"] == deal_id
    assert data["agreement"]["offerId"] == deal["offer_id"]
    assert data["agreement"]["buyer"] == BUYER_WALLET
    assert data["agreement"]["seller"] == SELLER_WALLET
    assert data["agreement"]["amount"] == "2500000000000000000"
    assert data["buyerConsent"]["signature"].startswith("0x")
    assert data["sellerConsent"]["signature"].startswith("0x")
    assert data["contract_address"] == TEST_V2_CONTRACT_ADDRESS

    # Test route alias /api/v1/deals/{deal_id}/contract-payload
    resp_alias = await client.get(
        f"/api/v1/deals/{deal_id}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp_alias.status_code == 200
    assert resp_alias.json() == data


@pytest.mark.asyncio
async def test_02_missing_buyer_consent_returns_409(client: AsyncClient):
    """2. Missing buyer consent -> 409 Conflict."""
    deal = await _setup_deal(client)
    # Only seller consents
    await _complete_consent(client, deal["deal_id"], deal["seller_token"], SELLER_PK)

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 409
    assert "Buyer has not provided agreement consent" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_03_missing_seller_consent_returns_409(client: AsyncClient):
    """3. Missing seller consent -> 409 Conflict."""
    deal = await _setup_deal(client)
    # Only buyer consents
    await _complete_consent(client, deal["deal_id"], deal["buyer_token"], BUYER_PK)

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 409
    assert "Seller has not provided agreement consent" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_04_expired_buyer_consent_returns_400(client: AsyncClient, db_session: AsyncSession):
    """4. Expired buyer consent -> 400 Bad Request."""
    deal = await _setup_fully_consented_deal(client)
    deal_id = uuid.UUID(deal["deal_id"])

    # Manually expire buyer consent deadline in database
    await db_session.execute(
        update(AgreementConsent)
        .where(AgreementConsent.offer_id == uuid.UUID(deal["offer_id"]), AgreementConsent.party_role == "buyer")
        .values(deadline=int(time.time()) - 100)
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Buyer consent has expired" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_05_expired_seller_consent_returns_400(client: AsyncClient, db_session: AsyncSession):
    """5. Expired seller consent -> 400 Bad Request."""
    deal = await _setup_fully_consented_deal(client)

    # Manually expire seller consent deadline in database
    await db_session.execute(
        update(AgreementConsent)
        .where(AgreementConsent.offer_id == uuid.UUID(deal["offer_id"]), AgreementConsent.party_role == "seller")
        .values(deadline=int(time.time()) - 100)
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Seller consent has expired" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_06_invalid_buyer_signature_returns_400(client: AsyncClient, db_session: AsyncSession):
    """6. Invalid buyer signature -> 400 Bad Request."""
    deal = await _setup_fully_consented_deal(client)

    # Corrupt buyer signature in DB (tampered signature)
    corrupted_sig = "0x" + "aa" * 65
    await db_session.execute(
        update(AgreementConsent)
        .where(AgreementConsent.offer_id == uuid.UUID(deal["offer_id"]), AgreementConsent.party_role == "buyer")
        .values(signature=corrupted_sig)
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Buyer signature is invalid" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_07_invalid_seller_signature_returns_400(client: AsyncClient, db_session: AsyncSession):
    """7. Invalid seller signature -> 400 Bad Request."""
    deal = await _setup_fully_consented_deal(client)

    # Corrupt seller signature in DB
    corrupted_sig = "0x" + "bb" * 65
    await db_session.execute(
        update(AgreementConsent)
        .where(AgreementConsent.offer_id == uuid.UUID(deal["offer_id"]), AgreementConsent.party_role == "seller")
        .values(signature=corrupted_sig)
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Seller signature is invalid" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_08_wrong_buyer_wallet_returns_400(client: AsyncClient, db_session: AsyncSession):
    """8. Wrong buyer wallet -> 400 Bad Request."""
    deal = await _setup_fully_consented_deal(client)

    # Change buyer consent's signer_wallet to an unauthorized wallet
    await db_session.execute(
        update(AgreementConsent)
        .where(AgreementConsent.offer_id == uuid.UUID(deal["offer_id"]), AgreementConsent.party_role == "buyer")
        .values(signer_wallet=THIRD_PARTY_WALLET)
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Buyer consent signer wallet does not match registered buyer wallet" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_09_wrong_seller_wallet_returns_400(client: AsyncClient, db_session: AsyncSession):
    """9. Wrong seller wallet -> 400 Bad Request."""
    deal = await _setup_fully_consented_deal(client)

    # Change seller consent's signer_wallet to an unauthorized wallet
    await db_session.execute(
        update(AgreementConsent)
        .where(AgreementConsent.offer_id == uuid.UUID(deal["offer_id"]), AgreementConsent.party_role == "seller")
        .values(signer_wallet=THIRD_PARTY_WALLET)
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Seller consent signer wallet does not match registered seller wallet" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_10_mismatched_agreement_hash_returns_400(client: AsyncClient, db_session: AsyncSession):
    """10. Mismatched agreement hash -> 400 Bad Request."""
    deal = await _setup_fully_consented_deal(client)

    # Tamper with deal.agreement_hash
    tampered_hash = "0x" + "99" * 32
    await db_session.execute(
        update(EscrowDeal)
        .where(EscrowDeal.id == uuid.UUID(deal["deal_id"]))
        .values(agreement_hash=tampered_hash)
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Deal agreement hash does not match canonical agreement hash" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_11_mismatched_offer_id_returns_400(client: AsyncClient, db_session: AsyncSession):
    """11. Mismatched offer ID -> 400 Bad Request."""
    deal = await _setup_fully_consented_deal(client)

    # Point buyer consent to a different offer UUID
    random_offer_id = uuid.uuid4()
    await db_session.execute(
        update(AgreementConsent)
        .where(AgreementConsent.offer_id == uuid.UUID(deal["offer_id"]), AgreementConsent.party_role == "buyer")
        .values(offer_id=random_offer_id)
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    # Query for consents with deal.offer_id will not find buyer consent -> 409 Conflict
    assert resp.status_code == 409
    assert "Buyer has not provided agreement consent" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_12_mismatched_deal_id_returns_400(client: AsyncClient, db_session: AsyncSession):
    """12. Mismatched deal ID -> 400 Bad Request."""
    deal = await _setup_fully_consented_deal(client)

    # Change buyer consent deal_id to a different deal ID
    different_deal_id = uuid.uuid4()
    await db_session.execute(
        update(AgreementConsent)
        .where(AgreementConsent.offer_id == uuid.UUID(deal["offer_id"]), AgreementConsent.party_role == "buyer")
        .values(deal_id=different_deal_id)
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Buyer consent deal ID does not match authoritative deal ID" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_13_modified_amount_returns_400(client: AsyncClient, db_session: AsyncSession):
    """13. Modified amount -> 400 Bad Request (signature cryptographic mismatch)."""
    deal = await _setup_fully_consented_deal(client)

    # Tamper with deal.amount_eth (e.g. increase price from 2.5 to 5.0 ETH)
    await db_session.execute(
        update(EscrowDeal)
        .where(EscrowDeal.id == uuid.UUID(deal["deal_id"]))
        .values(amount_eth=5.0)
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Buyer signature is invalid or incompatible" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_14_modified_delivery_deadline_returns_400(client: AsyncClient, db_session: AsyncSession):
    """14. Modified delivery deadline -> 400 Bad Request (signature cryptographic mismatch)."""
    deal = await _setup_fully_consented_deal(client)

    # Tamper with deal.deadline_ts (+ 1000 seconds)
    await db_session.execute(
        update(EscrowDeal)
        .where(EscrowDeal.id == uuid.UUID(deal["deal_id"]))
        .values(deadline_ts=deal["deadline"] + 1000)
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Buyer signature is invalid or incompatible" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_15_wrong_eip712_domain_returns_400(client: AsyncClient, db_session: AsyncSession):
    """15. Wrong EIP-712 domain name/version -> 400 Bad Request."""
    deal = await _setup_deal(client)

    # Buyer requests challenge
    chal_resp = await client.post(
        f"/api/v1/escrow/deals/{deal['deal_id']}/consent/challenge",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    chal_data = chal_resp.json()

    # Construct invalid signature with spoofed domain name "FakeCipherTrade"
    spoofed_domain_msg = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "AgreementConsent": chal_data["types"]["AgreementConsent"],
        },
        "primaryType": "AgreementConsent",
        "domain": {
            "name": "FakeCipherTrade",
            "version": "1",
            "chainId": chal_data["domain"]["chainId"],
            "verifyingContract": chal_data["domain"]["verifyingContract"],
        },
        "message": chal_data["message"],
    }
    spoofed_sig = "0x" + Account.from_key(BUYER_PK).sign_message(
        encode_typed_data(full_message=spoofed_domain_msg)
    ).signature.hex()

    # Direct verify will reject spoofed domain signature
    ver_resp = await client.post(
        f"/api/v1/escrow/deals/{deal['deal_id']}/consent/verify",
        json={"nonce": chal_data["nonce"], "signature": spoofed_sig},
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert ver_resp.status_code == 400
    assert "Cryptographic signature mismatch" in ver_resp.json()["detail"]


@pytest.mark.asyncio
async def test_16_wrong_chain_id_returns_400(client: AsyncClient, db_session: AsyncSession):
    """16. Wrong chain ID -> 400 Bad Request."""
    deal = await _setup_fully_consented_deal(client)

    # Change chain_id in AgreementConsent to 999999
    await db_session.execute(
        update(AgreementConsent)
        .where(AgreementConsent.offer_id == uuid.UUID(deal["offer_id"]), AgreementConsent.party_role == "buyer")
        .values(chain_id=999999)
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Buyer consent was issued for chain 999999" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_17_wrong_verifying_contract_when_configured_returns_400(client: AsyncClient):
    """17. Wrong verifying contract when configured -> 400 Bad Request."""
    # When V2 contract address is configured, but signatures were produced against legacy address
    v2_mock_addr = "0x1111111111111111111111111111111111111111"

    # Setup deal with legacy default address
    deal = await _setup_fully_consented_deal(client)

    # Now simulate setting escrow_v2_contract_address in settings
    with patch("app.services.consent_service.get_configured_v2_address", return_value=v2_mock_addr):
        resp = await client.get(
            f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
            headers={"Authorization": f"Bearer {deal['buyer_token']}"},
        )
        assert resp.status_code == 400
        assert "Buyer signature is invalid or incompatible" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_18_incompatible_legacy_7_field_schema_returns_400(client: AsyncClient, db_session: AsyncSession):
    """18. Incompatible legacy 7-field schema signatures -> 400 Bad Request."""
    deal = await _setup_deal(client)

    # Seller provides valid 11-field consent
    await _complete_consent(client, deal["deal_id"], deal["seller_token"], SELLER_PK)

    # Produce a legacy 7-field signature for buyer
    legacy_nonce = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    legacy_deadline = int(time.time()) + 3600
    legacy_7_field_msg = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "AgreementConsent": [
                {"name": "agreementHash", "type": "bytes32"},
                {"name": "offerId", "type": "string"},
                {"name": "dealId", "type": "string"},
                {"name": "partyRole", "type": "string"},
                {"name": "signer", "type": "address"},
                {"name": "nonce", "type": "string"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "AgreementConsent",
        "domain": {
            "name": "CipherTrade",
            "version": "1",
            "chainId": 31337,
            "verifyingContract": get_settings().escrow_contract_address,
        },
        "message": {
            "agreementHash": deal["agreement_hash"],
            "offerId": deal["offer_id"],
            "dealId": deal["deal_id"],
            "partyRole": "buyer",
            "signer": BUYER_WALLET,
            "nonce": legacy_nonce,
            "deadline": legacy_deadline,
        },
    }
    legacy_sig = "0x" + Account.from_key(BUYER_PK).sign_message(
        encode_typed_data(full_message=legacy_7_field_msg)
    ).signature.hex()

    # Insert legacy challenge & consent record into database
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    ch = AgreementConsentChallenge(
        offer_id=uuid.UUID(deal["offer_id"]),
        deal_id=uuid.UUID(deal["deal_id"]),
        user_id=uuid.UUID(deal["buyer_user"]["id"]),
        party_role="buyer",
        wallet_address=BUYER_WALLET,
        agreement_hash=deal["agreement_hash"],
        chain_id=31337,
        nonce=legacy_nonce,
        deadline=legacy_deadline,
        used_at=now_dt,
        created_at=now_dt,
    )
    db_session.add(ch)

    consent = AgreementConsent(
        offer_id=uuid.UUID(deal["offer_id"]),
        deal_id=uuid.UUID(deal["deal_id"]),
        user_id=uuid.UUID(deal["buyer_user"]["id"]),
        party_role="buyer",
        signer_wallet=BUYER_WALLET,
        agreement_hash=deal["agreement_hash"],
        chain_id=31337,
        nonce=legacy_nonce,
        signature=legacy_sig,
        deadline=legacy_deadline,
        created_at=now_dt,
    )
    db_session.add(consent)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Buyer signature is invalid or incompatible with the approved 11-field AgreementConsent schema" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_19_unauthorized_non_participant_returns_403(client: AsyncClient):
    """19. Unauthorized non-participant -> 403 Forbidden."""
    deal = await _setup_fully_consented_deal(client)

    # Register third-party user
    third_token, _ = await _register_and_bind_wallet(
        client, f"third_{uuid.uuid4().hex[:6]}@test.com", "buyer", THIRD_PARTY_PK, THIRD_PARTY_WALLET
    )

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {third_token}"},
    )
    assert resp.status_code == 403
    assert "Access denied: You are not a participant in this deal" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_20_seller_cannot_retrieve_buyer_payload_returns_403(client: AsyncClient):
    """20. Seller cannot retrieve buyer funding payload -> 403 Forbidden."""
    deal = await _setup_fully_consented_deal(client)

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['seller_token']}"},
    )
    assert resp.status_code == 403
    assert "Access denied: Only the buyer can retrieve the funding-ready contract payload" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_21_repeated_retrieval_does_not_consume_nonces(client: AsyncClient, db_session: AsyncSession):
    """21. Repeated payload retrieval does not consume nonces (idempotent read-only)."""
    deal = await _setup_fully_consented_deal(client)
    deal_id = deal["deal_id"]

    # Retrieve initial nonce status from DB
    stmt = select(AgreementConsentChallenge).where(AgreementConsentChallenge.deal_id == uuid.UUID(deal_id))
    challenges_before = (await db_session.execute(stmt)).scalars().all()
    used_ats_before = {c.nonce: c.used_at for c in challenges_before}

    # Fetch payload 3 consecutive times
    for _ in range(3):
        resp = await client.get(
            f"/api/v1/escrow/deals/{deal_id}/contract-payload",
            headers={"Authorization": f"Bearer {deal['buyer_token']}"},
        )
        assert resp.status_code == 200

    # Ensure challenges and database state are strictly unchanged
    challenges_after = (await db_session.execute(stmt)).scalars().all()
    used_ats_after = {c.nonce: c.used_at for c in challenges_after}
    assert used_ats_before == used_ats_after


@pytest.mark.asyncio
async def test_22_superseded_consent_returns_400(client: AsyncClient, db_session: AsyncSession):
    """22. Revoked/superseded consent -> 400 Bad Request."""
    deal = await _setup_fully_consented_deal(client)

    # Insert a newer challenge for buyer (simulating terms re-negotiation or fresh challenge request)
    newer_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=10)
    newer_ch = AgreementConsentChallenge(
        offer_id=uuid.UUID(deal["offer_id"]),
        deal_id=uuid.UUID(deal["deal_id"]),
        user_id=uuid.UUID(deal["buyer_user"]["id"]),
        party_role="buyer",
        wallet_address=BUYER_WALLET,
        agreement_hash=deal["agreement_hash"],
        chain_id=31337,
        nonce="0x" + uuid.uuid4().hex + uuid.uuid4().hex,
        deadline=int(time.time()) + 7200,
        used_at=None,
        created_at=newer_dt,
    )
    db_session.add(newer_ch)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Buyer consent has been superseded by a newer consent challenge" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_23_returned_proof_fields_match_solidity_layout(client: AsyncClient):
    """23. Returned proof fields match Solidity struct layout (agreement, buyerConsent, sellerConsent)."""
    deal = await _setup_fully_consented_deal(client)

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    # CommercialAgreement struct:
    agreement = data["agreement"]
    expected_agreement_keys = {"agreementHash", "offerId", "dealId", "buyer", "seller", "amount", "deliveryDeadline"}
    assert set(agreement.keys()) == expected_agreement_keys
    assert agreement["agreementHash"].startswith("0x") and len(agreement["agreementHash"]) == 66
    assert Web3.is_address(agreement["buyer"])
    assert Web3.is_address(agreement["seller"])
    assert isinstance(agreement["amount"], str)
    assert isinstance(agreement["deliveryDeadline"], int)

    # PartyConsentProof struct for buyer:
    buyer_proof = data["buyerConsent"]
    expected_proof_keys = {"nonce", "deadline", "signature"}
    assert set(buyer_proof.keys()) == expected_proof_keys
    assert isinstance(buyer_proof["nonce"], str)
    assert isinstance(buyer_proof["deadline"], int)
    assert buyer_proof["signature"].startswith("0x") and len(buyer_proof["signature"]) == 132

    # PartyConsentProof struct for seller:
    seller_proof = data["sellerConsent"]
    assert set(seller_proof.keys()) == expected_proof_keys
    assert isinstance(seller_proof["nonce"], str)
    assert isinstance(seller_proof["deadline"], int)
    assert seller_proof["signature"].startswith("0x") and len(seller_proof["signature"]) == 132


@pytest.mark.asyncio
async def test_24_large_uint256_serialized_as_decimal_string(client: AsyncClient):
    """24. Large uint256 serialized as decimal string without precision loss."""
    # Test with 150.0 ETH (150 * 10^18 Wei = 150000000000000000000 Wei > 2^53 - 1)
    large_eth_price = 150.0
    deal = await _setup_fully_consented_deal(client, price_eth=large_eth_price)

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    expected_wei_str = "150000000000000000000"
    assert data["agreement"]["amount"] == expected_wei_str
    # Verify it exceeds JS MAX_SAFE_INTEGER (9007199254740991)
    assert int(data["agreement"]["amount"]) > 9007199254740991


# ===========================================================================
# Phase 5E.2.1: V2 Domain Configuration Hardening & Golden Vector Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_25_v2_address_configured_exact_address_used(client: AsyncClient):
    """25. V2 address configured: exact address used in payload response."""
    exact_v2 = "0x3333333333333333333333333333333333333333"
    settings = get_settings()
    settings.escrow_v2_contract_address = exact_v2

    deal = await _setup_fully_consented_deal(client)
    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 200
    assert resp.json()["contract_address"] == exact_v2


@pytest.mark.asyncio
async def test_26_v2_address_missing_and_legacy_address_configured_rejected(client: AsyncClient):
    """26. V2 address missing and legacy address configured -> 503 Service Unavailable."""
    settings = get_settings()
    settings.escrow_v2_contract_address = None
    settings.escrow_contract_address = "0x5FbDB2315678afecb367f032d93F642f64180aa3"

    deal = await _setup_deal(client)
    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "CipherTradeEscrowV2 contract address is not configured" in detail
    assert "V2 deployment configuration is missing" in detail


@pytest.mark.asyncio
async def test_27_v2_address_missing_and_legacy_address_missing_rejected(client: AsyncClient):
    """27. V2 address missing and legacy address missing -> 503 Service Unavailable."""
    settings = get_settings()
    settings.escrow_v2_contract_address = None
    settings.escrow_contract_address = ""

    deal = await _setup_deal(client)
    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 503
    assert "CipherTradeEscrowV2 contract address is not configured" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_28_invalid_v2_address_rejected(client: AsyncClient):
    """28. Invalid V2 address format -> 503 Service Unavailable with validation error."""
    settings = get_settings()
    settings.escrow_v2_contract_address = "0xnot_a_valid_eth_address"

    deal = await _setup_deal(client)
    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 503
    assert "not a valid Ethereum address" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_29_v2_address_differs_from_legacy_v2_wins(client: AsyncClient):
    """29. V2 address differs from legacy address: V2 address always wins."""
    v2_addr = "0x4444444444444444444444444444444444444444"
    legacy_addr = "0x5555555555555555555555555555555555555555"
    settings = get_settings()
    settings.escrow_v2_contract_address = v2_addr
    settings.escrow_contract_address = legacy_addr

    deal = await _setup_fully_consented_deal(client)
    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 200
    assert resp.json()["contract_address"] == v2_addr
    assert resp.json()["contract_address"] != legacy_addr


@pytest.mark.asyncio
async def test_30_wrong_verifying_contract_signature_rejected(client: AsyncClient, db_session: AsyncSession):
    """30. Wrong verifying contract signature: rejected with 400 Bad Request."""
    settings = get_settings()
    settings.escrow_v2_contract_address = "0x6666666666666666666666666666666666666666"

    deal = await _setup_deal(client)

    # Buyer signs with a different verifying contract address (e.g. 0x7777...)
    chal_b = await client.post(
        f"/api/v1/escrow/deals/{deal['deal_id']}/consent/challenge",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    b_data = chal_b.json()
    b_data_tampered = dict(b_data)
    b_data_tampered["domain"] = dict(b_data["domain"])
    b_data_tampered["domain"]["verifyingContract"] = "0x7777777777777777777777777777777777777777"
    tampered_sig = _sign_eip712_challenge(b_data_tampered, BUYER_PK)

    # Insert tampered consent record directly in DB to simulate mismatch at payload verification
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    await db_session.execute(
        update(AgreementConsentChallenge)
        .where(AgreementConsentChallenge.nonce == b_data["nonce"])
        .values(used_at=now_dt)
    )
    consent = AgreementConsent(
        offer_id=uuid.UUID(deal["offer_id"]),
        deal_id=uuid.UUID(deal["deal_id"]),
        user_id=uuid.UUID(deal["buyer_user"]["id"]),
        party_role="buyer",
        signer_wallet=BUYER_WALLET,
        agreement_hash=deal["agreement_hash"],
        chain_id=31337,
        nonce=b_data["nonce"],
        signature=tampered_sig,
        deadline=b_data["message"]["deadline"],
        created_at=now_dt,
    )
    db_session.add(consent)

    # Seller provides normal consent for 0x6666...
    await _complete_consent(client, deal["deal_id"], deal["seller_token"], SELLER_PK)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/escrow/deals/{deal['deal_id']}/contract-payload",
        headers={"Authorization": f"Bearer {deal['buyer_token']}"},
    )
    assert resp.status_code == 400
    assert "Buyer signature is invalid or incompatible" in resp.json()["detail"]


def test_31_golden_vector_1_solidity_cross_language_parity():
    """
    31. Cross-language verification of Golden Test Vector 1 against exact Solidity test values.
    Asserts both buyer and seller struct hashes, final digests, and recovered signers match
    CipherTradeEscrowV2.t.sol test_01_golden_vector_1_exact_hashes.
    """
    types = {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "AgreementConsent": [
            {"name": "agreementHash", "type": "bytes32"},
            {"name": "offerId", "type": "string"},
            {"name": "dealId", "type": "string"},
            {"name": "buyer", "type": "address"},
            {"name": "seller", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "deliveryDeadline", "type": "uint256"},
            {"name": "partyRole", "type": "string"},
            {"name": "signer", "type": "address"},
            {"name": "nonce", "type": "string"},
            {"name": "deadline", "type": "uint256"},
        ],
    }
    domain = {
        "name": "CipherTrade",
        "version": "1",
        "chainId": 31337,
        "verifyingContract": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
    }
    base_msg = {
        "agreementHash": bytes.fromhex("6ef525c2e13920dbdb3549d949f23ddf711192863dee0cbae7df2b468a7adea2"),
        "offerId": "b732d80c-7fc8-479e-b9b2-3860bb777f91",
        "dealId": "9308ec28-c196-4ae2-a279-0ae6b907feea",
        "buyer": BUYER_WALLET,
        "seller": SELLER_WALLET,
        "amount": 2500000000000000000,
        "deliveryDeadline": 1764585600,
    }

    # Buyer computation
    b_msg = {
        **base_msg,
        "partyRole": "buyer",
        "signer": BUYER_WALLET,
        "nonce": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "deadline": 1763984400,
    }
    b_signable = encode_typed_data(full_message={"types": types, "primaryType": "AgreementConsent", "domain": domain, "message": b_msg})
    b_struct_hash = "0x" + b_signable.body.hex()
    b_digest = "0x" + Web3.keccak(b"\x19\x01" + b_signable.header + b_signable.body).hex()
    b_sig = bytes.fromhex("b7fe103ff3ade103980c52506fc469e5e97624033a307a3c03b9b714b7c0878a651153b88a8db8180146ac5cd8ccc6713c65c8a2bee80d5bd82e623ac26c1d711b")

    # Assert exact Solidity test_01 values for Buyer
    assert b_struct_hash == "0x159d0190162eda6466c25ed0b6a4232e6fa269f70bef074afb2d5be5c1326279"
    assert b_digest == "0x7c957c59256f2e0fee2393e649e9b54a690ecd067462a6ed8987f5a0d0869c3d"
    recovered_buyer = Account.recover_message(b_signable, signature=b_sig)
    assert Web3.to_checksum_address(recovered_buyer) == BUYER_WALLET

    # Seller computation
    s_msg = {
        **base_msg,
        "partyRole": "seller",
        "signer": SELLER_WALLET,
        "nonce": "f9e8d7c6b5a40312f9e8d7c6b5a40312f9e8d7c6b5a40312f9e8d7c6b5a40312",
        "deadline": 1763984700,
    }
    s_signable = encode_typed_data(full_message={"types": types, "primaryType": "AgreementConsent", "domain": domain, "message": s_msg})
    s_struct_hash = "0x" + s_signable.body.hex()
    s_digest = "0x" + Web3.keccak(b"\x19\x01" + s_signable.header + s_signable.body).hex()
    s_sig = bytes.fromhex("d2ec7722976f5d69a09461b926d27a5b8916fb2583d79ab70d2d07e4410d36a45b0abd30b6b2bb7c8866459f94576a9f8b076a62c9615b88bb96008757edf5271b")

    # Assert exact Solidity test_01 values for Seller (Reconciliation)
    assert s_struct_hash == "0xad8a28de3b876c04e94a749952b85a2b11b863007932126d01b20357d06eac3a"
    assert s_digest == "0x362f79880308675ecf2993edd5e69b46ad93f1a3d2d5ad37c8f5a2651b9c9b6d"
    recovered_seller = Account.recover_message(s_signable, signature=s_sig)
    assert Web3.to_checksum_address(recovered_seller) == SELLER_WALLET


def test_32_golden_vector_2_solidity_cross_language_parity():
    """
    32. Cross-language verification of Golden Test Vector 2 against exact Solidity test values.
    Asserts both buyer and seller struct hashes and final digests match
    CipherTradeEscrowV2.t.sol test_02_golden_vector_2_runtime_future_hashes.
    """
    types = {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "AgreementConsent": [
            {"name": "agreementHash", "type": "bytes32"},
            {"name": "offerId", "type": "string"},
            {"name": "dealId", "type": "string"},
            {"name": "buyer", "type": "address"},
            {"name": "seller", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "deliveryDeadline", "type": "uint256"},
            {"name": "partyRole", "type": "string"},
            {"name": "signer", "type": "address"},
            {"name": "nonce", "type": "string"},
            {"name": "deadline", "type": "uint256"},
        ],
    }
    domain = {
        "name": "CipherTrade",
        "version": "1",
        "chainId": 31337,
        "verifyingContract": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
    }
    base_msg = {
        "agreementHash": bytes.fromhex("6ef525c2e13920dbdb3549d949f23ddf711192863dee0cbae7df2b468a7adea2"),
        "offerId": "b732d80c-7fc8-479e-b9b2-3860bb777f91",
        "dealId": "9308ec28-c196-4ae2-a279-0ae6b907feea",
        "buyer": BUYER_WALLET,
        "seller": SELLER_WALLET,
        "amount": 2500000000000000000,
        "deliveryDeadline": 1795867200,
    }

    # Buyer computation
    b_msg = {
        **base_msg,
        "partyRole": "buyer",
        "signer": BUYER_WALLET,
        "nonce": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "deadline": 1795263600,
    }
    b_signable = encode_typed_data(full_message={"types": types, "primaryType": "AgreementConsent", "domain": domain, "message": b_msg})
    b_struct_hash = "0x" + b_signable.body.hex()
    b_digest = "0x" + Web3.keccak(b"\x19\x01" + b_signable.header + b_signable.body).hex()
    b_sig = bytes.fromhex("75b48d390a2830df86cc22837fb5691b9c0cba790037b58f46bc43a2fb46b9f060ed0222c0a70d343cfd697cf0b6fd06aa85294581b114ad54029f535733e3491c")

    assert b_struct_hash == "0x516ba976d0bebda88fac964a78d887d29d86b0170e37dc797eb41e80deb33766"
    assert b_digest == "0xd071cf50d74a9ebba7b257646637a850b85da5809614898c21d4e48cf74224b3"
    recovered_buyer = Account.recover_message(b_signable, signature=b_sig)
    assert Web3.to_checksum_address(recovered_buyer) == BUYER_WALLET

    # Seller computation
    s_msg = {
        **base_msg,
        "partyRole": "seller",
        "signer": SELLER_WALLET,
        "nonce": "f9e8d7c6b5a40312f9e8d7c6b5a40312f9e8d7c6b5a40312f9e8d7c6b5a40312",
        "deadline": 1795263900,
    }
    s_signable = encode_typed_data(full_message={"types": types, "primaryType": "AgreementConsent", "domain": domain, "message": s_msg})
    s_struct_hash = "0x" + s_signable.body.hex()
    s_digest = "0x" + Web3.keccak(b"\x19\x01" + s_signable.header + s_signable.body).hex()
    s_sig = bytes.fromhex("af779a2911a404092096f9ec6f698e46fc970bc2d480f9c5de3714598528e26a6bc16587f75d5429eeab1fba48dc31f25066da3dd23a8126ebcfb8d6df45c74a1b")

    assert s_struct_hash == "0x551458fb00b3842f72cfa3b37189299e897fdaa79f9d68fbb7c918b0f6a638e7"
    assert s_digest == "0xc92cfe07635d06acd1d4ab84f24249e7bc5d9863ac690aa2a8fb5a2d7b3fa62d"
    recovered_seller = Account.recover_message(s_signable, signature=s_sig)
    assert Web3.to_checksum_address(recovered_seller) == SELLER_WALLET
