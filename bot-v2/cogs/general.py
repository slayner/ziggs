"""Comandos gerais: /avatar e /banner."""
import os, time
import aiohttp
import discord
from discord import app_commands, Interaction, Member
from discord.ext import commands
from typing import Optional

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")

# Cache de comandos desativados por guild: {guild_id: (frozenset[str], timestamp)}
_cmd_cache: dict[int, tuple[frozenset[str], float]] = {}
_CMD_TTL = 60  # segundos


async def _disabled_commands(guild_id: int) -> frozenset[str]:
    """Retorna o conjunto de comandos desativados para o servidor (cache 60s)."""
    now = time.monotonic()
    cached = _cmd_cache.get(guild_id)
    if cached and (now - cached[1]) < _CMD_TTL:
        return cached[0]
    if not SITE_URL or not API_SECRET:
        return frozenset()
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(
                f"{SITE_URL}/bot/guild-commands/{guild_id}",
                headers={"Authorization": f"Bearer {API_SECRET}"},
                timeout=aiohttp.ClientTimeout(total=3),
            )
            if r.status == 200:
                data = await r.json()
                disabled = frozenset(data.get("disabled", []))
                _cmd_cache[guild_id] = (disabled, now)
                return disabled
    except Exception:
        pass
    return frozenset()


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="avatar", description="Mostra o avatar de um usuário ou servidor")
    @app_commands.describe(membro="Usuário alvo (padrão: você mesmo)")
    async def avatar(self, interaction: Interaction, membro: Optional[Member] = None) -> None:
        if interaction.guild_id:
            disabled = await _disabled_commands(interaction.guild_id)
            if "avatar" in disabled:
                await interaction.response.send_message(
                    "Este comando está desativado neste servidor.", ephemeral=True
                )
                return

        user = membro or interaction.user
        url  = user.display_avatar.url
        embed = discord.Embed(color=getattr(user, "color", discord.Color.blurple()))
        embed.set_author(name=str(user), icon_url=url)
        embed.set_image(url=url)
        # Link para avatar global quando em servidor (display_avatar pode ser server-specific)
        if membro and hasattr(membro, "avatar") and membro.avatar:
            global_url = membro.avatar.url
            if global_url != url:
                embed.description = f"[Avatar global]({global_url})"
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="banner", description="Mostra o banner de um usuário")
    @app_commands.describe(membro="Usuário alvo (padrão: você mesmo)")
    async def banner(self, interaction: Interaction, membro: Optional[Member] = None) -> None:
        if interaction.guild_id:
            disabled = await _disabled_commands(interaction.guild_id)
            if "banner" in disabled:
                await interaction.response.send_message(
                    "Este comando está desativado neste servidor.", ephemeral=True
                )
                return

        target = membro or interaction.user
        # fetch_user traz o banner (objeto de cache não inclui)
        user = await self.bot.fetch_user(target.id)
        if not user.banner:
            await interaction.response.send_message(
                f"**{target.display_name}** não tem banner.", ephemeral=True
            )
            return
        embed = discord.Embed(color=getattr(target, "color", discord.Color.blurple()))
        embed.set_author(name=str(target), icon_url=target.display_avatar.url)
        embed.set_image(url=user.banner.url)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
