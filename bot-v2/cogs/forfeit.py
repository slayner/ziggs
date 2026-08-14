"""Confisca saldos de membros que saíram do servidor há mais de 7 dias.

Porta do forfeit_balance_to_bank do bot legado (cogs/mentoria.py): quando um
membro sai do Discord, o bot marca left_at no backend. Um loop periódico
chama /bot/economy/forfeit-due que transfere o saldo pro banco da guilda
após o grace period. O log vai pro canal de logs configurado no /setup.
"""
import asyncio
import os

import discord
from discord.ext import commands, tasks

import http_client
from cogs.economy import format_silver
from cogs.general import _guild_command_config

SITE_URL = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")

_cog_ref: "ForfeitCog | None" = None


class ForfeitCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await http_client.post_best_effort(
            f"/bot/economy/member-left/{member.guild.id}/{member.id}",
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await http_client.post_best_effort(
            f"/bot/economy/member-returned/{member.guild.id}/{member.id}",
        )


@tasks.loop(minutes=30)
async def forfeit_loop(cog: ForfeitCog) -> None:
    for guild in cog.bot.guilds:
        try:
            data = await http_client.post_json(
                f"/bot/economy/forfeit-due/{guild.id}",
                {}, tag="forfeit", attempts=2, queue_on_failure=False,
            )
            forfeited = data.get("forfeited") or [] if data else []
            if not forfeited:
                continue
            cfg = await _guild_command_config(guild.id)
            chan_id = cfg.get("logs_channel_id")
            if chan_id:
                ch = guild.get_channel(int(chan_id))
                if ch is None:
                    try:
                        ch = await guild.fetch_channel(int(chan_id))
                    except discord.HTTPException:
                        ch = None
                if ch is not None:
                    for f in forfeited:
                        uid = f["user_id"]
                        amount = f["amount"]
                        try:
                            await ch.send(
                                f"🏦 Saldo de <@{uid}> — **{format_silver(amount)}** — "
                                f"transferido para o **guild bank** (7 dias fora da guilda).",
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                        except discord.HTTPException:
                            pass
            print(f"[forfeit] {guild.id}: {len(forfeited)} confisc(s)", flush=True)
        except Exception as e:
            print(f"[forfeit] {guild.id}: {type(e).__name__}: {e}", flush=True)


@forfeit_loop.before_loop
async def _before() -> None:
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@forfeit_loop.error
async def _on_error(error: BaseException) -> None:
    import traceback
    print(f"[forfeit] loop morreu, reiniciando: {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if _cog_ref is not None:
        asyncio.get_running_loop().call_soon(lambda: forfeit_loop.start(_cog_ref))


async def setup(bot: commands.Bot) -> None:
    global _cog_ref
    _cog_ref = ForfeitCog(bot)
    await bot.add_cog(_cog_ref)
    forfeit_loop.start(_cog_ref)
    print("[forfeit] cog carregada — loop de confisc de saldo ativo", flush=True)