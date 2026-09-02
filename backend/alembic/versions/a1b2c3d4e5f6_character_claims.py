"""character_claims and registered_characters tables

Revision ID: a1b2c3d4e5f6
Revises: e5b8d2f1a3c9
Create Date: 2026-06-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "e5b8d2f1a3c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "character_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("albion_player_id", sa.String(64), nullable=False, index=True),
        sa.Column("albion_player_name", sa.String(255), nullable=False),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("challenge", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("verified_event_id", sa.String(64)),
    )
    op.create_table(
        "registered_characters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("albion_player_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("albion_player_name", sa.String(255), nullable=False),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("character_claims.id"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("registered_characters")
    op.drop_table("character_claims")
