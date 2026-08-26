"""Publica Juicy Kills configuradas no site como embeds no Discord."""
import asyncio
import io
import os
from datetime import datetime, timezone
from urllib.parse import quote

import discord
from discord.ext import commands, tasks

import http_client
from cogs._discord_timeout import SKIP_EXC, dtimeout
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
        async for message in channel.history(limit=200):
            kill_id = _message_kill_id(message) if message.author.id == bot_id else None
            if kill_id is not None:
                found.add(kill_id)
    except (discord.Forbidden, discord.HTTPException):
        pass
    return found


async def _history_last_kill_ts(channel, bot_id: int) -> datetime | None:
    """Lê o timestamp da kill mais recente já postada no canal (cross-restart
    dedup). Procura o footer marker (ziggs:juicy-kill:N) nas últimas 200
    mensagens e devolve o timestamp da mais nova. Usado no 1º poll após o bot
    subir, quando o watermark local (memória) está vazio."""
    try:
        async for message in channel.history(limit=200):
            if message.author.id != bot_id:
                continue
            if not message.embeds or not message.embeds[0].footer.text:
                continue
            # Footer: "SITE_URL · ziggs:juicy-kill:N [· API delay ...]"
            footer = message.embeds[0].footer.text
            _, sep, marker = footer.rpartition(JUICY_MARKER)
            if not sep or not marker.isdigit():
                continue
            # O timestamp do post ≈ timestamp da kill (postamos em ordem cronológica).
            # Usar message.created_at (UTC) como proxy do watermark é conservador:
            # mata tudo postado até aquele instante, evitando re-post no restart.
            return message.created_at or None
    except (discord.Forbidden, discord.HTTPException):
        pass
    return None


def _parse_ts(ts: str | None) -> datetime | None:
    """ISO string -> datetime aware UTC, ou None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


_cog_ref: "JuicyKills | None" = None


class JuicyKills(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._watermarks: dict[int, datetime] = {}
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
        async with lock:
            await self._sync_guild_unlocked(guild)

    async def _sync_guild_unlocked(self, guild: discord.Guild) -> None:
        cfg = await _guild_command_config(guild.id)
        channel_id = cfg.get("juicy_kill_channel_id")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            return

        data = await http_client.get_json(
            f"/bot/guilds/{guild.id}/juicy-kill/queue", tag="juicy_kills",
        )
        kills = (data or {}).get("kills") or []
        if not kills:
            return

        # Watermark local: mata kills já postadas (dedup cross-restart).
        # Backend já filtra por watermark, mas mantemos um local também pra
        # tolerar race (poll pegou kills, bot reinicia antes de ackar, próximo
        # poll rebusca as mesmas — o watermark local evita re-post duplo).
        # NÃO usamos _history_last_kill_ts (message.created_at) aqui porque o
        # horário do post no Discord é SEMPRE mais recente que o timestamp da
        # kill no jogo — isso bloquearia kills legítimas na fila do backend.
        # Dedup por ID: no 1º poll após restart, lê os IDs das últimas 200
        # mensagens do canal e ignora kills já postadas. Depois, o watermark
        # de timestamp (preenchido só após postar com sucesso) cuida do resto.
        wm = self._watermarks.get(guild.id)
        posted_ids = self._posted_ids.get(guild.id)
        if posted_ids is None:
            bot_user = getattr(self.bot, "user", None)
            bot_id = bot_user.id if bot_user else 0
            posted_ids = await _history_kill_ids(channel, bot_id)
            self._posted_ids[guild.id] = posted_ids
        last_ts = None
        for kill in kills:
            ts = _parse_ts(kill.get("timestamp"))
            if ts is None:
                continue
            if wm is not None and ts <= wm:
                continue
            if kill["id"] in posted_ids:
                continue
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
            last_ts = ts
            self._watermarks[guild.id] = ts
            self._posted_ids.setdefault(guild.id, set()).add(kill["id"])

        if last_ts is not None:
            await http_client.post_json(
                f"/bot/guilds/{guild.id}/juicy-kill/synced",
                {"last_ts": last_ts.isoformat()}, tag="juicy_kills", attempts=2,
                queue_on_failure=True,
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
