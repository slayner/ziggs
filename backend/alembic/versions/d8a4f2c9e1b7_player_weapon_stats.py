"""player_weapon_stats: contadores brutos all-time por (jogador, arma)

Revision ID: d8a4f2c9e1b7
Revises: a1f3c8d6b2e4
Create Date: 2026-06-30 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd8a4f2c9e1b7'
down_revision: Union[str, None] = 'a1f3c8d6b2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    big_int = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        'player_weapon_stats',
        sa.Column('id', big_int, nullable=False),
        sa.Column('albion_player_id', sa.String(length=64), nullable=False),
        sa.Column('weapon_base', sa.String(length=128), nullable=False),
        sa.Column('kills', sa.Integer(), nullable=False),
        sa.Column('appearances', sa.Integer(), nullable=False),
        sa.Column('eligible_appearances', sa.Integer(), nullable=False),
        sa.Column('pierce_points', sa.Integer(), nullable=False),
        sa.Column('healer_points', sa.Integer(), nullable=False),
        sa.Column('zero_death_eligible_fights', sa.Integer(), nullable=False),
        sa.Column('tank_ok_fights', sa.Integer(), nullable=False),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('albion_player_id', 'weapon_base'),
    )
    op.create_index('ix_player_weapon_stats_albion_player_id', 'player_weapon_stats', ['albion_player_id'])
    op.create_index('ix_player_weapon_stats_weapon_base', 'player_weapon_stats', ['weapon_base'])


def downgrade() -> None:
    op.drop_index('ix_player_weapon_stats_weapon_base', table_name='player_weapon_stats')
    op.drop_index('ix_player_weapon_stats_albion_player_id', table_name='player_weapon_stats')
    op.drop_table('player_weapon_stats')
