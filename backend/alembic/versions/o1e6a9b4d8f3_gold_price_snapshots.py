"""gold_price_snapshots: histórico próprio de cotação prata-ouro (WS5)

Revision ID: o1e6a9b4d8f3
Revises: n0d5f8a3c7e2
Create Date: 2026-07-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'o1e6a9b4d8f3'
down_revision: Union[str, None] = 'n0d5f8a3c7e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    big_int = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        'gold_price_snapshots',
        sa.Column('id', big_int, nullable=False),
        sa.Column('region', sa.String(length=16), nullable=False),
        sa.Column('price', sa.Integer(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('region', 'recorded_at'),
    )


def downgrade() -> None:
    op.drop_table('gold_price_snapshots')
