"""Descobre batalhas PEQUENAS (< 4 jogadores) que a lista "recent" da API do
Albion nunca expõe — confirmado empiricamente: toda entrada nessa lista, em
qualquer offset da janela paginável (0 até o teto de ~10000), tem 4+
jogadores. Não é algo que controlamos, é a própria API do Albion que filtra
o que aparece ali (battle_tracker.sync_recent/backfill_step NUNCA vêem uma
batalha menor que essa, não importa quantas páginas peçam).

Só chegam até nós via BattleId dos eventos de kill individuais (ver
player_tracker.py, que cobre QUALQUER kill — 1v1 incluso — via um feed de
API diferente, sem esse chão mínimo). Este serviço varre o ledger de kills
incrementalmente e resolve pelo ID cru (mesmo fluxo do "colar link" manual,
battle_tracker.resolve_by_albion_id, que funciona pra qualquer tamanho de
batalha) as que ainda não têm Battle — assim 2v3 sem guilda entra no banco
e é analisada igual qualquer outra."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.db import SessionLocal
from app.models.battles import Battle
from app.models.players import PlayerKillEvent
from app.services.albion_gate import NEW_SMALL, albion_scope
from app.services.battle_tracker import resolve_by_albion_id
from app.services.player_tracker import HOSTS, make_client

log = logging.getLogger(__name__)

BATCH_SIZE = 200        # eventos de kill varridos por região por ciclo
BUSY_INTERVAL = 15       # segundos entre ciclos enquanto ainda há kill novo pra checar — era 5s, ver
# battle_tracker.BACKFILL_CYCLE_INTERVAL pro porquê (taxa agregada de requisição entre todo
# serviço de fundo rodando junto, não só concorrência, é o que estourava o rate limit da API)
IDLE_INTERVAL = 60       # segundos entre ciclos quando alcançou o fim do ledger

# ponytail: cursor em MEMÓRIA (reseta no restart, revarre do zero) — checar
# um trecho já visto de novo é barato (SELECT + comparação local, só bate na
# API do Albion pra batalha REALMENTE nova), não justifica tabela de cursor
# persistente só pra isso. Upgrade se o ledger crescer tanto que o rescan do
# zero pesar: persistir last_id por região tipo BattleSyncCursor.
_last_kill_event_id: dict[str, int] = {}


async def _discover_region(client, db, region: str) -> int:
    last_id = _last_kill_event_id.get(region, 0)
    rows = db.execute(
        select(PlayerKillEvent.id, PlayerKillEvent.albion_battle_id)
        .where(
            PlayerKillEvent.region == region,
            PlayerKillEvent.id > last_id,
            PlayerKillEvent.albion_battle_id.isnot(None),
        )
        .order_by(PlayerKillEvent.id)
        .limit(BATCH_SIZE)
    ).all()
    if not rows:
        return 0
    _last_kill_event_id[region] = rows[-1][0]

    candidate_ids = sorted({albion_battle_id for _, albion_battle_id in rows})
    known = set(db.scalars(
        select(Battle.albion_id).where(Battle.region == region, Battle.albion_id.in_(candidate_ids))
    ).all())
    missing = [cid for cid in candidate_ids if cid not in known]

    for albion_id in missing:
        try:
            # ponytail: NEW_SMALL flat — esses IDs vêm do ledger de kills recente,
            # então prioridade de "batalha nova pequena". resolve_by_albion_id
            # faz o deep só se não-frozen; o scope aqui só governa o bg pool.
            async with albion_scope(NEW_SMALL):
                await resolve_by_albion_id(client, db, albion_id)
        except Exception as e:
            log.warning("small_battle_discovery: falha ao resolver %s (%s): %r", albion_id, region, e)

    return len(missing)


async def run_forever() -> None:
    log.info("small_battle_discovery: iniciando")
    while True:
        db = SessionLocal()
        total_new = 0
        try:
            async with make_client() as client:
                for region in HOSTS:
                    total_new += await _discover_region(client, db, region)
            if total_new:
                log.info("small_battle_discovery: %d batalhas pequenas resolvidas", total_new)
        except Exception as e:
            log.error("small_battle_discovery: erro: %s", e)
        finally:
            db.close()
        await asyncio.sleep(BUSY_INTERVAL if total_new else IDLE_INTERVAL)
