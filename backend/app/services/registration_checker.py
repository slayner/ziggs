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

from sqlalchemy import func, select

from app.auth import discord
from app.config import get_settings
from app.db import get_session
from app.models.registration import BotRegistration
from app.models.tenancy import Guild
from app.services.albion_gate import BOT_REGISTER, albion_scope, slot
from app.services.player_tracker import HOSTS, make_client

log = logging.getLogger(__name__)
CHECK_INTERVAL = 6 * 3600  # segundos — mesma cadência do guild_check_loop do bot legado
VERIFY_MAX_ATTEMPTS = 3    # ciclos com nick não encontrado antes de revogar o registro manual
VERIFY_POLL_INTERVAL = 15 * 60  # tick do loop; com verificação pendente roda o ciclo na hora
_SEARCH_RETRIES = 2


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


async def _resolve_nick(client, name: str, region_pref: str | None):
    """Busca o nick no search da API pública (mesma rota do /register).
    Devolve (found, região, any_host_ok): found=None com any_host_ok=True =
    nick não existe (resposta válida da API); any_host_ok=False = API fora do
    ar/instável — NÃO conta como falha, o próximo ciclo tenta de novo."""
    nl = name.lower()
    hosts = {region_pref: HOSTS[region_pref]} if region_pref in HOSTS else HOSTS
    any_ok = False
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
            match = next(
                (p for p in resp.json().get("players", []) if (p.get("Name") or "").lower() == nl),
                None,
            )
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
    # Persiste active=False e libera read tx antes do await (Discord API call
    # em thread — read tx aberta impede wal_checkpoint).
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

                    if is_ally:
                        allowed_allies = g_settings.get("ally_allowed_guilds") or ["none"]
                        still_valid = bool(
                            data is not None
                            and g_alliance_id
                            and player_alliance_id == g_alliance_id
                            and ("all" in allowed_allies or player_guild_id in allowed_allies)
                        )
                    else:
                        owned = owned_ids_by_guild.get(guild_id, set())
                        if g_guild_id:
                            owned.add(str(g_guild_id))
                        still_valid = data is not None and player_guild_id in owned

                    if still_valid:
                        continue

                    reg = db.get(BotRegistration, rid)
                    if reg is not None:
                        await _revoke(db, reg, bot_token, "saiu da aliança permitida" if is_ally else "saiu da guilda Albion")

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
