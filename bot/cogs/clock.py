import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timezone
import os
from dotenv import load_dotenv
import database
from database import (
    save_utc_clock, load_utc_clock, delete_utc_clock,
    is_server_activated, get_activated_guild_ids,
)
from utils import send_ok, send_err, send_warn, send_info

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

class Clock(commands.Cog):
    """Cog com comandos de relógio UTC"""
    
    def __init__(self, bot):
        self.bot = bot
        self.update_utc_clock.start()
    
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
    
    def cog_unload(self):
        """Descarrega o cog e para o background task"""
        self.update_utc_clock.cancel()
    
    @tasks.loop(minutes=10)
    async def update_utc_clock(self):
        """Atualiza o nome da categoria com a hora UTC — por servidor (multi-tenant).

        TUDO é embrulhado em try/except: uma falha transitória (ex.: listar os
        servidores) NÃO pode escapar, senão o tasks.loop morre e o relógio para
        de vez até reiniciar o bot. (Era esse o bug do 'relógio parou e não voltou'.)
        """
        try:
            utc_time = datetime.now(timezone.utc).strftime('%H:%M')
            try:
                gids = await get_activated_guild_ids()
            except Exception as e:
                print(f'✗ clock: erro listando servidores: {e}')
                return
            for gid in gids:
                try:
                    with database.using_guild(gid):
                        cid, base = await load_utc_clock()
                except Exception:
                    continue
                if not cid or not base:
                    continue
                category = self.bot.get_channel(cid)
                if not isinstance(category, discord.CategoryChannel):
                    continue
                try:
                    await category.edit(name=f'{base} ({utc_time} UTC)', reason='Relógio UTC')
                except discord.Forbidden:
                    print(f'✗ clock: sem permissão p/ renomear a categoria [{gid}]')
                except discord.HTTPException as e:
                    # inclui rate limit/erros de API — só loga, NÃO derruba o loop
                    print(f'✗ clock: erro renomeando a categoria [{gid}]: {e}')
        except Exception as e:
            print(f'✗ clock: erro inesperado no loop (ignorado p/ não morrer): {e}')

    @update_utc_clock.error
    async def _on_clock_error(self, exc: Exception):
        """Rede de segurança: se MESMO ASSIM o loop morrer, reinicia."""
        print(f'✗ clock: loop caiu ({exc!r}) — reiniciando…')
        try:
            self.update_utc_clock.restart()
        except Exception as e:
            print(f'✗ clock: falha ao reiniciar o loop: {e}')

    @update_utc_clock.before_loop
    async def before_update_utc_clock(self):
        """Espera o bot estar pronto antes de iniciar a task"""
        await self.bot.wait_until_ready()

    async def publish_utc(self, category: discord.CategoryChannel) -> bool:
        """Configura a categoria como relógio UTC e renomeia na hora. Chamado pelo
        /setup → Relógio UTC. Retorna True se deu certo."""
        if not isinstance(category, discord.CategoryChannel):
            return False
        base = category.name
        await save_utc_clock(category.id, base)
        utc_time = datetime.now(timezone.utc).strftime('%H:%M')
        try:
            await category.edit(name=f'{base} ({utc_time} UTC)', reason='Relógio UTC')
        except discord.HTTPException as e:
            print(f'✗ clock: erro renomeando no setup: {e}')
            return False
        if not self.update_utc_clock.is_running():
            self.update_utc_clock.start()
        return True
    
    @commands.hybrid_command(
        name='stoputc',
        description='Para o relógio UTC (apenas owner)'
    )
    async def stoputc(self, ctx: commands.Context):
        """
        Para a atualização do relógio UTC
        """
        # Verificar se é o owner
        if ctx.author.id != OWNER_ID:
            await send_err(ctx, 'Apenas o owner pode usar este comando.')
            return
        
        category_id, category_name_base = await load_utc_clock()
        if category_id is None:
            await send_err(ctx, 'Nenhuma categoria está sendo monitorada.')
            return

        # Restaurar nome original
        try:
            category = self.bot.get_channel(category_id)
            if category and category_name_base:
                await category.edit(name=category_name_base)
        except Exception as e:
            print(f"✗ clock: erro restaurando nome da categoria: {e}")

        # Deletar do banco de dados (do servidor atual)
        await delete_utc_clock()

        await send_ok(ctx, 'Relógio UTC parado e configuração removida.')
        print('✓ Relógio UTC parado')
        print('✓ Configuração removida do banco de dados')

async def setup(bot):
    """Função obrigatória para carregar o cog"""
    await bot.add_cog(Clock(bot))
