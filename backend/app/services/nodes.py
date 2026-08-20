"""Serviço de nodes — fonte da verdade do calendário de nodes (substitui o
`bot/database.py` `node_*` do bot-v1). O bot-v2 renderiza o calendário e faz
proxy via `/bot/guilds/{g}/nodes/*`; o site expõe `/guilds/{g}/nodes/*`.

Nodes vivos (`NodeEvent`) são podados quando `spawn_at` passa de 1h. Toda
adição vai também pro `NodeEventLog` (auditoria permanente + base p/ scout
payment e `near_cta`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.domain.blackzone_maps import BLACKZONE_MAPS
from app.models.audit import AuditLog
from app.models.nodes import (
    NodeCalendar, NodeDef, NodeEvent, NodeEventLog, NodeMap, NodeMapExclusion,
)
from sqlalchemy.orm import Session

# Janela "nodes próximos do CTA" (±30min) — espelho do bot-v1
# (NODES_NEAR_THRESHOLD_SECONDS).
NEAR_CTA_WINDOW_SECONDS = 1800

# Nodes vivos somem do calendário 1h depois do spawn.
LIVE_TTL = timedelta(hours=1)

# Bloqueia adicionar o mesmo tipo+mapa dentro de 2h (espelho do bot-v1
# `find_duplicate_node`).
DUPLICATE_WINDOW = timedelta(hours=2)


class ServiceError(Exception):
    """Erro de regra de negócio (vira 400 na rota)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# defs (tipo/emoji/peso)
# ---------------------------------------------------------------------------

def list_defs(db: Session, guild_id: int) -> list[NodeDef]:
    # Ordem alfabética pelo nome — a UI não tem mais "Order" (sort) e lista em
    # ordem alfabética; o frontend reordena pelo nome localizado pelo idioma.
    return list(db.scalars(
        select(NodeDef).where(NodeDef.guild_id == guild_id).order_by(NodeDef.name)
    ))


def upsert_def(db: Session, guild_id: int, name: str, emoji: str | None,
               weight: float, sort: int) -> NodeDef:
    existing = db.scalar(select(NodeDef).where(
        NodeDef.guild_id == guild_id, NodeDef.name == name
    ))
    if existing:
        existing.emoji = emoji
        existing.weight = weight
        existing.sort = sort
        return existing
    row = NodeDef(guild_id=guild_id, name=name, emoji=emoji, weight=weight, sort=sort)
    db.add(row)
    db.flush()
    return row


def update_def(db: Session, guild_id: int, def_id: int, *,
               name: str | None = None, emoji: str | None = None,
               weight: float | None = None) -> NodeDef | None:
    """Edita um def já criado pelo id (não pelo nome) — permite renomear sem
    criar linha nova. name/emoji/weight None = não mexer."""
    row = db.scalar(select(NodeDef).where(
        NodeDef.id == def_id, NodeDef.guild_id == guild_id
    ))
    if row is None:
        return None
    if name is not None and name.strip():
        new_name = name.strip()
        # Unique (guild_id, name): renomear pra um nome que já existe em outra
        # linha vira IntegrityError 500 — captura aqui e devolve 400 limpo.
        if new_name != row.name:
            clash = db.scalar(select(NodeDef).where(
                NodeDef.guild_id == guild_id, NodeDef.name == new_name
            ))
            if clash is not None:
                raise ServiceError("já existe um tipo de node com esse nome")
            row.name = new_name
    if emoji is not None:
        row.emoji = emoji.strip() or None
    if weight is not None:
        row.weight = weight
    db.flush()
    return row


def remove_def(db: Session, guild_id: int, name: str) -> bool:
    res = db.execute(delete(NodeDef).where(
        NodeDef.guild_id == guild_id, NodeDef.name == name
    ))
    return res.rowcount > 0


# ---------------------------------------------------------------------------
# maps (efetivos = BZ embutida - exclusões + extras)
# ---------------------------------------------------------------------------

def effective_maps(db: Session, guild_id: int) -> list[str]:
    excluded = set(db.scalars(
        select(NodeMapExclusion.map_name).where(NodeMapExclusion.guild_id == guild_id)
    ))
    extras = list(db.scalars(
        select(NodeMap.map_name).where(NodeMap.guild_id == guild_id).order_by(NodeMap.map_name)
    ))
    base = [m for m in BLACKZONE_MAPS if m not in excluded]
    return sorted(set(base) | set(extras))


def add_map(db: Session, guild_id: int, map_name: str) -> None:
    """Adiciona um extra OU des-oculta um mapa embutido (remove a exclusão)."""
    map_name = map_name.strip()
    if not map_name:
        raise ServiceError("nome do mapa vazio")
    # Se era excluído da BZ, des-oculta.
    db.execute(delete(NodeMapExclusion).where(
        NodeMapExclusion.guild_id == guild_id, NodeMapExclusion.map_name == map_name
    ))
    if map_name not in BLACKZONE_MAPS:
        existing = db.scalar(select(NodeMap).where(
            NodeMap.guild_id == guild_id, NodeMap.map_name == map_name
        ))
        if not existing:
            db.add(NodeMap(guild_id=guild_id, map_name=map_name))


def remove_map(db: Session, guild_id: int, map_name: str) -> None:
    """Remove um extra OU oculta um mapa embutido (vira exclusão)."""
    map_name = map_name.strip()
    # Remove extra custom se existir.
    db.execute(delete(NodeMap).where(
        NodeMap.guild_id == guild_id, NodeMap.map_name == map_name
    ))
    if map_name in BLACKZONE_MAPS:
        existing = db.scalar(select(NodeMapExclusion).where(
            NodeMapExclusion.guild_id == guild_id, NodeMapExclusion.map_name == map_name
        ))
        if not existing:
            db.add(NodeMapExclusion(guild_id=guild_id, map_name=map_name))


def list_maps(db: Session, guild_id: int) -> dict:
    """Retorna extras, exclusões e a BZ embutida — p/ o dashboard mostrar tudo."""
    extras = list(db.scalars(
        select(NodeMap.map_name).where(NodeMap.guild_id == guild_id).order_by(NodeMap.map_name)
    ))
    exclusions = list(db.scalars(
        select(NodeMapExclusion.map_name).where(NodeMapExclusion.guild_id == guild_id).order_by(NodeMapExclusion.map_name)
    ))
    return {"extras": extras, "exclusions": exclusions, "builtin": list(BLACKZONE_MAPS)}


# ---------------------------------------------------------------------------
# events (vivos) + log
# ---------------------------------------------------------------------------

def prune_expired(db: Session, guild_id: int) -> int:
    cutoff = _now() - LIVE_TTL
    res = db.execute(delete(NodeEvent).where(
        NodeEvent.guild_id == guild_id, NodeEvent.spawn_at < cutoff
    ))
    return res.rowcount


def list_events(db: Session, guild_id: int) -> list[NodeEvent]:
    prune_expired(db, guild_id)
    return list(db.scalars(
        select(NodeEvent).where(NodeEvent.guild_id == guild_id).order_by(NodeEvent.spawn_at)
    ))


def find_duplicate(db: Session, guild_id: int, node_type: str,
                   map_name: str, spawn_at: datetime) -> NodeEvent | None:
    """Mesmo tipo+mapa dentro de 2h do spawn proposto → bloqueia."""
    spawn_at = _ensure_utc(spawn_at)
    lo, hi = spawn_at - DUPLICATE_WINDOW, spawn_at + DUPLICATE_WINDOW
    return db.scalar(select(NodeEvent).where(
        NodeEvent.guild_id == guild_id,
        NodeEvent.node_type == node_type,
        NodeEvent.map_name == map_name,
        NodeEvent.spawn_at.between(lo, hi),
    ))


def add_event(db: Session, guild_id: int, node_type: str, map_name: str,
              spawn_at: datetime, *, channel_id: int | None = None,
              added_by_id: int | None = None, added_by_name: str | None = None,
              allow_duplicate: bool = False) -> NodeEvent:
    if not node_type or not map_name:
        raise ServiceError("tipo e mapa são obrigatórios")
    spawn_at = _ensure_utc(spawn_at)
    if not allow_duplicate and find_duplicate(db, guild_id, node_type, map_name, spawn_at):
        raise ServiceError("node duplicado (mesmo tipo+mapa dentro de 2h)")
    row = NodeEvent(
        guild_id=guild_id, channel_id=channel_id, node_type=node_type,
        map_name=map_name, spawn_at=spawn_at,
        added_by_id=added_by_id, added_by_name=added_by_name,
    )
    db.add(row)
    db.flush()
    # Auditoria permanente — scout = quem adicionou (p/ pagamento de scout).
    log_row = NodeEventLog(
        guild_id=guild_id, node_type=node_type, map_name=map_name,
        spawn_at=spawn_at, scout_id=added_by_id, scout_name=added_by_name,
    )
    db.add(log_row)
    db.flush()
    db.add(AuditLog(
        guild_id=guild_id, actor_id=added_by_id, actor_type="bot", source="bot",
        action="node.add", entity="node_event", entity_id=str(row.id),
        after={"node_type": node_type, "map_name": map_name,
               "spawn_at": spawn_at.isoformat(), "scout_name": added_by_name},
        note=f"log #{log_row.id}",
    ))
    return row


def delete_event(db: Session, guild_id: int, event_id: int,
                 *, actor_id: int | None = None, actor_source: str = "bot") -> bool:
    # Snapshot antes de deletar (NodeEvent perde após commit).
    row = db.scalar(select(NodeEvent).where(
        NodeEvent.id == event_id, NodeEvent.guild_id == guild_id
    ))
    res = db.execute(delete(NodeEvent).where(
        NodeEvent.id == event_id, NodeEvent.guild_id == guild_id
    ))
    if res.rowcount > 0 and row is not None:
        db.add(AuditLog(
            guild_id=guild_id, actor_id=actor_id, actor_type=actor_source, source=actor_source,
            action="node.delete", entity="node_event", entity_id=str(event_id),
            before={"node_type": row.node_type, "map_name": row.map_name,
                    "spawn_at": row.spawn_at.isoformat() if row.spawn_at else None},
        ))
    return res.rowcount > 0


def removable_events(db: Session, guild_id: int, *, staff: bool,
                     user_id: int) -> list[NodeEvent]:
    """Staff vê todos; membro comum só vê os que ele adicionou (espelho do
    `get_removable_nodes` do bot-v1)."""
    prune_expired(db, guild_id)
    q = select(NodeEvent).where(NodeEvent.guild_id == guild_id).order_by(NodeEvent.spawn_at)
    if not staff:
        q = q.where(NodeEvent.added_by_id == user_id)
    return list(db.scalars(q))


def near_cta(db: Session, guild_id: int, ts: datetime,
             window_seconds: int = NEAR_CTA_WINDOW_SECONDS) -> list[NodeEventLog]:
    """Nodes do log cujo spawn cai dentro de ±window_seconds de `ts` — espelho
    do `get_nodes_near` do bot-v1. Usado pelo embed de evento (definition/
    verification) e pelo dashboard."""
    ts = _ensure_utc(ts)
    lo = ts - timedelta(seconds=window_seconds)
    hi = ts + timedelta(seconds=window_seconds)
    return list(db.scalars(
        select(NodeEventLog).where(
            NodeEventLog.guild_id == guild_id,
            NodeEventLog.spawn_at.between(lo, hi),
        ).order_by(NodeEventLog.spawn_at)
    ))


def list_log(db: Session, guild_id: int, limit: int = 200) -> list[NodeEventLog]:
    return list(db.scalars(
        select(NodeEventLog).where(NodeEventLog.guild_id == guild_id)
        .order_by(NodeEventLog.spawn_at.desc()).limit(limit)
    ))


# ---------------------------------------------------------------------------
# captura de node em review (scout payout)
# ---------------------------------------------------------------------------

def captured_for_event(db: Session, guild_id: int, event_id: int) -> list[NodeEventLog]:
    """Nodes marcados como capturados e vinculados a este evento — base do
    scout payout no _calc_payout."""
    return list(db.scalars(
        select(NodeEventLog).where(
            NodeEventLog.guild_id == guild_id,
            NodeEventLog.event_id == event_id,
            NodeEventLog.captured.is_(True),
        ).order_by(NodeEventLog.spawn_at)
    ))


def weight_for(db: Session, guild_id: int, node_type: str) -> float:
    """Peso do scout para o tipo de node (NodeDef.weight). Default 1.0 se o
    tipo não estiver cadastrado — significa 100% do valor vendido pro scout."""
    row = db.scalar(select(NodeDef).where(
        NodeDef.guild_id == guild_id, NodeDef.name == node_type
    ))
    return float(row.weight) if row is not None else 1.0


def claim_node(
    db: Session, guild_id: int, event_id: int, node_log_id: int,
    captured: bool, sold_value: int, actor_id: int | None,
) -> NodeEventLog:
    """Marca um NodeEventLog como capturado (ou não) pelo evento e grava o
    valor vendido. Vincula o log ao evento (event_id) na primeira captura —
    é o que liga o node ao evento pro scout payout."""
    if sold_value < 0:
        raise ServiceError("valor vendido não pode ser negativo")
    from app.models.events import Event
    from app.services.events import _mark_dirty, _require_mutable
    ev = db.scalar(select(Event).where(Event.id == event_id, Event.guild_id == guild_id))
    if ev is None:
        raise ServiceError("evento não encontrado")
    _require_mutable(ev)
    row = db.scalar(select(NodeEventLog).where(
        NodeEventLog.id == node_log_id, NodeEventLog.guild_id == guild_id
    ))
    if row is None:
        raise ServiceError("node não encontrado no log")
    row.event_id = event_id if captured else None
    row.captured = captured
    row.sold_value = int(sold_value) if captured else 0
    _mark_dirty(ev)
    db.flush()
    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type="bot", source="bot",
        action="node.claim", entity="node_event_log", entity_id=str(node_log_id),
        after={"event_id": event_id, "captured": captured, "sold_value": int(sold_value)},
        note=f"node {row.node_type} @ {row.map_name}",
    ))
    return row


# ---------------------------------------------------------------------------
# calendar (singleton por guilda)
# ---------------------------------------------------------------------------

def get_calendar(db: Session, guild_id: int) -> NodeCalendar | None:
    return db.get(NodeCalendar, guild_id)


def set_calendar(db: Session, guild_id: int, *, channel_id: int | None,
                 message_id: int | None = None) -> NodeCalendar:
    row = db.get(NodeCalendar, guild_id)
    if row is None:
        row = NodeCalendar(guild_id=guild_id, channel_id=channel_id, message_id=message_id)
        db.add(row)
    else:
        if channel_id is not None:
            row.channel_id = channel_id
        if message_id is not None:
            row.message_id = message_id
    return row


# ---------------------------------------------------------------------------
# self-check (ponytail: a janela ±1800s é o contrato do near_cta)
# ---------------------------------------------------------------------------

def _self_check() -> None:  # pragma: no cover
    from datetime import datetime as _dt
    base = _dt(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    # janela simétrica
    assert abs((base - (base - timedelta(seconds=NEAR_CTA_WINDOW_SECONDS))).total_seconds()) == NEAR_CTA_WINDOW_SECONDS
    # base_percent/percent clamp seria do voice — aqui só valida a janela


if __name__ == "__main__":  # pragma: no cover
    _self_check()
    print("nodes self-check ok")
