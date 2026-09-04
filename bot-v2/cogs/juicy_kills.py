"""Publica Juicy Kills configuradas no site como embeds no Discord."""
import asyncio
import io
import os
from urllib.parse import quote

import discord
from discord.ext import commands, tasks

import http_client
from cogs._discord_timeout import SKIP_EXC, dtimeout
from cogs.general import _guild_command_config, clear_unavailable_channel

SITE_URL = os.getenv("BOT_SITE_URL", "").rstrip("/")
PUBLIC_URL = os.getenv("BOT_PUBLIC_URL", "").rstrip("/") or SITE_URL
REGION_PREFIX = {"americas": "am", "asia": "as", "europe": "eu"}
JUICY_MARKER = "ziggs:juicy-kill:"
JUICY_SYNC_CONCURRENCY = 3


def _profile_url(region: str, name: str, event_id: str | None = None) -> str:
    prefix = REGION_PREFIX.get(region, "am")
    url = f"{PUBLIC_URL}/{prefix}/{quote(name, safe='')}"
    return f"{url}?activity={quote(str(event_id), safe='')}" if event_id else url


def _link(region: str, player: dict, event_id: str | None = None) -> str:
    name = player.get("name") or "Unknown"
    return f"[{discord.utils.escape_markdown(name)}]({_profile_url(region, name, event_id)})"



def _fmt_delay(secs: float | None) -> str | None:
    if not secs or secs <= 0:
        return None
    mins = int(secs // 60)
    if mins < 1:
        return f"{int(secs)}s"
    if mins < 60:
        return f"{mins}min"
    return f"{mins // 60}h{mins % 60}min"


def _build_embed(kill: dict, filename: str) -> discord.Embed:
    killer = kill.get("killer") or {}
    victim = kill.get("victim") or {}
    event_id = str(kill["albion_event_id"])
    region = kill["region"]
    header = f"## {_link(region, killer, event_id)} killed {_link(region, victim, event_id)}"
    lines = [header]

    participants = []
    seen = {(killer.get("name") or "").casefold(), (victim.get("name") or "").casefold()}
    for player in kill.get("participants") or []:
        name = player.get("name") or ""
        if name and name.casefold() not in seen:
            participants.append(_link(region, player))
            seen.add(name.casefold())
    if participants:
        prefix = "**Participants:** "
        shown = []
        for participant in participants:
            candidate = prefix + ", ".join([*shown, participant])
            if len(header) + len(candidate) + 2 > 4096:
                break
            shown.append(participant)
        if shown:
            participant_line = prefix + ", ".join(shown)
            if len(shown) < len(participants) and len(header) + len(participant_line) + 5 <= 4096:
                participant_line += ", …"
            lines.append(participant_line)
    embed = discord.Embed(
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    embed.set_image(url=f"attachment://{filename}")

    footer_parts = [PUBLIC_URL, f"{JUICY_MARKER}{kill['id']}"]
    delay_secs = kill.get("api_delay_secs")
    if delay_secs and delay_secs > 1800:
        delay_str = _fmt_delay(delay_secs)
        if delay_str:
            footer_parts.append(f"API delay {delay_str}")
    embed.set_footer(text=" · ".join(footer_parts))
    return embed


def _message_kill_id(message) -> int | None:
    if not message.embeds or not message.embeds[0].footer.text:
        return None
    _, separator, marker = message.embeds[0].footer.text.rpartition(JUICY_MARKER)
    if not separator:
        return None
    return int(marker) if marker.isdigit() else None


async def _history_kill_ids(channel, bot_id: int) -> set[int]:
    found = set()
    try:
        history = channel.history(limit=200)
        while True:
            message = await dtimeout(anext(history))
            kill_id = _message_kill_id(message) if message.author.id == bot_id else None
            if kill_id is not None:
                found.add(kill_id)
    except StopAsyncIteration:
        pass
    except SKIP_EXC:
        pass
    return found


_cog_ref: "JuicyKills | None" = None


class JuicyKills(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._posted_ids: dict[int, set[int]] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        global _cog_ref
        _cog_ref = self
        if not juicy_kills_loop.is_running():
            juicy_kills_loop.start(self)

    async def cog_unload(self) -> None:
        juicy_kills_loop.cancel()

    async def sync_guild(self, guild: discord.Guild) -> None:
        lock = self._locks.setdefault(guild.id, asyncio.Lock())
        try:
            async with asyncio.timeout(30):
                async with lock:
                    await self._sync_guild_unlocked(guild)
        except (TimeoutError, asyncio.TimeoutError):
            print(f"[juicy_kills] sync de {guild.id} passou de 30s; próximo tick tenta de novo")

    async def _sync_guild_unlocked(self, guild: discord.Guild) -> None:
        cfg = await _guild_command_config(guild.id)
        channel_id = cfg.get("juicy_kill_channel_id")
        if not channel_id:
            return
        try:
            cid = int(channel_id)
        except (TypeError, ValueError):
            return
        channel = guild.get_channel(cid)
        if channel is None:
            try:
                channel = await dtimeout(guild.fetch_channel(cid))
            except SKIP_EXC as e:
                print(f"[juicy_kills] canal {cid} inacessível em {guild.id}: {type(e).__name__}: {e}")
                if isinstance(e, discord.NotFound):
                    await clear_unavailable_channel(guild.id, "juicy_kill_channel_id", cid)
                return
        data = await http_client.get_json(
            f"/bot/guilds/{guild.id}/juicy-kill/queue", tag="juicy_kills",
        )
        kills = (data or {}).get("kills") or []
        if not kills:
            return

        # A outbox só é confirmada após o Discord aceitar a mensagem. No restart,
        # os markers recentes evitam repetir um post cujo ACK tenha falhado.
        posted_ids = self._posted_ids.get(guild.id)
        if posted_ids is None:
            bot_user = getattr(self.bot, "user", None)
            bot_id = bot_user.id if bot_user else 0
            posted_ids = await _history_kill_ids(channel, bot_id)
            self._posted_ids[guild.id] = posted_ids
        acknowledged_ids = []
        for kill in kills:
            if kill["id"] in posted_ids:
                # A mensagem já foi enviada antes de uma falha no ACK. Reconhece
                # agora sem duplicá-la no Discord.
                pass
            else:
                image = await http_client.get_bytes(
                    f"/bot/guilds/{guild.id}/juicy-kill/{kill['id']}/image",
                    timeout=30, tag="juicy_kills",
                )
                if image is None or len(image) > guild.filesize_limit:
                    break
                filename = f"juicy-kill-{kill['id']}.png"
                try:
                    await dtimeout(channel.send(
                        embed=_build_embed(kill, filename),
                        file=discord.File(io.BytesIO(image), filename=filename),
                    ))
                except SKIP_EXC:
                    break
                posted_ids.add(kill["id"])
            acknowledged_ids.append(kill["id"])

        if acknowledged_ids:
            acknowledged = await http_client.post_json(
                f"/bot/guilds/{guild.id}/juicy-kill/synced",
                {"kill_ids": acknowledged_ids}, tag="juicy_kills", attempts=2,
                queue_on_failure=True,
            )


@tasks.loop(seconds=30)
async def juicy_kills_loop(cog: JuicyKills) -> None:
    semaphore = asyncio.Semaphore(JUICY_SYNC_CONCURRENCY)

    async def sync_limited(guild: discord.Guild) -> None:
        async with semaphore:
            await cog.sync_guild(guild)

    await asyncio.gather(*(sync_limited(g) for g in cog.bot.guilds), return_exceptions=True)


@juicy_kills_loop.before_loop
async def _before() -> None:
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@juicy_kills_loop.error
async def _on_error(error: BaseException) -> None:
    import traceback
    print(f"[juicy_kills] loop morreu, reiniciando: {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if _cog_ref is not None:
        asyncio.get_running_loop().call_soon(lambda: juicy_kills_loop.start(_cog_ref))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JuicyKills(bot))
