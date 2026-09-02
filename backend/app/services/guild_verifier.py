"""Verificação de fundo das guildas de Albion vinculadas a cada servidor
Discord. Resolve o ID real da guilda na API pública da Albion (a partir do
nome), descobre a aliança e seus membros, e periodicamente re-confirma que
tudo ainda bate — alianças trocam de composição, guildas saem, nomes somem.

Doutrina igual battle scan / warm: só olhamos ONDE buscar; a verdade vem da
API pública da Albion, NUNCA do client. O usuário adiciona pelo nome (sem
saber o ID); este worker converte nome → ID + aliança + membros e grava nos
campos canônicos (`Guild.albion_guild_id`, `albion_alliance_id`,
`GuildAlbionLink.verified`, `Guild.settings["alliance_members"]`).

A verificação é idempotente: re-rodar sobre a mesma guilda só atualiza o que
mudou (aliança nova, guilda saiu da aliança, nome sumiu). Falha de uma guilda
não derruba o ciclo — segue pra próxima."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SyncSessionLocal
from app.models.tenancy import Guild, GuildAlbionLink
from app.services.albion_gate import GUILD_VERIFY, albion_scope, slot
from app.services.player_tracker import HOSTS, make_client

log = logging.getLogger(__name__)

VERIFY_INTERVAL = 15 * 60


async def _search_guild(client, host: str, name: str) -> dict | None:
    """Busca `name` no endpoint público de search e casa por nome (casefold).
    Devolve o registro cru da guilda (com Id/AllianceId/AllianceName) ou None."""
    nl = name.strip().lower()
    if not nl:
        return None
    try:
        async with slot(host):
            resp = await client.get(f"https://{host}/api/gameinfo/search", params={"q": name})
    except Exception as e:
        log.debug("guild_verifier: search %s @ %s falhou: %s", name, host, e)
        return None
    if resp.status_code != 200:
        return None
    candidates = resp.json().get("guilds", []) if resp.json() else []
    return next((g for g in candidates if (g.get("Name") or "").lower() == nl), None)


async def _fetch_guild(client, host: str, guild_id: str) -> dict | None:
    try:
        async with slot(host):
            resp = await client.get(f"https://{host}/api/gameinfo/guilds/{guild_id}")
    except Exception as e:
        log.debug("guild_verifier: guild %s @ %s falhou: %s", guild_id, host, e)
        return None
    if resp.status_code != 200:
        return None
    raw = resp.json()
    return raw if isinstance(raw, dict) and raw.get("Id") else None


async def _fetch_alliance_members(client, host: str, alliance_id: str) -> list[dict] | None:
    try:
        async with slot(host):
            resp = await client.get(f"https://{host}/api/gameinfo/alliances/{alliance_id}")
    except Exception as e:
        log.debug("guild_verifier: alliance %s @ %s falhou: %s", alliance_id, host, e)
        return None
    if resp.status_code != 200:
        return None
    raw = resp.json()
    if not isinstance(raw, dict):
        return None
    gs = raw.get("Guilds") or raw.get("guilds") or []
    out = []
    for g in gs:
        if isinstance(g, dict) and g.get("Id"):
            out.append({"id": str(g["Id"]), "name": g.get("Name") or str(g["Id"])})
    return out


def _set_guild_verified(g: Guild, value: bool) -> None:
    s = g.settings if isinstance(g.settings, dict) else {}
    s["guild_verified"] = value
    g.settings = s


async def _verify_guild(client, db: Session, g: Guild) -> None:
    """Verifica a guilda primária e cada link adicional de um servidor Discord.
    Tudo numa só sessão síncrona; commit no fim. Erros por link são isolados."""
    region = (g.settings or {}).get("albion_guild_region") or "americas"
    host = HOSTS.get(region) or HOSTS["americas"]

    # ── Primária ────────────────────────────────────────────────────────────
    if g.albion_guild_id:
        # Já tem ID real — busca direto pelo ID (mais confiável que search
        # por nome, que falha com espaços/timeout na API da Albion).
        detail = await _fetch_guild(client, host, g.albion_guild_id)
        if detail:
            g.albion_guild_name = detail.get("Name") or g.albion_guild_name
            g.albion_alliance_id = (str(detail.get("AllianceId") or "") or None)
            g.albion_alliance_name = detail.get("AllianceName") or None
            _set_guild_verified(g, True)
            if g.albion_alliance_id:
                members = await _fetch_alliance_members(client, host, g.albion_alliance_id)
                s = g.settings if isinstance(g.settings, dict) else {}
                if members is not None:
                    s["alliance_members"] = members
                g.settings = s
            else:
                s = g.settings if isinstance(g.settings, dict) else {}
                s.pop("alliance_members", None)
                g.settings = s
        else:
            _set_guild_verified(g, False)
            log.info("guild_verifier: primária id=%s não encontrada em %s", g.albion_guild_id, region)
    elif g.albion_guild_name:
        match = await _search_guild(client, host, g.albion_guild_name)
        if match and match.get("Id"):
            g.albion_guild_id = str(match["Id"])
            g.albion_alliance_id = (str(match.get("AllianceId") or "") or None)
            g.albion_alliance_name = match.get("AllianceName") or None
            _set_guild_verified(g, True)
            # Confirma aliança via endpoint de guilda (o search às vezes não
            # traz AllianceName, só AllianceId) e descobre membros da aliança.
            if g.albion_alliance_id:
                detail = await _fetch_guild(client, host, g.albion_guild_id)
                if detail:
                    g.albion_alliance_id = (str(detail.get("AllianceId") or "") or None)
                    g.albion_alliance_name = detail.get("AllianceName") or g.albion_alliance_name
                members = await _fetch_alliance_members(client, host, g.albion_alliance_id)
                s = g.settings if isinstance(g.settings, dict) else {}
                if members is not None:
                    s["alliance_members"] = members
                g.settings = s
            else:
                s = g.settings if isinstance(g.settings, dict) else {}
                s.pop("alliance_members", None)
                g.settings = s
        else:
            _set_guild_verified(g, False)
            log.info("guild_verifier: primária %r não encontrada em %s", g.albion_guild_name, region)

    # ── Links adicionais ───────────────────────────────────────────────────
    links = db.scalars(
        select(GuildAlbionLink).where(GuildAlbionLink.guild_id == g.id)
    ).all()
    for link in links:
        link_host = HOSTS.get(link.region) or host
        # Se o ID ainda é sintético (manual:), tenta resolver pelo nome.
        synthetic = (link.albion_guild_id or "").startswith("manual:")
        if not synthetic:
            # Já tem ID real — só confirma que ainda existe.
            detail = await _fetch_guild(client, link_host, link.albion_guild_id)
            if detail:
                link.verified = True
                link.alliance_id = (str(detail.get("AllianceId") or "") or None)
                link.alliance_name = detail.get("AllianceName") or None
                link.albion_guild_name = detail.get("Name") or link.albion_guild_name
            else:
                link.verified = False
            continue
        if not link.albion_guild_name:
            link.verified = False
            continue
        match = await _search_guild(client, link_host, link.albion_guild_name)
        if match and match.get("Id"):
            link.albion_guild_id = str(match["Id"])
            link.alliance_id = (str(match.get("AllianceId") or "") or None)
            link.alliance_name = match.get("AllianceName") or None
            link.verified = True
        else:
            link.verified = False
            log.info("guild_verifier: link %r não encontrado em %s", link.albion_guild_name, link.region)

    db.commit()


async def _run_once() -> None:
    db = SyncSessionLocal()
    try:
        guilds = db.scalars(
            select(Guild).where(Guild.albion_guild_id.is_not(None))
        ).all()
        # Snapshot só com o que o loop precisa pra abrir a sessão curta por
        # guilda (HOST/HTTP não pode rodar com read tx de listagem aberta).
        targets = [(g.id,) for g in guilds]
        db.commit()
    except Exception:
        log.exception("guild_verifier: erro ao listar guildas")
        db.rollback()
        db.close()
        return

    async with make_client() as client:
        async with albion_scope(GUILD_VERIFY):
            for (gid,) in targets:
                db2 = SyncSessionLocal()
                try:
                    g = db2.get(Guild, gid)
                    if g is None or not g.albion_guild_id:
                        continue
                    await _verify_guild(client, db2, g)
                except Exception as e:
                    db2.rollback()
                    log.warning("guild_verifier: guild %s falhou: %s", gid, e)
                finally:
                    db2.close()


async def run_forever() -> None:
    log.info("guild_verifier: iniciando (intervalo=%ds)", VERIFY_INTERVAL)
    # ponytail: folga de 60s no boot — não brigar com os fetchers acordando.
    #
    # Escala: rodando no backend com albion_scope(GUILD_VERIFY=5) — abaixo das
    # pesquisas user-facing (perfil/register/claim) mas acima da cadeia de
    # batalhas (NEW_ELIGIBLE=10+). Se a lista de guildas crescer a ponto de
    # a verificação recorrente concorrer demais com batalhas no pool bg,
    # migrar pro scan_dispatcher: novo feed_type="guild_verify" com prioridade
    # 5 (mesma) — VPS workers buscam search+guild+alliance na API pública e
    # reportam o cru, backend aplica o upsert (mesmo padrão de "battles"/
    # "kills"). Sem mudar schema hoje — ScanWorkTask.target é nullable e basta
    # carregar guild_id lá; a coluna feed_type (String(16)) já comporta.
    await asyncio.sleep(60)
    while True:
        try:
            await _run_once()
        except Exception as e:
            log.error("guild_verifier: erro no ciclo: %s", e)
        await asyncio.sleep(VERIFY_INTERVAL)