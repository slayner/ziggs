"""índice da fila de juicy kills do bot.

Revision ID: zz2a3b4c5d6e
Revises: zz1a2b3c4d5e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zz2a3b4c5d6e"
down_revision: Union[str, None] = "zz1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # A tabela tem milhões de kills; não bloquear inserts do feed durante o build.
        with op.get_context().autocommit_block():
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_pke_juicy_queue "
                "ON player_kill_events (region, silver_dropped, timestamp) "
                "WHERE fame > 0 AND silver_dropped IS NOT NULL"
            )
    else:
        op.create_index(
            "ix_pke_juicy_queue", "player_kill_events",
            ["region", "silver_dropped", "timestamp"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_pke_juicy_queue")
    else:
        op.drop_index("ix_pke_juicy_queue", table_name="player_kill_events")
