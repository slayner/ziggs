"""Publica Juicy Kills configuradas no site como embeds no Discord."""
import asyncio
import io
import os
from urllib.parse import quote

import discord
from discord.ext import commands, tasks

import http_client
from cogs.general import _guild_command_config

SITE_URL = os.getenv("BOT_SITE_URL", "").rstrip("/")
PUBLIC_URL = os.getenv("BOT_PUBLIC_URL", "").rstrip("/") or SITE_URL
REGION_PREFIX = {"americas": "am", "asia": "as", "europe": "eu"}
JUICY_MARKER = "ziggs:juicy-kill:"


def _profile_url(region: str, name: str, event_id: str | None = None) -> str:
    prefix = REGION_PREFIX.get(region, "am")
    url = f"{PUBLIC_URL}/{prefix}/{quote(name, safe='')}"
    return f"{url}?activity={quote(str(event_id), safe='')}" if event_id else url


def _link(region: str, player: dict, event_id: str | None = None) -> str:
    name = player.get("name") or "Unknown"
    return f"[{discord.utils.escape_markdown(name)}]({_profile_url(region, name, event_id)})"



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
    embed.set_footer(text=f"{PUBLIC_URL} · {JUICY_MARKER}{kill['id']}")
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
        async for message in channel.history(limit=200):
            kill_id = _message_kill_id(message) if message.author.id == bot_id else None
            if kill_id is not None:
                found.add(kill_id)
    except (discord.Forbidden, discord.HTTPException):
        pass
    return found


_cog_ref: "JuicyKills | None" = None


class JuicyKills(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._sent: dict[int, int] = {}
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
        async with lock:
            cfg = await _guild_command_config(guild.id)
            channel_id = cfg.get("juicy_kill_channel_id")
            channel = guild.get_channel(int(channel_id)) if channel_id else None
            if channel is None:
                return
            data = await http_client.get_json(
                f"/bot/guilds/{guild.id}/juicy-kill/queue", tag="juicy_kills",
            )
            kills = (data or {}).get("kills") or []
            history_ids = await _history_kill_ids(channel, guild.me.id if guild.me else 0)
            last_id = None
            for kill in kills:
                if kill["id"] <= self._sent.get(guild.id, 0) or kill["id"] in history_ids:
                    last_id = kill["id"]
                    self._sent[guild.id] = last_id
                    continue
                image = await http_client.get_bytes(
                    f"/bot/guilds/{guild.id}/juicy-kill/{kill['id']}/image",
                    timeout=30, tag="juicy_kills",
                )
                if image is None or len(image) > guild.filesize_limit:
                    break
                filename = f"juicy-kill-{kill['id']}.png"
                try:
                    await channel.send(
                        embed=_build_embed(kill, filename),
                        file=discord.File(io.BytesIO(image), filename=filename),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    break
                last_id = kill["id"]
                self._sent[guild.id] = last_id

            if last_id is not None:
                await http_client.post_json(
                    f"/bot/guilds/{guild.id}/juicy-kill/synced",
                    {"last_id": last_id}, tag="juicy_kills", attempts=2,
                    queue_on_failure=False,
                )


@tasks.loop(seconds=30)
async def juicy_kills_loop(cog: JuicyKills) -> None:
    await asyncio.gather(*(cog.sync_guild(g) for g in cog.bot.guilds), return_exceptions=True)


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
