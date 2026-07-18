"""Battle feed — mensageiro de batalhas.

Polla o backend por batalhas novas que casam com os filtros da guilda (canal
configurado + mínimo de jogadores) e posta o link no canal definido pelo admin
no site (GuildConfig → Battle Feed).

O link aponta pro endpoint de embed do backend (/battles/embed/{public_id})
que serve OG tags com a imagem de resumo PNG — o Discord gera o card
automaticamente. meta-refresh redireciona humanos pro frontend (SPA). Assim
qualquer pessoa colando o link vê o embed, não só o bot. Nenhum texto além
do link é enviado, como pedido.

Mesma estrutura de cogs/audit_log.py: @tasks.loop, _cog_ref global, before_loop
espera wait_until_ready, .error auto-reinicia via call_soon."""
import asyncio
import os
from typing import Optional

import discord
from discord.ext import commands, tasks

import http_client
from cogs.general import _guild_command_config

SITE_URL = os.getenv("BOT_SITE_URL", "").rstrip("/")


async def _get(path: str) -> Optional[dict]:
    return await http_client.get_json(path)


async def _post(path: str, body: dict) -> Optional[dict]:
    return await http_client.post_json(path, body)


_cog_ref: "BattleFeed | None" = None


class BattleFeed(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        global _cog_ref
        _cog_ref = self
        print("[battle_feed] cog carregada — loop de battle feed ativo")
        if not battle_feed_loop.is_running():
            battle_feed_loop.start(self)

    async def cog_unload(self) -> None:
        battle_feed_loop.cancel()

    async def sync_guild(self, guild: discord.Guild) -> None:
        cfg = await _guild_command_config(guild.id)
        channel_id = cfg.get("battle_feed_channel_id")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            return

        data = await _get(f"/bot/guilds/{guild.id}/battle-feed")
        if data is None:
            return  # site fora do ar — próximo tick cobre
        battles = data.get("battles") or []
        if not battles:
            return

        last_id = None
        for b in battles:
            # Link de embed (backend) — o Discord crawler lê as OG tags e gera
            # o card com a imagem de resumo. meta-refresh redireciona humanos
            # pro frontend (SPA). Assim qualquer pessoa colando o link vê o
            # embed, não só o bot.
            link = f"{SITE_URL}/battles/embed/{b['public_id']}"
            try:
                await channel.send(content=link)
            except (discord.Forbidden, discord.HTTPException):
                break  # para no primeiro erro — ack só até o último enviado
            last_id = b["id"]

        if last_id is not None:
            await _post(f"/bot/guilds/{guild.id}/battle-feed-synced", {"last_id": last_id})


@tasks.loop(seconds=30)
async def battle_feed_loop(cog: BattleFeed) -> None:
    # ponytail: paralelo como event_work_loop — com muitas guildas o sequencial
    # soma latência à toa (30s de ciclo, mas N guildas em sequência pode passar).
    await asyncio.gather(
        *(cog.sync_guild(g) for g in cog.bot.guilds),
        return_exceptions=True,
    )


@battle_feed_loop.before_loop
async def _before() -> None:
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@battle_feed_loop.error
async def _on_error(error: BaseException) -> None:
    import traceback
    print(f"[battle_feed] LOOP MORREU, reiniciando: {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if _cog_ref is not None:
        asyncio.get_running_loop().call_soon(lambda: battle_feed_loop.start(_cog_ref))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BattleFeed(bot))