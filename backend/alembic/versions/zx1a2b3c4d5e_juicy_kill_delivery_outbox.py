"""Outbox materializada de juicy kills por guilda.

Revision ID: zx1a2b3c4d5e
Revises: zt1a2b3c4d5e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zx1a2b3c4d5e"
down_revision: Union[str, None] = "zt1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "juicy_kill_deliveries",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("kill_id", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("fame", sa.BigInteger(), nullable=False),
        sa.Column("silver_dropped", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["kill_id"], ["player_kill_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("guild_id", "kill_id"),
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_jkd_pending_poll",
            "juicy_kill_deliveries",
            ["guild_id", "region", "occurred_at", "kill_id"],
            unique=False,
            postgresql_where=sa.text("state = 'pending'"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_jkd_pending_poll",
            table_name="juicy_kill_deliveries",
            postgresql_concurrently=True,
        )
    op.drop_table("juicy_kill_deliveries")
