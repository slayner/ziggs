"""Energy Control — embed constante de jogadores com energia baixa.

O bot mantém um embed único por guilda. O site marca dirty quando logs
chegam ou saldos mudam; o bot pergunta no loop de 30s e só reedita quando
dirty. O embed é sempre a primeira mensagem do canal — se há mensagens
mais novas, reenvia (apaga a velha, posta nova). Jogadores adicionados à
lista são mencionados 1x (ping no content); nas atualizações seguintes
aparecem na listagem sem novo ping.
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import discord
from discord.ext import commands, tasks

import http_client
from cogs._discord_timeout import SKIP_EXC, dtimeout
from cogs.general import _guild_command_config
from i18n import t

_LOOP_INTERVAL = 30  # segundos


async def _get(path: str) -> Optional[dict]:
    return await http_client.get_json(path, tag="energy_control")


async def _post(path: str, body: dict) -> Optional[dict]:
    return await http_client.post_json(
        path, body, tag="energy_control", attempts=2, queue_on_failure=False,
    )


async def _purge_bot_messages(channel: discord.TextChannel) -> None:
    """Apaga TODAS as mensagens do bot no canal (best-effort)."""
    try:
        async for msg in channel.history(limit=100):
            if msg.author == channel.guild.me:
                try:
                    await dtimeout(msg.delete())
                except SKIP_EXC:
                    pass
    except SKIP_EXC:
        pass


def _build_energy_embed(lang: str, rows: list[dict], threshold: int) -> discord.Embed:
    """Embed clean: uma linha por jogador no formato '@mention saldo'.
    Sem colunas, sem descrição, sem título bold. Vazio = mensagem amigável."""
    embed = discord.Embed(color=discord.Color.gold(), title=t(lang, "energy_control_title"))
    if not rows:
        embed.description = t(lang, "energy_control_empty")
        return embed

    lines = [f"<@{r['user_id']}> {r['balance']}" for r in rows[:25]]
    embed.description = "\n".join(lines)
    return embed


class EnergyControl(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._message_ids: dict[int, int] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        if not energy_control_loop.is_running():
            energy_control_loop.start(self)

    async def cog_unload(self) -> None:
        energy_control_loop.cancel()

    async def refresh_energy_control(self, guild: discord.Guild, *, force: bool = False) -> None:
        """Sincronização imediata — chamada pelo catch_up no on_ready.
        force=true ignora o dirty check do backend."""
        lock = self._locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            await self._sync_unlocked(guild, force=force)

    async def _sync_unlocked(self, guild: discord.Guild, *, force: bool = False) -> None:
        cfg = await _guild_command_config(guild.id)
        channel_id = cfg.get("energy_control_channel_id")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await dtimeout(guild.fetch_channel(int(channel_id)))
            except SKIP_EXC:
                return
        if not isinstance(channel, discord.TextChannel):
            print(f"[energy_control] {guild.id}: canal {channel_id} não é TextChannel", flush=True)
            return

        data = await _get(f"/bot/guilds/{guild.id}/energy-control?force={'true' if force else 'false'}")
        if data is None:
            print(f"[energy_control] {guild.id}: backend retornou None", flush=True)
            return

        # Só reedita se dirty (economiza API do Discord).
        if not data.get("dirty"):
            return

        print(f"[energy_control] {guild.id}: dirty=True, processando {len(data.get('rows') or [])} rows", flush=True)

        lang = cfg.get("language", "pt")
        rows = data.get("rows") or []
        threshold = data.get("threshold", 100)
        embed = _build_energy_embed(lang, rows, threshold)

        # new_uids vem do backend (quem entrou na lista desde a última sync).
        # O bot menciona esses 1x no content. No force (catch_up) vem vazio.
        new_uids = data.get("new_uids") or []
        current_uids = [r["user_id"] for r in rows]

        # Resolve message_id: cache local -> backend.
        message_id = self._message_ids.get(guild.id)
        if message_id is None:
            raw = data.get("message_id")
            message_id = int(raw) if raw else None

        message = None
        if message_id:
            try:
                message = await dtimeout(channel.fetch_message(message_id))
            except SKIP_EXC:
                message = None

        # Verifica se o embed é a última mensagem do canal. Se não for
        # (há mensagens mais novas), reenvia para ficar no topo.
        is_last = False
        if message is not None:
            try:
                async for last_msg in channel.history(limit=1):
                    is_last = last_msg.id == message.id
                    break
            except SKIP_EXC:
                is_last = False

        # Content: pings dos novos jogadores (1x cada).
        new_mentions = " ".join(f"<@{uid}>" for uid in new_uids)
        content = new_mentions if new_mentions else None

        if message is not None and is_last and not content:
            # Edit in-place: embed é a última msg e ninguém novo pra pingar.
            try:
                await dtimeout(message.edit(embed=embed))
            except SKIP_EXC:
                return
        else:
            # Reenvia: apaga TODAS as mensagens do bot no canal e posta nova.
            await _purge_bot_messages(channel)
            try:
                mentions = discord.AllowedMentions(users=bool(content))
                message = await dtimeout(channel.send(
                    content=content, embed=embed, allowed_mentions=mentions,
                ))
            except SKIP_EXC:
                return

        self._message_ids[guild.id] = message.id
        await _post(
            f"/bot/guilds/{guild.id}/energy-control/synced",
            {"channel_id": str(channel.id), "message_id": str(message.id),
             "known_uids": current_uids},
        )


_cog_ref: Optional[EnergyControl] = None


@tasks.loop(seconds=_LOOP_INTERVAL)
async def energy_control_loop(cog: EnergyControl) -> None:
    await asyncio.gather(
        *(cog.refresh_energy_control(g) for g in cog.bot.guilds),
        return_exceptions=True,
    )


@energy_control_loop.before_loop
async def _before() -> None:
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@energy_control_loop.error
async def _on_error(error: BaseException) -> None:
    import traceback
    print(f"[energy_control] loop morreu, reiniciando: {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if _cog_ref is not None:
        asyncio.get_running_loop().call_soon(lambda: energy_control_loop.start(_cog_ref))


async def setup(bot: commands.Bot) -> None:
    global _cog_ref
    _cog_ref = EnergyControl(bot)
    await bot.add_cog(_cog_ref)
    print("[energy_control] cog carregada — loop de embed de energia ativo", flush=True)