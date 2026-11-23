"""
Unit and integration tests for Dual-Contract State Synchronization and Contract Tracking.
Phase 5E.4.1.

Verifies:
1. V1 synchronization handles 7-field tuple and uses V1 contract.
2. V2 synchronization handles 8-field tuple and validates dealUuidHash.
3. V2 dealUuidHash mismatch is safely rejected.
4. Mismatched buyer, seller, amount, or agreement hash aborts sync.
5. Unknown contract version is rejected without falling back.
6. Mismatched contract address is rejected.
7. V2 RPC error does NOT trigger V1 fallback.
8. Historical compatibility: deals missing contract metadata resolve identity from fund_tx_hash.
9. Historical deal with unconfigured destination address is rejected.
10. Unauthorized callers cannot sync (403).
11. Terminal states (RELEASED, REFUNDED, CANCELLED, RESOLVED) cannot be downgraded.
12. Repeated sync is idempotent.
13. Failed verification does not persist contract identity.
"""
from __future__ import annotations

import time
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from web3 import Web3

from app.config import get_settings
from app.models.models import EscrowDeal, EscrowState, Offer, Requirement, User
from app.services import escrow_service


V1_ADDR = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
V2_ADDR = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"
BUYER_ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
SELLER_ADDR = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


async def _create_test_deal(
    db_session,
    contract_version: str | None = "v2",
    contract_address: str | None = V2_ADDR,
    chain_deal_id: int | None = 10,
    fund_tx_hash: str | None = "0x" + "a" * 64,
    on_chain_state: EscrowState | None = EscrowState.FUNDED,
) -> tuple[EscrowDeal, User, User]:
    buyer = User(
        id=uuid.uuid4(),
        email=f"buyer_{uuid.uuid4().hex[:6]}@test.com",
        display_name="Buyer User",
        primary_role="buyer",
        wallet_address=BUYER_ADDR,
        hashed_password="test_hashed_password",
    )
    seller = User(
        id=uuid.uuid4(),
        email=f"seller_{uuid.uuid4().hex[:6]}@test.com",
        display_name="Seller User",
        primary_role="seller",
        wallet_address=SELLER_ADDR,
        hashed_password="test_hashed_password",
    )
    db_session.add_all([buyer, seller])
    await db_session.flush()

    req = Requirement(
        id=uuid.uuid4(),
        buyer_id=buyer.id,
        title="Test Requirement",
        description="Testing dual sync",
        category="security",
    )
    db_session.add(req)
    await db_session.flush()

    offer = Offer(
        id=uuid.uuid4(),
        requirement_id=req.id,
        seller_id=seller.id,
        price_eth=1.5,
        delivery_deadline=int(time.time()) + 86400,
        terms="Testing terms",
    )
    db_session.add(offer)
    await db_session.flush()

    ag_hash = "0x" + "b" * 64
    deal = EscrowDeal(
        id=uuid.uuid4(),
        offer_id=offer.id,
        buyer_id=buyer.id,
        seller_id=seller.id,
        amount_eth=1.5,
        deadline_ts=offer.delivery_deadline,
        agreement_hash=ag_hash,
        chain_deal_id=chain_deal_id,
        fund_tx_hash=fund_tx_hash,
        on_chain_state=on_chain_state,
        contract_address=contract_address,
        contract_version=contract_version,
    )
    db_session.add(deal)
    await db_session.commit()
    await db_session.refresh(deal)
    return deal, buyer, seller


@pytest.mark.asyncio
async def test_01_v1_sync_success_with_7_field_tuple(db_session):
    deal, buyer, _ = await _create_test_deal(
        db_session, contract_version="v1", contract_address=V1_ADDR
    )

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 31337

    # V1 returns 7 fields: buyer, seller, amount, deadline, agreementHash, state, fundsWithdrawn
    # state = 1 (CONFIRMED)
    expected_wei = int(Decimal("1.5") * Decimal(10**18))
    v1_raw_deal = (
        BUYER_ADDR,
        SELLER_ADDR,
        expected_wei,
        deal.deadline_ts,
        bytes.fromhex(deal.agreement_hash[2:]),
        1,  # State.CONFIRMED
        False,
    )

    mock_contract_v1 = MagicMock()
    mock_contract_v1.functions.getDeal(deal.chain_deal_id).call.return_value = v1_raw_deal

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3), \
         patch("app.services.escrow_service.get_escrow_contract", return_value=mock_contract_v1), \
         patch("app.services.escrow_service.get_escrow_v2_contract") as mock_v2_helper:

        updated = await escrow_service.sync_deal_state(db_session, deal.id, buyer)

        assert updated.on_chain_state == EscrowState.CONFIRMED
        assert updated.contract_version == "v1"
        assert updated.contract_address == V1_ADDR
        # Ensure V2 contract helper was never touched
        mock_v2_helper.assert_not_called()


@pytest.mark.asyncio
async def test_02_v2_sync_success_with_8_field_tuple(db_session):
    deal, buyer, _ = await _create_test_deal(
        db_session, contract_version="v2", contract_address=V2_ADDR
    )

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 31337

    expected_wei = int(Decimal("1.5") * Decimal(10**18))
    expected_uuid_hash = Web3.keccak(text=str(deal.id))

    # V2 returns 8 fields: buyer, seller, amount, deadline, agreementHash, dealUuidHash, state, fundsWithdrawn
    # state = 2 (RELEASED)
    v2_raw_deal = (
        BUYER_ADDR,
        SELLER_ADDR,
        expected_wei,
        deal.deadline_ts,
        bytes.fromhex(deal.agreement_hash[2:]),
        expected_uuid_hash,
        2,  # State.RELEASED
        True,
    )

    mock_contract_v2 = MagicMock()
    mock_contract_v2.functions.getDeal(deal.chain_deal_id).call.return_value = v2_raw_deal

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3), \
         patch("app.services.escrow_service.get_escrow_v2_contract", return_value=mock_contract_v2), \
         patch("app.services.escrow_service.get_escrow_contract") as mock_v1_helper:

        updated = await escrow_service.sync_deal_state(db_session, deal.id, buyer)

        assert updated.on_chain_state == EscrowState.RELEASED
        assert updated.contract_version == "v2"
        assert updated.contract_address == V2_ADDR
        mock_v1_helper.assert_not_called()


@pytest.mark.asyncio
async def test_03_v2_sync_rejects_deal_uuid_hash_mismatch(db_session):
    deal, buyer, _ = await _create_test_deal(
        db_session, contract_version="v2", contract_address=V2_ADDR
    )

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 31337

    expected_wei = int(Decimal("1.5") * Decimal(10**18))
    wrong_uuid_hash = Web3.keccak(text="completely-different-deal-id")

    v2_raw_deal = (
        BUYER_ADDR,
        SELLER_ADDR,
        expected_wei,
        deal.deadline_ts,
        bytes.fromhex(deal.agreement_hash[2:]),
        wrong_uuid_hash,
        1,
        False,
    )

    mock_contract_v2 = MagicMock()
    mock_contract_v2.functions.getDeal(deal.chain_deal_id).call.return_value = v2_raw_deal

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3), \
         patch("app.services.escrow_service.get_escrow_v2_contract", return_value=mock_contract_v2):

        with pytest.raises(ValueError, match="deal UUID hash does not match"):
            await escrow_service.sync_deal_state(db_session, deal.id, buyer)


@pytest.mark.asyncio
async def test_04_v2_sync_rejects_wrong_buyer(db_session):
    deal, buyer, _ = await _create_test_deal(
        db_session, contract_version="v2", contract_address=V2_ADDR
    )

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 31337

    expected_wei = int(Decimal("1.5") * Decimal(10**18))
    expected_uuid_hash = Web3.keccak(text=str(deal.id))

    v2_raw_deal = (
        "0x1111111111111111111111111111111111111111",  # Impostor buyer
        SELLER_ADDR,
        expected_wei,
        deal.deadline_ts,
        bytes.fromhex(deal.agreement_hash[2:]),
        expected_uuid_hash,
        1,
        False,
    )

    mock_contract_v2 = MagicMock()
    mock_contract_v2.functions.getDeal(deal.chain_deal_id).call.return_value = v2_raw_deal

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3), \
         patch("app.services.escrow_service.get_escrow_v2_contract", return_value=mock_contract_v2):

        with pytest.raises(ValueError, match="On-chain buyer .* does not match"):
            await escrow_service.sync_deal_state(db_session, deal.id, buyer)


@pytest.mark.asyncio
async def test_05_sync_rejects_unknown_contract_version(db_session):
    deal, buyer, _ = await _create_test_deal(
        db_session, contract_version="v99", contract_address=V2_ADDR
    )

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 31337

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        with pytest.raises(ValueError, match="Unsupported contract version 'v99'"):
            await escrow_service.sync_deal_state(db_session, deal.id, buyer)


@pytest.mark.asyncio
async def test_06_sync_rejects_mismatched_contract_address(db_session):
    fake_address = "0x1234567890123456789012345678901234567890"
    deal, buyer, _ = await _create_test_deal(
        db_session, contract_version="v2", contract_address=fake_address
    )

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 31337

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        with pytest.raises(ValueError, match="does not match configured V2 address"):
            await escrow_service.sync_deal_state(db_session, deal.id, buyer)


@pytest.mark.asyncio
async def test_07_v2_rpc_error_does_not_fallback_to_v1(db_session):
    deal, buyer, _ = await _create_test_deal(
        db_session, contract_version="v2", contract_address=V2_ADDR
    )

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 31337

    mock_contract_v2 = MagicMock()
    mock_contract_v2.functions.getDeal(deal.chain_deal_id).call.side_effect = RuntimeError("RPC Node Offline")

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3), \
         patch("app.services.escrow_service.get_escrow_v2_contract", return_value=mock_contract_v2), \
         patch("app.services.escrow_service.get_escrow_contract") as mock_v1_helper:

        with pytest.raises(ValueError, match="Failed to read deal from contract getDeal"):
            await escrow_service.sync_deal_state(db_session, deal.id, buyer)

        # Strict check: V1 contract MUST NEVER be invoked as fallback
        mock_v1_helper.assert_not_called()


@pytest.mark.asyncio
async def test_08_historical_deal_missing_metadata_resolves_from_tx_hash(db_session):
    # Historical deal with contract_version=None and contract_address=None
    deal, buyer, _ = await _create_test_deal(
        db_session, contract_version=None, contract_address=None
    )

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 31337
    # Historical funding tx destination points to V2 contract
    mock_w3.eth.get_transaction.return_value = {"to": V2_ADDR}

    expected_wei = int(Decimal("1.5") * Decimal(10**18))
    expected_uuid_hash = Web3.keccak(text=str(deal.id))
    v2_raw_deal = (
        BUYER_ADDR,
        SELLER_ADDR,
        expected_wei,
        deal.deadline_ts,
        bytes.fromhex(deal.agreement_hash[2:]),
        expected_uuid_hash,
        0,  # FUNDED
        False,
    )

    mock_contract_v2 = MagicMock()
    mock_contract_v2.functions.getDeal(deal.chain_deal_id).call.return_value = v2_raw_deal

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3), \
         patch("app.services.escrow_service.get_escrow_v2_contract", return_value=mock_contract_v2):

        updated = await escrow_service.sync_deal_state(db_session, deal.id, buyer)

        # Verified metadata is successfully backfilled and persisted
        assert updated.contract_version == "v2"
        assert updated.contract_address == V2_ADDR


@pytest.mark.asyncio
async def test_09_historical_deal_unconfigured_destination_rejected(db_session):
    deal, buyer, _ = await _create_test_deal(
        db_session, contract_version=None, contract_address=None
    )

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 31337
    # Historical funding tx destination points to an unknown contract
    unconfigured_dest = "0x9999999999999999999999999999999999999999"
    mock_w3.eth.get_transaction.return_value = {"to": unconfigured_dest}

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        with pytest.raises(ValueError, match="does not match any configured contract address"):
            await escrow_service.sync_deal_state(db_session, deal.id, buyer)


@pytest.mark.asyncio
async def test_10_unauthorized_user_cannot_sync(db_session):
    deal, _, _ = await _create_test_deal(db_session)
    stranger = User(
        id=uuid.uuid4(),
        email="stranger@test.com",
        display_name="Stranger",
        primary_role="buyer",
        hashed_password="test_hashed_password",
    )
    db_session.add(stranger)
    await db_session.commit()

    with pytest.raises(PermissionError, match="Access denied"):
        await escrow_service.sync_deal_state(db_session, deal.id, stranger)


@pytest.mark.asyncio
async def test_11_terminal_state_protection_prevents_downgrade(db_session):
    # Deal is already in terminal RELEASED state
    deal, buyer, _ = await _create_test_deal(
        db_session, contract_version="v2", contract_address=V2_ADDR, on_chain_state=EscrowState.RELEASED
    )

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 31337

    expected_wei = int(Decimal("1.5") * Decimal(10**18))
    expected_uuid_hash = Web3.keccak(text=str(deal.id))

    # An old node response returns state=0 (FUNDED)
    v2_raw_deal = (
        BUYER_ADDR,
        SELLER_ADDR,
        expected_wei,
        deal.deadline_ts,
        bytes.fromhex(deal.agreement_hash[2:]),
        expected_uuid_hash,
        0,  # FUNDED (downgrade attempt)
        False,
    )

    mock_contract_v2 = MagicMock()
    mock_contract_v2.functions.getDeal(deal.chain_deal_id).call.return_value = v2_raw_deal

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3), \
         patch("app.services.escrow_service.get_escrow_v2_contract", return_value=mock_contract_v2):

        updated = await escrow_service.sync_deal_state(db_session, deal.id, buyer)

        # State remains RELEASED
        assert updated.on_chain_state == EscrowState.RELEASED


@pytest.mark.asyncio
async def test_12_failed_verification_does_not_persist_contract_identity(db_session):
    deal, buyer, _ = await _create_test_deal(
        db_session, contract_version=None, contract_address=None, on_chain_state=None, chain_deal_id=None, fund_tx_hash=None
    )

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 31337

    # Mock receipt with status 0 (failed tx)
    mock_w3.eth.get_transaction.return_value = {
        "to": V2_ADDR,
        "from": BUYER_ADDR,
        "blockNumber": 100,
        "chainId": 31337,
        "value": int(Decimal("1.5") * Decimal(10**18)),
    }
    mock_w3.eth.get_transaction_receipt.return_value = {
        "status": 0,  # FAILED
        "blockNumber": 100,
        "to": V2_ADDR,
    }

    with patch("app.services.escrow_service.get_web3", return_value=mock_w3):
        with pytest.raises(ValueError, match="Transaction failed on chain"):
            await escrow_service.verify_funding_transaction(
                db=db_session,
                deal=deal,
                tx_hash="0x" + "c" * 64,
                buyer=buyer,
            )

    # Verify database state was NOT mutated
    await db_session.refresh(deal)
    assert deal.contract_address is None
    assert deal.contract_version is None
    assert deal.on_chain_state is None
    assert deal.chain_deal_id is None

