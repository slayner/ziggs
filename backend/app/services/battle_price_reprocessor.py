"""Reprocessa o cache de preço de loot de batalha (ItemPriceLatest,
city=_BATTLE_SENTINEL) — refaz cada item já cacheado usando a lógica
corrigida (mediana, não média) de services.prices.get_battle_prices.

Esse cache é PERMANENTE por design (nunca reconsultado sozinho — ver
docstring de get_battle_prices), então o bug antigo da média (uma única
listagem "troll" sem concorrência inflando pra sempre o preço de um item
ilíquido, ex.: T5_BAG_INSIGHT cacheado a 27M quando o mercado real é ~40k)
deixava itens errados PERMANENTEMENTE — corrigir só o algoritmo não
re-arruma o que já estava cacheado. Este serviço varre o que existia ANTES
do fix (corte por recorded_at < _CUTOFF, capturado na subida do processo) e
recalcula, aos poucos, sem competir por rate limit com o resto dos serviços
de fundo. Roda uma vez só: quando não sobra nada anterior ao corte, fica
ocioso pra sempre (IDLE_INTERVAL) — não é um serviço perpétuo como os outros,
é uma migração de dados que roda dentro do processo da API em vez de um
script avulso (mesmo motivo do battle_reprocessor: sem disputar lock do
SQLite com um segundo processo)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.models.prices import ItemPriceLatest
from app.services.prices import _BATTLE_SENTINEL, get_battle_prices

log = logging.getLogger(__name__)

# silver_dropped não pode recalcular o ledger enquanto este worker ainda está
# corrigindo o cache antigo contaminado por listagens troll. Ambos sobem juntos
# no lifespan; o Event explicita a ordem sem serializar os workers no main.py.
ready = asyncio.Event()

BATCH_SIZE = 50      # igual ao _BATCH_SIZE interno do prices.py — 1 lote aqui = 1 lote de requisições lá
BUSY_INTERVAL = 5    # subiu de 30/10s pra 50/5s (usuário batendo em preços errados na hora de navegar,
# vale acelerar a migração) — ainda devagar de propósito,
# soma à taxa agregada de requisição de todo o resto dos serviços de fundo (ver battle_tracker.py)
IDLE_INTERVAL = 300  # segundos entre ciclos depois que a migração terminou

# Só reprocessa o que já existia ANTES do processo subir — preços recém-
# cacheados por este próprio serviço (ou por qualquer /battles/prices normal
# depois do fix) já usam a mediana, não precisam entrar de novo na fila.
_CUTOFF = datetime.now(timezone.utc)


async def _reprocess_batch(db) -> int:
    item_ids = db.scalars(
        select(ItemPriceLatest.item_id)
        .where(ItemPriceLatest.city == _BATTLE_SENTINEL, ItemPriceLatest.recorded_at < _CUTOFF)
        .limit(BATCH_SIZE)
    ).all()
    if not item_ids:
        return 0
    # Contenção passageira com outro serviço de fundo escrevendo no mesmo
    # instante (mesma classe de "database is locked" já tratada em
    # battle_groups.py) — 2 tentativas curtas em vez de perder o lote
    # inteiro e cair pro IDLE_INTERVAL (5min) mesmo com a migração longe de
    # terminar. asyncio.sleep (não time.sleep): isto roda no loop de
    # eventos, um sleep bloqueante travaria toda a API junto.
    for attempt in range(2):
        try:
            db.execute(delete(ItemPriceLatest).where(
                ItemPriceLatest.city == _BATTLE_SENTINEL,
                ItemPriceLatest.item_id.in_(item_ids),
            ))
            db.commit()
            break
        except OperationalError:
            db.rollback()
            if attempt == 1:
                raise
            await asyncio.sleep(1.0)
    await get_battle_prices(db, item_ids)  # refaz + re-cacheia (mediana, ver prices.py)
    return len(item_ids)


async def run_forever() -> None:
    log.info("battle_price_reprocessor: iniciando")
    while True:
        db = SessionLocal()
        n = 0
        try:
            n = await _reprocess_batch(db)
            if n:
                log.info("battle_price_reprocessor: %d preços recalculados", n)
            else:
                ready.set()
        except Exception as e:
            log.error("battle_price_reprocessor: erro: %s", e)
        finally:
            db.close()
        await asyncio.sleep(BUSY_INTERVAL if n > 0 else IDLE_INTERVAL)
