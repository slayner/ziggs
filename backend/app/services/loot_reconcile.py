"""Reconciliação própria: lootlog + baú + mortes → depositados / não depositados / morreu com.

Sistema próprio (baseado no SUNLootChecker): combina as COLETAS do lootlog
(submissões anônimas dos loggers, dedup canonical entre loggers) com o baú da
guilda (GuildChestEntry) e as MORTES (EventDeath) e produz 3 categorias:

  · depositados   — itens no baú
  · não depositados — looteado mas não está no baú, por jogador (ratted)
  · morreu com     — gear perdida na morte (regear silver_value) + itens
                    recuperados do cadáver (lootlog looted_from == morto)

Tudo casado por item_id (= item_type). Preços via services/loot.get_price.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas.loot import (
    ChestEntryOut, DeathLossOut, LootReconcileEventOut, LootReconcileOut,
    NotDepositedOut,
)
from app.models.events import EventDeath
from app.models.loot import GuildChestEntry
from app.models.lootlog import LootLogSubmission
from app.services import loot

# Janela de dedup: a mesma coleta vista por loggers diferentes chega com
# timestamps defasados (ms entre máquinas). 60s agrupa a mesma coleta.
# ponytail: janela fixa; se loggers capturarem em redes distintas com mais
# defasagem, subir pra 120s. (v1 usava 60s.)
_DEDUP_WINDOW_S = 60.0


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _canonical_loot(rows: list[dict]) -> list[dict]:
    """Dedup canonical: funde detecções de vários loggers em coletas únicas.
    Chave: (item_id, quantity, looted_by, looted_from) + timestamps em até
    60s. Cada cluster vira UMA coleta (a primeira ocorrência)."""
    parsed: list[tuple[datetime, dict]] = []
    for r in rows:
        dt = _parse_ts(r.get("ts"))
        if dt is not None:
            parsed.append((dt, r))
    parsed.sort(key=lambda x: x[0])

    groups: dict[tuple, list[tuple[datetime, dict]]] = defaultdict(list)
    for dt, r in parsed:
        key = (r.get("item_id"), int(r.get("quantity") or 1),
               r.get("looted_by"), r.get("looted_from"))
        groups[key].append((dt, r))

    canonical: list[dict] = []
    for key, lst in groups.items():
        lst.sort(key=lambda x: x[0])
        cluster: list[tuple[datetime, dict]] = []
        for dt, r in lst:
            if cluster and (dt - cluster[-1][0]).total_seconds() > _DEDUP_WINDOW_S:
                canonical.append(cluster[0][1])  # 1ª ocorrência do cluster
                cluster = []
            cluster.append((dt, r))
        if cluster:
            canonical.append(cluster[0][1])
    return canonical


def _price(db: Session, cache: dict[str, int], item_id: str) -> int:
    if not item_id:
        return 0
    if item_id not in cache:
        try:
            cache[item_id] = loot.get_price(db, item_id).silver_value
        except Exception:
            cache[item_id] = 0
    return cache[item_id]


def unified_reconcile(db: Session, guild_id: int, event_id: int) -> LootReconcileOut:
    # 1) loot do lootlog (submissões anônimas, dedup canonical).
    subs = db.scalars(select(LootLogSubmission).where(
        LootLogSubmission.guild_id == guild_id,
        LootLogSubmission.event_id == event_id,
    )).all()
    all_rows: list[dict] = []
    for s in subs:
        for r in (s.loot_rows or []):
            all_rows.append(r)
    loot_events = _canonical_loot(all_rows)

    # Cache de preços: semeia com unit_price já guardado no ingest p/ não
    # re-bater na API (submissões antigas sem unit_price caem p/ get_price).
    price_cache: dict[str, int] = {}
    for ev in loot_events:
        up = ev.get("unit_price")
        if up and ev.get("item_id") and ev["item_id"] not in price_cache:
            price_cache[ev["item_id"]] = up

    # 2) baú (GuildChestEntry).
    chest_rows = db.scalars(select(GuildChestEntry).where(
        GuildChestEntry.guild_id == guild_id,
        GuildChestEntry.event_id == event_id,
    )).all()

    # 3) mortes (EventDeath).
    deaths = db.scalars(select(EventDeath).where(
        EventDeath.guild_id == guild_id,
        EventDeath.event_id == event_id,
    ).order_by(EventDeath.id)).all()

    # ── depositados (baú) ─────────────────────────────────────────────────
    # Reusa o silver_value precificado no upload; só re-busca se veio 0
    # (item sem preço no cache na hora do upload).
    chest_out: list[ChestEntryOut] = []
    chest_qty_by_item: dict[str, int] = defaultdict(int)
    for c in chest_rows:
        unit = c.silver_value if c.silver_value > 0 else _price(db, price_cache, c.item_type)
        chest_qty_by_item[c.item_type] += c.quantity
        chest_out.append(ChestEntryOut(
            id=c.id, item_type=c.item_type, item_name=c.item_name,
            quantity=c.quantity, silver_value=unit,
            deposited_by_name=c.deposited_by_name, snapshot_at=c.snapshot_at,
        ))

    # ── coletas por item + por looter ──────────────────────────────────────
    looted_by_item: dict[str, dict] = {}            # item_id -> {name, total, looters:{name:qty}}
    for ev in loot_events:
        iid = ev.get("item_id")
        if not iid:
            continue
        slot = looted_by_item.setdefault(iid, {"name": ev.get("item_name") or iid,
                                               "total": 0, "looters": defaultdict(int)})
        slot["total"] += int(ev.get("quantity") or 1)
        slot["looters"][ev.get("looted_by") or "?"] += int(ev.get("quantity") or 1)

    # ── não depositados (ratted) ───────────────────────────────────────────
    not_deposited: list[NotDepositedOut] = []
    for iid, info in looted_by_item.items():
        chest_qty = chest_qty_by_item.get(iid, 0)
        missing = info["total"] - chest_qty
        if missing <= 0:
            continue  # tudo coberto pelo baú
        unit = _price(db, price_cache, iid)
        looters = [{"looted_by": name, "qty": q}
                   for name, q in sorted(info["looters"].items(), key=lambda x: -x[1])]
        not_deposited.append(NotDepositedOut(
            item_id=iid, item_name=info["name"], missing_qty=missing,
            looted_qty=info["total"], chest_qty=chest_qty,
            silver_value=unit, missing_value=missing * unit,
            looters=looters,
        ))
    not_deposited.sort(key=lambda n: -n.missing_value)

    # ── morreu com (per death) ─────────────────────────────────────────────
    # gear perdida = regear silver_value; itens recuperados do cadáver =
    # coletas do lootlog cujo looted_from casa com o nome do morto.
    by_looted_from: dict[str, list[dict]] = defaultdict(list)
    for ev in loot_events:
        lf = (ev.get("looted_from") or "").strip().lower()
        if lf:
            by_looted_from[lf].append(ev)

    death_out: list[DeathLossOut] = []
    for d in deaths:
        key = (d.display_name or "").strip().lower()
        recovered = by_looted_from.get(key, [])
        rec_items: list[dict] = []
        for ev in recovered:
            rec_items.append({
                "item_id": ev.get("item_id"), "item_name": ev.get("item_name"),
                "quantity": int(ev.get("quantity") or 1),
                "looted_by": ev.get("looted_by"),
            })
        death_out.append(DeathLossOut(
            user_id=d.user_id, display_name=d.display_name,
            silver_value=d.silver_value, notes=d.notes,
            recovered_items=rec_items,
        ))

    loot_events_out = [LootReconcileEventOut(
        item_id=ev.get("item_id") or "", item_name=ev.get("item_name") or "",
        quantity=int(ev.get("quantity") or 1), looted_by=ev.get("looted_by") or "",
        looted_from=ev.get("looted_from"),
    ) for ev in loot_events]

    total_looted_value = sum(
        (ev.get("unit_price") if ev.get("unit_price") else _price(db, price_cache, ev.get("item_id") or ""))
        * int(ev.get("quantity") or 1)
        for ev in loot_events
    )
    total_chest_value = sum(c.silver_value * c.quantity for c in chest_out)
    return LootReconcileOut(
        has_loot_log=len(loot_events) > 0,
        has_chest_log=len(chest_rows) > 0,
        has_deaths=len(deaths) > 0,
        deposited=chest_out,
        not_deposited=not_deposited,
        died_with=death_out,
        loot_events=loot_events_out,
        total_looted_value=total_looted_value,
        total_chest_value=total_chest_value,
        missing_value=sum(n.missing_value for n in not_deposited),
        total_regear_value=sum(d.silver_value for d in deaths),
    )


# ── self-check ──────────────────────────────────────────────────────────────

def _demo() -> None:
    rows = [
        {"ts": "2026-07-03T20:00:00Z", "item_id": "T4_BAG", "item_name": "Bag",
         "quantity": 1, "looted_by": "A", "looted_from": "Mob"},
        # mesma coleta, logger diferente (ts defasado) → 1 canonical
        {"ts": "2026-07-03T20:00:20Z", "item_id": "T4_BAG", "item_name": "Bag",
         "quantity": 1, "looted_by": "A", "looted_from": "Mob"},
        {"ts": "2026-07-03T20:05:00Z", "item_id": "T8_OX", "item_name": "Ox",
         "quantity": 1, "looted_by": "B", "looted_from": "VictimX"},
    ]
    canon = _canonical_loot(rows)
    assert len(canon) == 2, canon
    assert canon[0]["item_id"] == "T4_BAG" and canon[1]["looted_from"] == "VictimX"
    # janela > 60s separa clusters da mesma chave
    rows2 = [
        {"ts": "2026-07-03T20:00:00Z", "item_id": "T4_BAG", "item_name": "Bag",
         "quantity": 1, "looted_by": "A", "looted_from": "Mob"},
        {"ts": "2026-07-03T20:05:00Z", "item_id": "T4_BAG", "item_name": "Bag",
         "quantity": 1, "looted_by": "A", "looted_from": "Mob"},
    ]
    assert len(_canonical_loot(rows2)) == 2
    print("loot_reconcile self-check ok")


if __name__ == "__main__":
    _demo()