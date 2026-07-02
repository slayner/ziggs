"""economy_balances: saldo de prata por membro Discord (comandos economy do bot)

Revision ID: c9d3e7a1f5b8
Revises: b6f4a1e8c3d7
Create Date: 2026-07-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9d3e7a1f5b8'
down_revision: Union[str, None] = 'b6f4a1e8c3d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bigint():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        'economy_balances',
        sa.Column('id', _bigint(), primary_key=True),
        sa.Column('guild_id', sa.BigInteger(), sa.ForeignKey('guilds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('discord_user_id', sa.BigInteger(), nullable=False),
        sa.Column('balance', _bigint(), nullable=False, server_default='0'),
        sa.Column('total_earned', _bigint(), nullable=False, server_default='0'),
        sa.UniqueConstraint('guild_id', 'discord_user_id', name='uq_economy_balance_member'),
    )
    op.create_index('ix_economy_balances_guild_id', 'economy_balances', ['guild_id'])
    op.create_index('ix_economy_balances_discord_user_id', 'economy_balances', ['discord_user_id'])


def downgrade() -> None:
    op.drop_index('ix_economy_balances_discord_user_id', table_name='economy_balances')
    op.drop_index('ix_economy_balances_guild_id', table_name='economy_balances')
    op.drop_table('economy_balances')
