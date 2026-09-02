"""guild_profiles + alliance_profiles (profile_warmer de guilds/aliancas)

Revision ID: t6e1f9b5d2c8
Revises: s5d0e3f8c9b2
Create Date: 2026-07-20 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 't6e1f9b5d2c8'
down_revision: Union[str, None] = 's5d0e3f8c9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json():
    return JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        'guild_profiles',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True),
        sa.Column('albion_id', sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column('name', sa.String(length=255), nullable=False, index=True),
        sa.Column('region', sa.String(length=16), nullable=False, server_default='americas'),
        sa.Column('alliance_id', sa.String(length=64)),
        sa.Column('alliance_name', sa.String(length=255)),
        sa.Column('kill_fame', sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=False, server_default='0'),
        sa.Column('death_fame', sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=False, server_default='0'),
        sa.Column('members', _json()),
        sa.Column('founder_id', sa.String(length=64)),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('refresh_requested_at', sa.DateTime(timezone=True)),
    )
    op.create_table(
        'alliance_profiles',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True),
        sa.Column('albion_id', sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column('name', sa.String(length=255), nullable=False, index=True),
        sa.Column('region', sa.String(length=16), nullable=False, server_default='americas'),
        sa.Column('guilds', _json()),
        sa.Column('founder_id', sa.String(length=64)),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('refresh_requested_at', sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table('alliance_profiles')
    op.drop_table('guild_profiles')