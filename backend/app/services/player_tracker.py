"""Background task que acompanha o kill feed global do Albion (3 regiões) e
mantém perfis + ledger de kills atualizados."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.players import AlbionPlayer, PlayerKillEvent, PlayerSnapshot, KillSyncCursor
from app.services import search_index
from app.services.albion_gate import NEW_ELIGIBLE, OLD_ELIGIBLE, OTHER, albion_scope, observe_response, slot

log = logging.getLogger(__name__)

# Hosts oficiais por região — mesmos usados em bot/cogs/battleboard.py e em
# battle_tracker.py (que importa esse dict daqui pra não duplicar).
HOSTS = {
    "americas": "gameinfo.albiononline.com",
    "europe": "gameinfo-ams.albiononline.com",
    "asia": "gameinfo-sgp.albiononline.com",
}

TIMEOUT = httpx.Timeout(20.0, read=40.0)  # ponytail: read 40s — API do Albion (Américas) estoura 20s sob carga; connect/pool/write ficam em 20s
POLL_INTERVAL = 120  # 2 min — kill feed atualiza rápido; antes era 300s (5min) e kills demoravam 5x mais que batalhas pra serem descobertas
SNAPSHOT_MAX_AGE = timedelta(hours=24)  # resolução do gráfico de crescimento de fama
FEED_LIMIT = 51  # máximo que a API devuelve por página
FEED_MAX_PAGES = 8  # até 8 páginas (408 events) por poll — cobre rajadas grandes sem exceder rate limit


async def _observe_albion(response: httpx.Response) -> None:
    """Response hook: alimenta o rate limiter adaptativo (albion_gate) com o
    status de cada resposta do gameinfo — 2xx recupera a taxa, 429/504 recuam.
    Todo request ao gameinfo usa este cliente, então o feedback é completo sem
    call site reportar nada. Timeout (sem resposta) não passa por aqui — mas já
    se auto-limita (o slot fica preso os 40s), então não precisa do sinal."""
    observe_response(response.status_code)


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT,
        headers={"User-Agent": "ziggs-platform"},
        verify=False,
        event_hooks={"response": [_observe_albion]},
    )


def upsert_player(db: Session, data: dict, region: str, *, commit: bool = True) -> AlbionPlayer:
    """Salva/atualiza jogador e tira snapshot quando a guilda muda (ou quando
    o último snapshot já passou de SNAPSHOT_MAX_AGE — dá resolução pro
    gráfico de crescimento de fama sem precisar de um job dedicado, já que
    o polling reativo toca em qualquer jogador ativo a cada POLL_INTERVAL).

    `commit=False` deixa o commit pro caller (poll_once/sync_player_kills fazem
    commit por evento, não por jogador — antes eram ~765 commits síncronos no
    event loop por ciclo, wedging o backend inteiro; SQLite serializa writers
    e cada commit é um fsync bloqueante no loop)."""
    albion_id = data.get("Id") or data.get("id", "")
    if not albion_id:
        raise ValueError("dados sem Id de jogador")

    name = data.get("Name") or data.get("name", "")
    guild_id = data.get("GuildId") or data.get("guildId") or None
    guild_name = data.get("GuildName") or data.get("guildName") or None
    alliance_id = data.get("AllianceId") or data.get("allianceId") or None
    alliance_name = data.get("AllianceName") or data.get("allianceName") or None
    alliance_tag = data.get("AllianceTag") or data.get("allianceTag") or None
    avatar = data.get("Avatar") or data.get("avatar") or None

    lifetime = data.get("LifetimeStatistics")
    has_lifetime = isinstance(lifetime, dict) and bool(lifetime)
    kill_fame = data.get("KillFame") or 0
    death_fame = data.get("DeathFame") or 0
    # Stats detalhadas (PvE/Crafting/Gathering por recurso) só vêm no perfil
    # completo e no feed; buscas trazem só o topo. Sem LifetimeStatistics no
    # payload, NÃO sobrescreve — senão um upsert de busca zera as coletas que
    # já tínhamos (ver _synthetic_raw em routes/players.py).
    if has_lifetime:
        pve_fame = ((lifetime.get("PvE") or {}).get("Total") or 0)
        crafting_fame = ((lifetime.get("Crafting") or {}).get("Total") or 0)
        gathering = lifetime.get("Gathering") or {}
        gathering_fame = ((gathering.get("All") or {}).get("Total") or 0)
        # Coleta por recurso (Wood/Hide/Ore/Rock/Fiber).Total — mesma fonte do
        # gathering_fame total. None no payload não zera o que já tínhamos (o
        # bloco `if has_lifetime` só roda quando LifetimeStatistics veio).
        gather = {r: ((gathering.get(r.capitalize()) or {}).get("Total") or 0)
                  for r in ("wood", "hide", "ore", "rock", "fiber")}
        # FishingFame — escalar solto no LifetimeStatistics (não dentro de
        # Gathering), irmão de Gathering.All.Total.
        fishing_fame = int(lifetime.get("FishingFame") or 0)
    else:
        pve_fame = crafting_fame = gathering_fame = None  # type: ignore[assignment]
        gather = None  # type: ignore[assignment]
        fishing_fame = None  # type: ignore[assignment]

    now = datetime.now(timezone.utc)
    player = db.query(AlbionPlayer).filter_by(albion_id=albion_id).first()
    is_new = player is None
    guild_changed = not is_new and player.guild_id != guild_id

    if is_new:
        player = AlbionPlayer(
            albion_id=albion_id, name=name, region=region,
            guild_id=guild_id, guild_name=guild_name,
            alliance_id=alliance_id, alliance_name=alliance_name, alliance_tag=alliance_tag,
            avatar=avatar,
            kill_fame=kill_fame, death_fame=death_fame,
            pve_fame=pve_fame or 0, crafting_fame=crafting_fame or 0, gathering_fame=gathering_fame or 0,
            gather_wood=gather["wood"] if gather else 0,
            gather_hide=gather["hide"] if gather else 0,
            gather_ore=gather["ore"] if gather else 0,
            gather_rock=gather["rock"] if gather else 0,
            gather_fiber=gather["fiber"] if gather else 0,
            fishing_fame=fishing_fame or 0,
            lifetime_statistics=lifetime if has_lifetime else None,
            first_seen_at=now, last_seen_at=now,
        )
        db.add(player)
        db.flush()
    else:
        player.name = name
        player.guild_id = guild_id
        player.guild_name = guild_name
        player.alliance_id = alliance_id
        player.alliance_name = alliance_name
        player.alliance_tag = alliance_tag
        if avatar:
            player.avatar = avatar
        player.kill_fame = kill_fame
        player.death_fame = death_fame
        if has_lifetime:
            player.lifetime_statistics = lifetime
            player.pve_fame = pve_fame
            player.crafting_fame = crafting_fame
            player.gathering_fame = gathering_fame
            player.gather_wood = gather["wood"]
            player.gather_hide = gather["hide"]
            player.gather_ore = gather["ore"]
            player.gather_rock = gather["rock"]
            player.gather_fiber = gather["fiber"]
            player.fishing_fame = fishing_fame
        player.last_seen_at = now
        player.is_deleted = False

    last_snapshot_stale = False
    if not is_new and not guild_changed:
        last = (
            db.query(PlayerSnapshot)
            .filter_by(player_id=player.id)
            .order_by(PlayerSnapshot.snapshotted_at.desc())
            .first()
        )
        last_snapshot_stale = last is None or (now - _aware(last.snapshotted_at)) > SNAPSHOT_MAX_AGE

    if is_new or guild_changed or last_snapshot_stale:
        db.add(PlayerSnapshot(
            player_id=player.id,
            guild_id=guild_id, guild_name=guild_name,
            alliance_id=alliance_id, alliance_tag=alliance_tag,
            kill_fame=player.kill_fame, death_fame=player.death_fame, pve_fame=player.pve_fame,
            snapshotted_at=now,
        ))

    search_index.safe_upsert_entry(
        db, entity_type="player", entity_id=albion_id, display_name=name,
        region=region, guild_name=guild_name, alliance_name=alliance_name,
    )

    if commit:
        db.commit()
    return player


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _record_kill_event(db: Session, ev: dict, region: str, *, commit: bool = True) -> None:
    """Registra o kill no ledger (PlayerKillEvent), dedupe por
    region+albion_event_id — chamado depois de upsert_player do killer/vítima,
    então killer_player_id/victim_player_id já existem.

    `commit=False` deixa o commit pro caller (ver upsert_player)."""
    event_id = str(ev.get("EventId") or "")
    if not event_id:
        return
    existing = db.scalar(
        select(PlayerKillEvent).where(
            PlayerKillEvent.region == region, PlayerKillEvent.albion_event_id == event_id,
        )
    )
    if existing is not None:
        return

    killer, victim = ev.get("Killer") or {}, ev.get("Victim") or {}
    killer_id, victim_id = killer.get("Id"), victim.get("Id")
    killer_row = db.scalar(select(AlbionPlayer).where(AlbionPlayer.albion_id == killer_id)) if killer_id else None
    victim_row = db.scalar(select(AlbionPlayer).where(AlbionPlayer.albion_id == victim_id)) if victim_id else None

    participant_count = ev.get("numberOfParticipants") or 1
    row = PlayerKillEvent(region=region, albion_event_id=event_id)
    row.timestamp = datetime.fromisoformat(ev["TimeStamp"].replace("Z", "+00:00"))
    row.fame = ev.get("TotalVictimKillFame") or 0
    row.killer_player_id = killer_row.id if killer_row else None
    row.victim_player_id = victim_row.id if victim_row else None
    row.participant_count = participant_count
    row.participants = [
        {"name": p.get("Name"), "albion_id": p.get("Id")}
        for p in (ev.get("Participants") or [])
        if p and p.get("Name")
    ]
    row.group_member_count = ev.get("groupMemberCount")
    row.is_solo = participant_count == 1
    row.albion_battle_id = str(ev["BattleId"]) if ev.get("BattleId") else None
    row.kill_area = ev.get("KillArea")
    row.killer_equipment = killer.get("Equipment")
    row.victim_equipment = victim.get("Equipment")
    row.victim_inventory = victim.get("Inventory")
    row.killer_guild_id = killer.get("GuildId") or None
    row.killer_guild_name = killer.get("GuildName") or None
    row.victim_guild_id = victim.get("GuildId") or None
    row.victim_guild_name = victim.get("GuildName") or None
    db.add(row)
    if commit:
        db.commit()


PLAYER_SYNC_LIMIT = 50  # kills/mortes buscados por sincronização ativa, sem paginar mais que isso


async def _upsert_event_players(db: Session, ev: dict, region: str, skip_id: str | None = None) -> None:
    """skip_id: não re-upserta esse albion_id a partir dos dados embutidos no
    evento — um kill/death ANTIGO traz a guilda/fama do jogador NA ÉPOCA
    daquele evento, não o estado atual. Usado quando o evento veio da
    sincronização ativa de UM jogador específico (ver sync_player_kills):
    aplicar isso a ele mesmo geraria trocas de guilda falsas, fora de ordem
    cronológica, cada vez que o perfil dele é recarregado."""
    for role in ("Killer", "Victim"):
        p = ev.get(role)
        if p and p.get("Id") and p.get("Id") != skip_id:
            try:
                upsert_player(db, p, region, commit=False)
            except Exception as e:
                log.debug("player_tracker: skip %s (%s): %s", p.get("Id"), region, e)
    for participant in (ev.get("Participants") or []):
        if participant and participant.get("Id") and participant.get("Id") != skip_id:
            try:
                upsert_player(db, participant, region, commit=False)
            except Exception as e:
                log.debug("player_tracker: skip participant %s (%s): %s", participant.get("Id"), region, e)


async def sync_player_kills(client: httpx.AsyncClient, db: Session, host: str, region: str, albion_id: str) -> int:
    """Busca as kills/mortes recentes desse jogador direto na API (endpoint
    por jogador, não o feed global) e registra no ledger — o feed global só
    pega quem está nos 51 eventos mais recentes da região no momento exato
    do poll, então a luta de um jogador específico pode nunca ter sido vista
    passivamente. Chamado a cada carregamento/refresh do perfil.

    O próprio `albion_id` NÃO é re-upsertado a partir desses eventos (ver
    _upsert_event_players) — o estado atual dele já vem fresco da chamada a
    /players/{id} feita por quem chama essa função."""
    count = 0
    for kind in ("kills", "deaths"):
        events = None
        for attempt in range(2):  # ponytail: 1 retry, API do Albion dá ReadTimeout transiente com frequência
            try:
                async with slot():
                    resp = await client.get(
                        f"https://{host}/api/gameinfo/players/{albion_id}/{kind}",
                        params={"limit": PLAYER_SYNC_LIMIT, "offset": 0},
                    )
                resp.raise_for_status()
                events = resp.json()
                break
            except Exception as e:
                if attempt == 1:
                    log.debug("player_tracker: falha ao sincronizar %s de %s (%s): %s", kind, albion_id, region, e)
        if not isinstance(events, list):
            continue
        for ev in events:
            # Dedupe ANTES do trabalho pesado: num refresh de jogador ativo a
            # maioria das kills recentes JÁ está no ledger (loads anteriores +
            # feed global). Sem isso, _upsert_event_players re-upsertava
            # killer/vítima/participantes — CADA um com um db.commit() — pra todo
            # evento já conhecido: centenas de commits à toa por refresh,
            # travando o event loop (SQLAlchemy síncrono em código async). Evento
            # já registrado → pula o upsert dos players E o record.
            event_id = str(ev.get("EventId") or "")
            if event_id and db.scalar(
                select(PlayerKillEvent.id).where(
                    PlayerKillEvent.region == region,
                    PlayerKillEvent.albion_event_id == event_id,
                )
            ) is not None:
                # Commit libera read tx do SELECT de dedup antes da próxima
                # iteração (que faz HTTP no topo do loop). Sem isto, a read tx
                # fica aberta durante o await do próximo evento.
                db.commit()
                continue
            await _upsert_event_players(db, ev, region, skip_id=albion_id)
            try:
                _record_kill_event(db, ev, region, commit=False)
                db.commit()  # batch: 1 commit por evento, não por jogador/kill
                count += 1
            except Exception as e:
                db.rollback()
                log.debug("player_tracker: skip sync event %s (%s): %s", ev.get("EventId"), region, e)
        if events:
            log.info("sync_kills: %s (%s) — %d %s ingeridos", albion_id, region, len(events), kind)
    return count


async def poll_once() -> int:
    """Busca o kill feed das 3 regiões uma vez, paginando até não achar eventos
    novos (ou atingir FEED_MAX_PAGES). Upserta jogadores e registra cada kill
    no ledger. Retorna contagem de jogadores upsertados."""
    count = 0
    db = SessionLocal()
    try:
        async with make_client() as c:
            for region, host in HOSTS.items():
                seen_event_ids: set[str] = set()
                for page in range(FEED_MAX_PAGES):
                    offset = page * FEED_LIMIT
                    try:
                        async with albion_scope(NEW_ELIGIBLE):
                            async with slot():
                                resp = await c.get(
                                    f"https://{host}/api/gameinfo/events",
                                    params={"limit": FEED_LIMIT, "offset": offset},
                                )
                        resp.raise_for_status()
                        events = resp.json()
                    except Exception as e:
                        log.warning("player_tracker: falha ao buscar kill feed (%s, offset=%d): %s", region, offset, e)
                        break
                    if not isinstance(events, list) or not events:
                        break

                    new_count = 0
                    for ev in events:
                        event_id = str(ev.get("EventId") or "")
                        if not event_id or event_id in seen_event_ids:
                            continue
                        seen_event_ids.add(event_id)

                        # Dedupe contra o banco: se já temos este event_id,
                        # pula o upsert pesado (igual sync_player_kills faz).
                        if db.scalar(
                            select(PlayerKillEvent.id).where(
                                PlayerKillEvent.region == region,
                                PlayerKillEvent.albion_event_id == event_id,
                            )
                        ) is not None:
                            continue

                        try:
                            for role in ("Killer", "Victim"):
                                p = ev.get(role)
                                if p and p.get("Id"):
                                    upsert_player(db, p, region, commit=False)
                                    count += 1
                            for assist in (ev.get("Participants") or []):
                                if assist and assist.get("Id"):
                                    upsert_player(db, assist, region, commit=False)
                                    count += 1
                            _record_kill_event(db, ev, region, commit=False)
                            db.commit()
                            new_count += 1
                        except Exception as e:
                            db.rollback()
                            log.debug("player_tracker: skip event %s (%s): %s", event_id, region, e)

                    # Se esta página não teve eventos novos, não vale a pena
                    # paginar mais — as próximas páginas são tudo já conhecido.
                    if new_count == 0:
                        break

                log.debug("player_tracker: %s — %d eventos novos em %d páginas", region, len(seen_event_ids), page + 1)
    finally:
        db.close()

    return count


async def run_forever() -> None:
    """Loop de polling — iniciado no startup do FastAPI."""
    log.info("player_tracker: iniciando (intervalo=%ds, hosts=%s)", POLL_INTERVAL, list(HOSTS))
    while True:
        try:
            n = await poll_once()
            log.debug("player_tracker: %d jogadores atualizados", n)
        except Exception as e:
            log.error("player_tracker: erro inesperado: %s", e)
        await asyncio.sleep(POLL_INTERVAL)


# ─── Backfill histórico de kills (varre a janela de ~1k da API) ───────────────
# Igual ao backfill de batalhas: o poll recente (poll_once) só pega as 8
# primeiras páginas (~408 events). Se >408 kills acontecem entre ciclos, as do
# meio somem da janela recente. Este backfill varre offset 0→999 numa volta
# perpétua, processando os events que o poll não alcançou.

KILL_BACKFILL_PAGE_SIZE = 51
KILL_BACKFILL_PAGES_PER_CYCLE = 3
KILL_BACKFILL_CYCLE_INTERVAL = 20  # segundos entre ciclos (mesmo ritmo do battle backfill)
KILL_BACKFILL_OFFSET_LIMIT = 999  # teto duro da API de events (offset+limit > 1000 → 500)


async def _get_kill_cursor(db: Session, region: str) -> KillSyncCursor:
    cursor = db.get(KillSyncCursor, region)
    if cursor is None:
        cursor = KillSyncCursor(region=region, next_offset=0, done=False)
        db.add(cursor)
        db.flush()
    return cursor


async def backfill_kills_step(client: httpx.AsyncClient, db: Session, region: str, host: str) -> None:
    """Avança a paginação de events da região dentro da janela de ~1000.
    Processa events que o poll recente não pegou (dedup por event_id).
    Ao completar uma volta, reseta o cursor e recomeça."""
    cursor = await _get_kill_cursor(db, region)

    async with albion_scope(OLD_ELIGIBLE):
        for _ in range(KILL_BACKFILL_PAGES_PER_CYCLE):
            if cursor.next_offset + KILL_BACKFILL_PAGE_SIZE > KILL_BACKFILL_OFFSET_LIMIT:
                cursor.done = True
                cursor.next_offset = 0
                db.commit()
                return

            try:
                async with slot():
                    resp = await client.get(
                        f"https://{host}/api/gameinfo/events",
                        params={"limit": KILL_BACKFILL_PAGE_SIZE, "offset": cursor.next_offset},
                    )
                resp.raise_for_status()
                events = resp.json()
            except Exception as e:
                log.warning("player_tracker: falha no backfill de kills (%s, offset=%d): %s",
                            region, cursor.next_offset, e)
                return

            if not isinstance(events, list) or not events:
                cursor.done = True
                cursor.next_offset = 0
                db.commit()
                return

            new_count = 0
            for ev in events:
                event_id = str(ev.get("EventId") or "")
                if not event_id:
                    continue
                if db.scalar(
                    select(PlayerKillEvent.id).where(
                        PlayerKillEvent.region == region,
                        PlayerKillEvent.albion_event_id == event_id,
                    )
                ) is not None:
                    continue
                try:
                    for role in ("Killer", "Victim"):
                        p = ev.get(role)
                        if p and p.get("Id"):
                            upsert_player(db, p, region, commit=False)
                    for assist in (ev.get("Participants") or []):
                        if assist and assist.get("Id"):
                            upsert_player(db, assist, region, commit=False)
                    _record_kill_event(db, ev, region, commit=False)
                    db.commit()
                    new_count += 1
                except Exception as e:
                    db.rollback()
                    log.debug("player_tracker: skip backfill event %s (%s): %s", event_id, region, e)

            cursor.next_offset += len(events)
            if len(events) < KILL_BACKFILL_PAGE_SIZE:
                cursor.done = True
                cursor.next_offset = 0
                db.commit()
                return
            db.commit()

    if new_count:
        log.info("player_tracker: backfill kills %s offset=%d — %d events novos", region, cursor.next_offset, new_count)


async def backfill_kills_cycle() -> None:
    """Um ciclo de backfill de kills das 3 regiões."""
    db = SessionLocal()
    try:
        async with make_client() as client:
            for region, host in HOSTS.items():
                try:
                    await backfill_kills_step(client, db, region, host)
                except Exception as e:
                    log.warning("player_tracker: falha no backfill de kills (%s): %s", region, e)
    finally:
        db.close()


async def run_backfill_forever() -> None:
    """Backfill de kills roda no seu próprio loop, separado do poll recente."""
    log.info("player_tracker: backfill de kills iniciado (interval=%ds)", KILL_BACKFILL_CYCLE_INTERVAL)
    while True:
        try:
            await backfill_kills_cycle()
        except Exception as e:
            log.error("player_tracker: erro no backfill de kills: %s", e)
        await asyncio.sleep(KILL_BACKFILL_CYCLE_INTERVAL)
