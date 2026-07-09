"""Background task — sincroniza feed global de batalhas dos 3 servidores Albion.

Dois níveis de processamento (ver CLAUDE.md/plano):
- "light": resumo por guilda (kills/deaths/fame) — todas as batalhas, quase grátis.
- "deep": eventos de kill paginados + builds + detecção de lados — TODA
  batalha, sem mínimo de jogadores (ver DEEP_PROCESS_MIN_PLAYERS), pra
  alimentar os contadores de arma all-time (app.services.weapon_stats) com
  toda aparição, inclusive as não-elegíveis pro KB.
  O corte de "30 vs 30" (is_zvz) é um rótulo aplicado por cima, confirmado
  depois da análise de lados — não é critério pra processar ou não.
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
from app.services.albion_gate import (
    NEW_ELIGIBLE, OLD_ELIGIBLE, OTHER, albion_scope, battle_priority, slot,
)
from app.services.player_tracker import HOSTS, make_client

log = logging.getLogger(__name__)

POLL_INTERVAL = 60  # 1 min — pedido explícito; deep-process é paralelo e compete no bg pool do albion_gate (6 slots), sobra folga
DEEP_PROCESS_MIN_PLAYERS = 0    # sem mínimo — toda batalha é deep-processada, até 1v1/gank

# Motivos de reprocess_reason marcados pelo caminho de DESCOBERTA AO VIVO
# (bateu em algo agora, batalha nova fica "invisível" pro usuário até
# resolver) — battle_reprocessor.py prioriza esses sobre motivos de campanha
# histórica de fundo (ex.: "guildless_bracket", sem pressa nenhuma), senão
# uma fila de milhares de itens antigos deixa a correção urgente esperando
# atrás de trabalho que pode esperar.
REPROCESS_REASON_EMPTY = "deep_process_empty"    # API ainda sem detalhe/eventos (comum pra batalha muito recente)
REPROCESS_REASON_FAILED = "deep_process_failed"  # erro de rede/HTTP ao buscar
REPROCESS_REASON_SWEEPER = "sweeper"  # batalha achada por sondagem de ID (battle_sweeper), aguarda deep-process na fila de fundo
ZVZ_MIN_PLAYERS_PER_SIDE = 30   # corte de is_zvz, confirmado após a análise de lados
DEEP_REPROCESS_WINDOW = timedelta(hours=1)  # batalha congela (para de reprocessar) depois disso
EVENTS_PAGE_LIMIT = 51
EVENTS_MAX_PAGES = 40  # teto de segurança (~2000 eventos) p/ não rodar infinito numa ZvZ gigante

BACKFILL_MAX_AGE = timedelta(days=365)  # não busca batalha mais velha que isso
BACKFILL_PAGE_SIZE = 51
BACKFILL_PAGES_PER_CYCLE = 3  # páginas de backfill por região a cada ciclo
BACKFILL_CYCLE_INTERVAL = 20  # segundos entre ciclos do loop de backfill (roda à parte do sync recente)
# ponytail: era 2s — dava ~9 requisições de listagem a cada 2s só do backfill,
# somado a retry_stuck/reprocessor/small_battle_discovery rodando em paralelo
# cada um no seu próprio ritmo curto, a taxa AGREGADA de requisições (não a
# concorrência, já limitada por semáforo) estourava o rate limit da API do
# Albion de forma sustentada — 429 em cascata que nem o backoff por chamada
# resolvia, porque a pressão total nunca dava trégua. É trabalho de fundo,
# "no seu próprio ritmo" (pedido explícito) — não precisa ser rápido.

# A API de batalhas da Albion é paginada por um índice de busca com janela máxima
# de 10000 resultados: offset+limit acima disso não retorna lista vazia, retorna
# 500 sempre (confirmado: offset=9945 -> 200, offset=9950 -> 500, reproduzível).
# Cada região acumula mais de 10k batalhas em ~3 dias, então esse teto é atingido
# muito antes do BACKFILL_MAX_AGE de 365 dias — sem essa checagem o cursor nunca
# avança nem marca done, e o backfill fica retentando o mesmo offset pra sempre.
BATTLES_API_OFFSET_LIMIT = 10000

PAGE_PAUSE = 0.5  # segundos entre páginas na varredura reversa de startup (ver _reverse_startup_sweep)

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
    async with slot():
        resp = await client.get(
            f"https://{host}/api/gameinfo/battles",
            params={"sort": "recent", "limit": limit, "offset": offset},
        )
    resp.raise_for_status()
    return resp.json()


async def fetch_events(client: httpx.AsyncClient, host: str, albion_battle_id: str) -> list[dict]:
    events: list[dict] = []
    for page in range(EVENTS_MAX_PAGES):
        async with slot():
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
    """NÃO comita — quem chama isso num laço (sync_recent, backfill_step)
    deve comitar UMA VEZ depois do laço inteiro, não a cada batalha. Commit
    por batalha (até ~400/região por ciclo agora) significava centenas de
    fsyncs/WAL-checkpoints por ciclo, disputando o lock do SQLite com todo
    outro serviço de fundo — era o maior gargalo do sync "recente" ficando
    lento e deixando batalha nova pra trás."""
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

    db.flush()  # visível pra outras queries NESTA sessão, sem fsync — quem chama comita no fim do laço
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
            "damage_dealt": 0.0, "damage_taken": 0.0, "healing_done": 0.0, "assists": 0,
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
            "ip": 0.0, "damage_dealt": 0.0, "damage_taken": 0.0, "healing_done": 0.0, "assists": 0,
            "equipment": None,
        }
    return participants


async def _fetch_deep_data(client: httpx.AsyncClient, host: str, battle: Battle) -> tuple[dict | None, list[dict]]:
    """Só a parte de rede de deep_process (sem toque na DB) — pode ser chamada
    em paralelo pra várias batalhas de uma vez, ver backfill_step.

    429 (rate limit) espera o Retry-After da própria API se vier, senão um
    backoff crescente, antes de tentar de novo — sem isso a batalha falhava
    na hora e só voltava a ser tentada num ciclo futuro (via reprocess_reason,
    ver _deep_process_batch), o que sob rate limit sustentado (muitos
    serviços de fundo batendo na mesma API ao mesmo tempo) virava uma
    cascata de 429 em vez de se recuperar sozinho."""
    for attempt in range(3):
        try:
            raw = await fetch_battle_detail(client, host, battle.albion_id)
            events = await fetch_events(client, host, battle.albion_id)
            return raw, events
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 or attempt == 2:
                raise
            retry_after = e.response.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else 5.0 * (attempt + 1)
            await asyncio.sleep(wait)


def _write_deep_data(db: Session, battle: Battle, raw: dict | None, events: list[dict]) -> bool:
    """Retorna False quando não deu pra gravar nada (API sem detalhe/eventos
    ainda, ou eventos sem participante nenhum) — NÃO significa "não tinha
    nada pra processar": Battle.kill_count>0 do resumo leve já prova que
    houve mortes de verdade (uma morte só acontece se alguém matou). É comum
    pra batalha muito recente cujo endpoint de detalhe/eventos ainda não
    indexou — o chamador (_deep_process_batch/_retry_stuck_battles) precisa
    tratar False como falha e marcar reprocess_reason, senão a batalha fica
    travada em "light" pra sempre, sem nenhum sinal de erro (foi exatamente
    isso que deixava batalha nova com "zona desconhecida" e detalhe vazio)."""
    if not events and not raw:
        return False

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
        killer_id = killer.get("Id") or killer.get("id")

        for p in (ev.get("Participants") or []):
            prow = _touch_participant(participants, p)
            if prow is not None:
                prow["damage_dealt"] += float(p.get("DamageDone") or 0)
                prow["healing_done"] += float(p.get("SupportHealingDone") or 0)
                # Participants[] inclui o próprio matador — só conta assist
                # quem participou SEM ser quem deu o golpe final.
                pid = p.get("Id") or p.get("id")
                if pid and pid != killer_id:
                    prow["assists"] += 1

        # kills/deaths/kill_fame já vêm autoritativos do resumo (_seed_from_summary)
        # — aqui só soma dano tomado, que não existe lá.
        if vrow is not None:
            vrow["damage_taken"] += sum(
                float(p.get("DamageDone") or 0) for p in (ev.get("Participants") or [])
            )

        fame = int(ev.get("TotalVictimKillFame") or 0)
        if krow is not None and vrow is not None:
            kf = battle_sides.faction_key(krow["guild_id"], krow["alliance_id"], krow["albion_player_id"])
            vf = battle_sides.faction_key(vrow["guild_id"], vrow["alliance_id"], vrow["albion_player_id"])
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
        return False

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
            assists=row["assists"], equipment=row["equipment"],
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
        # BattleGuild só existe pra guilda de verdade (albion_guild_id nunca
        # vazio aqui, ver upsert_battle_light) — nunca cai no fallback por
        # jogador, não precisa de player_id.
        fk = battle_sides.faction_key(bg.albion_guild_id, bg.alliance_id, None)
        label = analysis.side_of.get(fk)
        bg.side_id = side_rows[label].id if label else None

    battle.processing_tier = "deep"
    a_count = analysis.player_count.get("A", 0)
    b_count = analysis.player_count.get("B", 0)
    battle.is_zvz = a_count >= ZVZ_MIN_PLAYERS_PER_SIDE and b_count >= ZVZ_MIN_PLAYERS_PER_SIDE
    battle.is_lethal = is_lethal
    db.commit()
    return True


async def deep_process(client: httpx.AsyncClient, db: Session, battle: Battle, host: str) -> None:
    raw, events = await _fetch_deep_data(client, host, battle)
    if not _write_deep_data(db, battle, raw, events):
        battle.reprocess_reason = battle.reprocess_reason or REPROCESS_REASON_EMPTY
        db.commit()


async def fetch_battle_detail(client: httpx.AsyncClient, host: str, albion_id: str) -> dict | None:
    async with slot():
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
            db.commit()
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


# Concorrência de deep-fetch agora vive no bg pool do albion_gate (6 slots,
# heap por prioridade). Cada fetch_one seta o scope da batalha (NEW/OLD ×
# ELIGIBLE/SMALL) e os `slot()` dentro de fetch_battle_detail/fetch_events
# disputam o bg pool — prioridade decide quem entra primeiro quando o pool
# enche. Antes eram 3 Semaphore(4) (=12) sem ordem entre sync_recent/backfill/
# reprocessor; agora 6 slots com ordem, e perfil/claim/regear têm reserved
# pool separado (ver albion_gate) então nunca esperam atrás disso aqui.

# priority_fn(battle) -> int: qual tier de bg pool pra deep-fetch dessa batalha.
# Callers escolhem conforme o ciclo (sync_recent=novo, backfill=velho, sweep=OTHER).
def _prio_new(b: Battle) -> int: return battle_priority(b, is_new=True)
def _prio_old(b: Battle) -> int: return battle_priority(b, is_new=False)
def _prio_other(_b: Battle) -> int: return OTHER


async def _backfill_deep_fetch_all(
    client: httpx.AsyncClient, host: str, battles: list[Battle], *, priority_fn,
) -> list[tuple[Battle, dict | None, list[dict]] | tuple[Battle, Exception]]:
    """Busca em paralelo (rede só, sem DB) os dados profundos de várias batalhas
    de uma vez — é a paginação de eventos (até 40 páginas/batalha) que faz o
    backfill sequencial ser absurdamente lento, então aqui é onde o tempo de
    espera de rede das várias batalhas se sobrepõe em vez de somar.
    `priority_fn(battle)` decide o tier do bg pool praquele deep-fetch — o
    `slot()` real vive dentro de fetch_battle_detail/fetch_events e lê o
    contextvar que o scope aqui seta."""
    async def fetch_one(battle: Battle):
        async with albion_scope(priority_fn(battle)):
            try:
                raw, events = await _fetch_deep_data(client, host, battle)
                return battle, raw, events
            except Exception as e:
                return battle, e

    return await asyncio.gather(*[fetch_one(b) for b in battles])


RETRY_STUCK_BATCH = 20  # batalhas travadas em "light" retentadas por região a cada ciclo


async def _retry_stuck_battles(client: httpx.AsyncClient, db: Session, region: str, host: str) -> int:
    """Batalhas que qualificavam pra deep (players_total >= DEEP_PROCESS_MIN_PLAYERS)
    mas tiveram o fetch falhando uma vez (rede instável, rate limit etc.) nunca são
    revisitadas pelo fluxo normal: o cursor do backfill só avança pra frente e o
    sync_recent só olha o topo do feed e desiste depois de _is_frozen. Isso varre
    o que ficou "light" indevidamente e tenta de novo.

    Roda no seu PRÓPRIO loop (ver run_retry_stuck_forever), não mais
    amarrado ao fim de um lap do backfill (_finish_lap) — a varredura
    reversa de startup (_reverse_startup_sweep) sozinha pode levar minutos
    pra cobrir as 3 regiões antes do backfill normal sequer começar, e
    enquanto isso essa rede de segurança nunca rodava: batalha nova que
    falhasse no primeiro attempt ficava travada em "light" indefinidamente,
    sem ninguém pra tentar de novo — era exatamente o sintoma de "batalha
    fantasma" logo após o servidor subir."""
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
        return 0

    now = datetime.now(timezone.utc)
    # retry_stuck: batalha nova (não-frozen) que falhou → NEW_*; antiga → OLD_*.
    def _prio(b: Battle) -> int: return battle_priority(b, is_new=not _is_frozen(b, now))

    for result in await _backfill_deep_fetch_all(client, host, stuck, priority_fn=_prio):
        battle = result[0]
        if isinstance(result[1], Exception):
            log.warning("battle_tracker: retry de %s (%s) falhou de novo: %r",
                        battle.albion_id, region, result[1])
            battle.reprocess_reason = battle.reprocess_reason or REPROCESS_REASON_FAILED
            db.commit()
            continue
        _, raw, events = result
        try:
            ok = _write_deep_data(db, battle, raw, events)
        except Exception as e:
            db.rollback()
            log.warning("battle_tracker: falha ao salvar retry de %s (%s): %r",
                        battle.albion_id, region, e)
            ok = False
        if not ok:
            battle.reprocess_reason = battle.reprocess_reason or REPROCESS_REASON_EMPTY
            db.commit()
    return len(stuck)


RETRY_STUCK_INTERVAL = 15  # segundos entre ciclos do loop independente de retry — idem BACKFILL_CYCLE_INTERVAL, fila de fundo não precisa ser rápida


async def run_retry_stuck_forever() -> None:
    """Loop próprio, sempre ativo, independente do backfill/varredura reversa
    — ver _retry_stuck_battles pro porquê de não estar mais amarrado a
    _finish_lap."""
    log.info("battle_tracker: retry de batalhas travadas iniciando")
    while True:
        db = SessionLocal()
        try:
            async with make_client() as client:
                for region, host in HOSTS.items():
                    try:
                        n = await _retry_stuck_battles(client, db, region, host)
                        if n:
                            log.info("battle_tracker: %d batalhas travadas retentadas (%s)", n, region)
                    except Exception as e:
                        log.warning("battle_tracker: erro no retry de travadas (%s): %s", region, e)
        except Exception as e:
            log.error("battle_tracker: erro no loop de retry: %s", e)
        finally:
            db.close()
        await asyncio.sleep(RETRY_STUCK_INTERVAL)


async def _finish_lap(db: Session, cursor: BattleSyncCursor) -> None:
    """Fim de uma volta completa na janela paginável da API (ver
    BATTLES_API_OFFSET_LIMIT) — reseta o cursor pro início em vez de travar
    em done=True pra sempre. A API do Albion só expõe as ~10000 batalhas MAIS
    RECENTES nesse endpoint (não é um arquivo histórico fixo, é uma janela
    que desliza pra frente com o tempo) — parar de vez depois da primeira
    volta deixava passar batalha que o sync "recente" (janela bem mais
    estreita, ver sync_recent) não pegou a tempo, e ela nunca mais era vista
    de novo. Loop pra sempre: cada volta nova é uma segunda rede de segurança
    varrendo a mesma janela recente do zero. `done` fica só como telemetria
    ("completou pelo menos 1 volta"), não trava mais nada.

    Retry de batalhas travadas em "light" NÃO roda mais daqui — ver
    run_retry_stuck_forever, um loop independente à parte (essa função podia
    ficar minutos sem ser chamada durante a varredura reversa de startup,
    deixando batalha nova travada sem rede de segurança nenhuma)."""
    cursor.done = True
    cursor.next_offset = 0
    db.commit()


async def _deep_process_batch(
    client: httpx.AsyncClient, db: Session, region: str, host: str, qualifying: list[Battle],
    *, priority_fn,
) -> None:
    """Deep-processa em paralelo uma lista de batalhas JÁ qualificadas (ver
    _backfill_deep_fetch_all) e grava. Usado por todo mundo que deep-processa
    (sync_recent, _process_battle_batch) — fonte única do "o que fazer quando
    falha".

    Falha em qualquer batalha (rede, 429, o que for) marca reprocess_reason
    nela em vez de só logar e desistir — cai na MESMA fila genérica que
    app.services.battle_reprocessor já varre sem parar (bem mais devagar,
    sem disputar a API com o resto), garantindo que a batalha não se perde
    de vez por causa de um erro passageiro.

    `priority_fn`: sync_recent passa _prio_new (batalha nova, prioridade
    alta no bg pool); backfill passa _prio_old; reverse sweep passa _prio_other."""
    if not qualifying:
        return
    for result in await _backfill_deep_fetch_all(client, host, qualifying, priority_fn=priority_fn):
        battle = result[0]
        if isinstance(result[1], Exception):
            log.warning("battle_tracker: falha no deep-process de %s (%s): %r",
                        battle.albion_id, region, result[1])
            battle.reprocess_reason = battle.reprocess_reason or REPROCESS_REASON_FAILED
            db.commit()
            continue
        _, raw, events = result
        try:
            ok = _write_deep_data(db, battle, raw, events)
        except Exception as e:
            db.rollback()
            log.warning("battle_tracker: falha ao salvar %s (%s): %r", battle.albion_id, region, e)
            ok = False
        if not ok:
            battle.reprocess_reason = battle.reprocess_reason or REPROCESS_REASON_EMPTY
            db.commit()


async def _process_battle_batch(client: httpx.AsyncClient, db: Session, region: str, host: str, batch: list[dict], *, priority_fn) -> None:
    """Fluxo comum de uma página de batalhas cruas da API: upsert leve,
    filtra quem qualifica pra deep, e deep-processa (ver _deep_process_batch).
    Usado pelo avanço normal do backfill (backfill_step) e pela varredura
    reversa de startup (_reverse_startup_sweep). `priority_fn` repassa pro
    tier do bg pool de cada deep-fetch (backfill=_prio_old, sweep=_prio_other)."""
    qualifying: list[Battle] = []
    for raw in batch:
        try:
            battle = upsert_battle_light(db, raw, region)
        except Exception as e:
            db.rollback()
            log.debug("battle_tracker: skip backfill %s (%s): %s", raw.get("id"), region, e)
            continue
        if battle is None or battle.processing_tier == "deep" or battle.players_total < DEEP_PROCESS_MIN_PLAYERS:
            continue
        qualifying.append(battle)
    try:
        db.commit()
    except Exception as e:
        # Sem isto, "database is locked" (contenção transitória com outro
        # serviço de fundo) deixava a Session numa transação já abortada —
        # TODO próximo commit nesta MESMA sessão falha de novo com "This
        # Session's transaction has been rolled back...", em cascata pelo
        # resto da página, pelo resto da varredura reversa de startup
        # (~196 páginas/região) e pelas regiões seguintes (mesma sessão
        # reusada em run_backfill_forever). Rollback aqui + pula o deep
        # processing desta página: as batalhas voltam a ser vistas no
        # próximo lap do backfill perpétuo normal (mesmo princípio de
        # resiliência já documentado pra falha de rede/429).
        db.rollback()
        log.warning("battle_tracker: falha ao comitar página (%s): %r — recoberta num ciclo depois", region, e)
        return

    await _deep_process_batch(client, db, region, host, qualifying, priority_fn=priority_fn)


async def backfill_step(client: httpx.AsyncClient, db: Session, region: str, host: str) -> None:
    """Avança a paginação da região dentro da janela de ~10000 batalhas mais
    recentes que a API do Albion expõe (ver BATTLES_API_OFFSET_LIMIT),
    processa em profundidade (em paralelo, ver _backfill_deep_fetch_all) as
    que qualificam, e persiste o cursor pra continuar de onde parou no
    próximo ciclo. Ao completar uma volta (bate no teto da API, a página
    veio vazia, ou a batalha mais velha já passou de BACKFILL_MAX_AGE),
    retenta batalhas travadas em "light" (ver _retry_stuck_battles) e
    recomeça do offset 0 — ver _finish_lap. Nunca para de vez."""
    cursor = await _get_cursor(db, region)
    cutoff = datetime.now(timezone.utc) - BACKFILL_MAX_AGE

    async with albion_scope(OLD_ELIGIBLE):
        for _ in range(BACKFILL_PAGES_PER_CYCLE):
            if cursor.next_offset + BACKFILL_PAGE_SIZE > BATTLES_API_OFFSET_LIMIT:
                await _finish_lap(db, cursor)
                return

            try:
                batch = await fetch_battles(client, host, limit=BACKFILL_PAGE_SIZE, offset=cursor.next_offset)
            except Exception as e:
                log.warning("battle_tracker: falha no backfill (%s, offset=%d): %r", region, cursor.next_offset, e)
                return

            if not batch:
                await _finish_lap(db, cursor)
                return

            reached_cutoff = False
            fresh: list[dict] = []
            for raw in batch:
                if _parse_dt(raw["startTime"]) < cutoff:
                    reached_cutoff = True
                    break
                fresh.append(raw)

            await _process_battle_batch(client, db, region, host, fresh, priority_fn=_prio_old)

            cursor.next_offset += len(batch)
            if reached_cutoff or len(batch) < BACKFILL_PAGE_SIZE:
                await _finish_lap(db, cursor)
                return
            db.commit()


async def _reverse_startup_sweep(client: httpx.AsyncClient, db: Session, region: str, host: str) -> None:
    """Varredura única, só no startup do servidor: cobre a janela de ~10000
    batalhas mais recentes que a API expõe (ver BATTLES_API_OFFSET_LIMIT) de
    TRÁS PRA FRENTE (offset mais alto → 0) — o oposto do sentido normal do
    backfill (ver backfill_step, que sempre avança 0 → topo). Prioriza
    justamente as batalhas MAIS PERTO de sumir da janela: se o processo
    reinicia bem quando um lap novo do backfill normal começa do offset 0,
    as batalhas do fim da janela (as mais velhas ainda alcançáveis) só
    seriam vistas por último — e o tempo até o backfill sequencial chegar
    lá pode ser o suficiente pra elas já terem sido empurradas pra fora da
    janela por batalha nova, sumindo sem nunca ter sido vistas. Depois
    dessa varredura, o loop perpétuo normal (backfill_cycle) assume.

    O teto real da API é um pouco menor que BATTLES_API_OFFSET_LIMIT (ver
    comentário na constante) — por isso tolera algumas falhas seguidas no
    topo da janela (offset ainda inválido) antes de desistir de vez.

    Pausa curta entre páginas (ver PAGE_PAUSE) — ~196 páginas por região
    sem pausa nenhuma é, sozinho, rajada suficiente pra estourar rate limit
    logo no startup, antes até do resto dos serviços de fundo entrarem em
    ritmo. Se abortar por 429 sustentado, quem sobrou nessa passada única
    ainda é coberto depois pelo backfill perpétuo normal (backfill_cycle)."""
    offset = BATTLES_API_OFFSET_LIMIT - BACKFILL_PAGE_SIZE
    consecutive_failures = 0
    async with albion_scope(OTHER):
        while offset >= 0:
            try:
                batch = await fetch_battles(client, host, limit=BACKFILL_PAGE_SIZE, offset=offset)
            except Exception as e:
                consecutive_failures += 1
                if consecutive_failures >= 5:
                    log.warning("battle_tracker: varredura reversa de startup abortada (%s, offset=%d): %r",
                                region, offset, e)
                    return
                offset -= BACKFILL_PAGE_SIZE
                await asyncio.sleep(PAGE_PAUSE)
                continue
            consecutive_failures = 0
            if batch:
                await _process_battle_batch(client, db, region, host, batch, priority_fn=_prio_other)
            offset -= BACKFILL_PAGE_SIZE
            await asyncio.sleep(PAGE_PAUSE)
    log.info("battle_tracker: varredura reversa de startup concluída (%s)", region)


RECENT_PAGES_PER_CYCLE = 8  # 8*51=408 batalhas/região por ciclo — deep_process roda em paralelo


async def sync_recent() -> int:
    """Busca e salva as batalhas mais recentes dos 3 servidores e processa em
    profundidade as que parecem ZvZ. Roda sozinho (sem o backfill histórico
    junto) pra não ter o feed "recente" atrasado por um backfill lento —
    ver run_backfill_forever, que cuida do histórico em paralelo.

    Busca várias páginas (não só a primeira) por região: só 51 batalhas
    (1 página) não cobre picos de atividade — uma batalha que sai do top 51
    entre dois ciclos de POLL_INTERVAL (1 min) nunca mais é vista, já que o
    backfill histórico serve só pra carga inicial e reseta ao chegar no teto
    da API (ver BATTLES_API_OFFSET_LIMIT / _finish_lap).

    Deep-process em paralelo (ver _backfill_deep_fetch_all, concorrência
    limitada pelo bg pool do albion_gate — 6 slots, compartilhado com
    todo outro serviço que deep-processa, pra não estourar rate limit da
    API) — com DEEP_PROCESS_MIN_PLAYERS=0 praticamente toda batalha do feed
    qualifica, então processar uma de cada vez aqui (await sequencial) fazia
    o ciclo inteiro (3 regiões × até ~400 batalhas) estourar bem além do
    POLL_INTERVAL: batalha nova saía da janela de "recentes" antes do ciclo
    lento sequer chegar nela, e a região nunca mais a via de novo — sintoma
    era batalha real "nunca encontrada" mesmo ainda visível na API do
    Albion. Falha em qualquer batalha aqui não é definitiva: cai na fila de
    reprocess_reason (ver _deep_process_batch) até conseguir."""
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    count = 0
    try:
        async with make_client() as client:
            for region, host in HOSTS.items():
                # Cada página é tentada isoladamente — antes, timeout numa página
                # no meio (ex.: página 5 de 8) descartava TODAS as páginas já
                # buscadas daquela região pro ciclo inteiro (um try/except só em
                # volta do laço todo), zerando a descoberta da região naquele
                # ciclo. Agora só para de paginar mais, mantém o que já pegou.
                battles: list[dict] = []
                async with albion_scope(NEW_ELIGIBLE):
                    for page in range(RECENT_PAGES_PER_CYCLE):
                        try:
                            batch = await fetch_battles(client, host, offset=page * BACKFILL_PAGE_SIZE)
                        except Exception as e:
                            log.warning("battle_tracker: falha ao buscar página %d do feed (%s): %r", page, region, e)
                            break
                        if not batch:
                            break
                        battles.extend(batch)
                        if len(batch) < BACKFILL_PAGE_SIZE:
                            break

                qualifying: list[Battle] = []
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
                    qualifying.append(battle)
                db.commit()

                await _deep_process_batch(client, db, region, host, qualifying, priority_fn=_prio_new)
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
    pra não martelar a API da Albion sem necessidade.

    Antes do loop perpétuo normal, roda 1x a varredura reversa de startup
    (ver _reverse_startup_sweep) — toda vez que o servidor sobe, cobre a
    janela de trás pra frente pra pegar primeiro quem está mais perto de
    sumir da janela de ~10000 que a API expõe."""
    log.info("battle_tracker: backfill iniciando (bg pool do albion_gate)")

    db = SessionLocal()
    try:
        async with make_client() as client:
            for region, host in HOSTS.items():
                try:
                    await _reverse_startup_sweep(client, db, region, host)
                except Exception as e:
                    log.error("battle_tracker: erro na varredura reversa de startup (%s): %s", region, e)
                    # Backstop: mesma sessão é reusada pra região seguinte (não
                    # recriada por região) — sem isto, uma falha aqui deixaria
                    # a próxima região herdar uma transação já abortada.
                    db.rollback()
    finally:
        db.close()

    while True:
        try:
            await backfill_cycle()
        except Exception as e:
            log.error("battle_tracker: erro no backfill: %s", e)
        await asyncio.sleep(BACKFILL_CYCLE_INTERVAL)
