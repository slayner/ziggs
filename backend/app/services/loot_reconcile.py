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
    NotDepositedOut, ReconcileLooter, ReconcileLooterItem,
)
from app.models.events import EventDeath
from app.models.events import Event
from app.models.loot import GuildChestEntry, LootVerification
from app.models.lootlog import LootLogSubmission
from app.services import loot
from app.services.lootlog import _event_window

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


def _is_trash(ev: dict) -> bool:
    """Loot vendor 'Trash' do jogo — ignorado no reconcile (não é gear, não vai
    pro baú). item_id do tipo T?_TRASH_* ou item_name literal 'Trash'."""
    if "TRASH" in (ev.get("item_id") or "").upper():
        return True
    return (ev.get("item_name") or "").strip().lower() == "trash"


def _per_looter(
    db: Session, price_cache: dict[str, int], loot_events: list[dict],
    chest_rows, verified: set[tuple[str, str]],
) -> list[ReconcileLooter]:
    """Timeline por looter: cada item que ele pegou vira 'died' (morreu
    carregando → cinza, desconsiderado), 'deposited' (chegou no baú → verde) ou
    'missing' (sobreviveu e trouxe pra cidade mas não depositou → vermelho, ou
    amarelo se conferido).

    Morte = alguém looteou o CADÁVER dele (uma coleta com looted_from == looter).
    Regra: um item pego em ts_p está perdido se existe uma morte dele em ts_d >=
    ts_p (estava no inventário na hora da morte). Coletas depois da última morte
    (ou de quem nunca morreu) = carregadas pra cidade. Depósito abate primeiro o
    devido (missing), depois explica o morto — assim loot depositado numa volta
    anterior não vira 'roubado' nem 'morto' por engano.

    (Já houve uma "Phase 2" com marcadores de cidade do companion partindo a
    sessão em viagens — ABANDONADA em 19/07/2026: entrar na cidade não implica
    ter depositado, então quem só passasse por ela carregando o loot e
    morresse depois seria COBRADO por itens que morreram junto. A regra v1 é a
    conservadora certa. Não reintroduza sem resolver isso.)"""
    # mortes por vítima (nome lower → lista de datetimes)
    deaths_by_victim: dict[str, list[datetime]] = defaultdict(list)
    for ev in loot_events:
        vic = (ev.get("looted_from") or "").strip().lower()
        dt = _parse_ts(ev.get("ts"))
        if vic and dt is not None:
            deaths_by_victim[vic].append(dt)

    # coletas por (looter_lower, item) → carregado vs morto
    agg: dict[tuple[str, str], dict] = {}
    for ev in loot_events:
        looter = ev.get("looted_by") or ""
        iid = ev.get("item_id")
        if not looter or not iid:
            continue
        lk = looter.strip().lower()
        qty = int(ev.get("quantity") or 1)
        dt = _parse_ts(ev.get("ts"))
        deaths = deaths_by_victim.get(lk, [])
        # morreu carregando: morte em ts >= pickup. Sem ts legível mas com morte
        # registrada → conservador (marca morto, não acusa de roubo).
        died = (dt is None and bool(deaths)) or (dt is not None and any(d >= dt for d in deaths))
        slot = agg.setdefault((lk, iid), {"name": looter, "item_name": ev.get("item_name") or iid,
                                          "carried": 0, "died": 0})
        slot["died" if died else "carried"] += qty

    # depósitos por (depositor_lower, item)
    dep_by: dict[tuple[str, str], int] = defaultdict(int)
    for c in chest_rows:
        who = (c.deposited_by_name or "").strip().lower()
        if who:
            dep_by[(who, c.item_type)] += c.quantity

    order = {"missing": 0, "deposited": 1, "died": 2}
    by_looter: dict[str, dict] = {}
    for (lk, iid), slot in agg.items():
        carried, died = slot["carried"], slot["died"]
        dep = dep_by.get((lk, iid), 0)
        missing = max(0, carried - dep)
        dep_left = max(0, dep - carried)          # depósito além do carregado
        died_excl = max(0, died - dep_left)        # morto não explicado por depósito
        deposited_shown = min(dep, carried + died)  # cap no total looteado
        unit = _price(db, price_cache, iid)
        entry = by_looter.setdefault(lk, {"name": slot["name"], "items": []})
        for status, qty in (("missing", missing), ("deposited", deposited_shown), ("died", died_excl)):
            if qty > 0:
                entry["items"].append(ReconcileLooterItem(
                    item_id=iid, item_name=slot["item_name"], status=status,
                    quantity=qty, silver_value=unit, value=qty * unit,
                    verified=(status == "missing" and (lk, iid) in verified),
                ))

    out: list[ReconcileLooter] = []
    clean_names: set[str] = set()  # looters 100% depositados → vão pro fim da lista
    for info in by_looter.values():
        items = info["items"]
        items.sort(key=lambda it: (order.get(it.status, 9), -it.value))
        miss = [it for it in items if it.status == "missing"]
        if all(it.status == "deposited" for it in items):
            clean_names.add(info["name"])
        out.append(ReconcileLooter(
            looted_by=info["name"],
            missing_qty=sum(it.quantity for it in miss),
            missing_value=sum(it.value for it in miss),
            items=items,
        ))
    # devedores primeiro (maior valor), depois quem só morreu/misto, e por último
    # quem depositou tudo (limpo) — mas ainda visível.
    out.sort(key=lambda l: (l.looted_by in clean_names, -l.missing_value))
    return out


def toggle_verification(
    db: Session, guild_id: int, event_id: int,
    looted_by: str, item_id: str, user_id: int | None,
) -> bool:
    """Marca/desmarca (toggle) um item 'não depositado' como conferido. Retorna o
    novo estado (True = agora conferido)."""
    existing = db.scalar(select(LootVerification).where(
        LootVerification.guild_id == guild_id,
        LootVerification.event_id == event_id,
        LootVerification.looted_by == looted_by,
        LootVerification.item_id == item_id,
    ))
    if existing is not None:
        db.delete(existing)
        db.commit()
        return False
    db.add(LootVerification(
        guild_id=guild_id, event_id=event_id, looted_by=looted_by,
        item_id=item_id, verified_by_user_id=user_id,
    ))
    db.commit()
    return True


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
    # Janela do evento: started_at - 5min … ended_at + 15min (mesmo critério do
    # compute_logger_weights). Coletas fora desta janela são desconsideradas
    # completamente — não aparecem na reconciliação nem contam como loot.
    ev = db.get(Event, event_id)
    win_start, win_end = _event_window(ev) if ev is not None else (None, None)

    # 1) loot do lootlog (submissões anônimas, dedup canonical).
    subs = db.scalars(select(LootLogSubmission).where(
        LootLogSubmission.guild_id == guild_id,
        LootLogSubmission.event_id == event_id,
    )).all()
    all_rows: list[dict] = []
    for s in subs:
        for r in (s.loot_rows or []):
            dt = _parse_ts(r.get("ts"))
            if dt is not None:
                if win_start is not None and dt < win_start:
                    continue
                if win_end is not None and dt > win_end:
                    continue
            all_rows.append(r)
    # Ignora 'Trash' (loot de vendor, não vai pro baú) antes de tudo.
    loot_events = [e for e in _canonical_loot(all_rows) if not _is_trash(e)]

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

    # 4) conferências (itens 'não depositado' já checados por admin → amarelo).
    verifs = db.scalars(select(LootVerification).where(
        LootVerification.guild_id == guild_id,
        LootVerification.event_id == event_id,
    )).all()
    verified = {((v.looted_by or "").strip().lower(), v.item_id) for v in verifs}

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

    # Visão por jogador (foco no looter): timeline morte/depósito/roubo.
    looters = _per_looter(db, price_cache, loot_events, chest_rows, verified)

    return LootReconcileOut(
        has_loot_log=len(loot_events) > 0,
        has_chest_log=len(chest_rows) > 0,
        has_deaths=len(deaths) > 0,
        deposited=chest_out,
        not_deposited=not_deposited,
        looters=looters,
        died_with=death_out,
        loot_events=loot_events_out,
        total_looted_value=total_looted_value,
        total_chest_value=total_chest_value,
        # 'devido' agora sai da timeline por looter (sobreviveu e não depositou),
        # não do looted-menos-baú por item — mortes deixam de contar como roubo.
        missing_value=sum(l.missing_value for l in looters),
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

    # ── timeline por looter (died / deposited / missing) ────────────────────
    class _Chest:
        def __init__(self, who, item, qty):
            self.deposited_by_name, self.item_type, self.quantity = who, item, qty

    def _ev(ts, item, qty, by, frm=None):
        return {"ts": ts, "item_id": item, "item_name": item, "quantity": qty,
                "looted_by": by, "looted_from": frm}

    # Bob: pega Espada 20:01, MORRE 20:04 (corpo looteado por Zed), pega Machado
    #      20:10 e sobrevive. Espada = died (cinza); Machado = missing (roubo).
    evs = [
        _ev("2026-07-03T20:01:00Z", "SWORD", 1, "Bob", "Mob"),
        _ev("2026-07-03T20:04:00Z", "BOB_GEAR", 1, "Zed", "Bob"),   # morte do Bob
        _ev("2026-07-03T20:10:00Z", "AXE", 1, "Bob", "Mob"),
        # Ann pega Elmo e deposita → deposited (verde), sem morte.
        _ev("2026-07-03T20:02:00Z", "HELM", 2, "Ann", "Mob"),
    ]
    looters = _per_looter(None, {"SWORD": 0, "AXE": 0, "HELM": 0, "BOB_GEAR": 0},
                          evs, [_Chest("Ann", "HELM", 2)], set())
    bob = next(l for l in looters if l.looted_by == "Bob")
    st = {it.item_id: it.status for it in bob.items}
    assert st["SWORD"] == "died" and st["AXE"] == "missing", st
    assert bob.missing_qty == 1
    # Ann depositou tudo → aparece, mas no FIM da lista (Bob deve → vem antes).
    assert looters[-1].looted_by == "Ann" and looters[0].looted_by == "Bob", looters
    ann = next(l for l in looters if l.looted_by == "Ann")
    assert all(it.status == "deposited" for it in ann.items) and ann.missing_qty == 0

    # verified: marca o Machado do Bob → verified=True no item missing.
    looters2 = _per_looter(None, {"AXE": 0, "SWORD": 0, "BOB_GEAR": 0}, evs, [],
                           {("bob", "AXE")})
    bob2 = next(l for l in looters2 if l.looted_by == "Bob")
    axe = next(it for it in bob2.items if it.item_id == "AXE")
    assert axe.status == "missing" and axe.verified, axe

    # Trash é ignorado.
    assert _is_trash({"item_id": "T4_TRASH_HIDE", "item_name": "x"})
    assert _is_trash({"item_id": "", "item_name": "Trash"})
    assert not _is_trash({"item_id": "T4_BAG", "item_name": "Bag"})

    print("loot_reconcile self-check ok")


if __name__ == "__main__":
    _demo()