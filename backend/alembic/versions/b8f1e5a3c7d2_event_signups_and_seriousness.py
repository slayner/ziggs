"""event_signups_and_seriousness: signup model (substitui cta_function_logs)
+ seriousness/participation_mode/functions_released/dirty-flag no Event

Revision ID: b8f1e5a3c7d2
Revises: a2f7c4e9d1b6
Create Date: 2026-07-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b8f1e5a3c7d2'
down_revision: Union[str, None] = 'a2f7c4e9d1b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bigint():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    event_seriousness = sa.Enum('casual', 'serious', name='event_seriousness')
    participation_mode = sa.Enum('presence', 'voice_percent', name='participation_mode')
    event_seriousness.create(op.get_bind(), checkfirst=True)
    participation_mode.create(op.get_bind(), checkfirst=True)

    op.add_column('events', sa.Column('seriousness', event_seriousness, nullable=False, server_default='casual'))
    op.add_column('events', sa.Column('participation_mode', participation_mode, nullable=False, server_default='presence'))
    op.add_column('events', sa.Column('functions_released', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('events', sa.Column('signup_message_dirty', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('events', sa.Column('signup_last_synced_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'event_signups',
        sa.Column('id', _bigint(), primary_key=True),
        sa.Column('event_id', sa.BigInteger(), sa.ForeignKey('events.id', ondelete='CASCADE'), nullable=False),
        sa.Column('guild_id', sa.BigInteger(), sa.ForeignKey('guilds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('user_name', sa.String(255), nullable=True),
        sa.Column('function_1', sa.String(128), nullable=True),
        sa.Column('function_2', sa.String(128), nullable=True),
        sa.Column('function_3', sa.String(128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('event_id', 'user_id', name='uq_event_signups_event_user'),
    )
    op.create_index('ix_event_signups_event_id', 'event_signups', ['event_id'])
    op.create_index('ix_event_signups_guild_id', 'event_signups', ['guild_id'])


def downgrade() -> None:
    op.drop_index('ix_event_signups_guild_id', table_name='event_signups')
    op.drop_index('ix_event_signups_event_id', table_name='event_signups')
    op.drop_table('event_signups')
    op.drop_column('events', 'signup_last_synced_at')
    op.drop_column('events', 'signup_message_dirty')
    op.drop_column('events', 'functions_released')
    op.drop_column('events', 'participation_mode')
    op.drop_column('events', 'seriousness')
    sa.Enum(name='participation_mode').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='event_seriousness').drop(op.get_bind(), checkfirst=True)
