"""Battle feed — mensageiro de batalhas.

Polla o backend por batalhas novas que casam com os filtros da guilda (canal
configurado + mínimo de jogadores) e posta o link no canal definido pelo admin
no site (GuildConfig → Battle Feed).

O link é a própria URL pública da batalha (/{public_id}). O serving da SPA
injeta nela as OG tags com a imagem de resumo, então qualquer pessoa colando
o mesmo link recebe o embed — não só o bot. Nenhum texto além do link é enviado.

Checkpoint por timestamp (battle_feed_last_ts), não por id interno — uma
batalha descoberta tardiamente (sweeper, backfill) tem id maior mas start_time
menor; no cursor por id seria postada fora de ordem cronológica. Por
start_time, posta em ordem do jogo.

Mesma estrutura de cogs/audit_log.py: @tasks.loop, _cog_ref global, before_loop
espera wait_until_ready, .error auto-reinicia via call_soon."""
import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

import http_client
from cogs._discord_timeout import SKIP_EXC, dtimeout
from cogs.general import _guild_command_config

SITE_URL = os.getenv("BOT_SITE_URL", "").rstrip("/")
PUBLIC_URL = os.getenv("BOT_PUBLIC_URL", "").rstrip("/") or SITE_URL


async def _get(path: str) -> Optional[dict]:
    return await http_client.get_json(path)


async def _post(path: str, body: dict) -> Optional[dict]:
    return await http_client.post_json(path, body, tag="battle_feed", attempts=2)


def _parse_ts(ts: str | None) -> datetime | None:
    """ISO string -> datetime aware UTC, ou None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


async def _history_posted_ids(channel, bot_id: int) -> set[str]:
    """Lê os public_ids já postados no canal (cross-restart dedup). O bot posta
    só o link (PUBLIC_URL/{public_id}), então extrai o public_id do conteúdo
    das últimas mensagens dele mesmo. Usado no 1º poll após restart, quando o
    watermark local (memória) está vazio — sem isso, batalhas postadas mas não
    ackadas (bot caiu antes de chamar /synced) seriam re-postadas."""
    posted: set[str] = set()
    try:
        history = channel.history(limit=200)
        while True:
            message = await dtimeout(anext(history))
            if message.author.id != bot_id or not message.content:
                continue
            # Link: https://ziggs.example/{public_id} — pega o último segmento.
            content = message.content.strip()
            for token in content.split():
                if "/" in token and not token.startswith("<"):
                    pid = token.rstrip("/").rsplit("/", 1)[-1]
                    if pid and pid != token:
                        posted.add(pid)
    except StopAsyncIteration:
        pass
    except SKIP_EXC:
        pass
    return posted


_cog_ref: "BattleFeed | None" = None


class BattleFeed(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._watermarks: dict[int, datetime] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        global _cog_ref
        _cog_ref = self
        print("[battle_feed] cog carregada — loop de battle feed ativo")
        if not battle_feed_loop.is_running():
            battle_feed_loop.start(self)

    async def cog_unload(self) -> None:
        battle_feed_loop.cancel()

    async def sync_guild(self, guild: discord.Guild) -> None:
        lock = self._locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            await self._sync_guild_unlocked(guild)

    async def _sync_guild_unlocked(self, guild: discord.Guild) -> None:
        cfg = await _guild_command_config(guild.id)
        channel_id = cfg.get("battle_feed_channel_id")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            return

        data = await _get(f"/bot/guilds/{guild.id}/battle-feed")
        if data is None:
            return  # site fora do ar — próximo tick cobre
        battles = (data.get("battles") or [])

        # Watermark local: mata batalhas já postadas (dedup cross-restart).
        wm = self._watermarks.get(guild.id)
        # 1º poll após restart: watermark local vazio. Lê o histórico do canal
        # pra descobrir quais public_ids já foram postados e pular eles — sem
        # isso, batalhas postadas mas não ackadas (bot caiu antes de /synced)
        # seriam re-postadas no próximo ciclo.
        posted_ids: set[str] | None = None
        if wm is None:
            bot_user = getattr(self.bot, "user", None)
            bot_id = bot_user.id if bot_user else 0
            posted_ids = await _history_posted_ids(channel, bot_id)
        last_ts = None
        for b in battles:
            ts = _parse_ts(b.get("start_time"))
            if ts is None:
                continue
            if wm is not None and ts <= wm:
                continue
            if posted_ids is not None and b.get("public_id") in posted_ids:
                # Já postada mas ainda sem ACK (o processo caiu ou o backend
                # estava indisponível). Inclui no checkpoint para reconhecê-la.
                last_ts = ts
                continue
            link = f"{PUBLIC_URL}/{b['public_id']}"
            try:
                await dtimeout(channel.send(content=link))
            except SKIP_EXC:
                break  # para no primeiro erro — ack só até o último enviado
            last_ts = ts

        if last_ts is not None:
            acknowledged = await _post(
                f"/bot/guilds/{guild.id}/battle-feed-synced", {"last_ts": last_ts.isoformat()},
            )
            if acknowledged is not None:
                self._watermarks[guild.id] = last_ts


@tasks.loop(seconds=30)
async def battle_feed_loop(cog: BattleFeed) -> None:
    # ponytail: paralelo como event_work_loop — com muitas guildas o sequencial
    # soma latência à toa (30s de ciclo, mas N guildas em sequência pode passar).
    await asyncio.gather(
        *(cog.sync_guild(g) for g in cog.bot.guilds),
        return_exceptions=True,
    )


@battle_feed_loop.before_loop
async def _before() -> None:
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@battle_feed_loop.error
async def _on_error(error: BaseException) -> None:
    import traceback
    print(f"[battle_feed] LOOP MORREU, reiniciando: {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if _cog_ref is not None:
        asyncio.get_running_loop().call_soon(lambda: battle_feed_loop.start(_cog_ref))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BattleFeed(bot))
