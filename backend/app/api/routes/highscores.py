"""Highscores — rankings gerais (não escopados a guilda) de guildas e
jogadores de Albion: fama PvP, underdog, eficiência, mais batalhas, e
pontuação por arma (ver app.api.routes.players._weapon_points pro sistema de
pontos por função — dps/pierce/support/healer/tank — aplicado aqui em escala,
pra todos os jogadores de uma vez, em vez de um só)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import deps
from app.api.routes.battles import (
    ASSISTS_PER_POINT, HEALING_PER_POINT, SUPPORT_ELIGIBLE_FIGHT_POINTS, TANK_ELIGIBLE_FIGHT_POINTS,
    _wbase, _weapon_function_map, _week_start_utc,
    eligible_guild_battles_subquery, latest_guild_names, lethal_with_healing_filter,
)
from app.models.battles import Battle, BattleGuild, BattleParticipant, BattleSide
from app.models.catalog import Weapon
from app.models.players import AlbionPlayer, PlayerKillEvent, PlayerWeaponStat
from app.services.search_norm import match as search_match, norm_sql, normalize as norm_name

router = APIRouter(prefix="/highscores", tags=["highscores"])

# Armas removidas do jogo mas com histórico em PlayerWeaponStat — não aparecem
# mais no dropdown nem nos rankings de highscores. O histórico bruto permanece
# no DB; só não é mais surfaceado. Adicione a base aqui quando a SBI remover
# outra arma do live.
REMOVED_WEAPON_BASES = {"2H_IRONGAUNTLETS_HELL"}  # "Black Hands" (demonic Iron Gauntlets)

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
    return [r.strip() for r in regions.split(",") if r.strip()]


def _window_start(window: str) -> datetime | None:
    return _week_start_utc() if window == "week" else None


def _base_battle_filters(region_list: list[str] | None, week_start: datetime | None) -> list:
    filters = [Battle.processing_tier == "deep", *lethal_with_healing_filter()]
    if week_start:
        filters.append(Battle.start_time >= week_start)
    if region_list:
        filters.append(Battle.region.in_(region_list))
    return filters


# ── métricas por guilda (fama / eficiência / mais batalhas compartilham a
# mesma agregação base — só mudam o que ordenam) ───────────────────────────

def _guild_base_stats(db: Session, battle_filters: list) -> list[tuple[str, int, float, int]]:
    """(guild_id, fame, avg_players, battles) por guilda, entre as batalhas
    elegíveis que passam em `battle_filters`."""
    eligible_sq = eligible_guild_battles_subquery()
    rows = db.execute(
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
    ).all()
    return [(r.albion_guild_id, int(r.fame or 0), float(r.avg_players or 0), r.battles) for r in rows]


def _underdog_candidates(db: Session, battle_filters: list) -> list[tuple[str, int]]:
    """(guild_id, kills) — soma os kills das vezes que a guilda teve MENOS
    jogadores no próprio lado do que a soma dos outros lados (não-ratos)
    NESSA luta. Ordenado desc."""
    eligible_sq = eligible_guild_battles_subquery()
    rows = db.execute(
        select(BattleGuild.albion_guild_id, BattleGuild.battle_id, BattleGuild.side_id, BattleGuild.kills)
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .join(
            eligible_sq,
            (eligible_sq.c.battle_id == BattleGuild.battle_id) & (eligible_sq.c.guild_id == BattleGuild.albion_guild_id),
        )
        .where(*battle_filters, BattleGuild.side_id.isnot(None))
    ).all()
    if not rows:
        return []

    battle_ids = list({r.battle_id for r in rows})
    sides_by_battle: dict[int, dict[int, tuple[int, bool]]] = {}
    for bid, sid, pc, is_rats in db.execute(
        select(BattleSide.battle_id, BattleSide.id, BattleSide.player_count, BattleSide.is_rats)
        .where(BattleSide.battle_id.in_(battle_ids))
    ):
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

def _bulk_weapon_points(
    db: Session, region_list: list[str] | None, week_start: datetime | None,
    weapon_base: str | None = None,
) -> dict[str, dict[str, int]]:
    """albion_player_id -> {weapon_base: points}.

    All-time (week_start=None) lê de PlayerWeaponStat — contadores brutos
    pré-calculados por app.services.weapon_stats, evita escanear toda
    BattleParticipant/PlayerKillEvent a cada request. "Esta semana" continua
    ao vivo (janela curta, já é rápido).

    `weapon_base` restringe a UMA arma: o ranking `weapon:<base>` só olha essa
    base, então filtrar no SQL (índice em weapon_base) lê alguns milhares de
    linhas em vez das ~214k da tabela inteira. `weapon_scorer` (melhor arma de
    cada jogador) precisa de TODAS, aí vem None."""
    if week_start is None:
        return _bulk_weapon_points_alltime(db, region_list, weapon_base)
    return _bulk_weapon_points_live(db, region_list, week_start, weapon_base)


def _bulk_weapon_points_alltime(
    db: Session, region_list: list[str] | None, weapon_base: str | None = None,
) -> dict[str, dict[str, int]]:
    """Aplica a fórmula de pontos (ver PlayerWeaponStat) por cima dos
    contadores brutos pré-calculados — leitura é só um join + aritmética,
    nada de escanear batalha/kill aqui."""
    weapon_fn = _weapon_function_map(db)
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
    rows = db.execute(query)
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


def _bulk_weapon_points_live(
    db: Session, region_list: list[str] | None, week_start: datetime | None,
    weapon_base: str | None = None,
) -> dict[str, dict[str, int]]:
    """albion_player_id -> {weapon_base: points} — escaneado ao vivo, usado
    só pra "esta semana" (janela curta, ver _bulk_weapon_points)."""
    weapon_fn = _weapon_function_map(db)
    points: dict[str, dict[str, int]] = {}

    kill_filters = [PlayerKillEvent.fame > 0]
    if region_list:
        kill_filters.append(PlayerKillEvent.region.in_(region_list))
    if week_start:
        kill_filters.append(PlayerKillEvent.timestamp >= week_start)
    for albion_id, equip in db.execute(
        select(AlbionPlayer.albion_id, PlayerKillEvent.killer_equipment)
        .join(AlbionPlayer, AlbionPlayer.id == PlayerKillEvent.killer_player_id)
        .where(*kill_filters)
    ):
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
    rw_filters: list = []
    if region_list:
        rw_filters.append(Battle.region.in_(region_list))
    if week_start:
        rw_filters.append(Battle.start_time >= week_start)

    # Select de colunas só (não a entidade ORM inteira) — hidratar ~470k
    # objetos BattleParticipant pra ler 5 campos cada é o maior custo dessa
    # função; isso devolve tuplas crás, bem mais barato em massa.
    bp_rows = db.execute(
        select(
            BattleParticipant.battle_id, BattleParticipant.albion_player_id, BattleParticipant.guild_id,
            BattleParticipant.equipment, BattleParticipant.assists, BattleParticipant.healing_done, BattleParticipant.deaths,
        )
        .join(Battle, Battle.id == BattleParticipant.battle_id)
        .where(*rw_filters, BattleParticipant.equipment.isnot(None))
    ).all()
    if not bp_rows:
        return points

    lethal_healing_ids = set(db.scalars(
        select(Battle.id).where(*rw_filters, *lethal_with_healing_filter())
    ).all())
    eligible_sq = eligible_guild_battles_subquery()
    guild_player_count = {
        (bid, gid): cnt for bid, gid, cnt in db.execute(
            select(eligible_sq.c.battle_id, eligible_sq.c.guild_id, eligible_sq.c.player_count)
            .join(Battle, Battle.id == eligible_sq.c.battle_id)
            .where(*rw_filters)
        )
    }
    deaths_by_battle: dict[int, dict[str, int]] = {}
    for bid, gid, deaths in db.execute(
        select(BattleGuild.battle_id, BattleGuild.albion_guild_id, BattleGuild.deaths)
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .where(*rw_filters)
    ):
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


def _player_info(db: Session, albion_ids: list[str]) -> dict[str, AlbionPlayer]:
    if not albion_ids:
        return {}
    return {p.albion_id: p for p in db.scalars(select(AlbionPlayer).where(AlbionPlayer.albion_id.in_(albion_ids)))}


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

def _gather_ranking(
    db: Session, kind: str, region_list: list[str] | None,
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
    rows = db.execute(q).all()
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


def _silver_ranking(
    db: Session, region_list: list[str] | None,
    search_term: str | None, limit: int, offset: int,
) -> dict:
    """Ranking de prata dropada — SUM(silver_dropped) por vítima em
    player_kill_events. silver_dropped é precificado pelo worker
    silver_dropped (services/silver_dropped.py); sem ele, tudo é NULL.
    Filtra silver_dropped>0 (NULL/0 não entram) e fame>0 (mesmo critério de
    atividade do perfil)."""
    silver_by_player = select(
        PlayerKillEvent.victim_player_id.label("player_id"),
        func.sum(PlayerKillEvent.silver_dropped).label("silver"),
    ) \
        .where(PlayerKillEvent.silver_dropped > 0, PlayerKillEvent.fame > 0) \
        .group_by(PlayerKillEvent.victim_player_id) \
        .subquery()
    filters = []
    if region_list:
        filters.append(AlbionPlayer.region.in_(region_list))
    if search_term:
        filters.append(norm_sql(AlbionPlayer.name).like(f"%{norm_name(search_term)}%"))
    joined = silver_by_player.join(AlbionPlayer, AlbionPlayer.id == silver_by_player.c.player_id)
    total = db.scalar(
        select(func.count()).select_from(joined).where(*filters)
    ) or 0
    page = db.execute(
        select(AlbionPlayer, silver_by_player.c.silver)
        .select_from(joined)
        .where(*filters)
        .order_by(silver_by_player.c.silver.desc(), AlbionPlayer.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "total": total,
        "rows": [
            {**_player_out(player, player.albion_id), "value": int(silver), "rank": offset + i + 1}
            for i, (player, silver) in enumerate(page)
        ],
    }


# ── destaques semanais (4 cards) ────────────────────────────────────────────

@router.get("/highlights")
def highscore_highlights(regions: str | None = None, db: Session = Depends(deps.db_session)):
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
        cached = db.get(DashboardCache, ckey)
        if cached is not None:
            return cached.payload
    return _compute_highlights(db, region_list)


def _compute_highlights(db: Session, region_list: list[str] | None) -> dict:
    week_start = _week_start_utc()
    battle_filters = _base_battle_filters(region_list, week_start)

    guild_stats = _guild_base_stats(db, battle_filters)
    guild_ids = [g[0] for g in guild_stats]
    names = latest_guild_names(db, guild_ids)

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
    underdog_candidates = _underdog_candidates(db, battle_filters)
    if underdog_candidates:
        gid, kills = underdog_candidates[0]
        underdog_names = latest_guild_names(db, [gid])
        underdog = {"guild": _guild_out(gid, underdog_names), "kills": kills}

    weapon_scorer = None
    bulk_points = _bulk_weapon_points(db, region_list, week_start)
    best: tuple[str, str, int] | None = None
    for albion_id, weapons in bulk_points.items():
        if not weapons:
            continue
        wb, pts = max(
            ((w, p) for w, p in weapons.items() if w not in REMOVED_WEAPON_BASES),
            key=lambda kv: kv[1], default=(None, 0),
        )
        if pts > 0 and (best is None or pts > best[2]):
            best = (albion_id, wb, pts)
    if best:
        albion_id, wb, pts = best
        player = _player_info(db, [albion_id]).get(albion_id)
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
def highscore_rankings(
    kind: str,
    regions: str | None = None,
    window: str = "alltime",
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(deps.db_session),
):
    """Servido do precompute de 5min (highscores_cache) para a forma comum —
    seleção de regiões conhecida, sem busca, página dentro do top-N. Busca,
    páginas profundas e combinações raras de região caem no cálculo ao vivo
    (`_compute_rankings`)."""
    from app.models.dashboard_cache import DashboardCache
    from app.services import highscores_cache as hc

    region_list = _region_list(regions)
    search_term = search.strip().lower() if search else None
    ckey = hc.rankings_cache_key(kind, window, region_list)
    if ckey:
        cached = db.get(DashboardCache, ckey)
        if cached is not None:
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

    return _compute_rankings(db, kind, region_list, _window_start(window), search_term, limit, offset)


def _compute_rankings(
    db: Session, kind: str, region_list: list[str] | None,
    week_start: datetime | None, search_term: str | None, limit: int, offset: int,
) -> dict:
    if kind in _GUILD_KINDS:
        battle_filters = _base_battle_filters(region_list, week_start)
        if kind == "underdog":
            candidates = _underdog_candidates(db, battle_filters)
        else:
            guild_stats = _guild_base_stats(db, battle_filters)
            if kind == "pvp_fame":
                candidates = [(g[0], g[1]) for g in guild_stats]
            elif kind == "efficiency":
                candidates = [(g[0], round(g[1] / g[2])) for g in guild_stats if g[2] > 0]
            else:  # most_battles
                candidates = [(g[0], g[3]) for g in guild_stats]
            candidates.sort(key=lambda kv: kv[1], reverse=True)

        names = latest_guild_names(db, [gid for gid, _ in candidates])
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
    if kind in _GATHER_KINDS:
        return _gather_ranking(db, kind, region_list, search_term, limit, offset)
    if kind == _SILVER_KIND:
        return _silver_ranking(db, region_list, search_term, limit, offset)

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
        matching_ids = set(db.scalars(
            select(AlbionPlayer.albion_id).where(norm_sql(AlbionPlayer.name).like(f"%{norm_name(search_term)}%"))
        ).all())
        if not matching_ids:
            return {"total": 0, "rows": []}
    # weapon_base != None restringe a query a essa arma (índice) em vez de
    # computar pontos de todas; weapon_scorer (weapon_base=None) precisa de todas.
    bulk_points = _bulk_weapon_points(db, region_list, week_start, weapon_base)
    candidates3: list[tuple[str, str, int]] = []
    for albion_id, weapons in bulk_points.items():
        if weapon_base is not None:
            pts = weapons.get(weapon_base, 0)
            if pts > 0:
                candidates3.append((albion_id, weapon_base, pts))
        else:
            if not weapons:
                continue
            # weapon_scorer: melhor arma do jogador — ignora bases removidas pra
            # não eleger ninguém "melhor player" com arma que não existe mais.
            wb, pts = max(
                ((w, p) for w, p in weapons.items() if w not in REMOVED_WEAPON_BASES),
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
    players = _player_info(db, [c[0] for _, c in page])
    return {
        "total": total,
        "rows": [
            {**_player_out(players.get(albion_id), albion_id), "weapon_base": wb, "value": pts, "rank": rank}
            for rank, (albion_id, wb, pts) in page
        ],
    }


@router.get("/weapons")
def highscore_weapons(db: Session = Depends(deps.db_session)):
    """Todas as armas do catálogo (base + função) — público, sem auth, pro
    dropdown de rankings por arma do Highscores. Nome/ícone ficam a cargo do
    frontend (albion-items.ts já tem nome localizado + render por base)."""
    seen: dict[str, str | None] = {}
    for item_id, fn in db.execute(select(Weapon.item_id, Weapon.invisible_function)):
        wb = _wbase(item_id)
        if wb and wb not in seen and wb not in REMOVED_WEAPON_BASES:
            seen[wb] = fn
    return [{"weapon_base": wb, "invisible_function": fn} for wb, fn in sorted(seen.items())]
