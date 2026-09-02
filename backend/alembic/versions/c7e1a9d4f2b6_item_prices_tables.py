"""item_prices/item_prices_latest — cache de preços do Albion (já existia em
alguns bancos criados manualmente, nunca tinha entrado pra migração)

Revision ID: c7e1a9d4f2b6
Revises: b2d5f9a3c7e1
Create Date: 2026-06-24 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c7e1a9d4f2b6'
down_revision: Union[str, None] = 'b2d5f9a3c7e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bigint():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('item_prices'):
        op.create_table(
            'item_prices',
            sa.Column('id', _bigint(), primary_key=True),
            sa.Column('item_id', sa.String(length=128), nullable=False),
            sa.Column('city', sa.String(length=48), nullable=False),
            sa.Column('quality', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('sell_price_min', _bigint(), nullable=False, server_default='0'),
            sa.Column('price_date', sa.DateTime(timezone=True), nullable=False),
            sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index('ix_item_prices_item_id', 'item_prices', ['item_id'])

    if not inspector.has_table('item_prices_latest'):
        op.create_table(
            'item_prices_latest',
            sa.Column('id', _bigint(), primary_key=True),
            sa.Column('item_id', sa.String(length=128), nullable=False),
            sa.Column('city', sa.String(length=48), nullable=False),
            sa.Column('quality', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('sell_price_min', _bigint(), nullable=False, server_default='0'),
            sa.Column('price_date', sa.DateTime(timezone=True), nullable=False),
            sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint('item_id', 'city', 'quality'),
        )
        op.create_index('ix_item_prices_latest_item_id', 'item_prices_latest', ['item_id'])


def downgrade() -> None:
    op.drop_table('item_prices_latest')
    op.drop_table('item_prices')
