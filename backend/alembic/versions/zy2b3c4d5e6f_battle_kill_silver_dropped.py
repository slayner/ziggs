"""Persiste prata perdida nas mortes processadas de batalha.

Revision ID: zy2b3c4d5e6f
Revises: zx1a2b3c4d5e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zy2b3c4d5e6f"
down_revision: Union[str, None] = "zx1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("battle_kill_events", sa.Column("silver_dropped", sa.BigInteger(), nullable=True))
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_bke_unpriced_queue",
            "battle_kill_events",
            ["timestamp", "id"],
            unique=False,
            postgresql_where=sa.text("silver_dropped IS NULL"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("ix_bke_unpriced_queue", table_name="battle_kill_events", postgresql_concurrently=True)
    op.drop_column("battle_kill_events", "silver_dropped")
