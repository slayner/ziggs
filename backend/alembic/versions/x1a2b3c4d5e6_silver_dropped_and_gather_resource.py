"""silver_dropped em player_kill_events + gather_* por recurso em albion_players

silver_dropped: prata perdida na morte (preço dos itens equipados + carregados),
precificada no processamento (worker silver_dropped), em vez de só on-demand ao
abrir o perfil. Permite ranking de highscore "mais prata dropada" por jogador.

gather_wood/hide/ore/rock/fiber: coleta por recurso extraída do
lifetime_statistics.Gathering.{Wood,Hide,Ore,Rock,Fiber}.Total — gathering_fame
(total) já era escalar; o por-recurso só vivia no blob JSON. Permite rankings de
coleta por recurso no highscore.

Revision ID: x1a2b3c4d5e6
Revises: w9b4c5d6e7f8
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'x1a2b3c4d5e6'
down_revision: Union[str, None] = 'w9b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # nullable: NULL = ainda não precificado (pendente no worker), 0 = precificado
    # e deu zero (vítima sem gear ou itens sem cotação), >0 = prata real. Usar 0
    # como "pendente" (default não-nullable) causaria loop infinito no worker:
    # evento com gear mas preço 0 ficava 0 pra sempre e era reprocessado todo ciclo.
    op.add_column('player_kill_events',
                  sa.Column('silver_dropped', sa.BigInteger(), nullable=True))
    op.create_index('ix_player_kill_events_silver_dropped',
                     'player_kill_events', ['silver_dropped'])

    for res in ('wood', 'hide', 'ore', 'rock', 'fiber'):
        op.add_column('albion_players',
                      sa.Column(f'gather_{res}', sa.BigInteger(), nullable=False, server_default='0'))
    for res in ('wood', 'hide', 'ore', 'rock', 'fiber'):
        op.create_index(f'ix_albion_players_gather_{res}',
                        'albion_players', [f'gather_{res}'])
    op.create_index('ix_albion_players_gathering_fame',
                    'albion_players', ['gathering_fame'])

    # Backfill de gather_* a partir do lifetime_statistics já gravado em
    # jogadores existentes (novos passam pelo upsert_player daqui pra frente).
    # Extrai do JSON no formato da API do Albion: Gathering.{Wood,Hide,Ore,
    # Rock,Fiber}.Total. Funciona em SQLite (json_extract) e Postgres (->).
    bind = op.get_bind()
    _backfill_gather(bind)


def _backfill_gather(bind) -> None:
    import json
    res_keys = {'wood': 'Wood', 'hide': 'Hide', 'ore': 'Ore', 'rock': 'Rock', 'fiber': 'Fiber'}
    rows = bind.execute(sa.text(
        "SELECT id, lifetime_statistics FROM albion_players WHERE lifetime_statistics IS NOT NULL"
    )).fetchall()
    if not rows:
        return
    updated = 0
    for pid, blob in rows:
        # O SELECT cru devolve str (JSON text do SQLite), não dict — o type
        # decorator do SQLAlchemy só age em queries ORM, não em sa.text cru.
        if isinstance(blob, str):
            try:
                blob = json.loads(blob)
            except (ValueError, TypeError):
                continue
        if not isinstance(blob, dict):
            continue
        gathering = blob.get("Gathering") or {}
        vals = {f"gather_{r}": ((gathering.get(res_keys[r]) or {}).get("Total") or 0) for r in res_keys}
        if not any(vals.values()):
            continue
        set_clause = ", ".join(f"{k} = :{k}" for k in vals)
        bind.execute(sa.text(f"UPDATE albion_players SET {set_clause} WHERE id = :pid"),
                     {**vals, "pid": pid})
        updated += 1
    if updated:
        print(f"  backfill gather_*: {updated} jogadores atualizados")


def downgrade() -> None:
    op.drop_index('ix_albion_players_gathering_fame', table_name='albion_players')
    for res in ('wood', 'hide', 'ore', 'rock', 'fiber'):
        op.drop_index(f'ix_albion_players_gather_{res}', table_name='albion_players')
        op.drop_column('albion_players', f'gather_{res}')
    op.drop_index('ix_player_kill_events_silver_dropped', table_name='player_kill_events')
    op.drop_column('player_kill_events', 'silver_dropped')