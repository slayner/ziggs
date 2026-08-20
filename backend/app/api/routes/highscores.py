"""Highscores — rankings gerais (não escopados a guilda) de guildas e
jogadores de Albion: fama PvP, underdog, eficiência, mais batalhas, e
pontuação por arma (ver app.api.routes.players._weapon_points pro sistema de
pontos por função — dps/pierce/support/healer/tank — aplicado aqui em escala,
pra todos os jogadores de uma vez, em vez de um só)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.routes.battles import (
    ASSISTS_PER_POINT, HEALING_PER_POINT, SUPPORT_ELIGIBLE_FIGHT_POINTS, TANK_ELIGIBLE_FIGHT_POINTS,
    _wbase, _weapon_function_map, _week_start_utc,
    eligible_guild_battles_subquery, latest_guild_names, lethal_with_healing_filter,
)
from app.models.battles import Battle, BattleGuild, BattleParticipant, BattleSide
from app.models.catalog import Weapon
from app.models.players import AlbionPlayer, PlayerKillEvent, PlayerWeaponStat
from app.services import season_calendar
from app.services.search_norm import match as search_match, norm_sql, normalize as norm_name

router = APIRouter(prefix="/highscores", tags=["highscores"])

# Armas removidas do jogo mas com histórico em PlayerWeaponStat — não aparecem
# mais no dropdown nem nos rankings de highscores. O histórico bruto permanece
# no DB; só não é mais surfaceado. Adicione a base aqui quando a SBI remover
# outra arma do live.
REMOVED_WEAPON_BASES = {"2H_IRONGAUNTLETS_HELL"}  # "Black Hands" (demonic Iron Gauntlets)

# Battlemounts (invisible_function='battlemount') também não são armas — o
# ranking de highscores é de armas, mounts continuam no catálogo pra comps e
# classificação de batalha (battles.py _classify_role). Carregado do banco uma
# vez e cacheado pra não varrer a tabela a cada request.
_battlemount_bases: set[str] | None = None


async def _excluded_weapon_bases(db: AsyncSession) -> set[str]:
    """Bases que não devem aparecer no ranking de armas: removidas + battlemounts."""
    global _battlemount_bases
    if _battlemount_bases is None:
        rows = await db.scalars(select(Weapon.item_id).where(Weapon.invisible_function == "battlemount"))
        _battlemount_bases = {_wbase(item_id) for item_id in rows if _wbase(item_id)}
    return REMOVED_WEAPON_BASES | _battlemount_bases

# Kinds de jogador que agregam de AlbionPlayer diretamente (não de
# batalha/kill event) — coleta (total + por recurso). "alltime" só (são
# famas acumulativas da conta, não janela semanal). Ranking simples:
# ORDER BY coluna DESC com filtro de região.
_GATHER_KINDS = {
    "gather_total": "gathering_fame",
    "gather_wood": "gather_wood",
    "gather_hide": "gather_hide",
    "gather_ore": "gather_ore",
    "gather_rock": "gather_rock",
    "gather_fiber": "gather_fiber",
    "fishing": "fishing_fame",
    "crafting": "crafting_fame",
}
# silver_dropped agrega de player_kill_events (sum por vítima) — kind próprio.
_SILVER_KIND = "silver_dropped"


def _region_list(regions: str | None) -> list[str] | None:
    if not regions:
        return None
    out = list(dict.fromkeys(r.strip() for r in regions.split(",") if r.strip()))
    if any(r not in season_calendar.REGIONS for r in out):
        raise HTTPException(422, "invalid region")
    return out or None


@dataclass(frozen=True)
class TimeWindow:
    """Bounds de tempo de um ranking window.

    - uniform (alltime/week/month): ``lo``/``hi`` aplicados à coluna de
      timestamp independentemente de região; ambos None = alltime.
    - regional (season/season:N): ``regional`` mapeia região → (lo, hi); os
      starts diferem por região (Americas/Europe 11:00 UTC, Asia 00:00 UTC),
      então a query emite um OR de cláusulas por região. Quando ``regional``
      está setado, ``lo``/``hi`` são ignorados e o filtro de região é dobrado
      nas próprias cláusulas (não há ``region.in_`` separado).
    """
    lo: datetime | None = None
    hi: datetime | None = None
    regional: dict[str, tuple[datetime | None, datetime | None]] | None = None

    @property
    def is_alltime(self) -> bool:
        return self.lo is None and self.hi is None and not self.regional


async def _resolve_window(window: str, region_list: list[str] | None) -> TimeWindow:
    """Resolve um window string (alltime/week/month/season/season:N) em
    bounds concretos. Week = domingo 00:00 UTC, half-open; month = mês
    calendário UTC, half-open; season = corrente por região."""
    now = datetime.now(timezone.utc)
    if window == "week":
        start = _week_start_utc()
        return TimeWindow(lo=start, hi=start + timedelta(days=7))
    if window == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        nextm = (start + timedelta(days=32)).replace(day=1)
        return TimeWindow(lo=start, hi=nextm)
    if window == "season":
        cal = await season_calendar.load_calendar()
        regional = _season_regional(cal, region_list, now=now)
        if len(regional) != len(region_list or season_calendar.REGIONS):
            raise HTTPException(503, "season calendar unavailable")
        return TimeWindow(regional=regional)
    if window.startswith("season:"):
        try:
            n = int(window.split(":", 1)[1])
        except ValueError:
            raise HTTPException(422, "invalid highscore window")
        cal = await season_calendar.load_calendar()
        regional = _season_regional(cal, region_list, season_num=n)
        if len(regional) != len(region_list or season_calendar.REGIONS):
            raise HTTPException(422, "unknown season")
        return TimeWindow(regional=regional)
    if window == "alltime":
        return TimeWindow()
    raise HTTPException(422, "invalid highscore window")


def _season_regional(
    cal: dict[str, dict[int, datetime]],
    region_list: list[str] | None,
    *,
    now: datetime | None = None,
    season_num: int | None = None,
) -> dict[str, tuple[datetime | None, datetime | None]]:
    """{região: (lo, hi)} p/ as regiões selecionadas. ``season_num=None`` →
    season corrente por região; senão a season histórica N. Regiões sem
    bound definido caem fora (não contribuem com cláusula)."""
    regions = region_list or list(season_calendar.REGIONS)
    out: dict[str, tuple[datetime | None, datetime | None]] = {}
    for r in regions:
        if season_num is None:
            n = season_calendar.current_season(cal, r, now or datetime.now(timezone.utc))
            if n is None:
                continue
        else:
            n = season_num
        lo, hi = season_calendar.season_bounds(cal, r, n)
        if lo is None:
            continue
        out[r] = (lo, hi)
    return out


def _time_region_clauses(
    tw: TimeWindow, region_col, time_col, region_list: list[str] | None,
) -> list:
    """Clauses WHERE de tempo + região. Para windows regionais (season) o
    filtro de região vai DENTRO do OR por região (starts diferem) — neste
    caso o caller NÃO deve adicionar ``region.in_`` extra."""
    if tw.regional:
        clauses = []
        for region, (lo, hi) in tw.regional.items():
            parts = [region_col == region]
            if lo is not None:
                parts.append(time_col >= lo)
            if hi is not None:
                parts.append(time_col < hi)
            clauses.append(and_(*parts))
        return [or_(*clauses)] if clauses else []
    out = []
    if tw.lo is not None:
        out.append(time_col >= tw.lo)
    if tw.hi is not None:
        out.append(time_col < tw.hi)
    if region_list:
        out.append(region_col.in_(region_list))
    return out


def _window_marker(tw: TimeWindow) -> str:
    """Identifica os bounds concretos para não servir cache do período anterior."""
    if tw.regional:
        return "|".join(
            f"{region}:{lo.isoformat() if lo else ''}:{hi.isoformat() if hi else ''}"
            for region, (lo, hi) in sorted(tw.regional.items())
        )
    return f"{tw.lo.isoformat() if tw.lo else ''}:{tw.hi.isoformat() if tw.hi else ''}"


def _base_battle_filters(region_list: list[str] | None, tw: TimeWindow) -> list:
    filters = [Battle.processing_tier == "deep", *lethal_with_healing_filter()]
    filters.extend(_time_region_clauses(tw, Battle.region, Battle.start_time, region_list))
    return filters


# ── métricas por guilda (fama / eficiência / mais batalhas compartilham a
# mesma agregação base — só mudam o que ordenam) ───────────────────────────

async def _guild_base_stats(db: AsyncSession, battle_filters: list) -> list[tuple[str, int, float, int]]:
    """(guild_id, fame, avg_players, battles) por guilda, entre as batalhas
    elegíveis que passam em `battle_filters`."""
    eligible_sq = eligible_guild_battles_subquery()
    rows = (await db.execute(
        select(
            BattleGuild.albion_guild_id,
            func.sum(BattleGuild.kill_fame).label("fame"),
            func.avg(eligible_sq.c.player_count).label("avg_players"),
            func.count(func.distinct(BattleGuild.battle_id)).label("battles"),
        )
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .join(
            eligible_sq,
            (eligible_sq.c.battle_id == BattleGuild.battle_id) & (eligible_sq.c.guild_id == BattleGuild.albion_guild_id),
        )
        .where(*battle_filters)
        .group_by(BattleGuild.albion_guild_id)
    )).all()
    return [(r.albion_guild_id, int(r.fame or 0), float(r.avg_players or 0), r.battles) for r in rows]


async def _underdog_candidates(db: AsyncSession, battle_filters: list) -> list[tuple[str, int]]:
    """(guild_id, kills) — soma os kills das vezes que a guilda teve MENOS
    jogadores no próprio lado do que a soma dos outros lados (não-ratos)
    NESSA luta. Ordenado desc."""
    eligible_sq = eligible_guild_battles_subquery()
    rows = (await db.execute(
        select(BattleGuild.albion_guild_id, BattleGuild.battle_id, BattleGuild.side_id, BattleGuild.kills)
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .join(
            eligible_sq,
            (eligible_sq.c.battle_id == BattleGuild.battle_id) & (eligible_sq.c.guild_id == BattleGuild.albion_guild_id),
        )
        .where(*battle_filters, BattleGuild.side_id.isnot(None))
    )).all()
    if not rows:
        return []

    battle_ids = list({r.battle_id for r in rows})
    sides_by_battle: dict[int, dict[int, tuple[int, bool]]] = {}
    for bid, sid, pc, is_rats in (await db.execute(
        select(BattleSide.battle_id, BattleSide.id, BattleSide.player_count, BattleSide.is_rats)
        .where(BattleSide.battle_id.in_(battle_ids))
    )):
        sides_by_battle.setdefault(bid, {})[sid] = (pc, is_rats)

    underdog_kills: dict[str, int] = {}
    for gid, bid, side_id, kills in rows:
        side_map = sides_by_battle.get(bid, {})
        own = side_map.get(side_id)
        if own is None or own[1]:  # sem lado identificado ou é o bucket de ratos
            continue
        other = sum(pc for sid, (pc, is_rats) in side_map.items() if sid != side_id and not is_rats)
        if other > 0 and own[0] < other:
            underdog_kills[gid] = underdog_kills.get(gid, 0) + kills

    return sorted(underdog_kills.items(), key=lambda kv: kv[1], reverse=True)


# ── pontuação por arma, todos os jogadores de uma vez (ver
# routes/players.py _weapon_points pro equivalente single-player — mesmas
# constantes de pontuação, importadas de battles.py) ───────────────────────

async def _bulk_weapon_points(
    db: AsyncSession, region_list: list[str] | None, tw: TimeWindow,
    weapon_base: str | None = None,
) -> dict[str, dict[str, int]]:
    """albion_player_id -> {weapon_base: points}.

    All-time (``tw.is_alltime``) lê de PlayerWeaponStat — contadores brutos
    pré-calculados por app.services.weapon_stats, evita escanear toda
    BattleParticipant/PlayerKillEvent a cada request. Windows com bounds
    (week/month/season) continuam ao vivo (janela delimitada, já é rápido).

    `weapon_base` restringe a UMA arma: o ranking `weapon:<base>` só olha essa
    base, então filtrar no SQL (índice em weapon_base) lê alguns milhares de
    linhas em vez das ~214k da tabela inteira. `weapon_scorer` (melhor arma de
    cada jogador) precisa de TODAS, aí vem None."""
    if tw.is_alltime:
        return await _bulk_weapon_points_alltime(db, region_list, weapon_base)
    return await _bulk_weapon_points_live(db, region_list, tw, weapon_base)


async def _bulk_weapon_points_alltime(
    db: AsyncSession, region_list: list[str] | None, weapon_base: str | None = None,
) -> dict[str, dict[str, int]]:
    """Aplica a fórmula de pontos (ver PlayerWeaponStat) por cima dos
    contadores brutos pré-calculados — leitura é só um join + aritmética,
    nada de escanear batalha/kill aqui."""
    weapon_fn = await _weapon_function_map(db)
    points: dict[str, dict[str, int]] = {}

    # Só faz join com AlbionPlayer quando precisa filtrar região — alguns
    # albion_player_id aparecem em BattleParticipant (não tem FK pra
    # AlbionPlayer) sem nunca terem sido registrados como AlbionPlayer
    # (não passaram pelo player_tracker ainda); um INNER JOIN incondicional
    # apagaria esses jogadores até do recorte "todas as regiões".
    query = select(
        PlayerWeaponStat.albion_player_id, PlayerWeaponStat.weapon_base, PlayerWeaponStat.kills,
        PlayerWeaponStat.pierce_points, PlayerWeaponStat.healer_points,
        PlayerWeaponStat.zero_death_eligible_fights, PlayerWeaponStat.tank_ok_fights,
    )
    if weapon_base is not None:
        query = query.where(PlayerWeaponStat.weapon_base == weapon_base)
    if region_list:
        query = query.join(
            AlbionPlayer, AlbionPlayer.albion_id == PlayerWeaponStat.albion_player_id
        ).where(AlbionPlayer.region.in_(region_list))
    rows = await db.execute(query)
    for albion_id, wb, kills, pierce_points, healer_points, zd_fights, tank_fights in rows:
        role = weapon_fn.get(wb, "dps")
        pts = kills
        if role == "pierce":
            pts += pierce_points
        elif role == "healer":
            pts += healer_points
        elif role == "support":
            pts += zd_fights * SUPPORT_ELIGIBLE_FIGHT_POINTS
        elif role == "tank":
            pts += tank_fights * TANK_ELIGIBLE_FIGHT_POINTS
        points.setdefault(albion_id, {})[wb] = pts
    return points


async def _bulk_weapon_points_live(
    db: AsyncSession, region_list: list[str] | None, tw: TimeWindow,
    weapon_base: str | None = None,
) -> dict[str, dict[str, int]]:
    """albion_player_id -> {weapon_base: points} — escaneado ao vivo, usado
    pra qualquer window com bounds (week/month/season)."""
    weapon_fn = await _weapon_function_map(db)
    points: dict[str, dict[str, int]] = {}

    kill_filters = [PlayerKillEvent.fame > 0]
    kill_filters.extend(_time_region_clauses(tw, PlayerKillEvent.region, PlayerKillEvent.timestamp, region_list))
    for albion_id, equip in (await db.execute(
        select(AlbionPlayer.albion_id, PlayerKillEvent.killer_equipment)
        .join(AlbionPlayer, AlbionPlayer.id == PlayerKillEvent.killer_player_id)
        .where(*kill_filters)
    )):
        wb = _wbase(((equip or {}).get("MainHand") or {}).get("Type"))
        if wb and (weapon_base is None or wb == weapon_base):
            bucket = points.setdefault(albion_id, {})
            bucket[wb] = bucket.get(wb, 0) + 1

    # Ranking de UMA arma dps: dps não ganha nada de participante (o laço
    # abaixo faz `continue` em dps), então as kills já são o total — pula o
    # scan de ~470k participantes inteiro.
    if weapon_base is not None and weapon_fn.get(weapon_base, "dps") == "dps":
        return points

    # Filtros por região/janela aplicados direto via join em Battle — NUNCA via
    # Battle.id.in_(lista enorme), que estoura o limite de parâmetros do
    # SQLite (e degrada Postgres) em bases grandes.
    rw_filters: list = list(_time_region_clauses(tw, Battle.region, Battle.start_time, region_list))

    # Select de colunas só (não a entidade ORM inteira) — hidratar ~470k
    # objetos BattleParticipant pra ler 5 campos cada é o maior custo dessa
    # função; isso devolve tuplas cras, bem mais barato em massa.
    bp_rows = (await db.execute(
        select(
            BattleParticipant.battle_id, BattleParticipant.albion_player_id, BattleParticipant.guild_id,
            BattleParticipant.equipment, BattleParticipant.assists, BattleParticipant.healing_done, BattleParticipant.deaths,
        )
        .join(Battle, Battle.id == BattleParticipant.battle_id)
        .where(*rw_filters, BattleParticipant.equipment.isnot(None))
    )).all()
    if not bp_rows:
        return points

    lethal_healing_ids = set((await db.scalars(
        select(Battle.id).where(*rw_filters, *lethal_with_healing_filter())
    )).all())
    eligible_sq = eligible_guild_battles_subquery()
    guild_player_count = {
        (bid, gid): cnt for bid, gid, cnt in (await db.execute(
            select(eligible_sq.c.battle_id, eligible_sq.c.guild_id, eligible_sq.c.player_count)
            .join(Battle, Battle.id == eligible_sq.c.battle_id)
            .where(*rw_filters)
        ))
    }
    deaths_by_battle: dict[int, dict[str, int]] = {}
    for bid, gid, deaths in (await db.execute(
        select(BattleGuild.battle_id, BattleGuild.albion_guild_id, BattleGuild.deaths)
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .where(*rw_filters)
    )):
        deaths_by_battle.setdefault(bid, {})[gid] = deaths

    for battle_id, albion_player_id, guild_id, equipment, assists, healing_done, deaths in bp_rows:
        if not equipment:
            continue  # SQL "equipment IS NOT NULL" não pega JSON null nem lista vazia
        wb = _wbase((equipment[0] or {}).get("weapon"))
        if not wb or (weapon_base is not None and wb != weapon_base):
            continue
        role = weapon_fn.get(wb, "dps")
        if role == "dps":
            continue  # já coberto inteiramente pelas kills (PlayerKillEvent)
        bucket = points.setdefault(albion_player_id, {})

        if role == "pierce":
            bucket[wb] = bucket.get(wb, 0) + assists // ASSISTS_PER_POINT
            continue
        if role == "healer":
            bucket[wb] = bucket.get(wb, 0) + int(healing_done // HEALING_PER_POINT)
            continue

        eligible = battle_id in lethal_healing_ids and (battle_id, guild_id) in guild_player_count
        if not eligible or deaths != 0:
            continue
        if role == "support":
            bucket[wb] = bucket.get(wb, 0) + SUPPORT_ELIGIBLE_FIGHT_POINTS
        elif role == "tank":
            deaths_map = deaths_by_battle.get(battle_id, {})
            own_deaths = deaths_map.get(guild_id, 0)
            other_deaths = sum(d for g, d in deaths_map.items() if g != guild_id)
            if own_deaths <= other_deaths:
                bucket[wb] = bucket.get(wb, 0) + TANK_ELIGIBLE_FIGHT_POINTS

    return points


async def _player_info(db: AsyncSession, albion_ids: list[str]) -> dict[str, AlbionPlayer]:
    if not albion_ids:
        return {}
    return {p.albion_id: p for p in (await db.scalars(select(AlbionPlayer).where(AlbionPlayer.albion_id.in_(albion_ids))))}


def _guild_out(gid: str, names: dict[str, tuple[str, str | None]]) -> dict:
    name, alliance = names.get(gid, (gid, None))
    return {"albion_guild_id": gid, "name": name, "alliance_name": alliance}


def _player_out(p: AlbionPlayer | None, albion_id: str) -> dict:
    if p is None:
        return {"albion_id": albion_id, "name": albion_id, "region": None, "guild_name": None, "alliance_name": None}
    return {
        "albion_id": p.albion_id, "name": p.name, "region": p.region,
        "guild_name": p.guild_name, "alliance_name": p.alliance_name,
    }


# ── rankings de coleta (gather_total + por recurso) e silver_dropped ────────
# Agregam de AlbionPlayer/PlayerKillEvent diretamente, sem batalha — "alltime"
# só (famas acumulativas da conta, não janela semanal).

async def _gather_ranking(
    db: AsyncSession, kind: str, region_list: list[str] | None,
    search_term: str | None, limit: int, offset: int,
) -> dict:
    """Ranking de coleta por recurso — ORDER BY coluna DESC em albion_players.
    Busca por nome filtra antes (subquery), em vez de carregar tudo e filtrar
    em Python (são ~100k jogadores)."""
    col_name = _GATHER_KINDS[kind]
    col = getattr(AlbionPlayer, col_name)
    q = select(AlbionPlayer.albion_id, AlbionPlayer.name, AlbionPlayer.region,
               AlbionPlayer.guild_name, AlbionPlayer.alliance_name, col) \
        .where(col > 0)
    if region_list:
        q = q.where(AlbionPlayer.region.in_(region_list))
    if search_term:
        q = q.where(norm_sql(AlbionPlayer.name).like(f"%{norm_name(search_term)}%"))
    q = q.order_by(col.desc())
    rows = (await db.execute(q)).all()
    total = len(rows)
    page = rows[offset:offset + limit]
    return {
        "total": total,
        "rows": [
            {
                "albion_id": r.albion_id, "name": r.name, "region": r.region,
                "guild_name": r.guild_name, "alliance_name": r.alliance_name,
                "value": int(getattr(r, col_name) or 0), "rank": offset + i + 1,
            }
            for i, r in enumerate(page)
        ],
    }


async def _silver_ranking(
    db: AsyncSession, region_list: list[str] | None, tw: TimeWindow,
    search_term: str | None, limit: int, offset: int,
) -> dict:
    """Ranking de prata dropada — SUM(silver_dropped) por vítima em
    player_kill_events. silver_dropped é precificado pelo worker
    silver_dropped (services/silver_dropped.py); sem ele, tudo é NULL.
    Filtra silver_dropped>0 (NULL/0 não entram) e fame>0 (mesmo critério de
    atividade do perfil). Suporta windows: bounds vão no timestamp do evento
    (region-by-region pra season, já que starts diferem)."""
    silver_where = [PlayerKillEvent.silver_dropped > 0, PlayerKillEvent.fame > 0]
    silver_where.extend(_time_region_clauses(tw, PlayerKillEvent.region, PlayerKillEvent.timestamp, region_list))
    silver_by_player = select(
        PlayerKillEvent.victim_player_id.label("player_id"),
        func.sum(PlayerKillEvent.silver_dropped).label("silver"),
    ) \
        .where(*silver_where) \
        .group_by(PlayerKillEvent.victim_player_id) \
        .subquery()
    filters = []
    if region_list:
        filters.append(AlbionPlayer.region.in_(region_list))
    if search_term:
        filters.append(norm_sql(AlbionPlayer.name).like(f"%{norm_name(search_term)}%"))
    joined = silver_by_player.join(AlbionPlayer, AlbionPlayer.id == silver_by_player.c.player_id)
    total = (await db.scalar(
        select(func.count()).select_from(joined).where(*filters)
    )) or 0
    page = (await db.execute(
        select(AlbionPlayer, silver_by_player.c.silver)
        .select_from(joined)
        .where(*filters)
        .order_by(silver_by_player.c.silver.desc(), AlbionPlayer.id)
        .offset(offset)
        .limit(limit)
    )).all()
    return {
        "total": total,
        "rows": [
            {**_player_out(player, player.albion_id), "value": int(silver), "rank": offset + i + 1}
            for i, (player, silver) in enumerate(page)
        ],
    }


# ── destaques semanais (4 cards) ────────────────────────────────────────────

@router.get("/highlights")
async def highscore_highlights(regions: str | None = None, db: AsyncSession = Depends(deps.async_db_session)):
    """Os 4 destaques do topo da página Highscores — sempre semanais (reseta
    domingo 00:00 UTC), mesmo critério de elegibilidade do ranking semanal de
    fama (battles.battle_highlights).

    Servido do precompute de 5min (highscores_cache) quando a seleção de
    regiões é uma das comuns; combinações fora disso caem no cálculo ao vivo."""
    from app.models.dashboard_cache import DashboardCache
    from app.services import highscores_cache as hc

    region_list = _region_list(regions)
    ckey = hc.highlights_cache_key(region_list)
    if ckey:
        cached = await db.get(DashboardCache, ckey)
        if cached is not None:
            return cached.payload
    return await _compute_highlights(db, region_list)


async def _compute_highlights(db: AsyncSession, region_list: list[str] | None) -> dict:
    week_start = _week_start_utc()
    # Highlights continuam semanais (reseta domingo 00:00 UTC) — não mudam com
    # o seletor de window. Upper bound = próximo domingo 00:00 UTC: como
    # agora < próximo domingo, nenhuma batalha corrente é excluída (half-open).
    tw = TimeWindow(lo=week_start, hi=week_start + timedelta(days=7))
    battle_filters = _base_battle_filters(region_list, tw)

    guild_stats = await _guild_base_stats(db, battle_filters)
    guild_ids = [g[0] for g in guild_stats]
    names = await latest_guild_names(db, guild_ids)

    efficiency = None
    most_battles = None
    if guild_stats:
        eff_gid, eff_fame, eff_avg, _ = max(
            (g for g in guild_stats if g[2] > 0), key=lambda g: g[1] / g[2], default=(None, 0, 0, 0)
        )
        if eff_gid:
            efficiency = {"guild": _guild_out(eff_gid, names), "fame_per_player": round(eff_fame / eff_avg)}
        mb_gid, _, _, mb_battles = max(guild_stats, key=lambda g: g[3])
        most_battles = {"guild": _guild_out(mb_gid, names), "battles": mb_battles}

    underdog = None
    underdog_candidates = await _underdog_candidates(db, battle_filters)
    if underdog_candidates:
        gid, kills = underdog_candidates[0]
        underdog_names = await latest_guild_names(db, [gid])
        underdog = {"guild": _guild_out(gid, underdog_names), "kills": kills}

    weapon_scorer = None
    bulk_points = await _bulk_weapon_points(db, region_list, tw)
    excluded = await _excluded_weapon_bases(db)
    best: tuple[str, str, int] | None = None
    for albion_id, weapons in bulk_points.items():
        if not weapons:
            continue
        wb, pts = max(
            ((w, p) for w, p in weapons.items() if w not in excluded),
            key=lambda kv: kv[1], default=(None, 0),
        )
        if pts > 0 and (best is None or pts > best[2]):
            best = (albion_id, wb, pts)
    if best:
        albion_id, wb, pts = best
        player = (await _player_info(db, [albion_id])).get(albion_id)
        weapon_scorer = {"player": _player_out(player, albion_id), "weapon_base": wb, "points": pts}

    return {
        "week_start": week_start.isoformat(),
        "underdog": underdog,
        "weapon_scorer": weapon_scorer,
        "efficiency": efficiency,
        "most_battles": most_battles,
    }


# ── listas paginadas ────────────────────────────────────────────────────────

_GUILD_KINDS = {"pvp_fame", "underdog", "efficiency", "most_battles"}
# Kinds que suportam toggle de escopo jogador↔guilda no frontend.
# pvp_fame/most_battles são guilda por padrão → scope=player agrega por BattleParticipant.
# crafting é jogador por padrão → scope=guild soma AlbionPlayer.crafting_fame por guild_name.
_PLAYER_SCOPE_KINDS = {"pvp_fame", "most_battles"}
_GUILD_SCOPE_KINDS = {"crafting"}


async def _player_kill_fame_rankings(
    db: AsyncSession, region_list: list[str] | None, tw: TimeWindow,
    search_term: str | None, limit: int, offset: int,
) -> dict:
    """scope=player pvp_fame: SUM(fame) over player_kill_events by killer.

    Counts ALL PvP fame from the kill feed (every kill seen by the global
    tracker), not just battle fame. This includes 1v1s, ganks and small
    fights that never become tracked battles. Supports time windows via the
    event timestamp, like _silver_ranking does.
    """
    fame_where = [PlayerKillEvent.fame > 0]
    fame_where.extend(_time_region_clauses(tw, PlayerKillEvent.region, PlayerKillEvent.timestamp, region_list))
    fame_by_killer = select(
        PlayerKillEvent.killer_player_id.label("player_id"),
        func.sum(PlayerKillEvent.fame).label("fame"),
    ) \
        .where(*fame_where) \
        .group_by(PlayerKillEvent.killer_player_id) \
        .subquery()
    filters = []
    if region_list:
        filters.append(AlbionPlayer.region.in_(region_list))
    if search_term:
        filters.append(norm_sql(AlbionPlayer.name).like(f"%{norm_name(search_term)}%"))
    joined = fame_by_killer.join(AlbionPlayer, AlbionPlayer.id == fame_by_killer.c.player_id)
    total = (await db.scalar(
        select(func.count()).select_from(joined).where(*filters)
    )) or 0
    page = (await db.execute(
        select(AlbionPlayer, fame_by_killer.c.fame)
        .select_from(joined)
        .where(*filters)
        .order_by(fame_by_killer.c.fame.desc(), AlbionPlayer.id)
        .offset(offset)
        .limit(limit)
    )).all()
    return {
        "total": total,
        "rows": [
            {**_player_out(player, player.albion_id), "value": int(fame), "rank": offset + i + 1}
            for i, (player, fame) in enumerate(page)
        ],
    }


async def _player_battle_rankings(
    db: AsyncSession, kind: str, battle_filters: list,
    search_term: str | None, limit: int, offset: int,
) -> dict:
    """scope=player para pvp_fame/most_battles: agrega BattleParticipant."""
    if kind == "pvp_fame":
        val_expr = func.sum(BattleParticipant.kill_fame)
    else:  # most_battles
        val_expr = func.count(func.distinct(BattleParticipant.battle_id))
    rows = (await db.execute(
        select(BattleParticipant.albion_player_id, val_expr.label("val"))
        .join(Battle, Battle.id == BattleParticipant.battle_id)
        .where(*battle_filters)
        .group_by(BattleParticipant.albion_player_id)
    )).all()
    candidates = [(r.albion_player_id, int(r.val or 0)) for r in rows if r.val]
    candidates.sort(key=lambda kv: kv[1], reverse=True)
    ranked = list(enumerate(candidates, start=1))
    if search_term:
        matching_ids = set((await db.scalars(
            select(AlbionPlayer.albion_id).where(norm_sql(AlbionPlayer.name).like(f"%{norm_name(search_term)}%"))
        )).all())
        if not matching_ids:
            return {"total": 0, "rows": []}
        ranked = [(rank, c) for rank, c in ranked if c[0] in matching_ids]
    total = len(ranked)
    page = ranked[offset:offset + limit]
    players = await _player_info(db, [c[0] for _, c in page])
    return {
        "total": total,
        "rows": [
            {**_player_out(players.get(aid), aid), "value": v, "rank": rank}
            for rank, (aid, v) in page
        ],
    }


async def _guild_gather_rankings(
    db: AsyncSession, kind: str, region_list: list[str] | None,
    search_term: str | None, limit: int, offset: int,
) -> dict:
    """scope=guild para crafting/gather: soma fama de AlbionPlayer por guild_name."""
    col_name = _GATHER_KINDS[kind]
    col = getattr(AlbionPlayer, col_name)
    q = (
        select(
            AlbionPlayer.guild_name,
            func.max(AlbionPlayer.alliance_name).label("alliance"),
            func.sum(col).label("val"),
        )
        .where(col > 0, AlbionPlayer.guild_name.isnot(None), AlbionPlayer.guild_name != "")
    )
    if region_list:
        q = q.where(AlbionPlayer.region.in_(region_list))
    q = q.group_by(AlbionPlayer.guild_name)
    rows = (await db.execute(q)).all()
    candidates = [(r.guild_name, r.alliance, int(r.val or 0)) for r in rows if r.val]
    candidates.sort(key=lambda kv: kv[2], reverse=True)
    ranked = list(enumerate(candidates, start=1))
    if search_term:
        ranked = [(rank, c) for rank, c in ranked if search_match(search_term, c[0])]
    total = len(ranked)
    page = ranked[offset:offset + limit]
    return {
        "total": total,
        "rows": [
            {"albion_guild_id": name, "name": name, "alliance_name": alliance, "value": v, "rank": rank}
            for rank, (name, alliance, v) in page
        ],
    }


def _filter_cached_rows(kind: str, rows: list[dict], search_term: str) -> list[dict]:
    """Filtra as linhas JÁ cacheadas (que carregam o `rank` global) por nome —
    mesma semântica do ramo de busca de `_compute_rankings`, mas sem recomputar
    a agregação ao vivo. Guilda casa nome OU aliança (search_match, tolera
    typo); jogador casa substring do nome normalizado (espelha
    `norm_sql(name).like('%norm%')` do caminho ao vivo)."""
    if kind in _GUILD_KINDS:
        return [
            r for r in rows
            if search_match(search_term, r.get("name") or "")
            or search_match(search_term, r.get("alliance_name") or "")
        ]
    nq = norm_name(search_term)
    return [r for r in rows if nq in norm_name(r.get("name") or "")]


@router.get("/rankings")
async def highscore_rankings(
    kind: str,
    regions: str | None = None,
    window: str = "alltime",
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    scope: str = "default",
    db: AsyncSession = Depends(deps.async_db_session),
):
    """Servido do precompute de 5min (highscores_cache) para a forma comum —
    seleção de regiões conhecida, sem busca, página dentro do top-N. Busca,
    páginas profundas, combinações raras de região e scope não-default caem
    no cálculo ao vivo (`_compute_rankings`)."""
    from app.models.dashboard_cache import DashboardCache
    from app.services import highscores_cache as hc

    region_list = _region_list(regions)
    search_term = search.strip().lower() if search else None
    if kind in _GATHER_KINDS and window != "alltime":
        raise HTTPException(422, "this ranking only supports alltime")
    if window not in {"alltime", "week", "month", "season"} and not window.startswith("season:"):
        raise HTTPException(422, "invalid highscore window")
    tw = await _resolve_window(window, region_list)
    # scope não-default bypassa cache (precompute só cobre default), EXCETO
    # player-scope pvp_fame que tem precompute próprio (a query live aggregate
    # 4M+ kill events e demora 40-80s).
    cache_kind = kind
    if scope == "player" and kind == "pvp_fame":
        cache_kind = "pvp_fame_player"
    ckey = hc.rankings_cache_key(cache_kind, window, region_list) if cache_kind != kind or scope == "default" else None
    if ckey:
        cached = await db.get(DashboardCache, ckey)
        if cached is not None and cached.payload.get("_window") == _window_marker(tw):
            rows = cached.payload.get("rows", [])
            total = cached.payload.get("total", len(rows))
            if search_term is None:
                # Página dentro do top-N cacheado, OU cache completo (guilda —
                # ranking inteiro cabe): serve do cache. Página funda de jogador
                # (fora do top-N) cai pro ao vivo.
                if offset + limit <= hc.TOP_N or total <= len(rows):
                    return {"total": total, "rows": rows[offset:offset + limit]}
            else:
                # Busca por nome bypassava o cache e recomputava a agregação
                # inteira ao vivo (~5-12s). O cache já traz o rank GLOBAL de cada
                # linha, então filtrar o top-N cacheado devolve o mesmo com rank
                # certo, na hora.
                matched = _filter_cached_rows(kind, rows, search_term)
                if matched:
                    return {"total": len(matched), "rows": matched[offset:offset + limit]}
                # Sem match no top-N. Se o cache é COMPLETO (total == linhas —
                # rankings de guilda têm poucas centenas e cabem inteiros no
                # TOP_N), não há match em lugar nenhum → vazio na hora. Se é
                # truncado (jogador: ~100k+), pode haver match mais fundo → cai
                # pro ao vivo (que ainda corta cedo quando o nome não casa).
                if total <= len(rows):
                    return {"total": 0, "rows": []}

    return await _compute_rankings(db, kind, region_list, tw, search_term, limit, offset, scope)


async def _compute_rankings(
    db: AsyncSession, kind: str, region_list: list[str] | None,
    tw: TimeWindow, search_term: str | None, limit: int, offset: int,
    scope: str = "default",
) -> dict:
    # scope=player em kinds de guilda de batalha.
    if scope == "player" and kind in _PLAYER_SCOPE_KINDS:
        if kind == "pvp_fame":
            # Player PvP fame counts ALL kills (1v1, gank, small fights), not
            # just battle fame. Guilds stay battle-filtered above.
            return await _player_kill_fame_rankings(db, region_list, tw, search_term, limit, offset)
        battle_filters = _base_battle_filters(region_list, tw)
        return await _player_battle_rankings(db, kind, battle_filters, search_term, limit, offset)

    if kind in _GUILD_KINDS:
        battle_filters = _base_battle_filters(region_list, tw)
        if kind == "underdog":
            candidates = await _underdog_candidates(db, battle_filters)
        else:
            guild_stats = await _guild_base_stats(db, battle_filters)
            if kind == "pvp_fame":
                candidates = [(g[0], g[1]) for g in guild_stats]
            elif kind == "efficiency":
                candidates = [(g[0], round(g[1] / g[2])) for g in guild_stats if g[2] > 0]
            else:  # most_battles
                candidates = [(g[0], g[3]) for g in guild_stats]
            candidates.sort(key=lambda kv: kv[1], reverse=True)

        names = await latest_guild_names(db, [gid for gid, _ in candidates])
        # Posição no ranking COMPLETO, não na lista já filtrada por busca —
        # senão a 1ª guilda que sobra depois de buscar por nome sempre virava
        # "#1", como se fosse ranking próprio dela em vez da posição real
        # entre todas as guildas.
        ranked = list(enumerate(candidates, start=1))
        if search_term:
            ranked = [
                (rank, (gid, v)) for rank, (gid, v) in ranked
                if search_match(search_term, names.get(gid, (gid, None))[0] or "")
                or search_match(search_term, names.get(gid, (None, None))[1] or "")
            ]
        total = len(ranked)
        page = ranked[offset:offset + limit]
        return {"total": total, "rows": [{**_guild_out(gid, names), "value": v, "rank": rank} for rank, (gid, v) in page]}

    # ── kinds de coleta: agregação direta em albion_players ──────────────
    # gather/fishing/crafting são famas acumulativas da conta (sem timestamp)
    # — ignoram ``tw`` e são honestamente all-time só.
    if kind in _GATHER_KINDS:
        if scope == "guild":
            return await _guild_gather_rankings(db, kind, region_list, search_term, limit, offset)
        return await _gather_ranking(db, kind, region_list, search_term, limit, offset)
    if kind == _SILVER_KIND:
        return await _silver_ranking(db, region_list, tw, search_term, limit, offset)

    # kinds de jogador+arma: "weapon_scorer" (melhor arma de cada jogador,
    # qualquer arma) ou "weapon:<base>" (um arma específica)
    weapon_base = kind.split(":", 1)[1] if kind.startswith("weapon:") else None
    # Arma removida do jogo: dropdown já não lista; chamada direta à rota com
    # kind=weapon:<base removida> devolve lista vazia em vez de ranking fantasma.
    if weapon_base in REMOVED_WEAPON_BASES:
        return {"total": 0, "rows": []}
    # Busca por nome: resolve os ids ANTES do _bulk_weapon_points (caro). Nome
    # que não casa ninguém (typo, jogador fora do highscore) devolve vazio na
    # hora, sem escanear PlayerWeaponStat inteiro. Via SQL (não .in_(lista
    # enorme) — o candidato all-time tem ~100k jogadores distintos, muito além
    # do teto de parâmetros do SQLite); cruzado com os candidatos em Python.
    matching_ids: set[str] | None = None
    if search_term:
        matching_ids = set((await db.scalars(
            select(AlbionPlayer.albion_id).where(norm_sql(AlbionPlayer.name).like(f"%{norm_name(search_term)}%"))
        )).all())
        if not matching_ids:
            return {"total": 0, "rows": []}
    # weapon_base != None restringe a query a essa arma (índice) em vez de
    # computar pontos de todas; weapon_scorer (weapon_base=None) precisa de todas.
    bulk_points = await _bulk_weapon_points(db, region_list, tw, weapon_base)
    excluded = await _excluded_weapon_bases(db)
    candidates3: list[tuple[str, str, int]] = []
    for albion_id, weapons in bulk_points.items():
        if weapon_base is not None:
            pts = weapons.get(weapon_base, 0)
            if pts > 0:
                candidates3.append((albion_id, weapon_base, pts))
        else:
            if not weapons:
                continue
            # weapon_scorer: melhor arma do jogador — ignora bases excluídas
            # (removidas do jogo + battlemounts) pra não eleger ninguém "melhor
            # player" com arma que não existe ou com mount.
            wb, pts = max(
                ((w, p) for w, p in weapons.items() if w not in excluded),
                key=lambda kv: kv[1], default=(None, 0),
            )
            if pts > 0:
                candidates3.append((albion_id, wb, pts))
    candidates3.sort(key=lambda c: c[2], reverse=True)

    # Posição no ranking COMPLETO, não na lista já filtrada por busca — mesmo
    # motivo do ramo de guildas acima.
    ranked3 = list(enumerate(candidates3, start=1))
    if matching_ids is not None:
        ranked3 = [(rank, c) for rank, c in ranked3 if c[0] in matching_ids]
    total = len(ranked3)
    page = ranked3[offset:offset + limit]
    players = await _player_info(db, [c[0] for _, c in page])
    return {
        "total": total,
        "rows": [
            {**_player_out(players.get(albion_id), albion_id), "weapon_base": wb, "value": pts, "rank": rank}
            for rank, (albion_id, wb, pts) in page
        ],
    }


@router.get("/seasons")
async def highscore_seasons(regions: str | None = None):
    """Metadados pro seletor de season do Highscores: season corrente POR
    REGIÃO e seasons históricas válidas pras regiões selecionadas.

    Season corrente resolve região-por-região, então na transição curta
    (Asia virou N+1 às 00:00 UTC, Americas/Europe ainda viram às 11:00 UTC)
    as regiões podem reportar seasons diferentes — o frontend mostra "Esta
    season" (alias) e os bounds são resolvidos por região na rota de
    rankings, sem atribuir bounds errados. Seasons históricas excluem
    qualquer season corrente (não duplica no seletor)."""
    region_list = _region_list(regions)
    cal = await season_calendar.load_calendar()
    now = datetime.now(timezone.utc)
    selected = region_list or list(season_calendar.REGIONS)
    current = {r: season_calendar.current_season(cal, r, now) for r in selected}
    current_vals = {v for v in current.values() if v is not None}
    # Seasons presentes em TODAS as regiões selecionadas — uma season histórica
    # só faz sentido se todas as regiões selecionadas têm start definido.
    common: set[int] = set(cal.get(selected[0], {})) if selected else set()
    for r in selected[1:]:
        common &= set(cal.get(r, {}))
    # < min(current) exclui qualquer season que AINDA é corrente em alguma
    # região (durante a transição), pra não duplicar "Esta season" no histórico.
    min_current = min(current_vals) if current_vals else None
    historical = sorted(
        (n for n in common if min_current is not None and n < min_current),
        reverse=True,
    )
    return {"current_seasons": current, "historical_seasons": historical}


@router.get("/weapons")
async def highscore_weapons(db: AsyncSession = Depends(deps.async_db_session)):
    """Todas as armas do catálogo (base + função) — público, sem auth, pro
    dropdown de rankings por arma do Highscores. Nome/ícone ficam a cargo do
    frontend (albion-items.ts já tem nome localizado + render por base).

    Battlemounts (invisible_function='battlemount') são excluídas — o ranking é
    de ARMAS, não de mounts. Elas continuam no catálogo pra comps/classificação
    de batalha (ver battles.py _classify_role), só não aparecem no dropdown."""
    excluded = await _excluded_weapon_bases(db)
    seen: dict[str, str | None] = {}
    for item_id, fn in (await db.execute(select(Weapon.item_id, Weapon.invisible_function))):
        wb = _wbase(item_id)
        if wb and wb not in seen and wb not in excluded:
            seen[wb] = fn
    return [{"weapon_base": wb, "invisible_function": fn} for wb, fn in sorted(seen.items())]
