import os
import sys

# Força stdout/stderr a usar UTF-8 (Windows default é cp1252, que não suporta ✓ ✗ → etc.)
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import asyncio
import discord
from discord.ext import commands
from discord.state import ConnectionState
from dotenv import load_dotenv

# Carregar variáveis de ambiente ANTES de importar database — ele lê BOT_DB_DIR
# no momento do import, então o .env precisa já estar carregado aqui.
load_dotenv()

import aiohttp
import database
from database import init_database, activate_server
import utils

TOKEN = os.getenv('DISCORD_TOKEN')
_SITE_URL    = os.getenv('BOT_SITE_URL', '').rstrip('/')
_API_SECRET  = os.getenv('BOT_API_SECRET', '')

async def _heartbeat(guild: discord.Guild):
    """Notifica o site que o bot está presente neste servidor."""
    if not _SITE_URL or not _API_SECRET:
        return
    url = f'{_SITE_URL}/bot/heartbeat/{guild.id}'
    body = {'guild_name': guild.name, 'guild_icon': guild.icon.key if guild.icon else None}
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(url, json=body, headers={'Authorization': f'Bearer {_API_SECRET}'}, timeout=aiohttp.ClientTimeout(total=5))
    except Exception:
        pass  # best-effort

# Configurar intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# ====================================================================
# MULTI-TENANT: o contexto de servidor (ContextVar) é ligado em cada ponto de
# entrada e o banco é resolvido a partir dele.
#   1) parse_interaction_create (monkeypatch) -> TODA interação: slash, híbrido
#      por slash, autocomplete, botões, selects e modais. Roda no task do gateway
#      ANTES de despachar os handlers (criados via create_task, que copiam o
#      contexto atual), então o servidor propaga pra todos eles.
#   2) before_invoke -> comandos de texto/prefixo.
# Listeners de gateway (on_member_*, on_message...) e loops setam o contexto
# explicitamente (cada um sabe seu guild).
#
# IMPORTANTE: o patch PRECISA vir ANTES de criar o bot. O ConnectionState monta
# self.parsers (via inspect.getmembers) no __init__, capturando uma referência
# BOUND ao parse_interaction_create. Se patchássemos DEPOIS de criar o bot, o
# gateway seguiria chamando o método ANTIGO e as interações (autocomplete, botões,
# etc.) ficariam SEM servidor no contexto.
# ====================================================================
_orig_parse_interaction_create = ConnectionState.parse_interaction_create
def _parse_interaction_create(self, data):
    try:
        gid = data.get('guild_id')
        database.set_current_guild(int(gid) if gid else None)
    except Exception:
        database.set_current_guild(None)
    return _orig_parse_interaction_create(self, data)
ConnectionState.parse_interaction_create = _parse_interaction_create

# Criar bot (DEPOIS do patch — pro ConnectionState capturar o parser já patchado).
# case_insensitive=True: comandos de prefixo aceitam qualquer caixa (!BAL == !bal).
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None,
                   case_insensitive=True)

@bot.before_invoke
async def _bind_guild_before_invoke(ctx):
    database.set_current_guild(ctx.guild.id if ctx.guild else None)


# Carregar cogs
async def load_cogs():
    """Carrega todos os cogs da pasta cogs"""
    cogs_path = os.path.join(os.path.dirname(__file__), 'cogs')
    for filename in os.listdir(cogs_path):
        if filename.endswith('.py') and filename != '__init__.py':
            await bot.load_extension(f'cogs.{filename[:-3]}')
            print(f'✓ Cog carregada: {filename}')

@bot.event
async def on_ready():
    """Evento acionado quando o bot está pronto"""
    print(f'✓ Bot conectado como {bot.user}')
    for guild in bot.guilds:
        try:
            await activate_server(guild.id)
            await _heartbeat(guild)
        except Exception as e:
            print(f'✗ auto-ativação {guild.id}: {e}')
    print(f'✓ {len(bot.guilds)} servidor(es) ativado(s)')
    print(f'✓ Sincronizando comandos...')
    try:
        synced = await bot.tree.sync()
        print(f'✓ {len(synced)} comando(s) sincronizado(s)')
    except Exception as e:
        print(f'✗ Erro ao sincronizar comandos: {e}')

@bot.event
async def on_guild_join(guild: discord.Guild):
    """Auto-ativa e registra no site quando o bot entra em um novo servidor."""
    try:
        await activate_server(guild.id)
        await _heartbeat(guild)
        print(f'✓ Servidor ativado: {guild.name} ({guild.id})')
    except Exception as e:
        print(f'✗ Falha ao ativar {guild.id}: {e}')


@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Global hook: marca interações para cancelar deleção de ephemerals agendados.
    Chamamos `mark_ephemeral_interacted` para qualquer interação que esteja ligada
    a uma mensagem — isso impede que o helper apague ephemerals que foram usados.
    """
    try:
        utils.mark_ephemeral_interacted(interaction)
    except Exception:
        pass

@bot.event
async def on_command_error(ctx, error):
    """Tratador de erros para comandos"""
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send('❌ Argumentos insuficientes.')
    elif isinstance(error, commands.CommandNotFound):
        ...
    else:
        await ctx.send(f'❌ Erro: {str(error)}')
        print(f'Erro não tratado: {error}')

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """Tratador de erros para comandos slash"""
    # Resposta SEGURA: a interação pode já ter expirado (10062) ou sido respondida.
    async def _safe(msg: str):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
                try:
                    await utils.schedule_ephemeral_from_ctx(interaction)
                except Exception:
                    pass
            else:
                await interaction.response.send_message(msg, ephemeral=True)
                try:
                    await utils.schedule_ephemeral_from_ctx(interaction)
                except Exception:
                    pass
        except discord.HTTPException:
            pass  # interação expirada/inacessível — nada a fazer

    # Erros padrão
    if isinstance(error, discord.app_commands.MissingPermissions):
        await _safe('❌ Você não tem permissão para usar este comando.')
    elif isinstance(error, discord.app_commands.BotMissingPermissions):
        await _safe('❌ Não tenho permissão para executar esta ação.')
    else:
        await _safe(f'❌ Erro: {str(error)}')
        print(f'Erro no comando slash: {error}')

async def main():
    """Função principal"""
    # Inicializar banco de dados antes de carregar os cogs
    print(f'✓ Inicializando banco de dados...')
    await init_database()
    print(f'✓ Banco de dados inicializado')

    async with bot:
        await load_cogs()
        await bot.start(TOKEN)

if __name__ == '__main__':
    asyncio.run(main())
