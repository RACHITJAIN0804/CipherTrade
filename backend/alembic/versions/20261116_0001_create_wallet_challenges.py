"""Create wallet_challenges table

Revision ID: 20261116_0001
Revises: None
Create Date: 2026-11-16 17:15:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261116_0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'wallet_challenges',
        sa.Column('id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('wallet_address', sa.String(length=42), nullable=False),
        sa.Column('nonce', sa.String(length=64), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('chain_id', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_wallet_challenges_user_id'), 'wallet_challenges', ['user_id'], unique=False)
    op.create_index(op.f('ix_wallet_challenges_wallet_address'), 'wallet_challenges', ['wallet_address'], unique=False)
    op.create_index(op.f('ix_wallet_challenges_nonce'), 'wallet_challenges', ['nonce'], unique=True)
    op.create_index(op.f('ix_wallet_challenges_expires_at'), 'wallet_challenges', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_wallet_challenges_expires_at'), table_name='wallet_challenges')
    op.drop_index(op.f('ix_wallet_challenges_nonce'), table_name='wallet_challenges')
    op.drop_index(op.f('ix_wallet_challenges_wallet_address'), table_name='wallet_challenges')
    op.drop_index(op.f('ix_wallet_challenges_user_id'), table_name='wallet_challenges')
    op.drop_table('wallet_challenges')

