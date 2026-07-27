"""invalida silver_dropped calculado antes da mediana anti-troll.

Revision ID: z8d9e0f1a2b3
Revises: z7c8d9e0f1a2
"""
from typing import Sequence, Union

from alembic import op


revision: str = "z8d9e0f1a2b3"
down_revision: Union[str, None] = "z7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL = pendente no silver_dropped worker. A barreira `ready` desse worker
    # garante que a reprecificação só começa depois de o cache permanente de
    # batalha ter sido reconstruído com mediana, não média.
    op.execute("UPDATE player_kill_events SET silver_dropped = NULL")


def downgrade() -> None:
    # Os totais anteriores eram incorretos; não há dado válido para restaurar.
    pass
