import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import asyncio, os, time
import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN      = os.getenv("DISCORD_TOKEN", "")
SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


async def _post(path: str, body: dict | None = None) -> None:
    """Envia uma requisição POST ao site (best-effort)."""
    if not SITE_URL or not API_SECRET:
        return
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(
                f"{SITE_URL}{path}",
                json=body or {},
                headers={"Authorization": f"Bearer {API_SECRET}"},
                timeout=aiohttp.ClientTimeout(total=5),
            )
    except Exception:
        pass


async def heartbeat(guild: discord.Guild) -> None:
    body = {
        "guild_name": guild.name,
        "guild_icon": guild.icon.key if guild.icon else None,
    }
    await _post(f"/bot/heartbeat/{guild.id}", body)


@tasks.loop(minutes=5)
async def heartbeat_loop() -> None:
    for guild in bot.guilds:
        await heartbeat(guild)


@bot.event
async def on_ready() -> None:
    print(f"✓ {bot.user} — {len(bot.guilds)} servidor(es)")
    for guild in bot.guilds:
        await heartbeat(guild)
    if not heartbeat_loop.is_running():
        heartbeat_loop.start()
    try:
        synced = await bot.tree.sync()
        print(f"✓ {len(synced)} comando(s) sincronizado(s)")
    except Exception as e:
        print(f"✗ sync: {e}")


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    await heartbeat(guild)
    print(f"✓ Entrei em: {guild.name} ({guild.id})")


@bot.event
async def on_guild_remove(guild: discord.Guild) -> None:
    await _post(f"/bot/goodbye/{guild.id}")
    print(f"✗ Saí de: {guild.name} ({guild.id})")


async def main() -> None:
    async with bot:
        await bot.load_extension("cogs.general")
        await bot.start(TOKEN)


asyncio.run(main())
