import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from database import (
    load_economy_config, update_economy_config,
)
from utils import send_err
import utils

load_dotenv()

# Disarray (nível 1..67) -> nº de jogadores correspondente. Índice = nível-1.
DISARRAY_PLAYERS = [
    21, 22, 23, 24, 25, 26, 27, 28, 29, 30,
    31, 32, 33, 34, 36, 37, 39, 41, 44, 46,
    48, 49, 51, 54, 56, 58, 61, 64, 67, 70,
    74, 79, 83, 89, 95, 99, 103, 108, 114, 119,
    126, 133, 141, 148, 154, 160, 167, 175, 183, 192,
    200, 207, 215, 223, 232, 242, 252, 264, 276, 290,
    305, 322, 341, 361, 385, 412, 445,
]


class Misc(commands.Cog):
    """Cog misc para comandos de administração simples"""

    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='disarray',
        description='Quantos jogadores corresponde a um nível de disarray (1 a 67).',
    )
    @app_commands.guild_only()
    @app_commands.describe(numero='Nível de disarray (1 a 67)')
    async def disarray(self, ctx: commands.Context, numero: int):
        n = len(DISARRAY_PLAYERS)
        if not (1 <= numero <= n):
            await send_err(ctx, f'Disarray deve estar entre **1** e **{n}**.')
            return
        players = DISARRAY_PLAYERS[numero - 1]
        await ctx.send(f'🔢 Disarray **{numero}** = **{players}** jogadores.', ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(ctx)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # TeamSpeak
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name='teamspeak',
        aliases=['ts', 'ts3'],
        description='Mostra o endereço e a senha do TeamSpeak.',
    )
    @app_commands.guild_only()
    async def teamspeak(self, ctx: commands.Context):
        cfg = await load_economy_config()
        addr = (cfg.get('teamspeak_address') or '').strip()
        pw = (cfg.get('teamspeak_password') or '').strip()
        if not addr:
            await send_err(ctx, 'TeamSpeak ainda não configurado. Use `/setup` → TeamSpeak.')
            return
        msg = f'🎧 **TeamSpeak**\n📡 Endereço: `{addr}`'
        if pw:
            msg += f'\n🔑 Senha: ||{pw}||'   # senha em spoiler
        await ctx.send(msg, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(ctx)
        except Exception:
            pass


async def setup(bot):
    await bot.add_cog(Misc(bot))
