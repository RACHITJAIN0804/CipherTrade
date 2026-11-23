"""Add contract_address and contract_version to escrow_deals

Revision ID: 20261123_0001
Revises: 20261120_0001
Create Date: 2026-11-23 10:35:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261123_0001'
down_revision: Union[str, None] = '20261120_0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = insp.get_table_names()
    if 'escrow_deals' in tables:
        columns = [c['name'] for c in insp.get_columns('escrow_deals')]
        with op.batch_alter_table('escrow_deals') as batch_op:
            if 'contract_address' not in columns:
                batch_op.add_column(sa.Column('contract_address', sa.String(length=42), nullable=True))
            if 'contract_version' not in columns:
                batch_op.add_column(sa.Column('contract_version', sa.String(length=10), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = insp.get_table_names()
    if 'escrow_deals' in tables:
        columns = [c['name'] for c in insp.get_columns('escrow_deals')]
        with op.batch_alter_table('escrow_deals') as batch_op:
            if 'contract_version' in columns:
                batch_op.drop_column('contract_version')
            if 'contract_address' in columns:
                batch_op.drop_column('contract_address')
