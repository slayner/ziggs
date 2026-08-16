"""Pré-aquece o perfil de quem já apareceu numa batalha registrada — evita o
fetch lento (busca por nome + perfil + kills, 3 chamadas à API do Albion) na
primeira visita à página de perfil de um jogador que nunca vimos antes.

Funciona como fila, não como polling: cada Battle tem profiles_synced=False
até ser processada UMA VEZ (nunca reprocessa a mesma batalha de novo). Isso
cobre tanto o histórico inteiro já registrado (rodado uma vez no startup,
ver migração scripts/add_battle_profiles_synced.py) quanto batalhas novas
(toda Battle nasce com profiles_synced=False)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db import AsyncSessionLocal, SyncSessionLocal
from app.models.battles import Battle, BattleParticipant
from app.models.guild_profiles import AllianceProfile, GuildProfile
from app.models.players import AlbionPlayer
from app.services.albion_gate import PROFILE, WARM, albion_scope, slot
from app.services.player_tracker import HOSTS, make_client, sync_player_kills, upsert_player

log = logging.getLogger(__name__)

# Stage do refresh em andamento, por albion_id. Mesmo padrão do _load_progress
# de /players/by-name (rotas/players.py): dict em memória, lido por endpoint
# de progresso. Em falha, o caller reseta pra 'queued' (não limpa) — o pedido
# volta pro próximo ciclo e o usuário vê voltar pra fila em vez de travar.
# Stages: 'queued' (na fila) → 'fetching' (buscando perfil) → 'kills'
# (sincronizando kills/deaths) → 'building' (gravando snapshot).
_refresh_progress: dict[str, str] = {}

# albion_id -> quando o warmer TERMINOU um refresh de verdade. É o sinal de
# cooldown do botão ⟳ (routes/players.py) — NÃO dá pra usar last_seen_at, que o
# player_tracker bumpa a cada aparição no kill feed global, então jogador ativo
# (o que a gente mais quer atualizar) ficava com last_seen_at sempre recente e o
# refresh era recusado pra sempre. Em memória (mesmo padrão de _refresh_progress
# /_load_progress, single-process): reset no boot só libera 1 refresh, inofensivo.
# Podado por TTL em sync_refresh_requests pra não crescer sem limite.
_refresh_done_at: dict[str, "datetime"] = {}
_REFRESH_DONE_TTL = timedelta(minutes=15)  # > cooldown do botão; entrada mais velha é inútil

# ponytail: era 20 — com o rate limiter global da Albion agora bem mais
# apertado (ver albion_gate.py), 20 batalhas (cada uma com vários
# participantes, até 3 chamadas/jogador) podia virar um ciclo de MINUTOS
# segurando uma única sessão de DB aberta com commits síncronos intercalados
# — cada commit bloqueia o event loop inteiro (SQLAlchemy síncrono chamado
# direto de código async), e com a fila histórica na casa de dezenas de
# milhares (não-urgente, ver REPROCESS_REASON_* pra distinguir de urgente),
# isso deixava o backend inteiro (até rotas simples tipo /health) engasgando
# por vários segundos, intermitente, logo após um restart. Lotes bem
# menores = ciclos bem mais curtos = sessão de DB fecha rápido = mais
# brechas pro event loop respirar entre um ciclo e outro.
BATCH_SIZE = 3       # batalhas por ciclo
BUSY_INTERVAL = 3    # segundos entre ciclos enquanto há fila pra processar
IDLE_INTERVAL = 60    # segundos entre ciclos quando a fila esvaziou
STALE_AFTER = timedelta(days=7)  # só re-busca perfil com mais tempo que isso sem atualizar


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _warm_player(client, db: AsyncSession, host: str, region: str, albion_id: str, *, force: bool = False) -> bool:
    """Aquece o perfil. Retorna True se buscou e gravou com sucesso, False se
    pulou (fresh), falhou ou host inválido. O caller usa isso pra decidir se
    limpa `refresh_requested_at` — só limpa em sucesso, senão o pedido volta
    pro próximo ciclo (retry automático até a Albion responder).

    Atualiza `_refresh_progress[albion_id]` com a etapa corrente pra o botão
    ⟳ do perfil mostrar progresso detalhado (ver /players/refresh-progress).
    Em falha NÃO limpa o stage aqui — o caller (sync_refresh_requests) reseta
    pra 'queued' quando mantém o pedido na fila, pra o usuário ver voltar pra
    fila em vez de ficar travado num stage morto."""
    player = await db.scalar(
        select(AlbionPlayer).where(AlbionPlayer.albion_id == albion_id, AlbionPlayer.region == region)
    )
    if not force and player is not None and (datetime.now(timezone.utc) - _aware(player.last_seen_at)) < STALE_AFTER:
        return True  # fresco conta como sucesso — não há trabalho a fazer
    # Fecha a read tx ANTES do HTTP — read tx aberta durante await impede
    # wal_checkpoint, cresce o WAL, e commit futuro fsync-o inteiro (173s+).
    await db.commit()
    try:
        _refresh_progress[albion_id] = "fetching"
        async with slot(host):
            resp = await client.get(f"https://{host}/api/gameinfo/players/{albion_id}")
        if resp.status_code != 200:
            log.info("warm: player %s (%s) — HTTP %d", albion_id, region, resp.status_code)
            return False
        raw = resp.json()
        if not (isinstance(raw, dict) and raw.get("Id")):
            log.info("warm: player %s (%s) — payload vazio", albion_id, region)
            return False
        # Grava o NÚCLEO do perfil (fama/guilda/lifetime stats) JÁ, antes da sync
        # de kills (~2 requests = ~10s no rate limit). Como o front reaplica o
        # perfil a cada poll DURANTE o refresh, os dados principais aparecem
        # frescos após 1 request em vez dos 3 — a sync de kills só completa o
        # feed de atividade, que já vem quase todo do feed global. `skip_id` no
        # sync_player_kills garante que ele não sobrescreve este upsert com o
        # snapshot (mais velho) embutido nos eventos.
        await upsert_player(db, raw, region)
        _refresh_progress[albion_id] = "kills"
        await sync_player_kills(client, db, host, region, albion_id)
        log.info("warm: player %s (%s) — %s aquecido", raw.get("Name") or albion_id, region, "refresh" if force else "backfill")
        return True
    except Exception as e:
        log.info("warm: player %s (%s) — falha: %s", albion_id, region, e)
        return False


# ── warm de guilda/aliança ─────────────────────────────────────────────────
# Mesma doutrina do warm de player: cliente diz ONDE olhar (region + id), o
# backend busca a verdade na API pública da Albion e grava no DB. Não confia
# em stats vindos do cliente. O warmer roda em PROFILE (reserved pool), não
# compete com backfill/sweeper.

# IDs de guilda/aliança são por região — não existem nas outras 2. Sem
# `region` o warmer não sabe qual host consultar. Region é descoberta na
# primeira visita ao perfil (rotas /public/guilds/{id} e /alliances/{id}
# tentam os 3 hosts até achar) e gravada no GuildProfile/AllianceProfile.
# Campos trimados do /guilds/{id}/members — o payload cru da Albion vem com
# Equipment/Inventory/LifetimeStatistics por membro (árvore funda, pesada) que
# ninguém lê aqui; guardar cru infla o JSON à toa pra guildas de centenas de
# membros.
def _trim_member(m: dict) -> dict:
    return {
        "Id": m.get("Id"),
        "Name": m.get("Name"),
        "KillFame": m.get("KillFame"),
        "DeathFame": m.get("DeathFame"),
        "AverageItemPower": m.get("AverageItemPower"),
    }


async def _upsert_guild_profile(db: AsyncSession, raw: dict, region: str, members: list | None) -> None:
    """Grava/atualiza o perfil de guilda a partir do payload cru da Albion.

    `members` vem de uma chamada SEPARADA (`_warm_guild`): /gameinfo/guilds/{id}
    não traz lista de membros embutida — só /gameinfo/guilds/{id}/members traz.
    Confirmado contra a API real em 21/07/2026; antes lia `raw.get("members")`,
    que não existe nesse payload e sempre voltava None (`GuildProfile.members`
    nunca era preenchido)."""
    gp = await db.scalar(select(GuildProfile).where(GuildProfile.albion_id == raw["Id"]))
    if gp is None:
        gp = GuildProfile(albion_id=raw["Id"], name=raw.get("Name", raw["Id"]), region=region)
        db.add(gp)
    else:
        gp.name = raw.get("Name", gp.name)
        gp.region = region
    gp.alliance_id = str(raw.get("AllianceId") or "") or None
    gp.alliance_name = raw.get("AllianceName") or None
    # "killFame" em minúsculo é o que a API realmente devolve (confirmado ao
    # vivo) — "KillFame" nunca batia e gp.kill_fame ficava sempre 0.
    # DeathFame, ao contrário, vem maiúsculo — a Albion é inconsistente entre
    # os dois campos no MESMO payload, não é typo nosso.
    gp.kill_fame = int(raw.get("killFame") or 0)
    gp.death_fame = int(raw.get("DeathFame") or 0)
    gp.members = [_trim_member(m) for m in members] if members else None
    gp.founder_id = str(raw.get("FounderId") or "") or None
    gp.last_seen_at = datetime.now(timezone.utc)
    await db.commit()


async def _upsert_alliance_profile(db: AsyncSession, raw: dict, region: str) -> None:
    """Grava/atualiza o perfil de aliança a partir do payload cru da Albion.

    A chave primária do payload de aliança é "AllianceId", não "Id" (só o
    payload de GUILDA usa "Id") — confirmado ao vivo em 21/07/2026. Com "Id"
    o lookup abaixo levantava KeyError toda vez (o caller já filtra por
    raw.get("AllianceId") antes de chamar, mas o bug ficava aqui se alguém
    reusasse a função)."""
    ap = await db.scalar(select(AllianceProfile).where(AllianceProfile.albion_id == raw["AllianceId"]))
    if ap is None:
        ap = AllianceProfile(albion_id=raw["AllianceId"], name=raw.get("AllianceName", raw["AllianceId"]), region=region)
        db.add(ap)
    else:
        ap.name = raw.get("AllianceName", ap.name)
        ap.region = region
    # "Guilds" (maiúsculo) é o campo real — "guilds" nunca batia. Mesmo
    # corrigido, a Albion devolve [] com frequência mesmo pra aliança com
    # guildas conhecidas (confirmado ao vivo) — o campo é best-effort, não
    # confiável; a lista de guildas mostrada no site continua vindo do
    # tracking próprio (_guilds_in_alliance), não daqui.
    ap.guilds = raw.get("Guilds") or None
    ap.founder_id = str(raw.get("FounderId") or "") or None
    ap.last_seen_at = datetime.now(timezone.utc)
    await db.commit()


async def _warm_guild(client, db: AsyncSession, host: str, region: str, albion_id: str) -> bool:
    """Busca o perfil de guilda na Albion e grava no GuildProfile. Retorna
    True em sucesso, False em falha — mesmo contrato do _warm_player."""
    try:
        _refresh_progress[f"g:{albion_id}"] = "fetching"
        async with slot(host):
            resp = await client.get(f"https://{host}/api/gameinfo/guilds/{albion_id}")
        if resp.status_code != 200:
            log.info("warm: guild %s (%s) — HTTP %d", albion_id, region, resp.status_code)
            return False
        raw = resp.json()
        if not (isinstance(raw, dict) and raw.get("Id")):
            log.info("warm: guild %s (%s) — payload vazio", albion_id, region)
            return False
        # Lista de membros é um endpoint SEPARADO — /guilds/{id} não a traz
        # embutida. Falha aqui não derruba o warm inteiro (guilda de 300
        # membros já respondeu 504 numa medição ao vivo): o resto do perfil
        # (fame, aliança) continua valendo, membros só fica None até o
        # próximo ciclo tentar de novo.
        members = None
        try:
            async with slot(host):
                mresp = await client.get(f"https://{host}/api/gameinfo/guilds/{albion_id}/members")
            if mresp.status_code == 200:
                mraw = mresp.json()
                if isinstance(mraw, list):
                    members = mraw
        except Exception as e:
            log.debug("profile_warmer: members de guild %s (%s) falhou: %s", albion_id, region, e)
        _refresh_progress[f"g:{albion_id}"] = "building"
        await _upsert_guild_profile(db, raw, region, members)
        log.info("warm: guild %s (%s) — %s aquecida (%d membros)",
                 raw.get("Name") or albion_id, region, "refresh",
                 len(members) if members else 0)
        return True
    except Exception as e:
        log.info("warm: guild %s (%s) — falha: %s", albion_id, region, e)
        return False


async def _warm_alliance(client, db: AsyncSession, host: str, region: str, albion_id: str) -> bool:
    """Busca o perfil de aliança na Albion e grava no AllianceProfile. Retorna
    True em sucesso, False em falha — mesmo contrato do _warm_player."""
    try:
        _refresh_progress[f"a:{albion_id}"] = "fetching"
        async with slot(host):
            resp = await client.get(f"https://{host}/api/gameinfo/alliances/{albion_id}")
        if resp.status_code != 200:
            log.info("warm: alliance %s (%s) — HTTP %d", albion_id, region, resp.status_code)
            return False
        raw = resp.json()
        # O payload de aliança usa "AllianceId" como chave primária, não "Id"
        # (só o de guilda usa "Id") — confirmado ao vivo em 21/07/2026. Com
        # "Id" isto sempre voltava False e _upsert_alliance_profile nunca era
        # chamado: AllianceProfile nunca era criado/atualizado pelo warmer.
        if not (isinstance(raw, dict) and raw.get("AllianceId")):
            log.info("warm: alliance %s (%s) — payload vazio", albion_id, region)
            return False
        _refresh_progress[f"a:{albion_id}"] = "building"
        await _upsert_alliance_profile(db, raw, region)
        log.info("warm: alliance %s (%s) — %s aquecida",
                 raw.get("AllianceName") or albion_id, region, "refresh")
        return True
    except Exception as e:
        log.info("warm: alliance %s (%s) — falha: %s", albion_id, region, e)
        return False


REFRESH_BATCH_SIZE = 10  # pedidos explícitos (POST /players/{id}/refresh) por ciclo

# Tempo máximo que um refresh/cold-load pode levar DEPOIS de começar a ser
# processado (não conta tempo em fila). Se exceder, o pedido é esquecido:
# refresh_requested_at limpo, stage marcado como error:timeout. Sem isso, um
# pedido que travou (rate limiter da Albion pegando timeout infinito, slot
# albion_gate que nunca completa) ficava pra sempre na fila e o usuário via
# "atualizando…" eternamente. 15min é generoso: a busca mais pesada
# (weapon_scorer week, ~470k participantes) leva ~20s; 15min cobre retries.
PROCESSING_TIMEOUT = timedelta(minutes=15)


async def _warm_with_timeout(coro, albion_id: str, stage_key: str) -> bool:
    """Envolve um _warm_* com timeout de PROCESSING_TIMEOUT. Em timeout, marca
    o stage como error:timeout e retorna False (caller vai limpar o pedido).
    O timeout é por-item, começa quando o item entra em processamento — tempo
    em fila (esperando slot do albion_gate ou ciclo do warmer) não conta."""
    try:
        return await asyncio.wait_for(coro, timeout=PROCESSING_TIMEOUT.total_seconds())
    except asyncio.TimeoutError:
        log.warning("profile_warmer: timeout (%s) em %s — pedido esquecido",
                    PROCESSING_TIMEOUT, albion_id)
        _refresh_progress[stage_key] = "error:timeout"
        return False
    except asyncio.CancelledError:
        # Task cancelada (warmer caindo, shutdown) — não limpa, próxima volta
        # pega se ainda tivermos rodando.
        raise


async def sync_refresh_requests() -> int:
    """Fura a fila normal pra processar refresh pedido explicitamente pelo
    usuário (botão ⟳ do perfil) — ignora STALE_AFTER, é pedido direto.

    Roda em albion_scope(PROFILE): pedido explícito é user-facing (alguém
    esperando), vai pro reserved pool em vez de competir no bg com todo o
    backfill/sweeper de batalhas (era OTHER → até 3min esperando slot)."""
    processed = 0
    # Poda o cooldown antes de tudo (roda todo ciclo) — entrada mais velha que o
    # TTL já passou do cooldown, é inútil e só ocuparia memória.
    _cutoff = datetime.now(timezone.utc) - _REFRESH_DONE_TTL
    for _k in [k for k, t in _refresh_done_at.items() if t < _cutoff]:
        _refresh_done_at.pop(_k, None)
    async with AsyncSessionLocal() as db:
        players = (await db.scalars(
            select(AlbionPlayer)
            .where(AlbionPlayer.refresh_requested_at.isnot(None))
            .order_by(AlbionPlayer.refresh_requested_at)
            .limit(REFRESH_BATCH_SIZE)
        )).all()
        if not players:
            return 0
        # Materializa (albion_id, region) e commit antes do HTTP — read tx
        # aberta durante await impede wal_checkpoint.
        refresh_list = [(p.albion_id, p.region) for p in players]
        await db.commit()
        async with make_client() as client:
            async with albion_scope(PROFILE):
                for albion_id, region in refresh_list:
                    _refresh_progress[albion_id] = "queued"
                    host = HOSTS.get(region)
                    ok = False
                    if host is not None:
                        ok = await _warm_with_timeout(
                            _warm_player(client, db, host, region, albion_id, force=True),
                            albion_id, albion_id,
                        )
                    # Só limpa em sucesso (ou host inválido — sem ponto em
                    # retentar host que não existe). Fetch falhou → pedido
                    # continua na fila e o próximo ciclo tenta de novo (retry
                    # automático, sem o usuário precisar clicar de novo).
                    # Timeout → esquece (não adianta refazer se a Albion travou).
                    if ok or host is None or _refresh_progress.get(albion_id) == "error:timeout":
                        p = await db.scalar(select(AlbionPlayer).where(AlbionPlayer.albion_id == albion_id, AlbionPlayer.region == region))
                        if p is not None:
                            p.refresh_requested_at = None
                            await db.commit()
                        if ok:
                            # Só refresh de VERDADE arma o cooldown (timeout/host
                            # inválido não atualizou nada — não deve bloquear retry).
                            _refresh_done_at[albion_id] = datetime.now(timezone.utc)
                        if _refresh_progress.get(albion_id) != "error:timeout":
                            _refresh_progress.pop(albion_id, None)
                    else:
                        # Falha: stage volta pra 'queued' — o pedido continua
                        # na fila e o próximo ciclo refaz. O usuário vê voltar
                        # pra fila em vez de ficar travado num stage intermediário.
                        _refresh_progress[albion_id] = "queued"
                    processed += 1
    return processed


async def sync_guild_refresh_requests() -> int:
    """Processa refreshes pendentes de guildas (POST /public/guilds/{id}/refresh).
    Mesma mecânica de sync_refresh_requests: busca na Albion, grava no
    GuildProfile, só limpa refresh_requested_at em sucesso (retry automático
    em falha). Stages em _refresh_progress com prefixo 'g:' pra não colidir
    com players."""
    processed = 0
    async with AsyncSessionLocal() as db:
        guilds = (await db.scalars(
            select(GuildProfile)
            .where(GuildProfile.refresh_requested_at.isnot(None))
            .order_by(GuildProfile.refresh_requested_at)
            .limit(REFRESH_BATCH_SIZE)
        )).all()
        if not guilds:
            return 0
        refresh_list = [(g.albion_id, g.region) for g in guilds]
        await db.commit()
        async with make_client() as client:
            async with albion_scope(PROFILE):
                for albion_id, region in refresh_list:
                    _refresh_progress[f"g:{albion_id}"] = "queued"
                    host = HOSTS.get(region)
                    ok = False
                    if host is not None:
                        ok = await _warm_with_timeout(
                            _warm_guild(client, db, host, region, albion_id),
                            albion_id, f"g:{albion_id}",
                        )
                    if ok or host is None or _refresh_progress.get(f"g:{albion_id}") == "error:timeout":
                        g = await db.scalar(select(GuildProfile).where(GuildProfile.albion_id == albion_id))
                        if g is not None:
                            g.refresh_requested_at = None
                            await db.commit()
                        if _refresh_progress.get(f"g:{albion_id}") != "error:timeout":
                            _refresh_progress.pop(f"g:{albion_id}", None)
                    else:
                        _refresh_progress[f"g:{albion_id}"] = "queued"
                    processed += 1
    return processed


async def sync_alliance_refresh_requests() -> int:
    """Processa refreshes pendentes de alianças (POST /public/alliances/{id}/refresh).
    Mesma mecânica de sync_guild_refresh_requests, com prefixo 'a:' nos stages."""
    processed = 0
    async with AsyncSessionLocal() as db:
        alliances = (await db.scalars(
            select(AllianceProfile)
            .where(AllianceProfile.refresh_requested_at.isnot(None))
            .order_by(AllianceProfile.refresh_requested_at)
            .limit(REFRESH_BATCH_SIZE)
        )).all()
        if not alliances:
            return 0
        refresh_list = [(a.albion_id, a.region) for a in alliances]
        await db.commit()
        async with make_client() as client:
            async with albion_scope(PROFILE):
                for albion_id, region in refresh_list:
                    _refresh_progress[f"a:{albion_id}"] = "queued"
                    host = HOSTS.get(region)
                    ok = False
                    if host is not None:
                        ok = await _warm_with_timeout(
                            _warm_alliance(client, db, host, region, albion_id),
                            albion_id, f"a:{albion_id}",
                        )
                    if ok or host is None or _refresh_progress.get(f"a:{albion_id}") == "error:timeout":
                        a = await db.scalar(select(AllianceProfile).where(AllianceProfile.albion_id == albion_id))
                        if a is not None:
                            a.refresh_requested_at = None
                            await db.commit()
                        if _refresh_progress.get(f"a:{albion_id}") != "error:timeout":
                            _refresh_progress.pop(f"a:{albion_id}", None)
                    else:
                        _refresh_progress[f"a:{albion_id}"] = "queued"
                    processed += 1
    return processed


# POST /players/{id}/refresh dá set() aqui pra acordar o warmer na hora em
# vez de esperar o sleep idle (até IDLE_INTERVAL=60s). O DB é a fonte de
# verdade (refresh_requested_at); o event é só otimização de wake-up.
refresh_event = asyncio.Event()


def request_refresh() -> None:
    """Sinaliza que um refresh explícito foi enfileirado — acorda o warmer."""
    refresh_event.set()


async def warm_by_name(name: str, region: str) -> dict:
    """Aquece o perfil de `name`/`region` a pedido de um companion (o próprio
    personagem do usuário).

    O companion só NOMEIA quem aquecer — o dado vem SEMPRE da API pública da
    Albion, nunca de stats mandados pelo cliente (mesma doutrina do battle
    scan: cliente diz onde olhar, o backend busca a verdade). Conhecido e velho
    → enfileira refresh (o loop drena); desconhecido → resolve nome→id e faz o
    bootstrap (é o caso que importa: gatherer/solo que nunca cai em ZvZ
    rastreada e por isso nunca era aquecido).

    Retorna dict com `status` curto para o log do companion.

    Bootstrap custa uma busca + um perfil na Albion — por isso a rota que chama
    isto tem teto apertado por install."""
    host = HOSTS.get(region)
    if host is None:
        return {"status": "bad_region"}
    async with AsyncSessionLocal() as db:
        try:
            player = await db.scalar(
                select(AlbionPlayer).where(
                    AlbionPlayer.region == region, func.lower(AlbionPlayer.name) == name.lower()
                )
            )
            if player is not None:
                # Conhecido: só refetcha se de fato velho (não spamma a Albion com
                # char que já está fresco). O loop pega pelo refresh_requested_at.
                if player.refresh_requested_at is None and (
                    datetime.now(timezone.utc) - _aware(player.last_seen_at) > STALE_AFTER
                ):
                    player.refresh_requested_at = datetime.now(timezone.utc)
                    await db.commit()
                    request_refresh()
                    return {"status": "queued"}
                await db.commit()
                return {"status": "fresh"}

            # Desconhecido: bootstrap. Nome da Albion é case-sensitive ("Moskka" ≠
            # "MosKKa" = contas distintas), então match exato primeiro.
            # WARM (não PROFILE): é warm de FUNDO do companion, não pode furar a
            # descoberta de batalha nova (ver cadeia de prioridade no albion_gate).
            await db.commit()  # libera read tx antes do HTTP
            async with make_client() as client:
                async with albion_scope(WARM):
                    async with slot(host):
                        resp = await client.get(f"https://{host}/api/gameinfo/search", params={"q": name})
                    if resp.status_code != 200:
                        log.info("warm: %s (%s) — busca falhou HTTP %d", name, region, resp.status_code)
                        return {"status": "search_failed"}
                    cands = resp.json().get("players", [])
                    match = next((p for p in cands if p.get("Name") == name), None) or next(
                        (p for p in cands if p.get("Name", "").lower() == name.lower()), None
                    )
                    if not match:
                        log.info("warm: %s (%s) — não encontrado na busca", name, region)
                        return {"status": "not_found"}
                    async with slot(host):
                        pr = await client.get(f"https://{host}/api/gameinfo/players/{match['Id']}")
                    if pr.status_code != 200:
                        log.info("warm: %s (%s) — perfil falhou HTTP %d", name, region, pr.status_code)
                        return {"status": "fetch_failed"}
                    raw = pr.json()
                    if not (isinstance(raw, dict) and raw.get("Id")):
                        log.info("warm: %s (%s) — perfil vazio", name, region)
                        return {"status": "fetch_failed"}
                    await sync_player_kills(client, db, host, region, raw["Id"])
                    await upsert_player(db, raw, region)
            log.info("warm: %s (%s) — bootstrap de %s", name, region, raw["Id"])
            return {"status": "bootstrapped"}
        except Exception as e:
            log.debug("warm_by_name %s/%s: %s", region, name, e)
            return {"status": "error"}


def queue_refresh_seen(names: list[str], region: str) -> dict:
    """Fase 2 do warm: enfileira refresh dos players (vistos por um companion em
    jogo) que JÁ conhecemos e estão velhos (> STALE_AFTER). REFRESH-ONLY — não
    faz bootstrap de nome desconhecido (isso é só pro próprio char, ver
    `warm_by_name`), então nome que não casa com `AlbionPlayer` sai de graça e
    não dá pra abusar disto pra disparar busca na Albion. Drenado pelo loop
    normal (`sync_refresh_requests`), então a vazão à Albion segue capada.

    Síncrona de propósito: sem chamada à Albion, e a rota é `def` (FastAPI roda
    em threadpool, não trava o event loop no commit)."""
    if HOSTS.get(region) is None:
        return {"seen": len(names), "known": 0, "queued": 0}
    wanted = {n.strip().lower() for n in names if n and n.strip()}
    if not wanted:
        return {"seen": len(names), "known": 0, "queued": 0}
    db = SyncSessionLocal()
    queued = 0
    rows = []
    try:
        rows = db.scalars(
            select(AlbionPlayer).where(
                AlbionPlayer.region == region,
                func.lower(AlbionPlayer.name).in_(wanted),
            )
        ).all()
        now = datetime.now(timezone.utc)
        for p in rows:
            if p.refresh_requested_at is None and (now - _aware(p.last_seen_at) > STALE_AFTER):
                p.refresh_requested_at = now
                queued += 1
        if queued:
            db.commit()
            request_refresh()
            log.info("warm/seen (%s): %d de %d conhecidos enfileirados pra refresh", region, queued, len(rows))
    except Exception as e:
        db.rollback()
        log.debug("queue_refresh_seen %s: %s", region, e)
    finally:
        db.close()
    return {"seen": len(names), "known": len(rows), "queued": queued}


async def sync_once() -> int:
    """Processa um lote de batalhas pendentes. Retorna quantas processou."""
    processed = 0
    async with AsyncSessionLocal() as db:
        battles = (await db.scalars(
            select(Battle).where(Battle.profiles_synced.is_(False)).order_by(Battle.id).limit(BATCH_SIZE)
        )).all()
        if not battles:
            return 0
        # Materializa IDs das batalhas e commita antes do HTTP — read tx aberta
        # durante await impede wal_checkpoint. battle.id é estável (autoincrement).
        battle_ids = [(b.id, b.region) for b in battles]
        await db.commit()
        async with make_client() as client:
            for battle_id, region in battle_ids:
                host = HOSTS.get(region)
                if host is not None:
                    player_ids = (await db.scalars(
                        select(BattleParticipant.albion_player_id).where(BattleParticipant.battle_id == battle_id)
                    )).all()
                    # Commit antes do HTTP: libera read tx do SELECT de player_ids.
                    await db.commit()
                    for albion_id in player_ids:
                        await _warm_player(client, db, host, region, albion_id)
                # Rebusca a battle pra marcar profiles_synced (não guardar ORM
                # obsoleto da SELECT inicial, já commited e expirado).
                b = await db.get(Battle, battle_id)
                if b is not None:
                    b.profiles_synced = True
                    await db.commit()
                processed += 1
    return processed


async def run_refresh_forever() -> None:
    """Loop DEDICADO a refreshes explícitos (botão ⟳ de player/guild/alliance).

    Separado do backfill (`run_forever`/`sync_once`) e rodando como task própria
    concorrente, pra que um pedido do usuário NUNCA fique preso atrás de um lote
    de backfill (que, com o rate limiter apertado, leva minutos). Os três
    sync_*_refresh_requests entram em albion_scope(PROFILE) → reserved pool, e
    a prioridade PROFILE (0) do rate limiter serve o refresh na frente do
    backfill (WARM=12) mesmo quando os dois loops disputam a Albion. Acordado
    na hora por request_refresh() (via refresh_event) quando um POST enfileira."""
    log.info("profile_warmer: refresh loop iniciando")
    while True:
        refresh_event.clear()  # POSTs durante o trabalho acendem pra próxima volta
        try:
            n = await sync_refresh_requests()
            n += await sync_guild_refresh_requests()
            n += await sync_alliance_refresh_requests()
            if n:
                log.debug("profile_warmer: %d refresh(es) explícito(s) processado(s)", n)
        except Exception as e:
            log.error("profile_warmer: erro no refresh loop: %s", e)
            n = 0
        # n>0: pode haver mais na fila (batch de 10) — cicla rápido. Senão dorme
        # até um POST /refresh acordar via refresh_event.
        timeout = BUSY_INTERVAL if n > 0 else IDLE_INTERVAL
        try:
            await asyncio.wait_for(refresh_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass


async def run_forever() -> None:
    """Backfill de perfis de batalhas rastreadas (fila `profiles_synced`). Roda
    em WARM: abaixo da descoberta de batalha NOVA (não a atrasa) e acima do
    backfill de batalha ANTIGA — a cadeia de prioridade pedida pelo dono
    (batalhas > warmer > batalhas antigas, ver albion_gate). Os refreshes
    explícitos rodam em `run_refresh_forever`, task concorrente."""
    log.info("profile_warmer: backfill iniciando")
    while True:
        try:
            async with albion_scope(WARM):
                n = await sync_once()
        except Exception as e:
            log.error("profile_warmer: erro: %s", e)
            n = 0
        await asyncio.sleep(BUSY_INTERVAL if n > 0 else IDLE_INTERVAL)
