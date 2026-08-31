"""Threads de lootlog por evento (outbox — espelho de regear_threads).

Quando um evento entra em IN_PROGRESS o site marca `lootlog_thread_dirty`; este
loop puxa `/bot/events/{g}/lootlog-thread-work`, cria uma thread pública no canal
dedicado `lootlog_thread_channel_id` (header localizado) e chama
`/lootlog-thread-synced` gravando `Event.lootlog_thread_id`. .csv do lootlogger
postado na thread vira LootLogSubmission atrelado ao evento (ver cogs/lootlogs.py).

Eventos terminais com thread ativa são arquivados (lock) — best-effort.
"""
from __future__ import annotations

import asyncio
import os

import discord
from discord.ext import commands, tasks

import http_client
from cogs._discord_timeout import SKIP_EXC, dtimeout
from cogs.general import _guild_command_config, clear_unavailable_channel, guild_lang_for
from i18n import t

SITE_URL = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")


async def _get(path: str) -> dict | None:
    return await http_client.get_json(path, tag="lootlog_threads")


async def _post(path: str, body: dict) -> dict | None:
    return await http_client.post_json(
        path, body, tag="lootlog_threads", attempts=2,
    )


_cog_ref: "LootlogThreads | None" = None


class LootlogThreads(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._guild_locks: dict[int, asyncio.Lock] = {}
        self._thread_ids: dict[tuple[int, int], int] = {}

    async def cog_load(self) -> None:
        global _cog_ref
        _cog_ref = self
        print("[lootlog_threads] cog carregada — loop de criação de threads ativo")
        if not lootlog_thread_work_loop.is_running():
            lootlog_thread_work_loop.start(self)

    async def cog_unload(self) -> None:
        lootlog_thread_work_loop.cancel()

    # Mesmo motivo do regear_threads: SEM listener de on_ready próprio — o
    # catch-up roda em main.py depois de _wait_for_backend() (ver on_ready lá).

    async def sync_guild(self, guild: discord.Guild) -> None:
        lock = self._guild_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            await self._sync_guild_unlocked(guild)

    async def _sync_guild_unlocked(self, guild: discord.Guild) -> None:
        cfg = await _guild_command_config(guild.id)
        channel_id = cfg.get("lootlog_thread_channel_id")
        if not channel_id:
            return  # feature off (sem canal) ou backend fora — não logar por-tick
                    # (queda do backend já é logada 1× por http_client)
        try:
            cid = int(channel_id)
        except (TypeError, ValueError):
            return
        channel = guild.get_channel(cid)
        if channel is None:
            try:
                channel = await dtimeout(guild.fetch_channel(cid))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, asyncio.TimeoutError) as e:
                print(f"[lootlog_threads] canal {cid} inacessível em {guild.id}: "
                      f"{type(e).__name__}: {e}")
                if isinstance(e, discord.NotFound):
                    await clear_unavailable_channel(guild.id, "lootlog_thread_channel_id", cid)
                return
        if not isinstance(channel, discord.TextChannel):
            print(f"[lootlog_threads] canal {cid} não é de texto em {guild.id}")
            return
        lang = await guild_lang_for(guild.id)

        work = await _get(f"/bot/events/{guild.id}/lootlog-thread-work")
        if work is None:
            return  # backend fora/401 já logado 1× (http_client / tag) — próximo tick cobre

        create = work.get("create") or []
        if create:
            print(f"[lootlog_threads] {guild.id}: {len(create)} thread(s) pra criar no canal "
                  f"{channel.id} → {[ev.get('event_id') for ev in create]}")
        for ev in create:
            await self._create_thread(guild, channel, lang, ev)
        for ev in work.get("archive") or []:
            await self._archive_thread(guild, lang, ev)

    async def _create_thread(self, guild: discord.Guild, channel: discord.TextChannel,
                             lang: str, ev: dict) -> None:
        event_id = ev.get("event_id")
        title = ev.get("title") or ""
        if not event_id:
            return
        name = t(lang, "ev_lootlog_thread_title", n=event_id, title=title)[:100]
        key = (guild.id, int(event_id))
        thread = guild.get_thread(self._thread_ids.get(key, 0))
        if thread is None:
            thread = next((item for item in channel.threads if item.name == name), None)
        if thread is None:
            print(f"[lootlog_threads] criando thread '{name}' p/ evento {event_id} no canal {channel.id}")
            try:
                thread = await dtimeout(channel.create_thread(
                    name=name, type=discord.ChannelType.public_thread,
                ))
            except Exception as e:
                print(f"[lootlog_threads] falhou criar thread p/ evento {event_id} "
                      f"em {channel.id}: {type(e).__name__}: {e}")
                return
            print(f"[lootlog_threads] ✓ thread {thread.id} criada p/ evento {event_id}")
            # Embed com o botão '📤 Enviar log' (submissão anônima via modal FileUpload).
            from cogs.lootlogs import LootlogSubmitView
            embed = discord.Embed(
                title=t(lang, "ev_lootlog_thread_title", n=event_id, title=title),
                description=t(lang, "ev_lootlog_thread_header", n=event_id),
                color=0x2b2d31,
            )
            try:
                await dtimeout(thread.send(embed=embed, view=LootlogSubmitView(lang)))
            except SKIP_EXC as e:
                print(f"[lootlog_threads] falhou postar embed-botão na thread "
                      f"{thread.id}: {type(e).__name__}: {e}")
        self._thread_ids[key] = thread.id
        await _post(
            f"/bot/events/{guild.id}/{event_id}/lootlog-thread-synced",
            {"lootlog_thread_id": str(thread.id), "clear_dirty": True},
        )

    async def _archive_thread(self, guild: discord.Guild, lang: str, ev: dict) -> None:
        tid = ev.get("lootlog_thread_id")
        event_id = ev.get("event_id")
        if not tid or not event_id:
            return
        try:
            thread = guild.get_thread(int(tid))
            if thread is None:
                thread = await dtimeout(guild.fetch_channel(int(tid)))
        except (TypeError, ValueError, *SKIP_EXC):
            thread = None
        if thread is None:
            await _post(
                f"/bot/events/{guild.id}/{event_id}/lootlog-thread-archived", {})
            return
        try:
            await dtimeout(thread.edit(archived=True, locked=True))
        except SKIP_EXC:
            return
        await _post(
            f"/bot/events/{guild.id}/{event_id}/lootlog-thread-archived", {})


@tasks.loop(seconds=10)
async def lootlog_thread_work_loop(cog: "LootlogThreads") -> None:
    for guild in cog.bot.guilds:
        try:
            await cog.sync_guild(guild)
        except Exception as e:
            print(f"[lootlog_threads] erro no loop ({guild.id}): {type(e).__name__}: {e}")


@lootlog_thread_work_loop.before_loop
async def _before() -> None:
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@lootlog_thread_work_loop.error
async def _on_error(error: BaseException) -> None:
    import traceback
    print(f"[lootlog_threads] LOOP MORREU, reiniciando: {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if _cog_ref is not None:
        asyncio.get_running_loop().call_soon(lambda: lootlog_thread_work_loop.start(_cog_ref))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LootlogThreads(bot))
