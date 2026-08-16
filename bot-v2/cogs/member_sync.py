"""Sync de membros Discord → backend (5min).

Popula a tabela guild_members com TODOS os membros do server, não só os que
logaram no site via OAuth. O site usa isso no autocomplete ao adicionar
participante a um evento. Saídas (on_member_remove) já são marcadas pelo cog
general; este sync só cuida de entradas e atualizações de cargo/avatar.
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import discord
from discord.ext import commands, tasks

import http_client

SITE_URL = os.getenv("BOT_SITE_URL", "").rstrip("/")

_SYNC_TIMEOUT = 120  # segundos por guilda (fetch members + POST)


async def _post(path: str, body: dict) -> Optional[dict]:
    return await http_client.post_json(
        path, body, tag="member_sync", attempts=2, queue_on_failure=False,
    )


_cog_ref: "MemberSync | None" = None


class MemberSync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        global _cog_ref
        _cog_ref = self
        print("[member_sync] cog carregada — loop de sync de membros ativo")
        if not sync_loop.is_running():
            sync_loop.start(self)

    async def cog_unload(self) -> None:
        sync_loop.cancel()


@tasks.loop(minutes=5)
async def sync_loop(cog: "MemberSync"):
    for guild in cog.bot.guilds:
        try:
            async with asyncio.timeout(_SYNC_TIMEOUT):
                members = []
                for m in guild.members:
                    if m.bot:
                        continue
                    members.append({
                        "user_id": m.id,
                        "username": m.name,
                        "global_name": m.global_name,
                        "avatar": m.avatar.key if m.avatar else None,
                        "discord_role_ids": [str(r.id) for r in m.roles if r.id != guild.id],
                        "is_guild_admin": m.guild_permissions.manage_guild,
                    })
                await _post(
                    f"/bot/guilds/{guild.id}/members-sync",
                    {"members": members},
                )
        except TimeoutError:
            print(f"[member_sync] sync de {guild.name} passou de {_SYNC_TIMEOUT}s — pulando")
        except Exception:
            import traceback
            traceback.print_exc()


@sync_loop.before_loop
async def _before():
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@sync_loop.error
async def _on_error(error: BaseException):
    print(f"[member_sync] loop MORREU: {error!r} — reiniciando")
    import traceback
    traceback.print_exc()
    if _cog_ref is not None:
        asyncio.get_running_loop().call_soon(lambda: sync_loop.start(_cog_ref))


async def setup(bot: commands.Bot):
    await bot.add_cog(MemberSync(bot))
