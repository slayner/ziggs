"""Background task — sincroniza feed global de batalhas dos 3 servidores Albion.

Dois níveis de processamento (ver CLAUDE.md/plano):
- "light": resumo por guilda (kills/deaths/fame) — todas as batalhas, quase grátis.
- "deep": eventos de kill paginados + builds + detecção de lados — qualquer
  batalha com pelo menos DEEP_PROCESS_MIN_PLAYERS jogadores no resumo bruto,
  o suficiente pra cobrir lutas pequenas de road/hellgate, não só ZvZ.
  O corte de "30 vs 30" (is_zvz) é um rótulo aplicado por cima, confirmado
  depois da análise de lados — não é mais o critério pra processar ou não.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.battles import (
    Battle, BattleGuild, BattleKillEvent, BattleParticipant, BattleSide, BattleSyncCursor,
)
from app.services import battle_sides
from app.services.player_tracker import HOSTS, make_client

log = logging.getLogger(__name__)

POLL_INTERVAL = 300  # 5 min
DEEP_PROCESS_MIN_PLAYERS = 5    # pré-filtro bruto — baixo de propósito pra cobrir road/hellgate
ZVZ_MIN_PLAYERS_PER_SIDE = 30   # corte de is_zvz, confirmado após a análise de lados
DEEP_REPROCESS_WINDOW = timedelta(hours=1)  # batalha congela (para de reprocessar) depois disso
EVENTS_PAGE_LIMIT = 51
EVENTS_MAX_PAGES = 40  # teto de segurança (~2000 eventos) p/ não rodar infinito numa ZvZ gigante

BACKFILL_MAX_AGE = timedelta(days=365)  # não busca batalha mais velha que isso
BACKFILL_PAGE_SIZE = 51
BACKFILL_PAGES_PER_CYCLE = 3  # páginas de backfill por região a cada ciclo
BACKFILL_CYCLE_INTERVAL = 2  # segundos entre ciclos do loop de backfill (roda à parte do sync recente)

# A API de batalhas da Albion é paginada por um índice de busca com janela máxima
# de 10000 resultados: offset+limit acima disso não retorna lista vazia, retorna
# 500 sempre (confirmado: offset=9945 -> 200, offset=9950 -> 500, reproduzível).
# Cada região acumula mais de 10k batalhas em ~3 dias, então esse teto é atingido
# muito antes do BACKFILL_MAX_AGE de 365 dias — sem essa checagem o cursor nunca
# avança nem marca done, e o backfill fica retentando o mesmo offset pra sempre.
BATTLES_API_OFFSET_LIMIT = 10000

_EQUIP_SLOT_MAP = {
    "MainHand": "weapon", "OffHand": "offhand", "Head": "helmet",
    "Armor": "armor", "Shoes": "boots", "Cape": "cape",
    "Food": "food", "Potion": "potion", "Mount": "mount", "Bag": "bag",
}
# Só os slots de gear "de verdade" (exclui comida/poção/montaria/bag) — usado
# pra checar se uma vítima estava equipada de verdade (ver is_lethal).
_CORE_GEAR_SLOTS = ("weapon", "offhand", "helmet", "armor", "boots", "cape")


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _simplify_equipment(raw: dict) -> dict:
    out = {}
    for key, slot in _EQUIP_SLOT_MAP.items():
        item = raw.get(key)
        if item and item.get("Type"):
            out[slot] = item["Type"]
            if item.get("Quality"):
                out[f"{slot}_quality"] = item["Quality"]
    return out


def _simplify_inventory(raw: list[dict] | None) -> list[dict] | None:
    """Itens carregados (não equipados) — lista solta de {Type, Count}, sem
    slot fixo. Usado pra somar no valor aproximado da build de uma kill."""
    if not raw:
        return None
    out = [
        {"item_id": item["Type"], "count": int(item.get("Count") or 1)}
        for item in raw if item and item.get("Type")
    ]
    return out or None


async def fetch_battles(client: httpx.AsyncClient, host: str, limit: int = 51, offset: int = 0) -> list[dict]:
    resp = await client.get(
        f"https://{host}/api/gameinfo/battles",
        params={"sort": "recent", "limit": limit, "offset": offset},
    )
    resp.raise_for_status()
    return resp.json()


async def fetch_events(client: httpx.AsyncClient, host: str, albion_battle_id: str) -> list[dict]:
    events: list[dict] = []
    for page in range(EVENTS_MAX_PAGES):
        resp = await client.get(
            f"https://{host}/api/gameinfo/events/battle/{albion_battle_id}",
            params={"offset": page * EVENTS_PAGE_LIMIT, "limit": EVENTS_PAGE_LIMIT},
        )
        resp.raise_for_status()
        page_data = resp.json()
        if not isinstance(page_data, list) or not page_data:
            break
        events.extend(page_data)
        if len(page_data) < EVENTS_PAGE_LIMIT:
            break
    return events


def upsert_battle_light(db: Session, raw: dict, region: str) -> Battle | None:
    albion_id = str(raw.get("id", ""))
    if not albion_id:
        return None

    now = datetime.now(timezone.utc)
    players_total = len(raw.get("players") or {})
    battle = db.scalar(
        select(Battle).where(Battle.region == region, Battle.albion_id == albion_id)
    )

    if battle is None:
        battle = Battle(
            region=region,
            albion_id=albion_id,
            start_time=_parse_dt(raw["startTime"]),
            end_time=_parse_dt(raw["endTime"]) if raw.get("endTime") else None,
            total_fame=raw.get("totalFame") or 0,
            kill_count=raw.get("totalKills") or 0,
            cluster=raw.get("clusterName"),
            players_total=players_total,
            processing_tier="light",
            fetched_at=now,
        )
        db.add(battle)
        db.flush()
    else:
        battle.end_time = _parse_dt(raw["endTime"]) if raw.get("endTime") else battle.end_time
        battle.total_fame = raw.get("totalFame") or 0
        battle.kill_count = raw.get("totalKills") or 0
        battle.players_total = players_total
        battle.fetched_at = now

    for g in (raw.get("guilds") or {}).values():
        gid = g.get("id", "")
        if not gid:
            continue
        existing = db.scalar(
            select(BattleGuild).where(
                BattleGuild.battle_id == battle.id,
                BattleGuild.albion_guild_id == gid,
            )
        )
        if existing:
            existing.kill_fame = g.get("killFame") or 0
            existing.kills = g.get("kills") or 0
            existing.deaths = g.get("deaths") or 0
        else:
            db.add(BattleGuild(
                battle_id=battle.id,
                albion_guild_id=gid,
                guild_name=g.get("name", ""),
                alliance_id=g.get("allianceId") or None,
                alliance_name=g.get("alliance") or None,
                kill_fame=g.get("killFame") or 0,
                kills=g.get("kills") or 0,
                deaths=g.get("deaths") or 0,
            ))

    db.commit()
    return battle


def _is_frozen(battle: Battle, now: datetime) -> bool:
    if battle.end_time is None:
        return False  # ainda rolando
    end = battle.end_time if battle.end_time.tzinfo else battle.end_time.replace(tzinfo=timezone.utc)
    return now - end > DEEP_REPROCESS_WINDOW


def _touch_participant(participants: dict, p: dict) -> dict | None:
    pid = p.get("Id") or p.get("id")
    if not pid:
        return None
    row = participants.get(pid)
    if row is None:
        row = {
            "albion_player_id": pid,
            "name": p.get("Name") or p.get("name") or "",
            "guild_id": p.get("GuildId") or None,
            "guild_name": p.get("GuildName") or None,
            "alliance_id": p.get("AllianceId") or None,
            "alliance_name": p.get("AllianceName") or None,
            "kills": 0, "deaths": 0, "kill_fame": 0, "ip": 0.0,
            "damage_dealt": 0.0, "damage_taken": 0.0, "healing_done": 0.0,
            "equipment": None,
        }
        participants[pid] = row
    if p.get("Equipment"):
        eq = _simplify_equipment(p["Equipment"])
        if eq:
            builds = row["equipment"] or []
            if eq not in builds:
                builds.append(eq)
            row["equipment"] = builds
    ip = p.get("AverageItemPower") or 0
    if ip > row["ip"]:
        row["ip"] = ip
    return row


def _seed_from_summary(raw: dict) -> dict[str, dict]:
    """Os eventos de kill só cobrem quem matou/morreu/ajudou num kill REGISTRADO
    — numa ZvZ de 300 pessoas com só 100 kills, é normal mais de 1/3 do pelotão
    nunca aparecer ali (lutaram mas não fecharam nem assistiram nenhuma kill
    creditada). O resumo da batalha (`players`) já lista todo mundo que esteve
    na luta, então o roster vem de lá; os eventos só REFINAM (build/ip/dano/cura)."""
    participants: dict[str, dict] = {}
    for pid, p in (raw.get("players") or {}).items():
        participants[pid] = {
            "albion_player_id": pid,
            "name": p.get("name") or "",
            "guild_id": p.get("guildId") or None,
            "guild_name": p.get("guildName") or None,
            "alliance_id": p.get("allianceId") or None,
            "alliance_name": p.get("allianceName") or None,
            "kills": p.get("kills") or 0,
            "deaths": p.get("deaths") or 0,
            "kill_fame": p.get("killFame") or 0,
            "ip": 0.0, "damage_dealt": 0.0, "damage_taken": 0.0, "healing_done": 0.0,
            "equipment": None,
        }
    return participants


async def _fetch_deep_data(client: httpx.AsyncClient, host: str, battle: Battle) -> tuple[dict | None, list[dict]]:
    """Só a parte de rede de deep_process (sem toque na DB) — pode ser chamada
    em paralelo pra várias batalhas de uma vez, ver backfill_step."""
    for attempt in range(3):
        try:
            raw = await fetch_battle_detail(client, host, battle.albion_id)
            events = await fetch_events(client, host, battle.albion_id)
            return raw, events
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)


def _write_deep_data(db: Session, battle: Battle, raw: dict | None, events: list[dict]) -> None:
    if not events and not raw:
        return

    # Reconstrói do zero a cada poll: batalhas ZvZ têm no máx. ~2000 eventos,
    # é mais simples e idempotente do que fazer merge incremental.
    db.query(BattleKillEvent).filter(BattleKillEvent.battle_id == battle.id).delete()
    db.query(BattleParticipant).filter(BattleParticipant.battle_id == battle.id).delete()
    db.query(BattleSide).filter(BattleSide.battle_id == battle.id).delete()
    db.flush()

    participants: dict[str, dict] = _seed_from_summary(raw) if raw else {}
    kills_between: dict[tuple[str, str], int] = {}
    kill_rows: list[tuple[
        str, str, int, str | None, str | None,
        dict | None, dict | None, list[dict] | None, list[dict] | None,
    ]] = []

    for ev in events:
        killer, victim = ev.get("Killer") or {}, ev.get("Victim") or {}
        krow, vrow = _touch_participant(participants, killer), _touch_participant(participants, victim)

        for p in (ev.get("Participants") or []):
            prow = _touch_participant(participants, p)
            if prow is not None:
                prow["damage_dealt"] += float(p.get("DamageDone") or 0)
                prow["healing_done"] += float(p.get("SupportHealingDone") or 0)

        # kills/deaths/kill_fame já vêm autoritativos do resumo (_seed_from_summary)
        # — aqui só soma dano tomado, que não existe lá.
        if vrow is not None:
            vrow["damage_taken"] += sum(
                float(p.get("DamageDone") or 0) for p in (ev.get("Participants") or [])
            )

        fame = int(ev.get("TotalVictimKillFame") or 0)
        if krow is not None and vrow is not None:
            kf = battle_sides.faction_key(krow["guild_id"], krow["alliance_id"])
            vf = battle_sides.faction_key(vrow["guild_id"], vrow["alliance_id"])
            kills_between[(kf, vf)] = kills_between.get((kf, vf), 0) + 1

        kill_rows.append((
            str(ev.get("EventId")), ev.get("TimeStamp"), fame,
            killer.get("Id") or killer.get("id"),
            victim.get("Id") or victim.get("id"),
            _simplify_equipment(killer["Equipment"]) if killer.get("Equipment") else None,
            _simplify_equipment(victim["Equipment"]) if victim.get("Equipment") else None,
            _simplify_inventory(killer.get("Inventory")),
            _simplify_inventory(victim.get("Inventory")),
        ))

    if not participants:
        return

    factions, player_faction = battle_sides.build_factions(participants)

    analysis = battle_sides.analyze(factions, kills_between)

    side_rows: dict[str, BattleSide] = {}
    for label in set(analysis.side_of.values()):
        side = BattleSide(
            battle_id=battle.id, label=label, is_rats=(label == "rats"),
            player_count=analysis.player_count.get(label, 0),
            score=analysis.score.get(label, 0),
        )
        db.add(side)
        side_rows[label] = side
    db.flush()

    participant_rows: dict[str, BattleParticipant] = {}
    for pid, row in participants.items():
        side = side_rows.get(analysis.side_of.get(player_faction[pid], "rats"))
        prow = BattleParticipant(
            battle_id=battle.id, albion_player_id=pid, name=row["name"],
            guild_id=row["guild_id"], guild_name=row["guild_name"],
            alliance_id=row["alliance_id"], alliance_name=row["alliance_name"],
            side_id=side.id if side else None,
            kills=row["kills"], deaths=row["deaths"], kill_fame=row["kill_fame"],
            ip=row["ip"], damage_dealt=row["damage_dealt"],
            damage_taken=row["damage_taken"], healing_done=row["healing_done"],
            equipment=row["equipment"],
        )
        db.add(prow)
        participant_rows[pid] = prow
    db.flush()

    is_lethal = True
    for albion_event_id, ts, fame, kid, vid, killer_equipment, victim_equipment, killer_inventory, victim_inventory in kill_rows:
        krow, vrow = participant_rows.get(kid), participant_rows.get(vid)
        db.add(BattleKillEvent(
            battle_id=battle.id, albion_event_id=albion_event_id,
            timestamp=_parse_dt(ts), fame=fame,
            killer_participant_id=krow.id if krow else None,
            victim_participant_id=vrow.id if vrow else None,
            killer_side_id=krow.side_id if krow else None,
            victim_side_id=vrow.side_id if vrow else None,
            killer_equipment=killer_equipment,
            victim_equipment=victim_equipment,
            killer_inventory=killer_inventory,
            victim_inventory=victim_inventory,
        ))
        # Cada Battle.albion_id é UM mapa só — uma morte de jogador com
        # equipamento (não pelado) que deu fama 0 já prova que a zona inteira
        # não é letal (duelo/arena). Pelado com fama 0 é normal, não conta.
        if fame == 0 and victim_equipment and any(victim_equipment.get(s) for s in _CORE_GEAR_SLOTS):
            is_lethal = False

    for bg in db.scalars(select(BattleGuild).where(BattleGuild.battle_id == battle.id)):
        fk = battle_sides.faction_key(bg.albion_guild_id, bg.alliance_id)
        label = analysis.side_of.get(fk)
        bg.side_id = side_rows[label].id if label else None

    battle.processing_tier = "deep"
    a_count = analysis.player_count.get("A", 0)
    b_count = analysis.player_count.get("B", 0)
    battle.is_zvz = a_count >= ZVZ_MIN_PLAYERS_PER_SIDE and b_count >= ZVZ_MIN_PLAYERS_PER_SIDE
    battle.is_lethal = is_lethal
    db.commit()


async def deep_process(client: httpx.AsyncClient, db: Session, battle: Battle, host: str) -> None:
    raw, events = await _fetch_deep_data(client, host, battle)
    _write_deep_data(db, battle, raw, events)


async def fetch_battle_detail(client: httpx.AsyncClient, host: str, albion_id: str) -> dict | None:
    resp = await client.get(f"https://{host}/api/gameinfo/battles/{albion_id}")
    if resp.status_code != 200:
        return None
    data = resp.json()
    return data if isinstance(data, dict) and data.get("id") else None


async def resolve_by_albion_id(client: httpx.AsyncClient, db: Session, albion_id: str) -> Battle | None:
    """Acha a batalha pelo ID cru do Albion — primeiro na nossa base (qualquer
    região), senão tenta os 3 hosts (cada ID só existe de fato numa região, as
    outras 2 respondem 404). Resolvida explicitamente por alguém, sempre processa
    em profundidade (builds/lados) mesmo que a luta seja pequena."""
    existing = db.scalars(
        select(Battle).where(Battle.albion_id == albion_id).order_by(Battle.start_time.desc())
    ).all()
    battle = existing[0] if existing else None
    host = HOSTS.get(battle.region) if battle else None

    if battle is None:
        for region, candidate_host in HOSTS.items():
            try:
                raw = await fetch_battle_detail(client, candidate_host, albion_id)
            except Exception:
                continue
            if raw is None:
                continue
            battle = upsert_battle_light(db, raw, region)
            host = candidate_host
            break

    if battle is None:
        return None

    if battle.processing_tier != "deep" or not _is_frozen(battle, datetime.now(timezone.utc)):
        try:
            await deep_process(client, db, battle, host)
        except Exception as e:
            log.warning("battle_tracker: falha ao resolver %s: %s", albion_id, e)

    return battle


async def _get_cursor(db: Session, region: str) -> BattleSyncCursor:
    cursor = db.get(BattleSyncCursor, region)
    if cursor is None:
        cursor = BattleSyncCursor(region=region, next_offset=0, done=False)
        db.add(cursor)
        db.flush()
    return cursor


_BACKFILL_CONCURRENCY = 6  # deep-processa várias batalhas históricas em paralelo (só a parte de rede)


async def _backfill_deep_fetch_all(
    client: httpx.AsyncClient, host: str, battles: list[Battle],
) -> list[tuple[Battle, dict | None, list[dict]] | tuple[Battle, Exception]]:
    """Busca em paralelo (rede só, sem DB) os dados profundos de várias batalhas
    de uma vez — é a paginação de eventos (até 40 páginas/batalha) que faz o
    backfill sequencial ser absurdamente lento, então aqui é onde o tempo de
    espera de rede das várias batalhas se sobrepõe em vez de somar."""
    sem = asyncio.Semaphore(_BACKFILL_CONCURRENCY)

    async def fetch_one(battle: Battle):
        async with sem:
            try:
                raw, events = await _fetch_deep_data(client, host, battle)
                return battle, raw, events
            except Exception as e:
                return battle, e

    return await asyncio.gather(*[fetch_one(b) for b in battles])


RETRY_STUCK_BATCH = 20  # batalhas travadas em "light" retentadas por região a cada ciclo


async def _retry_stuck_battles(client: httpx.AsyncClient, db: Session, region: str, host: str) -> None:
    """Batalhas que qualificavam pra deep (players_total >= DEEP_PROCESS_MIN_PLAYERS)
    mas tiveram o fetch falhando uma vez (rede instável, rate limit etc.) nunca são
    revisitadas pelo fluxo normal: o cursor do backfill só avança pra frente e o
    sync_recent só olha o topo do feed e desiste depois de _is_frozen. Isso varre
    o que ficou "light" indevidamente e tenta de novo — roda só depois que o
    backfill histórico da região termina (ver chamada em backfill_step)."""
    stuck = db.scalars(
        select(Battle)
        .where(
            Battle.region == region,
            Battle.processing_tier == "light",
            Battle.players_total >= DEEP_PROCESS_MIN_PLAYERS,
        )
        .order_by(Battle.start_time.desc())
        .limit(RETRY_STUCK_BATCH)
    ).all()
    if not stuck:
        return

    for result in await _backfill_deep_fetch_all(client, host, stuck):
        battle = result[0]
        if isinstance(result[1], Exception):
            log.warning("battle_tracker: retry de %s (%s) falhou de novo: %r",
                        battle.albion_id, region, result[1])
            continue
        _, raw, events = result
        try:
            _write_deep_data(db, battle, raw, events)
        except Exception as e:
            db.rollback()
            log.warning("battle_tracker: falha ao salvar retry de %s (%s): %r",
                        battle.albion_id, region, e)


async def backfill_step(client: httpx.AsyncClient, db: Session, region: str, host: str) -> None:
    """Avança a paginação histórica de uma região: busca batalhas mais antigas
    que as já cobertas pelo sync "recente", processa em profundidade (em
    paralelo, ver _backfill_deep_fetch_all) as que qualificam, e persiste o
    cursor pra continuar de onde parou no próximo ciclo. Para de vez
    (done=True) quando a API não tem mais nada ou quando a batalha mais velha
    da página já passou de BACKFILL_MAX_AGE — depois disso o cursor não
    avança mais, não tem nada mais velho pra buscar, e o ciclo passa a
    retentar batalhas que ficaram travadas em "light" (ver _retry_stuck_battles)."""
    cursor = await _get_cursor(db, region)
    if cursor.done:
        await _retry_stuck_battles(client, db, region, host)
        return

    cutoff = datetime.now(timezone.utc) - BACKFILL_MAX_AGE

    for _ in range(BACKFILL_PAGES_PER_CYCLE):
        if cursor.next_offset + BACKFILL_PAGE_SIZE > BATTLES_API_OFFSET_LIMIT:
            # Acima da janela máxima da API (ver BATTLES_API_OFFSET_LIMIT) — não
            # tem como buscar mais, isso é o fim do histórico disponível.
            cursor.done = True
            db.commit()
            return

        try:
            batch = await fetch_battles(client, host, limit=BACKFILL_PAGE_SIZE, offset=cursor.next_offset)
        except Exception as e:
            log.warning("battle_tracker: falha no backfill (%s, offset=%d): %r", region, cursor.next_offset, e)
            return

        if not batch:
            cursor.done = True
            db.commit()
            return

        reached_cutoff = False
        qualifying: list[Battle] = []
        for raw in batch:
            if _parse_dt(raw["startTime"]) < cutoff:
                reached_cutoff = True
                break
            try:
                battle = upsert_battle_light(db, raw, region)
            except Exception as e:
                log.debug("battle_tracker: skip backfill %s (%s): %s", raw.get("id"), region, e)
                continue
            if battle is None or battle.processing_tier == "deep" or battle.players_total < DEEP_PROCESS_MIN_PLAYERS:
                continue
            qualifying.append(battle)
        db.commit()

        if qualifying:
            for result in await _backfill_deep_fetch_all(client, host, qualifying):
                battle = result[0]
                if isinstance(result[1], Exception):
                    log.warning("battle_tracker: falha no backfill profundo de %s (%s): %r",
                                battle.albion_id, region, result[1])
                    continue
                _, raw, events = result
                try:
                    _write_deep_data(db, battle, raw, events)
                except Exception as e:
                    db.rollback()
                    log.warning("battle_tracker: falha ao salvar backfill profundo de %s (%s): %r",
                                battle.albion_id, region, e)

        cursor.next_offset += len(batch)
        if reached_cutoff or len(batch) < BACKFILL_PAGE_SIZE:
            cursor.done = True
            db.commit()
            return
        db.commit()


async def sync_recent() -> int:
    """Busca e salva as batalhas mais recentes dos 3 servidores e processa em
    profundidade as que parecem ZvZ. Roda sozinho (sem o backfill histórico
    junto) pra não ter o feed "recente" atrasado por um backfill lento —
    ver run_backfill_forever, que cuida do histórico em paralelo."""
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    count = 0
    try:
        async with make_client() as client:
            for region, host in HOSTS.items():
                try:
                    battles = await fetch_battles(client, host)
                except Exception as e:
                    log.warning("battle_tracker: falha ao buscar feed (%s): %s", region, e)
                    continue

                for raw in battles:
                    try:
                        battle = upsert_battle_light(db, raw, region)
                    except Exception as e:
                        log.debug("battle_tracker: skip %s (%s): %s", raw.get("id"), region, e)
                        continue
                    if battle is None:
                        continue
                    count += 1

                    if battle.players_total < DEEP_PROCESS_MIN_PLAYERS or _is_frozen(battle, now):
                        continue
                    try:
                        await deep_process(client, db, battle, host)
                    except Exception as e:
                        log.warning("battle_tracker: falha no processamento profundo de %s (%s): %s",
                                    battle.albion_id, region, e)
    finally:
        db.close()
    return count


async def backfill_cycle() -> None:
    """Um ciclo de backfill histórico das 3 regiões — ver run_backfill_forever."""
    db = SessionLocal()
    try:
        async with make_client() as client:
            for region, host in HOSTS.items():
                try:
                    await backfill_step(client, db, region, host)
                except Exception as e:
                    log.warning("battle_tracker: falha no backfill (%s): %s", region, e)
    finally:
        db.close()


async def run_forever() -> None:
    log.info("battle_tracker: iniciando (intervalo=%ds, hosts=%s)", POLL_INTERVAL, list(HOSTS))
    while True:
        try:
            n = await sync_recent()
            log.debug("battle_tracker: %d batalhas atualizadas", n)
        except Exception as e:
            log.error("battle_tracker: erro: %s", e)
        await asyncio.sleep(POLL_INTERVAL)


async def run_backfill_forever() -> None:
    """Backfill histórico roda no seu próprio loop, sem esperar o ciclo de
    5min do sync recente — antes os dois rodavam em série dentro do mesmo
    intervalo, então uma batalha histórica grande (paginação de até 40
    páginas de evento) atrasava tanto o próximo passo do backfill quanto a
    checagem de batalhas novas. Cada ciclo aqui já processa em paralelo
    (ver _backfill_deep_fetch_all), então o sleep curto entre ciclos é só
    pra não martelar a API da Albion sem necessidade."""
    log.info("battle_tracker: backfill iniciando (concorrência=%d)", _BACKFILL_CONCURRENCY)
    while True:
        try:
            await backfill_cycle()
        except Exception as e:
            log.error("battle_tracker: erro no backfill: %s", e)
        await asyncio.sleep(BACKFILL_CYCLE_INTERVAL)
