"""Perfis públicos de guildas e alianças — sem autenticação, sem escopo de guild."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import deps
from app.api.routes.battles import _as_builds, _classify_role, _factions_summary, _factions_summary_bulk, _weapon_function_map, _wbase
from app.db import SessionLocal
from app.models.battles import Battle, BattleGuild, BattleParticipant, BattleSide
from app.models.dashboard_cache import DashboardCache
from app.models.guild_profiles import AllianceProfile, GuildProfile
from app.models.players import AlbionPlayer, DeletedProfile, PlayerKillEvent, SearchEntry
from app.services import battle_groups
from app.services.search_norm import match as search_match, normalize as norm_name, prefix_range

log = logging.getLogger(__name__)

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
    min_players/min_kills filtram QUAIS batalhas entram no cálculo de stats.

    Otimização: a subquery `latest_sub` original agrupava TODOS os
    BattleParticipant do banco (~2.5M rows em prod) pra computar "última
    batalha por player" — sem filtro de guilda, escaneava o banco inteiro
    só pra descartar 99% no JOIN externo. Pré-filtramos os candidates da
    guilda primeiro (índice ix_battle_participants_guild_id, ~ms), e a
    subquery agrupa só esses — medido: 42s → <10ms no dev com 2.5M rows.
    Semântica preservada: "última batalha global do jogador" continua sendo
    global (a subquery não filtra guild_id, só o conjunto de players)."""
    # Candidates: players que aparecem NESSA guilda em qualquer batalha.
    # Índice em guild_id torna isso O(log n) em vez de full scan.
    candidates = list(db.scalars(
        select(BattleParticipant.albion_player_id)
        .where(BattleParticipant.guild_id == guild_id)
        .distinct()
    ).all())
    if not candidates:
        return []

    # Última batalha (por ID) por jogador — SÓ dos candidates da guilda,
    # não do banco inteiro. Battle.id é único e sequencial, evita ambiguidade
    # quando duas batalhas têm o mesmo start_time.
    latest_sub = (
        select(BattleParticipant.albion_player_id, func.max(Battle.id).label("bid"))
        .join(Battle, Battle.id == BattleParticipant.battle_id)
        .where(BattleParticipant.albion_player_id.in_(candidates))
        .group_by(BattleParticipant.albion_player_id)
        .subquery()
    )
    # IDs dos que ainda estão nesta guilda: a última batalha global do
    # jogador é uma onde ele estava nesta guilda (senão saiu).
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
    aliança continuaria aparecendo pra sempre só por ter lutado junto antes.

    Mesma otimização de _members: pré-filtra candidates da aliança (índice
    em alliance_id) antes de agrupar — senão a subquery agrupa todas as
    guildas do banco (~milhares) só pra descartar quase todas no JOIN."""
    # Candidates: guildas que apareceram NESSA aliança em qualquer batalha.
    candidates = list(db.scalars(
        select(BattleGuild.albion_guild_id)
        .where(BattleGuild.alliance_id == alliance_id)
        .distinct()
    ).all())
    if not candidates:
        return []

    latest_sub = (
        select(BattleGuild.albion_guild_id, func.max(Battle.id).label("bid"))
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .where(BattleGuild.albion_guild_id.in_(candidates))
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


def _win_results_bulk(db: Session, battles: list[tuple[Battle, int | None]]) -> dict[int, str | None]:
    """Versão em lote de _win_result: resolve vitória/derrota de várias batalhas
    ZvZ em 1 query (todos os BattleSide dos battles de uma vez), em vez de 2
    queries por batalha (1 BattleSide por side_id + 1 max(score) por battle).
    Pra 10 batalhas na página: 20 queries → 1.

    `battles` = lista de (battle, side_id). Retorna dict[battle.id, result].
    Batalhas não-ZvZ / sem side_id / is_rats ficam None. Mesma semântica de
    _win_result: max(score) dos INIMIGOS (sides != side_id, is_rats=False).

    Cada entrada de `battles` vira uma chave no dict de saída por battle.id —
    se a mesma batalha aparecer 2x com side_id diferente (raro, mas possível
    se o caller passar ambas), só a última entra no dict (mesma batalha não
    pode ter 2 resultados na mesma renderização)."""
    # Filtra só as que valem consultar (ZvZ + side_id presente).
    # wanted_pairs: lista de (battle_id, side_id) únicos, na ordem de entrada.
    wanted_pairs: list[tuple[int, int]] = []
    seen_pairs: set[tuple[int, int]] = set()
    battle_ids: set[int] = set()
    for battle, side_id in battles:
        if not battle.is_zvz or not side_id:
            continue
        pair = (battle.id, side_id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        wanted_pairs.append(pair)
        battle_ids.add(battle.id)
    if not wanted_pairs:
        return {b.id: None for b, _ in battles}

    # 1 query: todos os BattleSide dos battles em questão. Pode trazer vários
    # sides por batalha (ZvZ com 5 lados), mas é 1 round-trip só.
    all_sides = db.scalars(
        select(BattleSide).where(BattleSide.battle_id.in_(battle_ids))
    ).all()
    by_battle: dict[int, dict[int, BattleSide]] = {}
    for s in all_sides:
        by_battle.setdefault(s.battle_id, {})[s.id] = s

    out: dict[int, str | None] = {}
    # Inicializa TODAS as batalhas (inclui as não-ZvZ / sem side_id) como None.
    for battle, _ in battles:
        out.setdefault(battle.id, None)
    # Sobrescreve com o resultado real, processando cada par único.
    for bid, side_id in wanted_pairs:
        sides = by_battle.get(bid, {})
        my_side = sides.get(side_id)
        if my_side is None or my_side.is_rats:
            out[bid] = None
            continue
        # max(score) dos inimigos (sides != side_id, is_rats=False) — mesma
        # lógica do _win_result original.
        enemy_max = max(
            (s.score for s in sides.values() if s.id != side_id and not s.is_rats),
            default=None,
        )
        if enemy_max is None:
            out[bid] = None
        elif my_side.score > enemy_max:
            out[bid] = "win"
        elif my_side.score < enemy_max:
            out[bid] = "loss"
        else:
            out[bid] = "draw"
    return out


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


# Cache do conjunto de battle_id que passam no HAVING >= min_players — a
# subquery agrupa todos os 2.5M BattleParticipant por (battle_id, guild_id)
# e devolve ~5k battle_ids; mesma resposta pra qualquer guilda/alliance, só
# cresce quando entra batalha nova >= min_players. Sem cache, era executada
# 2x por _battles_guild/_battles_alliance (count + page) = 6.6s por chamada.
# TTL de 5min: batalhas novas com >= 25 jogadores são raras (minutos entre
# ZvZs grandes), e o perfil de guilda já tem cache de 5min no _cold_cache.
# ponytail: dict em memória por min_players — move pra Redis se virar
# multi-processo. Invalidação por timestamp (não por evento) é simples e
# suficiente: 5min é a mesma janela do cold cache.
_BIG_BIDS_TTL = 300.0
_big_bids_cache: dict[int, tuple[float, set[int]]] = {}


def _big_battle_ids(db: Session, min_players: int) -> set[int]:
    """Conjunto de battle_id com >= min_players de alguma guilda. Cacheado
    por 5min — ver comentário em _big_bids_cache."""
    import time as _time
    now = _time.monotonic()
    entry = _big_bids_cache.get(min_players)
    if entry is not None and now < entry[0]:
        return entry[1]
    bids = set(db.scalars(
        select(BattleParticipant.battle_id)
        .where(BattleParticipant.guild_id.isnot(None))
        .group_by(BattleParticipant.battle_id, BattleParticipant.guild_id)
        .having(func.count(BattleParticipant.id) >= min_players)
    ).all())
    _big_bids_cache[min_players] = (now + _BIG_BIDS_TTL, bids)
    return bids


def _battles_guild(
    db: Session, guild_id: str, page: int = 0, min_players: int = 25, min_kills: int = 5,
    factions_cache: dict[int, list[dict]] | None = None,
) -> tuple[list[dict], int]:
    where, big_sub = _battle_filters(min_players, min_kills)
    guild_where = [BattleGuild.albion_guild_id == guild_id, *where]
    # big_sub vira set em memória cacheado por 5min (ver _big_battle_ids) —
    # evita re-executar a subquery de 2.5M rows 2x (count + page) por chamada.
    big_extra = [Battle.id.in_(_big_battle_ids(db, min_players))] if big_sub is not None else []

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
    # Batch: 1 query pra todos os _win_result + 1 batch pra todos os
    # _factions_summary (3 queries fixas, em vez de 3 por batalha) — antes
    # 5 queries por batalha (2 de _win_result + 3 de _factions_summary),
    # agora 1 + 3 = 4 totais pra a página inteira.
    win_results = _win_results_bulk(db, [(battle, bg.side_id) for battle, bg in rows])
    factions_by_bid = _factions_summary_bulk(db, [battle.id for battle, _ in rows])
    fc = factions_cache if factions_cache is not None else {}
    out = []
    for battle, bg in rows:
        factions = fc.get(battle.id)
        if factions is None:
            factions = factions_by_bid.get(battle.id, [])
            fc[battle.id] = factions
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
            "result": win_results.get(battle.id),
            "factions": factions,
        })
    return out, total


def _battles_alliance(
    db: Session, alliance_id: str, page: int = 0, min_players: int = 25, min_kills: int = 5,
    factions_cache: dict[int, list[dict]] | None = None,
) -> tuple[list[dict], int]:
    where, big_sub = _battle_filters(min_players, min_kills)
    alliance_where = [BattleGuild.alliance_id == alliance_id, *where]
    big_extra = [Battle.id.in_(_big_battle_ids(db, min_players))] if big_sub is not None else []

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
    # Mesmo batch de _battles_guild — 1 query pra todos os _win_result,
    # 1 batch pra todos os _factions_summary.
    win_results = _win_results_bulk(
        db, [(battles.get(bid), agg.get(bid, {}).get("side_id") if agg.get(bid) else None) for bid in battle_ids if battles.get(bid)]
    )
    factions_by_bid = _factions_summary_bulk(db, list(battles.keys()))
    fc = factions_cache if factions_cache is not None else {}
    out = []
    for bid in battle_ids:
        battle = battles.get(bid)
        a = agg.get(bid)
        if not battle or not a:
            continue
        factions = fc.get(battle.id)
        if factions is None:
            factions = factions_by_bid.get(battle.id, [])
            fc[battle.id] = factions
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
            "result": win_results.get(battle.id),
            "factions": factions,
        })
    return out, total




# ---------------------------------------------------------------------------
# Busca global
# ---------------------------------------------------------------------------

def _search_entities(db: Session, entity_type: str, q: str, nq: str, limit: int = 6, region: str | None = None) -> list[SearchEntry]:
    """3 passes sargáveis sobre SearchEntry, cada um só roda se o anterior não
    encheu `limit`: prefixo (usa ix_search_entries_type_norm) -> substring ->
    fuzzy (edit-distance ≤1, só p/ queries de entidade ≥4 chars — mesma regra
    de search_norm.match). Substitui os full-scans de norm_sql(...).like()
    direto em battle_participants/battle_guilds do _search antigo.

    `region` (opcional) filtra players/guilds/alliances por servidor — nomes
    não são únicos entre Americas/Europe/Asia, e o usuário pode pesquisar
    "slayner americas" pra restringir. None = todas as regiões."""
    lo, hi = prefix_range(nq)
    region_filter = [SearchEntry.region == region] if region else []
    found = list(db.scalars(
        select(SearchEntry)
        .where(SearchEntry.entity_type == entity_type, SearchEntry.norm_name >= lo, SearchEntry.norm_name < hi, *region_filter)
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
                *region_filter,
            )
            .order_by(SearchEntry.weight.desc())
            .limit(limit - len(found))
        ).all()
        found.extend(more)
        seen.update(e.entity_id for e in more)

    # Fuzzy sem filtro de região: guilda "ALISON" na europe não casa por
    # prefix/substring com filtro americas, mas o fuzzy por name_len ignora
    # região e depois filtra — pra não perder o caso raro de região errada
    # no banco (guilda migrate). Só roda se a região NÃO foi pedida (com
    # região, não faz sentido buscar跨-região).
    if len(found) < limit and len(nq) >= 4 and not region:
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


def _search(db: Session, q: str, region: str | None = None) -> dict:
    nq = norm_name(q)

    player_entries = _search_entities(db, "player", q, nq, region=region)
    guild_entries = _search_entities(db, "guild", q, nq, region=region)
    alliance_entries = _search_entities(db, "alliance", q, nq, region=region)

    players = [
        {
            "albion_id": e.entity_id, "name": e.display_name, "battles": e.weight,
            "region": e.region or "americas",
            "guild_name": e.guild_name, "alliance_name": e.alliance_name,
        }
        for e in player_entries
    ]
    guilds = [
        {"albion_id": e.entity_id, "name": e.display_name, "alliance_name": e.alliance_name, "battles": e.weight,
         "region": e.region}
        for e in guild_entries
    ]
    alliances = [
        {"albion_id": e.entity_id, "name": e.display_name, "guild_count": e.guild_count or 0, "battles": e.weight,
         "region": e.region}
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
        # Batch: 3 queries pra todos os _factions_summary de uma vez, em vez
        # de 3 por batalha (6 batalhas = 18 queries → 3).
        factions_by_bid = _factions_summary_bulk(db, [b.id for b in top_battles])
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
                "factions": factions_by_bid.get(b.id, []),
            })

    return {"players": players, "guilds": guilds, "alliances": alliances, "battles": battles}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/search")
def global_search(q: str = "", region: str | None = None, db: Session = Depends(deps.db_session)):
    q = q.strip()
    if len(q) < 2:
        return {"players": [], "guilds": [], "alliances": [], "battles": []}
    # `region` vem do parse do frontend ("slayner americas" → q="slayner",
    # region="americas"). Valida contra as regiões conhecidas; string aleatória
    # não filtra (devolve tudo, como se não tivesse região).
    valid_regions = {"americas", "europe", "asia"}
    r = region if region in valid_regions else None
    return _search(db, q, region=r)


@router.get("/search/external")
async def global_search_external(q: str, region: str | None = None, db: Session = Depends(deps.db_session)):
    """Busca direto na API do Albion e persiste tudo que encontrar — players
    (upsert_player), guilds e alliances (upsert_entry em SearchEntry).
    Prioridade PROFILE (fura a fila de fundo: warmers, backfill).

    `region` restringe a um host só (americas/europe/asia); sem ele busca nas
    3 regiões em paralelo. Mesma resposta do /search local, mas após gravar,
    então uma 2ª busca pelo mesmo nome já resolve localmente (instantâneo).
    Idempotente: re-buscar o mesmo nome só atualiza afiliação/fama.

    Chamado pelo frontend DEPOIS de 8s esperando pelo /search local sem
    resultados — "talvez não tenhamos esse nome na base ainda, mas a Albion
    tem". Tudo que é encontrado vira perfil (mesmo que incompleto), pra evitar
    futuras buscas acidentais: a próxima vez já acha local."""
    import httpx
    from app.services.albion_gate import PROFILE, albion_scope, slot
    from app.services.player_tracker import HOSTS, make_client, upsert_player
    from app.services import search_index

    q = q.strip()
    if len(q) < 2:
        return {"players": [], "guilds": [], "alliances": []}

    # Restringe ao host da região pedida, ou todas as 3 se não veio região.
    valid_regions = {"americas", "europe", "asia"}
    targets = {region: HOSTS[region]} if region in valid_regions else dict(HOSTS)

    async def _search_region(client: httpx.AsyncClient, host: str, region: str) -> dict:
        try:
            async with slot():
                resp = await client.get(f"https://{host}/api/gameinfo/search", params={"q": q}, timeout=8.0)
            if resp.status_code != 200:
                return {"players": [], "guilds": [], "alliances": []}
            return resp.json()
        except (httpx.TimeoutException, httpx.NetworkError):
            return {"players": [], "guilds": [], "alliances": []}

    async with make_client() as c:
        async with albion_scope(PROFILE):
            results = await asyncio.gather(*[
                _search_region(c, host, region) for region, host in targets.items()
            ])

    # Persiste tudo que a Albion devolveu. Players viram AlbionPlayer (upsert),
    # guilds/alliances viram SearchEntry direto (não há modelo próprio — só
    # aparecem em batalhas, e o search_index rebuild eventualmente os pega
    # também; aqui já grava pra ser encontrável agora).
    players_out: list[dict] = []
    guilds_out: list[dict] = []
    alliances_out: list[dict] = []
    for region, data in zip(targets.keys(), results):
        for p in data.get("players", []) or []:
            if not p.get("Id"):
                continue
            try:
                upsert_player(db, p, region)
            except Exception:
                pass
            players_out.append({
                "albion_id": p["Id"], "name": p.get("Name") or p["Id"],
                "region": region,
                "guild_name": p.get("GuildName"), "alliance_name": p.get("AllianceName"),
                "battles": 0,
            })
            search_index.safe_upsert_entry(
                db, entity_type="player", entity_id=p["Id"], display_name=p.get("Name") or "",
                region=region, guild_name=p.get("GuildName"), alliance_name=p.get("AllianceName"),
            )
        for g in data.get("guilds", []) or []:
            if not g.get("Id"):
                continue
            guilds_out.append({
                "albion_id": g["Id"], "name": g.get("Name") or g["Id"],
                "alliance_name": g.get("AllianceName"), "battles": 0,
                "region": region,
            })
            search_index.safe_upsert_entry(
                db, entity_type="guild", entity_id=g["Id"], display_name=g.get("Name") or "",
                region=region, alliance_name=g.get("AllianceName"),
            )
        for a in data.get("alliances", []) or []:
            if not a.get("Id"):
                continue
            alliances_out.append({
                "albion_id": a["Id"], "name": a.get("Name") or a["Id"],
                "guild_count": 0, "battles": 0,
                "region": region,
            })
            search_index.safe_upsert_entry(
                db, entity_type="alliance", entity_id=a["Id"], display_name=a.get("Name") or "",
                region=region,
            )
    try:
        db.commit()
    except Exception:
        db.rollback()

    return {"players": players_out, "guilds": guilds_out, "alliances": alliances_out}


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


# Tasks de cold load em background — sobrevivem ao client desconectar. Sem
# isso, reload na tab cancelava a coroutine da request e a agregação pesada
# (~1min pra guildas grandes) morria no meio; a nova request recomeçava do
# zero. Agora a task continua, e o _load_progress mostra o stage atual pra
# qualquer request que perguntar — a barra continua de onde estava.
_cold_load_tasks: dict[str, asyncio.Task] = {}

# Timestamp do último timeout de cold load, por chave. A rota checa se passou
# tempo suficiente pra tentar de novo (evita loop infinito de timeout imediato
# mas permite retry após um tempo). Sem isso, ou o stage de erro fica pra
# sempre (toda request devolve 504) ou re-dispara imediatamente (loop infinito).
_TIMEOUT_RETRY_AFTER = timedelta(seconds=30)
_cold_timeout_at: dict[str, datetime] = {}


def _check_cold_timeout(key: str) -> None:
    """Checa se a última task de cold load terminou com timeout. Se sim e ainda
    não passou tempo suficiente pra tentar de novo, levanta 504. Se já passou,
    limpa o stage de erro e deixa a rota disparar nova task."""
    entry = _load_progress.get(key)
    if not (entry and entry[1] == "error:timeout"):
        return
    timeout_at = _cold_timeout_at.get(key)
    if timeout_at is None:
        return  # sem timestamp — limpa e tenta de novo
    if datetime.now(timezone.utc) - timeout_at < _TIMEOUT_RETRY_AFTER:
        raise HTTPException(504, "Tempo esgotado ao carregar o perfil — tente novamente")
    # Passou tempo suficiente — limpa o erro e deixa disparar nova task.
    _load_progress.pop(key, None)
    _cold_timeout_at.pop(key, None)

# Cache do resultado da agregação — chave = "guild:{id}" / "alliance:{id}".
# A agregação de guilds/alianças é só leitura (não grava no DB), então sem
# cache cada reload refazia tudo do zero. Com cache, a primeira visit dispara
# a task, o resultado é guardado aqui, e próximas leituras servem instantâneo.
#
# TTL de 15 DIAS: o perfil serve do cache sem reclamar — o usuário vê a idade
# (last_synced_at) e decide se quer atualizar usando o botão ⟳. Loading
# forçado só acontece se o cache expirou (15 dias) ou se nunca foi carregado.
# Antes era 5min/30min e abertura com 7min de diferença refazia do zero — o
# usuário esperava 1min pra ver a mesma coisa que já tinha visto.
#
# O refresh (botão ⟳) invalida o cache via _cold_cache_get checando
# last_seen_at do GuildProfile/AllianceProfile — quando o warmer termina, a
# próxima leitura vê que o perfil foi atualizado e refaz a agregação (com o
# last_synced_at fresco).
#
# Write-through: além do dict em memória (leitura instantânea no mesmo
# processo), todo payload também é gravado no DashboardCache (DB) — mesma
# tabela/padrão do highscores_cache. Sem isso, reiniciar o backend perdia o
# cache inteiro e a PRIMEIRA visita de cada perfil depois do restart tinha
# que refazer a agregação pesada (~1min) do zero, mesmo perfis visitados
# minutos antes do restart. Não é um precompute agendado como highscores_cache
# (guildas/alianças são demais pra pré-computar todas) — só grava o que já foi
# visitado, sob demanda, exatamente como o dict fazia.
_COLD_CACHE_TTL = timedelta(days=15)

# Timeout de cold load (primeira visita) — mesmo valor do profile_warmer.
# Se a agregação não terminar a tempo, o stage vira error:timeout e a task
# continua rastreada até a thread sair. Tempo em fila não conta — só depois
# de começar a processar.
COLD_LOAD_TIMEOUT = timedelta(minutes=15)
_cold_cache: dict[str, tuple[datetime, dict]] = {}


def _cold_cache_put(key: str, payload: dict) -> None:
    """Grava o payload no dict em memória E no DashboardCache (DB)."""
    now = datetime.now(timezone.utc)
    _cold_cache[key] = (now, payload)
    db = SessionLocal()
    try:
        row = db.get(DashboardCache, key)
        if row is None:
            db.add(DashboardCache(key=key, payload=payload))
        else:
            row.payload = payload
        db.commit()
    except Exception:
        db.rollback()
        log.exception("cold_cache: falha ao persistir %s", key)
    finally:
        db.close()


def _cold_cache_get(key: str, db: Session | None = None) -> dict | None:
    """Retorna o payload cacheado se válido. Se `db` for passado, checa também
    se o `last_seen_at` do GuildProfile/AllianceProfile é mais recente que o
    cache — se sim, o warmer rodou um refresh desde que o cache foi escrito e
    o payload precisa ser refeito (o `last_synced_at` mudou). Sem isso, o
    usuário clica em ⟳, o warmer atualiza o perfil, mas o cache continua
    servindo o `last_synced_at` antigo — o refresh parece não ter feito nada.

    Miss em memória cai pro DashboardCache antes de mandar refazer a
    agregação — cobre o caso comum de "backend acabou de reiniciar, mas esse
    perfil já tinha sido cacheado antes"."""
    entry = _cold_cache.get(key)
    if entry is None:
        if db is None:
            return None
        row = db.get(DashboardCache, key)
        if row is None:
            return None
        cached_at = _aware(row.updated_at)
        if datetime.now(timezone.utc) - cached_at > _COLD_CACHE_TTL:
            return None
        entry = (cached_at, row.payload)
        _cold_cache[key] = entry  # repovoa a memória — próxima leitura não bate no DB de novo
    cached_at, payload = entry
    # TTL expirou — remove e retorna None.
    if datetime.now(timezone.utc) - cached_at > _COLD_CACHE_TTL:
        _cold_cache.pop(key, None)
        return None
    # Se tem DB, checa se o warmer rodou um refresh depois do cache.
    if db is not None and key.startswith("guild:"):
        gp = db.scalar(select(GuildProfile).where(GuildProfile.albion_id == key.split(":", 1)[1]))
        if gp is not None and _aware(gp.last_seen_at) > cached_at:
            _cold_cache.pop(key, None)
            return None
    elif db is not None and key.startswith("alliance:"):
        ap = db.scalar(select(AllianceProfile).where(AllianceProfile.albion_id == key.split(":", 1)[1]))
        if ap is not None and _aware(ap.last_seen_at) > cached_at:
            _cold_cache.pop(key, None)
            return None
    return payload


def _overlay_refresh_state(cached: dict, db: Session, model, albion_id: str) -> dict:
    """Sobrepõe o `refresh_requested_at` VIVO do DB no payload cacheado.

    Clicar em ⟳ (POST /refresh) grava `refresh_requested_at` mas NÃO mexe em
    `last_seen_at` — então `_cold_cache_get` não invalida (só olha last_seen_at)
    e o payload cacheado continua com `refresh_requested_at: null` stale. O
    front lê isso como "refresh já terminou", para o polling na hora e volta a
    mostrar a idade antiga — o botão parece não fazer nada (era o bug de
    'guilda com age 20h: clico e volta pra 20h'). Lê a linha (barato, indexado)
    e devolve cópia RASA com o flag corrente — não muta o dict do cache."""
    row = db.scalar(select(model).where(model.albion_id == albion_id))
    if row is None:
        return cached
    return {**cached, "refresh_requested_at":
            _aware(row.refresh_requested_at).isoformat() if row.refresh_requested_at else None}


def _build_guild_payload_sync(db: Session, albion_id: str) -> dict:
    """Monta o payload completo do perfil de guilda a partir do DB. Síncrono,
    chamado pela task de cold load (em to_thread) e pela rota quando já está
    no cache. Não atrelado a request HTTP — pode rodar em background."""
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
        gp = db.scalar(select(GuildProfile).where(GuildProfile.albion_id == albion_id))
        _load_progress[key] = (token, "silver")
        kill_fame = _fame_windows(db, c7, c30, guild_id=albion_id)
        silver_dropped = _silver_windows(db, c7, c30, guild_id=albion_id)
        battle_windows = _battle_windows(db, c7, c30, guild_id=albion_id)
        _load_progress[key] = (token, "members")
        # 25/5 = mesmo default que a aba Membros do frontend pede na primeira
        # renderização (ver GuildProfilePage.tsx). Alinhado de propósito: o
        # payload embutido serve o caso comum sem round-trip extra — o
        # frontend só refaz a busca se o usuário mudar o filtro.
        members = _members(db, albion_id, min_players=25, min_kills=5)
        _load_progress[key] = (token, "history")
        battles_count = db.scalar(
            select(func.count(func.distinct(BattleGuild.battle_id)))
            .join(Battle, Battle.id == BattleGuild.battle_id)
            .where(BattleGuild.albion_guild_id == albion_id, *_LETHAL_BIG)
        ) or 0
        alliance_history = _guild_alliance_history(db, albion_id)
        # Primeira página da aba Batalhas, mesmos defaults do frontend
        # (page=0, min_players=25, min_kills=5) — mesmo motivo do `members`
        # acima: a aba abre instantânea em vez de refazer uma busca que já
        # dava pra ter vindo de graça no cold-load. Paginado (LIMIT 10), bem
        # mais barato que o scan de `_members`.
        # factions_cache: se essa guilda aparecer tb em _members (via
        # _classify_role não, que não chama _factions_summary) não há ganho;
        # mas o cache é barato e deixa pronto pro caso de reusar a mesma
        # batalha em outra seção do payload no futuro.
        factions_cache: dict[int, list[dict]] = {}
        page0_battles, page0_total = _battles_guild(db, albion_id, 0, min_players=25, min_kills=5, factions_cache=factions_cache)
    finally:
        _pop_progress_if_owner(key, token)

    return {
        "albion_id": albion_id,
        "name": bg.guild_name,
        "alliance_id": bg.alliance_id,
        "alliance_name": bg.alliance_name,
        "last_synced_at": _aware(gp.last_seen_at).isoformat() if gp else (
            _aware(last_synced_at).isoformat() if last_synced_at else None
        ),
        "refresh_requested_at": _aware(gp.refresh_requested_at).isoformat() if gp and gp.refresh_requested_at else None,
        "kills_total": kills_total,
        "deaths_total": deaths_total,
        "kill_fame": kill_fame,
        "silver_dropped": silver_dropped,
        "battles": battle_windows,
        "members": members,
        "battles_count": battles_count,
        "alliance_history": alliance_history,
        "battles_page0": {
            "battles": page0_battles, "total": page0_total, "page": 0,
            "pages": max(1, -(-page0_total // PAGE_SIZE)),
        },
    }


def _build_payload_with_session(builder, albion_id: str) -> dict:
    """Executa um builder síncrono com uma sessão pertencente à mesma thread."""
    db = SessionLocal()
    try:
        return builder(db, albion_id)
    finally:
        db.close()


async def _cold_load_guild(albion_id: str) -> None:
    """Faz a agregação pesada do perfil de guilda em background — sobrevive ao
    client desconectar. Atualiza _load_progress em cada etapa; cacheia o
    resultado em _cold_cache; limpa a task no final.

    Timeout de COLD_LOAD_TIMEOUT (15min): se a agregação não terminar a tempo
    (DB travado, query lenta demais), o stage vira error:timeout e a task fica
    rastreada até a thread sair, impedindo outro worker para a mesma chave."""
    key = f"guild:{albion_id}"
    worker = asyncio.create_task(asyncio.to_thread(
        _build_payload_with_session, _build_guild_payload_sync, albion_id,
    ))
    try:
        payload = await asyncio.wait_for(
            asyncio.shield(worker),
            timeout=COLD_LOAD_TIMEOUT.total_seconds(),
        )
        await asyncio.to_thread(_cold_cache_put, key, payload)
    except asyncio.TimeoutError:
        _load_progress[key] = (object(), "error:timeout")
        _cold_timeout_at[key] = datetime.now(timezone.utc)
        try:
            await worker
        except Exception:
            pass
    except Exception:
        pass  # erro já tratado dentro de _build_guild_payload_sync (HTTPException)
    finally:
        _cold_load_tasks.pop(key, None)


# ── refresh de guilda/aliança (botão ⟳ do perfil) ──────────────────────────
# Mesmo padrão do /players/{id}/refresh: enfileira no profile_warmer, que busca
# na Albion e grava no GuildProfile/AllianceProfile. Cooldown de 10min, estado
# compartilhado (refresh_requested_at != null = "atualizando" pra todos), retry
# automático em falha. Stages em _refresh_progress com prefixo g:/a:.

REFRESH_COOLDOWN = timedelta(minutes=10)


def _resolve_region_for_guild(db: Session, albion_id: str) -> str | None:
    """Descobre a região de uma guilda: primeiro olha no GuildProfile (se já
    foi aquecida antes), depois cai pro BattleGuild mais recente (a região da
    última batalha onde a guilda apareceu). None se não sabe — o warmer não
    consegue buscar sem saber o host."""
    gp = db.scalar(select(GuildProfile).where(GuildProfile.albion_id == albion_id))
    if gp is not None:
        return gp.region
    return db.scalar(
        select(Battle.region)
        .join(BattleGuild, BattleGuild.battle_id == Battle.id)
        .where(BattleGuild.albion_guild_id == albion_id)
        .order_by(Battle.id.desc())
        .limit(1)
    )


def _resolve_region_for_alliance(db: Session, albion_id: str) -> str | None:
    ap = db.scalar(select(AllianceProfile).where(AllianceProfile.albion_id == albion_id))
    if ap is not None:
        return ap.region
    return db.scalar(
        select(Battle.region)
        .join(BattleGuild, BattleGuild.battle_id == Battle.id)
        .where(BattleGuild.alliance_id == albion_id)
        .order_by(Battle.id.desc())
        .limit(1)
    )


@router.get("/refresh-progress/{entity_type}/{albion_id}")
def get_entity_refresh_progress(entity_type: str, albion_id: str):
    """Etapa do refresh em andamento (stage=null: nada em andamento). Mesmo
    padrão do /players/refresh-progress."""
    from app.services.profile_warmer import _refresh_progress
    prefix = "g:" if entity_type == "guild" else "a:" if entity_type == "alliance" else ""
    return {"stage": _refresh_progress.get(f"{prefix}{albion_id}")}


@router.post("/guilds/{albion_id}/refresh")
async def refresh_guild(albion_id: str, db: Session = Depends(deps.db_session)):
    """Enfileira refresh da guilda no profile_warmer. Cooldown de 5min,
    estado compartilhado entre todos os visitantes."""
    from app.services.profile_warmer import _refresh_progress, request_refresh

    # Confirma que a guilda existe (em BattleGuild ou GuildProfile)
    region = _resolve_region_for_guild(db, albion_id)
    if region is None:
        raise HTTPException(404, "Guild não encontrada")

    gp = db.scalar(select(GuildProfile).where(GuildProfile.albion_id == albion_id))
    if gp is not None and gp.refresh_requested_at is not None:
        return {"queued": True, "refreshing": True, "cooldown_seconds": 0}

    # Sinal de "quando foi atualizado por último" pro cooldown: gp.last_seen_at
    # se a guilda já foi aquecida, senão o fetch de batalha mais recente — o
    # MESMO fallback que _build_guild_payload_sync usa pro "age" mostrado na
    # tela (ver ali). Sem isso, uma guilda nunca aquecida pelo warmer (só
    # vista via battle tracker) mostrava age "agora" na tela mas o cooldown
    # pulava direto por achar gp is None — dava pra pedir refresh de novo na
    # hora, contradizendo o que a própria tela dizia.
    last_signal = gp.last_seen_at if gp is not None else db.scalar(
        select(func.max(Battle.fetched_at))
        .join(BattleGuild, BattleGuild.battle_id == Battle.id)
        .where(BattleGuild.albion_guild_id == albion_id)
    )
    if last_signal is not None:
        elapsed = datetime.now(timezone.utc) - _aware(last_signal)
        if elapsed < REFRESH_COOLDOWN:
            return {"queued": False, "refreshing": False,
                    "cooldown_seconds": int((REFRESH_COOLDOWN - elapsed).total_seconds())}

    if gp is None:
        gp = GuildProfile(albion_id=albion_id, name=albion_id, region=region)
        db.add(gp)
    else:
        gp.region = region
    gp.refresh_requested_at = datetime.now(timezone.utc)
    db.commit()
    _refresh_progress[f"g:{albion_id}"] = "queued"
    request_refresh()
    return {"queued": True, "refreshing": True, "cooldown_seconds": 0}


@router.post("/alliances/{albion_id}/refresh")
async def refresh_alliance(albion_id: str, db: Session = Depends(deps.db_session)):
    """Enfileira refresh da aliança no profile_warmer. Mesmo padrão de
    /guilds/{id}/refresh."""
    from app.services.profile_warmer import _refresh_progress, request_refresh

    region = _resolve_region_for_alliance(db, albion_id)
    if region is None:
        raise HTTPException(404, "Aliança não encontrada")

    ap = db.scalar(select(AllianceProfile).where(AllianceProfile.albion_id == albion_id))
    if ap is not None and ap.refresh_requested_at is not None:
        return {"queued": True, "refreshing": True, "cooldown_seconds": 0}

    # Mesmo fallback do lado guilda — ver comentário em refresh_guild.
    last_signal = ap.last_seen_at if ap is not None else db.scalar(
        select(func.max(Battle.fetched_at))
        .join(BattleGuild, BattleGuild.battle_id == Battle.id)
        .where(BattleGuild.alliance_id == albion_id)
    )
    if last_signal is not None:
        elapsed = datetime.now(timezone.utc) - _aware(last_signal)
        if elapsed < REFRESH_COOLDOWN:
            return {"queued": False, "refreshing": False,
                    "cooldown_seconds": int((REFRESH_COOLDOWN - elapsed).total_seconds())}

    if ap is None:
        ap = AllianceProfile(albion_id=albion_id, name=albion_id, region=region)
        db.add(ap)
    else:
        ap.region = region
    ap.refresh_requested_at = datetime.now(timezone.utc)
    db.commit()
    _refresh_progress[f"a:{albion_id}"] = "queued"
    request_refresh()
    return {"queued": True, "refreshing": True, "cooldown_seconds": 0}


@router.get("/guilds/{albion_id}")
async def guild_profile(albion_id: str, db: Session = Depends(deps.db_session)):
    """Perfil de guilda. Cache-first: se a agregação já foi feita (e está no
    _cold_cache, válida por 5min), serve instantâneo. Senão, dispara task em
    background (asyncio.create_task — sobrevive ao client desconectar) e
    retorna 200 com _cold_load=true; o front faz polling de /load-progress
    até a task terminar e a próxima leitura servir do cache.

    Antes: a agregação rodava dentro da request. Reload na tab cancelava a
    coroutine e o trabalho morria no meio; a nova request recomeçava do zero.
    Agora a task continua, e o _load_progress mostra o stage atual pra
    qualquer request — a barra continua de onde estava."""
    key = f"guild:{albion_id}"
    cached = _cold_cache_get(key, db)
    if cached is not None:
        return _overlay_refresh_state(cached, db, GuildProfile, albion_id)

    # Valida que a guilda existe antes de disparar a task (senão a task roda
    # à toa e o front fica preso na barra).
    bg_exists = db.scalars(
        select(BattleGuild.albion_guild_id)
        .where(BattleGuild.albion_guild_id == albion_id)
        .limit(1)
    ).first()
    if not bg_exists:
        raise HTTPException(404, "Guild não encontrada")

    # Se a última task terminou com timeout, devolve 504 se ainda não passou
    # tempo suficiente pra tentar de novo (evita loop infinito de timeout
    # imediato). Depois de 30s, limpa o erro e deixa disparar nova task.
    _check_cold_timeout(key)

    # Dispara task em background (ou junta-se a uma em andamento).
    task = _cold_load_tasks.get(key)
    if task is None or task.done():
        _cold_load_tasks[key] = asyncio.create_task(_cold_load_guild(albion_id))

    # Retorna stub — o front detecta _cold_load e continua polling.
    return {"_cold_load": True, "albion_id": albion_id}


def _build_alliance_payload_sync(db: Session, albion_id: str) -> dict:
    """Monta o payload completo do perfil de aliança a partir do DB. Síncrono,
    chamado pela task de cold load (em to_thread) e servido do cache pela rota."""
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
        ap = db.scalar(select(AllianceProfile).where(AllianceProfile.albion_id == albion_id))
        _load_progress[key] = (token, "silver")
        kill_fame = _fame_windows(db, c7, c30, alliance_id=albion_id)
        silver_dropped = _silver_windows(db, c7, c30, alliance_id=albion_id)
        battle_windows = _battle_windows(db, c7, c30, alliance_id=albion_id)
        _load_progress[key] = (token, "members")
        # Mesmo alinhamento de default do lado guilda — ver comentário acima.
        guilds = _guilds_in_alliance(db, albion_id, min_players=25, min_kills=5)
        _load_progress[key] = (token, "history")
        battles_count = db.scalar(
            select(func.count(func.distinct(BattleGuild.battle_id)))
            .join(Battle, Battle.id == BattleGuild.battle_id)
            .where(BattleGuild.alliance_id == albion_id, *_LETHAL_BIG)
        ) or 0
        roster_log = _alliance_roster_log(db, albion_id)
        # Primeira página da aba Batalhas — mesmo motivo do lado guilda.
        factions_cache: dict[int, list[dict]] = {}
        page0_battles, page0_total = _battles_alliance(db, albion_id, 0, min_players=25, min_kills=5, factions_cache=factions_cache)
    finally:
        _pop_progress_if_owner(key, token)

    return {
        "albion_id": albion_id,
        "name": bg.alliance_name,
        "last_synced_at": _aware(ap.last_seen_at).isoformat() if ap else (
            _aware(last_synced_at).isoformat() if last_synced_at else None
        ),
        "refresh_requested_at": _aware(ap.refresh_requested_at).isoformat() if ap and ap.refresh_requested_at else None,
        "kills_total": kills_total,
        "deaths_total": deaths_total,
        "kill_fame": kill_fame,
        "silver_dropped": silver_dropped,
        "battles": battle_windows,
        "battles_page0": {
            "battles": page0_battles, "total": page0_total, "page": 0,
            "pages": max(1, -(-page0_total // PAGE_SIZE)),
        },
        "guilds": guilds,
        "battles_count": battles_count,
        "roster_log": roster_log,
    }


async def _cold_load_alliance(albion_id: str) -> None:
    """Faz a agregação pesada do perfil de aliança em background — mesmo
    padrão de _cold_load_guild. Cacheia o resultado em _cold_cache. Timeout
    de COLD_LOAD_TIMEOUT (15min), mesmo padrão."""
    key = f"alliance:{albion_id}"
    worker = asyncio.create_task(asyncio.to_thread(
        _build_payload_with_session, _build_alliance_payload_sync, albion_id,
    ))
    try:
        payload = await asyncio.wait_for(
            asyncio.shield(worker),
            timeout=COLD_LOAD_TIMEOUT.total_seconds(),
        )
        await asyncio.to_thread(_cold_cache_put, key, payload)
    except asyncio.TimeoutError:
        _load_progress[key] = (object(), "error:timeout")
        _cold_timeout_at[key] = datetime.now(timezone.utc)
        try:
            await worker
        except Exception:
            pass
    except Exception:
        pass
    finally:
        _cold_load_tasks.pop(key, None)


@router.get("/alliances/{albion_id}")
async def alliance_profile(albion_id: str, db: Session = Depends(deps.db_session)):
    """Perfil de aliança. Cache-first + cold load em background — mesmo padrão
    de guild_profile. Serve do _cold_cache se válido; senão dispara task em
    background e retorna stub com _cold_load=true."""
    key = f"alliance:{albion_id}"
    cached = _cold_cache_get(key, db)
    if cached is not None:
        return _overlay_refresh_state(cached, db, AllianceProfile, albion_id)

    bg_exists = db.scalars(
        select(BattleGuild.alliance_id)
        .where(BattleGuild.alliance_id == albion_id)
        .limit(1)
    ).first()
    if not bg_exists:
        raise HTTPException(404, "Aliança não encontrada")

    # Se a última task terminou com timeout, devolve 504 se ainda não passou
    # tempo suficiente pra tentar de novo. Mesmo padrão de guild_profile.
    _check_cold_timeout(key)

    task = _cold_load_tasks.get(key)
    if task is None or task.done():
        _cold_load_tasks[key] = asyncio.create_task(_cold_load_alliance(albion_id))

    return {"_cold_load": True, "albion_id": albion_id}


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
