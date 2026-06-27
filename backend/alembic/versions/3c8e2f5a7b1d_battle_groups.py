"""battle_groups: código curto compartilhável (7 chars), suporta combinar batalhas

Revision ID: 3c8e2f5a7b1d
Revises: 2b7f9a1c3d4e
Create Date: 2026-06-23 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '3c8e2f5a7b1d'
down_revision: Union[str, None] = '2b7f9a1c3d4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'battle_groups',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column('public_id', sa.String(7), nullable=False, unique=True),
        sa.Column('fingerprint', sa.String(255), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_battle_groups_public_id', 'battle_groups', ['public_id'])

    op.create_table(
        'battle_group_members',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column('group_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battle_groups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('battle_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  sa.ForeignKey('battles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.UniqueConstraint('group_id', 'battle_id'),
    )
    op.create_index('ix_battle_group_members_group_id', 'battle_group_members', ['group_id'])
    op.create_index('ix_battle_group_members_battle_id', 'battle_group_members', ['battle_id'])


def downgrade() -> None:
    op.drop_table('battle_group_members')
    op.drop_table('battle_groups')
