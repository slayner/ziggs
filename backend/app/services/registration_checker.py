"""Recheca periodicamente se os personagens registrados via /register ainda
estão na guilda Albion configurada (ou, pra registros de aliados, ainda numa
guilda aliada permitida); remove o cargo Discord de quem saiu/deixou de valer.
Só vigia guildas com register_remove_role_on_leave=True (default) — nas
outras, a checagem de guilda só acontece dentro do próprio /register.

Registros "de confiança" (ID sintético manual:*, criados com a vigilância
desligada) não são vigiados no loop normal. Quando a guilda RELIGA a
vigilância, o PATCH agenda register_verify_pending=True e a fase de
verificação retroativa resolve cada nick na API: encontrado → o registro
vira real (ID verdadeiro) e a membresia é checada na hora (fora da guilda →
cargo removido); não encontrado após VERIFY_MAX_ATTEMPTS ciclos → perde o
registro e o cargo. Mesmo nick em Discords diferentes é mantido — gente com
main+alt registra o mesmo personagem duas vezes, cada linha com seu cargo."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.auth import discord
from app.config import get_settings
from app.db import get_session
from app.models.audit import AuditLog
from app.models.registration import BotRegistration
from app.models.tenancy import Guild
from app.services.albion_gate import BOT_REGISTER, albion_scope, slot
from app.services.player_tracker import HOSTS, make_client

log = logging.getLogger(__name__)


def _pick_best_match(matches: list[dict], owned_ids: set[str], alliance_id: str | None) -> dict:
    """Desambigua múltiplos personagens de mesmo nome (case-insensitive):
    prefere o que está numa das guildas próprias, depois na aliança, depois
    qualquer um com guilda (evita o deletado sem guilda). Fallback: primeiro."""
    if not matches:
        return {}
    for p in matches:
        gid = str(p.get("GuildId") or "")
        if gid and gid in owned_ids:
            return p
    if alliance_id:
        for p in matches:
            aid = str(p.get("AllianceId") or "") or None
            if aid and aid == alliance_id:
                return p
    for p in matches:
        if str(p.get("GuildId") or ""):
            return p
    return matches[0]


# 15min entre ciclos completos — cadência pedida pelo dono pra revogar rápido
# registros de aliados cuja guilda saiu da aliança. Antes era 6h (igual ao bot
# legado), o que deixava membros com permissão por até 6h depois de uma guilda
# aliada sair da aliança. O custo de cada ciclo é 1 HTTP por registro ativo
# (GET /players/{id}); com registros na casa das centenas por guilda e poucas
# guildas, isso ainda cabe folgado no pool reserved BOT_REGISTER (5 slots).
CHECK_INTERVAL = 15 * 60
VERIFY_MAX_ATTEMPTS = 3    # ciclos com nick não encontrado antes de revogar o registro manual
VERIFY_POLL_INTERVAL = 15 * 60  # tick do loop; igual a CHECK_INTERVAL — ciclo roda todo tick
_SEARCH_RETRIES = 2

# Revogação por membresia NÃO é mais na primeira falha da API do Albion. A API
# retorna GuildId vazio/404/stale temporariamente com frequência, e cada falha
# virava revogação imediata de um membro que estava na guild. Agora acumula
# fail_count; só revoga após REVOKE_AFTER_FAILS falhas CONSECUTIVAS em ciclos
# diferentes. Sucesso zera o contador. Com intervalo de 15min, 4 falhas = 1h
# de tolerância — tempo pra API se recuperar de um blip.
REVOKE_AFTER_FAILS = 4


def _manual_attempt_id(current_id: str, nick_lower: str) -> str | None:
    """Próximo ID sintético após MAIS UMA falha de resolução de nick.
    None = tentativas esgotadas (hora de revogar). O contador mora no próprio
    ID ("manual:foo" → "manual:foo#1" → …), então não precisa de coluna nova.
    Só herda o contador se o nick do ID for o MESMO — linha com nick editado
    recomeça do zero."""
    tries = 0
    base = current_id
    if "#" in current_id:
        base, _, count = current_id.rpartition("#")
        if count.isdigit() and base == f"manual:{nick_lower}":
            tries = int(count)
    tries += 1
    if tries >= VERIFY_MAX_ATTEMPTS:
        return None
    return f"manual:{nick_lower}#{tries}"


async def _resolve_nick(
    client, name: str, region_pref: str | None,
    owned_ids: set[str] | None = None, alliance_id: str | None = None,
):
    """Busca o nick no search da API pública (mesma rota do /register).
    Devolve (found, região, any_host_ok): found=None com any_host_ok=True =
    nick não existe (resposta válida da API); any_host_ok=False = API fora do
    ar/instável — NÃO conta como falha, o próximo ciclo tenta de novo.

    Se houver mais de um personagem com o mesmo nome (case-insensitive) — ex.:
    um deletado sem guilda e um ativo na guilda configurada — prefere o que
    está na guilda/aliança configurada (desambiguação igual ao /register)."""
    nl = name.lower()
    hosts = {region_pref: HOSTS[region_pref]} if region_pref in HOSTS else HOSTS
    any_ok = False
    owned = owned_ids or set()
    for r, host in hosts.items():
        for attempt in range(_SEARCH_RETRIES):
            try:
                async with slot(host):
                    resp = await client.get(
                        f"https://{host}/api/gameinfo/search", params={"q": name},
                    )
                resp.raise_for_status()
            except Exception:
                continue
            any_ok = True
            matches = [p for p in resp.json().get("players", []) if (p.get("Name") or "").lower() == nl]
            match = _pick_best_match(matches, owned, alliance_id) if matches else None
            if match:
                return match, r, True
            break  # resposta válida sem o nick nessa região — não repete
    return None, None, any_ok


def _membership_ok(found: dict, g_guild_id, g_alliance_id, g_settings: dict, owned: set) -> tuple[bool, bool]:
    """(membresia válida, é aliado) pelas regras do fluxo normal — a resposta
    do search já traz GuildId/AllianceId, então dá pra decidir sem segunda
    consulta à API."""
    player_guild_id = str(found.get("GuildId") or "")
    player_alliance_id = str(found.get("AllianceId") or "") or None
    if player_guild_id in owned or player_guild_id == g_guild_id:
        return True, False
    if g_alliance_id and player_alliance_id == g_alliance_id:
        allowed = g_settings.get("ally_allowed_guilds") or ["none"]
        if "all" in allowed or player_guild_id in allowed:
            return True, True
    return False, False


async def _revoke(db, reg: BotRegistration, bot_token: str | None, motivo: str) -> None:
    reg.active = False
    db.add(AuditLog(
        guild_id=reg.guild_id, actor_id=reg.discord_user_id,
        actor_type="bot", source="bot",
        action="registration.revoked", entity="bot_registration", entity_id=str(reg.id),
        before={"albion_player_name": reg.albion_player_name, "role_id": str(reg.role_id), "active": True},
        after={"active": False},
        note=motivo,
    ))
    # Persiste active=False + AuditLog e libera read tx antes do await (Discord
    # API call em thread — read tx aberta impede wal_checkpoint).
    db.commit()
    if bot_token:
        try:
            await asyncio.to_thread(
                discord.remove_guild_member_role,
                str(reg.guild_id), str(reg.discord_user_id), str(reg.role_id), bot_token,
            )
        except Exception as e:
            log.warning("registration_checker: falha ao remover cargo de %s: %s", reg.discord_user_id, e)
    log.info("Registro de %s revogado (%s)", reg.albion_player_name, motivo)


async def _mirror_ally_to_own_guild(
    db, ally_reg: BotRegistration, player_guild_id: str, player_name: str,
    player_alliance_id: str | None, player_id: str, region: str,
    bot_token: str | None,
) -> None:
    """Quando um aliado é validado na guilda Ziggs A, e a guilda Albion dele é
    uma `Guild` no Ziggs B com registration ativo, cria um `BotRegistration`
    espelho na guilda B (membro direto, is_ally=False) e atribui o cargo no
    Discord da guilda B — o aliado que já passava pela verificação constante
    recebe automaticamente o cargo de registro na própria guilda dele quando
    ele ativa o registration lá, sem precisar rodar /register de novo.
    Idempotente: se já existe linha ativa, não duplica nem re-aplica o cargo."""
    if not player_guild_id:
        return
    own_guild = db.scalar(select(Guild).where(Guild.albion_guild_id == player_guild_id))
    if own_guild is None or own_guild.id == ally_reg.guild_id:
        return  # a guilda Albion dele não é uma guilda no Ziggs, ou é a própria
    own_settings = own_guild.settings or {}
    own_role_id = own_settings.get("register_role_id")
    if not own_role_id:
        return  # guilda de destino não tem registration ativo — não há o que dar
    # Já existe linha desse personagem + discord na guilda de destino?
    existing = db.scalar(select(BotRegistration).where(
        BotRegistration.guild_id == own_guild.id,
        BotRegistration.albion_player_id == player_id,
        BotRegistration.discord_user_id == ally_reg.discord_user_id,
    ))
    if existing is not None:
        if not existing.active:
            # Reativa linha inativa (voltou pra guilda própria depois de sair).
            existing.active = True
            existing.human_revoked_at = None
            existing.albion_player_name = player_name
            existing.region = region
            existing.role_id = int(own_role_id)
            existing.is_ally = False
            db.commit()
            await _apply_role(
                str(own_guild.id), str(ally_reg.discord_user_id),
                str(own_role_id), bot_token,
            )
        return
    db.add(BotRegistration(
        guild_id=own_guild.id,
        discord_user_id=ally_reg.discord_user_id,
        albion_player_id=player_id,
        albion_player_name=player_name,
        region=region,
        role_id=int(own_role_id),
        is_ally=False,
        active=True,
    ))
    db.add(AuditLog(
        guild_id=own_guild.id, actor_id=None, actor_type="system", source="system",
        action="registration.mirror_ally", entity="bot_registration", entity_id=None,
        after={
            "albion_player_name": player_name, "albion_player_id": player_id,
            "role_id": str(own_role_id), "from_guild_id": str(ally_reg.guild_id),
            "player_guild_id": player_guild_id,
            "alliance_id": player_alliance_id,
        },
        note=f"espelho auto: aliado válido em {ally_reg.guild_id} é membro direto aqui",
    ))
    db.commit()
    await _apply_role(
        str(own_guild.id), str(ally_reg.discord_user_id),
        str(own_role_id), bot_token,
    )
    log.info("Espelho aliado: %s agora registrado em %s (guilda própria dele)",
             player_name, own_guild.id)


async def _apply_role(guild_id: str, user_id: str, role_id: str, bot_token: str | None) -> None:
    """Atribui o cargo no Discord (best-effort: 403/404 = membro não está no
    servidor, ignora — não dá erro)."""
    if not bot_token:
        return
    try:
        await asyncio.to_thread(discord.add_guild_member_role, guild_id, user_id, role_id, bot_token)
    except Exception as e:
        log.warning("registration_checker: falha ao aplicar cargo espelho em %s/%s: %s", guild_id, user_id, e)


async def _verify_manual(db, client, bot_token: str, manual_by_guild: dict, guild_data: dict, owned_ids_by_guild: dict) -> None:
    """Fase 1: verificação retroativa dos registros manuais das guildas com
    register_verify_pending. Cada linha é independente — mesmo nick em
    Discords diferentes vira DUAS linhas reais com o MESMO player ID
    (constraint por user permite): é o caso main+alt que se mantém."""
    for guild_id, rows in manual_by_guild.items():
        g_guild_id, g_alliance_id, g_settings = guild_data.get(guild_id, (None, None, {}))
        guild_region = g_settings.get("albion_guild_region")
        owned = set(owned_ids_by_guild.get(guild_id, set()))
        if g_guild_id:
            owned.add(str(g_guild_id))
        for rid, _gid, region, _pid, albion_player_name, _ally, _uid, _role in rows:
            found, found_region, any_ok = await _resolve_nick(
                client, albion_player_name, guild_region or region,
                owned_ids=owned, alliance_id=g_alliance_id,
            )
            if not any_ok:
                continue  # API fora do ar — não conta falha
            reg = db.get(BotRegistration, rid)  # revalida após o await
            if reg is None or not reg.active:
                continue
            if found is None:
                nxt = _manual_attempt_id(reg.albion_player_id, reg.albion_player_name.lower())
                if nxt is None:
                    await _revoke(db, reg, bot_token, f"nick não encontrado após {VERIFY_MAX_ATTEMPTS} verificações")
                else:
                    reg.albion_player_id = nxt
                    db.commit()
                    log.info("Verificação: nick %s não encontrado (tentativa acumulada no ID %s)", albion_player_name, nxt)
                continue
            real_id = str(found["Id"])
            # Merge: o usuário JÁ tem linha real desse personagem (ex: self-
            # register pelo fluxo normal depois do registro manual) → a linha
            # manual é sobra, deleta em vez de violar a unique (guild, player, user).
            dup = db.scalar(select(BotRegistration).where(
                BotRegistration.guild_id == guild_id,
                BotRegistration.albion_player_id == real_id,
                BotRegistration.discord_user_id == reg.discord_user_id,
                BotRegistration.id != rid,
            ))
            if dup is not None:
                db.delete(reg)
                db.commit()
                log.info("Verificação: registro manual de %s era duplicado do real — merge", albion_player_name)
                continue
            reg.albion_player_id = real_id
            reg.albion_player_name = found["Name"]
            if found_region:
                reg.region = found_region
            valid, is_ally = _membership_ok(found, g_guild_id, g_alliance_id, g_settings, owned)
            reg.is_ally = is_ally
            if valid:
                db.commit()
                log.info("Verificação: %s confirmado (ID real %s)%s", found["Name"], real_id, ", aliado" if is_ally else "")
            else:
                await _revoke(db, reg, bot_token, "verificado fora da guilda")
        # Fila da guilda acabou (nenhum manual: ativo restante)? Limpa a flag.
        still = db.scalar(
            select(func.count()).select_from(BotRegistration).where(
                BotRegistration.guild_id == guild_id,
                BotRegistration.albion_player_id.like("manual:%"),
                BotRegistration.active.is_(True),
            )
        )
        if not still:
            g = db.get(Guild, guild_id)
            if g is not None:
                settings = dict(g.settings or {})
                if settings.pop("register_verify_pending", None) is not None:
                    g.settings = settings
                    db.commit()


async def _check_once() -> None:
    db = next(get_session())
    try:
        regs = db.scalars(select(BotRegistration).where(BotRegistration.active.is_(True))).all()
        if not regs:
            return

        guilds: dict[int, Guild] = {
            g.id: g for g in db.scalars(
                select(Guild).where(Guild.id.in_({r.guild_id for r in regs}))
            ).all()
        }

        # Materializa dados e commit antes do HTTP — read tx aberta durante
        # await (HTTP + Discord API) impede wal_checkpoint.
        reg_data = [(r.id, r.guild_id, r.region, r.albion_player_id, r.albion_player_name,
                     r.is_ally, r.discord_user_id, r.role_id) for r in regs]
        guild_data = {gid: (g.albion_guild_id, g.albion_alliance_id, g.settings or {})
                      for gid, g in guilds.items()}
        from app.services.guild_links import albion_guild_ids
        owned_ids_by_guild = {gid: set(albion_guild_ids(db, gid)) for gid in guilds}
        db.commit()

        # Guildas com verificação retroativa pendente → registros manuais delas.
        manual_by_guild: dict[int, list] = {}
        for row in reg_data:
            if not row[3].startswith("manual:"):
                continue
            g_settings = guild_data.get(row[1], (None, None, {}))[2]
            if g_settings.get("register_verify_pending"):
                manual_by_guild.setdefault(row[1], []).append(row)

        bot_token = get_settings().discord_bot_token
        async with make_client() as client:
            async with albion_scope(BOT_REGISTER):
                if manual_by_guild:
                    await _verify_manual(db, client, bot_token, manual_by_guild, guild_data, owned_ids_by_guild)
                for rid, guild_id, region, albion_player_id, albion_player_name, is_ally, discord_user_id, role_id in reg_data:
                    # Registro "de confiança" (feito com a vigilância desligada,
                    # sem consultar a API): ID sintético manual:*, nunca foi
                    # verificado — não há o que revalidar (a checagem consultaria
                    # um ID que não existe na API e revogaria o registro injustamente).
                    if albion_player_id.startswith("manual:"):
                        continue
                    g_guild_id, g_alliance_id, g_settings = guild_data.get(guild_id, (None, None, {}))
                    if g_guild_id is None or not g_guild_id:
                        continue
                    # Vigilância desligada na guilda (register_remove_role_on_leave=False,
                    # default True): a checagem de guilda só existiu no momento do
                    # /register — registros de lá não são revalidados.
                    if g_settings.get("register_remove_role_on_leave", True) is False:
                        continue
                    host = HOSTS.get(region)
                    if not host:
                        continue
                    try:
                        async with slot(host):
                            resp = await client.get(f"https://{host}/api/gameinfo/players/{albion_player_id}")
                    except Exception as e:
                        log.debug("registration_checker: falha ao consultar %s: %s", albion_player_name, e)
                        continue

                    data = resp.json() if resp.status_code == 200 else None
                    player_guild_id = str(data.get("GuildId") or "") if data else None
                    player_alliance_id = (str(data.get("AllianceId") or "") or None) if data else None

                    # 404/timeout/JSON inválido = API instável, NÃO é "saiu da
                    # guild". Não conta falha nem sucesso — próxima tentativa.
                    if data is None:
                        continue

                    if is_ally:
                        allowed_allies = g_settings.get("ally_allowed_guilds") or ["none"]
                        still_valid = bool(
                            g_alliance_id
                            and player_alliance_id == g_alliance_id
                            and ("all" in allowed_allies or player_guild_id in allowed_allies)
                        )
                        # Aliado válido → espelha pra própria guilda dele se ela
                        # existir no Ziggs (auto-registro sem /register manual).
                        if still_valid and player_guild_id and albion_player_id:
                            reg_snapshot = db.get(BotRegistration, rid)
                            if reg_snapshot is not None:
                                await _mirror_ally_to_own_guild(
                                    db, reg_snapshot, player_guild_id,
                                    albion_player_name, player_alliance_id,
                                    albion_player_id, region, bot_token,
                                )
                    else:
                        owned = owned_ids_by_guild.get(guild_id, set())
                        if g_guild_id:
                            owned.add(str(g_guild_id))
                        still_valid = player_guild_id in owned

                    reg_fresh = db.get(BotRegistration, rid)
                    if reg_fresh is None or not reg_fresh.active:
                        continue

                    if still_valid:
                        # Sucesso zera o contador de falhas consecutivas.
                        if reg_fresh.fail_count > 0:
                            reg_fresh.fail_count = 0
                            reg_fresh.last_fail_at = None
                            db.commit()
                        continue

                    # Falha real (API respondeu 200 mas GuildId não bate).
                    # Acumula e só revoga após REVOKE_AFTER_FAILS consecutivas.
                    reg_fresh.fail_count = (reg_fresh.fail_count or 0) + 1
                    reg_fresh.last_fail_at = datetime.now(timezone.utc)
                    db.commit()
                    if reg_fresh.fail_count < REVOKE_AFTER_FAILS:
                        log.info("registration_checker: %s falhou validação (%d/%d) — aguardando mais falhas antes de revogar",
                                 albion_player_name, reg_fresh.fail_count, REVOKE_AFTER_FAILS)
                        continue
                    await _revoke(db, reg_fresh, bot_token, "saiu da aliança permitida" if is_ally else "saiu da guilda Albion")

        db.commit()
    except Exception:
        log.exception("Erro no registration_checker")
        db.rollback()
    finally:
        db.close()


def _has_pending_verification_sync() -> bool:
    """Alguma guilda com register_verify_pending e registros manuais ativos?
    Query barata (uma por tick de 15min) que decide se o ciclo completo roda
    na hora em vez de esperar o sweep de 6h."""
    db = next(get_session())
    try:
        gids = [g.id for g in db.scalars(select(Guild)).all()
                if (g.settings or {}).get("register_verify_pending")]
        if not gids:
            return False
        return db.scalar(
            select(func.count()).select_from(BotRegistration).where(
                BotRegistration.active.is_(True),
                BotRegistration.albion_player_id.like("manual:%"),
                BotRegistration.guild_id.in_(gids),
            )
        ) > 0
    finally:
        db.close()


async def run_forever() -> None:
    last_sweep = 0.0
    while True:
        await asyncio.sleep(VERIFY_POLL_INTERVAL)
        try:
            pending = await asyncio.to_thread(_has_pending_verification_sync)
        except Exception:
            pending = False
        now = time.monotonic()
        if not pending and (now - last_sweep) < CHECK_INTERVAL:
            continue
        await _check_once()
        last_sweep = time.monotonic()
