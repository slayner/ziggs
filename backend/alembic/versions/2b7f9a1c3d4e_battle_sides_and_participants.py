"""battles: region multi-servidor, lados (handholding), participantes e eventos de kill

Revision ID: 2b7f9a1c3d4e
Revises: 1a2b3c4d5e6f
Create Date: 2026-06-23 19:00:00.000000

`battles`/`battle_guilds` são recriadas (não ALTER) porque hoje só guardam um
cache de resumo, sempre repopulado pelo poll seguinte de battle_tracker — não
há dado de usuário a preservar ali.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '2b7f9a1c3d4e'
down_revision: Union[str, None] = '1a2b3c4d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    return postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _bigint():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.drop_table('battle_guilds')
    op.drop_table('battles')

    op.create_table(
        'battles',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column('region', sa.String(16), nullable=False),
        sa.Column('albion_id', sa.String(64), nullable=False),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_fame', _bigint(), nullable=False, server_default='0'),
        sa.Column('kill_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cluster', sa.String(255), nullable=True),
        sa.Column('players_total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('processing_tier', sa.String(16), nullable=False, server_default='light'),
        sa.Column('is_zvz', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('region', 'albion_id', name='uq_battles_region_albion_id'),
    )
    op.create_index('ix_battles_region', 'battles', ['region'])
    op.create_index('ix_battles_albion_id', 'battles', ['albion_id'])

    op.create_table(
        'battle_sides',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column('battle_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('label', sa.String(8), nullable=False),
        sa.Column('is_rats', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('player_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('score', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('ix_battle_sides_battle_id', 'battle_sides', ['battle_id'])

    op.create_table(
        'battle_guilds',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column('battle_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('albion_guild_id', sa.String(64), nullable=False),
        sa.Column('guild_name', sa.String(255), nullable=False),
        sa.Column('alliance_id', sa.String(64), nullable=True),
        sa.Column('alliance_name', sa.String(255), nullable=True),
        sa.Column('kill_fame', _bigint(), nullable=False, server_default='0'),
        sa.Column('kills', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('deaths', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('side_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battle_sides.id', ondelete='SET NULL'), nullable=True),
        sa.UniqueConstraint('battle_id', 'albion_guild_id'),
    )
    op.create_index('ix_battle_guilds_battle_id', 'battle_guilds', ['battle_id'])
    op.create_index('ix_battle_guilds_albion_guild_id', 'battle_guilds', ['albion_guild_id'])

    op.create_table(
        'battle_participants',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column('battle_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('albion_player_id', sa.String(64), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('guild_id', sa.String(64), nullable=True),
        sa.Column('guild_name', sa.String(255), nullable=True),
        sa.Column('alliance_id', sa.String(64), nullable=True),
        sa.Column('alliance_name', sa.String(255), nullable=True),
        sa.Column('side_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battle_sides.id', ondelete='SET NULL'), nullable=True),
        sa.Column('kills', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('deaths', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('kill_fame', _bigint(), nullable=False, server_default='0'),
        sa.Column('ip', sa.Float(), nullable=False, server_default='0'),
        sa.Column('damage_dealt', sa.Float(), nullable=False, server_default='0'),
        sa.Column('damage_taken', sa.Float(), nullable=False, server_default='0'),
        sa.Column('healing_done', sa.Float(), nullable=False, server_default='0'),
        sa.Column('equipment', _json_type(), nullable=True),
        sa.UniqueConstraint('battle_id', 'albion_player_id'),
    )
    op.create_index('ix_battle_participants_battle_id', 'battle_participants', ['battle_id'])
    op.create_index('ix_battle_participants_albion_player_id', 'battle_participants', ['albion_player_id'])

    op.create_table(
        'battle_kill_events',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column('battle_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('albion_event_id', sa.String(64), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fame', _bigint(), nullable=False, server_default='0'),
        sa.Column('killer_participant_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battle_participants.id', ondelete='SET NULL'), nullable=True),
        sa.Column('victim_participant_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battle_participants.id', ondelete='SET NULL'), nullable=True),
        sa.Column('killer_side_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battle_sides.id', ondelete='SET NULL'), nullable=True),
        sa.Column('victim_side_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battle_sides.id', ondelete='SET NULL'), nullable=True),
        sa.UniqueConstraint('battle_id', 'albion_event_id'),
    )
    op.create_index('ix_battle_kill_events_battle_id', 'battle_kill_events', ['battle_id'])
    op.create_index('ix_battle_kill_events_albion_event_id', 'battle_kill_events', ['albion_event_id'])


def downgrade() -> None:
    op.drop_table('battle_kill_events')
    op.drop_table('battle_participants')
    op.drop_table('battle_guilds')
    op.drop_table('battle_sides')
    op.drop_table('battles')

    op.create_table(
        'battles',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column('albion_id', sa.String(64), nullable=False, unique=True),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_fame', _bigint(), nullable=False, server_default='0'),
        sa.Column('kill_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cluster', sa.String(255), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'battle_guilds',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column('battle_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('albion_guild_id', sa.String(64), nullable=False),
        sa.Column('guild_name', sa.String(255), nullable=False),
        sa.Column('alliance_id', sa.String(64), nullable=True),
        sa.Column('alliance_name', sa.String(255), nullable=True),
        sa.Column('kill_fame', _bigint(), nullable=False, server_default='0'),
        sa.Column('kills', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('deaths', sa.Integer(), nullable=False, server_default='0'),
        sa.UniqueConstraint('battle_id', 'albion_guild_id'),
    )
