"""
Serviço de loot: armazena entradas do log de combate e do baú,
reconcilia (loot vs baú por item_type+quantidade) e busca preços.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas.loot import (
    ChestEntryIn, ChestEntryOut, ChestUpload,
    ItemPriceOut, LootEntryIn, LootEntryOut,
    LootUpload, MissingItem, ReconcileResult,
)
from app.models.loot import EventLootEntry, GuildChestEntry, ItemPriceCache

_PRICE_TTL = timedelta(hours=1)
_PRICE_API = "https://www.albion-online-data.com/api/v2/stats/prices/{}.json"
_PRICE_CITIES = "Caerleon,Bridgewatch,Fort Sterling,Lymhurst,Martlock,Thetford"


class LootServiceError(Exception):
    pass


# ---------------------------------------------------------------------------
# preços
# ---------------------------------------------------------------------------

def get_price(db: Session, item_type: str) -> ItemPriceOut:
    """Retorna preço em prata para o item, consultando cache e depois a API."""
    now = datetime.now(timezone.utc)
    cached = db.get(ItemPriceCache, item_type)

    if cached and (now - cached.fetched_at.replace(tzinfo=timezone.utc)) < _PRICE_TTL:
        return ItemPriceOut(
            item_type=item_type,
            silver_value=cached.silver_value,
            source="cache",
            fetched_at=cached.fetched_at,
        )

    price = _fetch_price_api(item_type)

    if cached:
        cached.silver_value = price
        cached.fetched_at = now
    else:
        db.add(ItemPriceCache(item_type=item_type, silver_value=price, fetched_at=now))
    db.flush()

    return ItemPriceOut(
        item_type=item_type,
        silver_value=price,
        source="api" if price > 0 else "unknown",
        fetched_at=now,
    )


def _fetch_price_api(item_type: str) -> int:
    try:
        url = _PRICE_API.format(item_type)
        r = httpx.get(url, params={"locations": _PRICE_CITIES, "qualities": "1"}, timeout=5)
        r.raise_for_status()
        data = r.json()
        prices = [
            d["sell_price_min"]
            for d in data
            if d.get("sell_price_min", 0) > 0
        ]
        return min(prices) if prices else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# upload de loot
# ---------------------------------------------------------------------------

def upload_loot(db: Session, guild_id: int, event_id: int, payload: LootUpload) -> ReconcileResult:
    if payload.replace:
        _delete_loot(db, guild_id, event_id)

    for entry in payload.entries:
        sv = entry.silver_value
        if sv == 0:
            cached = db.get(ItemPriceCache, entry.item_type)
            sv = cached.silver_value if cached else 0

        db.add(EventLootEntry(
            event_id=event_id,
            guild_id=guild_id,
            looted_by_name=entry.looted_by_name,
            looted_by_user_id=entry.looted_by_user_id,
            item_type=entry.item_type,
            item_name=entry.item_name,
            quantity=entry.quantity,
            silver_value=sv,
            source="bot_upload",
        ))

    db.flush()
    _reconcile(db, guild_id, event_id)
    return _build_result(db, guild_id, event_id)


# ---------------------------------------------------------------------------
# upload do baú
# ---------------------------------------------------------------------------

def upload_chest(db: Session, guild_id: int, event_id: int | None, payload: ChestUpload) -> ReconcileResult:
    if payload.replace and event_id is not None:
        _delete_chest(db, guild_id, event_id)

    for entry in payload.entries:
        sv = entry.silver_value
        if sv == 0:
            cached = db.get(ItemPriceCache, entry.item_type)
            sv = cached.silver_value if cached else 0

        db.add(GuildChestEntry(
            guild_id=guild_id,
            event_id=event_id,
            item_type=entry.item_type,
            item_name=entry.item_name,
            quantity=entry.quantity,
            silver_value=sv,
            deposited_by_name=entry.deposited_by_name,
            deposited_by_user_id=entry.deposited_by_user_id,
            snapshot_at=payload.snapshot_at,
        ))

    db.flush()
    if event_id is not None:
        _reconcile(db, guild_id, event_id)
    return _build_result(db, guild_id, event_id)


# ---------------------------------------------------------------------------
# reconciliação
# ---------------------------------------------------------------------------

def reconcile(db: Session, guild_id: int, event_id: int) -> ReconcileResult:
    _reconcile(db, guild_id, event_id)
    return _build_result(db, guild_id, event_id)


def _reconcile(db: Session, guild_id: int, event_id: int) -> None:
    """Marca in_chest em cada EventLootEntry comparando totais por item_type."""
    loot_rows = db.scalars(
        select(EventLootEntry).where(
            EventLootEntry.event_id == event_id,
            EventLootEntry.guild_id == guild_id,
        )
    ).all()

    chest_rows = db.scalars(
        select(GuildChestEntry).where(
            GuildChestEntry.event_id == event_id,
            GuildChestEntry.guild_id == guild_id,
        )
    ).all()

    # Total no baú por item_type
    chest_totals: dict[str, int] = defaultdict(int)
    for cr in chest_rows:
        chest_totals[cr.item_type] += cr.quantity

    # Total looteado por item_type
    loot_totals: dict[str, int] = defaultdict(int)
    for lr in loot_rows:
        loot_totals[lr.item_type] += lr.quantity

    covered = {
        item_type
        for item_type, qty in loot_totals.items()
        if chest_totals.get(item_type, 0) >= qty
    }

    for lr in loot_rows:
        lr.in_chest = lr.item_type in covered

    db.flush()


def _build_result(db: Session, guild_id: int, event_id: int | None) -> ReconcileResult:
    loot_rows = db.scalars(
        select(EventLootEntry).where(
            EventLootEntry.event_id == event_id,
            EventLootEntry.guild_id == guild_id,
        )
    ).all() if event_id is not None else []

    chest_rows = db.scalars(
        select(GuildChestEntry).where(
            GuildChestEntry.event_id == event_id,
            GuildChestEntry.guild_id == guild_id,
        )
    ).all() if event_id is not None else []

    looted_out = [
        LootEntryOut(
            id=r.id, looted_by_name=r.looted_by_name,
            looted_by_user_id=r.looted_by_user_id,
            item_type=r.item_type, item_name=r.item_name,
            quantity=r.quantity, silver_value=r.silver_value,
            in_chest=r.in_chest,
        )
        for r in loot_rows
    ]

    chest_out = [
        ChestEntryOut(
            id=r.id, item_type=r.item_type, item_name=r.item_name,
            quantity=r.quantity, silver_value=r.silver_value,
            deposited_by_name=r.deposited_by_name, snapshot_at=r.snapshot_at,
        )
        for r in chest_rows
    ]

    # Calcula itens faltando: looted mas baú < qty
    loot_totals: dict[str, dict] = {}
    for r in loot_rows:
        if r.item_type not in loot_totals:
            loot_totals[r.item_type] = {"name": r.item_name, "looted": 0, "sv": r.silver_value}
        loot_totals[r.item_type]["looted"] += r.quantity

    chest_totals: dict[str, int] = defaultdict(int)
    for r in chest_rows:
        chest_totals[r.item_type] += r.quantity

    missing_out = []
    for item_type, info in loot_totals.items():
        chest_qty = chest_totals.get(item_type, 0)
        looted_qty = info["looted"]
        if chest_qty < looted_qty:
            diff = looted_qty - chest_qty
            missing_out.append(MissingItem(
                item_type=item_type,
                item_name=info["name"],
                looted_qty=looted_qty,
                chest_qty=chest_qty,
                missing_qty=diff,
                silver_value=info["sv"],
                missing_value=diff * info["sv"],
            ))

    return ReconcileResult(
        looted=looted_out,
        chest=chest_out,
        missing=missing_out,
        total_looted_value=sum(r.silver_value * r.quantity for r in loot_rows),
        total_chest_value=sum(r.silver_value * r.quantity for r in chest_rows),
        missing_value=sum(m.missing_value for m in missing_out),
        has_loot_log=len(loot_rows) > 0,
        has_chest_log=len(chest_rows) > 0,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _delete_loot(db: Session, guild_id: int, event_id: int) -> None:
    for row in db.scalars(select(EventLootEntry).where(
        EventLootEntry.event_id == event_id,
        EventLootEntry.guild_id == guild_id,
    )).all():
        db.delete(row)
    db.flush()


def _delete_chest(db: Session, guild_id: int, event_id: int) -> None:
    for row in db.scalars(select(GuildChestEntry).where(
        GuildChestEntry.event_id == event_id,
        GuildChestEntry.guild_id == guild_id,
    )).all():
        db.delete(row)
    db.flush()
