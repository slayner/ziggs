"""bot_registrations table

Revision ID: b4f7c1d9e3a2
Revises: a1b2c3d4e5f6
Create Date: 2026-06-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "b4f7c1d9e3a2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_registrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), sa.ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("albion_player_id", sa.String(64), nullable=False, index=True),
        sa.Column("albion_player_name", sa.String(255), nullable=False),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("guild_id", "albion_player_id", name="uq_bot_reg_character"),
    )


def downgrade() -> None:
    op.drop_table("bot_registrations")
