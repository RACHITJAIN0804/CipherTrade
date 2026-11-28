"""Expand models for V3 multi-token escrow support, backfill historical ETH records, and enforce downgrade protection.

Revision ID: 20261124_0001
Revises: 20261123_0001
Create Date: 2026-11-24 11:15:00.000000

Mandatory safeguards implemented:
- Pre-validation of historical rows (detects negative or corrupt values before modification).
- Dialect-agnostic batch operations for SQLite compatibility.
- Lossless exact 100-digit Decimal arithmetic for backfilling ETH amounts to 18-decimal base units.
- Strict reconciliation check for every backfilled row against its original amount.
- Non-destructive downgrade check: prevents downgrade if any V3 deal (even ETH), non-default fee/agreement, non-ETH requirement/offer, or amendment consent exists.
"""
from __future__ import annotations

import decimal
from decimal import Decimal
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column

# Revision identifiers, used by Alembic.
revision: str = '20261124_0001'
down_revision: Union[str, None] = '20261123_0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 100-digit precision Decimal context to avoid any floating-point approximation
DECIMAL_CONTEXT = decimal.Context(prec=100)
decimal.setcontext(DECIMAL_CONTEXT)
SCALE_ETH = Decimal("1000000000000000000")  # 10^18
UINT256_MAX = (1 << 256) - 1


def _to_eth_base_units(amount_val: Union[float, Decimal, str, int]) -> str:
    """Exact conversion of ETH numeric value to 18-decimal base units string."""
    d = Decimal(str(amount_val))
    if d < Decimal("0"):
        raise ValueError(f"Negative amount cannot be converted to base units: {amount_val}")
    base_units_dec = d * SCALE_ETH
    if base_units_dec != base_units_dec.to_integral_value():
        raise ValueError(f"Amount {amount_val} has fractional wei under 18 decimals")
    base_units_int = int(base_units_dec)
    if base_units_int > UINT256_MAX:
        raise ValueError(f"Base units {base_units_int} exceeds uint256 max")
    return str(base_units_int)


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = insp.get_table_names()

    # ─────────────────────────────────────────────────────────────────────────────
    # Step 1: Pre-validation of historical data
    # ─────────────────────────────────────────────────────────────────────────────
    if 'requirements' in tables:
        neg_reqs = conn.execute(
            sa.text("SELECT id, budget_min, budget_max FROM requirements WHERE budget_min < 0 OR budget_max < 0")
        ).fetchall()
        if neg_reqs:
            raise ValueError(f"Cannot migrate: Found requirements with negative budget: {neg_reqs}")

        inv_reqs = conn.execute(
            sa.text("SELECT id, budget_min, budget_max FROM requirements WHERE budget_min IS NOT NULL AND budget_max IS NOT NULL AND budget_min > budget_max")
        ).fetchall()
        if inv_reqs:
            raise ValueError(f"Cannot migrate: Found requirements with inverted budgets: {inv_reqs}")

    if 'offers' in tables:
        neg_offers = conn.execute(
            sa.text("SELECT id, price_eth FROM offers WHERE price_eth <= 0")
        ).fetchall()
        if neg_offers:
            raise ValueError(f"Cannot migrate: Found offers with non-positive price_eth: {neg_offers}")

    if 'escrow_deals' in tables:
        neg_deals = conn.execute(
            sa.text("SELECT id, amount_eth FROM escrow_deals WHERE amount_eth <= 0")
        ).fetchall()
        if neg_deals:
            raise ValueError(f"Cannot migrate: Found escrow deals with non-positive amount_eth: {neg_deals}")

    # ─────────────────────────────────────────────────────────────────────────────
    # Step 2: Add new columns as initially nullable
    # ─────────────────────────────────────────────────────────────────────────────
    # 2.1 Requirements
    if 'requirements' in tables:
        req_cols = [c['name'] for c in insp.get_columns('requirements')]
        with op.batch_alter_table('requirements') as batch_op:
            if 'currency' not in req_cols:
                batch_op.add_column(sa.Column('currency', sa.String(length=10), nullable=True))
            if 'token_address' not in req_cols:
                batch_op.add_column(sa.Column('token_address', sa.String(length=42), nullable=True))
            if 'token_decimals' not in req_cols:
                batch_op.add_column(sa.Column('token_decimals', sa.Integer(), nullable=True))
            if 'chain_id' not in req_cols:
                batch_op.add_column(sa.Column('chain_id', sa.Integer(), nullable=True))
            if 'budget_min_base_units' not in req_cols:
                batch_op.add_column(sa.Column('budget_min_base_units', sa.String(length=78), nullable=True))
            if 'budget_max_base_units' not in req_cols:
                batch_op.add_column(sa.Column('budget_max_base_units', sa.String(length=78), nullable=True))

    # 2.2 Offers
    if 'offers' in tables:
        offer_cols = [c['name'] for c in insp.get_columns('offers')]
        with op.batch_alter_table('offers') as batch_op:
            if 'currency' not in offer_cols:
                batch_op.add_column(sa.Column('currency', sa.String(length=10), nullable=True))
            if 'token_address' not in offer_cols:
                batch_op.add_column(sa.Column('token_address', sa.String(length=42), nullable=True))
            if 'token_decimals' not in offer_cols:
                batch_op.add_column(sa.Column('token_decimals', sa.Integer(), nullable=True))
            if 'chain_id' not in offer_cols:
                batch_op.add_column(sa.Column('chain_id', sa.Integer(), nullable=True))
            if 'price_base_units' not in offer_cols:
                batch_op.add_column(sa.Column('price_base_units', sa.String(length=78), nullable=True))

    # 2.3 Escrow Deals
    if 'escrow_deals' in tables:
        deal_cols = [c['name'] for c in insp.get_columns('escrow_deals')]
        with op.batch_alter_table('escrow_deals') as batch_op:
            if 'chain_id' not in deal_cols:
                batch_op.add_column(sa.Column('chain_id', sa.Integer(), nullable=True))
            if 'token_address' not in deal_cols:
                batch_op.add_column(sa.Column('token_address', sa.String(length=42), nullable=True))
            if 'token_symbol' not in deal_cols:
                batch_op.add_column(sa.Column('token_symbol', sa.String(length=10), nullable=True))
            if 'token_decimals' not in deal_cols:
                batch_op.add_column(sa.Column('token_decimals', sa.Integer(), nullable=True))
            if 'amount_base_units' not in deal_cols:
                batch_op.add_column(sa.Column('amount_base_units', sa.String(length=78), nullable=True))
            if 'fee_bps' not in deal_cols:
                batch_op.add_column(sa.Column('fee_bps', sa.Integer(), nullable=True))
            if 'treasury_address' not in deal_cols:
                batch_op.add_column(sa.Column('treasury_address', sa.String(length=42), nullable=True))
            if 'agreement_version' not in deal_cols:
                batch_op.add_column(sa.Column('agreement_version', sa.Integer(), nullable=True))
            if 'previous_agreement_hash' not in deal_cols:
                batch_op.add_column(sa.Column('previous_agreement_hash', sa.String(length=66), nullable=True))

    # 2.4 Agreement Consents
    if 'agreement_consents' in tables:
        consent_cols = [c['name'] for c in insp.get_columns('agreement_consents')]
        with op.batch_alter_table('agreement_consents') as batch_op:
            if 'agreement_version' not in consent_cols:
                batch_op.add_column(sa.Column('agreement_version', sa.Integer(), nullable=True))
            if 'token_address' not in consent_cols:
                batch_op.add_column(sa.Column('token_address', sa.String(length=42), nullable=True))
            if 'amount_base_units' not in consent_cols:
                batch_op.add_column(sa.Column('amount_base_units', sa.String(length=78), nullable=True))
            if 'fee_bps' not in consent_cols:
                batch_op.add_column(sa.Column('fee_bps', sa.Integer(), nullable=True))
            if 'treasury_address' not in consent_cols:
                batch_op.add_column(sa.Column('treasury_address', sa.String(length=42), nullable=True))
            if 'previous_agreement_hash' not in consent_cols:
                batch_op.add_column(sa.Column('previous_agreement_hash', sa.String(length=66), nullable=True))

    # ─────────────────────────────────────────────────────────────────────────────
    # Step 3: Exact Decimal Backfill & Row-by-Row Reconciliation
    # ─────────────────────────────────────────────────────────────────────────────
    # 3.1 Requirements
    if 'requirements' in tables:
        rows = conn.execute(sa.text("SELECT id, budget_min, budget_max FROM requirements")).fetchall()
        for r_id, b_min, b_max in rows:
            min_bu = _to_eth_base_units(b_min) if b_min is not None else None
            max_bu = _to_eth_base_units(b_max) if b_max is not None else None
            conn.execute(
                sa.text(
                    "UPDATE requirements SET "
                    "currency = 'ETH', "
                    "token_address = NULL, "
                    "token_decimals = 18, "
                    "budget_min_base_units = :min_bu, "
                    "budget_max_base_units = :max_bu "
                    "WHERE id = :id"
                ),
                {"min_bu": min_bu, "max_bu": max_bu, "id": r_id}
            )
            # Reconcile backfill
            if b_min is not None:
                assert Decimal(min_bu) / SCALE_ETH == Decimal(str(b_min))
            if b_max is not None:
                assert Decimal(max_bu) / SCALE_ETH == Decimal(str(b_max))

    # 3.2 Offers
    if 'offers' in tables:
        rows = conn.execute(sa.text("SELECT id, price_eth FROM offers")).fetchall()
        for o_id, price_eth in rows:
            price_bu = _to_eth_base_units(price_eth)
            conn.execute(
                sa.text(
                    "UPDATE offers SET "
                    "currency = 'ETH', "
                    "token_address = NULL, "
                    "token_decimals = 18, "
                    "price_base_units = :price_bu "
                    "WHERE id = :id"
                ),
                {"price_bu": price_bu, "id": o_id}
            )
            # Reconcile backfill
            assert Decimal(price_bu) / SCALE_ETH == Decimal(str(price_eth))

    # 3.3 Escrow Deals
    if 'escrow_deals' in tables:
        rows = conn.execute(sa.text("SELECT id, amount_eth, chain_deal_id FROM escrow_deals")).fetchall()
        for d_id, amount_eth, c_id in rows:
            amt_bu = _to_eth_base_units(amount_eth)
            conn.execute(
                sa.text(
                    "UPDATE escrow_deals SET "
                    "token_symbol = 'ETH', "
                    "token_address = NULL, "
                    "token_decimals = 18, "
                    "amount_base_units = :amt_bu, "
                    "agreement_version = 1, "
                    "fee_bps = NULL, "
                    "treasury_address = NULL, "
                    "previous_agreement_hash = NULL "
                    "WHERE id = :id"
                ),
                {"amt_bu": amt_bu, "id": d_id}
            )
            # Reconcile backfill
            assert Decimal(amt_bu) / SCALE_ETH == Decimal(str(amount_eth))

    # 3.4 Agreement Consents
    if 'agreement_consents' in tables:
        conn.execute(
            sa.text(
                "UPDATE agreement_consents SET "
                "agreement_version = 1, "
                "token_address = NULL, "
                "amount_base_units = NULL, "
                "fee_bps = NULL, "
                "treasury_address = NULL, "
                "previous_agreement_hash = NULL "
                "WHERE agreement_version IS NULL"
            )
        )

    # ─────────────────────────────────────────────────────────────────────────────
    # Step 3.5: Post-Backfill Integrity Verification (Pre-NOT NULL Check)
    # Reject any remaining NULL, empty, or invalid base unit strings before altering columns
    # ─────────────────────────────────────────────────────────────────────────────
    if 'offers' in tables:
        null_offers = conn.execute(
            sa.text("SELECT id FROM offers WHERE price_base_units IS NULL OR price_base_units = ''")
        ).fetchall()
        if null_offers:
            raise ValueError(f"Cannot apply NOT NULL: Found offers with NULL/empty price_base_units: {null_offers}")

    if 'escrow_deals' in tables:
        null_deals = conn.execute(
            sa.text("SELECT id FROM escrow_deals WHERE amount_base_units IS NULL OR amount_base_units = ''")
        ).fetchall()
        if null_deals:
            raise ValueError(f"Cannot apply NOT NULL: Found escrow deals with NULL/empty amount_base_units: {null_deals}")

    # ─────────────────────────────────────────────────────────────────────────────
    # Step 4: Apply NOT NULL constraints and Updated Unique Constraint
    # ─────────────────────────────────────────────────────────────────────────────
    # Requirements: currency NOT NULL
    if 'requirements' in tables:
        with op.batch_alter_table('requirements') as batch_op:
            batch_op.alter_column('currency', existing_type=sa.String(length=10), nullable=False)

    # Offers: currency NOT NULL, token_decimals NOT NULL, price_base_units NOT NULL
    if 'offers' in tables:
        with op.batch_alter_table('offers') as batch_op:
            batch_op.alter_column('currency', existing_type=sa.String(length=10), nullable=False)
            batch_op.alter_column('token_decimals', existing_type=sa.Integer(), nullable=False)
            batch_op.alter_column('price_base_units', existing_type=sa.String(length=78), nullable=False)

    # Escrow Deals: token_symbol NOT NULL, token_decimals NOT NULL, amount_base_units NOT NULL, agreement_version NOT NULL
    if 'escrow_deals' in tables:
        with op.batch_alter_table('escrow_deals') as batch_op:
            batch_op.alter_column('token_symbol', existing_type=sa.String(length=10), nullable=False)
            batch_op.alter_column('token_decimals', existing_type=sa.Integer(), nullable=False)
            batch_op.alter_column('amount_base_units', existing_type=sa.String(length=78), nullable=False)
            batch_op.alter_column('agreement_version', existing_type=sa.Integer(), nullable=False)

    # Agreement Consents: agreement_version NOT NULL, and unique constraint migration
    if 'agreement_consents' in tables:
        with op.batch_alter_table('agreement_consents') as batch_op:
            batch_op.alter_column('agreement_version', existing_type=sa.Integer(), nullable=False)
            # Drop old unique constraint on ('offer_id', 'party_role')
            try:
                batch_op.drop_constraint('uq_offer_party_consent', type_='unique')
            except Exception:
                pass
            # Create new unique constraint on ('offer_id', 'party_role', 'agreement_version')
            batch_op.create_unique_constraint(
                'uq_offer_party_version_consent',
                ['offer_id', 'party_role', 'agreement_version']
            )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = insp.get_table_names()

    # ─────────────────────────────────────────────────────────────────────────────
    # MANDATORY DOWNGRADE PROTECTION GUARD
    # Abort if downgrade would destroy:
    # 1. Any V3 escrow deal (including V3 ETH deals)
    # 2. Non-ETH deals (token_symbol != 'ETH' or token_address IS NOT NULL)
    # 3. Non-default fee_bps or treasury_address
    # 4. Agreements with version > 1 or previous_agreement_hash set
    # 5. Non-ETH requirements or offers
    # 6. Additional agreement consent versions (version != 1)
    # ─────────────────────────────────────────────────────────────────────────────
    if 'escrow_deals' in tables:
        cols = [c['name'] for c in insp.get_columns('escrow_deals')]
        if 'contract_version' in cols:
            v3_deals = conn.execute(
                sa.text("SELECT COUNT(*) FROM escrow_deals WHERE contract_version = 'v3'")
            ).scalar() or 0
            if v3_deals > 0:
                raise RuntimeError(
                    f"Downgrade aborted: {v3_deals} V3 escrow deal(s) exist. "
                    "Downgrading would permanently destroy V3 deal data."
                )

        if 'token_symbol' in cols:
            non_eth_deals = conn.execute(
                sa.text("SELECT COUNT(*) FROM escrow_deals WHERE token_symbol != 'ETH' OR token_address IS NOT NULL")
            ).scalar() or 0
            if non_eth_deals > 0:
                raise RuntimeError(
                    f"Downgrade aborted: {non_eth_deals} non-ETH escrow deal(s) exist. "
                    "Downgrading would permanently destroy token address and asset data."
                )

        if 'fee_bps' in cols and 'treasury_address' in cols:
            custom_fee_deals = conn.execute(
                sa.text("SELECT COUNT(*) FROM escrow_deals WHERE fee_bps IS NOT NULL OR treasury_address IS NOT NULL")
            ).scalar() or 0
            if custom_fee_deals > 0:
                raise RuntimeError(
                    f"Downgrade aborted: {custom_fee_deals} deal(s) have explicit V3 fee/treasury data."
                )

        if 'agreement_version' in cols:
            amended_deals = conn.execute(
                sa.text("SELECT COUNT(*) FROM escrow_deals WHERE agreement_version > 1 OR previous_agreement_hash IS NOT NULL")
            ).scalar() or 0
            if amended_deals > 0:
                raise RuntimeError(
                    f"Downgrade aborted: {amended_deals} deal(s) have agreement amendments (version > 1)."
                )

    if 'requirements' in tables:
        req_cols = [c['name'] for c in insp.get_columns('requirements')]
        if 'currency' in req_cols:
            non_eth_reqs = conn.execute(
                sa.text("SELECT COUNT(*) FROM requirements WHERE currency != 'ETH' OR token_address IS NOT NULL")
            ).scalar() or 0
            if non_eth_reqs > 0:
                raise RuntimeError(
                    f"Downgrade aborted: {non_eth_reqs} non-ETH requirement(s) exist. "
                    "Downgrading would permanently destroy ERC20 requirement data."
                )
        if 'chain_id' in req_cols:
            custom_chain_reqs = conn.execute(
                sa.text("SELECT COUNT(*) FROM requirements WHERE chain_id IS NOT NULL")
            ).scalar() or 0
            if custom_chain_reqs > 0:
                raise RuntimeError(
                    f"Downgrade aborted: {custom_chain_reqs} requirement(s) have explicit chain_id set."
                )
        if 'token_decimals' in req_cols:
            custom_dec_reqs = conn.execute(
                sa.text("SELECT COUNT(*) FROM requirements WHERE token_decimals IS NOT NULL AND token_decimals != 18")
            ).scalar() or 0
            if custom_dec_reqs > 0:
                raise RuntimeError(
                    f"Downgrade aborted: {custom_dec_reqs} requirement(s) have non-default token_decimals set."
                )

    if 'offers' in tables:
        offer_cols = [c['name'] for c in insp.get_columns('offers')]
        if 'currency' in offer_cols:
            non_eth_offers = conn.execute(
                sa.text("SELECT COUNT(*) FROM offers WHERE currency != 'ETH' OR token_address IS NOT NULL")
            ).scalar() or 0
            if non_eth_offers > 0:
                raise RuntimeError(
                    f"Downgrade aborted: {non_eth_offers} non-ETH offer(s) exist. "
                    "Downgrading would permanently destroy ERC20 offer data."
                )
        if 'chain_id' in offer_cols:
            custom_chain_offers = conn.execute(
                sa.text("SELECT COUNT(*) FROM offers WHERE chain_id IS NOT NULL")
            ).scalar() or 0
            if custom_chain_offers > 0:
                raise RuntimeError(
                    f"Downgrade aborted: {custom_chain_offers} offer(s) have explicit chain_id set."
                )
        if 'token_decimals' in offer_cols:
            custom_dec_offers = conn.execute(
                sa.text("SELECT COUNT(*) FROM offers WHERE token_decimals != 18")
            ).scalar() or 0
            if custom_dec_offers > 0:
                raise RuntimeError(
                    f"Downgrade aborted: {custom_dec_offers} offer(s) have non-default token_decimals set."
                )

    if 'agreement_consents' in tables:
        consent_cols = [c['name'] for c in insp.get_columns('agreement_consents')]
        if 'agreement_version' in consent_cols:
            multi_ver_consents = conn.execute(
                sa.text("SELECT COUNT(*) FROM agreement_consents WHERE agreement_version > 1")
            ).scalar() or 0
            if multi_ver_consents > 0:
                raise RuntimeError(
                    f"Downgrade aborted: {multi_ver_consents} amended agreement consent(s) exist (version > 1)."
                )

        # Comprehensive inspection of all newly introduced AgreementConsent V3 fields:
        # Abort if ANY consent (even version 1) contains non-default V3 metadata
        v3_consent_clauses = []
        if 'token_address' in consent_cols:
            v3_consent_clauses.append("token_address IS NOT NULL")
        if 'amount_base_units' in consent_cols:
            v3_consent_clauses.append("amount_base_units IS NOT NULL")
        if 'fee_bps' in consent_cols:
            v3_consent_clauses.append("fee_bps IS NOT NULL")
        if 'treasury_address' in consent_cols:
            v3_consent_clauses.append("treasury_address IS NOT NULL")
        if 'previous_agreement_hash' in consent_cols:
            v3_consent_clauses.append("previous_agreement_hash IS NOT NULL")

        if v3_consent_clauses:
            where_expr = " OR ".join(v3_consent_clauses)
            v3_consents_count = conn.execute(
                sa.text(f"SELECT COUNT(*) FROM agreement_consents WHERE {where_expr}")
            ).scalar() or 0
            if v3_consents_count > 0:
                raise RuntimeError(
                    f"Downgrade aborted: {v3_consents_count} agreement consent(s) contain explicit V3 metadata "
                    "(token_address, amount_base_units, fee_bps, treasury_address, or previous_agreement_hash)."
                )

    # ─────────────────────────────────────────────────────────────────────────────
    # MANDATORY ROW-BY-ROW BASE-UNIT RECONSTRUCTABILITY GUARDS
    # Abort if any base-unit value differs from exact legacy ETH conversion,
    # or is malformed, negative, fractional, empty, out of uint256 range,
    # or if a non-NULL base-unit budget is paired with a NULL legacy budget.
    # ─────────────────────────────────────────────────────────────────────────────
    # 1. Requirements base units reconstructability
    if 'requirements' in tables:
        req_cols = [c['name'] for c in insp.get_columns('requirements')]
        has_min_bu = 'budget_min_base_units' in req_cols
        has_max_bu = 'budget_max_base_units' in req_cols

        if has_min_bu or has_max_bu:
            rows = conn.execute(
                sa.text("SELECT id, budget_min, budget_max, budget_min_base_units, budget_max_base_units FROM requirements")
            ).fetchall()
            for r_id, b_min, b_max, min_bu, max_bu in rows:
                # Check budget_min vs budget_min_base_units
                if b_min is not None:
                    if min_bu is None or min_bu == "":
                        raise RuntimeError(
                            f"Downgrade aborted: Requirement {r_id} has legacy budget_min {b_min} "
                            "but missing/empty budget_min_base_units."
                        )
                    try:
                        expected_min_bu = _to_eth_base_units(b_min)
                    except Exception as exc:
                        raise RuntimeError(
                            f"Downgrade aborted: Requirement {r_id} legacy budget_min {b_min} cannot be converted: {exc}"
                        )
                    if min_bu != expected_min_bu:
                        raise RuntimeError(
                            f"Downgrade aborted: Requirement {r_id} has budget_min_base_units '{min_bu}' "
                            f"which does not match exact legacy budget_min {b_min} ({expected_min_bu})."
                        )
                else:
                    if min_bu is not None:
                        raise RuntimeError(
                            f"Downgrade aborted: Requirement {r_id} has non-NULL budget_min_base_units '{min_bu}' "
                            "paired with NULL legacy budget_min."
                        )

                # Check budget_max vs budget_max_base_units
                if b_max is not None:
                    if max_bu is None or max_bu == "":
                        raise RuntimeError(
                            f"Downgrade aborted: Requirement {r_id} has legacy budget_max {b_max} "
                            "but missing/empty budget_max_base_units."
                        )
                    try:
                        expected_max_bu = _to_eth_base_units(b_max)
                    except Exception as exc:
                        raise RuntimeError(
                            f"Downgrade aborted: Requirement {r_id} legacy budget_max {b_max} cannot be converted: {exc}"
                        )
                    if max_bu != expected_max_bu:
                        raise RuntimeError(
                            f"Downgrade aborted: Requirement {r_id} has budget_max_base_units '{max_bu}' "
                            f"which does not match exact legacy budget_max {b_max} ({expected_max_bu})."
                        )
                else:
                    if max_bu is not None:
                        raise RuntimeError(
                            f"Downgrade aborted: Requirement {r_id} has non-NULL budget_max_base_units '{max_bu}' "
                            "paired with NULL legacy budget_max."
                        )

    # 2. Offers price_base_units reconstructability
    if 'offers' in tables:
        offer_cols = [c['name'] for c in insp.get_columns('offers')]
        if 'price_base_units' in offer_cols:
            rows = conn.execute(
                sa.text("SELECT id, price_eth, price_base_units FROM offers")
            ).fetchall()
            for o_id, price_eth, price_bu in rows:
                if price_bu is None or price_bu == "":
                    raise RuntimeError(
                        f"Downgrade aborted: Offer {o_id} has missing/empty price_base_units."
                    )
                try:
                    expected_price_bu = _to_eth_base_units(price_eth)
                except Exception as exc:
                    raise RuntimeError(
                        f"Downgrade aborted: Offer {o_id} legacy price_eth {price_eth} cannot be converted: {exc}"
                    )
                if price_bu != expected_price_bu:
                    raise RuntimeError(
                        f"Downgrade aborted: Offer {o_id} has price_base_units '{price_bu}' "
                        f"which does not match exact legacy price_eth {price_eth} ({expected_price_bu})."
                    )

    # 3. Escrow Deals amount_base_units reconstructability
    if 'escrow_deals' in tables:
        deal_cols = [c['name'] for c in insp.get_columns('escrow_deals')]
        if 'amount_base_units' in deal_cols:
            rows = conn.execute(
                sa.text("SELECT id, amount_eth, amount_base_units FROM escrow_deals")
            ).fetchall()
            for d_id, amount_eth, amt_bu in rows:
                if amt_bu is None or amt_bu == "":
                    raise RuntimeError(
                        f"Downgrade aborted: Escrow deal {d_id} has missing/empty amount_base_units."
                    )
                try:
                    expected_amt_bu = _to_eth_base_units(amount_eth)
                except Exception as exc:
                    raise RuntimeError(
                        f"Downgrade aborted: Escrow deal {d_id} legacy amount_eth {amount_eth} cannot be converted: {exc}"
                    )
                if amt_bu != expected_amt_bu:
                    raise RuntimeError(
                        f"Downgrade aborted: Escrow deal {d_id} has amount_base_units '{amt_bu}' "
                        f"which does not match exact legacy amount_eth {amount_eth} ({expected_amt_bu})."
                    )

    # ─────────────────────────────────────────────────────────────────────────────
    # Safe downgrade steps (reversing changes cleanly when guards pass)
    # ─────────────────────────────────────────────────────────────────────────────
    # 1. Agreement Consents
    if 'agreement_consents' in tables:
        consent_cols = [c['name'] for c in insp.get_columns('agreement_consents')]
        with op.batch_alter_table('agreement_consents') as batch_op:
            try:
                batch_op.drop_constraint('uq_offer_party_version_consent', type_='unique')
            except Exception:
                pass
            batch_op.create_unique_constraint('uq_offer_party_consent', ['offer_id', 'party_role'])
            for col in ['previous_agreement_hash', 'treasury_address', 'fee_bps', 'amount_base_units', 'token_address', 'agreement_version']:
                if col in consent_cols:
                    batch_op.drop_column(col)

    # 2. Escrow Deals
    if 'escrow_deals' in tables:
        deal_cols = [c['name'] for c in insp.get_columns('escrow_deals')]
        with op.batch_alter_table('escrow_deals') as batch_op:
            for col in ['previous_agreement_hash', 'agreement_version', 'treasury_address', 'fee_bps', 'amount_base_units', 'token_decimals', 'token_symbol', 'token_address', 'chain_id']:
                if col in deal_cols:
                    batch_op.drop_column(col)

    # 3. Offers
    if 'offers' in tables:
        offer_cols = [c['name'] for c in insp.get_columns('offers')]
        with op.batch_alter_table('offers') as batch_op:
            for col in ['price_base_units', 'chain_id', 'token_decimals', 'token_address', 'currency']:
                if col in offer_cols:
                    batch_op.drop_column(col)

    # 4. Requirements
    if 'requirements' in tables:
        req_cols = [c['name'] for c in insp.get_columns('requirements')]
        with op.batch_alter_table('requirements') as batch_op:
            for col in ['budget_max_base_units', 'budget_min_base_units', 'chain_id', 'token_decimals', 'token_address', 'currency']:
                if col in req_cols:
                    batch_op.drop_column(col)
