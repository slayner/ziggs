"""Perfis públicos de guildas e alianças — sem autenticação, sem escopo de guild."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import deps
from app.api.routes.battles import _as_builds, _classify_role, _factions_summary, _weapon_function_map, _wbase
from app.models.battles import Battle, BattleGuild, BattleParticipant, BattleSide
from app.models.players import AlbionPlayer, DeletedProfile, PlayerKillEvent, SearchEntry
from app.services import battle_groups
from app.services.search_norm import match as search_match, normalize as norm_name, prefix_range

router = APIRouter(prefix="/public", tags=["profiles"])


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _deleted_set(db: Session, entity_type: str, ids: list[str]) -> set[str]:
    """Retorna conjunto de albion_ids marcados como excluídos."""
    if not ids:
        return set()
    return set(db.scalars(
        select(DeletedProfile.albion_id)
        .where(DeletedProfile.entity_type == entity_type, DeletedProfile.albion_id.in_(ids))
    ).all())


def _cutoffs() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now - timedelta(days=7), now - timedelta(days=30)


def _split_windows(rows: list[tuple], cutoff_7d: datetime, cutoff_30d: datetime) -> dict:
    """Agrega lista de (timestamp, valor) nas três janelas temporais."""
    result = {"7d": 0, "30d": 0, "all": 0}
    for ts, val in rows:
        v = int(val or 0)
        result["all"] += v
        t = _aware(ts)
        if t >= cutoff_30d:
            result["30d"] += v
        if t >= cutoff_7d:
            result["7d"] += v
    return result


# ---------------------------------------------------------------------------
# Helpers de agregação — cada função aceita guild_id XOR alliance_id
# ---------------------------------------------------------------------------

_LETHAL_BIG = (Battle.players_total > 20, Battle.is_lethal == True)


def _fame_windows(db: Session, c7: datetime, c30: datetime, *, guild_id=None, alliance_id=None) -> dict:
    """Kill fame somado por janela — só batalhas letais com mais de 20 jogadores."""
    if guild_id:
        rows = db.execute(
            select(Battle.start_time, BattleGuild.kill_fame)
            .join(BattleGuild, BattleGuild.battle_id == Battle.id)
            .where(BattleGuild.albion_guild_id == guild_id, *_LETHAL_BIG)
        ).all()
    else:
        rows = db.execute(
            select(Battle.start_time, func.sum(BattleGuild.kill_fame))
            .join(BattleGuild, BattleGuild.battle_id == Battle.id)
            .where(BattleGuild.alliance_id == alliance_id, *_LETHAL_BIG)
            .group_by(Battle.id, Battle.start_time)
        ).all()
    return _split_windows(rows, c7, c30)


def _silver_windows(db: Session, c7: datetime, c30: datetime, *, guild_id=None, alliance_id=None) -> dict:
    """Fama dropada (earned by enemies killing this guild/alliance members)."""
    if guild_id:
        victim_filter = PlayerKillEvent.victim_guild_id == guild_id
    else:
        guild_ids = db.scalars(
            select(BattleGuild.albion_guild_id)
            .where(BattleGuild.alliance_id == alliance_id)
            .distinct()
        ).all()
        if not guild_ids:
            return {"7d": 0, "30d": 0, "all": 0}
        victim_filter = PlayerKillEvent.victim_guild_id.in_(guild_ids)

    rows = db.execute(
        select(PlayerKillEvent.timestamp, PlayerKillEvent.fame)
        .where(victim_filter, PlayerKillEvent.fame > 0)
    ).all()
    return _split_windows(rows, c7, c30)


def _battle_windows(db: Session, c7: datetime, c30: datetime, *, guild_id=None, alliance_id=None) -> dict:
    """Contagem de batalhas distintas por janela — só letais com mais de 20 jogadores."""
    if guild_id:
        rows = db.execute(
            select(Battle.start_time)
            .join(BattleGuild, BattleGuild.battle_id == Battle.id)
            .where(BattleGuild.albion_guild_id == guild_id, *_LETHAL_BIG)
        ).all()
        return _split_windows([(ts, 1) for (ts,) in rows], c7, c30)
    else:
        rows = db.execute(
            select(Battle.start_time)
            .join(BattleGuild, BattleGuild.battle_id == Battle.id)
            .where(BattleGuild.alliance_id == alliance_id, *_LETHAL_BIG)
            .group_by(Battle.id, Battle.start_time)
        ).all()
        return _split_windows([(ts, 1) for (ts,) in rows], c7, c30)


def _totals(db: Session, *, guild_id=None, alliance_id=None) -> tuple[int, int]:
    """Kills e mortes totais — só batalhas letais com mais de 20 jogadores."""
    q = (
        select(func.sum(BattleGuild.kills), func.sum(BattleGuild.deaths))
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .where(*_LETHAL_BIG)
    )
    if guild_id:
        q = q.where(BattleGuild.albion_guild_id == guild_id)
    else:
        q = q.where(BattleGuild.alliance_id == alliance_id)
    row = db.execute(q).one()
    return int(row[0] or 0), int(row[1] or 0)


def _members(db: Session, guild_id: str, min_players: int = 0, min_kills: int = 0) -> list[dict]:
    """Jogadores cujo último evento registrado ainda os mostra nesta guilda.
    min_players/min_kills filtram QUAIS batalhas entram no cálculo de stats."""
    # Última batalha (por ID) por jogador em toda a base — Battle.id é único e sequencial,
    # evita ambiguidade quando duas batalhas têm o mesmo start_time
    latest_sub = (
        select(BattleParticipant.albion_player_id, func.max(Battle.id).label("bid"))
        .join(Battle, Battle.id == BattleParticipant.battle_id)
        .group_by(BattleParticipant.albion_player_id)
        .subquery()
    )
    # IDs dos que ainda estão nesta guilda (sem filtro de tamanho — é sobre roster, não stats)
    current_ids = set(db.scalars(
        select(BattleParticipant.albion_player_id)
        .join(Battle, Battle.id == BattleParticipant.battle_id)
        .join(
            latest_sub,
            (latest_sub.c.albion_player_id == BattleParticipant.albion_player_id) &
            (latest_sub.c.bid == Battle.id),
        )
        .where(BattleParticipant.guild_id == guild_id)
        .distinct()
    ).all())

    # Remove jogadores que o tracker já sabe que estão em outra guilda ou sem guilda.
    # guild_id IS NULL significa que saiu da guilda; != trata só valores não-nulos em SQL.
    known_elsewhere = set(db.scalars(
        select(AlbionPlayer.albion_id)
        .where(
            AlbionPlayer.albion_id.in_(current_ids),
            (AlbionPlayer.guild_id != guild_id) | AlbionPlayer.guild_id.is_(None),
        )
    ).all())
    current_ids -= known_elsewhere

    if not current_ids:
        return []

    # Filtros de batalha para cálculo de stats
    stat_filters = [
        BattleParticipant.guild_id == guild_id,
        BattleParticipant.albion_player_id.in_(current_ids),
    ]
    if min_players > 0:
        stat_filters.append(Battle.players_total >= min_players)
    if min_kills > 0:
        stat_filters.append(Battle.kill_count >= min_kills)

    rows = db.execute(
        select(
            BattleParticipant.albion_player_id,
            BattleParticipant.name,
            func.count(BattleParticipant.id).label("battles"),
            func.sum(BattleParticipant.kills).label("kills"),
            func.sum(BattleParticipant.deaths).label("deaths"),
            func.sum(BattleParticipant.kill_fame).label("kill_fame"),
            func.max(Battle.start_time).label("last_seen"),
            func.max(Battle.region).label("region"),
            func.max(AlbionPlayer.is_deleted.cast(sa.Integer())).label("is_deleted"),
        )
        .join(Battle, Battle.id == BattleParticipant.battle_id)
        .outerjoin(AlbionPlayer, AlbionPlayer.albion_id == BattleParticipant.albion_player_id)
        .where(*stat_filters)
        .group_by(BattleParticipant.albion_player_id, BattleParticipant.name)
        .order_by(func.count(BattleParticipant.id).desc())
    ).all()

    # Contagem de roles — mesmo filtro de batalha
    weapon_fn = _weapon_function_map(db)
    equip_rows = db.execute(
        select(BattleParticipant.albion_player_id, BattleParticipant.equipment)
        .join(Battle, Battle.id == BattleParticipant.battle_id)
        .where(*stat_filters, BattleParticipant.equipment.isnot(None))
    ).all()
    role_map: dict[str, dict[str, int]] = {}
    for pid, equip in equip_rows:
        role = _classify_role(_as_builds(equip), weapon_fn)
        if role:
            rc = role_map.setdefault(pid, {})
            rc[role] = rc.get(role, 0) + 1

    return [
        {
            "albion_id": r.albion_player_id,
            "name": r.name,
            "region": r.region or "americas",
            "battles": r.battles,
            "kills": int(r.kills or 0),
            "deaths": int(r.deaths or 0),
            "kill_fame": int(r.kill_fame or 0),
            "last_seen": _aware(r.last_seen).isoformat(),
            "roles": role_map.get(r.albion_player_id, {}),
            "is_deleted": bool(r.is_deleted),
        }
        for r in rows
    ]


def _guilds_in_alliance(db: Session, alliance_id: str, min_players: int = 0, min_kills: int = 0) -> list[dict]:
    """Guildas cuja ÚLTIMA aparição em batalha ainda mostra elas nesta aliança —
    mesmo critério usado pro roster de uma guilda (ver _members): só a
    aparição mais recente conta pro roster, senão uma guilda que já saiu da
    aliança continuaria aparecendo pra sempre só por ter lutado junto antes."""
    latest_sub = (
        select(BattleGuild.albion_guild_id, func.max(Battle.id).label("bid"))
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .group_by(BattleGuild.albion_guild_id)
        .subquery()
    )
    current_ids = set(db.scalars(
        select(BattleGuild.albion_guild_id)
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .join(
            latest_sub,
            (latest_sub.c.albion_guild_id == BattleGuild.albion_guild_id) &
            (latest_sub.c.bid == Battle.id),
        )
        .where(BattleGuild.alliance_id == alliance_id)
        .distinct()
    ).all())
    if not current_ids:
        return []

    filters = [BattleGuild.alliance_id == alliance_id, BattleGuild.albion_guild_id.in_(current_ids)]
    if min_players > 0:
        filters.append(Battle.players_total >= min_players)
    if min_kills > 0:
        filters.append(Battle.kill_count >= min_kills)

    rows = db.execute(
        select(
            BattleGuild.albion_guild_id,
            BattleGuild.guild_name,
            func.count(func.distinct(BattleGuild.battle_id)).label("battles"),
            func.sum(BattleGuild.kills).label("kills"),
            func.sum(BattleGuild.deaths).label("deaths"),
            func.sum(BattleGuild.kill_fame).label("kill_fame"),
            func.max(Battle.start_time).label("last_seen"),
        )
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .where(*filters)
        .group_by(BattleGuild.albion_guild_id, BattleGuild.guild_name)
        .order_by(func.count(func.distinct(BattleGuild.battle_id)).desc())
    ).all()
    return [
        {
            "albion_id": r.albion_guild_id,
            "name": r.guild_name,
            "battles": r.battles,
            "kills": int(r.kills or 0),
            "deaths": int(r.deaths or 0),
            "kill_fame": int(r.kill_fame or 0),
            "last_seen": _aware(r.last_seen).isoformat(),
        }
        for r in rows
    ]



def _guild_alliance_history(db: Session, guild_id: str) -> list[dict]:
    """Cronologia de entradas/saídas de alianças desta guilda, mais recente primeiro."""
    rows = db.execute(
        select(Battle.start_time, BattleGuild.alliance_id, BattleGuild.alliance_name)
        .join(BattleGuild, BattleGuild.battle_id == Battle.id)
        .where(BattleGuild.albion_guild_id == guild_id)
        .order_by(Battle.start_time)
    ).mappings().all()

    events: list[dict] = []
    prev_id: str | None = "__init__"  # type: ignore[assignment]
    prev_name: str | None = None

    for row in rows:
        cur_id = row["alliance_id"]
        if cur_id == prev_id:
            continue
        ts = _aware(row["start_time"]).isoformat()
        if prev_id != "__init__":
            if prev_id:
                events.append({"event": "left", "date": ts,
                                "alliance_id": prev_id, "alliance_name": prev_name})
            if cur_id:
                events.append({"event": "joined", "date": ts,
                                "alliance_id": cur_id, "alliance_name": row["alliance_name"]})
        elif cur_id:
            events.append({"event": "joined", "date": ts,
                            "alliance_id": cur_id, "alliance_name": row["alliance_name"]})
        prev_id = cur_id
        prev_name = row["alliance_name"]

    return list(reversed(events))


def _alliance_roster_log(db: Session, alliance_id: str) -> list[dict]:
    """Log de mudanças na composição de guildas da aliança, mais recente primeiro.

    Uma guilda "entrou" quando aparece com nossa tag após ter sido vista com outra
    (ou nenhuma). Uma guilda "saiu" quando aparece com tag diferente após estar conosco.
    Ausência numa luta não conta — ela pode simplesmente não ter participado.
    """
    # Todas as guildas que já apareceram sob esta aliança
    guild_ids = list(db.scalars(
        select(BattleGuild.albion_guild_id)
        .where(BattleGuild.alliance_id == alliance_id)
        .distinct()
    ).all())

    if not guild_ids:
        return []

    # Todas as aparições dessas guildas em qualquer batalha, com a tag que usaram
    rows = db.execute(
        select(
            Battle.id.label("battle_id"),
            Battle.start_time,
            BattleGuild.albion_guild_id,
            BattleGuild.guild_name,
            BattleGuild.alliance_id.label("seen_alliance"),
        )
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .where(BattleGuild.albion_guild_id.in_(guild_ids))
        .order_by(Battle.start_time, Battle.id)
    ).mappings().all()

    # Agrupa por batalha mantendo ordem
    battle_order: list[tuple[int, datetime]] = []
    seen_battles: set[int] = set()
    battle_guilds: dict[int, dict[str, tuple[str, str | None]]] = {}

    for r in rows:
        bid = r["battle_id"]
        if bid not in seen_battles:
            seen_battles.add(bid)
            battle_order.append((bid, r["start_time"]))
            battle_guilds[bid] = {}
        battle_guilds[bid][r["albion_guild_id"]] = (r["guild_name"], r["seen_alliance"])

    # Conta batalhas por guilda nesta aliança (para ordenação no frontend)
    guild_battle_counts: dict[str, int] = {}
    for guilds_in_battle in battle_guilds.values():
        for gid, (_, seen_ally) in guilds_in_battle.items():
            if seen_ally == alliance_id:
                guild_battle_counts[gid] = guild_battle_counts.get(gid, 0) + 1

    # Guildas excluídas do jogo
    all_guild_ids = list(guild_battle_counts.keys())
    deleted_guilds_in_alliance = _deleted_set(db, "guild", all_guild_ids)

    def _gdict(gid: str, name: str) -> dict:
        return {"guild_id": gid, "name": name, "battles": guild_battle_counts.get(gid, 0),
                "is_deleted": gid in deleted_guilds_in_alliance}

    # Rastreia última tag vista por guilda e roster atual
    last_alliance: dict[str, str | None] = {}  # None = nunca visto antes
    current_roster: dict[str, str] = {}        # guild_id → nome (tag = alliance_id)
    events: list[dict] = []

    for bid, start_time in battle_order:
        guilds = battle_guilds[bid]
        added: list[tuple[str, str]] = []
        removed: list[tuple[str, str]] = []
        roster_before = dict(current_roster)

        for gid, (gname, seen_ally) in guilds.items():
            prev = last_alliance.get(gid)  # None = primeira vez que vemos esta guilda
            was_in = prev == alliance_id
            is_in = seen_ally == alliance_id

            # Só gera evento se já vimos a guilda antes (caso contrário não sabemos o histórico)
            if prev is not None:
                if is_in and not was_in:
                    added.append((gid, gname))
                elif was_in and not is_in:
                    removed.append((gid, gname))

            last_alliance[gid] = seen_ally
            if is_in:
                current_roster[gid] = gname
            else:
                current_roster.pop(gid, None)

        if added or removed:
            events.append({
                "date": _aware(start_time).isoformat(),
                "added":   [_gdict(g, n) for g, n in sorted(added)],
                "removed": [_gdict(g, n) for g, n in sorted(removed)],
                "roster_before": [_gdict(g, n) for g, n in sorted(roster_before.items())],
                "roster_after":  [_gdict(g, n) for g, n in sorted(current_roster.items())],
            })

    return list(reversed(events))


def _win_result(db: Session, battle: Battle, side_id: int | None) -> str | None:
    if not battle.is_zvz or not side_id:
        return None
    my_side = db.scalar(select(BattleSide).where(BattleSide.id == side_id))
    if not my_side or my_side.is_rats:
        return None
    enemy_max = db.scalar(
        select(func.max(BattleSide.score)).where(
            BattleSide.battle_id == battle.id,
            BattleSide.id != side_id,
            BattleSide.is_rats == False,
        )
    )
    if enemy_max is None:
        return None
    if my_side.score > enemy_max:
        return "win"
    if my_side.score < enemy_max:
        return "loss"
    return "draw"


PAGE_SIZE = 10


def _battle_filters(min_players: int, min_kills: int):
    """Retorna (where_clauses, big_ids_subquery_or_None) comuns aos dois helpers."""
    where = [Battle.is_lethal == True]
    if min_kills > 0:
        where.append(Battle.kill_count >= min_kills)
    big_sub = (
        select(BattleParticipant.battle_id)
        .where(BattleParticipant.guild_id.isnot(None))
        .group_by(BattleParticipant.battle_id, BattleParticipant.guild_id)
        .having(func.count(BattleParticipant.id) >= min_players)
    ) if min_players > 0 else None
    return where, big_sub


def _battles_guild(db: Session, guild_id: str, page: int = 0, min_players: int = 25, min_kills: int = 5) -> tuple[list[dict], int]:
    where, big_sub = _battle_filters(min_players, min_kills)
    guild_where = [BattleGuild.albion_guild_id == guild_id, *where]
    big_extra = [Battle.id.in_(big_sub)] if big_sub is not None else []

    total = db.scalar(
        select(func.count(func.distinct(BattleGuild.battle_id)))
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .where(*guild_where, *big_extra)
    ) or 0

    rows = db.execute(
        select(Battle, BattleGuild)
        .join(BattleGuild, BattleGuild.battle_id == Battle.id)
        .where(*guild_where, *big_extra)
        .order_by(Battle.start_time.desc())
        .limit(PAGE_SIZE).offset(page * PAGE_SIZE)
    ).all()
    groups = battle_groups.get_or_create_groups_bulk(db, [battle.id for battle, _ in rows])
    out = []
    for battle, bg in rows:
        out.append({
            "public_id": groups[battle.id].public_id,
            "region": battle.region,
            "start_time": _aware(battle.start_time).isoformat(),
            "cluster": battle.cluster,
            "is_zvz": battle.is_zvz,
            "players_total": battle.players_total,
            "kills": bg.kills,
            "deaths": bg.deaths,
            "kill_fame": bg.kill_fame,
            "result": _win_result(db, battle, bg.side_id),
            "factions": _factions_summary(db, battle.id),
        })
    return out, total


def _battles_alliance(db: Session, alliance_id: str, page: int = 0, min_players: int = 25, min_kills: int = 5) -> tuple[list[dict], int]:
    where, big_sub = _battle_filters(min_players, min_kills)
    alliance_where = [BattleGuild.alliance_id == alliance_id, *where]
    big_extra = [Battle.id.in_(big_sub)] if big_sub is not None else []

    total = db.scalar(
        select(func.count(func.distinct(BattleGuild.battle_id)))
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .where(*alliance_where, *big_extra)
    ) or 0

    battle_ids = db.scalars(
        select(BattleGuild.battle_id)
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .where(*alliance_where, *big_extra)
        .distinct()
        .order_by(Battle.start_time.desc())
        .limit(PAGE_SIZE).offset(page * PAGE_SIZE)
    ).all()
    if not battle_ids:
        return [], total

    agg = {
        row["battle_id"]: row
        for row in db.execute(
            select(
                BattleGuild.battle_id,
                func.sum(BattleGuild.kills).label("kills"),
                func.sum(BattleGuild.deaths).label("deaths"),
                func.sum(BattleGuild.kill_fame).label("kill_fame"),
                func.min(BattleGuild.side_id).label("side_id"),
            )
            .where(BattleGuild.battle_id.in_(battle_ids), BattleGuild.alliance_id == alliance_id)
            .group_by(BattleGuild.battle_id)
        ).mappings().all()
    }

    battles = {
        b.id: b for b in db.scalars(
            select(Battle).where(Battle.id.in_(battle_ids))
        ).all()
    }

    groups = battle_groups.get_or_create_groups_bulk(db, list(battles.keys()))
    out = []
    for bid in battle_ids:
        battle = battles.get(bid)
        a = agg.get(bid)
        if not battle or not a:
            continue
        out.append({
            "public_id": groups[battle.id].public_id,
            "region": battle.region,
            "start_time": _aware(battle.start_time).isoformat(),
            "cluster": battle.cluster,
            "is_zvz": battle.is_zvz,
            "players_total": battle.players_total,
            "kills": int(a["kills"] or 0),
            "deaths": int(a["deaths"] or 0),
            "kill_fame": int(a["kill_fame"] or 0),
            "result": _win_result(db, battle, a["side_id"]),
            "factions": _factions_summary(db, battle.id),
        })
    return out, total




# ---------------------------------------------------------------------------
# Busca global
# ---------------------------------------------------------------------------

def _search_entities(db: Session, entity_type: str, q: str, nq: str, limit: int = 6) -> list[SearchEntry]:
    """3 passes sargáveis sobre SearchEntry, cada um só roda se o anterior não
    encheu `limit`: prefixo (usa ix_search_entries_type_norm) -> substring ->
    fuzzy (edit-distance ≤1, só p/ queries de entidade ≥4 chars — mesma regra
    de search_norm.match). Substitui os full-scans de norm_sql(...).like()
    direto em battle_participants/battle_guilds do _search antigo."""
    lo, hi = prefix_range(nq)
    found = list(db.scalars(
        select(SearchEntry)
        .where(SearchEntry.entity_type == entity_type, SearchEntry.norm_name >= lo, SearchEntry.norm_name < hi)
        .order_by(SearchEntry.weight.desc())
        .limit(limit)
    ).all())
    seen = {e.entity_id for e in found}

    if len(found) < limit:
        more = db.scalars(
            select(SearchEntry)
            .where(
                SearchEntry.entity_type == entity_type,
                SearchEntry.norm_name.like(f"%{nq}%"),
                SearchEntry.entity_id.notin_(seen),
            )
            .order_by(SearchEntry.weight.desc())
            .limit(limit - len(found))
        ).all()
        found.extend(more)
        seen.update(e.entity_id for e in more)

    if len(found) < limit and len(nq) >= 4:
        ln = len(nq)
        candidates = db.scalars(
            select(SearchEntry)
            .where(
                SearchEntry.entity_type == entity_type,
                SearchEntry.name_len.between(ln - 1, ln + 1),
                SearchEntry.entity_id.notin_(seen),
            )
            .order_by(SearchEntry.weight.desc())
            .limit(300)
        ).all()
        for e in candidates:
            if len(found) == limit:
                break
            if search_match(q, e.display_name):
                found.append(e)
                seen.add(e.entity_id)

    return found


def _search(db: Session, q: str) -> dict:
    nq = norm_name(q)

    player_entries = _search_entities(db, "player", q, nq)
    guild_entries = _search_entities(db, "guild", q, nq)
    alliance_entries = _search_entities(db, "alliance", q, nq)

    players = [
        {
            "albion_id": e.entity_id, "name": e.display_name, "battles": e.weight,
            "region": e.region or "americas",
            "guild_name": e.guild_name, "alliance_name": e.alliance_name,
        }
        for e in player_entries
    ]
    guilds = [
        {"albion_id": e.entity_id, "name": e.display_name, "alliance_name": e.alliance_name, "battles": e.weight}
        for e in guild_entries
    ]
    alliances = [
        {"albion_id": e.entity_id, "name": e.display_name, "guild_count": e.guild_count or 0, "battles": e.weight}
        for e in alliance_entries
    ]

    # Batalhas — une player hits + guild/aliança hits (dos resultados já
    # resolvidos acima, por FK indexada — não escaneia mais nome/aliança),
    # pega as 6 mais recentes.
    p_ids = [e.entity_id for e in player_entries]
    g_ids = [e.entity_id for e in guild_entries]
    a_ids = [e.entity_id for e in alliance_entries]

    p_bids: set[int] = set()
    if p_ids:
        p_bids = set(db.scalars(
            select(Battle.id)
            .join(BattleParticipant, BattleParticipant.battle_id == Battle.id)
            .where(BattleParticipant.albion_player_id.in_(p_ids))
            .order_by(Battle.start_time.desc()).limit(30)
        ).all())
    g_bids: set[int] = set()
    if g_ids or a_ids:
        g_bids = set(db.scalars(
            select(Battle.id)
            .join(BattleGuild, BattleGuild.battle_id == Battle.id)
            .where(sa.or_(BattleGuild.albion_guild_id.in_(g_ids), BattleGuild.alliance_id.in_(a_ids)))
            .order_by(Battle.start_time.desc()).limit(30)
        ).all())
    all_bids = list(p_bids | g_bids)

    battles = []
    if all_bids:
        top_battles = db.scalars(
            select(Battle).where(Battle.id.in_(all_bids)).order_by(Battle.start_time.desc()).limit(6)
        ).all()
        # Só lê (nunca cria) — busca roda a cada tecla digitada, não vale a
        # pena escrever (e disputar lock com o resto do tráfego de fundo) só
        # pra sugestão de autocomplete. Batalha sem link público ainda fica
        # de fora da sugestão (ganha um assim que alguém abrir ela de fato).
        groups = battle_groups.get_existing_groups_bulk(db, [b.id for b in top_battles])
        for b in top_battles:
            group = groups.get(b.id)
            if group is None:
                continue
            battles.append({
                "public_id": group.public_id,
                "start_time": _aware(b.start_time).isoformat(),
                "cluster": b.cluster,
                "kill_count": b.kill_count,
                "total_fame": b.total_fame,
                "region": b.region,
                "factions": _factions_summary(db, b.id),
            })

    return {"players": players, "guilds": guilds, "alliances": alliances, "battles": battles}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/search")
def global_search(q: str = "", db: Session = Depends(deps.db_session)):
    q = q.strip()
    if len(q) < 2:
        return {"players": [], "guilds": [], "alliances": [], "battles": []}
    return _search(db, q)


# Etapa corrente da montagem de um perfil de guilda/aliança (agregação pesada
# sobre todas as batalhas — leva ~1min), consumida por polling do frontend.
# Mesma mecânica de /players/load-progress (routes/players.py). Valor =
# (token_da_run, stage): cargas simultâneas do MESMO perfil (StrictMode em
# dev, dois visitantes) compartilham a chave — o token garante que uma run
# terminando não apague a etapa de outra ainda em andamento.
# ponytail: dict em memória — mover pra Redis/DB se o deploy virar multi-processo.
_load_progress: dict[str, tuple[object, str]] = {}


@router.get("/load-progress/{entity_type}/{albion_id}")
def get_load_progress(entity_type: str, albion_id: str):
    """Etapa da montagem em andamento (stage=null: nada em andamento)."""
    entry = _load_progress.get(f"{entity_type}:{albion_id}")
    return {"stage": entry[1] if entry else None}


def _pop_progress_if_owner(key: str, token: object) -> None:
    entry = _load_progress.get(key)
    if entry is not None and entry[0] is token:
        _load_progress.pop(key, None)


@router.get("/guilds/{albion_id}")
def guild_profile(albion_id: str, db: Session = Depends(deps.db_session)):
    bg = db.scalars(
        select(BattleGuild)
        .where(BattleGuild.albion_guild_id == albion_id)
        .order_by(BattleGuild.id.desc())
        .limit(1)
    ).first()
    if not bg:
        raise HTTPException(404, "Guild não encontrada")

    key = f"guild:{albion_id}"
    token = object()
    _load_progress[key] = (token, "stats")
    try:
        c7, c30 = _cutoffs()
        kills_total, deaths_total = _totals(db, guild_id=albion_id)
        last_synced_at = db.scalar(
            select(func.max(Battle.fetched_at))
            .join(BattleGuild, BattleGuild.battle_id == Battle.id)
            .where(BattleGuild.albion_guild_id == albion_id)
        )
        kill_fame = _fame_windows(db, c7, c30, guild_id=albion_id)
        _load_progress[key] = (token, "silver")
        silver_dropped = _silver_windows(db, c7, c30, guild_id=albion_id)
        battle_windows = _battle_windows(db, c7, c30, guild_id=albion_id)
        _load_progress[key] = (token, "members")
        members = _members(db, albion_id)
        _load_progress[key] = (token, "history")
        battles_count = db.scalar(
            select(func.count(func.distinct(BattleGuild.battle_id)))
            .join(Battle, Battle.id == BattleGuild.battle_id)
            .where(BattleGuild.albion_guild_id == albion_id, *_LETHAL_BIG)
        ) or 0
        alliance_history = _guild_alliance_history(db, albion_id)
    finally:
        _pop_progress_if_owner(key, token)

    return {
        "albion_id": albion_id,
        "name": bg.guild_name,
        "alliance_id": bg.alliance_id,
        "alliance_name": bg.alliance_name,
        "last_synced_at": _aware(last_synced_at).isoformat() if last_synced_at else None,
        "kills_total": kills_total,
        "deaths_total": deaths_total,
        "kill_fame": kill_fame,
        "silver_dropped": silver_dropped,
        "battles": battle_windows,
        "members": members,
        "battles_count": battles_count,
        "alliance_history": alliance_history,
    }


@router.get("/alliances/{albion_id}")
def alliance_profile(albion_id: str, db: Session = Depends(deps.db_session)):
    bg = db.scalars(
        select(BattleGuild)
        .where(BattleGuild.alliance_id == albion_id)
        .order_by(BattleGuild.id.desc())
        .limit(1)
    ).first()
    if not bg:
        raise HTTPException(404, "Aliança não encontrada")

    key = f"alliance:{albion_id}"
    token = object()
    _load_progress[key] = (token, "stats")
    try:
        c7, c30 = _cutoffs()
        kills_total, deaths_total = _totals(db, alliance_id=albion_id)
        last_synced_at = db.scalar(
            select(func.max(Battle.fetched_at))
            .join(BattleGuild, BattleGuild.battle_id == Battle.id)
            .where(BattleGuild.alliance_id == albion_id)
        )
        kill_fame = _fame_windows(db, c7, c30, alliance_id=albion_id)
        _load_progress[key] = (token, "silver")
        silver_dropped = _silver_windows(db, c7, c30, alliance_id=albion_id)
        battle_windows = _battle_windows(db, c7, c30, alliance_id=albion_id)
        _load_progress[key] = (token, "members")
        guilds = _guilds_in_alliance(db, albion_id)
        _load_progress[key] = (token, "history")
        battles_count = db.scalar(
            select(func.count(func.distinct(BattleGuild.battle_id)))
            .join(Battle, Battle.id == BattleGuild.battle_id)
            .where(BattleGuild.alliance_id == albion_id, *_LETHAL_BIG)
        ) or 0
        roster_log = _alliance_roster_log(db, albion_id)
    finally:
        _pop_progress_if_owner(key, token)

    return {
        "albion_id": albion_id,
        "name": bg.alliance_name,
        "last_synced_at": _aware(last_synced_at).isoformat() if last_synced_at else None,
        "kills_total": kills_total,
        "deaths_total": deaths_total,
        "kill_fame": kill_fame,
        "silver_dropped": silver_dropped,
        "battles": battle_windows,
        "guilds": guilds,
        "battles_count": battles_count,
        "roster_log": roster_log,
    }


async def _check_albion_entity(entity_type: str, albion_id: str, path: str, db: Session) -> dict:
    """IDs de guilda/aliança são por REGIÃO (não existem nas outras 2, ver
    AlbionPlayer.region) — checar só o host Americas (como era antes)
    marcava "deletada" qualquer entidade de Europe/Asia, mesmo bem viva.
    Tenta os 3 hosts regionais (mesmo padrão de
    battle_tracker.resolve_by_albion_id) e só marca deletado de verdade se
    os 3 confirmarem 404 — qualquer timeout/erro no meio vira "unknown",
    nunca deletado."""
    import httpx
    from app.services.albion_gate import PROFILE, albion_scope, slot
    from app.services.player_tracker import HOSTS, make_client

    found = False
    inconclusive = False
    async with make_client() as c:
        async with albion_scope(PROFILE):
            for host in HOSTS.values():
                try:
                    async with slot():
                        resp = await c.get(f"https://{host}/api/gameinfo/{path}/{albion_id}", timeout=8.0)
                except (httpx.TimeoutException, httpx.NetworkError):
                    inconclusive = True
                    continue
                if resp.status_code == 200:
                    found = True
                    break
                if resp.status_code != 404:
                    inconclusive = True

    row = db.scalar(select(DeletedProfile).where(
        DeletedProfile.entity_type == entity_type, DeletedProfile.albion_id == albion_id
    ))
    if found:
        if row is not None:
            db.delete(row)
            db.commit()
        return {"exists": True}
    if inconclusive:
        return {"exists": True, "unknown": True}
    if row is None:
        db.add(DeletedProfile(entity_type=entity_type, albion_id=albion_id,
                              deleted_at=datetime.now(timezone.utc)))
        db.commit()
    return {"exists": False}


@router.get("/guilds/{albion_id}/check")
async def check_guild(albion_id: str, db: Session = Depends(deps.db_session)):
    return await _check_albion_entity("guild", albion_id, "guilds", db)


@router.get("/alliances/{albion_id}/check")
async def check_alliance(albion_id: str, db: Session = Depends(deps.db_session)):
    return await _check_albion_entity("alliance", albion_id, "alliances", db)


@router.get("/guilds/{albion_id}/members")
def guild_members(albion_id: str, min_players: int = 0, min_kills: int = 0, db: Session = Depends(deps.db_session)):
    return _members(db, albion_id, min_players=min_players, min_kills=min_kills)


@router.get("/alliances/{albion_id}/members")
def alliance_members(albion_id: str, min_players: int = 0, min_kills: int = 0, db: Session = Depends(deps.db_session)):
    return _guilds_in_alliance(db, albion_id, min_players=min_players, min_kills=min_kills)


@router.get("/guilds/{albion_id}/battles")
def guild_battles(albion_id: str, page: int = 0, min_players: int = 25, min_kills: int = 5, db: Session = Depends(deps.db_session)):
    battles, total = _battles_guild(db, albion_id, page, min_players=min_players, min_kills=min_kills)
    return {"battles": battles, "total": total, "page": page, "pages": max(1, -(-total // PAGE_SIZE))}


@router.get("/alliances/{albion_id}/battles")
def alliance_battles(albion_id: str, page: int = 0, min_players: int = 25, min_kills: int = 5, db: Session = Depends(deps.db_session)):
    battles, total = _battles_alliance(db, albion_id, page, min_players=min_players, min_kills=min_kills)
    return {"battles": battles, "total": total, "page": page, "pages": max(1, -(-total // PAGE_SIZE))}
