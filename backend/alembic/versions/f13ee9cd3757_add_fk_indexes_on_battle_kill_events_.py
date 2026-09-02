"""add FK indexes on battle_kill_events and battle_participants

Revision ID: f13ee9cd3757
Revises: zd5e6f7a8b9c
Create Date: 2026-08-26 03:03:01.291202
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f13ee9cd3757'
down_revision: Union[str, None] = 'zd5e6f7a8b9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_bke_killer_pid', 'battle_kill_events', ['killer_participant_id'])
    op.create_index('ix_bke_victim_pid', 'battle_kill_events', ['victim_participant_id'])
    op.create_index('ix_bke_killer_sid', 'battle_kill_events', ['killer_side_id'])
    op.create_index('ix_bke_victim_sid', 'battle_kill_events', ['victim_side_id'])
    op.create_index('ix_bp_side_id', 'battle_participants', ['side_id'])


def downgrade() -> None:
    op.drop_index('ix_bp_side_id', table_name='battle_participants')
    op.drop_index('ix_bke_victim_sid', table_name='battle_kill_events')
    op.drop_index('ix_bke_killer_sid', table_name='battle_kill_events')
    op.drop_index('ix_bke_victim_pid', table_name='battle_kill_events')
    op.drop_index('ix_bke_killer_pid', table_name='battle_kill_events')
