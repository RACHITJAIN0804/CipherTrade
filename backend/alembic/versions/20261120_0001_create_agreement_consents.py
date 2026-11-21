"""Create agreement_consent_challenges and agreement_consents tables

Revision ID: 20261120_0001
Revises: 20261116_0001
Create Date: 2026-11-20 16:35:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261120_0001'
down_revision: Union[str, None] = '20261116_0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. agreement_consent_challenges table
    op.create_table(
        'agreement_consent_challenges',
        sa.Column('id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('offer_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('deal_id', sa.Uuid(as_uuid=True), nullable=True),
        sa.Column('user_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('party_role', sa.String(length=16), nullable=False),
        sa.Column('wallet_address', sa.String(length=42), nullable=False),
        sa.Column('agreement_hash', sa.String(length=66), nullable=False),
        sa.Column('chain_id', sa.Integer(), nullable=False),
        sa.Column('nonce', sa.String(length=64), nullable=False),
        sa.Column('deadline', sa.Integer(), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['offer_id'], ['offers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['deal_id'], ['escrow_deals.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_agreement_consent_challenges_offer_id'), 'agreement_consent_challenges', ['offer_id'], unique=False)
    op.create_index(op.f('ix_agreement_consent_challenges_deal_id'), 'agreement_consent_challenges', ['deal_id'], unique=False)
    op.create_index(op.f('ix_agreement_consent_challenges_user_id'), 'agreement_consent_challenges', ['user_id'], unique=False)
    op.create_index(op.f('ix_agreement_consent_challenges_wallet_address'), 'agreement_consent_challenges', ['wallet_address'], unique=False)
    op.create_index(op.f('ix_agreement_consent_challenges_agreement_hash'), 'agreement_consent_challenges', ['agreement_hash'], unique=False)
    op.create_index(op.f('ix_agreement_consent_challenges_nonce'), 'agreement_consent_challenges', ['nonce'], unique=True)

    # 2. agreement_consents table
    op.create_table(
        'agreement_consents',
        sa.Column('id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('offer_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('deal_id', sa.Uuid(as_uuid=True), nullable=True),
        sa.Column('user_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('party_role', sa.String(length=16), nullable=False),
        sa.Column('signer_wallet', sa.String(length=42), nullable=False),
        sa.Column('agreement_hash', sa.String(length=66), nullable=False),
        sa.Column('chain_id', sa.Integer(), nullable=False),
        sa.Column('nonce', sa.String(length=64), nullable=False),
        sa.Column('signature', sa.String(length=132), nullable=False),
        sa.Column('deadline', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['offer_id'], ['offers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['deal_id'], ['escrow_deals.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('offer_id', 'party_role', name='uq_offer_party_consent'),
    )
    op.create_index(op.f('ix_agreement_consents_offer'), 'agreement_consents', ['offer_id'], unique=False)
    op.create_index(op.f('ix_agreement_consents_deal'), 'agreement_consents', ['deal_id'], unique=False)
    op.create_index(op.f('ix_agreement_consents_hash'), 'agreement_consents', ['agreement_hash'], unique=False)
    op.create_index(op.f('ix_agreement_consents_nonce'), 'agreement_consents', ['nonce'], unique=True)
    op.create_index(op.f('ix_agreement_consents_user_id'), 'agreement_consents', ['user_id'], unique=False)


def downgrade() -> None:
    # 2. Drop agreement_consents
    op.drop_index(op.f('ix_agreement_consents_user_id'), table_name='agreement_consents')
    op.drop_index(op.f('ix_agreement_consents_nonce'), table_name='agreement_consents')
    op.drop_index(op.f('ix_agreement_consents_hash'), table_name='agreement_consents')
    op.drop_index(op.f('ix_agreement_consents_deal'), table_name='agreement_consents')
    op.drop_index(op.f('ix_agreement_consents_offer'), table_name='agreement_consents')
    op.drop_table('agreement_consents')

    # 1. Drop agreement_consent_challenges
    op.drop_index(op.f('ix_agreement_consent_challenges_nonce'), table_name='agreement_consent_challenges')
    op.drop_index(op.f('ix_agreement_consent_challenges_agreement_hash'), table_name='agreement_consent_challenges')
    op.drop_index(op.f('ix_agreement_consent_challenges_wallet_address'), table_name='agreement_consent_challenges')
    op.drop_index(op.f('ix_agreement_consent_challenges_user_id'), table_name='agreement_consent_challenges')
    op.drop_index(op.f('ix_agreement_consent_challenges_deal_id'), table_name='agreement_consent_challenges')
    op.drop_index(op.f('ix_agreement_consent_challenges_offer_id'), table_name='agreement_consent_challenges')
    op.drop_table('agreement_consent_challenges')
