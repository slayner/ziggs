"""Rotas de perfis públicos de jogadores de Albion."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import deps
from app.api.routes.battles import (
    _factions_summary, _wbase, _weapon_function_map,
    SUPPORT_ELIGIBLE_FIGHT_POINTS, TANK_ELIGIBLE_FIGHT_POINTS,
)
from app.models.battles import Battle, BattleParticipant
from app.models.players import AlbionPlayer, DeletedProfile, PlayerKillEvent, PlayerSnapshot, PlayerWeaponStat, SearchEntry
from app.services import battle_groups, prices, user_profile
from app.services.albion_gate import PROFILE, albion_scope, slot
from app.services.player_tracker import HOSTS, make_client, sync_player_kills, upsert_player
from app.services.profile_warmer import request_refresh
from app.services.search_norm import normalize as norm_name, prefix_range

router = APIRouter(prefix="/players", tags=["players"])


def _aware(dt: datetime) -> datetime:
    # SQLite não preserva tz-awareness na leitura mesmo com DateTime(timezone=True).
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# View cache-first não força mais fetch ao vivo a cada visita — mas
# guild_id/guild_name só atualizam via feed global (amostra os 51 eventos
# mais recentes por região a cada 5min, pode nunca pegar jogador pouco
# ativo em PvP) ou o warmer semanal. Sem isso um perfil raramente visitado
# podia ficar mostrando a guilda de MESES atrás como se fosse a atual
# (e o "guild_history" interpretava isso como "voltou pra guilda antiga").
# Um limiar bem mais curto aqui, só pra enfileirar correção em segundo
# plano (mesma fila do botão ⟳) — a view em si continua instantânea.
PROFILE_STALE_AFTER = timedelta(minutes=30)


def _queue_refresh_if_stale(db: Session, player: AlbionPlayer) -> None:
    if player.refresh_requested_at is not None:
        return  # já enfileirado
    if datetime.now(timezone.utc) - _aware(player.last_seen_at) > PROFILE_STALE_AFTER:
        player.refresh_requested_at = datetime.now(timezone.utc)
        db.commit()


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
            async with slot():
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


def _battle_history(db: Session, albion_player_id: str, region: str) -> list[dict]:
    """Batalhas que entraram em deep-processing (>= DEEP_PROCESS_MIN_PLAYERS
    jogadores, ver battle_tracker.py) onde esse jogador apareceu — lutas
    pequenas/solo não geram BattleParticipant, ficam só no ledger de kills.
    Sem limite: a paginação do perfil (10/página) já aguenta a lista inteira."""
    rows = db.execute(
        select(Battle, BattleParticipant)
        .join(BattleParticipant, BattleParticipant.battle_id == Battle.id)
        .where(BattleParticipant.albion_player_id == albion_player_id, Battle.region == region)
        .order_by(Battle.start_time.desc())
    ).all()
    # Em lote — um commit só pra lista inteira, não um por batalha (pode ser
    # centenas pra jogador ativo desde que DEEP_PROCESS_MIN_PLAYERS virou 0).
    groups = battle_groups.get_or_create_groups_bulk(db, [battle.id for battle, _ in rows])
    out = []
    for battle, bp in rows:
        group = groups[battle.id]
        out.append({
            "public_id": group.public_id,
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
            "factions": _factions_summary(db, battle.id),
            "kills": bp.kills, "deaths": bp.deaths, "kill_fame": bp.kill_fame,
        })
    return out


def _battle_links_bulk(db: Session, region: str, albion_battle_ids: list[str | None]) -> dict[str, tuple[str | None, list[dict]]]:
    """Versão em lote de resolução de link público + heatmap de facções
    (mesma etiqueta centralizada da listagem de batalhas) pra várias
    battle_id de uma vez, com um commit só (ver
    battle_groups.get_or_create_groups_bulk) — usado pela lista de
    kills/mortes do perfil, que não tem paginação e pode ter centenas de
    eventos pra jogador ativo. Luta "light" (< deep-processing) linka
    normal, só sem heatmap (facções vazias)."""
    ids = sorted({i for i in albion_battle_ids if i})
    if not ids:
        return {}
    battles = db.scalars(select(Battle).where(Battle.region == region, Battle.albion_id.in_(ids))).all()
    groups = battle_groups.get_or_create_groups_bulk(db, [b.id for b in battles])
    return {b.albion_id: (groups[b.id].public_id, _factions_summary(db, b.id)) for b in battles}


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


def _kill_ledger_rows(db: Session, player_id: int, region: str, role: str) -> list[PlayerKillEvent]:
    """Sem limite: a paginação do perfil (10/página) já aguenta a lista inteira."""
    own_col = PlayerKillEvent.killer_player_id if role == "kills" else PlayerKillEvent.victim_player_id
    return list(db.scalars(
        select(PlayerKillEvent)
        .where(own_col == player_id, PlayerKillEvent.region == region)
        .order_by(PlayerKillEvent.timestamp.desc())
    ).all())


# Slots que compõem a "identidade" de uma build pro widget de armas mais
# usadas — sem capa (pedido explícito) e sem mount/food/potion (irrelevantes
# pra build de combate).
_BUILD_SLOTS = ("MainHand", "OffHand", "Head", "Armor", "Shoes")


def _weapon_points(db: Session, player: AlbionPlayer, by_weapon: dict[str, list[PlayerKillEvent]]) -> dict[str, int]:
    """Pontos por arma — kills (by_weapon, ledger ao vivo) + bônus de função
    lido de PlayerWeaponStat (contadores brutos pré-calculados, ver
    app.services.weapon_stats; mesma fórmula usada no Highscores all-time)."""
    weapon_fn = _weapon_function_map(db)
    points: dict[str, int] = {wb: len(evs) for wb, evs in by_weapon.items()}

    stats = db.scalars(
        select(PlayerWeaponStat).where(PlayerWeaponStat.albion_player_id == player.albion_id)
    ).all()
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


def _top_weapons(db: Session, player: AlbionPlayer) -> list[dict]:
    """Top 5 armas em destaque do perfil, ordenadas pelo peso/pontos (ver
    _weapon_points — não kills puras) e, pra cada uma, as top 5 builds usadas
    nos abates com ela — build identificada só pela BASE do item (ignora
    tier/encantamento e capa, pedido explícito). Pontos não vêm só de kills,
    então uma arma pode aparecer aqui mesmo com poucos (ou zero) abates."""
    kill_rows = db.scalars(
        select(PlayerKillEvent).where(
            PlayerKillEvent.killer_player_id == player.id,
            PlayerKillEvent.region == player.region,
            PlayerKillEvent.fame > 0,
        )
    ).all()

    by_weapon: dict[str, list[PlayerKillEvent]] = {}
    for ev in kill_rows:
        weapon_base = _wbase(((ev.killer_equipment or {}).get("MainHand") or {}).get("Type"))
        if weapon_base:
            by_weapon.setdefault(weapon_base, []).append(ev)

    points = _weapon_points(db, player, by_weapon)
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


def _serialize_kill(
    db: Session, ev: PlayerKillEvent, role: str, weapon_fn_map: dict[str, str],
    battle_links: dict[str, tuple[str | None, list[dict]]],
) -> dict:
    # O "outro" é sempre o oponente (vítima numa kill, matador numa morte).
    # `equipment` é sempre o set do PRÓPRIO jogador do perfil, `other_equipment`
    # o do oponente — a UI mostra os dois lados do confronto, não só o nosso.
    other_id = ev.victim_player_id if role == "kills" else ev.killer_player_id
    own_equipment = ev.killer_equipment if role == "kills" else ev.victim_equipment
    other_equipment = ev.victim_equipment if role == "kills" else ev.killer_equipment
    other = db.get(AlbionPlayer, other_id) if other_id else None
    own_weapon = (own_equipment or {}).get("MainHand") or {}
    battle_public_id, battle_factions = battle_links.get(ev.albion_battle_id, (None, []))
    return {
        "event_id": ev.albion_event_id,
        "timestamp": _aware(ev.timestamp).isoformat(),
        "fame": ev.fame,
        "is_solo": ev.is_solo,
        "participant_count": ev.participant_count,
        "other_name": other.name if other else None,
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


async def _silver_dropped(db: Session, death_rows: list[PlayerKillEvent]) -> int:
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
    return sum(price_by_id.get(iid, 0) * qty for iid, qty in pairs)


def _guild_history(db: Session, player: AlbionPlayer) -> list[dict]:
    """Estadias por guilda derivadas das batalhas do jogador.
    Usa Battle.start_time — timestamps de kill events têm bugs de fuso na API
    do Albion que corrompem a linha do tempo."""
    rows = db.execute(
        select(Battle, BattleParticipant)
        .join(BattleParticipant, BattleParticipant.battle_id == Battle.id)
        .where(
            BattleParticipant.albion_player_id == player.albion_id,
            Battle.region == player.region,
            BattleParticipant.guild_id.isnot(None),
        )
        .order_by(Battle.start_time.asc())
    ).all()

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
    deleted_guilds = set(db.scalars(
        select(DeletedProfile.albion_id).where(
            DeletedProfile.entity_type == "guild", DeletedProfile.albion_id.in_(guild_ids)
        )
    ).all()) if guild_ids else set()
    deleted_alliances = set(db.scalars(
        select(DeletedProfile.albion_id).where(
            DeletedProfile.entity_type == "alliance", DeletedProfile.albion_id.in_(alliance_ids)
        )
    ).all()) if alliance_ids else set()
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


async def _build_profile_payload(db: Session, player: AlbionPlayer, raw: dict) -> dict:
    guild_history = _guild_history(db, player)

    # Cresce em ordem cronológica — 90 pontos cobrem ~3 meses de histórico
    # com a resolução diária do gatilho de snapshot (ver upsert_player).
    fame_series = db.scalars(
        select(PlayerSnapshot).where(PlayerSnapshot.player_id == player.id)
        .order_by(PlayerSnapshot.snapshotted_at.asc()).limit(90)
    ).all()
    fame_history = [
        {"t": _aware(s.snapshotted_at).isoformat(), "kill_fame": s.kill_fame, "death_fame": s.death_fame}
        for s in fame_series
    ]

    death_rows = _kill_ledger_rows(db, player.id, player.region, "deaths")
    kill_rows = _kill_ledger_rows(db, player.id, player.region, "kills")
    weapon_fn_map = _weapon_function_map(db)

    # Mortes/kills que não contam como atividade (fama zerada) também não
    # entram na prata dropada — pedido explícito.
    death_rows_for_activity = [ev for ev in death_rows if _counts_for_activity(ev)]
    kill_rows_for_activity = [ev for ev in kill_rows if _counts_for_activity(ev)]

    # Em lote — um commit só pra lista inteira de kills+mortes, não um por
    # evento (sem paginação aqui, pode ser centenas pra jogador ativo).
    battle_links = _battle_links_bulk(
        db, player.region,
        [ev.albion_battle_id for ev in kill_rows_for_activity + death_rows_for_activity],
    )

    return {
        **raw,
        "_is_deleted": player.is_deleted,
        "custom_profile": user_profile.get_public_customization(db, player.albion_id),
        "_ziggs": {
            "region": player.region,
            "first_seen_at": _aware(player.first_seen_at).isoformat(),
            "last_seen_at": _aware(player.last_seen_at).isoformat(),
            "guild_history": guild_history,
            "fame_history": fame_history,
            "battle_history": _battle_history(db, player.albion_id, player.region),
            "top_weapons": _top_weapons(db, player),
            "kills": [_serialize_kill(db, ev, "kills", weapon_fn_map, battle_links) for ev in kill_rows_for_activity],
            "deaths": [_serialize_kill(db, ev, "deaths", weapon_fn_map, battle_links) for ev in death_rows_for_activity],
            "silver_dropped": await _silver_dropped(db, death_rows_for_activity),
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
async def search_players(q: str = Query(min_length=2), region: str = "americas", db: Session = Depends(deps.db_session)):
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
    local_ids: list[str] = list(db.scalars(
        select(SearchEntry.entity_id)
        .where(SearchEntry.entity_type == "player", SearchEntry.region == region,
               SearchEntry.norm_name >= lo, SearchEntry.norm_name < hi)
        .order_by(SearchEntry.weight.desc())
        .limit(20)
    ).all())
    if len(local_ids) < 20:
        local_ids += list(db.scalars(
            select(SearchEntry.entity_id)
            .where(
                SearchEntry.entity_type == "player", SearchEntry.region == region,
                SearchEntry.norm_name.like(f"%{nq}%"), SearchEntry.entity_id.notin_(local_ids),
            )
            .order_by(SearchEntry.weight.desc())
            .limit(20 - len(local_ids))
        ).all())

    local: list[AlbionPlayer] = []
    if local_ids:
        by_id = {p.albion_id: p for p in db.scalars(select(AlbionPlayer).where(AlbionPlayer.albion_id.in_(local_ids))).all()}
        local = [by_id[pid] for pid in local_ids if pid in by_id]
    if local:
        qlow = q.lower()
        local = sorted(local, key=lambda p: (not p.name.lower().startswith(qlow), p.name.lower()))
        return {"players": [_player_to_search_result(p) for p in local]}

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
                upsert_player(db, p, region)
            except Exception:
                pass

    return {"players": players}


def _synthetic_raw(player: AlbionPlayer) -> dict:
    """Reconstrói o formato bruto da API do Albion a partir do que já temos
    salvo — cobre os campos que o perfil realmente lê (ver
    PlayerProfilePage.tsx): Id/Name/Guild*/Alliance*/Avatar/*Fame e o
    LifetimeStatistics. Com lifetime_statistics preenchido (perfil/feed), o
    blob cru volta inteiro — inclui coleta por recurso (Wood/Ore/...), que os
    escalares não guardam. Sem o blob, reconstrói o mínimo a partir dos
    escalares (coleta por recurso só aparece depois de um refresh ao vivo)."""
    lifetime = player.lifetime_statistics
    if not lifetime:
        lifetime = {
            "PvE": {"Total": player.pve_fame},
            "Crafting": {"Total": player.crafting_fame},
            "Gathering": {"All": {"Total": player.gathering_fame}},
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


@router.get("/load-progress/{region}/{name}")
def get_load_progress(region: str, name: str):
    """Etapa da carga fria em andamento pra esse perfil (stage=null: nada
    em andamento — ou é um perfil já cacheado, que resolve na hora)."""
    entry = _load_progress.get(f"{region}:{name.lower()}")
    return {"stage": entry[1] if entry else None}


@router.get("/by-name/{region}/{name}")
async def get_player_by_name(region: str, name: str, db: Session = Depends(deps.db_session)):
    """Resolve `/am/Slayner` etc: busca o nome exato na região indicada,
    depois carrega o perfil completo já sabendo o host certo.

    Cache-first: se já temos esse jogador (visto antes, ver profile_warmer),
    mostra na hora o que já sabemos — sem chamada à Albion. Só busca ao vivo
    quando é a primeira vez que vemos esse nome (nada pra mostrar ainda)."""
    host = HOSTS.get(region)
    if host is None:
        raise HTTPException(400, "Região inválida")

    cached = db.scalar(
        select(AlbionPlayer).where(AlbionPlayer.region == region, func.lower(AlbionPlayer.name) == name.lower())
    )
    if cached is not None:
        _queue_refresh_if_stale(db, cached)
        return await _build_profile_payload(db, cached, _synthetic_raw(cached))

    progress_key = f"{region}:{name.lower()}"
    progress_token = object()
    _load_progress[progress_key] = (progress_token, "search")
    try:
        async with make_client() as c:
            async with albion_scope(PROFILE):
                try:
                    resp = await _get_with_retry(c, f"https://{host}/api/gameinfo/search", params={"q": name})
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    raise HTTPException(status_code=502, detail=f"Albion API: {e.response.status_code}")
                except httpx.RequestError as e:
                    raise HTTPException(status_code=502, detail=f"Erro de conexão: {e}")
                candidates = resp.json().get("players", [])
                # Nomes da Albion diferenciam maiúsculas/minúsculas — "Moskka" e
                # "MosKKa" são DUAS contas distintas. Prioriza match exato; só cai
                # pra case-insensitive se não achar nenhum (ex: erro de digitação na
                # URL) — nunca o contrário, senão pode resolver pra conta errada.
                match = next((p for p in candidates if p.get("Name") == name), None)
                if match is None:
                    match = next((p for p in candidates if p.get("Name", "").lower() == name.lower()), None)
                if match is None:
                    raise HTTPException(404, "Jogador não encontrado nessa região")
                _load_progress[progress_key] = (progress_token, "details")
                raw = await _fetch_player_raw(c, host, match["Id"])
                if raw is None:
                    raise HTTPException(404, "Jogador não encontrado")
                # Não depende só do feed global ter visto a luta desse jogador
                # específico — busca direto nos endpoints de kills/deaths dele.
                _load_progress[progress_key] = (progress_token, "kills")
                await sync_player_kills(c, db, host, region, raw["Id"])

        _load_progress[progress_key] = (progress_token, "build")
        player = upsert_player(db, raw, region)
        return await _build_profile_payload(db, player, raw)
    finally:
        # só limpa se a etapa ainda é desta run — não apaga a de outra em andamento
        entry = _load_progress.get(progress_key)
        if entry is not None and entry[0] is progress_token:
            _load_progress.pop(progress_key, None)


@router.get("/{albion_id}")
async def get_player(albion_id: str, region: str | None = None, db: Session = Depends(deps.db_session)):
    """Retorna perfil completo e salva snapshot no banco. Sem `region`
    informado, tenta os 3 hosts em sequência — um ID só responde 200 numa
    região (mesmo padrão de battle_tracker.resolve_by_albion_id).

    Cache-first: mesma lógica de get_player_by_name — só busca ao vivo se
    nunca vimos esse ID antes."""
    cached = db.scalar(select(AlbionPlayer).where(AlbionPlayer.albion_id == albion_id))
    if cached is not None:
        _queue_refresh_if_stale(db, cached)
        return await _build_profile_payload(db, cached, _synthetic_raw(cached))

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
                existing = db.scalar(select(AlbionPlayer).where(AlbionPlayer.albion_id == albion_id))
                if existing and not existing.is_deleted:
                    existing.is_deleted = True
                    db.commit()
                raise HTTPException(404, "Jogador não encontrado")
            await sync_player_kills(c, db, resolved_host, resolved_region, raw["Id"])

    player = upsert_player(db, raw, resolved_region)
    return await _build_profile_payload(db, player, raw)


@router.post("/{albion_id}/refresh")
async def request_player_refresh(albion_id: str, db: Session = Depends(deps.db_session)):
    """Enfileira uma atualização (botão ⟳ do perfil) — não busca nada agora,
    só marca o pedido; o profile_warmer processa em alta prioridade (PROFILE,
    reserved pool) e limpa o campo. O refresh_event acorda o warmer na hora,
    sem esperar o sleep idle."""
    player = db.scalar(select(AlbionPlayer).where(AlbionPlayer.albion_id == albion_id))
    if player is None:
        raise HTTPException(404, "Jogador não encontrado")
    player.refresh_requested_at = datetime.now(timezone.utc)
    db.commit()
    request_refresh()
    return {"queued": True}


@router.get("/{albion_id}/kills")
async def get_player_kills(albion_id: str, offset: int = 0, limit: int = 10):
    """Proxy direto do Albion — complementa o ledger próprio (PlayerKillEvent)
    com histórico de antes do ledger existir."""
    async with make_client() as c:
        async with albion_scope(PROFILE):
            try:
                async with slot():
                    resp = await c.get(
                        f"https://gameinfo.albiononline.com/api/gameinfo/players/{albion_id}/kills",
                        params={"offset": offset, "limit": limit},
                    )
                resp.raise_for_status()
            except httpx.RequestError as e:
                raise HTTPException(status_code=502, detail=str(e))
    return resp.json()


@router.get("/{albion_id}/deaths")
async def get_player_deaths(albion_id: str, offset: int = 0, limit: int = 10):
    async with make_client() as c:
        async with albion_scope(PROFILE):
            try:
                async with slot():
                    resp = await c.get(
                        f"https://gameinfo.albiononline.com/api/gameinfo/players/{albion_id}/deaths",
                        params={"offset": offset, "limit": limit},
                    )
                resp.raise_for_status()
            except httpx.RequestError as e:
                raise HTTPException(status_code=502, detail=str(e))
    return resp.json()
