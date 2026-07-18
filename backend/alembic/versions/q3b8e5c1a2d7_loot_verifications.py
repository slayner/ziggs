"""loot_verifications

Revision ID: q3b8e5c1a2d7
Revises: d5a819f2aa86
Create Date: 2026-07-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'q3b8e5c1a2d7'
down_revision: Union[str, None] = 'd5a819f2aa86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('loot_verifications',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('guild_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('event_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('looted_by', sa.String(length=64), nullable=False),
        sa.Column('item_id', sa.String(length=128), nullable=False),
        sa.Column('verified_by_user_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'looted_by', 'item_id', name='uq_loot_verif'),
    )
    op.create_index('ix_loot_verifications_guild_id', 'loot_verifications', ['guild_id'])
    op.create_index('ix_loot_verifications_event_id', 'loot_verifications', ['event_id'])


def downgrade() -> None:
    op.drop_index('ix_loot_verifications_event_id', table_name='loot_verifications')
    op.drop_index('ix_loot_verifications_guild_id', table_name='loot_verifications')
    op.drop_table('loot_verifications')
