import re

import discord
from discord.ext import commands
from discord import app_commands
from database import is_server_activated
from utils import make_embed, send_err

# Captura um snowflake (ID de usuário ou servidor) de uma @menção ou ID cru.
_ID_RE = re.compile(r'(\d{15,25})')


class Avatar(commands.Cog):
    """Cog com comandos de avatar/banner (de usuários OU de servidores)."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Verifica se o servidor foi ativado antes de executar comandos"""
        # Se não for em um servidor, permitir (DM)
        if ctx.guild is None:
            return True

        # Verificar se o servidor foi ativado
        is_activated = await is_server_activated(ctx.guild.id)
        if not is_activated:
            await send_err(ctx, 'Este servidor não está ativado!\n'
                                'Use `/ativar` para ativar o bot neste servidor.')
            return False

        return True

    async def _resolve_target(self, ctx: commands.Context, alvo: str):
        """Resolve `alvo` para `('user', User)` ou `('guild', Guild)` ou `(None, None)`.

        Aceita: @menção de usuário, ID de usuário ou ID de servidor. Sem `alvo`, usa o
        próprio autor. O usuário é sempre buscado via `fetch_user` (pra trazer o banner,
        que não vem em objetos de cache). Servidor só resolve se o bot estiver nele.
        """
        if alvo is None:
            oid = ctx.author.id
        else:
            m = _ID_RE.search(alvo)
            if not m:
                return None, None
            oid = int(m.group(1))

        # 1) Tenta como usuário (fetch traz avatar E banner completos).
        try:
            user = await self.bot.fetch_user(oid)
            return 'user', user
        except (discord.NotFound, discord.HTTPException):
            pass

        # 2) Tenta como servidor (só dá certo se o bot for membro do servidor).
        guild = self.bot.get_guild(oid)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(oid)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                guild = None
        if guild is not None:
            return 'guild', guild

        return None, None

    @commands.hybrid_command(
        name='avatar',
        description='Mostra o avatar de um usuário ou o ícone de um servidor'
    )
    @app_commands.describe(alvo='@menção/ID de usuário ou ID de servidor (vazio = você)')
    async def avatar(self, ctx: commands.Context, *, alvo: str = None):
        """Avatar de um usuário (@menção/ID) ou ícone de um servidor (ID). Sem alvo, mostra o seu."""
        kind, obj = await self._resolve_target(ctx, alvo)
        if kind is None:
            await send_err(ctx, "Não encontrei esse usuário/servidor. Use uma @menção, ID de "
                                "usuário ou ID de servidor (o bot precisa estar no servidor).")
            return

        if kind == 'user':
            embed = make_embed('info', title=f"Avatar de {obj.name}")
            embed.set_image(url=obj.display_avatar.url)
        else:  # guild
            if not obj.icon:
                await send_err(ctx, f"O servidor **{obj.name}** não tem ícone.")
                return
            embed = make_embed('info', title=f"Ícone de {obj.name}")
            embed.set_image(url=obj.icon.url)

        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name='banner',
        description='Mostra o banner de um usuário ou de um servidor'
    )
    @app_commands.describe(alvo='@menção/ID de usuário ou ID de servidor (vazio = você)')
    async def banner(self, ctx: commands.Context, *, alvo: str = None):
        """Banner de um usuário (@menção/ID) ou de um servidor (ID). Sem alvo, mostra o seu."""
        kind, obj = await self._resolve_target(ctx, alvo)
        if kind is None:
            await send_err(ctx, "Não encontrei esse usuário/servidor. Use uma @menção, ID de "
                                "usuário ou ID de servidor (o bot precisa estar no servidor).")
            return

        if not obj.banner:
            quem = f"O servidor **{obj.name}**" if kind == 'guild' else f"**{obj.name}**"
            await send_err(ctx, f"{quem} não tem banner.")
            return

        titulo = f"Banner do servidor {obj.name}" if kind == 'guild' else f"Banner de {obj.name}"
        embed = make_embed('info', title=titulo)
        embed.set_image(url=obj.banner.url)
        await ctx.send(embed=embed)


async def setup(bot):
    """Função obrigatória para carregar o cog"""
    await bot.add_cog(Avatar(bot))
