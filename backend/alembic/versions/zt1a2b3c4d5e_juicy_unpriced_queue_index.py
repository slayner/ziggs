"""Índice da fila de juicy kills ainda sem preço.

Revision ID: zt1a2b3c4d5e
Revises: zr1a2b3c4d5e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zt1a2b3c4d5e"
down_revision: Union[str, None] = "zr1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A tabela tem muitos GB; criação concorrente mantém consultas e ingestão ativas.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_pke_juicy_unpriced_queue",
            "player_kill_events",
            ["region", "timestamp"],
            unique=False,
            postgresql_where=sa.text("silver_dropped IS NULL"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_pke_juicy_unpriced_queue",
            table_name="player_kill_events",
            postgresql_concurrently=True,
        )
