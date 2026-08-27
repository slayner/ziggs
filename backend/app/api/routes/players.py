"""Rotas de perfis públicos de jogadores de Albion."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.routes.battles import (
    _factions_summary, _factions_summary_bulk, _wbase, _weapon_function_map,
    SUPPORT_ELIGIBLE_FIGHT_POINTS, TANK_ELIGIBLE_FIGHT_POINTS,
)
from app.db import AsyncSessionLocal, SyncSessionLocal
from app.models.battles import Battle, BattleParticipant
from app.models.players import AlbionPlayer, DeletedProfile, PlayerKillEvent, PlayerSnapshot, PlayerWeaponStat, SearchEntry
from app.services import battle_groups, prices, user_profile
from app.services.albion_gate import PROFILE, albion_scope, queue_depth, slot
from app.services.awakened import awakened_value
from app.services.player_tracker import HOSTS, make_client, sync_player_kills, upsert_player
from app.services.profile_warmer import request_refresh
from app.services.search_norm import normalize as norm_name, prefix_range

router = APIRouter(prefix="/players", tags=["players"])


def _aware(dt: datetime) -> datetime:
    # SQLite não preserva tz-awareness na leitura mesmo com DateTime(timezone=True).
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ponytail: user_profile.py ainda sync (próxima migração). AsyncSession não
# cruza pra thread, então sessão sync própria por chamada — aberta/fechada
# dentro do to_thread. Remover quando user_profile migrar.
async def _run_sync(fn, *args):
    """Roda fn(sync_db, *args) numa thread com sessão sync temporária."""
    def _run():
        db = SyncSessionLocal()
        try:
            return fn(db, *args)
        finally:
            db.close()
    return await asyncio.to_thread(_run)


# View cache-first: serve do DB instantâneo, nunca força loading na primeira
# visita se já temos o jogador. O warmer (profile_warmer) re-aquece em
# background com STALE_AFTER=7dias; o botão ⟳ é o refresh manual.
#
# PROFILE_STALE_AFTER controla quando abrir o perfil enfileira um refresh
# automático em background (sem loading — só marca refresh_requested_at e o
# warmer processa). 15 dias: o perfil serve do cache sem reclamar, o usuário
# vê a idade (last_seen_at + "agora"/"5m"/"7d" atrás) e decide se quer
# atualizar com o botão ⟳. Antes era 30min e toda visita enfileirava refresh
# — desperdício de cota da Albion pra um usuário que só quer ver o perfil.
PROFILE_STALE_AFTER = timedelta(days=15)

# Timeout de cold load (primeira visita) e refresh — mesmo valor do
# profile_warmer.PROCESSING_TIMEOUT. Se o fetch na Albion não terminar a
# tempo, o stage vira error:timeout e a task se auto-remove. Tempo em fila
# (esperando slot do albion_gate) não conta — só o tempo DEPOIS de começar.
COLD_LOAD_TIMEOUT = timedelta(minutes=15)


async def _queue_refresh_if_stale(db: AsyncSession, player: AlbionPlayer) -> None:
    if player.refresh_requested_at is not None:
        return  # já enfileirado
    now = datetime.now(timezone.utc)
    stale = now - _aware(player.last_seen_at) > PROFILE_STALE_AFTER
    # Albion às vezes retorna zeros no primeiro fetch — se o perfil tem
    # lifetime_statistics (foi carregado) mas TODAS as famas são 0, provavelmente
    # é um fetch ruim. Enfileira refresh imediatamente em vez de esperar 15 dias.
    all_zero = (
        (player.kill_fame or 0) == 0
        and (player.death_fame or 0) == 0
        and (player.pve_fame or 0) == 0
        and (player.gathering_fame or 0) == 0
        and (player.crafting_fame or 0) == 0
    )
    if stale or all_zero:
        player.refresh_requested_at = now
        await db.commit()


async def _get_with_retry(
    client: httpx.AsyncClient, url: str, *, params: dict | None = None, attempts: int = 3,
) -> httpx.Response:
    """GET com retry pra erro transiente — 429 é comum na busca/perfil por
    nick (usuário digitando gera bastante tráfego de uma vez) e sem isto
    virava 502 pro usuário já na 1ª tentativa. Mesmo padrão de
    battle_tracker._fetch_deep_data: 429 respeita o Retry-After da própria
    API (ou backoff crescente se não vier), timeout com backoff exponencial.
    Devolve a Response mesmo se não for 200 na última tentativa — quem chama
    decide o que fazer com o status (raise_for_status/checagem manual)."""
    for attempt in range(attempts):
        try:
            async with slot(httpx.URL(url).host):
                resp = await client.get(url, params=params)
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(2 ** attempt)
            continue
        if resp.status_code == 429 and attempt < attempts - 1:
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else 5.0 * (attempt + 1)
            await asyncio.sleep(wait)
            continue
        return resp
    raise AssertionError("unreachable")


async def _fetch_player_raw(client: httpx.AsyncClient, host: str, albion_id: str) -> dict | None:
    resp = await _get_with_retry(client, f"https://{host}/api/gameinfo/players/{albion_id}")
    if resp.status_code != 200:
        return None
    data = resp.json()
    return data if isinstance(data, dict) and data.get("Id") else None


async def _battle_history(
    db: AsyncSession, albion_player_id: str, region: str,
    factions_cache: dict[int, list[dict]] | None = None,
) -> list[dict]:
    """Batalhas que entraram em deep-processing (>= DEEP_PROCESS_MIN_PLAYERS
    jogadores, ver battle_tracker.py) onde esse jogador apareceu — lutas
    pequenas/solo não geram BattleParticipant, ficam só no ledger de kills.
    Sem limite: a paginação do perfil (10/página) já aguenta a lista inteira.

    `factions_cache` deduplica `_factions_summary` entre _battle_history e
    _battle_links_bulk — a mesma batalha aparece nas duas (histórico + kills),
    e sem cache cada uma rodava 3 queries (BattleSide + BattleGuild + count).
    """
    rows = (await db.execute(
        select(Battle, BattleParticipant)
        .join(BattleParticipant, BattleParticipant.battle_id == Battle.id)
        .where(BattleParticipant.albion_player_id == albion_player_id, Battle.region == region)
        .order_by(Battle.start_time.desc())
    )).all()
    # Em lote — um commit só pra lista inteira, não um por batalha (pode ser
    # centenas pra jogador ativo desde que DEEP_PROCESS_MIN_PLAYERS virou 0).
    groups = await battle_groups.get_or_create_groups_bulk(db, [battle.id for battle, _ in rows])
    public_ids = {bid: g.public_id for bid, g in groups.items()}
    fc = factions_cache if factions_cache is not None else {}
    # Batch: 3 queries fixas pra todas as batalhas, em vez de 3 por batalha.
    missing_bids = [b.id for b, _ in rows if b.id not in fc]
    if missing_bids:
        fc.update(await _factions_summary_bulk(db, missing_bids))
    out = []
    for battle, bp in rows:
        public_id = public_ids[battle.id]
        out.append({
            "public_id": public_id,
            "region": battle.region,
            "start_time": _aware(battle.start_time).isoformat(),
            "cluster": battle.cluster,
            "is_zvz": battle.is_zvz,
            "players_total": battle.players_total,
            "total_fame": battle.total_fame,
            "kill_count": battle.kill_count,
            # mesmo resumo de facções da listagem de batalhas (ver
            # routes/battles.py _factions_summary) — pro front renderizar
            # com o heatmap igual o bracket, em vez do cluster (sempre nulo).
            "factions": fc.get(battle.id, []),
            "kills": bp.kills, "deaths": bp.deaths, "kill_fame": bp.kill_fame,
        })
    return out


async def _battle_links_bulk(
    db: AsyncSession, region: str, albion_battle_ids: list[str | None],
    factions_cache: dict[int, list[dict]] | None = None,
) -> dict[str, tuple[str | None, list[dict]]]:
    """Versão em lote de resolução de link público + heatmap de facções
    (mesma etiqueta centralizada da listagem de batalhas) pra várias
    battle_id de uma vez, com um commit só (ver
    battle_groups.get_or_create_groups_bulk) — usado pela lista de
    kills/mortes do perfil, que não tem paginação e pode ter centenas de
    eventos pra jogador ativo. Luta "light" (< deep-processing) linka
    normal, só sem heatmap (facções vazias).

    `factions_cache` é compartilhado com _battle_history pra não re-rodar
    _factions_summary pra batalhas que já foram processadas lá."""
    ids = sorted({i for i in albion_battle_ids if i})
    if not ids:
        return {}
    battles = (await db.scalars(select(Battle).where(Battle.region == region, Battle.albion_id.in_(ids)))).all()
    groups = await battle_groups.get_or_create_groups_bulk(db, [b.id for b in battles])
    public_ids = {bid: g.public_id for bid, g in groups.items()}
    fc = factions_cache if factions_cache is not None else {}
    # Batch: 3 queries fixas pra todas as batalhas, em vez de 3 por batalha.
    missing_bids = [b.id for b in battles if b.id not in fc]
    if missing_bids:
        fc.update(await _factions_summary_bulk(db, missing_bids))
    out: dict[str, tuple[str | None, list[dict]]] = {}
    for b in battles:
        out[b.albion_id] = (public_ids[b.id], fc.get(b.id, []))
    return out


def _counts_for_activity(ev: PlayerKillEvent) -> bool:
    """Só mostra na atividade do perfil kills que realmente "valeram" fama —
    filtra duelos/repetições com fama zerada (anti-farm do próprio Albion).

    Tentamos estimar a fama esperada a partir do "Item Value" oculto do set
    da vítima (escala 2^(tier+encantamento), confirmado via wiki) e exigir um
    mínimo relativo a isso — mas validamos contra o próprio ledger e a fama
    real de PvP depende MUITO do poder do matador em relação à vítima (bônus
    de underdog/penalidade de overkill), que não temos como reconstruir. Kills
    legítimas contra gear caro (ex: vítima em T8.4 BiS, fama real 80k) ficavam
    abaixo de qualquer corte razoável de "% do esperado". Sem dado confiável
    pra isso, fama > 0 é o único sinal que não arrisca falso positivo."""
    return ev.fame > 0


async def _kill_ledger_rows(db: AsyncSession, player_id: int, region: str, role: str) -> list[PlayerKillEvent]:
    """Sem limite: a paginação do perfil (10/página) já aguenta a lista inteira."""
    own_col = PlayerKillEvent.killer_player_id if role == "kills" else PlayerKillEvent.victim_player_id
    return list((await db.scalars(
        select(PlayerKillEvent)
        .where(own_col == player_id, PlayerKillEvent.region == region)
        .order_by(PlayerKillEvent.timestamp.desc())
    )).all())


async def _kill_highlights(db: AsyncSession, player: AlbionPlayer) -> dict:
    """Kill mais valiosa e morte mais valiosa do jogador — por silver_dropped
    e por fame. 4 queries O(1) (top 1 com ORDER BY + LIMIT 1), só pra destacar
    no perfil. silver_dropped IS NOT NULL filtra kills ainda não precificadas
    pelo worker (NULL = pendente, não conta)."""
    region = player.region
    own_kill = PlayerKillEvent.killer_player_id
    own_death = PlayerKillEvent.victim_player_id

    async def _top(col, order_col, desc: bool) -> PlayerKillEvent | None:
        q = select(PlayerKillEvent).where(
            col == player.id, PlayerKillEvent.region == region,
            PlayerKillEvent.fame > 0,
        )
        if order_col == PlayerKillEvent.silver_dropped:
            q = q.where(PlayerKillEvent.silver_dropped.is_not(None))
        q = q.order_by(order_col.desc() if desc else order_col.asc())
        return (await db.scalars(q.limit(1))).first()

    async def _serialize(ev: PlayerKillEvent | None, role: str) -> dict | None:
        if ev is None:
            return None
        # Oponente: se role=kills, a vítima; se role=deaths, o matador.
        opp_id = ev.victim_player_id if role == "kills" else ev.killer_player_id
        opp = await db.scalar(select(AlbionPlayer).where(AlbionPlayer.id == opp_id)) if opp_id else None
        return {
            "event_id": ev.albion_event_id,
            "timestamp": _aware(ev.timestamp).isoformat() if ev.timestamp else None,
            "fame": ev.fame,
            "silver_dropped": ev.silver_dropped or 0,
            "is_solo": ev.is_solo,
            "participant_count": ev.participant_count,
            "albion_battle_id": ev.albion_battle_id,
            "opponent": {
                "name": opp.name if opp else None,
                "guild_name": opp.guild_name if opp else None,
                "alliance_name": opp.alliance_name if opp else None,
            } if opp else None,
            "victim_equipment": ev.victim_equipment,
            "killer_equipment": ev.killer_equipment,
        }

    return {
        "kill_by_silver": await _serialize(await _top(own_kill, PlayerKillEvent.silver_dropped, True), "kills"),
        "kill_by_fame": await _serialize(await _top(own_kill, PlayerKillEvent.fame, True), "kills"),
        "death_by_silver": await _serialize(await _top(own_death, PlayerKillEvent.silver_dropped, True), "deaths"),
        "death_by_fame": await _serialize(await _top(own_death, PlayerKillEvent.fame, True), "deaths"),
    }


# Slots que compõem a "identidade" de uma build pro widget de armas mais
# usadas — sem capa (pedido explícito) e sem mount/food/potion (irrelevantes
# pra build de combate).
_BUILD_SLOTS = ("MainHand", "OffHand", "Head", "Armor", "Shoes")


async def _weapon_points(db: AsyncSession, player: AlbionPlayer, by_weapon: dict[str, list[PlayerKillEvent]]) -> dict[str, int]:
    """Pontos por arma — kills (by_weapon, ledger ao vivo) + bônus de função
    lido de PlayerWeaponStat (contadores brutos pré-calculados, ver
    app.services.weapon_stats; mesma fórmula usada no Highscores all-time)."""
    weapon_fn = await _weapon_function_map(db)
    points: dict[str, int] = {wb: len(evs) for wb, evs in by_weapon.items()}

    stats = (await db.scalars(
        select(PlayerWeaponStat).where(PlayerWeaponStat.albion_player_id == player.albion_id)
    )).all()
    for s in stats:
        role = weapon_fn.get(s.weapon_base, "dps")
        if role == "dps":
            continue  # já coberto inteiramente pelas kills (PlayerKillEvent)
        if role == "pierce":
            points[s.weapon_base] = points.get(s.weapon_base, 0) + s.pierce_points
        elif role == "healer":
            points[s.weapon_base] = points.get(s.weapon_base, 0) + s.healer_points
        elif role == "support":
            points[s.weapon_base] = points.get(s.weapon_base, 0) + s.zero_death_eligible_fights * SUPPORT_ELIGIBLE_FIGHT_POINTS
        elif role == "tank":
            points[s.weapon_base] = points.get(s.weapon_base, 0) + s.tank_ok_fights * TANK_ELIGIBLE_FIGHT_POINTS

    return points


async def _top_weapons(db: AsyncSession, player: AlbionPlayer) -> list[dict]:
    """Top 5 armas em destaque do perfil, ordenadas pelo peso/pontos (ver
    _weapon_points — não kills puras) e, pra cada uma, as top 5 builds usadas
    nos abates com ela — build identificada só pela BASE do item (ignora
    tier/encantamento e capa, pedido explícito). Pontos não vêm só de kills,
    então uma arma pode aparecer aqui mesmo com poucos (ou zero) abates."""
    kill_rows = (await db.scalars(
        select(PlayerKillEvent).where(
            PlayerKillEvent.killer_player_id == player.id,
            PlayerKillEvent.region == player.region,
            PlayerKillEvent.fame > 0,
        )
    )).all()

    by_weapon: dict[str, list[PlayerKillEvent]] = {}
    for ev in kill_rows:
        weapon_base = _wbase(((ev.killer_equipment or {}).get("MainHand") or {}).get("Type"))
        if weapon_base:
            by_weapon.setdefault(weapon_base, []).append(ev)

    points = await _weapon_points(db, player, by_weapon)
    top_weapons = [(wb, p) for wb, p in points.items() if p > 0]
    top_weapons.sort(key=lambda kv: kv[1], reverse=True)

    out = []
    for weapon_base, weight in top_weapons[:5]:
        evs = by_weapon.get(weapon_base, [])
        build_counts: dict[tuple[str | None, ...], int] = {}
        for ev in evs:
            equip = ev.killer_equipment or {}
            key = tuple(_wbase((equip.get(slot) or {}).get("Type")) for slot in _BUILD_SLOTS)
            build_counts[key] = build_counts.get(key, 0) + 1
        top_builds = sorted(build_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        out.append({
            "weapon_base": weapon_base,
            "points": weight,
            "builds": [
                {
                    "weapon_base": key[0], "offhand_base": key[1], "helmet_base": key[2],
                    "armor_base": key[3], "boots_base": key[4], "kills": count,
                }
                for key, count in top_builds
            ],
        })
    return out


async def _serialize_kill(
    db: AsyncSession, ev: PlayerKillEvent, role: str, weapon_fn_map: dict[str, str],
    battle_links: dict[str, tuple[str | None, list[dict]]],
    other_players: dict[int, AlbionPlayer] | None = None,
) -> dict:
    # O "outro" é sempre o oponente (vítima numa kill, matador numa morte).
    # `equipment` é sempre o set do PRÓPRIO jogador do perfil, `other_equipment`
    # o do oponente — a UI mostra os dois lados do confronto, não só o nosso.
    other_id = ev.victim_player_id if role == "kills" else ev.killer_player_id
    own_equipment = ev.killer_equipment if role == "kills" else ev.victim_equipment
    other_equipment = ev.victim_equipment if role == "kills" else ev.killer_equipment
    # Batch lookup: other_players vem pré-carregado pelo caller (todos os
    # oponentes em 1 query), em vez de db.get por kill (N queries pra um
    # jogador ativo com centenas de kills). Fallback db.get só se o caller
    # não passou o dict (compat com outros call sites).
    if other_id is None:
        other = None
    elif other_players is not None:
        other = other_players.get(other_id)
    else:
        other = await db.get(AlbionPlayer, other_id)
    own_weapon = (own_equipment or {}).get("MainHand") or {}
    battle_public_id, battle_factions = battle_links.get(ev.albion_battle_id, (None, []))
    return {
        "event_id": ev.albion_event_id,
        "timestamp": _aware(ev.timestamp).isoformat(),
        "fame": ev.fame,
        "silver_dropped": ev.silver_dropped or 0,
        "is_solo": ev.is_solo,
        "participant_count": ev.participant_count,
        "other_name": other.name if other else None,
        "other_albion_id": other.albion_id if other else None,
        "other_guild_name": other.guild_name if other else None,
        "other_alliance_name": other.alliance_name if other else None,
        "equipment": own_equipment or {},
        "other_equipment": other_equipment or {},
        # Função (tank/healer/support/dps/pierce) dirigida pela arma própria —
        # mesmo catálogo global usado pra sugestões de comp (Weapon.invisible_function).
        "role": weapon_fn_map.get(_wbase(own_weapon.get("Type"))),
        "battle_public_id": battle_public_id,
        "battle_factions": battle_factions,
    }


async def _silver_dropped(db: AsyncSession, death_rows: list[PlayerKillEvent]) -> int:
    """Prata aproximada perdida nas mortes registradas — preço dos itens
    equipados + carregados no momento da morte, reaproveitando o cache
    permanente de preço de loot (mesmo usado na página de batalha)."""
    pairs: list[tuple[str, int]] = []
    for ev in death_rows:
        for item in (ev.victim_equipment or {}).values():
            if item and item.get("Type"):
                pairs.append((item["Type"], 1))
        for inv in (ev.victim_inventory or []):
            if inv and inv.get("Type"):
                pairs.append((inv["Type"], inv.get("Count") or 1))
    if not pairs:
        return 0
    item_ids = list({iid for iid, _ in pairs})
    price_by_id = await prices.get_battle_prices(db, item_ids)
    total = 0
    for ev in death_rows:
        for item in (ev.victim_equipment or {}).values():
            if item and item.get("Type"):
                total += price_by_id.get(item["Type"], 0) + awakened_value(
                    item["Type"], item.get("LegendarySoul"),
                )
        for item in (ev.victim_inventory or []):
            if item and item.get("Type"):
                total += (
                    price_by_id.get(item["Type"], 0)
                    + awakened_value(item["Type"], item.get("LegendarySoul"))
                ) * (item.get("Count") or 1)
    return total


# Rank do jogador num kind de coleta (gather_*/fishing/crafting) — quantos têm
# valor MAIOR (na mesma região), +1. Top500 só: >500 devolve 0 (não aparece no
# perfil). Região do jogador é o escopo natural (nomes não são únicos entre
# servidores, e o ranking filtra por região). Mesma lógica do
# highscores._gather_ranking, mas só a posição de UM jogador.
_GATHER_RANK_KINDS = {
    "gather_total": "gathering_fame",
    "gather_wood": "gather_wood",
    "gather_hide": "gather_hide",
    "gather_ore": "gather_ore",
    "gather_rock": "gather_rock",
    "gather_fiber": "gather_fiber",
    "fishing": "fishing_fame",
    "crafting": "crafting_fame",
}

_TOP_N = 500


async def _gather_rank_of(db: AsyncSession, player: AlbionPlayer, kind: str) -> int:
    """Posição (1-indexed) do jogador no ranking do kind, top500 só.
    0 = fora do top500 (não mostra no perfil)."""
    col = getattr(AlbionPlayer, _GATHER_RANK_KINDS[kind])
    own = getattr(player, _GATHER_RANK_KINDS[kind]) or 0
    if own <= 0:
        return 0
    # Conta quantos têm valor MAIOR na mesma região — rank = maior+1.
    higher = await db.scalar(
        select(func.count()).select_from(AlbionPlayer)
        .where(AlbionPlayer.region == player.region, col > own)
    ) or 0
    rank = int(higher) + 1
    return rank if rank <= _TOP_N else 0


async def _gather_ranks(db: AsyncSession, player: AlbionPlayer) -> dict[str, int]:
    """{kind: rank} pra todos os kinds de coleta — top500 só (0 = fora).
    Uma query por kind (são 8), barato. Usado no _ziggs do perfil."""
    out: dict[str, int] = {}
    for kind in _GATHER_RANK_KINDS:
        r = await _gather_rank_of(db, player, kind)
        if r > 0:
            out[kind] = r
    return out


async def _guild_history(db: AsyncSession, player: AlbionPlayer) -> list[dict]:
    """Estadias por guilda derivadas das batalhas do jogador.
    Usa Battle.start_time — timestamps de kill events têm bugs de fuso na API
    do Albion que corrompem a linha do tempo."""
    rows = (await db.execute(
        select(Battle, BattleParticipant)
        .join(BattleParticipant, BattleParticipant.battle_id == Battle.id)
        .where(
            BattleParticipant.albion_player_id == player.albion_id,
            Battle.region == player.region,
            BattleParticipant.guild_id.isnot(None),
        )
        .order_by(Battle.start_time.asc())
    )).all()

    if not rows:
        return []

    stints: list[dict] = []
    for battle, bp in rows:
        ts = _aware(battle.start_time)
        if stints and stints[-1]["guild_id"] == bp.guild_id:
            stints[-1]["end"] = ts
            stints[-1]["kills"] += bp.kills
            stints[-1]["deaths"] += bp.deaths
            if not stints[-1]["alliance_tag"] and bp.alliance_name:
                stints[-1]["alliance_tag"] = bp.alliance_name
                stints[-1]["alliance_id"] = bp.alliance_id
        else:
            stints.append({
                "guild_id": bp.guild_id,
                "guild_name": bp.guild_name,
                "alliance_tag": bp.alliance_name,
                "alliance_id": bp.alliance_id,
                "start": ts,
                "end": ts,
                "kills": bp.kills,
                "deaths": bp.deaths,
            })

    stints.reverse()

    out = []
    for i, stint in enumerate(stints):
        is_current = i == 0 and player.guild_id == stint["guild_id"]
        out.append({
            "guild_id": stint["guild_id"],
            "guild_name": stint["guild_name"],
            "alliance_id": stint.get("alliance_id"),
            "alliance_tag": stint["alliance_tag"],
            "start": stint["start"].isoformat(),
            "end": None if is_current else stint["end"].isoformat(),
            "kills": stint["kills"],
            "deaths": stint["deaths"],
            "silver_dropped": 0,
        })

    # Enriquece cada entrada com is_deleted de guild/alliance
    guild_ids = [e["guild_id"] for e in out if e.get("guild_id")]
    alliance_ids = [e["alliance_id"] for e in out if e.get("alliance_id")]
    deleted_guilds = set((await db.scalars(
        select(DeletedProfile.albion_id).where(
            DeletedProfile.entity_type == "guild", DeletedProfile.albion_id.in_(guild_ids)
        )
    )).all()) if guild_ids else set()
    deleted_alliances = set((await db.scalars(
        select(DeletedProfile.albion_id).where(
            DeletedProfile.entity_type == "alliance", DeletedProfile.albion_id.in_(alliance_ids)
        )
    )).all()) if alliance_ids else set()
    for e in out:
        e["guild_is_deleted"] = e.get("guild_id") in deleted_guilds
        e["alliance_is_deleted"] = e.get("alliance_id") in deleted_alliances

    # Garante que a guilda atual do jogador apareça mesmo sem batalhas registradas sob ela
    if player.guild_id and (not out or out[0]["guild_id"] != player.guild_id):
        approx_start = out[0]["end"] if out and out[0]["end"] else _aware(player.last_seen_at).isoformat()
        out.insert(0, {
            "guild_id": player.guild_id,
            "guild_name": player.guild_name,
            "alliance_id": player.alliance_id,
            "alliance_tag": player.alliance_tag,
            "start": approx_start,
            "end": None,
            "kills": 0,
            "deaths": 0,
            "silver_dropped": 0,
        })

    return out


async def _build_profile_payload(db: AsyncSession, player: AlbionPlayer, raw: dict) -> dict:
    guild_history = await _guild_history(db, player)

    # Cresce em ordem cronológica — 90 pontos cobrem ~3 meses de histórico
    # com a resolução diária do gatilho de snapshot (ver upsert_player).
    fame_series = (await db.scalars(
        select(PlayerSnapshot).where(PlayerSnapshot.player_id == player.id)
        .order_by(PlayerSnapshot.snapshotted_at.asc()).limit(90)
    )).all()
    fame_history = [
        {"t": _aware(s.snapshotted_at).isoformat(), "kill_fame": s.kill_fame, "death_fame": s.death_fame}
        for s in fame_series
    ]

    death_rows = await _kill_ledger_rows(db, player.id, player.region, "deaths")
    kill_rows = await _kill_ledger_rows(db, player.id, player.region, "kills")
    weapon_fn_map = await _weapon_function_map(db)

    # Mortes/kills que não contam como atividade (fama zerada) também não
    # entram na prata dropada — pedido explícito.
    death_rows_for_activity = [ev for ev in death_rows if _counts_for_activity(ev)]
    kill_rows_for_activity = [ev for ev in kill_rows if _counts_for_activity(ev)]

    # Cache de _factions_summary compartilhado entre _battle_history e
    # _battle_links_bulk — a mesma batalha aparece nas duas listas (histórico
    # + kills), e sem cache cada uma rodava 3 queries (BattleSide + BattleGuild
    # + count). Um dict por-request: seguro em concorrência (cada request tem
    # o seu) e descarta sozinho no fim do escopo.
    factions_cache: dict[int, list[dict]] = {}

    # Pré-busca de todos os oponentes em 1 query — antes db.get por kill
    # (N queries pra jogador ativo com centenas de eventos). Inclui vítima e
    # matador de cada kill/morte do ledger do jogador.
    other_ids = {ev.victim_player_id for ev in kill_rows_for_activity} | {ev.killer_player_id for ev in death_rows_for_activity}
    other_ids.discard(None)
    other_players: dict[int, AlbionPlayer] = {}
    if other_ids:
        other_players = {
            p.id: p for p in (await db.scalars(select(AlbionPlayer).where(AlbionPlayer.id.in_(other_ids)))).all()
        }

    # Em lote — um commit só pra lista inteira de kills+mortes, não um por
    # evento (sem paginação aqui, pode ser centenas pra jogador ativo).
    battle_links = await _battle_links_bulk(
        db, player.region,
        [ev.albion_battle_id for ev in kill_rows_for_activity + death_rows_for_activity],
        factions_cache=factions_cache,
    )

    # Pré-calcula silver_dropped antes do dict — precisa de HTTP (get_battle_prices).
    # Commit libera read tx antes do await.
    await db.commit()
    silver_dropped_val = await _silver_dropped(db, death_rows_for_activity)

    return {
        **raw,
        "_is_deleted": player.is_deleted,
        "custom_profile": await _run_sync(user_profile.get_public_customization, player.albion_id),
        "_ziggs": {
            "region": player.region,
            "first_seen_at": _aware(player.first_seen_at).isoformat(),
            "last_seen_at": _aware(player.last_seen_at).isoformat(),
            # Estado de refresh compartilhado entre todos os visitantes — enquanto
            # não é None, o profile_warmer ainda vai re-sincronizar esse jogador
            # (botão ⟳ do perfil). O front mostra "atualizando" pra TODOS que
            # estão olhando, e desabilita o botão até sumir.
            "refresh_requested_at": _aware(player.refresh_requested_at).isoformat() if player.refresh_requested_at else None,
            "guild_history": guild_history,
            "fame_history": fame_history,
            "battle_history": await _battle_history(db, player.albion_id, player.region, factions_cache=factions_cache),
            "top_weapons": await _top_weapons(db, player),
            "kills": [await _serialize_kill(db, ev, "kills", weapon_fn_map, battle_links, other_players) for ev in kill_rows_for_activity],
            "deaths": [await _serialize_kill(db, ev, "deaths", weapon_fn_map, battle_links, other_players) for ev in death_rows_for_activity],
            "silver_dropped": silver_dropped_val,
            # Kill/morte mais valiosa do jogador (por silver e por fame) —
            # highlights pro perfil, não precisa abrir o ledger inteiro.
            "kill_highlights": await _kill_highlights(db, player),
            # Rank do jogador em cada kind de coleta — top500 só (0/vazio
            # = fora). Exibido no perfil como "MADEIRA (#481)" clicável, leva
            # pro highscores na posição dele. Ver _gather_ranks.
            "gather_ranks": await _gather_ranks(db, player),
        },
    }


def _player_to_search_result(p: AlbionPlayer) -> dict:
    return {
        "Id": p.albion_id, "Name": p.name,
        "GuildId": p.guild_id, "GuildName": p.guild_name,
        "AllianceId": p.alliance_id, "AllianceName": p.alliance_name, "AllianceTag": p.alliance_tag,
        "Avatar": p.avatar,
        "KillFame": p.kill_fame, "DeathFame": p.death_fame,
    }


@router.get("/search")
async def search_players(q: str = Query(min_length=2), region: str = "americas", db: AsyncSession = Depends(deps.async_db_session)):
    """Busca jogadores pelo nick no Albion Online (numa região por vez —
    nomes não são únicos entre Americas/Europe/Asia, são servidores separados).

    Busca primeiro na nossa base (instantâneo, sem chamar a Albion) — só cai
    pra busca ao vivo se não achou ninguém localmente. Resultado ao vivo é
    persistido (upsert_player) pra virar hit local da próxima vez."""
    host = HOSTS.get(region)
    if host is None:
        raise HTTPException(400, "Região inválida")

    # Prefixo (sargável, indexado) primeiro; substring só completa se sobrar
    # espaço — mesmo padrão de _search_entities em routes/profiles.py.
    nq = norm_name(q)
    lo, hi = prefix_range(nq)
    local_ids: list[str] = list((await db.scalars(
        select(SearchEntry.entity_id)
        .where(SearchEntry.entity_type == "player", SearchEntry.region == region,
               SearchEntry.norm_name >= lo, SearchEntry.norm_name < hi)
        .order_by(SearchEntry.weight.desc())
        .limit(20)
    )).all())
    if len(local_ids) < 20:
        local_ids += list((await db.scalars(
            select(SearchEntry.entity_id)
            .where(
                SearchEntry.entity_type == "player", SearchEntry.region == region,
                SearchEntry.norm_name.like(f"%{nq}%"), SearchEntry.entity_id.notin_(local_ids),
            )
            .order_by(SearchEntry.weight.desc())
            .limit(20 - len(local_ids))
        )).all())

    local: list[AlbionPlayer] = []
    if local_ids:
        by_id = {p.albion_id: p for p in (await db.scalars(select(AlbionPlayer).where(AlbionPlayer.albion_id.in_(local_ids)))).all()}
        local = [by_id[pid] for pid in local_ids if pid in by_id]
    if local:
        qlow = q.lower()
        local = sorted(local, key=lambda p: (not p.name.lower().startswith(qlow), p.name.lower()))
        return {"players": [_player_to_search_result(p) for p in local]}

    # Libera read tx antes do HTTP (busca na API do Albion).
    await db.commit()
    async with make_client() as c:
        async with albion_scope(PROFILE):
            try:
                resp = await _get_with_retry(c, f"https://{host}/api/gameinfo/search", params={"q": q})
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise HTTPException(status_code=502, detail=f"Albion API: {e.response.status_code}")
            except httpx.RequestError as e:
                raise HTTPException(status_code=502, detail=f"Erro de conexão: {e}")
    players = resp.json().get("players", [])
    for p in players:
        if p.get("Id"):
            try:
                await upsert_player(db, p, region)
            except Exception:
                pass

    return {"players": players}


def _synthetic_raw(player: AlbionPlayer) -> dict:
    """Reconstrói o formato bruto da API do Albion a partir do que já temos
    salvo — cobre os campos que o perfil realmente lê (ver
    PlayerProfilePage.tsx): Id/Name/Guild*/Alliance*/Avatar/*Fame e o
    LifetimeStatistics. Com lifetime_statistics preenchido (perfil/feed), o
    blob cru volta inteiro — inclui coleta por recurso (Wood/Ore/...), que os
    escalares não guardam. Sem o blob, reconstrói a partir dos escalares,
    INCLUINDO coleta por recurso e FishingFame (antes eram perdidos)."""
    lifetime = player.lifetime_statistics
    if not lifetime:
        lifetime = {
            "PvE": {"Total": player.pve_fame},
            "Crafting": {"Total": player.crafting_fame},
            "Gathering": {
                "All": {"Total": player.gathering_fame},
                "Wood": {"Total": player.gather_wood},
                "Hide": {"Total": player.gather_hide},
                "Ore": {"Total": player.gather_ore},
                "Rock": {"Total": player.gather_rock},
                "Fiber": {"Total": player.gather_fiber},
            },
            "FishingFame": player.fishing_fame,
        }
    return {
        "Id": player.albion_id, "Name": player.name,
        "GuildId": player.guild_id, "GuildName": player.guild_name,
        "AllianceId": player.alliance_id, "AllianceName": player.alliance_name, "AllianceTag": player.alliance_tag,
        "Avatar": player.avatar,
        "KillFame": player.kill_fame, "DeathFame": player.death_fame,
        "LifetimeStatistics": lifetime,
    }


# Etapa corrente da carga FRIA de um perfil (primeira visita — caminho lento
# que consulta a Albion), consumida por polling do frontend enquanto o fetch
# principal não retorna. Chave "region:nome_minusculo"; some ao terminar.
# Valor = (token_da_run, stage): cargas simultâneas do mesmo perfil
# (StrictMode em dev, dois visitantes) compartilham a chave — o token impede
# uma run terminando de apagar a etapa de outra em andamento.
# ponytail: dict em memória — com múltiplos workers cada um só vê o próprio;
# mover pra Redis/DB se o deploy virar multi-processo.
_load_progress: dict[str, tuple[object, str]] = {}

# Tasks de cold load em background — sobrevivem ao client desconectar. Sem
# isso, reload na tab cancelava a coroutine da request e o trabalho pesado
# (search + profile + kills na Albion) morria no meio; a nova request
# recomeçava do zero. Agora a task continua, e o _load_progress mostra o
# stage atual pra qualquer request que perguntar — a barra continua de onde
# estava, mesmo de outra aba/outro usuário.
# Chave = "region:nome_minusculo"; valor = asyncio.Task. A task se auto-remove
# ao terminar (sucesso ou falha).
_cold_load_tasks: dict[str, asyncio.Task] = {}

# Timestamp do último timeout de cold load, por chave. A rota checa se passou
# tempo suficiente pra tentar de novo (evita loop infinito de timeout imediato
# mas permite retry após um tempo). Mesmo padrão de profiles.py.
_TIMEOUT_RETRY_AFTER = timedelta(seconds=30)
_cold_timeout_at: dict[str, datetime] = {}


def _check_cold_timeout(key: str) -> None:
    """Checa se a última task de cold load terminou com timeout. Se sim e ainda
    não passou tempo suficiente, levanta 504. Se já passou, limpa e deixa
    tentar de novo."""
    entry = _load_progress.get(key)
    if not (entry and entry[1] == "error:timeout"):
        return
    timeout_at = _cold_timeout_at.get(key)
    if timeout_at is None:
        return
    if datetime.now(timezone.utc) - timeout_at < _TIMEOUT_RETRY_AFTER:
        raise HTTPException(504, "Tempo esgotado ao carregar o perfil — tente novamente")
    _load_progress.pop(key, None)
    _cold_timeout_at.pop(key, None)


@router.get("/load-progress/{region}/{name}")
def get_load_progress(region: str, name: str):
    """Etapa da carga fria em andamento pra esse perfil (stage=null: nada
    em andamento — ou é um perfil já cacheado, que resolve na hora)."""
    entry = _load_progress.get(f"{region}:{name.lower()}")
    # albion_queue: requests À FRENTE de um perfil (PROFILE) na fila — não o
    # total global (o perfil fura a fila do background). A UI mostra "na fila"
    # vs "buscando" pra explicar as pausas (rate limit 1/5s).
    return {"stage": entry[1] if entry else None, "albion_queue": queue_depth(PROFILE)}


async def _cold_load_player(region: str, name: str, host: str) -> None:
    """Faz o fetch pesado na Albion (search + profile + kills) e grava no DB.
    Roda como asyncio.create_task — não atrelada à request HTTP, sobrevive ao
    client desconectar. Atualiza _load_progress em cada etapa; limpa no final.
    Em falha, NÃO limpa o erro do _load_progress — a próxima request vê que
    terminou (stage=null) e re-tenta (ou retorna 404/502 dependendo do erro).

    Timeout de COLD_LOAD_TIMEOUT (15min): se o fetch não terminar a tempo, o
    stage vira error:timeout e a task se auto-remove. Sem isso, um cold load
    que travou (rate limiter da Albion pegando timeout infinito) ficava pra
    sempre em andamento e o usuário via "carregando…" eternamente."""
    progress_key = f"{region}:{name.lower()}"
    token = object()
    _load_progress[progress_key] = (token, "search")

    async def _work() -> None:
        async with make_client() as c:
            async with albion_scope(PROFILE):
                try:
                    resp = await _get_with_retry(c, f"https://{host}/api/gameinfo/search", params={"q": name})
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    _load_progress[progress_key] = (token, f"error:{e.response.status_code}")
                    return
                except httpx.RequestError:
                    _load_progress[progress_key] = (token, "error:network")
                    return
                candidates = resp.json().get("players", [])
                # Nomes da Albion diferenciam maiúsculas/minúsculas — "Moskka" e
                # "MosKKa" são DUAS contas distintas. Prioriza match exato; só cai
                # pra case-insensitive se não achar nenhum (ex: erro de digitação
                # na URL) — nunca o contrário, senão pode resolver pra conta errada.
                match = next((p for p in candidates if p.get("Name") == name), None)
                if match is None:
                    match = next((p for p in candidates if p.get("Name", "").lower() == name.lower()), None)
                if match is None:
                    _load_progress[progress_key] = (token, "error:notfound")
                    return
                _load_progress[progress_key] = (token, "details")
                raw = await _fetch_player_raw(c, host, match["Id"])
                if raw is None:
                    _load_progress[progress_key] = (token, "error:notfound")
                    return
                # Não depende só do feed global ter visto a luta desse jogador
                # específico — busca direto nos endpoints de kills/deaths dele.
                # Upsert ANTES da sync de kills (mesma ordem do _warm_player): o
                # jogador pode não existir no DB ainda (primeira visita de verdade),
                # e _record_kill_event resolve killer_player_id/victim_player_id
                # por lookup no banco. Sem a linha do jogador, os FKs ficam NULL
                # e o dedupe por event_id orfana as kills/deaths pra sempre — nem
                # o refresh (warmer) recupera, porque os eventos já estão no
                # ledger. Gravar o núcleo primeiro garante o link.
                _load_progress[progress_key] = (token, "build")
                async with AsyncSessionLocal() as db:
                    await upsert_player(db, raw, region)
                    _load_progress[progress_key] = (token, "kills")
                    await sync_player_kills(c, db, host, region, raw["Id"])

    try:
        await asyncio.wait_for(_work(), timeout=COLD_LOAD_TIMEOUT.total_seconds())
    except asyncio.TimeoutError:
        _load_progress[progress_key] = (token, "error:timeout")
        _cold_timeout_at[progress_key] = datetime.now(timezone.utc)
    except Exception as e:
        _load_progress[progress_key] = (token, f"error:{type(e).__name__}")
    finally:
        # Só limpa se ainda é desta run — não apaga a de outra em andamento.
        entry = _load_progress.get(progress_key)
        if entry is not None and entry[0] is token:
            _load_progress.pop(progress_key, None)
        _cold_load_tasks.pop(progress_key, None)


@router.get("/by-name/{region}/{name}")
async def get_player_by_name(region: str, name: str, db: AsyncSession = Depends(deps.async_db_session)):
    """Resolve `/am/Slayner` etc: busca o nome exato na região indicada,
    depois carrega o perfil completo já sabendo o host certo.

    Cache-first: se já temos esse jogador (ver profile_warmer), mostra na hora
    o que já sabemos — sem chamada à Albion. Só busca ao vivo quando é a
    primeira vez que vemos esse nome (nada pra mostrar ainda).

    Cold load desacoplado da request: o fetch pesado (search + profile + kills
    na Albion) roda como asyncio.create_task em background — sobrevive ao
    client desconectar (reload na tab). A request só enfileira a task (ou
    junta-se a uma em andamento) e retorna 202 com stage atual; o front faz
    polling de /load-progress até o perfil estar pronto no DB, quando a próxima
    leitura retorna o perfil completo."""
    host = HOSTS.get(region)
    if host is None:
        raise HTTPException(400, "Região inválida")

    cached = await db.scalar(
        select(AlbionPlayer).where(AlbionPlayer.region == region, func.lower(AlbionPlayer.name) == name.lower())
    )
    if cached is not None and cached.lifetime_statistics is not None:
        # Cache com perfil completo (LifetimeStatistics já foi carregado alguma
        # vez) — serve na hora. Se só vimos o player via feed/search (sem
        # LifetimeStatistics), kill_fame/death_fame/pve/gathering estão zeros e
        # o perfil mostra nada — precisa do cold load pra buscar o perfil
        # completo. Cai pro fluxo de cold load abaixo.
        await _queue_refresh_if_stale(db, cached)
        # Libera read tx antes do await (_build_profile_payload faz HTTP).
        await db.commit()
        return await _build_profile_payload(db, cached, _synthetic_raw(cached))

    if cached is not None and cached.lifetime_statistics is None:
        # Cache incompleto (visto via feed/search mas nunca warmed). Tenta
        # buscar o perfil completo SÍNCRONO com timeout curto — se a Albion
        # responder rápido, o usuário vê o perfil completo na mesma request,
        # sem stub nem polling. Se falhar/timeout, cai pro cold load async.
        # PULA se já tem cold load em andamento (não compete por slot do pool).
        progress_key = f"{region}:{name.lower()}"
        existing_task = _cold_load_tasks.get(progress_key)
        if cached.albion_id and (existing_task is None or existing_task.done()):
            try:
                async with make_client() as c:
                    async with albion_scope(PROFILE):
                        raw = await asyncio.wait_for(
                            _fetch_player_raw(c, host, cached.albion_id),
                            timeout=8.0,
                        )
                    if raw is not None:
                        # Upsert síncrono (rápido — é só DB) + kills via warmer
                        # em background. Assim o usuário vê stats completas na
                        # mesma request sem esperar a sync de kills (2 requests
                        # HTTP lentos que segurariam conexão do pool DB).
                        await upsert_player(db, raw, region)
                        await db.commit()
                        refreshed = await db.scalar(
                            select(AlbionPlayer).where(AlbionPlayer.albion_id == raw["Id"], AlbionPlayer.region == region)
                        )
                        if refreshed is not None:
                            # Marca pra o warmer sincronizar kills em background
                            # (tem seu próprio controle de pool/concorrência).
                            try:
                                refreshed.refresh_requested_at = datetime.now(timezone.utc)
                                await db.commit()
                            except Exception:
                                pass
                            return await _build_profile_payload(db, refreshed, raw)
            except (asyncio.TimeoutError, httpx.RequestError, Exception):
                pass  # cai pro cold load async abaixo
        # Síncrono falhou (ou já tem task rodando) — enfileira warm no
        # profile_warmer (não depende de o usuário ficar polling).
        if cached.albion_id:
            try:
                await request_refresh(db, cached.albion_id, region)
            except Exception:
                pass

    # Cold load: dispara task em background (ou junta-se a uma em andamento).
    # Se já tem task rodando, só retorna o stage atual — a barra continua de
    # onde estava, mesmo se o usuário acabou de chegar (ou deu reload).
    progress_key = f"{region}:{name.lower()}"
    # Trata erros registrados pela task (ela já terminou mas ainda não foi
    # limpa — devolve o erro pra o front mostrar em vez de loopar eterno).
    # Timeout tem cooldown de 30s antes de poder tentar de novo (evita loop
    # infinito de timeout imediato); outros erros (notfound, network) são
    # definitivos — devolve o erro e o usuário recarrega pra tentar de novo.
    entry = _load_progress.get(progress_key)
    stage = entry[1] if entry else None
    if stage and stage.startswith("error:"):
        kind = stage.split(":", 1)[1]
        if kind == "timeout":
            _check_cold_timeout(progress_key)  # levanta 504 ou limpa pra retry
        elif kind == "notfound":
            raise HTTPException(404, "Jogador não encontrado nessa região")
        elif kind == "network":
            raise HTTPException(502, "Erro de conexão com a Albion API")
        else:
            raise HTTPException(502, f"Albion API: {kind}")

    # Dispara task em background (ou junta-se a uma em andamento).
    task = _cold_load_tasks.get(progress_key)
    if task is None or task.done():
        t = asyncio.create_task(_cold_load_player(region, name, host))
        _cold_load_tasks[progress_key] = t

    # Cold load em andamento — retorna 200 com payload stub. O front detecta
    # _cold_load=true e mostra a barra de progresso (polling de /load-progress)
    # até o perfil estar pronto no DB, quando a próxima leitura retorna o
    # perfil completo. Não é 202 porque o front espera 200 com JSON.
    return {
        "Id": None, "Name": name, "_cold_load": True,
        "_ziggs": {"region": region, "first_seen_at": None, "last_seen_at": None,
                   "refresh_requested_at": None, "guild_history": [], "fame_history": [],
                   "battle_history": [], "top_weapons": [], "kills": [], "deaths": [],
                   "silver_dropped": 0},
    }


@router.get("/{albion_id}")
async def get_player(albion_id: str, region: str | None = None, db: AsyncSession = Depends(deps.async_db_session)):
    """Retorna perfil completo e salva snapshot no banco. Sem `region`
    informado, tenta os 3 hosts em sequência — um ID só responde 200 numa
    região (mesmo padrão de battle_tracker.resolve_by_albion_id).

    Cache-first: mesma lógica de get_player_by_name — só busca ao vivo se
    nunca vimos esse ID antes."""
    cached = await db.scalar(select(AlbionPlayer).where(AlbionPlayer.albion_id == albion_id))
    if cached is not None and cached.lifetime_statistics is not None:
        # Mesma lógica de get_player_by_name: só serve o cache se já temos o
        # perfil completo (LifetimeStatistics). Sem ele, kill_fame/death_fame/
        # pve/gathering estão zeros e o perfil mostra nada — cai pro cold load.
        await _queue_refresh_if_stale(db, cached)
        # Libera read tx antes do await (_build_profile_payload faz HTTP).
        await db.commit()
        return await _build_profile_payload(db, cached, _synthetic_raw(cached))

    if cached is not None and cached.lifetime_statistics is None and cached.region:
        # Cache incompleto (visto via feed/search mas nunca warmed). Tenta
        # buscar o perfil completo SÍNCRONO com timeout curto — mesma
        # estratégia de get_player_by_name. Se falhar, cai pro fluxo live abaixo.
        host = HOSTS.get(cached.region)
        if host:
            try:
                async with make_client() as c:
                    async with albion_scope(PROFILE):
                        raw = await asyncio.wait_for(
                            _fetch_player_raw(c, host, albion_id),
                            timeout=8.0,
                        )
                    if raw is not None:
                        await upsert_player(db, raw, cached.region)
                        await db.commit()
                        refreshed = await db.scalar(
                            select(AlbionPlayer).where(AlbionPlayer.albion_id == albion_id)
                        )
                        if refreshed is not None:
                            try:
                                refreshed.refresh_requested_at = datetime.now(timezone.utc)
                                await db.commit()
                            except Exception:
                                pass
                            return await _build_profile_payload(db, refreshed, raw)
            except (asyncio.TimeoutError, httpx.RequestError, Exception):
                pass  # cai pro fluxo live abaixo

    async with make_client() as c:
        async with albion_scope(PROFILE):
            raw = None
            resolved_region = region
            resolved_host = HOSTS.get(region) if region else None
            if resolved_host:
                raw = await _fetch_player_raw(c, resolved_host, albion_id)
            else:
                for r, host in HOSTS.items():
                    raw = await _fetch_player_raw(c, host, albion_id)
                    if raw is not None:
                        resolved_region, resolved_host = r, host
                        break

            if raw is None:
                # Marca como excluído se já estava no nosso banco
                existing = await db.scalar(select(AlbionPlayer).where(AlbionPlayer.albion_id == albion_id))
                if existing and not existing.is_deleted:
                    existing.is_deleted = True
                    await db.commit()
                raise HTTPException(404, "Jogador não encontrado")
            # Upsert ANTES da sync de kills — ver _cold_load_player. Sem a linha
            # do jogador no banco, _record_kill_event registra kills/deaths com
            # killer_player_id/victim_player_id = NULL (orfanadas pelo dedupe de
            # event_id; nem refresh recupera).
            player = await upsert_player(db, raw, resolved_region)
            await sync_player_kills(c, db, resolved_host, resolved_region, raw["Id"])

    return await _build_profile_payload(db, player, raw)


@router.get("/refresh-progress/{albion_id}")
def get_refresh_progress(albion_id: str):
    """Etapa do refresh em andamento pro botão ⟳ do perfil (stage=null: nada
    em andamento — ou não tem refresh, ou já terminou). Mesmo padrão do
    /players/load-progress: dict em memória no profile_warmer, lido por
    polling enquanto refresh_requested_at != null.

    Stages: 'queued' → 'fetching' → 'kills' → 'building' → (some)."""
    from app.services.profile_warmer import _refresh_progress
    return {"stage": _refresh_progress.get(albion_id), "albion_queue": queue_depth(PROFILE)}


@router.post("/{albion_id}/refresh")
async def request_player_refresh(albion_id: str, db: AsyncSession = Depends(deps.async_db_session)):
    """Enfileira uma atualização (botão ⟳ do perfil) — não busca nada agora,
    só marca o pedido; o profile_warmer processa em alta prioridade (PROFILE,
    reserved pool) e limpa o campo quando termina. O refresh_event acorda o
    warmer na hora, sem esperar o sleep idle.

    Cooldown de 10min (mesmo valor pros 3 tipos de perfil — ver REFRESH_COOLDOWN
    em profiles.py): após uma atualização (last_seen_at mudou), o perfil só
    pode ser refreshado de novo depois de 10min — evita spam e sobrecarga na
    API da Albion. Enquanto há um refresh em andamento (refresh_requested_at
    != None), não enfileira de novo: retorna o estado atual, então TODOS que
    estão olhando o perfil vêem "atualizando" ao mesmo tempo."""
    player = await db.scalar(select(AlbionPlayer).where(AlbionPlayer.albion_id == albion_id))
    if player is None:
        raise HTTPException(404, "Jogador não encontrado")

    # Já em andamento — só confirma o estado pro caller (não duplica o pedido).
    if player.refresh_requested_at is not None:
        return {"queued": True, "refreshing": True, "cooldown_seconds": 0}

    # Cooldown pós-refresh COMPLETO — o sinal é quando o WARMER terminou, não
    # last_seen_at: o player_tracker bumpa last_seen_at a cada aparição no kill
    # feed global, então jogador ativo (o que mais se quer atualizar) tinha
    # last_seen_at sempre < 10min e o botão ⟳ era recusado pra sempre (bug: a
    # atualização "não surtia efeito"). _refresh_done_at só marca refresh de
    # verdade (ver profile_warmer.sync_refresh_requests).
    from app.services.profile_warmer import _refresh_done_at
    REFRESH_COOLDOWN = timedelta(minutes=10)
    last_refresh = _refresh_done_at.get(albion_id)
    if last_refresh is not None:
        elapsed = datetime.now(timezone.utc) - _aware(last_refresh)
        if elapsed < REFRESH_COOLDOWN:
            return {
                "queued": False,
                "refreshing": False,
                "cooldown_seconds": int((REFRESH_COOLDOWN - elapsed).total_seconds()),
            }

    player.refresh_requested_at = datetime.now(timezone.utc)
    await db.commit()
    # Marca o stage inicial — o usuário vê "na fila" imediatamente, antes do
    # profile_warmer pegar o pedido (pode levar alguns segundos até o ciclo).
    from app.services.profile_warmer import _refresh_progress
    _refresh_progress[player.albion_id] = "queued"
    request_refresh()
    return {"queued": True, "refreshing": True, "cooldown_seconds": 0}


@router.get("/{albion_id}/kills")
async def get_player_kills(albion_id: str, offset: int = 0, limit: int = 10, region: str | None = None):
    """Proxy direto do Albion — complementa o ledger próprio (PlayerKillEvent)
    com histórico de antes do ledger existir. Usa o host da região do jogador
    (cada ID só existe numa região); sem região, tenta as 3 até achar."""
    hosts = [HOSTS[region]] if region and region in HOSTS else list(HOSTS.values())
    async with make_client() as c:
        async with albion_scope(PROFILE):
            for host in hosts:
                try:
                    async with slot(host):
                        resp = await c.get(
                            f"https://{host}/api/gameinfo/players/{albion_id}/kills",
                            params={"offset": offset, "limit": limit},
                        )
                    if resp.status_code == 200:
                        return resp.json()
                    if resp.status_code == 404:
                        continue
                    resp.raise_for_status()
                except httpx.RequestError:
                    continue
            raise HTTPException(status_code=502, detail="Erro de conexão com a Albion API")


@router.get("/{albion_id}/deaths")
async def get_player_deaths(albion_id: str, offset: int = 0, limit: int = 10, region: str | None = None):
    hosts = [HOSTS[region]] if region and region in HOSTS else list(HOSTS.values())
    async with make_client() as c:
        async with albion_scope(PROFILE):
            for host in hosts:
                try:
                    async with slot(host):
                        resp = await c.get(
                            f"https://{host}/api/gameinfo/players/{albion_id}/deaths",
                            params={"offset": offset, "limit": limit},
                        )
                    if resp.status_code == 200:
                        return resp.json()
                    if resp.status_code == 404:
                        continue
                    resp.raise_for_status()
                except httpx.RequestError:
                    continue
            raise HTTPException(status_code=502, detail="Erro de conexão com a Albion API")


@router.get("/{albion_id}/versus")
async def versus_history(
    albion_id: str,
    target: str = Query(..., description="Nome do jogador ou guilda oponente"),
    region: str = Query("americas"),
    kind: str = Query("both", description="kills | deaths | both"),
    db: AsyncSession = Depends(deps.async_db_session),
):
    """Histórico de confrontos do jogador do perfil contra um alvo — que pode
    ser um jogador (por nome) ou uma guilda (por nome). O site usa na aba
    Atividade: barra de pesquisa "X matou Y?" onde Y pode ser nick ou guilda.

    `albion_id` é o jogador do perfil. `target` é resolvido primeiro como
    jogador (AlbionPlayer por nome na região), depois como guilda
    (PlayerKillEvent.killer_guild_name / victim_guild_name — snapshot do
    evento, não tabela de guildas). `kind` filtra só kills, só deaths, ou
    ambos."""
    player = await db.scalar(select(AlbionPlayer).where(
        AlbionPlayer.albion_id == albion_id, AlbionPlayer.region == region,
    ))
    if player is None:
        return {"kills": [], "deaths": [], "target_name": target, "target_type": "unknown"}

    target_norm = target.strip()
    # Tenta resolver como jogador primeiro.
    opp = await db.scalar(select(AlbionPlayer).where(
        AlbionPlayer.name.ilike(target_norm), AlbionPlayer.region == region,
    ))

    kills_filters = [PlayerKillEvent.region == region, PlayerKillEvent.fame > 0]
    deaths_filters = [PlayerKillEvent.region == region, PlayerKillEvent.fame > 0]

    if opp is not None:
        # Jogador vs jogador: killer=player, victim=opp (kills); inverso (deaths).
        kills_filters.append(PlayerKillEvent.killer_player_id == player.id)
        kills_filters.append(PlayerKillEvent.victim_player_id == opp.id)
        deaths_filters.append(PlayerKillEvent.killer_player_id == opp.id)
        deaths_filters.append(PlayerKillEvent.victim_player_id == player.id)
        target_type = "player"
        target_name = opp.name
    else:
        # Jogador vs guilda: kills = player matou alguém da guilda alvo;
        # deaths = alguém da guilda alvo matou player. Guilda é snapshot do
        # evento (killer_guild_name / victim_guild_name), não tabela externa.
        target_type = "guild"
        target_name = target_norm
        kills_filters.append(PlayerKillEvent.killer_player_id == player.id)
        kills_filters.append(PlayerKillEvent.victim_guild_name.ilike(target_norm))
        deaths_filters.append(PlayerKillEvent.victim_player_id == player.id)
        deaths_filters.append(PlayerKillEvent.killer_guild_name.ilike(target_norm))

    out: dict = {"target_name": target_name, "target_type": target_type, "kills": [], "deaths": []}

    if kind in ("kills", "both"):
        kill_events = list((await db.scalars(
            select(PlayerKillEvent).where(*kills_filters).order_by(PlayerKillEvent.timestamp.desc())
        )).all())
        out["kills"] = _summarize_versus(kill_events)
        out["kills_count"] = len(kill_events)
        out["kills_silver"] = sum(ev.silver_dropped or 0 for ev in kill_events)

    if kind in ("deaths", "both"):
        death_events = list((await db.scalars(
            select(PlayerKillEvent).where(*deaths_filters).order_by(PlayerKillEvent.timestamp.desc())
        )).all())
        out["deaths"] = _summarize_versus(death_events)
        out["deaths_count"] = len(death_events)
        out["deaths_silver"] = sum(ev.silver_dropped or 0 for ev in death_events)

    return out


def _summarize_versus(events: list[PlayerKillEvent]) -> list[dict]:
    return [{
        "event_id": ev.albion_event_id,
        "timestamp": _aware(ev.timestamp).isoformat() if ev.timestamp else None,
        "fame": ev.fame,
        "silver_dropped": ev.silver_dropped or 0,
        "is_solo": ev.is_solo,
        "participant_count": ev.participant_count,
        "albion_battle_id": ev.albion_battle_id,
        "victim_guild_name": ev.victim_guild_name,
        "killer_guild_name": ev.killer_guild_name,
    } for ev in events]


# ── Embed de perfil (PNG para Discord) ────────────────────────────────────

async def _render_profile_preview(albion_id: str, region: str):
    from app.services.profile_preview import render_player_preview
    def _run():
        sdb = SyncSessionLocal()
        try:
            return render_player_preview(sdb, albion_id, region)
        finally:
            sdb.close()
    return await asyncio.to_thread(_run)


@router.get("/embed/{region}/{name}.png")
async def player_preview_png(region: str, name: str):
    """PNG de perfil do jogador pra embeds do Discord. Cacheado em disco (1h TTL).
    Se o player não está no DB, faz cold load síncrono (busca na Albion) pra
    que o embed do Discord sempre tenha uma imagem, mesmo pra perfis novos."""
    from urllib.parse import unquote
    from fastapi.responses import FileResponse

    if region not in HOSTS:
        raise HTTPException(400, "Região inválida")
    name_decoded = unquote(name)
    host = HOSTS[region]

    def _lookup():
        sdb = SyncSessionLocal()
        try:
            return sdb.scalar(
                select(AlbionPlayer).where(
                    AlbionPlayer.region == region,
                    func.lower(AlbionPlayer.name) == name_decoded.lower(),
                )
            )
        finally:
            sdb.close()

    player = await asyncio.to_thread(_lookup)

    # Player não está no DB — faz cold load síncrono (o Discord crawler
    # só espera alguns segundos, mas é melhor tentar do que devolver 404).
    if player is None:
        try:
            async with make_client() as c:
                async with albion_scope(PROFILE):
                    resp = await _get_with_retry(
                        c, f"https://{host}/api/gameinfo/search",
                        params={"q": name_decoded},
                    )
                    if resp.status_code != 200:
                        raise HTTPException(404, "Jogador não encontrado")
                    candidates = resp.json().get("players", [])
                    match = next((p for p in candidates if p.get("Name") == name_decoded), None)
                    if match is None:
                        match = next((p for p in candidates if p.get("Name", "").lower() == name_decoded.lower()), None)
                    if match is None:
                        raise HTTPException(404, "Jogador não encontrado")
                    raw = await asyncio.wait_for(
                        _fetch_player_raw(c, host, match["Id"]),
                        timeout=8.0,
                    )
                    if raw is None:
                        raise HTTPException(404, "Jogador não encontrado")
                async with AsyncSessionLocal() as db:
                    await upsert_player(db, raw, region)
                    # Marca pra o warmer sincronizar kills/deaths em background
                    # — o embed só busca o perfil (stats), mas as atividades
                    # (kills/deaths) precisam de sync_player_kills (2 requests
                    # HTTP lentos). Sem isso, o site abre o perfil e vê 0
                    # atividades mesmo com lifetime_statistics populado.
                    p = await db.scalar(
                        select(AlbionPlayer).where(
                            AlbionPlayer.albion_id == raw["Id"],
                            AlbionPlayer.region == region,
                        )
                    )
                    if p is not None and p.refresh_requested_at is None:
                        p.refresh_requested_at = datetime.now(timezone.utc)
                    await db.commit()
                player = await asyncio.to_thread(_lookup)
        except HTTPException:
            raise
        except (asyncio.TimeoutError, httpx.RequestError, Exception):
            raise HTTPException(502, "Erro ao buscar perfil na Albion API")

    if player is None:
        raise HTTPException(404, "Jogador não encontrado")
    path = await _render_profile_preview(player.albion_id, region)
    if path is None:
        raise HTTPException(404, "Não foi possível gerar o preview")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})


@router.get("/embed/id/{albion_id}.png")
async def player_preview_png_by_id(albion_id: str, region: str = "americas"):
    """PNG de perfil do jogador por albion_id (pra OG tags do spa.py)."""
    from fastapi.responses import FileResponse

    if region not in HOSTS:
        raise HTTPException(400, "Região inválida")
    path = await _render_profile_preview(albion_id, region)
    if path is None:
        raise HTTPException(404, "Jogador não encontrado")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})
