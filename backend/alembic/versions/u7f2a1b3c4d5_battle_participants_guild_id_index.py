"""battle_participants.guild_id index — roster/members queries por guilda

Sem esse índice, toda query de "membros da guilda X" faz full scan de
battle_participants (~2.5M rows em prod) porque o otimizador do SQLite/PG
caía no índice de albion_player_id (ordenar tudo e distinct) em vez de
filtrar guild_id primeiro. _members levava 30-42s pra achar 12 membros.

Também adiciona índices em:
- battle_participants.alliance_id (mesma classe de query pro roster de aliança)
- player_kill_events.victim_guild_id e killer_guild_id (silver_dropped em
  perfil de guilda/aliança filtra por essas colunas; sem índice, faz full
  scan de 770k+ rows só pra somar fama por janela)

Revision ID: u7f2a1b3c4d5
Revises: t6e1f9b5d2c8
Create Date: 2026-07-21
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'u7f2a1b3c4d5'
down_revision: Union[str, None] = 't6e1f9b5d2c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_battle_participants_guild_id', 'battle_participants', ['guild_id'])
    op.create_index('ix_battle_participants_alliance_id', 'battle_participants', ['alliance_id'])
    op.create_index('ix_player_kill_events_victim_guild_id', 'player_kill_events', ['victim_guild_id'])
    op.create_index('ix_player_kill_events_killer_guild_id', 'player_kill_events', ['killer_guild_id'])


def downgrade() -> None:
    op.drop_index('ix_player_kill_events_killer_guild_id', table_name='player_kill_events')
    op.drop_index('ix_player_kill_events_victim_guild_id', table_name='player_kill_events')
    op.drop_index('ix_battle_participants_alliance_id', table_name='battle_participants')
    op.drop_index('ix_battle_participants_guild_id', table_name='battle_participants')