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

from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models.prices import ItemPriceLatest
from app.services.prices import _BATTLE_SENTINEL, get_battle_prices

log = logging.getLogger(__name__)

# silver_dropped não pode recalcular o ledger enquanto este worker ainda está
# corrigindo o cache antigo contaminado por listagens troll. Ambos sobem juntos
# no lifespan; o Event explicita a ordem sem serializar os workers no main.py.
ready = asyncio.Event()

BATCH_SIZE = 50      # igual ao _BATCH_SIZE interno do prices.py — 1 lote aqui = 1 lote de requisições lá
BUSY_INTERVAL = 60   # a cada 60s (era 5s): com TTL de 8h no get_battle_prices, a maioria dos itens
# já está em cache e não precisa re-buscar; 5s só servia pra refluxar a AODP
# com os mesmos 50 itens que já tinham sido cacheados há segundos.
IDLE_INTERVAL = 300  # segundos entre ciclos depois que a migração terminou

# Só reprocessa o que já existia ANTES do processo subir — preços recém-
# cacheados por este próprio serviço (ou por qualquer /battles/prices normal
# depois do fix) já usam a mediana, não precisam entrar de novo na fila.
_CUTOFF = datetime.now(timezone.utc)


async def _reprocess_batch(db) -> int:
    # Itens cacheados ANTES do processo subir (potencial bug da média antiga).
    # get_battle_prices agora tem TTL de 8h e faz ON CONFLICT DO UPDATE, então
    # só chamar nos IDs stale re-busca e sobrescreve — não precisa mais DELETE.
    item_ids = (await db.scalars(
        select(ItemPriceLatest.item_id)
        .where(ItemPriceLatest.city == _BATTLE_SENTINEL, ItemPriceLatest.recorded_at < _CUTOFF)
        .limit(BATCH_SIZE)
    )).all()
    if not item_ids:
        return 0
    await db.commit()  # fecha a read tx antes do HTTP (ver _write_deep_data fix)
    await get_battle_prices(db, item_ids)
    return len(item_ids)


async def run_forever() -> None:
    log.info("battle_price_reprocessor: iniciando")
    while True:
        n = 0
        async with AsyncSessionLocal() as db:
            try:
                n = await _reprocess_batch(db)
                if n:
                    log.info("battle_price_reprocessor: %d preços recalculados", n)
                else:
                    ready.set()
            except Exception as e:
                log.error("battle_price_reprocessor: erro: %s", e)
        await asyncio.sleep(BUSY_INTERVAL if n > 0 else IDLE_INTERVAL)
