"""nodes_tracking: subsistema de nodes (calendário, defs, events, log, maps)

Revision ID: e1f5a7b9c3d4
Revises: b9e4d2a1f8c3
Create Date: 2026-07-04 00:00:00.000000

Cria as 6 tabelas do tracking de nodes (espelho do schema `node_*` do bot-v1,
agora no backend como fonte da verdade). Ver `app/models/nodes.py`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f5a7b9c3d4'
down_revision: Union[str, None] = 'b9e4d2a1f8c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bigint():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        'node_defs',
        sa.Column('id', _bigint(), primary_key=True),
        sa.Column('guild_id', sa.BigInteger(), sa.ForeignKey('guilds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('emoji', sa.String(32), nullable=True),
        sa.Column('weight', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('sort', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('guild_id', 'name', name='uq_node_defs_guild_name'),
    )
    op.create_index('ix_node_defs_guild_id', 'node_defs', ['guild_id'])

    op.create_table(
        'node_events',
        sa.Column('id', _bigint(), primary_key=True),
        sa.Column('guild_id', sa.BigInteger(), sa.ForeignKey('guilds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel_id', sa.BigInteger(), nullable=True),
        sa.Column('node_type', sa.String(128), nullable=False),
        sa.Column('map_name', sa.String(128), nullable=False),
        sa.Column('spawn_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('added_by_id', sa.BigInteger(), nullable=True),
        sa.Column('added_by_name', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_node_events_guild_id', 'node_events', ['guild_id'])
    op.create_index('ix_node_events_spawn_at', 'node_events', ['spawn_at'])

    op.create_table(
        'node_event_log',
        sa.Column('id', _bigint(), primary_key=True),
        sa.Column('guild_id', sa.BigInteger(), sa.ForeignKey('guilds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('node_type', sa.String(128), nullable=False),
        sa.Column('map_name', sa.String(128), nullable=False),
        sa.Column('spawn_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('scout_id', sa.BigInteger(), nullable=True),
        sa.Column('scout_name', sa.String(255), nullable=True),
        sa.Column('logged_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_node_event_log_guild_id', 'node_event_log', ['guild_id'])
    op.create_index('ix_node_event_log_spawn_at', 'node_event_log', ['spawn_at'])

    op.create_table(
        'node_maps',
        sa.Column('id', _bigint(), primary_key=True),
        sa.Column('guild_id', sa.BigInteger(), sa.ForeignKey('guilds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('map_name', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('guild_id', 'map_name', name='uq_node_maps_guild_name'),
    )
    op.create_index('ix_node_maps_guild_id', 'node_maps', ['guild_id'])

    op.create_table(
        'node_map_exclusions',
        sa.Column('id', _bigint(), primary_key=True),
        sa.Column('guild_id', sa.BigInteger(), sa.ForeignKey('guilds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('map_name', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('guild_id', 'map_name', name='uq_node_map_excl_guild_name'),
    )
    op.create_index('ix_node_map_exclusions_guild_id', 'node_map_exclusions', ['guild_id'])

    op.create_table(
        'node_calendar',
        sa.Column('guild_id', sa.BigInteger(), sa.ForeignKey('guilds.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('channel_id', sa.BigInteger(), nullable=True),
        sa.Column('message_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('node_calendar')
    op.drop_index('ix_node_map_exclusions_guild_id', table_name='node_map_exclusions')
    op.drop_table('node_map_exclusions')
    op.drop_index('ix_node_maps_guild_id', table_name='node_maps')
    op.drop_table('node_maps')
    op.drop_index('ix_node_event_log_spawn_at', table_name='node_event_log')
    op.drop_index('ix_node_event_log_guild_id', table_name='node_event_log')
    op.drop_table('node_event_log')
    op.drop_index('ix_node_events_spawn_at', table_name='node_events')
    op.drop_index('ix_node_events_guild_id', table_name='node_events')
    op.drop_table('node_events')
    op.drop_index('ix_node_defs_guild_id', table_name='node_defs')
    op.drop_table('node_defs')