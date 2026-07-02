"""economy_transactions: log de mudanças de saldo (id mostrado no rodapé dos
embeds do bot e usado pelo /undo)

Revision ID: a2f7c4e9d1b6
Revises: c9d3e7a1f5b8
Create Date: 2026-07-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a2f7c4e9d1b6'
down_revision: Union[str, None] = 'c9d3e7a1f5b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bigint():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        'economy_transactions',
        sa.Column('id', _bigint(), primary_key=True),
        sa.Column('guild_id', sa.BigInteger(), sa.ForeignKey('guilds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('actor_discord_id', sa.BigInteger(), nullable=False),
        sa.Column('from_user_id', sa.BigInteger(), nullable=True),
        sa.Column('to_user_id', sa.BigInteger(), nullable=True),
        sa.Column('total_earned_user_id', sa.BigInteger(), nullable=True),
        sa.Column('amount', _bigint(), nullable=False),
        sa.Column('undone', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_economy_transactions_guild_id', 'economy_transactions', ['guild_id'])


def downgrade() -> None:
    op.drop_index('ix_economy_transactions_guild_id', table_name='economy_transactions')
    op.drop_table('economy_transactions')
