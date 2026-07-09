"""event_assignments: escalação de jogadores em slots da comp por evento

Revision ID: c4a2e8f1b9d3
Revises: b8f1e5a3c7d2
Create Date: 2026-07-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c4a2e8f1b9d3'
down_revision: Union[str, None] = 'b8f1e5a3c7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bigint():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        'event_assignments',
        sa.Column('id', _bigint(), primary_key=True),
        sa.Column('event_id', sa.BigInteger(),
                  sa.ForeignKey('events.id', ondelete='CASCADE'), nullable=False),
        sa.Column('guild_id', sa.BigInteger(),
                  sa.ForeignKey('guilds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('comp_slot_id', sa.BigInteger(),
                  sa.ForeignKey('comp_slots.id', ondelete='SET NULL'), nullable=True),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('user_name', sa.String(255), nullable=True),
        sa.Column('game_role_id', sa.BigInteger(),
                  sa.ForeignKey('game_roles.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('event_id', 'comp_slot_id', name='uq_event_assignments_event_slot'),
        sa.UniqueConstraint('event_id', 'user_id', name='uq_event_assignments_event_user'),
    )
    op.create_index('ix_event_assignments_event_id',     'event_assignments', ['event_id'])
    op.create_index('ix_event_assignments_guild_id',     'event_assignments', ['guild_id'])
    op.create_index('ix_event_assignments_comp_slot_id', 'event_assignments', ['comp_slot_id'])
    op.create_index('ix_event_assignments_game_role_id', 'event_assignments', ['game_role_id'])


def downgrade() -> None:
    op.drop_index('ix_event_assignments_game_role_id', table_name='event_assignments')
    op.drop_index('ix_event_assignments_comp_slot_id', table_name='event_assignments')
    op.drop_index('ix_event_assignments_guild_id',     table_name='event_assignments')
    op.drop_index('ix_event_assignments_event_id',     table_name='event_assignments')
    op.drop_table('event_assignments')