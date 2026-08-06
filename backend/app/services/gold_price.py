"""Histórico da cotação prata↔ouro (AODP gold.json), persistido no nosso
banco — antes o Dashboard buscava direto da AODP a cada visita, sem garantia
de quanto tempo aquele serviço externo vai continuar no ar. Backfill
completo desde 2017-01-01 (a API tem esse histórico) + poll de janela curta
periódico, no mesmo espírito de services/player_count_snapshot.py.

Fonte: httpx contra {host}/api/v2/stats/gold.json?date=&end_date= — mesmo
padrão de requisição usado em services/prices.py."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal
from app.models.prices import GoldPriceSnapshot

log = logging.getLogger(__name__)

HOSTS = {
    "americas": "https://west.albion-online-data.com",
    "europe": "https://europe.albion-online-data.com",
    "asia": "https://east.albion-online-data.com",
}
_SINCE = datetime(2017, 1, 1, tzinfo=timezone.utc)
_BACKFILL_WINDOW = timedelta(days=180)
_POLL_WINDOW = timedelta(days=2)
POLL_INTERVAL = 900  # 15min
# ponytail: folga antes do backfill inicial — evita somar à fila de boot com
# battle_tracker/weapon_stats/etc (ver STARTUP_GRACE_DELAY em battle_tracker.py).
_STARTUP_DELAY = 120


def _mmddyyyy(d: datetime) -> str:
    return f"{d.month}-{d.day}-{d.year}"


def _parse_ts(raw: str) -> datetime:
    s = raw if raw.endswith("Z") else raw + "Z"
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


async def _fetch_chunk(region: str, start: datetime, end: datetime) -> list[dict]:
    url = f"{HOSTS[region]}/api/v2/stats/gold.json"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, params={"date": _mmddyyyy(start), "end_date": _mmddyyyy(end)})
        resp.raise_for_status()
        return resp.json()


async def _region_cursor(db: AsyncSession, region: str) -> datetime:
    row = await db.scalar(select(func.max(GoldPriceSnapshot.recorded_at)).where(GoldPriceSnapshot.region == region))
    if row is None:
        return _SINCE
    return row if row.tzinfo else row.replace(tzinfo=timezone.utc)


async def _insert_rows(db: AsyncSession, region: str, raw: list[dict]) -> int:
    rows = [
        {"region": region, "price": r["price"], "recorded_at": _parse_ts(r["timestamp"])}
        for r in raw if r.get("price")
    ]
    if not rows:
        return 0
    # on_conflict_do_nothing: idempotente por construção — reprocessar uma
    # janela já coberta (retomada após crash, ou overlap do poll com o fim
    # do backfill) nunca duplica, só ignora o que já existe.
    await db.execute(_pg_insert(GoldPriceSnapshot).on_conflict_do_nothing(), rows)
    await db.commit()
    return len(rows)


async def backfill(db: AsyncSession) -> None:
    """Por região, avança em janelas de 180 dias a partir do cursor (MAX já
    salvo, ou 2017-01-01 se vazio) até agora. Resumível por construção: uma
    interrupção no meio simplesmente recomeça do MAX real na próxima chamada,
    sem re-percorrer o que já foi salvo nem duplicar."""
    now = datetime.now(timezone.utc)
    for region in HOSTS:
        cursor = await _region_cursor(db, region)
        # Libera read tx antes do HTTP — read tx aberta durante await impede
        # wal_checkpoint, cresce o WAL, commit futuro fsync-o inteiro.
        await db.commit()
        while cursor < now:
            window_end = min(cursor + _BACKFILL_WINDOW, now)
            try:
                raw = await _fetch_chunk(region, cursor, window_end)
            except Exception as e:
                log.error("gold_price: backfill %s [%s..%s] falhou: %s", region, cursor.date(), window_end.date(), e)
                break  # próximo ciclo de run_forever tenta essa janela de novo
            n = await _insert_rows(db, region, raw)
            log.info("gold_price: backfill %s [%s..%s] +%d", region, cursor.date(), window_end.date(), n)
            cursor = window_end
            await asyncio.sleep(2)  # gentil com a AODP


async def _poll_once(db: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    start = now - _POLL_WINDOW
    for region in HOSTS:
        try:
            raw = await _fetch_chunk(region, start, now)
        except Exception as e:
            log.error("gold_price: poll %s falhou: %s", region, e)
            continue
        await _insert_rows(db, region, raw)
        await asyncio.sleep(2)


async def run_forever() -> None:
    await asyncio.sleep(_STARTUP_DELAY)
    log.info("gold_price: iniciando backfill (desde %s)", _SINCE.date())
    async with AsyncSessionLocal() as db:
        try:
            await backfill(db)
        except Exception as e:
            log.error("gold_price: erro no backfill: %s", e)
    log.info("gold_price: backfill concluído, poll a cada %ds", POLL_INTERVAL)
    while True:
        async with AsyncSessionLocal() as db:
            try:
                await _poll_once(db)
            except Exception as e:
                log.error("gold_price: erro no poll: %s", e)
        await asyncio.sleep(POLL_INTERVAL)
