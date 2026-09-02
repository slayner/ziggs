"""Varredor de resumo de mercado — mantém MarketSnapshot quente pra TODOS os
itens do catálogo, em GOTEJAMENTO contínuo. A UI (aba de mercado) só lê o
snapshot; nenhuma requisição externa acontece no caminho da leitura.

Por item (qualidade 1, janela de 7 dias):
  price      — preço médio mais recente
  change_pct — variação % primeiro→último dia da janela ("margem")
  demand     — total de itens vendidos na janela
  price_ts   — quando o preço foi visto por último (filtro de frescor do site)

Fonte por item: comparada por idade (price_ts). A varredura AODP (1 lote de
50 itens a cada 30s, ciclo contínuo do catálogo) é a única consulta AODP —
NÃO fazemos fallback sob demanda quando nosso dado falta. Para cada item do
lote: se o companion capturou (item_price_history) e o preço nosso é mais
recente ou igual ao AODP, fonte='ziggs'; senão fonte='aodp' (ainda não
cobrimos). Itens sem nenhuma fonte ficam sem snapshot até a próxima volta.

Ritmo: UM lote de 50 itens (= 1 request ao AODP) a cada 30 segundos, o tempo
todo. Com o catálogo atual (~83 lotes), o ciclo completo fecha em ~42 min —
cada item re-verificado nesse intervalo.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal
from app.models.prices import ItemPriceHistory, MarketSnapshot
from app.services.market_history import get_catalog

log = logging.getLogger(__name__)

# Mercados são separados por servidor do Albion — cada região tem seu host AODP.
# Chaves = mesma nomenclatura do seletor de servidor do site (useServer).
_REGION_HOSTS = {
    "west": "https://west.albion-online-data.com/api/v2/stats/history",
    "east": "https://east.albion-online-data.com/api/v2/stats/history",
    "europe": "https://europe.albion-online-data.com/api/v2/stats/history",
}
_REGIONS = list(_REGION_HOSTS)  # ["west", "east", "europe"]
_CITIES = "Lymhurst,Fort Sterling,Thetford,Bridgewatch,Martlock,Caerleon"
_WINDOW = timedelta(days=7)
_BATCH = 50
BATCH_PAUSE = 30.0         # 1 request (lote) a cada 30s, constante
_STARTUP_DELAY = 180       # não competir com a fila de boot dos outros trackers


def _ts_to_ms(raw: int) -> int:
    """Timestamp de bucket em formato incerto (.NET ticks / unix ms / unix s)
    → unix ms. Mesma normalização do frontend."""
    if raw > 10**15:
        return raw // 10_000 - 62_135_596_800_000
    if raw > 10**11:
        return raw
    if raw > 10**8:
        return raw * 1000
    return raw


def _summarize(points: list[tuple[int, int, int]]) -> tuple[int, float, int, int]:
    """(ts_ms, avg_price, count)[] → (price, change_pct, demand, last_ts_ms).
    last_ts_ms = quando o preço foi visto por último (0 se vazio)."""
    pts = sorted(p for p in points if p[1] > 0)
    if not pts:
        return (0, 0.0, 0, 0)
    first, last = pts[0][1], pts[-1][1]
    change = ((last - first) / first * 100) if first > 0 else 0.0
    demand = sum(p[2] for p in pts)
    return (last, round(change, 1), demand, pts[-1][0])


async def _own_summary(db: AsyncSession, item_id: str, region: str, cutoff_ms: int) -> tuple[int, float, int, int] | None:
    """Resumo a partir do histórico próprio (da região) ; None se cobertura
    insuficiente.

    Usa UM timescale só (prefere 1=7d, depois 0, depois 2) — somar buckets de
    escalas diferentes contaria a mesma venda duas vezes.
    """
    rows = (await db.scalars(
        select(ItemPriceHistory).where(
            ItemPriceHistory.item_id == item_id,
            ItemPriceHistory.region == region,
            ItemPriceHistory.quality == 1,
        )
    )).all()
    if not rows:
        return None
    for scale in (1, 0, 2):
        pts = [
            (_ts_to_ms(r.bucket_ts), round(r.silver_amount / r.item_count), r.item_count)
            for r in rows
            if r.timescale == scale and r.item_count > 0 and _ts_to_ms(r.bucket_ts) >= cutoff_ms
        ]
        if len(pts) >= 2:
            return _summarize(pts)
    return None


async def _fetch_aodp_batch(client: httpx.AsyncClient, host: str, ids: list[str]) -> dict[str, list[tuple[int, int, int]]]:
    """Histórico diário AODP pra um lote (host = servidor da região) → item_id:
    (ts_ms, avg, count)[]. Agrega cidades por dia (média ponderada por volume)."""
    resp = await client.get(
        f"{host}/{','.join(ids)}.json",
        params={"locations": _CITIES, "qualities": 1, "time-scale": 24},
    )
    resp.raise_for_status()
    # item → dia → [(avg, count)]
    by_item: dict[str, dict[str, list[tuple[int, int]]]] = {}
    for entry in resp.json():
        iid = entry.get("item_id")
        if not iid:
            continue
        dm = by_item.setdefault(iid, {})
        for d in entry.get("data") or []:
            if not d.get("avg_price") or not d.get("item_count"):
                continue
            dm.setdefault(d["timestamp"][:10], []).append((d["avg_price"], d["item_count"]))
    out: dict[str, list[tuple[int, int, int]]] = {}
    for iid, dm in by_item.items():
        pts = []
        for day, vals in dm.items():
            total = sum(c for _, c in vals)
            if total <= 0:
                continue
            wavg = round(sum(a * c for a, c in vals) / total)
            ts_ms = int(datetime.fromisoformat(day + "T00:00:00+00:00").timestamp() * 1000)
            pts.append((ts_ms, wavg, total))
        out[iid] = pts
    return out


async def _upsert(db: AsyncSession, item_id: str, region: str, price: int, change: float,
            demand: int, source: str, now: datetime, price_ts_ms: int = 0) -> None:
    price_ts = (
        datetime.fromtimestamp(price_ts_ms / 1000, tz=timezone.utc)
        if price_ts_ms > 0 else None
    )
    row = await db.scalar(select(MarketSnapshot).where(
        MarketSnapshot.item_id == item_id, MarketSnapshot.region == region,
    ))
    if row:
        row.price, row.change_pct, row.demand = price, change, demand
        row.source, row.updated_at, row.price_ts = source, now, price_ts
    else:
        db.add(MarketSnapshot(
            item_id=item_id, region=region, price=price, change_pct=change,
            demand=demand, source=source, updated_at=now, price_ts=price_ts,
        ))


async def _process_batch(client: httpx.AsyncClient, region: str, batch: list[str]) -> tuple[int, int]:
    """Processa UM lote de itens de UMA região na varredura AODP periódica.
    Para cada item: comparamos o AODP (deste lote/região) com nosso histórico
    próprio (da mesma região) e gravamos o MAIS RECENTE — fonte='ziggs' quando
    nosso preço é mais novo (ou igual), 'aodp' quando o AODP ainda é o mais
    recente (companion ainda não capturou).

    Não consultamos AODP sob demanda: a varredura periódica é a única fonte
    de dados AODP, e itens sem cobertura nossa ficam com fonte AODP até o
    companion preencher o histórico próprio. Retorna (com_dado, sem_dado).
    """
    now = datetime.now(timezone.utc)
    cutoff_ms = int((now - _WINDOW).timestamp() * 1000)
    filled = 0
    empty = 0
    async with AsyncSessionLocal() as db:
        # 1) AODP: UM request por lote (host da região) — varredura periódica.
        #    Se falhar, o lote fica pra próxima volta (idempotente).
        try:
            aodp = await _fetch_aodp_batch(client, _REGION_HOSTS[region], batch)
        except Exception as e:
            log.warning("market_snapshot: lote AODP (%s) falhou: %s", region, e)
            aodp = {}

        # 2) Pra cada item do lote: pega o AODP (recém-varrido) e o nosso próprio
        #    histórico, grava o mais recente. Itens sem AODP e sem nosso dado
        #    ficam sem snapshot (empty).
        for iid in batch:
            # AODP primeiro (recém-varrido neste ciclo).
            a_pts = aodp.get(iid, [])
            a_pts_w = [p for p in a_pts if p[0] >= cutoff_ms]
            a_price, a_change, a_demand, a_seen_ms = _summarize(a_pts_w)
            if a_price == 0 and a_pts:
                # Item ilíquido: nada na janela de 7d, mas há preço antigo —
                # grava com o price_ts VELHO (filtro de frescor do site decide
                # se mostra). Demanda 0.
                a_price, a_change, _d, a_seen_ms = _summarize(a_pts)
                a_demand = 0

            # Nosso histórico (da região): só vira se tiver ≥2 buckets na janela.
            own = await _own_summary(db, iid, region, cutoff_ms)

            # Decide fonte por idade (price_ts). empate ou nosso mais novo
            # → 'ziggs'; AODP mais novo → 'aodp'. Sem nenhum dos dois → empty.
            if own and (a_price == 0 or own[3] >= a_seen_ms):
                await _upsert(db, iid, region, own[0], own[1], own[2], "ziggs", now, own[3])
                filled += 1
            elif a_price > 0:
                await _upsert(db, iid, region, a_price, a_change, a_demand, "aodp", now, a_seen_ms)
                filled += 1
            else:
                empty += 1
        try:
            t_c0 = time.monotonic()
            await db.commit()
            t_commit = time.monotonic() - t_c0
            if t_commit > 1.0:
                log.warning("market_snapshot: commit lento — %s/%d em %.1fs", region, len(batch), t_commit)
        except OperationalError as e:
            if "database is locked" in str(e).lower():
                log.warning("market_snapshot: db locked — lote %s reprocessado próximo ciclo", region)
                await db.rollback()
            else:
                raise
    return (filled, empty)


async def sweep_once(region: str = "west") -> tuple[int, int]:
    """Varredura completa de UMA região em sequência (uso em testes/backfill
    manual — o run_forever usa o gotejamento round-robin, não isto)."""
    ids = [c["id"] for c in get_catalog()]
    filled = 0
    empty = 0
    async with httpx.AsyncClient(timeout=25) as client:
        for i in range(0, len(ids), _BATCH):
            f, e = await _process_batch(client, region, ids[i:i + _BATCH])
            filled += f
            empty += e
    return (filled, empty)


async def run_forever() -> None:
    """Gotejamento contínuo: 1 lote (= 1 request ao AODP) a cada BATCH_PAUSE
    segundos. Round-robin de região a cada tick — west, east, europe, west… —
    pra as 3 regiões avançarem juntas (nenhuma passa fome no cold start). Um
    ciclo completo cobre catálogo × 3 regiões."""
    await asyncio.sleep(_STARTUP_DELAY)
    n = 0  # contador global; região = n % 3, lote = (n // 3) % n_batches
    cycle_filled = 0
    cycle_empty = 0
    async with httpx.AsyncClient(timeout=25) as client:
        while True:
            ids = [c["id"] for c in get_catalog()]
            if not ids:
                await asyncio.sleep(60)
                continue
            n_batches = max(1, math.ceil(len(ids) / _BATCH))
            region = _REGIONS[n % len(_REGIONS)]
            batch_idx = (n // len(_REGIONS)) % n_batches
            # Fim de um ciclo completo (todas as regiões percorreram o catálogo).
            if n > 0 and n % (n_batches * len(_REGIONS)) == 0:
                log.info("market_snapshot: ciclo completo (todas as regiões) — %d com dado, %d sem",
                         cycle_filled, cycle_empty)
                cycle_filled = cycle_empty = 0
            batch = ids[batch_idx * _BATCH:(batch_idx + 1) * _BATCH]
            try:
                f, e = await _process_batch(client, region, batch)
                cycle_filled += f
                cycle_empty += e
            except Exception:
                log.exception("market_snapshot: lote %s/%d falhou", region, batch_idx)
            n += 1
            await asyncio.sleep(BATCH_PAUSE)
