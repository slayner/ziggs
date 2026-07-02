"""Reprocessa batalhas marcadas com Battle.reprocess_reason — fila genérica
de "essa lógica de processamento mudou, essa batalha precisa ser recalculada
com os dados crus de novo" (ver coluna em app/models/battles.py).

Sempre que uma mudança de lógica (faction_key, elegibilidade etc.) invalida
dados já gravados, em vez de escrever um script avulso que reprocessa tudo
de uma vez, só marca as batalhas afetadas (UPDATE em massa, metadado,
instantâneo — ver scripts/mark_*.py) e este serviço dá conta sozinho, aos
poucos. Roda DENTRO do mesmo processo da API (registrado no lifespan do
main.py, não um script separado) — por isso não disputa lock do SQLite com
tráfego real: as escritas entram na MESMA escala cooperativa de asyncio que
todo o resto do servidor, em vez de duas conexões de processos diferentes
brigando pelo arquivo. Foi exatamente rodar um script à parte que travou
request de usuário com "database is locked" numa rodada anterior."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import case, select

from app.db import SessionLocal
from app.models.battles import Battle
from app.services.battle_tracker import (
    REPROCESS_REASON_EMPTY, REPROCESS_REASON_FAILED, _backfill_deep_fetch_all, _reprocess_urgent_sem,
    _write_deep_data,
)
from app.services.player_tracker import HOSTS, make_client

log = logging.getLogger(__name__)

BATCH_SIZE = 15        # batalhas por ciclo — pequeno de propósito, é fila de fundo, não corrida
BUSY_INTERVAL = 15       # segundos entre ciclos enquanto há fila — era 3s, contribuía pro 429 em
# cascata somado a todo outro serviço de fundo rodando rápido ao mesmo tempo (ver battle_tracker.py)
IDLE_INTERVAL = 60       # segundos entre ciclos quando a fila esvaziou

# Motivos de descoberta AO VIVO (ver battle_tracker.py) furam a fila —
# batalha nova fica "invisível" pro usuário até resolver, não pode esperar
# atrás de campanha histórica de fundo (ex.: "guildless_bracket", que pode
# ter milhares de itens acumulados e nenhuma pressa nenhuma).
_URGENT_REASONS = (REPROCESS_REASON_EMPTY, REPROCESS_REASON_FAILED)


async def _reprocess_batch(client, db) -> int:
    priority = case((Battle.reprocess_reason.in_(_URGENT_REASONS), 0), else_=1)
    battles = db.scalars(
        select(Battle).where(Battle.reprocess_reason.isnot(None))
        .order_by(priority, Battle.id).limit(BATCH_SIZE)
    ).all()
    if not battles:
        return 0

    by_region: dict[str, list[Battle]] = {}
    for b in battles:
        by_region.setdefault(b.region, []).append(b)

    processed = 0
    for region, region_battles in by_region.items():
        host = HOSTS.get(region)
        if host is None:
            for b in region_battles:
                b.reprocess_reason = None
            db.commit()
            continue
        # Se o grupo inteiro é urgente (batalha nova, ver _URGENT_REASONS),
        # usa o pool DEDICADO em vez do padrão compartilhado com o backfill
        # histórico perpétuo — ver battle_tracker.py pro porquê. Grupo misto
        # (raro: acontece só na transição em que a fila urgente esvazia no
        # meio de um ciclo) cai no padrão, sem prejuízo real.
        sem = _reprocess_urgent_sem if all(b.reprocess_reason in _URGENT_REASONS for b in region_battles) else None
        for result in await _backfill_deep_fetch_all(client, host, region_battles, sem=sem):
            battle = result[0]
            if isinstance(result[1], Exception):
                log.warning("battle_reprocessor: falha ao buscar %s (%s): %r", battle.albion_id, region, result[1])
                continue
            _, raw, events = result
            try:
                ok = _write_deep_data(db, battle, raw, events)
            except Exception as e:
                db.rollback()
                log.warning("battle_reprocessor: falha ao salvar %s (%s): %r", battle.albion_id, region, e)
                continue
            # _write_deep_data pode retornar False sem levantar exceção (API
            # ainda sem detalhe/eventos pra essa batalha) — só tira da fila
            # (reprocess_reason=None) quando REALMENTE gravou; senão a
            # batalha saía da fila mesmo sem nunca ter sido processada de
            # verdade, e ficava perdida pra sempre sem sinal de erro.
            if ok:
                battle.reprocess_reason = None
                db.commit()
                processed += 1

    return processed


async def run_forever() -> None:
    log.info("battle_reprocessor: iniciando")
    while True:
        db = SessionLocal()
        n = 0
        try:
            async with make_client() as client:
                n = await _reprocess_batch(client, db)
            if n:
                log.info("battle_reprocessor: %d batalhas reprocessadas", n)
        except Exception as e:
            log.error("battle_reprocessor: erro: %s", e)
        finally:
            db.close()
        await asyncio.sleep(BUSY_INTERVAL if n > 0 else IDLE_INTERVAL)
