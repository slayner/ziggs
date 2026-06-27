"""Rotas públicas de battle tracker — sem escopar por guilda."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api import deps
from app.models.battles import Battle, BattleGuild, BattleKillEvent, BattleParticipant, BattleSide
from app.models.catalog import Weapon
from app.models.players import PlayerKillEvent
from app.services import battle_groups, battle_sides, battle_tracker, prices

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


def _weapon_function_map(db: Session) -> dict[str, str]:
    out: dict[str, str] = {}
    for item_id, fn in db.execute(select(Weapon.item_id, Weapon.invisible_function)).all():
        if fn:
            out[_wbase(item_id)] = fn
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


def _active_player_count(db: Session, region: str | None, start: datetime, end: datetime) -> int:
    """Distintos com kill OU morte no ledger na janela — não dá pra fazer
    direto em SQL com clareza (união de 2 colunas), então junta em Python;
    volume de uma janela de 7 dias é pequeno o bastante pra não pesar."""
    q = select(PlayerKillEvent.killer_player_id, PlayerKillEvent.victim_player_id).where(
        PlayerKillEvent.timestamp >= start, PlayerKillEvent.timestamp < end,
    )
    if region:
        q = q.where(PlayerKillEvent.region == region)
    ids: set[int] = set()
    for killer_id, victim_id in db.execute(q).all():
        if killer_id is not None:
            ids.add(killer_id)
        if victim_id is not None:
            ids.add(victim_id)
    return len(ids)


@router.get("/active-players")
def active_players(db: Session = Depends(deps.db_session)):
    """Jogadores distintos (kill ou morte) nos últimos 7 dias, por região +
    global, comparado com os 7 dias anteriores. Sem tabela de snapshot: o
    ledger (PlayerKillEvent) nunca é apagado, a janela anterior sempre pode
    ser recalculada — "histórico" vem de graça."""
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    prev_start = now - timedelta(days=14)

    def stat(region: str | None) -> dict:
        current = _active_player_count(db, region, week_start, now)
        previous = _active_player_count(db, region, prev_start, week_start)
        delta_pct = round((current - previous) / previous * 100) if previous else None
        return {"current": current, "previous": previous, "delta_pct": delta_pct}

    return {
        "americas": stat("americas"),
        "europe": stat("europe"),
        "asia": stat("asia"),
        "global": stat(None),
    }


@router.get("/highlights")
def battle_highlights(regions: str | None = None, db: Session = Depends(deps.db_session)):
    """Top 7 jogadores que mais apareceram em batalhas "de verdade" nos
    últimos 7 dias — só conta luta com mais de 5 jogadores, que teve cura
    (filtra ganks pequenos/solo) E que é letal (filtra duelo/arena disfarçado
    de luta grande, ver Battle.is_lethal em battle_tracker._write_deep_data)."""
    week_start = datetime.now(timezone.utc) - timedelta(days=7)

    q = select(Battle.id).where(
        Battle.processing_tier == "deep",
        Battle.players_total > 5,
        Battle.start_time >= week_start,
        Battle.is_lethal.is_(True),
    )
    if regions:
        q = q.where(Battle.region.in_([r.strip() for r in regions.split(",") if r.strip()]))
    q = q.where(Battle.id.in_(select(BattleParticipant.battle_id).where(BattleParticipant.healing_done > 0)))

    qualifying_ids = db.scalars(q).all()
    if not qualifying_ids:
        return {"players": []}

    counts = db.execute(
        select(BattleParticipant.albion_player_id, func.count(BattleParticipant.id).label("appearances"))
        .where(BattleParticipant.battle_id.in_(qualifying_ids))
        .group_by(BattleParticipant.albion_player_id)
        .order_by(func.count(BattleParticipant.id).desc())
        .limit(7)
    ).all()
    if not counts:
        return {"players": []}

    player_ids = [r.albion_player_id for r in counts]
    appearances = {r.albion_player_id: r.appearances for r in counts}

    # Nome/guilda/região podem variar entre as batalhas da janela (multi-
    # servidor) — busca o registro mais recente de cada jogador pra exibição
    # numa query separada (em vez de incluir essas colunas no GROUP BY acima,
    # que o Postgres rejeita sem agregação — diferente do SQLite, que aceita
    # de boa).
    latest_by_player: dict[str, tuple[BattleParticipant, str]] = {}
    for bp, region in db.execute(
        select(BattleParticipant, Battle.region)
        .join(Battle, Battle.id == BattleParticipant.battle_id)
        .where(BattleParticipant.albion_player_id.in_(player_ids), BattleParticipant.battle_id.in_(qualifying_ids))
        .order_by(BattleParticipant.battle_id.desc())
    ):
        latest_by_player.setdefault(bp.albion_player_id, (bp, region))

    return {"players": [
        {
            "albion_player_id": pid,
            "name": latest_by_player[pid][0].name,
            "guild_name": latest_by_player[pid][0].guild_name,
            "alliance_name": latest_by_player[pid][0].alliance_name,
            "region": latest_by_player[pid][1],
            "appearances": appearances[pid],
        }
        for pid in player_ids
    ]}


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
        term = f"%{search}%"
        guild_battle_ids = select(BattleGuild.battle_id).where(
            or_(BattleGuild.guild_name.ilike(term), BattleGuild.alliance_name.ilike(term))
        )
        player_battle_ids = select(BattleParticipant.battle_id).where(BattleParticipant.name.ilike(term))
        matching_battle_ids = set(db.scalars(guild_battle_ids).all()) | set(db.scalars(player_battle_ids).all())
        q = q.where(or_(Battle.id.in_(matching_battle_ids), Battle.cluster.ilike(term)))

    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    battles = db.scalars(q.order_by(Battle.start_time.desc()).limit(limit).offset(offset)).all()

    out = []
    for b in battles:
        # Toda batalha que aparece no feed ganha um link público (se ainda não tiver).
        group = battle_groups.get_or_create_group(db, [b.id])
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
        kf = battle_sides.faction_key(merged[k]["guild_id"], merged[k]["alliance_id"])
        vf = battle_sides.faction_key(merged[v]["guild_id"], merged[v]["alliance_id"])
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
    async with battle_tracker.make_client() as client:
        for albion_id in ids:
            battle = await battle_tracker.resolve_by_albion_id(client, db, albion_id)
            if battle is not None:
                resolved.append(battle)

    if not resolved:
        raise HTTPException(404, "Nenhuma dessas batalhas foi encontrada (nem na nossa base, nem na API do Albion)")

    group = battle_groups.get_or_create_group(db, [b.id for b in resolved])
    return _combined_detail(db, [b.id for b in resolved], group.public_id)


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
