import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from database import (
    activate_server, is_server_activated,
    load_economy_config, update_economy_config,
)
from cogs.setup import has_configured_role
from utils import send_ok, send_err, send_info
import utils

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

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

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Verifica se o servidor foi ativado antes de executar comandos, exceto `/ativar`."""
        # Permitir execução do comando de ativação mesmo em servidores não ativados
        try:
            cmd_name = ctx.command.name
        except Exception:
            cmd_name = None

        if cmd_name == 'ativar':
            return True

        # Se não for em um servidor (DM), permitir
        if ctx.guild is None:
            return True

        # Verificar ativação do servidor
        if not await is_server_activated(ctx.guild.id):
            await send_err(ctx, 'Este servidor não está ativado!\n'
                                'Use `/ativar` para ativar o bot neste servidor.'
            )
            return False

        return True

    @commands.hybrid_command(name='ativar', description='Ativa o bot no servidor (apenas owner)')
    async def ativar(self, ctx: commands.Context):
        """Ativa o bot no servidor para uso (apenas OWNER)."""
        if ctx.author.id != OWNER_ID:
            await send_err(ctx, 'Apenas o owner pode usar este comando.')
            return

        if ctx.guild is None:
            await send_err(ctx, 'Este comando só pode ser usado em um servidor.')
            return

        # Verificar se já está ativado
        if await is_server_activated(ctx.guild.id):
            await send_info(ctx, 'Este servidor já está ativado!')
            return

        await activate_server(ctx.guild.id)
        await send_ok(ctx, f'Servidor ativado com sucesso!\nO bot agora está funcional neste servidor: **{ctx.guild.name}**')
        print(f'✓ Servidor {ctx.guild.name} (ID: {ctx.guild.id}) ativado')

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
