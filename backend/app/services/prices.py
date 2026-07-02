"""
Serviço de preços históricos do Albion Online.

Fonte: west.albion-online-data.com (API pública do projeto albiondata).
Estratégia de preços:
  - Usa o endpoint /stats/history com time-scale=24 (dados diários).
  - Para cada item: média ponderada (por item_count) dos últimos HISTORY_DAYS dias,
    por cidade; depois média simples entre as cidades com dados.
  - As 5 cidades usadas: Lymhurst, Fort Sterling, Thetford, Bridgewatch, Martlock.
  - Cache em ItemPriceLatest com city=_AVG_SENTINEL, TTL de 4h.

Funções legacy (sync_prices / get_price) mantidas para compatibilidade.
"""
from __future__ import annotations

import asyncio
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as _sqlite_insert
from sqlalchemy.orm import Session

from app.models.catalog import GameRole
from app.models.prices import ItemPrice, ItemPriceLatest

# ── constantes ────────────────────────────────────────────────────────────────

_BASE_URL = "https://west.albion-online-data.com/api/v2/stats/prices"
_HISTORY_URL = "https://west.albion-online-data.com/api/v2/stats/history"
_DEFAULT_CITY = "Caerleon"
_PRICE_TTL = timedelta(hours=1)
_HISTORY_TTL = timedelta(hours=4)
_BATCH_SIZE = 50

CITIES = ["Lymhurst", "Fort Sterling", "Thetford", "Bridgewatch", "Martlock"]
HISTORY_DAYS = 7
_AVG_SENTINEL = "_5city_avg_"

VALID_SLOTS = ("offhand", "helmet", "armor", "boots", "cape", "food")

# ── helpers ───────────────────────────────────────────────────────────────────


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── API legado (uma cidade, spot price) ───────────────────────────────────────


async def _fetch_from_api(
    item_ids: list[str],
    city: str = _DEFAULT_CITY,
    quality: int = 1,
) -> list[dict[str, Any]]:
    ids_str = ",".join(item_ids)
    url = f"{_BASE_URL}/{ids_str}.json?locations={city}&qualities={quality}"
    async with httpx.AsyncClient(timeout=12) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def _upsert_latest(db: Session, data: list[dict[str, Any]], now: datetime) -> None:
    for row in data:
        if not row.get("sell_price_min"):
            continue
        item_id: str = row["item_id"]
        city: str = row["city"]
        quality: int = row["quality"]
        price: int = row["sell_price_min"]
        price_date = _parse_dt(row["sell_price_min_date"])

        db.add(ItemPrice(
            item_id=item_id, city=city, quality=quality,
            sell_price_min=price, price_date=price_date, recorded_at=now,
        ))
        existing = db.scalar(
            select(ItemPriceLatest).where(
                ItemPriceLatest.item_id == item_id,
                ItemPriceLatest.city == city,
                ItemPriceLatest.quality == quality,
            )
        )
        if existing:
            existing.sell_price_min = price
            existing.price_date = price_date
            existing.recorded_at = now
        else:
            db.add(ItemPriceLatest(
                item_id=item_id, city=city, quality=quality,
                sell_price_min=price, price_date=price_date, recorded_at=now,
            ))
    db.flush()


async def sync_prices(
    db: Session,
    item_ids: list[str],
    city: str = _DEFAULT_CITY,
    quality: int = 1,
    force: bool = False,
) -> None:
    if not item_ids:
        return
    now = datetime.now(timezone.utc)
    stale_before = now - _PRICE_TTL
    if not force:
        rows = db.scalars(
            select(ItemPriceLatest).where(
                ItemPriceLatest.item_id.in_(item_ids),
                ItemPriceLatest.city == city,
                ItemPriceLatest.quality == quality,
            )
        ).all()
        fresh = {r.item_id for r in rows if r.recorded_at >= stale_before}
        to_fetch = [i for i in item_ids if i not in fresh]
    else:
        to_fetch = list(item_ids)
    if not to_fetch:
        return
    tasks = [
        _fetch_from_api(to_fetch[i:i + _BATCH_SIZE], city, quality)
        for i in range(0, len(to_fetch), _BATCH_SIZE)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, list):
            _upsert_latest(db, result, now)


async def get_price(
    db: Session,
    item_id: str,
    city: str = _DEFAULT_CITY,
    quality: int = 1,
) -> int:
    await sync_prices(db, [item_id], city, quality)
    row = db.scalar(
        select(ItemPriceLatest).where(
            ItemPriceLatest.item_id == item_id,
            ItemPriceLatest.city == city,
            ItemPriceLatest.quality == quality,
        )
    )
    return row.sell_price_min if row else 0


# ── API 5 cidades — média histórica ──────────────────────────────────────────


async def _fetch_history(item_ids: list[str]) -> list[dict]:
    """Busca histórico diário das 5 cidades para uma lista de itens (qualidades 1-4)."""
    ids_str = ",".join(item_ids)
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{_HISTORY_URL}/{ids_str}.json",
            params={
                "locations": ",".join(CITIES),
                "qualities": "1,2,3,4",
                "time-scale": 24,
            },
        )
        resp.raise_for_status()
        return resp.json()


def _compute_5city_avg(history_data: list[dict]) -> dict[tuple[str, int], int]:
    """
    Calcula a média de preço por (item_id, quality) a partir do histórico das 5 cidades.

    Por cidade: média ponderada de avg_price pelos últimos HISTORY_DAYS dias.
    Entre cidades: média simples (cada cidade tem peso igual).
    Cidades sem dados no período são ignoradas.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    city_values: dict[tuple[str, int], list[int]] = {}

    for rec in history_data:
        iid = rec.get("item_id")
        q = rec.get("quality", 1)
        if not iid:
            continue

        recent = [
            d for d in (rec.get("data") or [])
            if d.get("avg_price") and (d.get("item_count") or 0) > 0
            and _parse_dt(d["timestamp"]) >= cutoff
        ]
        if not recent:
            continue

        total_count = sum(d["item_count"] for d in recent)
        if total_count == 0:
            continue
        weighted_avg = sum(d["avg_price"] * d["item_count"] for d in recent) / total_count
        city_values.setdefault((iid, q), []).append(int(weighted_avg))

    return {
        key: int(sum(vals) / len(vals))
        for key, vals in city_values.items()
        if vals
    }


def _upsert_avg(db: Session, item_id: str, quality: int, price: int, now: datetime) -> None:
    existing = db.scalar(
        select(ItemPriceLatest).where(
            ItemPriceLatest.item_id == item_id,
            ItemPriceLatest.city == _AVG_SENTINEL,
            ItemPriceLatest.quality == quality,
        )
    )
    if existing:
        existing.sell_price_min = price
        existing.price_date = now
        existing.recorded_at = now
    else:
        db.add(ItemPriceLatest(
            item_id=item_id, city=_AVG_SENTINEL, quality=quality,
            sell_price_min=price, price_date=now, recorded_at=now,
        ))


async def sync_5city_prices(
    db: Session,
    item_ids: list[str],
    quality: int = 1,  # kept for compat but ignored — always fetches q1-4
    force: bool = False,
) -> None:
    """Garante que todos os item_ids têm média 5 cidades recente (< _HISTORY_TTL) no banco."""
    if not item_ids:
        return

    now = datetime.now(timezone.utc)
    stale_before = now - _HISTORY_TTL

    if not force:
        # use quality=1 as proxy: if q1 is fresh, all qualities were fetched together
        rows = db.scalars(
            select(ItemPriceLatest).where(
                ItemPriceLatest.item_id.in_(item_ids),
                ItemPriceLatest.city == _AVG_SENTINEL,
                ItemPriceLatest.quality == 1,
            )
        ).all()
        fresh = {r.item_id for r in rows if r.recorded_at >= stale_before}
        to_fetch = [i for i in item_ids if i not in fresh]
    else:
        to_fetch = list(item_ids)

    if not to_fetch:
        return

    tasks = [
        _fetch_history(to_fetch[i:i + _BATCH_SIZE])
        for i in range(0, len(to_fetch), _BATCH_SIZE)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if not isinstance(result, list):
            continue
        avgs = _compute_5city_avg(result)
        for (iid, q), price in avgs.items():
            _upsert_avg(db, iid, q, price, now)

    db.flush()


_BATTLE_SENTINEL = "_battle_spot_"


async def _fetch_spot_prices(item_ids: list[str]) -> list[dict[str, Any]]:
    """Preço atual (não histórico) — aceita id com @enchant direto, diferente
    de /stats/history (que devolve [] pra qualquer id com @enchant)."""
    ids_str = ",".join(item_ids)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_BASE_URL}/{ids_str}.json",
            params={"locations": ",".join(CITIES), "qualities": "1,2,3,4"},
        )
        resp.raise_for_status()
        return resp.json()


async def get_battle_prices(db: Session, item_ids: list[str]) -> dict[str, int]:
    """Preço de loot de batalha — cache permanente, sem TTL.

    Diferente do resto desse módulo (que mantém o preço fresco pra cálculo de
    regear/comp), aqui uma vez buscado o item NUNCA é re-consultado na API
    externa: pedido explícito, "o preço da época" já é o suficiente e o
    objetivo é só evitar bater na API a cada vez que alguém abre a batalha.

    Usa o endpoint de preço atual (não o de histórico) porque itens de loot
    quase sempre vêm com @enchant no id, e só o endpoint de preço atual aceita
    isso — sentinela de cidade própria (_BATTLE_SENTINEL) pra não misturar
    com o cache de média histórica 5 cidades usado pelo regear/comps.
    """
    unique = list(dict.fromkeys(item_ids))
    if not unique:
        return {}
    rows = db.scalars(
        select(ItemPriceLatest).where(
            ItemPriceLatest.item_id.in_(unique),
            ItemPriceLatest.city == _BATTLE_SENTINEL,
        )
    ).all()
    cached = {r.item_id: r.sell_price_min for r in rows}
    missing = [i for i in unique if i not in cached]
    if missing:
        now = datetime.now(timezone.utc)
        tasks = [_fetch_spot_prices(missing[i:i + _BATCH_SIZE]) for i in range(0, len(missing), _BATCH_SIZE)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        by_item: dict[str, list[int]] = {}
        for result in results:
            if not isinstance(result, list):
                continue
            for row in result:
                if not row.get("sell_price_min"):
                    continue
                by_item.setdefault(row["item_id"], []).append(row["sell_price_min"])
        rows = []
        for item_id in missing:
            vals = by_item.get(item_id, [])
            # Mediana, não média — item ilíquido pode ter só 1-2 listagens
            # reais entre as ~28 combinações (cidade × qualidade) e o resto
            # zero/ausente; uma única listagem "troll" (vendedor sem
            # concorrência pedindo um preço absurdo) já bastava pra puxar a
            # MÉDIA pra um valor completamente fora da realidade — e como
            # esse cache é permanente (nunca reconsultado), ficava errado
            # pra sempre. Mediana ignora esse tipo de outlier isolado.
            price = round(statistics.median(vals)) if vals else 0
            cached[item_id] = price
            rows.append({"item_id": item_id, "city": _BATTLE_SENTINEL, "quality": 1,
                         "sell_price_min": price, "price_date": now, "recorded_at": now})
        if rows:
            db.execute(_sqlite_insert(ItemPriceLatest).on_conflict_do_nothing(), rows)
            db.commit()
    return cached


async def get_5city_avg_price(db: Session, item_id: str, quality: int = 1) -> int:
    """Retorna a média histórica 5 cidades para um item (busca se o cache estiver expirado)."""
    await sync_5city_prices(db, [item_id], quality)
    row = db.scalar(
        select(ItemPriceLatest).where(
            ItemPriceLatest.item_id == item_id,
            ItemPriceLatest.city == _AVG_SENTINEL,
            ItemPriceLatest.quality == quality,
        )
    )
    return row.sell_price_min if row else 0


# ── calculadora de regear ─────────────────────────────────────────────────────


class RegearItemEstimate:
    def __init__(
        self,
        slot: str,
        item_id: str,
        name: str,
        quality: int,
        quantity: int,
        unit_price: int,
    ) -> None:
        self.slot = slot
        self.item_id = item_id
        self.name = name
        self.quality = quality
        self.quantity = quantity
        self.unit_price = unit_price
        self.total_price = unit_price * quantity


class RegearEstimate:
    def __init__(
        self,
        participant_id: int,
        user_name: str | None,
        game_role_id: int | None,
        game_role_name: str | None,
        items: list[RegearItemEstimate],
    ) -> None:
        self.participant_id = participant_id
        self.user_name = user_name
        self.game_role_id = game_role_id
        self.game_role_name = game_role_name
        self.items = items
        self.total = sum(i.total_price for i in items)
        self.price_basis = f"Média histórica 5 cidades ({HISTORY_DAYS}d)"
        self.calculated_at = datetime.now(timezone.utc).isoformat()


async def estimate_regear(
    db: Session,
    participant_id: int,
    guild_id: int,
    event_id: int,
) -> RegearEstimate:
    """
    Calcula o regear de um participante com base na sua função no evento.
    Preços: média histórica dos últimos 7 dias nas 5 principais cidades.
    """
    from app.models.events import EventParticipant

    participant = db.get(EventParticipant, participant_id)
    if participant is None or participant.event_id != event_id or participant.guild_id != guild_id:
        raise ValueError("participante não encontrado")

    base = RegearEstimate(
        participant_id=participant_id,
        user_name=participant.user_name,
        game_role_id=participant.game_role_id,
        game_role_name=None,
        items=[],
    )

    if participant.game_role_id is None:
        return base

    role = db.get(GameRole, participant.game_role_id)
    if role is None:
        return base

    base.game_role_name = role.name
    build_items: list[dict] = role.build_items or []

    item_ids = [
        bi["item_id"] for bi in build_items
        if bi.get("item_id") and bi.get("slot") in VALID_SLOTS
    ]

    if item_ids:
        await sync_5city_prices(db, item_ids)

    results: list[RegearItemEstimate] = []
    for bi in build_items:
        iid = bi.get("item_id")
        slot = bi.get("slot", "")
        if not iid or slot not in VALID_SLOTS:
            continue

        quality = int(bi.get("quality", 1))
        quantity = int(bi.get("quantity", 1))

        row = db.scalar(
            select(ItemPriceLatest).where(
                ItemPriceLatest.item_id == iid,
                ItemPriceLatest.city == _AVG_SENTINEL,
                ItemPriceLatest.quality == quality,
            )
        )
        unit_price = row.sell_price_min if row else 0
        results.append(RegearItemEstimate(
            slot=slot,
            item_id=iid,
            name=bi.get("name", iid),
            quality=quality,
            quantity=quantity,
            unit_price=unit_price,
        ))

    base.items = results
    base.total = sum(i.total_price for i in results)
    return base
