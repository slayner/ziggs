"""alliance fields on guilds + is_ally on bot_registrations

Revision ID: c2a8e6f1b7d4
Revises: b4f7c1d9e3a2
Create Date: 2026-06-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "c2a8e6f1b7d4"
down_revision = "b4f7c1d9e3a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guilds", sa.Column("albion_alliance_id", sa.String(64)))
    op.add_column("guilds", sa.Column("albion_alliance_name", sa.String(255)))
    op.add_column("bot_registrations", sa.Column("is_ally", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("bot_registrations", "is_ally")
    op.drop_column("guilds", "albion_alliance_name")
    op.drop_column("guilds", "albion_alliance_id")
