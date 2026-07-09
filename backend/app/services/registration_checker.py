"""Recheca periodicamente se os personagens registrados via /register ainda
estão na guilda Albion configurada (ou, pra registros de aliados, ainda numa
guilda aliada permitida); remove o cargo Discord de quem saiu/deixou de valer."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.auth import discord
from app.config import get_settings
from app.db import get_session
from app.models.registration import BotRegistration
from app.models.tenancy import Guild
from app.services.albion_gate import BOT_REGISTER, albion_scope, slot
from app.services.player_tracker import HOSTS, make_client

log = logging.getLogger(__name__)
CHECK_INTERVAL = 6 * 3600  # segundos — mesma cadência do guild_check_loop do bot legado


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

        bot_token = get_settings().discord_bot_token
        async with make_client() as client:
            async with albion_scope(BOT_REGISTER):
                for reg in regs:
                    g = guilds.get(reg.guild_id)
                    if g is None or not g.albion_guild_id:
                        continue
                    host = HOSTS.get(reg.region)
                    if not host:
                        continue
                    try:
                        async with slot():
                            resp = await client.get(f"https://{host}/api/gameinfo/players/{reg.albion_player_id}")
                    except Exception as e:
                        log.debug("registration_checker: falha ao consultar %s: %s", reg.albion_player_name, e)
                        continue

                    data = resp.json() if resp.status_code == 200 else None
                    player_guild_id = str(data.get("GuildId") or "") if data else None
                    player_alliance_id = (str(data.get("AllianceId") or "") or None) if data else None

                    if reg.is_ally:
                        allowed_allies = (g.settings or {}).get("ally_allowed_guilds") or ["none"]
                        still_valid = bool(
                            data is not None
                            and g.albion_alliance_id
                            and player_alliance_id == g.albion_alliance_id
                            and ("all" in allowed_allies or player_guild_id in allowed_allies)
                        )
                    else:
                        still_valid = data is not None and player_guild_id == str(g.albion_guild_id)

                    if still_valid:
                        continue

                    reg.active = False
                    if bot_token:
                        try:
                            # ponytail: remove_guild_member_role é httpx síncrono
                            # (timeout=5). Chamado direto no event loop congelaria
                            # todos os handlers async por até 5s por membro; rodar
                            # em thread libera o loop.
                            await asyncio.to_thread(
                                discord.remove_guild_member_role,
                                str(reg.guild_id), str(reg.discord_user_id), str(reg.role_id), bot_token,
                            )
                        except Exception as e:
                            log.warning("registration_checker: falha ao remover cargo de %s: %s", reg.discord_user_id, e)
                    motivo = "saiu da aliança permitida" if reg.is_ally else "saiu da guilda Albion"
                    log.info("Registro de %s revogado (%s)", reg.albion_player_name, motivo)

        db.commit()
    except Exception:
        log.exception("Erro no registration_checker")
        db.rollback()
    finally:
        db.close()


async def run_forever() -> None:
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        await _check_once()
