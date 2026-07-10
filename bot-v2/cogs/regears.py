"""Regears por screenshot: lê imagens postadas no canal de regear da guilda e
envia ao backend para reconhecimento (OCR → API Albion → itens + preço sugerido).

O bot NÃO faz OCR nem valuation — só repassa a imagem ao backend
(`POST /guilds/{g}/regear/ingest`, auth Bearer BOT_API_SECRET) e reage na mensagem.
Logística revisa e aprova no site (RegearPage). Idempotente por msg_id (o backend
deduplica; aqui também cacheamos pra não re-baixar o mesmo attachment em edits)."""
import os
import time

import aiohttp
import discord
from discord.ext import commands

import http_client
from cogs.general import _guild_command_config, guild_lang_for
from i18n import t

SITE_URL = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")
# Link clicável no Discord (frontend) — ver mesmo comment em cogs/events.py.
PUBLIC_URL = os.getenv("BOT_PUBLIC_URL", "").rstrip("/") or SITE_URL

_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
# cache: guild_id -> (expires, {channel_ids}). Settings muda raramente. Uma
# guilda pode ter vários canais de regear, cada um com sua própria % de
# cobertura (aplicada no backend, na ingestão — o bot só precisa saber QUAIS
# canais assistir).
_channel_cache: dict[int, tuple[float, set[int]]] = {}
_CHANNEL_TTL = 120.0
# cache de msg_id já processados (1h) — evita reprocessar edits.
_done_msgs: dict[int, float] = {}
_DONE_TTL = 3600.0


async def _regear_channels(guild_id: int) -> set[int]:
    now = time.monotonic()
    cached = _channel_cache.get(guild_id)
    if cached and cached[0] > now:
        return cached[1]
    channels: set[int] = set()
    if SITE_URL and API_SECRET:
        data = await http_client.get_json(f"/bot/guilds/{guild_id}/regear/settings")
        if data:
            channels = {int(c["channel_id"]) for c in (data.get("channels") or []) if c.get("channel_id")}
    _channel_cache[guild_id] = (now + _CHANNEL_TTL, channels)
    return channels


async def _regear_thread_channel_id(guild_id: int) -> int | None:
    """Canal dedicado onde o bot cria threads de regear por evento. Vem do
    /bot/guild-commands (setting top-level da guilda), não do regear/settings."""
    cfg = await _guild_command_config(guild_id)
    cid = cfg.get("regear_thread_channel_id")
    try:
        return int(cid) if cid else None
    except (TypeError, ValueError):
        return None


def _prune_done() -> None:
    now = time.monotonic()
    stale = [k for k, v in _done_msgs.items() if v < now]
    for k in stale:
        _done_msgs.pop(k, None)


class Regears(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Só mensagens em guild, de humano, com anexo de imagem, no canal de regear.
        if message.guild is None or message.author.bot:
            return
        if not message.attachments:
            return
        guild_id = message.guild.id
        channels = await _regear_channels(guild_id)
        thread_channel_id = await _regear_thread_channel_id(guild_id)
        # Canal assistido se for um canal de regear configurado, OU o canal
        # dedicado de threads de regear (top-level), OU uma thread filha dele
        # (o backend resolve o event_id pelo thread id → Event.regear_thread_id).
        chan = message.channel
        is_thread_child = (
            thread_channel_id is not None
            and isinstance(chan, discord.Thread)
            and chan.parent_id == thread_channel_id
        )
        watched = chan.id in channels or chan.id == thread_channel_id or is_thread_child
        if not watched:
            return

        _prune_done()
        if message.id in _done_msgs:
            return  # já processado (ex.: edit)

        imgs = [a for a in message.attachments if a.filename.lower().endswith(_IMG_EXT)]
        if not imgs:
            return

        await message.add_reaction("⏳")
        lang = guild_lang_for(guild_id)
        request_id: int | None = None
        status = "manual"

        for att in imgs:
            try:
                data = await att.read()
            except Exception:
                continue
            form = aiohttp.FormData()
            form.add_field("file", data, filename=att.filename, content_type="image/png")
            form.add_field("msg_id", str(message.id))
            form.add_field("requester_name", message.author.display_name)
            form.add_field("requester_user_id", str(message.author.id))
            form.add_field("channel_id", str(chan.id))
            try:
                async with http_client.session().post(
                    f"{SITE_URL}/guilds/{guild_id}/regear/ingest",
                    data=form,
                    headers={"Authorization": f"Bearer {API_SECRET}"},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as r:
                    if r.status == 200:
                        out = await r.json()
                        request_id = out.get("id")
                        status = out.get("recognition_status") or "manual"
            except Exception:
                status = "manual"

        _done_msgs[message.id] = time.monotonic() + _DONE_TTL
        try:
            await message.remove_reaction("⏳", self.bot.user)
        except Exception:
            pass
        emoji = "✅" if status == "recognized" else "⚠️"
        await message.add_reaction(emoji)
        if request_id is not None:
            try:
                await message.reply(
                    t(lang, "regDeepLink", url=f"{PUBLIC_URL}/regear/{guild_id}/{request_id}",
                      status=emoji)
                )
            except Exception:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Regears(bot))