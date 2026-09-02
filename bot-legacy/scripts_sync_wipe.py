"""One-off: limpa os fantasmas globais da aplicação do bot antigo.

O dry-run sincronizou comandos no escopo de GUILD, mas os comandos GLOBAIS
da última sync da instância Discloud (com /register, /cta, etc.) continuam
registrados no Discord. Sync global é SUBSTITUIÇÃO total do conjunto global
— empurrando as 11 atuais, os fantasmas somem.

Via REST (bot.login, sem gateway) pra não criar 2ª sessão — o bot do dry-run
segue rodando. Uso: .venv\\Scripts\\python.exe scripts_sync_wipe.py
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import asyncio
from dotenv import load_dotenv
load_dotenv()

import discord
from discord.ext import commands

intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)


async def main():
    await bot.login(os.getenv('DISCORD_TOKEN'))
    cogs_path = os.path.join(os.path.dirname(__file__), 'cogs')
    for fn in sorted(os.listdir(cogs_path)):
        if fn.endswith('.py') and fn != '__init__.py':
            await bot.load_extension(f'cogs.{fn[:-3]}')
            print(f'loaded: {fn}')

    # 1) Zera o conjunto de GUILD se SYNC_GUILD_ID estiver setado (o dry-run
    #    copiou comandos pra guild — árvore nova não tem, o sync limpa).
    sgid = os.getenv('SYNC_GUILD_ID')
    if sgid:
        n_guild = len(await bot.tree.sync(guild=discord.Object(id=int(sgid))))
        print(f'guild [{sgid}]: {n_guild} (esperado 0)')
    # 2) Substitui o conjunto GLOBAL — apaga os fantasmas da Discloud.
    n_global = len(await bot.tree.sync())
    print(f'global: {n_global} comandos')
    await bot.close()


try:
    asyncio.run(main())
    code = 0
except Exception:
    import traceback
    traceback.print_exc()
    code = 1
sys.stdout.flush()
sys.stderr.flush()
os._exit(code)
