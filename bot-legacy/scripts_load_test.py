"""F2 check: bot carrega sem conectar no gateway.

Replica o main.py até o load_cogs — NÃO chama bot.start() pra não criar uma
2ª conexão com o mesmo token (a instância Discloud ainda está de pé).
Uso: .venv\\Scripts\\python.exe scripts_load_test.py
"""
import os
import sys

# Igual ao main.py: Windows usa cp1252 e os prints "✓/✗" explodem sem isso.
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import asyncio
import traceback
from discord.ext import commands
from discord.state import ConnectionState

from dotenv import load_dotenv
load_dotenv()

import database
from database import init_database

_orig = ConnectionState.parse_interaction_create
def _patched(self, data):
    try:
        gid = data.get('guild_id')
        database.set_current_guild(int(gid) if gid else None)
    except Exception:
        database.set_current_guild(None)
    return _orig(self, data)
ConnectionState.parse_interaction_create = _patched

import discord
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None,
                   case_insensitive=True)

async def main():
    await init_database()
    print('db ok')
    cogs_path = os.path.join(os.path.dirname(__file__), 'cogs')
    for fn in sorted(os.listdir(cogs_path)):
        if fn.endswith('.py') and fn != '__init__.py':
            await bot.load_extension(f'cogs.{fn[:-3]}')
            print(f'loaded: {fn}')
    cmds = sorted(c.qualified_name for c in bot.tree.get_commands())
    print(f'{len(cmds)} slash commands:', ', '.join(cmds))
    await bot.close()

try:
    asyncio.run(main())
    code = 0
except Exception:
    traceback.print_exc()
    code = 1
# Threads não-daemon (aiosqlite) seguram o exit do interpretador — fora na força.
sys.stdout.flush()
sys.stderr.flush()
os._exit(code)
