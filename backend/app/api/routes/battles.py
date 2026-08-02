"""Rotas públicas de battle tracker — sem escopar por guilda."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api import deps
from app.models.battles import Battle, BattleGuild, BattleKillEvent, BattleParticipant, BattleSide
from app.models.catalog import Weapon
from app.models.dashboard_cache import DashboardCache
from app.models.players import PlayerCountSnapshot, PlayerKillEvent
from app.models.prices import GoldPriceSnapshot
from app.services import battle_groups, battle_sides, battle_tracker, prices
from app.services.battle_preview import render_battle_preview
from app.services.player_activity import active_player_count
from app.services.search_norm import norm_sql, normalize as norm_name

router = APIRouter(prefix="/battles", tags=["battles"])

# Linhas de battlemount reais do Albion — o item id varia por tier/skin
# (ex.: "UNIQUE_MOUNT_SILVER_BATTLE_RHINO"), então o match é por substring
# do nome, não igualdade exata de base id.
BATTLEMOUNT_NAMES = [
    "BATTLE_RHINO", "ANCIENT_ENT", "BATTLE_EAGLE", "BEHEMOTH", "COLOSSUS_BEETLE",
    "GOLIATH_HORSEEATER", "JUGGERNAUT", "PHALANX_BEETLE", "ROVING_BASTION",
    "TOWER_CHARIOT", "SIEGE_BALLISTA", "FLAME_BASILISK", "VENOM_BASILISK",
    "COMMAND_MAMMOTH",
]


def _wbase(item_id: str | None) -> str | None:
    """Remove prefixo de tier (T4_..T8_) e sufixo de encantamento (@1..@3)."""
    if not item_id:
        return None
    base = item_id.split("@")[0]
    parts = base.split("_", 1)
    if len(parts) == 2 and parts[0].startswith("T") and parts[0][1:].isdigit():
        base = parts[1]
    return base


# ponytail: catálogo Weapon é global, ~1000 linhas, só muda em seed (raro).
# _build_profile_payload chama isto 2x por request (linha 411 + dentro de
# _top_weapons), e cada abertura de perfil faz mais 2 — sem cache, toda
# visita a perfil de jogador ativo re-escaneava a tabela inteira. TTL curto
# em memória cobre as 2 chamadas da mesma request e a rajada de 60s do
# PROFILE_REFRESH_MS do front (re-render que re-busca o perfil).
_WEAPON_FN_TTL = 60.0
_weapon_fn_cache: dict[str, str] | None = None
_weapon_fn_expires_at: float = 0.0


def _weapon_function_map(db: Session) -> dict[str, str]:
    global _weapon_fn_cache, _weapon_fn_expires_at
    now = time.monotonic()
    if _weapon_fn_cache is not None and now < _weapon_fn_expires_at:
        return _weapon_fn_cache
    out: dict[str, str] = {}
    for item_id, fn in db.execute(select(Weapon.item_id, Weapon.invisible_function)).all():
        if fn:
            out[_wbase(item_id)] = fn
    _weapon_fn_cache = out
    _weapon_fn_expires_at = now + _WEAPON_FN_TTL
    return out


def _as_builds(equipment) -> list[dict]:
    """Normaliza pro formato lista-de-builds atual — batalhas processadas antes
    do multi-build gravaram `equipment` como um dict único no banco."""
    if not equipment:
        return []
    if isinstance(equipment, list):
        return equipment
    return [equipment]


def _is_battlemount(mount_id: str | None) -> bool:
    base = _wbase(mount_id)
    if not base:
        return False
    upper = base.upper()
    return any(name in upper for name in BATTLEMOUNT_NAMES)


def _classify_role(builds: list[dict] | None, weapon_fn: dict[str, str]) -> str | None:
    """Classifica pela build PREDOMINANTE entre as distintas vistas no jogador.
    Antes bastava 1 snapshot com montaria de combate (ex.: montou um Rhino só
    pra fechar 1 kill no fim da luta) pra virar "battlemount" pra batalha
    inteira, sobrepondo a build real usada na maior parte da luta — daí a
    composição da batalha ficava dominada por "BM" sem sentido."""
    if not builds:
        return None
    counts: dict[str, int] = {}
    for b in builds:
        if _is_battlemount(b.get("mount")):
            role = "battlemount"
        else:
            fn = weapon_fn.get(_wbase(b.get("weapon")))
            if not fn:
                continue
            role = "dps" if fn.startswith("dps") else fn  # tank | healer | support | pierce
        counts[role] = counts.get(role, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _aware(dt: datetime) -> datetime:
    # SQLite não preserva tz-awareness na leitura mesmo com DateTime(timezone=True).
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_BIG_GUILD_PLAYER_CAP = 5  # com mais de 4 guildas na luta, esconde guildas "zerg" maiores que isso


def _factions_summary(db: Session, battle_id: int) -> list[dict]:
    """Resumo combinado das guildas/alianças que lutaram (exclui o bucket de
    ratos), ordenado por kills desc — usado pra etiqueta centralizada da
    listagem (substitui o antigo "X vs Y" de contagem de jogadores)."""
    real_side_ids = db.scalars(
        select(BattleSide.id).where(BattleSide.battle_id == battle_id, BattleSide.is_rats == False)
    ).all()
    if not real_side_ids:
        return []

    guilds = db.scalars(
        select(BattleGuild).where(
            BattleGuild.battle_id == battle_id, BattleGuild.side_id.in_(real_side_ids)
        )
    ).all()
    if not guilds:
        return []

    player_counts = dict(
        db.execute(
            select(BattleParticipant.guild_id, func.count(BattleParticipant.id))
            .where(BattleParticipant.battle_id == battle_id, BattleParticipant.guild_id.isnot(None))
            .group_by(BattleParticipant.guild_id)
        ).all()
    )

    return _aggregate_factions(guilds, player_counts)


def _aggregate_factions(
    guilds: list[BattleGuild], player_counts: dict[str, int],
) -> list[dict]:
    """Núcleo de _factions_summary: agrega guildas por aliança e aplica o corte
    de _BIG_GUILD_PLAYER_CAP. Separado pra _factions_summary_bulk reusar com
    dados pré-carregados em 1 query (3 round-trips pra N batalhas → 3 fixo)."""
    # Agrupa por aliança (guildas sem aliança ficam cada uma no seu próprio
    # grupo) — senão uma aliança com várias guildas na luta aparece repetida
    # na bracket, uma vez por guilda, com kills/heatmap fragmentados.
    agg: dict[str, dict] = {}
    for g in guilds:
        key = g.alliance_id or f"g:{g.albion_guild_id}"
        row = agg.get(key)
        pc = player_counts.get(g.albion_guild_id, 0)
        if row is None:
            agg[key] = {
                "guild_id": g.albion_guild_id,
                "guild_name": g.guild_name,
                "alliance_name": g.alliance_name,
                "kills": g.kills,
                "player_count": pc,
            }
        else:
            row["kills"] += g.kills
            row["player_count"] += pc
    rows = list(agg.values())

    if len(rows) > 4:
        # Só aplica o corte se sobrar alguém — numa ZvZ legítima de centenas de
        # jogadores é normal TODA guilda passar de _BIG_GUILD_PLAYER_CAP, e
        # zerar a lista inteira fazia a bracket cair pro fallback de cluster
        # (e mostrar "Zona desconhecida" mesmo com guildas conhecidas, ver
        # battle 9dug3ue/5673: 358 jogadores, 5 guildas, todas > 5 jogadores).
        # Comparação era invertida (mantinha as facções pequenas/insignificantes e
        # descartava as grandes, que são as que de fato lutaram — ver battle
        # w3vxl9w/2078: NIC com 164 jogadores e HDD com 188 ficaram de fora,
        # sobrando só "New heliansheng" com 4 jogadores e 2 kills).
        filtered = [r for r in rows if r["player_count"] > _BIG_GUILD_PLAYER_CAP]
        if filtered:
            rows = filtered

    rows.sort(key=lambda r: r["kills"], reverse=True)
    return rows


def _factions_summary_bulk(db: Session, battle_ids: list[int]) -> dict[int, list[dict]]:
    """Versão em lote de _factions_summary: 3 queries totais pra N batalhas,
    em vez de 3 por batalha. Pra 10 batalhas na página: 30 queries → 3.

    Retorna dict[battle_id, factions]. Batalhas sem sides reais / sem guildas
    devolvem []. Mesma semântica de _factions_summary (exclui is_rats, corta
    zergs > 4 facções, ordena por kills desc)."""
    ids = [b for b in battle_ids if b]
    if not ids:
        return {}
    # 1 query: sides reais (não-ratos) por batalha.
    real_sides = db.execute(
        select(BattleSide.id, BattleSide.battle_id).where(
            BattleSide.battle_id.in_(ids), BattleSide.is_rats == False
        )
    ).all()
    sides_by_battle: dict[int, list[int]] = {}
    for side_id, bid in real_sides:
        sides_by_battle.setdefault(bid, []).append(side_id)
    if not sides_by_battle:
        return {bid: [] for bid in ids}

    # 2 query: guildas desses sides. Filtra por battle_id (INDEXADO) e mantém
    # só os sides reais em Python — battle_guilds.side_id NÃO tem índice, então
    # WHERE side_id IN (...) varria a tabela INTEIRA (~160ms por chamada, em
    # toda busca global e listagem de batalha, não só aqui). battle_id já é
    # indexado e a lista de ids já está em mãos; o resultado é idêntico.
    real_side_ids = {sid for sides in sides_by_battle.values() for sid in sides}
    guilds = db.scalars(
        select(BattleGuild).where(BattleGuild.battle_id.in_(ids))
    ).all()
    guilds_by_battle: dict[int, list[BattleGuild]] = {}
    for g in guilds:
        if g.side_id in real_side_ids:
            guilds_by_battle.setdefault(g.battle_id, []).append(g)

    # 3 query: contagem de jogadores por guilda, agrupada por (battle, guild).
    player_counts_rows = db.execute(
        select(
            BattleParticipant.battle_id, BattleParticipant.guild_id,
            func.count(BattleParticipant.id),
        )
        .where(
            BattleParticipant.battle_id.in_(ids), BattleParticipant.guild_id.isnot(None)
        )
        .group_by(BattleParticipant.battle_id, BattleParticipant.guild_id)
    ).all()
    # Chave (battle_id, guild_id) → count.
    pc_by_battle: dict[int, dict[str, int]] = {}
    for bid, gid, cnt in player_counts_rows:
        pc_by_battle.setdefault(bid, {})[gid] = cnt

    out: dict[int, list[dict]] = {}
    for bid in ids:
        g_list = guilds_by_battle.get(bid, [])
        if not g_list:
            out[bid] = []
            continue
        out[bid] = _aggregate_factions(g_list, pc_by_battle.get(bid, {}))
    return out


@router.get("/active-players")
def active_players(db: Session = Depends(deps.db_session)):
    """Jogadores distintos (kill ou morte) nos últimos 7 dias, por região +
    global, comparado com os 7 dias anteriores. A janela anterior é
    recalculada do ledger (PlayerKillEvent), mas o HISTÓRICO ponto-a-ponto
    pro gráfico vem de PlayerCountSnapshot (ver active_players_history
    abaixo) — o ledger não guarda "quantos estavam ativos às 14h de terça".

    Servido do precompute de 15min (player_count_snapshot grava CARDS_KEY) —
    antes eram 8 scans do ledger a cada abertura do dashboard. Fallback ao vivo
    até o 1º snapshot existir."""
    from app.services.player_count_snapshot import CARDS_KEY

    cached = db.get(DashboardCache, CARDS_KEY)
    if cached is not None:
        return cached.payload

    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    prev_start = now - timedelta(days=14)

    def stat(region: str | None) -> dict:
        current = active_player_count(db, region, week_start, now)
        previous = active_player_count(db, region, prev_start, week_start)
        delta_pct = round((current - previous) / previous * 100) if previous else None
        return {"current": current, "previous": previous, "delta_pct": delta_pct}

    return {
        "americas": stat("americas"),
        "europe": stat("europe"),
        "asia": stat("asia"),
        "global": stat(None),
    }


_ACTIVE_HISTORY_RANGE_DAYS = {"1m": 31, "6m": 183, "1y": 366}
_ACTIVE_HISTORY_REGIONS = ("global", "americas", "europe", "asia")


@router.get("/active-players/history")
def active_players_history(range: str = "6m", db: Session = Depends(deps.db_session)):
    """Série histórica pro gráfico do dashboard — pontos coletados a cada
    15min por services/player_count_snapshot.py. `collected_since` ignora o
    filtro de range: é a data do PRIMEIRO snapshot já gravado, pra avisar o
    usuário desde quando a coleta existe mesmo que ele esteja olhando "1 mês"."""
    since_row = db.execute(select(func.min(PlayerCountSnapshot.recorded_at))).scalar_one_or_none()

    q = select(PlayerCountSnapshot.region, PlayerCountSnapshot.count, PlayerCountSnapshot.recorded_at)
    days = _ACTIVE_HISTORY_RANGE_DAYS.get(range)
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        q = q.where(PlayerCountSnapshot.recorded_at >= cutoff)
    q = q.order_by(PlayerCountSnapshot.recorded_at)

    series: dict[str, list[dict]] = {r: [] for r in _ACTIVE_HISTORY_REGIONS}
    for region, count, recorded_at in db.execute(q).all():
        if region in series:
            series[region].append({"t": int(_aware(recorded_at).timestamp() * 1000), "count": count})

    return {
        "collected_since": _aware(since_row).isoformat() if since_row else None,
        "series": series,
    }


_GOLD_RANGE_DAYS = {"1m": 31, "6m": 183, "1y": 366}
_GOLD_REGIONS = ("americas", "europe", "asia")
_GOLD_MAX_POINTS = 400
_GOLD_CACHE_TTL = 600  # 10min — "all" lê ~210k rows (3 regiões × ~70k), não vale recomputar a cada visita
_gold_history_cache: dict[str, tuple[float, dict]] = {}


def _bucket_avg(rows: list[tuple[datetime, int]], max_points: int) -> list[dict]:
    """Downsample por média de bucket — mesmo algoritmo do downsample() do
    frontend (Dashboard.tsx), mas rodando aqui pra não trafegar dezenas de
    milhares de pontos por região no range 'all'."""
    n = len(rows)
    if n <= max_points:
        return [{"t": int(_aware(ts).timestamp() * 1000), "price": price} for ts, price in rows]
    bucket_size = n / max_points
    out: list[dict] = []
    for i in range(max_points):
        start = int(i * bucket_size)
        end = max(start + 1, int((i + 1) * bucket_size))
        bucket = rows[start:end]
        if not bucket:
            continue
        mid_ts = bucket[len(bucket) // 2][0]
        avg_price = round(sum(p for _, p in bucket) / len(bucket))
        out.append({"t": int(_aware(mid_ts).timestamp() * 1000), "price": avg_price})
    return out


@router.get("/gold/history")
def gold_price_history(range: str = "6m", db: Session = Depends(deps.db_session)):
    """Série histórica da cotação prata↔ouro por região — nosso próprio
    backfill (services/gold_price.py), não mais fetch direto do browser pra
    AODP. Cache em memória por range: o range 'all' varre ~210k linhas, não
    vale recomputar a cada visita ao dashboard."""
    now_mono = time.monotonic()
    cached = _gold_history_cache.get(range)
    if cached and (now_mono - cached[0]) < _GOLD_CACHE_TTL:
        return cached[1]

    since_row = db.execute(select(func.min(GoldPriceSnapshot.recorded_at))).scalar_one_or_none()

    q = select(GoldPriceSnapshot.region, GoldPriceSnapshot.recorded_at, GoldPriceSnapshot.price)
    days = _GOLD_RANGE_DAYS.get(range)
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        q = q.where(GoldPriceSnapshot.recorded_at >= cutoff)
    q = q.order_by(GoldPriceSnapshot.region, GoldPriceSnapshot.recorded_at)

    by_region: dict[str, list[tuple[datetime, int]]] = {r: [] for r in _GOLD_REGIONS}
    for region, recorded_at, price in db.execute(q).all():
        if region in by_region:
            by_region[region].append((recorded_at, price))

    payload = {
        "collected_since": _aware(since_row).isoformat() if since_row else None,
        "series": {region: _bucket_avg(rows, _GOLD_MAX_POINTS) for region, rows in by_region.items()},
    }
    _gold_history_cache[range] = (now_mono, payload)
    return payload


def _week_start_utc() -> datetime:
    """Domingo 00:00 UTC mais recente — o ranking semanal de fama reseta
    nesse instante, não é uma janela rolante de 7 dias."""
    now = datetime.now(timezone.utc)
    days_since_sunday = (now.weekday() + 1) % 7  # weekday(): Mon=0..Sun=6
    return (now - timedelta(days=days_since_sunday)).replace(hour=0, minute=0, second=0, microsecond=0)


# Sistema de pontos "invisível" por arma — kills puras favoreciam só dps e
# nunca destacavam tank/suporte/healer/pierce, que carregam a luta sem
# necessariamente fechar abates pessoais. Kills sempre valem 1 ponto pra
# qualquer função; cada função soma um bônus específico. Compartilhado entre
# o destaque de armas do perfil de jogador (routes/players.py _weapon_points)
# e o ranking de "maior pontuador com uma arma" do Highscores.
HEALING_PER_POINT = 500_000        # healer: 1 ponto a cada 500k de cura NA MESMA luta
ASSISTS_PER_POINT = 3              # pierce: 1 ponto a cada 3 assists
SUPPORT_ELIGIBLE_FIGHT_POINTS = 2  # suporte: por luta elegível (ranking semanal) com 0 mortes
TANK_ELIGIBLE_FIGHT_POINTS = 3     # tank: idem, mas só se a guilda não perdeu mais que o time adversário


def lethal_with_healing_filter() -> list:
    """Luta letal (ver Battle.is_lethal em battle_tracker._write_deep_data)
    com cura registrada — de qualquer lado, não precisa ser da própria
    guilda. Reaproveitado pelo ranking semanal de guildas (battle_highlights)
    e pelo sistema de pontos por arma do perfil de jogador (routes/players.py)."""
    return [
        Battle.is_lethal.is_(True),
        Battle.id.in_(select(BattleParticipant.battle_id).where(BattleParticipant.healing_done > 0)),
    ]


def latest_guild_names(db: Session, guild_ids: list[str]) -> dict[str, tuple[str, str | None]]:
    """Nome/aliança mais recentes de cada guilda (podem variar entre batalhas)
    — busca separada da agregação principal porque incluir essas colunas no
    GROUP BY quebra no Postgres sem agregação. Reaproveitado pelo ranking
    semanal de fama e pelos rankings do Highscores."""
    if not guild_ids:
        return {}
    out: dict[str, tuple[str, str | None]] = {}
    for gid, gname, aname in db.execute(
        select(BattleGuild.albion_guild_id, BattleGuild.guild_name, BattleGuild.alliance_name)
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .where(BattleGuild.albion_guild_id.in_(guild_ids))
        .order_by(Battle.id.desc())
    ):
        out.setdefault(gid, (gname, aname))
    return out


def eligible_guild_battles_subquery():
    """(battle_id, guild_id, player_count) onde a guilda teve mais de 15
    jogadores distintos NESSA luta — independe de lado, conta os dois lados
    se a guilda aparecer em ambos (caso raro, mas não custa nada tratar
    certo). Reaproveitado pelo ranking semanal de guildas e pelo sistema de
    pontos por arma do perfil de jogador (routes/players.py)."""
    return (
        select(
            BattleParticipant.battle_id,
            BattleParticipant.guild_id,
            func.count(func.distinct(BattleParticipant.albion_player_id)).label("player_count"),
        )
        .where(BattleParticipant.guild_id.isnot(None))
        .group_by(BattleParticipant.battle_id, BattleParticipant.guild_id)
        .having(func.count(func.distinct(BattleParticipant.albion_player_id)) > 15)
        .subquery()
    )


@router.get("/highlights")
def battle_highlights(regions: str | None = None, db: Session = Depends(deps.db_session)):
    """Ranking semanal (reseta domingo 00:00 UTC) de fama PvP por guilda. Só
    conta fama de batalha elegível: a guilda teve mais de 15 jogadores NESSA
    luta (limiar é por guilda, não pela batalha toda), a luta é letal (ver
    Battle.is_lethal em battle_tracker._write_deep_data), e houve cura
    registrada — de qualquer lado, não precisa ser da própria guilda.

    Lido do precompute de 1min (dashboard_cache) — cada guilda pertence a uma
    única região, então mesclar os top-N cacheados de cada região pedida e
    reordenar dá o mesmo resultado da query ao vivo. Cai pra query ao vivo só
    se o cache ainda não esquentou (bem no boot)."""
    from app.services.dashboard_cache import REGIONS
    region_list = [r.strip() for r in regions.split(",") if r.strip()] if regions else list(REGIONS)
    cached = {row.key: row.payload for row in db.execute(
        select(DashboardCache).where(DashboardCache.key.in_([f"highlights:{r}" for r in region_list]))
    ).scalars()}
    if len(cached) == len(region_list):
        merged: dict[str, dict] = {}
        week_start_iso = None
        for payload in cached.values():
            week_start_iso = payload["week_start"]
            for g in payload["guilds"]:
                merged[g["albion_guild_id"]] = g
        guilds = sorted(merged.values(), key=lambda g: g["fame"], reverse=True)[:10]
        return {"week_start": week_start_iso or _week_start_utc().isoformat(), "guilds": guilds}

    week_start = _week_start_utc()

    battle_filters = [
        Battle.processing_tier == "deep",
        Battle.start_time >= week_start,
        *lethal_with_healing_filter(),
    ]
    if regions:
        battle_filters.append(Battle.region.in_([r.strip() for r in regions.split(",") if r.strip()]))

    eligible_guild_battles = eligible_guild_battles_subquery()

    fame_rows = db.execute(
        select(
            BattleGuild.albion_guild_id,
            func.sum(BattleGuild.kill_fame).label("fame"),
            func.avg(eligible_guild_battles.c.player_count).label("avg_players"),
        )
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .join(
            eligible_guild_battles,
            (eligible_guild_battles.c.battle_id == BattleGuild.battle_id)
            & (eligible_guild_battles.c.guild_id == BattleGuild.albion_guild_id),
        )
        .where(*battle_filters)
        .group_by(BattleGuild.albion_guild_id)
        .order_by(func.sum(BattleGuild.kill_fame).desc())
        .limit(10)
    ).all()
    if not fame_rows:
        return {"week_start": week_start.isoformat(), "guilds": []}

    guild_ids = [r.albion_guild_id for r in fame_rows]
    fame_by_id = {r.albion_guild_id: int(r.fame or 0) for r in fame_rows}
    avg_players_by_id = {r.albion_guild_id: round(r.avg_players or 0) for r in fame_rows}

    latest_by_id = latest_guild_names(db, guild_ids)

    return {
        "week_start": week_start.isoformat(),
        "guilds": [
            {
                "albion_guild_id": gid,
                "name": latest_by_id.get(gid, (gid, None))[0],
                "alliance_name": latest_by_id.get(gid, (gid, None))[1],
                "fame": fame_by_id[gid],
                "avg_players": avg_players_by_id[gid],
            }
            for gid in guild_ids
        ],
    }


@router.get("")
def list_battles(
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    date_from: str | None = None,  # "YYYY-MM-DD", UTC
    date_to: str | None = None,  # "YYYY-MM-DD", UTC, inclusive
    min_players: int = 5,
    min_kills: int = 5,
    regions: str | None = None,  # CSV de Battle.region (americas/europe/asia) — servidor(es) escolhido(s) nas configurações
    db: Session = Depends(deps.db_session),
):
    """Lista de batalhas — só consulta a nossa base, nunca a API do Albion.
    Os 3 filtros (data, jogadores mínimos, kills mínimas) sempre se combinam
    (AND), com busca por guilda/aliança/jogador/zona por cima."""
    from app.services.dashboard_cache import DEFAULT_MIN_KILLS, DEFAULT_MIN_PLAYERS

    # Formato "cacheável" = exatamente o que o BattlesCard do dashboard pede
    # (sem busca/filtro de data, primeira página, filtros default). Precompute
    # de 1min (dashboard_cache) evita recomputar isso a cada visita.
    cacheable = (
        offset == 0 and not search and not date_from and not date_to
        and min_players == DEFAULT_MIN_PLAYERS and min_kills == DEFAULT_MIN_KILLS
    )
    if cacheable:
        cached_row = db.get(DashboardCache, "recent_battles")
        if cached_row is not None:
            payload = cached_row.payload
            # ponytail: defesa contra payload chegar como JSON-texto cru
            # (coluna JSON devolve objeto, mas se algum dia vier str, parseia).
            if isinstance(payload, str):
                payload = json.loads(payload)
            # back-compat: payload já foi uma lista crua — dict novo carrega
            # rows + counts por região (ver dashboard_cache.refresh_recent_battles).
            if isinstance(payload, dict):
                rows = payload.get("rows", [])
                counts = payload.get("counts", {}) or {}
            else:
                rows = payload
                counts = {}
            wanted_regions = {r.strip() for r in regions.split(",") if r.strip()} if regions else None
            if wanted_regions is not None:
                rows = [r for r in rows if r["region"] in wanted_regions]
            # total REAL que bate com o que o path DB retorna pra offset>0 —
            # sem isso a paginação via cache via total=len(slice)≈10 e só
            # mostrava a página 0 das ~12k batalhas.
            if counts:
                regions_for_total = wanted_regions if wanted_regions is not None else set(counts)
                total = sum(counts.get(r, 0) for r in regions_for_total)
            else:
                total = len(rows)
            rows = rows[:limit]
            return {"battles": rows, "total": total}

    q = select(Battle)

    if regions:
        q = q.where(Battle.region.in_([r.strip() for r in regions.split(",") if r.strip()]))

    if date_from:
        # Comparado direto contra Battle.start_time (string naive UTC no SQLite,
        # ver _aware) — sem tzinfo aqui de propósito, pra não desalinhar o formato.
        q = q.where(Battle.start_time >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.where(Battle.start_time < datetime.fromisoformat(date_to) + timedelta(days=1))

    if min_kills > 0:
        q = q.where(Battle.kill_count >= min_kills)

    # Letalidade é checada por morte (não pela fama agregada — ver
    # Battle.is_lethal): só desqualifica batalha deep-processada onde alguma
    # vítima equipada dropou fama 0, prova de zona não letal (duelo/arena).
    # Batalha "light" (sem eventos de kill pra checar) fica de fora dessa
    # checagem, default True.
    q = q.where(Battle.is_lethal.is_(True))

    if min_players > 0:
        # "jogadores mínimos" = alguma guilda da batalha tinha pelo menos N
        # jogadores — não o total de jogadores da batalha (Battle.players_total).
        big_guild_battle_ids = (
            select(BattleParticipant.battle_id)
            .where(BattleParticipant.guild_id.isnot(None))
            .group_by(BattleParticipant.battle_id, BattleParticipant.guild_id)
            .having(func.count(BattleParticipant.id) >= min_players)
        )
        q = q.where(Battle.id.in_(big_guild_battle_ids))

    if search:
        nq = norm_name(search)
        term = f"%{nq}%"
        guild_battle_ids = select(BattleGuild.battle_id).where(
            or_(norm_sql(BattleGuild.guild_name).like(term), norm_sql(BattleGuild.alliance_name).like(term))
        )
        player_battle_ids = select(BattleParticipant.battle_id).where(norm_sql(BattleParticipant.name).like(term))
        matching_battle_ids = set(db.scalars(guild_battle_ids).all()) | set(db.scalars(player_battle_ids).all())
        q = q.where(or_(Battle.id.in_(matching_battle_ids), Battle.cluster.ilike(f"%{search}%")))

    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    battles = db.scalars(q.order_by(Battle.start_time.desc()).limit(limit).offset(offset)).all()

    # Toda batalha que aparece no feed ganha um link público (se ainda não tiver)
    # — em lote, um commit só pra página inteira, não um por batalha (ver
    # get_or_create_groups_bulk).
    groups = battle_groups.get_or_create_groups_bulk(db, [b.id for b in battles])

    out = []
    for b in battles:
        group = groups[b.id]
        out.append({
            "public_id": group.public_id,
            "region": b.region,
            "start_time": _aware(b.start_time).isoformat(),
            "end_time": _aware(b.end_time).isoformat() if b.end_time else None,
            "total_fame": b.total_fame,
            "kill_count": b.kill_count,
            "cluster": b.cluster,
            "players_total": b.players_total,
            "is_zvz": b.is_zvz,
            "factions": _factions_summary(db, b.id),
        })

    return {"battles": out, "total": total}


def _combined_detail(db: Session, battle_ids: list[int], public_id: str) -> dict:
    """Visão de detalhe — recalcula lados ao vivo a partir dos eventos das
    batalhas (1 ou várias combinadas), pra ficar correto mesmo quando o
    grupo junta mais de uma KB."""
    battles = db.scalars(select(Battle).where(Battle.id.in_(battle_ids))).all()
    if not battles:
        raise HTTPException(404, "Batalha não encontrada")
    battles.sort(key=lambda b: _aware(b.start_time))

    participants = db.scalars(
        select(BattleParticipant).where(BattleParticipant.battle_id.in_(battle_ids))
    ).all()

    merged: dict[str, dict] = {}
    pid_to_player: dict[int, str] = {}
    for p in participants:
        pid_to_player[p.id] = p.albion_player_id
        m = merged.get(p.albion_player_id)
        if m is None:
            merged[p.albion_player_id] = m = {
                "albion_player_id": p.albion_player_id, "name": p.name,
                "guild_id": p.guild_id, "guild_name": p.guild_name,
                "alliance_id": p.alliance_id, "alliance_name": p.alliance_name,
                "kills": 0, "deaths": 0, "kill_fame": 0, "death_fame": 0,
                "damage_dealt": 0.0, "damage_taken": 0.0, "healing_done": 0.0,
                "ip": 0.0, "equipment": [],
            }
        m["name"], m["guild_id"], m["guild_name"] = p.name, p.guild_id, p.guild_name
        m["alliance_id"], m["alliance_name"] = p.alliance_id, p.alliance_name
        m["kills"] += p.kills
        m["deaths"] += p.deaths
        m["kill_fame"] += p.kill_fame
        m["damage_dealt"] += p.damage_dealt
        m["damage_taken"] += p.damage_taken
        m["healing_done"] += p.healing_done
        # AverageItemPower direto da API do Albion (vem no resumo da batalha,
        # ver battle_tracker.py) — pedido explícito pra não tentar recalcular
        # a partir do equipamento. Maior valor entre as batalhas combinadas.
        m["ip"] = max(m["ip"], p.ip)
        for build in _as_builds(p.equipment):
            if build not in m["equipment"]:
                m["equipment"].append(build)

    events = db.scalars(select(BattleKillEvent).where(BattleKillEvent.battle_id.in_(battle_ids))).all()
    kills_between: dict[tuple[str, str], int] = {}
    for ev in events:
        k = pid_to_player.get(ev.killer_participant_id) if ev.killer_participant_id else None
        v = pid_to_player.get(ev.victim_participant_id) if ev.victim_participant_id else None
        if v:
            merged[v]["death_fame"] += ev.fame
        if not k or not v:
            continue
        kf = battle_sides.faction_key(merged[k]["guild_id"], merged[k]["alliance_id"], k)
        vf = battle_sides.faction_key(merged[v]["guild_id"], merged[v]["alliance_id"], v)
        kills_between[(kf, vf)] = kills_between.get((kf, vf), 0) + 1

    factions, player_faction = battle_sides.build_factions(merged)
    analysis = battle_sides.analyze(factions, kills_between)
    weapon_fn = _weapon_function_map(db)

    def p_payload(row: dict, side_label: str) -> dict:
        return {
            "albion_player_id": row["albion_player_id"],
            "name": row["name"],
            "guild_id": row["guild_id"],
            "guild_name": row["guild_name"],
            "alliance_id": row["alliance_id"],
            "alliance_name": row["alliance_name"],
            "side": side_label,
            "kills": row["kills"],
            "deaths": row["deaths"],
            "kill_fame": row["kill_fame"],
            "death_fame": row["death_fame"],
            # AverageItemPower da API do Albion direto — None se a build nunca
            # carregou (sem equipamento) ou se a API não reportou IP nenhum,
            # pra esse jogador não entrar na média de IP da guilda (pedido
            # explícito, ver avgIp no frontend).
            "ip": round(row["ip"], 1) if row["equipment"] and row["ip"] else None,
            "damage_dealt": round(row["damage_dealt"]),
            "damage_taken": round(row["damage_taken"]),
            "healing_done": round(row["healing_done"]),
            "equipment": row["equipment"] or None,
            "role": _classify_role(row["equipment"], weapon_fn),
        }

    kill_timeline = []
    for ev in events:
        k = pid_to_player.get(ev.killer_participant_id) if ev.killer_participant_id else None
        v = pid_to_player.get(ev.victim_participant_id) if ev.victim_participant_id else None
        if not k:
            continue
        row = merged[k]
        v_row = merged.get(v) if v else None
        side_label = analysis.side_of.get(player_faction[k], "rats")
        kill_timeline.append({
            "t": _aware(ev.timestamp).isoformat(),
            "side": side_label,
            "faction": row["alliance_name"] or row["guild_name"] or row["name"],
            "victim_faction": (v_row["alliance_name"] or v_row["guild_name"] or v_row["name"]) if v_row else None,
            # guilda/aliança separadas (faction acima já é o fallback combinado,
            # usado pelo agrupamento do horizonte) — pra lista de mortes mostrar
            # "[aliança] guilda" igual ao resto do site, não só uma das duas.
            "killer_guild": row["guild_name"],
            "killer_alliance": row["alliance_name"],
            "victim_guild": v_row["guild_name"] if v_row else None,
            "victim_alliance": v_row["alliance_name"] if v_row else None,
            "killer": row["name"],
            "victim": v_row["name"] if v_row else None,
            "fame": ev.fame,
            # build exata daquele kill (snapshot do evento), não a agregada do
            # jogador na batalha inteira — mais precisa pro tooltip do horizonte
            # e pra calculadora de valor da build na lista de mortes.
            "weapon": (ev.killer_equipment or {}).get("weapon"),
            "victim_weapon": (ev.victim_equipment or {}).get("weapon"),
            "killer_equipment": ev.killer_equipment,
            "victim_equipment": ev.victim_equipment,
            "killer_inventory": ev.killer_inventory,
            "victim_inventory": ev.victim_inventory,
        })
    kill_timeline.sort(key=lambda e: e["t"])

    sides_out = []
    for label in ("A", "B"):
        if label not in analysis.player_count:
            continue
        members = [merged[pid] for pid in merged if analysis.side_of.get(player_faction[pid]) == label]
        guild_tags: dict[str, dict] = {}
        for m in members:
            key = m["alliance_id"] or m["guild_id"] or m["name"]
            if key not in guild_tags:
                guild_tags[key] = {"guild_id": m["guild_id"], "guild_name": m["guild_name"],
                                    "alliance_name": m["alliance_name"]}
        sides_out.append({
            "label": label,
            "is_rats": False,
            "player_count": analysis.player_count.get(label, 0),
            "score": analysis.score.get(label, 0),
            "factions": list(guild_tags.values()),
            "participants": sorted(
                (p_payload(m, label) for m in members), key=lambda p: p["kills"], reverse=True
            ),
        })

    rat_members = [merged[pid] for pid in merged if analysis.side_of.get(player_faction[pid], "rats") == "rats"]
    rat_payload = sorted((p_payload(m, "rats") for m in rat_members), key=lambda p: p["kills"], reverse=True)

    all_rows = list(merged.values())

    regions = {b.region for b in battles}
    _battle_region = next(iter(regions)) if len(regions) == 1 else "multi"

    def _top(field: str) -> dict | None:
        rows = [r for r in all_rows if r[field]]
        if not rows:
            return None
        row = max(rows, key=lambda r: r[field])
        return {
            "name": row["name"],
            "value": round(row[field]) if isinstance(row[field], float) else row[field],
            "guild_name": row["guild_name"],
            "alliance_name": row["alliance_name"],
            "albion_player_id": row["albion_player_id"],
            "region": _battle_region,
        }

    highlights = {
        "top_kills": _top("kills"),
        "top_fame": _top("kill_fame"),
        "top_healing": _top("healing_done"),
        "top_death_fame": _top("death_fame"),
    }
    clusters = {b.cluster for b in battles if b.cluster}

    return {
        "public_id": public_id,
        "battle_count": len(battles),
        "albion_ids": [b.albion_id for b in battles],
        "region": next(iter(regions)) if len(regions) == 1 else "multi",
        "start_time": _aware(battles[0].start_time).isoformat(),
        "end_time": _aware(battles[-1].end_time).isoformat() if battles[-1].end_time else None,
        "total_fame": sum(b.total_fame for b in battles),
        "kill_count": sum(b.kill_count for b in battles),
        "cluster": next(iter(clusters)) if len(clusters) == 1 else None,
        "players_total": len(merged),
        "sides": sides_out,
        "rats": {"participants": rat_payload, "player_count": len(rat_payload)},
        "highlights": highlights,
        "kill_timeline": kill_timeline,
        # Nicks de quem descobriu estas batalhas via companion (agradecimento).
        "found_by": sorted({b.found_by for b in battles if b.found_by}),
    }


@router.get("/by-code/{public_id}")
def get_battle_by_code(public_id: str, db: Session = Depends(deps.db_session)):
    battle_ids = battle_groups.get_group_battle_ids(db, public_id)
    if not battle_ids:
        raise HTTPException(404, "Link não encontrado")
    return _combined_detail(db, battle_ids, public_id)


@router.get("/prices")
async def get_battle_item_prices(item_ids: str, db: Session = Depends(deps.db_session)):
    """Preço aproximado de itens pra calcular valor de loot em batalhas —
    cacheado pra sempre no banco (ver services.prices.get_battle_prices),
    não bate na API externa de novo depois da primeira consulta de cada item."""
    ids = [i.strip() for i in item_ids.split(",") if i.strip()]
    return await prices.get_battle_prices(db, ids)


@router.post("/resolve")
async def resolve_battles(albion_ids: list[str] = Body(..., embed=True), db: Session = Depends(deps.db_session)):
    """Recebe 1+ IDs crus do Albion (de qualquer região), acha/cria a batalha
    correspondente (na nossa base ou direto na API do Albion) e devolve o
    detalhe já combinado num único link público."""
    ids = list(dict.fromkeys(i.strip() for i in albion_ids if i.strip()))
    if not ids:
        raise HTTPException(400, "Nenhum ID informado")

    resolved: list[Battle] = []
    unresolved: list[str] = []
    async with battle_tracker.make_client() as client:
        for albion_id in ids:
            battle = await battle_tracker.resolve_by_albion_id(client, db, albion_id)
            if battle is not None:
                resolved.append(battle)
            else:
                unresolved.append(albion_id)

    if not resolved:
        raise HTTPException(
            404,
            "Batalha não encontrada — o ID pode estar errado, ou ela é antiga demais e não está mais disponível na API do Albion."
            if len(ids) == 1 else
            "Nenhuma das batalhas informadas foi encontrada — os IDs podem estar errados, ou são antigas demais para a API do Albion.",
        )

    group = battle_groups.get_or_create_group(db, [b.id for b in resolved])
    detail = _combined_detail(db, [b.id for b in resolved], group.public_id)
    # IDs que o usuário colou junto mas não resolveram (não existem mais na API
    # do Albion, geralmente por serem antigos demais) — o frontend ignora e
    # mostra esses como aviso ao lado da batalha combinada que SIM carregou.
    detail["unresolved_ids"] = unresolved
    return detail


@router.post("/merge")
def merge_battles(public_ids: list[str] = Body(..., embed=True), db: Session = Depends(deps.db_session)):
    """Combina 2+ batalhas já conhecidas (pelos public_id mostrados na listagem)
    num único link — usado pelo modo "Multi" da listagem, onde o usuário
    seleciona várias brackets à mão. Sem chamada à API do Albion (diferente
    de /resolve): só remonta os battle_id internos e reusa get_or_create_group."""
    ids = list(dict.fromkeys(i.strip() for i in public_ids if i.strip()))
    if len(ids) < 2:
        raise HTTPException(400, "Selecione pelo menos 2 batalhas")

    battle_ids: list[int] = []
    for public_id in ids:
        members = battle_groups.get_group_battle_ids(db, public_id)
        if not members:
            raise HTTPException(404, f"Batalha não encontrada: {public_id}")
        battle_ids.extend(members)
    battle_ids = list(dict.fromkeys(battle_ids))

    group = battle_groups.get_or_create_group(db, battle_ids)
    return _combined_detail(db, battle_ids, group.public_id)


@router.get("/preview/{public_id}.png")
def battle_preview_png(public_id: str, db: Session = Depends(deps.db_session)):
    """Imagem PNG de resumo da batalha pra embeds do Discord. Cacheada em disco
    (data/battle_preview_cache/) — batalhas são imutáveis, nunca regenera."""
    from fastapi.responses import FileResponse

    if not public_id or len(public_id) > 20:
        raise HTTPException(400, "código inválido")
    path = render_battle_preview(db, public_id)
    if path is None:
        raise HTTPException(404, "batalha não encontrada")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/embed/{public_id}")
def battle_embed_html(public_id: str, db: Session = Depends(deps.db_session)):
    """HTML mínimo com OG tags pra gerar embed no Discord quando alguém cola o
    link da batalha. O Discord crawler lê as meta tags (og:image aponta pro
    /battles/preview/{public_id}.png) e mostra o card com a imagem de resumo.

    meta-refresh redireciona humanos pro frontend (SPA). O Discord não segue
    redirects em OG crawls, então as tags são lidas antes do redirect.

    ponytail: HTML inline em vez de template engine — só 8 linhas de meta
    tags, Jinja2 seria overhead pra isso."""
    from fastapi.responses import HTMLResponse
    from app.config import get_settings

    if not public_id or len(public_id) > 20:
        raise HTTPException(400, "código inválido")
    battle_ids = battle_groups.get_group_battle_ids(db, public_id)
    if not battle_ids:
        raise HTTPException(404, "batalha não encontrada")

    s = get_settings()
    # Garante que o PNG existe (render sob demanda na 1ª vez)
    path = render_battle_preview(db, public_id)

    # Altura real do PNG (dinâmica conforme nº de factions)
    img_h = 250
    if path and path.exists():
        from PIL import Image as PILImage
        with PILImage.open(path) as img:
            img_h = img.height

    # Título: dados básicos da batalha pra mostrar no card do Discord
    b = db.get(Battle, battle_ids[0])
    title = f"{b.players_total} players · {b.kill_count} kills" if b else "Battle"
    if b and b.cluster:
        title += f" · {b.cluster}"
    image_url = f"{s.frontend_url}/battles/preview/{public_id}.png"
    canonical = f"{s.frontend_url}/{public_id}"

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta property="og:title" content="{title}">
<meta property="og:image" content="{image_url}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="600">
<meta property="og:image:height" content="{img_h}">
<meta property="og:url" content="{canonical}">
<meta property="og:type" content="website">
<meta http-equiv="refresh" content="0;url={canonical}">
<title>{title}</title>
</head><body>Redirecting to <a href="{canonical}">{canonical}</a></body></html>"""
    return HTMLResponse(content=html, headers={"Cache-Control": "public, max-age=3600"})
