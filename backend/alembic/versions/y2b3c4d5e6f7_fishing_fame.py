"""fishing_fame em albion_players — fama de pesca (FishingFame do
LifetimeStatistics, escalar irmão de Gathering.All.Total).

Permite ranking de highscore de pesca por jogador — mesmo padrão do
gather_* (coleta por recurso), mas fishing é um escalar solto no blob (não
fica dentro de Gathering), então ganha coluna própria.

Revision ID: y2b3c4d5e6f7
Revises: x1a2b3c4d5e6
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'y2b3c4d5e6f7'
down_revision: Union[str, None] = 'x1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('albion_players',
                  sa.Column('fishing_fame', sa.BigInteger(), nullable=False, server_default='0'))
    op.create_index('ix_albion_players_fishing_fame',
                    'albion_players', ['fishing_fame'])

    # Backfill a partir do lifetime_statistics já gravado — FishingFame é
    # escalar no topo do blob ( LifetimeStatistics.FishingFame), não dentro de
    # Gathering. Mesmo padrão do backfill de gather_* da migration anterior.
    bind = op.get_bind()
    _backfill_fishing(bind)


def _backfill_fishing(bind) -> None:
    import json
    rows = bind.execute(sa.text(
        "SELECT id, lifetime_statistics FROM albion_players WHERE lifetime_statistics IS NOT NULL"
    )).fetchall()
    if not rows:
        return
    updated = 0
    for pid, blob in rows:
        if isinstance(blob, str):
            try:
                blob = json.loads(blob)
            except (ValueError, TypeError):
                continue
        if not isinstance(blob, dict):
            continue
        fishing = int(blob.get("FishingFame") or 0)
        if fishing <= 0:
            continue
        bind.execute(sa.text("UPDATE albion_players SET fishing_fame = :v WHERE id = :pid"),
                     {"v": fishing, "pid": pid})
        updated += 1
    if updated:
        print(f"  backfill fishing_fame: {updated} jogadores atualizados")


def downgrade() -> None:
    op.drop_index('ix_albion_players_fishing_fame', table_name='albion_players')
    op.drop_column('albion_players', 'fishing_fame')