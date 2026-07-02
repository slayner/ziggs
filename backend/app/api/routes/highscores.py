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

router = APIRouter(prefix="/highscores", tags=["highscores"])


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

def _bulk_weapon_points(db: Session, region_list: list[str] | None, week_start: datetime | None) -> dict[str, dict[str, int]]:
    """albion_player_id -> {weapon_base: points}.

    All-time (week_start=None) lê de PlayerWeaponStat — contadores brutos
    pré-calculados por app.services.weapon_stats, evita escanear toda
    BattleParticipant/PlayerKillEvent a cada request. "Esta semana" continua
    ao vivo (janela curta, já é rápido)."""
    if week_start is None:
        return _bulk_weapon_points_alltime(db, region_list)
    return _bulk_weapon_points_live(db, region_list, week_start)


def _bulk_weapon_points_alltime(db: Session, region_list: list[str] | None) -> dict[str, dict[str, int]]:
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


def _bulk_weapon_points_live(db: Session, region_list: list[str] | None, week_start: datetime | None) -> dict[str, dict[str, int]]:
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
        if wb:
            bucket = points.setdefault(albion_id, {})
            bucket[wb] = bucket.get(wb, 0) + 1

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
        if not wb:
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


# ── destaques semanais (4 cards) ────────────────────────────────────────────

@router.get("/highlights")
def highscore_highlights(regions: str | None = None, db: Session = Depends(deps.db_session)):
    """Os 4 destaques do topo da página Highscores — sempre semanais (reseta
    domingo 00:00 UTC), mesmo critério de elegibilidade do ranking semanal de
    fama (battles.battle_highlights)."""
    region_list = _region_list(regions)
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
        wb, pts = max(weapons.items(), key=lambda kv: kv[1])
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
    region_list = _region_list(regions)
    week_start = _window_start(window)
    search_term = search.strip().lower() if search else None

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
                if search_term in names.get(gid, (gid, None))[0].lower()
                or (names.get(gid, (None, None))[1] or "").lower().find(search_term) != -1
            ]
        total = len(ranked)
        page = ranked[offset:offset + limit]
        return {"total": total, "rows": [{**_guild_out(gid, names), "value": v, "rank": rank} for rank, (gid, v) in page]}

    # kinds de jogador+arma: "weapon_scorer" (melhor arma de cada jogador,
    # qualquer arma) ou "weapon:<base>" (um arma específica)
    weapon_base = kind.split(":", 1)[1] if kind.startswith("weapon:") else None
    bulk_points = _bulk_weapon_points(db, region_list, week_start)
    candidates3: list[tuple[str, str, int]] = []
    for albion_id, weapons in bulk_points.items():
        if weapon_base is not None:
            pts = weapons.get(weapon_base, 0)
            if pts > 0:
                candidates3.append((albion_id, weapon_base, pts))
        else:
            if not weapons:
                continue
            wb, pts = max(weapons.items(), key=lambda kv: kv[1])
            if pts > 0:
                candidates3.append((albion_id, wb, pts))
    candidates3.sort(key=lambda c: c[2], reverse=True)

    # Posição no ranking COMPLETO, não na lista já filtrada por busca — mesmo
    # motivo do ramo de guildas acima.
    ranked3 = list(enumerate(candidates3, start=1))
    if search_term:
        # Filtra pelo nome via SQL (não Battle.id.in_(lista enorme) — o
        # candidato all-time pode ter ~100k jogadores distintos, muito além
        # do limite de parâmetros do SQLite) e cruza com os candidatos em Python.
        matching_ids = set(db.scalars(
            select(AlbionPlayer.albion_id).where(func.lower(AlbionPlayer.name).like(f"%{search_term}%"))
        ).all())
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
        if wb and wb not in seen:
            seen[wb] = fn
    return [{"weapon_base": wb, "invisible_function": fn} for wb, fn in sorted(seen.items())]
