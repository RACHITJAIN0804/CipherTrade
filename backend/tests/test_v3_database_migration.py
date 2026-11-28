"""
Comprehensive test suite for Phase 6.2.2.2 V3 Database Model Expansion and Alembic Migration.

Test matrix covering:
1. Model initialization for ETH, USDC, and USDT
2. Exact base-unit storage and validation
3. 6-decimal and 18-decimal asset metadata
4. Historical ETH conversion (exact integer arithmetic, 100-digit Decimal)
5. NULL and zero requirement budgets
6. Negative and inverted budget rejection
7. uint256 maximum and overflow handling
8. Invalid decimal strings (floats, scientific notation, letters, negative signs)
9. Consent version uniqueness (uq_offer_party_version_consent)
10. Historical V1/V2 compatibility (chain_id nullable, contract_version preserved)
11. Historical unknown chain ID preservation (no fabrication of 31337)
12. V3 ETH downgrade rejection
13. V3 ERC20 downgrade rejection
14. Non-ETH requirement / offer downgrade rejection
15. SQLite migration execution (upgrade and safe downgrade cycle)
16. PostgreSQL-compatible migration operations (batch and standard DDL)
"""
import uuid
from decimal import Decimal
import decimal
import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from alembic.config import Config
from alembic import command

from app.core.numeric import (
    UINT256_MAX,
    UINT256_MAX_STR,
    validate_base_units_str,
    decimal_to_base_units_str,
    base_units_str_to_decimal,
)
from app.models.models import (
    Base,
    User,
    UserRole,
    Requirement,
    RequirementStatus,
    Offer,
    OfferStatus,
    EscrowDeal,
    EscrowState,
    AgreementConsent,
)


# ─── Numeric & Base-Unit Unit Tests ──────────────────────────────────────────

def test_numeric_base_unit_validation_valid():
    assert validate_base_units_str("0") == "0"
    assert validate_base_units_str("1") == "1"
    assert validate_base_units_str("1000000000000000000") == "1000000000000000000"
    assert validate_base_units_str(UINT256_MAX_STR) == UINT256_MAX_STR


def test_numeric_base_unit_validation_invalid():
    # Negative / signs
    with pytest.raises(ValueError, match="only decimal digits"):
        validate_base_units_str("-1")
    with pytest.raises(ValueError, match="only decimal digits"):
        validate_base_units_str("+100")

    # Scientific notation
    with pytest.raises(ValueError, match="only decimal digits"):
        validate_base_units_str("1e18")
    with pytest.raises(ValueError, match="only decimal digits"):
        validate_base_units_str("1E18")

    # Decimals
    with pytest.raises(ValueError, match="only decimal digits"):
        validate_base_units_str("1.5")
    with pytest.raises(ValueError, match="only decimal digits"):
        validate_base_units_str("100.00")

    # Leading zeroes
    with pytest.raises(ValueError, match="leading zeroes"):
        validate_base_units_str("0123")
    with pytest.raises(ValueError, match="leading zeroes"):
        validate_base_units_str("00")

    # Non-digits / letters / hex
    with pytest.raises(ValueError, match="only decimal digits"):
        validate_base_units_str("0x123")
    with pytest.raises(ValueError, match="only decimal digits"):
        validate_base_units_str("abc")

    # Float type rejected directly
    with pytest.raises(TypeError, match="must be a string"):
        validate_base_units_str(100.5)

    # Empty string
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_base_units_str("")

    # uint256 overflow
    overflow_val = str(UINT256_MAX + 1)
    with pytest.raises(ValueError, match="exceeds uint256 maximum"):
        validate_base_units_str(overflow_val)


def test_decimal_conversion_assets():
    # ETH (18 decimals)
    eth_bu = decimal_to_base_units_str("1.5", decimals=18)
    assert eth_bu == "1500000000000000000"
    assert base_units_str_to_decimal(eth_bu, 18) == Decimal("1.5")

    # Smallest 8-decimal legacy quantum: 0.00000001 ETH -> 10,000,000,000 Wei (exact)
    small_eth = decimal_to_base_units_str("0.00000001", decimals=18)
    assert small_eth == "10000000000"
    assert base_units_str_to_decimal(small_eth, 18) == Decimal("0.00000001")

    # USDC (6 decimals)
    usdc_bu = decimal_to_base_units_str("250.75", decimals=6)
    assert usdc_bu == "250750000"
    assert base_units_str_to_decimal(usdc_bu, 6) == Decimal("250.75")

    # USDT (6 decimals)
    usdt_bu = decimal_to_base_units_str("1000", decimals=6)
    assert usdt_bu == "1000000000"
    assert base_units_str_to_decimal(usdt_bu, 6) == Decimal("1000")

    # Zero amount
    zero_bu = decimal_to_base_units_str("0", decimals=18)
    assert zero_bu == "0"
    assert base_units_str_to_decimal(zero_bu, 18) == Decimal("0")

    # Prohibit float input
    with pytest.raises(TypeError, match="Floating-point amounts are prohibited"):
        decimal_to_base_units_str(1.5, decimals=18)

    # Fractional base-unit rejection (e.g. 0.0000001 for 6 decimals token)
    with pytest.raises(ValueError, match="fractional base units"):
        decimal_to_base_units_str("0.0000001", decimals=6)


# ─── Model Initialization & Relationship Tests ───────────────────────────────

def test_models_v3_fields_initialization():
    u_buyer = User(
        email="buyer@test.com",
        display_name="Buyer",
        hashed_password="pw",
        primary_role=UserRole.BUYER,
        wallet_address="0x1111111111111111111111111111111111111111",
    )
    u_seller = User(
        email="seller@test.com",
        display_name="Seller",
        hashed_password="pw",
        primary_role=UserRole.SELLER,
        wallet_address="0x2222222222222222222222222222222222222222",
    )

    # 1. Requirement with multi-token fields (e.g. USDC requirement)
    req = Requirement(
        buyer_id=u_buyer.id,
        title="Need React Dev",
        description="Build frontend with Web3",
        category="Development",
        currency="USDC",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        token_decimals=6,
        chain_id=1,
        budget_min=100.0,
        budget_max=500.0,
        budget_min_base_units="100000000",
        budget_max_base_units="500000000",
    )
    assert req.currency == "USDC"
    assert req.token_decimals == 6
    assert req.budget_min_base_units == "100000000"

    # 2. Offer for USDC
    off = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=250.0,
        currency="USDC",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        token_decimals=6,
        chain_id=1,
        price_base_units="250000000",
        delivery_deadline=1800000000,
        terms="Delivery in 5 days",
    )
    assert off.price_base_units == "250000000"
    assert off.token_decimals == 6

    # 3. EscrowDeal for USDC on V3 contract
    deal = EscrowDeal(
        offer_id=off.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        contract_address="0x3333333333333333333333333333333333333333",
        contract_version="v3",
        chain_id=1,
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        token_symbol="USDC",
        token_decimals=6,
        amount_eth=250.0,
        amount_base_units="250000000",
        fee_bps=100,
        treasury_address="0x4444444444444444444444444444444444444444",
        agreement_version=1,
        previous_agreement_hash=None,
        deadline_ts=1800000000,
        agreement_hash="0x" + "a" * 64,
        on_chain_state=EscrowState.FUNDED,
    )
    assert deal.contract_version == "v3"
    assert deal.fee_bps == 100
    assert deal.amount_base_units == "250000000"

    # 4. AgreementConsent with amendment versioning
    consent_v1 = AgreementConsent(
        offer_id=off.id,
        deal_id=deal.id,
        user_id=u_buyer.id,
        party_role="buyer",
        signer_wallet="0x1111111111111111111111111111111111111111",
        agreement_hash="0x" + "a" * 64,
        chain_id=1,
        nonce="0x" + "1" * 64,
        signature="0x" + "2" * 130,
        deadline=1800000000,
        agreement_version=1,
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        amount_base_units="250000000",
        fee_bps=100,
        treasury_address="0x4444444444444444444444444444444444444444",
    )
    assert consent_v1.agreement_version == 1

    consent_v2 = AgreementConsent(
        offer_id=off.id,
        deal_id=deal.id,
        user_id=u_buyer.id,
        party_role="buyer",
        signer_wallet="0x1111111111111111111111111111111111111111",
        agreement_hash="0x" + "b" * 64,
        chain_id=1,
        nonce="0x" + "3" * 64,
        signature="0x" + "4" * 130,
        deadline=1800000000,
        agreement_version=2,
        previous_agreement_hash="0x" + "a" * 64,
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        amount_base_units="250000000",
        fee_bps=100,
        treasury_address="0x4444444444444444444444444444444444444444",
    )
    assert consent_v2.agreement_version == 2
    assert consent_v2.previous_agreement_hash == "0x" + "a" * 64


# ─── Alembic Migration & Downgrade Cycle Tests (SQLite Test DB) ──────────────

def test_alembic_upgrade_backfill_and_safeguards():
    """
    Test running Alembic upgrade to 20261124_0001, verifying:
    - Pre-validation
    - Exact backfill of historical records to 18 decimals
    - Null preservation for requirement budgets
    - Downgrade guards when V3 records or ERC20 records exist
    - Clean downgrade when only historical ETH data exists
    """
    engine = create_engine("sqlite:///:memory:")
    connection = engine.connect()

    import os
    from pathlib import Path
    backend_dir = Path(__file__).resolve().parent.parent
    alembic_ini_path = str(backend_dir / "alembic.ini")
    script_loc = str(backend_dir / "alembic")

    cfg = Config(alembic_ini_path)
    cfg.set_main_option("script_location", script_loc)
    cfg.attributes["connection"] = connection

    # 1. Create base tables using metadata (reflecting legacy baseline before V3 columns)
    # To create tables as they existed in revision 20261123_0001:
    connection.execute(text("""
        CREATE TABLE users (
            id VARCHAR(36) PRIMARY KEY,
            email VARCHAR(254) NOT NULL UNIQUE,
            display_name VARCHAR(64) NOT NULL,
            hashed_password VARCHAR(128) NOT NULL,
            primary_role VARCHAR(16) NOT NULL,
            wallet_address VARCHAR(42) UNIQUE,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """))
    connection.execute(text("""
        CREATE TABLE requirements (
            id VARCHAR(36) PRIMARY KEY,
            buyer_id VARCHAR(36) NOT NULL,
            title VARCHAR(256) NOT NULL,
            description TEXT NOT NULL,
            category VARCHAR(128) NOT NULL,
            keywords TEXT,
            budget_min NUMERIC(20, 8),
            budget_max NUMERIC(20, 8),
            status VARCHAR(16) NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            FOREIGN KEY (buyer_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """))
    connection.execute(text("""
        CREATE TABLE offers (
            id VARCHAR(36) PRIMARY KEY,
            requirement_id VARCHAR(36) NOT NULL,
            seller_id VARCHAR(36) NOT NULL,
            price_eth NUMERIC(20, 8) NOT NULL,
            delivery_deadline INTEGER NOT NULL,
            terms TEXT NOT NULL,
            status VARCHAR(16) NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            FOREIGN KEY (requirement_id) REFERENCES requirements (id) ON DELETE CASCADE,
            FOREIGN KEY (seller_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """))
    connection.execute(text("""
        CREATE TABLE escrow_deals (
            id VARCHAR(36) PRIMARY KEY,
            offer_id VARCHAR(36) NOT NULL UNIQUE,
            buyer_id VARCHAR(36) NOT NULL,
            seller_id VARCHAR(36) NOT NULL,
            chain_deal_id INTEGER UNIQUE,
            fund_tx_hash VARCHAR(66) UNIQUE,
            contract_address VARCHAR(42),
            contract_version VARCHAR(10),
            amount_eth NUMERIC(20, 8) NOT NULL,
            deadline_ts INTEGER NOT NULL,
            agreement_hash VARCHAR(66),
            on_chain_state VARCHAR(16),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            FOREIGN KEY (offer_id) REFERENCES offers (id) ON DELETE RESTRICT,
            FOREIGN KEY (buyer_id) REFERENCES users (id) ON DELETE RESTRICT,
            FOREIGN KEY (seller_id) REFERENCES users (id) ON DELETE RESTRICT
        )
    """))
    connection.execute(text("""
        CREATE TABLE agreement_consents (
            id VARCHAR(36) PRIMARY KEY,
            offer_id VARCHAR(36) NOT NULL,
            deal_id VARCHAR(36),
            user_id VARCHAR(36) NOT NULL,
            party_role VARCHAR(16) NOT NULL,
            signer_wallet VARCHAR(42) NOT NULL,
            agreement_hash VARCHAR(66) NOT NULL,
            chain_id INTEGER NOT NULL,
            nonce VARCHAR(64) NOT NULL,
            signature VARCHAR(132) NOT NULL,
            deadline INTEGER NOT NULL,
            created_at TIMESTAMP NOT NULL,
            CONSTRAINT uq_offer_party_consent UNIQUE (offer_id, party_role),
            FOREIGN KEY (offer_id) REFERENCES offers (id) ON DELETE CASCADE,
            FOREIGN KEY (deal_id) REFERENCES escrow_deals (id) ON DELETE SET NULL,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """))
    connection.execute(text("""
        CREATE TABLE alembic_version (
            version_num VARCHAR(32) NOT NULL PRIMARY KEY
        )
    """))
    connection.execute(text("INSERT INTO alembic_version VALUES ('20261123_0001')"))
    connection.commit()

    # 2. Insert historical V1/V2 records with strictly legacy columns
    u_buyer_id = str(uuid.uuid4())
    u_seller_id = str(uuid.uuid4())
    req_id1 = str(uuid.uuid4())  # Has min & max budget
    req_id2 = str(uuid.uuid4())  # Has NULL budget
    offer_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    consent_id = str(uuid.uuid4())

    connection.execute(
        text(
            "INSERT INTO users (id, email, display_name, hashed_password, primary_role, is_active, created_at, updated_at) "
            "VALUES (:id, 'b@test.com', 'Buyer', 'hash', 'BUYER', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": u_buyer_id}
    )
    connection.execute(
        text(
            "INSERT INTO users (id, email, display_name, hashed_password, primary_role, is_active, created_at, updated_at) "
            "VALUES (:id, 's@test.com', 'Seller', 'hash', 'SELLER', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": u_seller_id}
    )

    # Historical Requirement 1: 1.5 min, 3.25 max ETH
    connection.execute(
        text(
            "INSERT INTO requirements (id, buyer_id, title, description, category, budget_min, budget_max, status, created_at, updated_at) "
            "VALUES (:id, :bid, 'Req 1', 'Desc', 'Cat', 1.5, 3.25, 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": req_id1, "bid": u_buyer_id}
    )
    # Historical Requirement 2: NULL min, NULL max
    connection.execute(
        text(
            "INSERT INTO requirements (id, buyer_id, title, description, category, budget_min, budget_max, status, created_at, updated_at) "
            "VALUES (:id, :bid, 'Req 2', 'Desc', 'Cat', NULL, NULL, 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": req_id2, "bid": u_buyer_id}
    )

    # Historical Offer: 2.0 ETH
    connection.execute(
        text(
            "INSERT INTO offers (id, requirement_id, seller_id, price_eth, delivery_deadline, terms, status, created_at, updated_at) "
            "VALUES (:id, :rid, :sid, 2.0, 1800000000, 'Terms', 'PENDING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": offer_id, "rid": req_id1, "sid": u_seller_id}
    )

    # Historical EscrowDeal: 2.0 ETH, contract_version = 'v2'
    connection.execute(
        text(
            "INSERT INTO escrow_deals (id, offer_id, buyer_id, seller_id, chain_deal_id, fund_tx_hash, contract_address, contract_version, amount_eth, deadline_ts, created_at, updated_at) "
            "VALUES (:id, :oid, :bid, :sid, 1, '0xaaa', '0x111', 'v2', 2.0, 1800000000, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": deal_id, "oid": offer_id, "bid": u_buyer_id, "sid": u_seller_id}
    )

    # Historical AgreementConsent: Version 1 (implicit in old schema)
    connection.execute(
        text(
            "INSERT INTO agreement_consents (id, offer_id, deal_id, user_id, party_role, signer_wallet, agreement_hash, chain_id, nonce, signature, deadline, created_at) "
            "VALUES (:id, :oid, :did, :uid, 'buyer', '0xwallet', '0xhash', 31337, '0xnonce', '0xsig', 1800000000, CURRENT_TIMESTAMP)"
        ),
        {"id": consent_id, "oid": offer_id, "did": deal_id, "uid": u_buyer_id}
    )
    connection.commit()

    # 3. Perform Alembic upgrade to 20261124_0001
    command.upgrade(cfg, "20261124_0001")

    # 4. Verify backfilled records
    # Check Requirement 1
    r1 = connection.execute(
        text("SELECT currency, token_decimals, budget_min_base_units, budget_max_base_units FROM requirements WHERE id = :id"),
        {"id": req_id1}
    ).fetchone()
    assert r1[0] == "ETH"
    assert r1[1] == 18
    assert r1[2] == "1500000000000000000"
    assert r1[3] == "3250000000000000000"

    # Check Requirement 2 (NULL budgets preserved)
    r2 = connection.execute(
        text("SELECT currency, token_decimals, budget_min_base_units, budget_max_base_units FROM requirements WHERE id = :id"),
        {"id": req_id2}
    ).fetchone()
    assert r2[0] == "ETH"
    assert r2[1] == 18
    assert r2[2] is None
    assert r2[3] is None

    # Check Offer
    o = connection.execute(
        text("SELECT currency, token_decimals, price_base_units FROM offers WHERE id = :id"),
        {"id": offer_id}
    ).fetchone()
    assert o[0] == "ETH"
    assert o[1] == 18
    assert o[2] == "2000000000000000000"

    # Check EscrowDeal
    d = connection.execute(
        text("SELECT token_symbol, token_decimals, amount_base_units, agreement_version, contract_version FROM escrow_deals WHERE id = :id"),
        {"id": deal_id}
    ).fetchone()
    assert d[0] == "ETH"
    assert d[1] == 18
    assert d[2] == "2000000000000000000"
    assert d[3] == 1
    assert d[4] == "v2"

    # Check AgreementConsent
    c = connection.execute(
        text("SELECT agreement_version, token_address FROM agreement_consents WHERE id = :id"),
        {"id": consent_id}
    ).fetchone()
    assert c[0] == 1
    assert c[1] is None

    # 5. Test Downgrade Protection Guards
    # Guard A1: V3 ETH deal must abort downgrade
    deal_v3_id = str(uuid.uuid4())
    off2_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO offers (id, requirement_id, seller_id, price_eth, currency, token_decimals, price_base_units, delivery_deadline, terms, status, created_at, updated_at) "
            "VALUES (:id, :rid, :sid, 1.0, 'ETH', 18, '1000000000000000000', 1800000000, 'Terms 2', 'ACCEPTED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": off2_id, "rid": req_id1, "sid": u_seller_id}
    )
    connection.execute(
        text(
            "INSERT INTO escrow_deals (id, offer_id, buyer_id, seller_id, contract_version, amount_eth, token_symbol, token_decimals, amount_base_units, agreement_version, deadline_ts, created_at, updated_at) "
            "VALUES (:id, :oid, :bid, :sid, 'v3', 1.0, 'ETH', 18, '1000000000000000000', 1, 1800000000, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": deal_v3_id, "oid": off2_id, "bid": u_buyer_id, "sid": u_seller_id}
    )
    connection.commit()

    with pytest.raises(RuntimeError, match="V3 escrow deal"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(text("DELETE FROM escrow_deals WHERE id = :id"), {"id": deal_v3_id})
    connection.commit()

    # Guard A2: V3 ERC20 deal (e.g. token_symbol = 'USDC', token_address set) must abort downgrade
    deal_erc20_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO escrow_deals (id, offer_id, buyer_id, seller_id, contract_version, amount_eth, token_address, token_symbol, token_decimals, amount_base_units, agreement_version, deadline_ts, created_at, updated_at) "
            "VALUES (:id, :oid, :bid, :sid, 'v2', 100.0, '0xusdc', 'USDC', 6, '100000000', 1, 1800000000, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": deal_erc20_id, "oid": off2_id, "bid": u_buyer_id, "sid": u_seller_id}
    )
    connection.commit()

    with pytest.raises(RuntimeError, match="non-ETH escrow deal"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(text("DELETE FROM escrow_deals WHERE id = :id"), {"id": deal_erc20_id})
    connection.commit()

    # Guard A3: Custom fee / treasury data in escrow_deals
    deal_fee_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO escrow_deals (id, offer_id, buyer_id, seller_id, contract_version, amount_eth, token_symbol, token_decimals, amount_base_units, fee_bps, treasury_address, agreement_version, deadline_ts, created_at, updated_at) "
            "VALUES (:id, :oid, :bid, :sid, 'v2', 1.0, 'ETH', 18, '1000000000000000000', 150, '0xtreasury', 1, 1800000000, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": deal_fee_id, "oid": off2_id, "bid": u_buyer_id, "sid": u_seller_id}
    )
    connection.commit()

    with pytest.raises(RuntimeError, match="explicit V3 fee/treasury data"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(text("DELETE FROM escrow_deals WHERE id = :id"), {"id": deal_fee_id})
    connection.commit()

    # Guard B1: Non-ETH Requirement (currency != 'ETH')
    req_usdc_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO requirements (id, buyer_id, title, description, category, currency, token_address, token_decimals, budget_min, budget_max, budget_min_base_units, budget_max_base_units, status, created_at, updated_at) "
            "VALUES (:id, :bid, 'USDC req', 'Desc', 'Cat', 'USDC', '0xusdc', 6, 100, 200, '100000000', '200000000', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": req_usdc_id, "bid": u_buyer_id}
    )
    connection.commit()

    with pytest.raises(RuntimeError, match="non-ETH requirement"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(text("DELETE FROM requirements WHERE id = :id"), {"id": req_usdc_id})
    connection.commit()

    # Guard B2: Non-ETH Offer (currency != 'ETH')
    off_usdc_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO offers (id, requirement_id, seller_id, price_eth, currency, token_address, token_decimals, price_base_units, delivery_deadline, terms, status, created_at, updated_at) "
            "VALUES (:id, :rid, :sid, 100.0, 'USDC', '0xusdc', 6, '100000000', 1800000000, 'Terms USDC', 'PENDING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": off_usdc_id, "rid": req_id1, "sid": u_seller_id}
    )
    connection.commit()

    with pytest.raises(RuntimeError, match="non-ETH offer"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(text("DELETE FROM offers WHERE id = :id"), {"id": off_usdc_id})
    connection.commit()

    # Guard C1: Agreement Consent Version 1 with V3 metadata (e.g. fee_bps / treasury_address / token_address)
    consent_v1_meta_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO agreement_consents (id, offer_id, deal_id, user_id, party_role, signer_wallet, agreement_hash, chain_id, nonce, signature, deadline, agreement_version, fee_bps, treasury_address, created_at) "
            "VALUES (:id, :oid, :did, :uid, 'seller', '0xwallet', '0xhashmeta', 31337, '0xnoncemeta', '0xsigmeta', 1800000000, 1, 100, '0xtreasury', CURRENT_TIMESTAMP)"
        ),
        {"id": consent_v1_meta_id, "oid": offer_id, "did": deal_id, "uid": u_seller_id}
    )
    connection.commit()

    with pytest.raises(RuntimeError, match="explicit V3 metadata"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(text("DELETE FROM agreement_consents WHERE id = :id"), {"id": consent_v1_meta_id})
    connection.commit()

    # Guard C2: Agreement Consent Version 2 (amended agreement)
    consent_v2_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO agreement_consents (id, offer_id, deal_id, user_id, party_role, signer_wallet, agreement_hash, chain_id, nonce, signature, deadline, agreement_version, created_at) "
            "VALUES (:id, :oid, :did, :uid, 'buyer', '0xwallet', '0xhash2', 31337, '0xnonce2', '0xsig2', 1800000000, 2, CURRENT_TIMESTAMP)"
        ),
        {"id": consent_v2_id, "oid": offer_id, "did": deal_id, "uid": u_buyer_id}
    )
    connection.commit()

    with pytest.raises(RuntimeError, match="amended agreement consent"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(text("DELETE FROM agreement_consents WHERE id = :id"), {"id": consent_v2_id})
    connection.execute(text("DELETE FROM offers WHERE id = :id"), {"id": off2_id})
    connection.commit()

    # ─────────────────────────────────────────────────────────────────────────
    # Adversarial Reconstructability Tests (Scenarios 1-10)
    # ─────────────────────────────────────────────────────────────────────────
    # 1. Modified requirement minimum base units (1 wei difference)
    connection.execute(
        text("UPDATE requirements SET budget_min_base_units = '1500000000000000001' WHERE id = :id"),
        {"id": req_id1}
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="budget_min_base_units"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(
        text("UPDATE requirements SET budget_min_base_units = '1500000000000000000' WHERE id = :id"),
        {"id": req_id1}
    )
    connection.commit()

    # 2. Modified requirement maximum base units
    connection.execute(
        text("UPDATE requirements SET budget_max_base_units = '3250000000000000001' WHERE id = :id"),
        {"id": req_id1}
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="budget_max_base_units"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(
        text("UPDATE requirements SET budget_max_base_units = '3250000000000000000' WHERE id = :id"),
        {"id": req_id1}
    )
    connection.commit()

    # 3. Non-NULL base units paired with NULL legacy budget
    connection.execute(
        text("UPDATE requirements SET budget_min_base_units = '1000000000000000000' WHERE id = :id"),
        {"id": req_id2}
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="paired with NULL legacy budget_min"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(
        text("UPDATE requirements SET budget_min_base_units = NULL WHERE id = :id"),
        {"id": req_id2}
    )
    connection.commit()

    # 4. Modified offer price base units (1 wei discrepancy)
    connection.execute(
        text("UPDATE offers SET price_base_units = '2000000000000000001' WHERE id = :id"),
        {"id": offer_id}
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="price_base_units"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(
        text("UPDATE offers SET price_base_units = '2000000000000000000' WHERE id = :id"),
        {"id": offer_id}
    )
    connection.commit()

    # 5. Empty base-unit string on offer
    connection.execute(
        text("UPDATE offers SET price_base_units = '' WHERE id = :id"),
        {"id": offer_id}
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="missing/empty price_base_units"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(
        text("UPDATE offers SET price_base_units = '2000000000000000000' WHERE id = :id"),
        {"id": offer_id}
    )
    connection.commit()

    # 6. Modified legacy ETH escrow amount base units (1 wei discrepancy)
    connection.execute(
        text("UPDATE escrow_deals SET amount_base_units = '2000000000000000001' WHERE id = :id"),
        {"id": deal_id}
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="amount_base_units"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(
        text("UPDATE escrow_deals SET amount_base_units = '2000000000000000000' WHERE id = :id"),
        {"id": deal_id}
    )
    connection.commit()

    # 7. Non-numeric characters in base units
    connection.execute(
        text("UPDATE escrow_deals SET amount_base_units = '2000000000000000000abc' WHERE id = :id"),
        {"id": deal_id}
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="amount_base_units"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(
        text("UPDATE escrow_deals SET amount_base_units = '2000000000000000000' WHERE id = :id"),
        {"id": deal_id}
    )
    connection.commit()

    # 8. Negative base units string
    connection.execute(
        text("UPDATE escrow_deals SET amount_base_units = '-2000000000000000000' WHERE id = :id"),
        {"id": deal_id}
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="amount_base_units"):
        command.downgrade(cfg, "20261123_0001")
    connection.execute(
        text("UPDATE escrow_deals SET amount_base_units = '2000000000000000000' WHERE id = :id"),
        {"id": deal_id}
    )
    connection.commit()

    # 6. With all guards satisfied (pure historical ETH data), downgrade cleanly!
    command.downgrade(cfg, "20261123_0001")

    # Verify columns were cleanly dropped and original columns remain intact
    insp = sa.inspect(connection)
    req_cols = [c["name"] for c in insp.get_columns("requirements")]
    assert "currency" not in req_cols
    assert "budget_min_base_units" not in req_cols
    assert "budget_min" in req_cols
    assert "budget_max" in req_cols

    deal_cols = [c["name"] for c in insp.get_columns("escrow_deals")]
    assert "token_symbol" not in deal_cols
    assert "amount_base_units" not in deal_cols
    assert "amount_eth" in deal_cols

    connection.close()



# ─── Additional Detailed Coverage Tests ──────────────────────────────────────

def test_sqlite_in_memory_orm_crud_roundtrip():
    """Verify full SQLAlchemy session CRUD operations for ETH, USDC, and USDT models."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    u_buyer = User(
        email="buyer_crud@test.com",
        display_name="Buyer CRUD",
        hashed_password="pw",
        primary_role=UserRole.BUYER,
    )
    u_seller = User(
        email="seller_crud@test.com",
        display_name="Seller CRUD",
        hashed_password="pw",
        primary_role=UserRole.SELLER,
    )
    session.add_all([u_buyer, u_seller])
    session.commit()

    # 1. ETH Requirement
    req_eth = Requirement(
        buyer_id=u_buyer.id,
        title="ETH Work",
        description="ETH description",
        category="Dev",
        currency="ETH",
        token_decimals=18,
        budget_min=0.5,
        budget_max=1.0,
        budget_min_base_units="500000000000000000",
        budget_max_base_units="1000000000000000000",
    )
    # 2. USDT Requirement (zero min budget)
    req_usdt = Requirement(
        buyer_id=u_buyer.id,
        title="USDT Work",
        description="USDT description",
        category="Dev",
        currency="USDT",
        token_address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
        token_decimals=6,
        chain_id=1,
        budget_min=0.0,
        budget_max=100.0,
        budget_min_base_units="0",
        budget_max_base_units="100000000",
    )
    session.add_all([req_eth, req_usdt])
    session.commit()

    # Query back
    reloaded_usdt = session.scalar(select(Requirement).where(Requirement.id == req_usdt.id))
    assert reloaded_usdt is not None
    assert reloaded_usdt.currency == "USDT"
    assert reloaded_usdt.token_decimals == 6
    assert reloaded_usdt.budget_min_base_units == "0"
    assert reloaded_usdt.budget_max_base_units == "100000000"

    # 3. Create Offer
    off_usdt = Offer(
        requirement_id=req_usdt.id,
        seller_id=u_seller.id,
        price_eth=50.0,
        currency="USDT",
        token_address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
        token_decimals=6,
        chain_id=1,
        price_base_units="50000000",
        delivery_deadline=1900000000,
        terms="USDT deliverable terms",
    )
    session.add(off_usdt)
    session.commit()

    reloaded_off = session.scalar(select(Offer).where(Offer.id == off_usdt.id))
    assert reloaded_off.price_base_units == "50000000"
    assert reloaded_off.token_decimals == 6

    # 4. Consent unique constraint test (versioned consent)
    consent1 = AgreementConsent(
        offer_id=off_usdt.id,
        user_id=u_buyer.id,
        party_role="buyer",
        signer_wallet="0x1111111111111111111111111111111111111111",
        agreement_hash="0x" + "1" * 64,
        chain_id=1,
        nonce="0x" + "1" * 64,
        signature="0x" + "1" * 130,
        deadline=1900000000,
        agreement_version=1,
    )
    session.add(consent1)
    session.commit()

    # Amendment consent version 2 for the same party and offer is PERMITTED
    consent2 = AgreementConsent(
        offer_id=off_usdt.id,
        user_id=u_buyer.id,
        party_role="buyer",
        signer_wallet="0x1111111111111111111111111111111111111111",
        agreement_hash="0x" + "2" * 64,
        chain_id=1,
        nonce="0x" + "2" * 64,
        signature="0x" + "2" * 130,
        deadline=1900000000,
        agreement_version=2,
        previous_agreement_hash="0x" + "1" * 64,
    )
    session.add(consent2)
    session.commit()

    # Duplicate consent for same party, offer, AND agreement_version must fail!
    consent_dup = AgreementConsent(
        offer_id=off_usdt.id,
        user_id=u_buyer.id,
        party_role="buyer",
        signer_wallet="0x1111111111111111111111111111111111111111",
        agreement_hash="0x" + "3" * 64,
        chain_id=1,
        nonce="0x" + "3" * 64,
        signature="0x" + "3" * 130,
        deadline=1900000000,
        agreement_version=1,
    )
    session.add(consent_dup)
    with pytest.raises(Exception):  # IntegrityError
        session.commit()
    session.rollback()

    session.close()


def test_migration_prevalidation_rejections():
    """Verify Alembic pre-validation rejects invalid historical data prior to migration."""
    engine = create_engine("sqlite:///:memory:")
    conn = engine.connect()

    from pathlib import Path
    b_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(b_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(b_dir / "alembic"))
    cfg.attributes["connection"] = conn

    # Setup tables
    conn.execute(text("""
        CREATE TABLE users (
            id VARCHAR(36) PRIMARY KEY,
            email VARCHAR(254) NOT NULL UNIQUE,
            display_name VARCHAR(64) NOT NULL,
            hashed_password VARCHAR(128) NOT NULL,
            primary_role VARCHAR(16) NOT NULL,
            wallet_address VARCHAR(42) UNIQUE,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """))
    conn.execute(text("""
        CREATE TABLE requirements (
            id VARCHAR(36) PRIMARY KEY,
            buyer_id VARCHAR(36) NOT NULL,
            title VARCHAR(256) NOT NULL,
            description TEXT NOT NULL,
            category VARCHAR(128) NOT NULL,
            keywords TEXT,
            budget_min NUMERIC(20, 8),
            budget_max NUMERIC(20, 8),
            status VARCHAR(16) NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """))
    conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
    conn.execute(text("INSERT INTO alembic_version VALUES ('20261123_0001')"))

    # Case 1: Inverted budgets (min > max)
    conn.execute(text("""
        INSERT INTO requirements (id, buyer_id, title, description, category, budget_min, budget_max, status, created_at, updated_at)
        VALUES ('req-inv', 'u1', 'Title', 'Desc', 'Cat', 10.0, 5.0, 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """))
    conn.commit()

    with pytest.raises(ValueError, match="inverted budgets"):
        command.upgrade(cfg, "20261124_0001")

    # Fix inverted budget and insert negative budget
    conn.execute(text("DELETE FROM requirements"))
    conn.execute(text("""
        INSERT INTO requirements (id, buyer_id, title, description, category, budget_min, budget_max, status, created_at, updated_at)
        VALUES ('req-neg', 'u1', 'Title', 'Desc', 'Cat', -1.0, 5.0, 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """))
    conn.commit()

    with pytest.raises(ValueError, match="negative budget"):
        command.upgrade(cfg, "20261124_0001")

    conn.close()



def test_model_precision_defaults_and_validation():
    """
    Test exact Decimal defaults and validation on representative values:
    - 0.29 ETH
    - 1.5 ETH
    - 2 ETH
    - Valid amount with 18 decimal places
    - Amount with 19 decimal places (must raise ValueError)
    - Negative amount (must raise ValueError)
    - Exceeding uint256 (must raise ValueError)
    """
    from app.core.numeric import eth_amount_to_base_units_str

    # 1. 0.29 ETH (floating-point hazard test: 0.29 * 10**18 in float gives 289999999999999968)
    # With Decimal, it must be exactly 290000000000000000
    bu_029 = eth_amount_to_base_units_str(0.29)
    assert bu_029 == "290000000000000000"

    # 2. 1.5 ETH
    bu_15 = eth_amount_to_base_units_str(1.5)
    assert bu_15 == "1500000000000000000"

    # 3. 2 ETH
    bu_2 = eth_amount_to_base_units_str(2)
    assert bu_2 == "2000000000000000000"

    # 4. Valid amount with exactly 18 decimal places (0.000000000000000001 ETH -> 1 wei)
    bu_18 = eth_amount_to_base_units_str("0.000000000000000001")
    assert bu_18 == "1"

    # 5. Amount with 19 decimal places must raise ValueError
    with pytest.raises(ValueError, match="more than 18 decimal places"):
        eth_amount_to_base_units_str("0.0000000000000000001")

    # 6. Negative amount must raise ValueError
    with pytest.raises(ValueError, match="cannot be negative"):
        eth_amount_to_base_units_str("-1.5")

    # 7. Exceeding uint256 must raise ValueError
    with decimal.localcontext(decimal.Context(prec=100)):
        too_large_eth = str(Decimal(1 << 256) / Decimal("1000000000000000000"))
    with pytest.raises(ValueError, match="exceeds uint256"):
        eth_amount_to_base_units_str(too_large_eth)


def test_missing_financial_input_rejection_and_orm_defaults():
    """
    Phase 6.2.2.2.4: Missing Financial Input Rejection & ORM Default Audit.
    Proves:
    1. Offer insertion without price_eth does not silently create price_base_units="0", but raises explicit ValueError.
    2. Offer insertion with price_eth=None raises explicit ValueError.
    3. EscrowDeal insertion without amount_eth does not silently create amount_base_units="0", but raises explicit ValueError.
    4. EscrowDeal insertion with amount_eth=None raises explicit ValueError.
    5. Explicit zero in ORM sets base units to "0" (distinguishable from missing input).
    6. Explicit zero in API / business validation (OfferCreate, EscrowDealCreate) is rejected by business rules (gt=0).
    7. Valid ETH values produce exact base-unit strings via Decimal conversion without float hazards.
    8. Explicit base_units parameter bypasses derivation and is validated.
    """
    from pydantic import ValidationError
    from app.schemas.offers import OfferCreate
    from app.schemas.escrow import EscrowDealCreate

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    u_buyer = User(
        email="buyer_def@test.com",
        display_name="Buyer Default",
        hashed_password="pw",
        primary_role=UserRole.BUYER,
    )
    u_seller = User(
        email="seller_def@test.com",
        display_name="Seller Default",
        hashed_password="pw",
        primary_role=UserRole.SELLER,
    )
    session.add_all([u_buyer, u_seller])
    session.commit()

    req = Requirement(
        buyer_id=u_buyer.id,
        title="Test Req",
        description="Test Desc",
        category="Dev",
        budget_min=1.0,
        budget_max=2.0,
        budget_min_base_units="1000000000000000000",
        budget_max_base_units="2000000000000000000",
    )
    session.add(req)
    session.commit()

    # 1. Offer insertion WITHOUT price_eth must NOT silently create price_base_units="0"
    off_missing_price = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        delivery_deadline=1900000000,
        terms="Missing price terms",
    )
    session.add(off_missing_price)
    with pytest.raises((ValueError, sa.exc.StatementError), match="price_eth is required to derive price_base_units"):
        session.commit()
    session.rollback()

    # 2. Offer insertion with price_eth=None must raise explicit ValueError
    off_none_price = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=None,
        delivery_deadline=1900000000,
        terms="None price terms",
    )
    session.add(off_none_price)
    with pytest.raises((ValueError, sa.exc.StatementError), match="price_eth is required to derive price_base_units"):
        session.commit()
    session.rollback()

    # 3. Create a valid offer for subsequent EscrowDeal tests
    valid_off = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=1.5,
        delivery_deadline=1900000000,
        terms="Valid 1.5 ETH terms",
    )
    session.add(valid_off)
    session.commit()
    assert valid_off.price_base_units == "1500000000000000000"

    # 4. EscrowDeal insertion WITHOUT amount_eth must NOT silently create amount_base_units="0"
    deal_missing_amount = EscrowDeal(
        offer_id=valid_off.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        deadline_ts=1900000000,
    )
    session.add(deal_missing_amount)
    with pytest.raises((ValueError, sa.exc.StatementError), match="amount_eth is required to derive amount_base_units"):
        session.commit()
    session.rollback()

    # 5. EscrowDeal insertion with amount_eth=None must raise explicit ValueError
    deal_none_amount = EscrowDeal(
        offer_id=valid_off.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        amount_eth=None,
        deadline_ts=1900000000,
    )
    session.add(deal_none_amount)
    with pytest.raises((ValueError, sa.exc.StatementError), match="amount_eth is required to derive amount_base_units"):
        session.commit()
    session.rollback()

    # 6. Explicit zero in ORM: produces "0" (distinguishable from missing input error)
    off_zero = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=0.0,
        delivery_deadline=1900000000,
        terms="Explicit zero price terms",
    )
    session.add(off_zero)
    session.commit()
    assert off_zero.price_base_units == "0"

    # 7. Business validation: OfferCreate rejects explicit zero price
    with pytest.raises(ValidationError) as exc_off:
        OfferCreate(
            requirement_id=req.id,
            price_eth=0.0,
            delivery_deadline=1900000000,
            terms="Valid length terms here",
        )
    assert "greater than 0" in str(exc_off.value)

    # 8. Business validation: EscrowDealCreate rejects explicit zero amount
    with pytest.raises(ValidationError) as exc_deal:
        EscrowDealCreate(
            offer_id=valid_off.id,
            amount_eth=0.0,
        )
    assert "greater than 0" in str(exc_deal.value)

    # 9. Explicit base_units provided directly is preserved and validated
    off_explicit_bu = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=2.0,
        price_base_units="2000000000000000000",
        delivery_deadline=1900000000,
        terms="Explicit base units terms",
    )
    session.add(off_explicit_bu)
    session.commit()
    assert off_explicit_bu.price_base_units == "2000000000000000000"

    session.close()


def test_dual_financial_representation_consistency_and_validation():
    """
    Phase 6.2.2.2.5: Dual Financial Representation Consistency Audit & Hardening.
    Covers:
    1. Matching price_eth and price_base_units (ETH and non-ETH ERC20).
    2. Conflicting price_eth and price_base_units (including 1-wei discrepancy).
    3. Matching amount_eth and amount_base_units.
    4. Conflicting amount_eth and amount_base_units (including 1-wei discrepancy).
    5. Base-unit-only creation where explicitly supported (AgreementConsent and syntax validation).
    6. ETH-only creation with exact derivation (0.29 ETH floating hazard resistance).
    7. Missing both representations.
    8. Explicit zero behavior (matching vs. conflicting).
    9. Existing V1/V2 offer and escrow creation regression & update conflict rejection.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    u_buyer = User(
        email="buyer_dual@test.com",
        display_name="Buyer Dual",
        hashed_password="pw",
        primary_role=UserRole.BUYER,
    )
    u_seller = User(
        email="seller_dual@test.com",
        display_name="Seller Dual",
        hashed_password="pw",
        primary_role=UserRole.SELLER,
    )
    session.add_all([u_buyer, u_seller])
    session.commit()

    req = Requirement(
        buyer_id=u_buyer.id,
        title="Dual Financial Req",
        description="Dual description",
        category="Dev",
        budget_min=1.0,
        budget_max=5.0,
    )
    session.add(req)
    session.commit()

    # 1. Matching price_eth and price_base_units
    # 1a. ETH matching
    off_match_eth = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=1.5,
        price_base_units="1500000000000000000",
        delivery_deadline=1900000000,
        terms="Matching ETH offer",
    )
    session.add(off_match_eth)
    session.commit()
    assert off_match_eth.price_base_units == "1500000000000000000"

    # 1b. Non-ETH (USDT 6 decimals) matching
    off_match_usdt = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        currency="USDT",
        token_decimals=6,
        price_eth=50.0,
        price_base_units="50000000",
        delivery_deadline=1900000000,
        terms="Matching USDT offer",
    )
    session.add(off_match_usdt)
    session.commit()
    assert off_match_usdt.price_base_units == "50000000"

    # 2. Conflicting price_eth and price_base_units
    # 2a. Gross conflict (1.5 ETH vs 2.0 ETH base units)
    off_conflict_gross = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=1.5,
        price_base_units="2000000000000000000",
        delivery_deadline=1900000000,
        terms="Gross conflict offer",
    )
    session.add(off_conflict_gross)
    with pytest.raises((ValueError, sa.exc.StatementError), match="Conflicting financial values for Offer"):
        session.commit()
    session.rollback()

    # 2b. 1-wei discrepancy (1.5 ETH vs 1500000000000000001)
    off_conflict_1wei = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=1.5,
        price_base_units="1500000000000000001",
        delivery_deadline=1900000000,
        terms="1-wei conflict offer",
    )
    session.add(off_conflict_1wei)
    with pytest.raises((ValueError, sa.exc.StatementError), match="Conflicting financial values for Offer"):
        session.commit()
    session.rollback()

    # 3. Matching amount_eth and amount_base_units
    deal_match_eth = EscrowDeal(
        offer_id=off_match_eth.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        amount_eth=1.5,
        amount_base_units="1500000000000000000",
        deadline_ts=1900000000,
    )
    session.add(deal_match_eth)
    session.commit()
    assert deal_match_eth.amount_base_units == "1500000000000000000"

    # 4. Conflicting amount_eth and amount_base_units
    # 4a. Gross conflict
    deal_conflict_gross = EscrowDeal(
        offer_id=off_match_usdt.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        amount_eth=1.5,
        amount_base_units="1000000000000000000",
        deadline_ts=1900000000,
    )
    session.add(deal_conflict_gross)
    with pytest.raises((ValueError, sa.exc.StatementError), match="Conflicting financial values for EscrowDeal"):
        session.commit()
    session.rollback()

    # 4b. 1-wei discrepancy
    deal_conflict_1wei = EscrowDeal(
        offer_id=off_match_usdt.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        amount_eth=1.5,
        amount_base_units="1500000000000000001",
        deadline_ts=1900000000,
    )
    session.add(deal_conflict_1wei)
    with pytest.raises((ValueError, sa.exc.StatementError), match="Conflicting financial values for EscrowDeal"):
        session.commit()
    session.rollback()

    # 5. Base-unit-only creation where explicitly supported
    # 5a. AgreementConsent explicitly supports base-unit-only without amount_eth
    consent_valid = AgreementConsent(
        offer_id=off_match_eth.id,
        user_id=u_buyer.id,
        party_role="buyer",
        signer_wallet="0x1111111111111111111111111111111111111111",
        agreement_hash="0x" + "a" * 64,
        chain_id=1,
        nonce="0x" + "1" * 64,
        signature="0x" + "1" * 130,
        deadline=1900000000,
        agreement_version=1,
        amount_base_units="1500000000000000000",
    )
    session.add(consent_valid)
    session.commit()
    assert consent_valid.amount_base_units == "1500000000000000000"

    # 5b. AgreementConsent with invalid base units rejected
    with pytest.raises(ValueError):
        validate_base_units_str("-100", field_name="amount_base_units")

    # 6. ETH-only creation with exact derivation (0.29 ETH floating hazard resistance)
    off_eth_only = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=0.29,
        delivery_deadline=1900000000,
        terms="ETH only 0.29 offer",
    )
    session.add(off_eth_only)
    session.commit()
    # Must be exact 290000000000000000, not 289999999999999968
    assert off_eth_only.price_base_units == "290000000000000000"

    deal_eth_only = EscrowDeal(
        offer_id=off_eth_only.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        amount_eth=0.29,
        deadline_ts=1900000000,
    )
    session.add(deal_eth_only)
    session.commit()
    assert deal_eth_only.amount_base_units == "290000000000000000"

    # 7. Missing both representations
    off_missing_both = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=None,
        price_base_units=None,
        delivery_deadline=1900000000,
        terms="Missing both offer",
    )
    session.add(off_missing_both)
    with pytest.raises((ValueError, sa.exc.StatementError), match="both price_eth and price_base_units are missing"):
        session.commit()
    session.rollback()

    deal_missing_both = EscrowDeal(
        offer_id=off_match_eth.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        amount_eth=None,
        amount_base_units=None,
        deadline_ts=1900000000,
    )
    session.add(deal_missing_both)
    with pytest.raises((ValueError, sa.exc.StatementError), match="both amount_eth and amount_base_units are missing"):
        session.commit()
    session.rollback()

    # 8. Explicit zero behavior
    # 8a. Explicit zero matching
    off_zero_match = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=0.0,
        price_base_units="0",
        delivery_deadline=1900000000,
        terms="Zero matching offer",
    )
    session.add(off_zero_match)
    session.commit()
    assert off_zero_match.price_base_units == "0"

    # 8b. Explicit zero conflicting with non-zero base units
    off_zero_conflict = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=0.0,
        price_base_units="1",
        delivery_deadline=1900000000,
        terms="Zero conflicting offer",
    )
    session.add(off_zero_conflict)
    with pytest.raises((ValueError, sa.exc.StatementError), match="Conflicting financial values for Offer"):
        session.commit()
    session.rollback()

    # 9. Existing V1/V2 offer and escrow creation regression & update conflict rejection
    # 9a. Updating existing offer with conflicting values must fail
    off_match_eth.price_eth = 3.0
    # Note: price_base_units is still "1500000000000000000"
    with pytest.raises(ValueError, match="Conflicting financial values for Offer"):
        session.commit()
    session.rollback()

    # 9b. Updating existing offer with consistent values succeeds
    reloaded_off = session.scalar(select(Offer).where(Offer.id == off_match_eth.id))
    reloaded_off.price_eth = 3.0
    reloaded_off.price_base_units = "3000000000000000000"
    session.commit()
    assert reloaded_off.price_base_units == "3000000000000000000"

    session.close()


def test_core_insert_and_base_unit_only_verification():
    """
    Phase 6.2.2.2.6: Core Insert & Base-Unit-Only Model Verification.
    1. Actual ORM base-unit-only creation:
       - Offer with price_base_units supplied and price_eth=None:
         Accepted at ORM validation level, but fails at database level with IntegrityError (NOT NULL constraint).
       - EscrowDeal with amount_base_units supplied and amount_eth=None:
         Accepted at ORM validation level, but fails at database level with IntegrityError (NOT NULL constraint).
       - Missing both representations fails with ValueError.
    2. ERC20 EscrowDeal consistency:
       - currency="USDT", token_decimals=6, amount_eth=50, amount_base_units="50000000" -> Accepted.
       - 1-unit discrepancy: amount_base_units="50000001" -> Rejected with ValueError.
    3. SQLAlchemy Core insert verification:
       - Offer: Matching ETH and base-unit representations -> Accepted.
       - Offer: Conflicting representations (including 1-wei discrepancy) -> Rejected with ValueError.
       - Offer: Missing both representations -> Rejected with StatementError(ValueError).
       - Offer: ETH-only insertion -> Derives exact base-unit string via execution-context default.
       - EscrowDeal: Matching representations -> Accepted.
       - EscrowDeal: Conflicting representations (gross and 1-wei) -> Rejected with ValueError.
       - EscrowDeal: 6-decimal USDT matching vs 1-unit discrepancy -> Accepted vs Rejected with ValueError.
    """
    from sqlalchemy import insert
    from sqlalchemy.exc import IntegrityError, StatementError

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    u_buyer = User(
        email="buyer_core@test.com",
        display_name="Buyer Core",
        hashed_password="pw",
        primary_role=UserRole.BUYER,
    )
    u_seller = User(
        email="seller_core@test.com",
        display_name="Seller Core",
        hashed_password="pw",
        primary_role=UserRole.SELLER,
    )
    session.add_all([u_buyer, u_seller])
    session.commit()

    req = Requirement(
        buyer_id=u_buyer.id,
        title="Core Req",
        description="Core description",
        category="Dev",
        budget_min=1.0,
        budget_max=100.0,
    )
    session.add(req)
    session.commit()

    # ─── 1. Actual ORM base-unit-only creation ───
    # 1a. Offer with price_base_units supplied and price_eth=None
    # ORM validation accepts valid base units string, but database rejects NULL for legacy NOT NULL price_eth
    off_bu_only = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=None,
        price_base_units="1500000000000000000",
        delivery_deadline=1900000000,
        terms="Base unit only offer",
    )
    session.add(off_bu_only)
    with pytest.raises((IntegrityError, StatementError)):
        session.commit()
    session.rollback()

    # 1b. Offer with invalid base units string fails at ORM validation level
    off_invalid_bu = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=None,
        price_base_units="-1500000000000000000",
        delivery_deadline=1900000000,
        terms="Invalid base unit only offer",
    )
    session.add(off_invalid_bu)
    with pytest.raises(ValueError):
        session.commit()
    session.rollback()

    # Valid offer for subsequent escrow tests
    valid_off = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=50.0,
        currency="USDT",
        token_decimals=6,
        price_base_units="50000000",
        delivery_deadline=1900000000,
        terms="Valid USDT offer",
    )
    session.add(valid_off)
    session.commit()

    # 1c. EscrowDeal with amount_base_units supplied and amount_eth=None
    deal_bu_only = EscrowDeal(
        offer_id=valid_off.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        amount_eth=None,
        amount_base_units="50000000",
        deadline_ts=1900000000,
    )
    session.add(deal_bu_only)
    with pytest.raises((IntegrityError, StatementError)):
        session.commit()
    session.rollback()

    # 1d. Missing both representations still fails with ValueError
    deal_missing_both = EscrowDeal(
        offer_id=valid_off.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        amount_eth=None,
        amount_base_units=None,
        deadline_ts=1900000000,
    )
    session.add(deal_missing_both)
    with pytest.raises((ValueError, StatementError), match="both amount_eth and amount_base_units are missing"):
        session.commit()
    session.rollback()

    # ─── 2. ERC20 EscrowDeal consistency ───
    # 2a. Matching: currency="USDT", token_decimals=6, amount_eth=50, amount_base_units="50000000"
    deal_erc20_match = EscrowDeal(
        offer_id=valid_off.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        currency="USDT",
        token_decimals=6,
        amount_eth=50.0,
        amount_base_units="50000000",
        deadline_ts=1900000000,
    )
    session.add(deal_erc20_match)
    session.commit()
    assert deal_erc20_match.amount_base_units == "50000000"

    # 2b. 1-unit discrepancy: amount_base_units="50000001"
    deal_erc20_conflict = EscrowDeal(
        offer_id=valid_off.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        currency="USDT",
        token_decimals=6,
        amount_eth=50.0,
        amount_base_units="50000001",
        deadline_ts=1900000000,
    )
    session.add(deal_erc20_conflict)
    with pytest.raises((ValueError, StatementError), match="Conflicting financial values for EscrowDeal"):
        session.commit()
    session.rollback()

    # ─── 3. SQLAlchemy Core insert verification ───
    with engine.connect() as conn:
        # 3a. Offer Core insert: Matching ETH representations
        off_core_id = uuid.uuid4()
        stmt_off_match = insert(Offer).values(
            id=off_core_id,
            requirement_id=req.id,
            seller_id=u_seller.id,
            price_eth=1.5,
            price_base_units="1500000000000000000",
            delivery_deadline=1900000000,
            terms="Core matching terms",
        )
        conn.execute(stmt_off_match)
        res_bu = conn.execute(select(Offer.price_base_units).where(Offer.id == off_core_id)).scalar_one()
        assert res_bu == "1500000000000000000"

        # 3b. Offer Core insert: Conflicting representations (gross and 1-wei)
        stmt_off_gross = insert(Offer).values(
            id=uuid.uuid4(),
            requirement_id=req.id,
            seller_id=u_seller.id,
            price_eth=1.5,
            price_base_units="2000000000000000000",
            delivery_deadline=1900000000,
            terms="Core gross conflict",
        )
        with pytest.raises(ValueError, match="Conflicting financial values for Offer"):
            conn.execute(stmt_off_gross)

        stmt_off_1wei = insert(Offer).values(
            id=uuid.uuid4(),
            requirement_id=req.id,
            seller_id=u_seller.id,
            price_eth=1.5,
            price_base_units="1500000000000000001",
            delivery_deadline=1900000000,
            terms="Core 1wei conflict",
        )
        with pytest.raises(ValueError, match="Conflicting financial values for Offer"):
            conn.execute(stmt_off_1wei)

        # 3c. Offer Core insert: Missing both representations
        stmt_off_missing = insert(Offer).values(
            id=uuid.uuid4(),
            requirement_id=req.id,
            seller_id=u_seller.id,
            delivery_deadline=1900000000,
            terms="Core missing both",
        )
        with pytest.raises((ValueError, StatementError), match="both price_eth and price_base_units are missing"):
            conn.execute(stmt_off_missing)

        # 3d. Offer Core insert: ETH-only insertion derives exact base units
        off_eth_id = uuid.uuid4()
        stmt_off_eth = insert(Offer).values(
            id=off_eth_id,
            requirement_id=req.id,
            seller_id=u_seller.id,
            price_eth=0.29,
            delivery_deadline=1900000000,
            terms="Core eth only",
        )
        conn.execute(stmt_off_eth)
        res_eth_bu = conn.execute(select(Offer.price_base_units).where(Offer.id == off_eth_id)).scalar_one()
        assert res_eth_bu == "290000000000000000"

        # 3e. EscrowDeal Core insert: Matching representations
        deal_core_id = uuid.uuid4()
        stmt_deal_match = insert(EscrowDeal).values(
            id=deal_core_id,
            offer_id=off_core_id,
            buyer_id=u_buyer.id,
            seller_id=u_seller.id,
            amount_eth=1.5,
            amount_base_units="1500000000000000000",
            deadline_ts=1900000000,
        )
        conn.execute(stmt_deal_match)
        res_deal_bu = conn.execute(select(EscrowDeal.amount_base_units).where(EscrowDeal.id == deal_core_id)).scalar_one()
        assert res_deal_bu == "1500000000000000000"

        # 3f. EscrowDeal Core insert: Conflicting representations (1-wei)
        off_conflict_id = uuid.uuid4()
        conn.execute(
            insert(Offer).values(
                id=off_conflict_id,
                requirement_id=req.id,
                seller_id=u_seller.id,
                price_eth=1.5,
                delivery_deadline=1900000000,
                terms="Conflict offer",
            )
        )
        stmt_deal_conflict = insert(EscrowDeal).values(
            id=uuid.uuid4(),
            offer_id=off_conflict_id,
            buyer_id=u_buyer.id,
            seller_id=u_seller.id,
            amount_eth=1.5,
            amount_base_units="1500000000000000001",
            deadline_ts=1900000000,
        )
        with pytest.raises(ValueError, match="Conflicting financial values for EscrowDeal"):
            conn.execute(stmt_deal_conflict)

        # 3g. EscrowDeal Core insert: 6-decimal USDT matching vs 1-unit conflict
        off_usdt1_id = uuid.uuid4()
        conn.execute(
            insert(Offer).values(
                id=off_usdt1_id,
                requirement_id=req.id,
                seller_id=u_seller.id,
                price_eth=50.0,
                currency="USDT",
                token_decimals=6,
                price_base_units="50000000",
                delivery_deadline=1900000000,
                terms="Core USDT terms 1",
            )
        )
        stmt_usdt_match = insert(EscrowDeal).values(
            id=uuid.uuid4(),
            offer_id=off_usdt1_id,
            buyer_id=u_buyer.id,
            seller_id=u_seller.id,
            currency="USDT",
            token_decimals=6,
            amount_eth=50.0,
            amount_base_units="50000000",
            deadline_ts=1900000000,
        )
        conn.execute(stmt_usdt_match)

        off_usdt2_id = uuid.uuid4()
        conn.execute(
            insert(Offer).values(
                id=off_usdt2_id,
                requirement_id=req.id,
                seller_id=u_seller.id,
                price_eth=50.0,
                currency="USDT",
                token_decimals=6,
                price_base_units="50000000",
                delivery_deadline=1900000000,
                terms="Core USDT terms 2",
            )
        )
        stmt_usdt_conflict = insert(EscrowDeal).values(
            id=uuid.uuid4(),
            offer_id=off_usdt2_id,
            buyer_id=u_buyer.id,
            seller_id=u_seller.id,
            currency="USDT",
            token_decimals=6,
            amount_eth=50.0,
            amount_base_units="50000001",
            deadline_ts=1900000000,
        )
        with pytest.raises(ValueError, match="Conflicting financial values for EscrowDeal"):
            conn.execute(stmt_usdt_conflict)

    session.close()


def test_partial_core_update_financial_integrity_verification():
    """
    Phase 6.2.2.2.7: Partial Core UPDATE Financial Integrity Verification.

    Verifies that SQLAlchemy Core UPDATE statements cannot leave offers or escrow_deals
    with inconsistent legacy and base-unit financial representations:

    1. Offer Adversarial UPDATE Matrix:
       1a. Update only price_eth to different amount -> Rejected atomically (ValueError).
       1b. Update only price_base_units to different amount -> Rejected atomically (ValueError).
       1c. Update both fields with matching values -> Accepted, database state verified.
       1d. Update both fields with conflicting values -> Rejected atomically (ValueError).
       1e. Update unrelated fields without changing financial values -> Accepted, financial values intact.

    2. EscrowDeal Adversarial UPDATE Matrix:
       2a. Update only amount_eth to different amount -> Rejected atomically (ValueError).
       2b. Update only amount_base_units to different amount -> Rejected atomically (ValueError).
       2c. Update both fields with matching values -> Accepted, database state verified.
       2d. Update both fields with conflicting values (gross & 1-wei) -> Rejected atomically (ValueError).
       2e. Update unrelated fields without changing financial values -> Accepted, financial values intact.

    3. Additional Execution-Path Checks:
       3a. Parameter-dictionary execution (partial rejected, matching accepted, unrelated accepted).
       3b. Executemany execution (batch partial rejected, batch matching accepted).
       3c. ERC20 multi-token UPDATE (6-decimal USDT matching accepted, 1-unit conflict rejected, partial rejected).
    """
    from sqlalchemy import insert, update, bindparam, select
    from sqlalchemy.exc import StatementError

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    u_buyer = User(
        email="buyer_upd@test.com",
        display_name="Buyer Update",
        hashed_password="pw",
        primary_role=UserRole.BUYER,
    )
    u_seller = User(
        email="seller_upd@test.com",
        display_name="Seller Update",
        hashed_password="pw",
        primary_role=UserRole.SELLER,
    )
    session.add_all([u_buyer, u_seller])
    session.commit()

    req = Requirement(
        buyer_id=u_buyer.id,
        title="Update Test Req",
        description="Testing updates",
        category="Dev",
        budget_min=1.0,
        budget_max=100.0,
    )
    session.add(req)
    session.commit()

    off1 = Offer(
        requirement_id=req.id,
        seller_id=u_seller.id,
        price_eth=1.0,
        price_base_units="1000000000000000000",
        delivery_deadline=1900000000,
        terms="Initial Offer Terms",
        status=OfferStatus.PENDING,
    )
    session.add(off1)
    session.commit()

    deal1 = EscrowDeal(
        offer_id=off1.id,
        buyer_id=u_buyer.id,
        seller_id=u_seller.id,
        amount_eth=1.0,
        amount_base_units="1000000000000000000",
        token_symbol="ETH",
        token_decimals=18,
        deadline_ts=1900000000,
        on_chain_state=None,
    )
    session.add(deal1)
    session.commit()

    with engine.connect() as conn:
        # ─── 1. Offer Adversarial Tests ───
        # 1a. Update only price_eth to different amount -> rejected
        stmt_off_eth_only = update(Offer).where(Offer.id == off1.id).values(price_eth=2.5)
        with pytest.raises(ValueError, match="Partial Core UPDATE of financial fields is prohibited"):
            conn.execute(stmt_off_eth_only)

        # Verify persisted state unchanged
        row = conn.execute(select(Offer.price_eth, Offer.price_base_units).where(Offer.id == off1.id)).fetchone()
        assert float(row[0]) == 1.0
        assert row[1] == "1000000000000000000"

        # 1b. Update only price_base_units to different amount -> rejected
        stmt_off_bu_only = update(Offer).where(Offer.id == off1.id).values(price_base_units="2500000000000000000")
        with pytest.raises(ValueError, match="Partial Core UPDATE of financial fields is prohibited"):
            conn.execute(stmt_off_bu_only)

        # Verify persisted state unchanged
        row = conn.execute(select(Offer.price_eth, Offer.price_base_units).where(Offer.id == off1.id)).fetchone()
        assert float(row[0]) == 1.0
        assert row[1] == "1000000000000000000"

        # 1c. Update both fields with matching values -> accepted
        stmt_off_match = update(Offer).where(Offer.id == off1.id).values(
            price_eth=2.5,
            price_base_units="2500000000000000000",
        )
        conn.execute(stmt_off_match)
        conn.commit()

        # Verify persisted state updated
        row = conn.execute(select(Offer.price_eth, Offer.price_base_units).where(Offer.id == off1.id)).fetchone()
        assert float(row[0]) == 2.5
        assert row[1] == "2500000000000000000"

        # 1d. Update both fields with conflicting values -> rejected
        stmt_off_conflict = update(Offer).where(Offer.id == off1.id).values(
            price_eth=3.0,
            price_base_units="4000000000000000000",
        )
        with pytest.raises(ValueError, match="Conflicting financial values for Offer"):
            conn.execute(stmt_off_conflict)

        # Verify persisted state unchanged
        row = conn.execute(select(Offer.price_eth, Offer.price_base_units).where(Offer.id == off1.id)).fetchone()
        assert float(row[0]) == 2.5
        assert row[1] == "2500000000000000000"

        # 1e. Update unrelated fields without changing either financial value -> accepted
        stmt_off_unrelated = update(Offer).where(Offer.id == off1.id).values(
            terms="Updated Terms for Offer",
            status=OfferStatus.ACCEPTED,
        )
        conn.execute(stmt_off_unrelated)
        conn.commit()

        # Verify persisted state: terms updated, financial representations unchanged
        row = conn.execute(select(Offer.terms, Offer.status, Offer.price_eth, Offer.price_base_units).where(Offer.id == off1.id)).fetchone()
        assert row[0] == "Updated Terms for Offer"
        assert row[1] == OfferStatus.ACCEPTED
        assert float(row[2]) == 2.5
        assert row[3] == "2500000000000000000"

        # ─── 2. EscrowDeal Adversarial Tests ───
        # 2a. Update only amount_eth to different amount -> rejected
        stmt_deal_eth_only = update(EscrowDeal).where(EscrowDeal.id == deal1.id).values(amount_eth=5.0)
        with pytest.raises(ValueError, match="Partial Core UPDATE of financial fields is prohibited"):
            conn.execute(stmt_deal_eth_only)

        # Verify persisted state unchanged
        row = conn.execute(select(EscrowDeal.amount_eth, EscrowDeal.amount_base_units).where(EscrowDeal.id == deal1.id)).fetchone()
        assert float(row[0]) == 1.0
        assert row[1] == "1000000000000000000"

        # 2b. Update only amount_base_units to different amount -> rejected
        stmt_deal_bu_only = update(EscrowDeal).where(EscrowDeal.id == deal1.id).values(amount_base_units="5000000000000000000")
        with pytest.raises(ValueError, match="Partial Core UPDATE of financial fields is prohibited"):
            conn.execute(stmt_deal_bu_only)

        # Verify persisted state unchanged
        row = conn.execute(select(EscrowDeal.amount_eth, EscrowDeal.amount_base_units).where(EscrowDeal.id == deal1.id)).fetchone()
        assert float(row[0]) == 1.0
        assert row[1] == "1000000000000000000"

        # 2c. Update both fields with matching values -> accepted
        stmt_deal_match = update(EscrowDeal).where(EscrowDeal.id == deal1.id).values(
            amount_eth=5.0,
            amount_base_units="5000000000000000000",
        )
        conn.execute(stmt_deal_match)
        conn.commit()

        # Verify persisted state updated
        row = conn.execute(select(EscrowDeal.amount_eth, EscrowDeal.amount_base_units).where(EscrowDeal.id == deal1.id)).fetchone()
        assert float(row[0]) == 5.0
        assert row[1] == "5000000000000000000"

        # 2d. Update both fields with conflicting values (1-wei mismatch) -> rejected
        stmt_deal_conflict = update(EscrowDeal).where(EscrowDeal.id == deal1.id).values(
            amount_eth=5.0,
            amount_base_units="5000000000000000001",
        )
        with pytest.raises(ValueError, match="Conflicting financial values for EscrowDeal"):
            conn.execute(stmt_deal_conflict)

        # Verify persisted state unchanged
        row = conn.execute(select(EscrowDeal.amount_eth, EscrowDeal.amount_base_units).where(EscrowDeal.id == deal1.id)).fetchone()
        assert float(row[0]) == 5.0
        assert row[1] == "5000000000000000000"

        # 2e. Update unrelated fields without changing either financial value -> accepted
        stmt_deal_unrelated = update(EscrowDeal).where(EscrowDeal.id == deal1.id).values(
            deadline_ts=2000000000,
            on_chain_state=EscrowState.FUNDED,
        )
        conn.execute(stmt_deal_unrelated)
        conn.commit()

        # Verify persisted state: deadline updated, financials unchanged
        row = conn.execute(select(EscrowDeal.deadline_ts, EscrowDeal.on_chain_state, EscrowDeal.amount_eth, EscrowDeal.amount_base_units).where(EscrowDeal.id == deal1.id)).fetchone()
        assert row[0] == 2000000000
        assert row[1] == EscrowState.FUNDED
        assert float(row[2]) == 5.0
        assert row[3] == "5000000000000000000"

        # ─── 3. Additional Execution-Path Checks ───
        # 3a. Parameter-dictionary execution
        # Partial update via parameter dict -> rejected
        with pytest.raises(ValueError, match="Partial Core UPDATE of financial fields is prohibited"):
            conn.execute(update(Offer).where(Offer.id == off1.id), {"price_eth": 8.0})

        # Matching update via parameter dict -> accepted
        conn.execute(
            update(Offer).where(Offer.id == off1.id),
            {"price_eth": 8.0, "price_base_units": "8000000000000000000"},
        )
        conn.commit()
        row = conn.execute(select(Offer.price_eth, Offer.price_base_units).where(Offer.id == off1.id)).fetchone()
        assert float(row[0]) == 8.0
        assert row[1] == "8000000000000000000"

        # Unrelated update via parameter dict -> accepted
        conn.execute(update(Offer).where(Offer.id == off1.id), {"terms": "Dict Param Terms"})
        conn.commit()
        row = conn.execute(select(Offer.terms, Offer.price_eth).where(Offer.id == off1.id)).fetchone()
        assert row[0] == "Dict Param Terms"
        assert float(row[1]) == 8.0

        # 3b. Executemany execution
        off2 = Offer(
            requirement_id=req.id,
            seller_id=u_seller.id,
            price_eth=1.0,
            price_base_units="1000000000000000000",
            delivery_deadline=1900000000,
            terms="Offer 2 for Executemany",
            status=OfferStatus.PENDING,
        )
        session.add(off2)
        session.commit()

        # Executemany partial update -> rejected
        with pytest.raises(ValueError, match="Partial Core UPDATE of financial fields is prohibited"):
            conn.execute(
                update(Offer).where(Offer.id == bindparam("b_id")),
                [
                    {"b_id": off1.id, "price_eth": 10.0},
                    {"b_id": off2.id, "price_eth": 11.0},
                ],
            )

        # Executemany matching update -> accepted
        conn.execute(
            update(Offer).where(Offer.id == bindparam("b_id")),
            [
                {"b_id": off1.id, "price_eth": 10.0, "price_base_units": "10000000000000000000"},
                {"b_id": off2.id, "price_eth": 11.0, "price_base_units": "11000000000000000000"},
            ],
        )
        conn.commit()
        row1 = conn.execute(select(Offer.price_eth, Offer.price_base_units).where(Offer.id == off1.id)).fetchone()
        row2 = conn.execute(select(Offer.price_eth, Offer.price_base_units).where(Offer.id == off2.id)).fetchone()
        assert float(row1[0]) == 10.0 and row1[1] == "10000000000000000000"
        assert float(row2[0]) == 11.0 and row2[1] == "11000000000000000000"

        # 3c. ERC20 multi-token UPDATE (6 decimals)
        off_usdt = Offer(
            requirement_id=req.id,
            seller_id=u_seller.id,
            price_eth=50.0,
            currency="USDT",
            token_decimals=6,
            price_base_units="50000000",
            delivery_deadline=1900000000,
            terms="USDT Offer for update",
        )
        session.add(off_usdt)
        session.commit()

        deal_usdt = EscrowDeal(
            offer_id=off_usdt.id,
            buyer_id=u_buyer.id,
            seller_id=u_seller.id,
            amount_eth=50.0,
            currency="USDT",
            token_decimals=6,
            amount_base_units="50000000",
            deadline_ts=1900000000,
        )
        session.add(deal_usdt)
        session.commit()

        # Matching 6-decimal update -> accepted
        conn.execute(
            update(EscrowDeal).where(EscrowDeal.id == deal_usdt.id).values(
                currency="USDT",
                token_decimals=6,
                amount_eth=75.0,
                amount_base_units="75000000",
            )
        )
        conn.commit()
        row_usdt = conn.execute(select(EscrowDeal.amount_eth, EscrowDeal.amount_base_units).where(EscrowDeal.id == deal_usdt.id)).fetchone()
        assert float(row_usdt[0]) == 75.0
        assert row_usdt[1] == "75000000"

        # 1-unit mismatch 6-decimal update -> rejected
        with pytest.raises(ValueError, match="Conflicting financial values for EscrowDeal"):
            conn.execute(
                update(EscrowDeal).where(EscrowDeal.id == deal_usdt.id).values(
                    currency="USDT",
                    token_decimals=6,
                    amount_eth=75.0,
                    amount_base_units="75000001",
                )
            )

        # Partial update on ERC20 deal -> rejected
        with pytest.raises(ValueError, match="Partial Core UPDATE of financial fields is prohibited"):
            conn.execute(
                update(EscrowDeal).where(EscrowDeal.id == deal_usdt.id).values(
                    amount_eth=80.0,
                )
            )

    session.close()
