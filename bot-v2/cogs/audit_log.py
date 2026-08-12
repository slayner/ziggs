"""Canal de logs do bot — retransmite o AuditLog (trilha de auditoria imutável
do site, ver app/models/audit.py) pro Discord em tempo real.

Toggle "Bot Logs" nas Features do site (GuildConfig.tsx) liga/desliga —
default True (era sempre-ativa antes do toggle existir). Desligada, o bot nem
cria nem mantém canal nenhum. Ligada e sem logs_channel_id configurado, o bot
cria um canal próprio (negado pra @everyone; quem tem permissão Administrator
do Discord já enxerga qualquer canal automaticamente, então não precisa listar
cargos) e reporta o id pro site guardar. Cursor (logs_last_sent_id) vive no
site — mesmo padrão de massinfo_message_id (ver cogs/events.py)."""
import asyncio
import os
from datetime import datetime
from typing import Optional

import discord
from discord.ext import commands, tasks

import http_client
from cogs.general import _guild_command_config, guild_lang_for
from i18n import t

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")

_CHANNEL_NAME = "logs-bot"
_SOURCE_COLOR = {
    "site": discord.Color.blurple(), "bot": discord.Color.green(), "system": discord.Color.greyple(),
}


async def _get(path: str) -> Optional[dict]:
    return await http_client.get_json(path)


async def _post(path: str, body: dict) -> Optional[dict]:
    return await http_client.post_json(path, body, tag="audit_log", attempts=2)


def _diff_lines(before: Optional[dict], after: Optional[dict]) -> list[str]:
    before = before or {}
    after = after or {}
    lines = []
    for k in sorted(set(before) | set(after)):
        bv, av = before.get(k), after.get(k)
        if bv == av:
            continue
        lines.append(f"**{k}**: {bv} → {av}")
    return lines


def _build_log_embed(lang: str, entry: dict) -> discord.Embed:
    embed = discord.Embed(
        title=entry["action"],
        color=_SOURCE_COLOR.get(entry["source"], discord.Color.greyple()),
    )
    entity_line = entry["entity"]
    if entry.get("entity_id"):
        entity_line += f" #{entry['entity_id']}"
    embed.add_field(name=t(lang, "logs_entity"), value=entity_line, inline=True)
    actor = f"<@{entry['actor_id']}>" if entry.get("actor_id") else t(lang, "logs_system")
    embed.add_field(name=t(lang, "logs_actor"), value=f"{actor} · {entry['source']}", inline=True)
    diff = _diff_lines(entry.get("before"), entry.get("after"))
    if diff:
        embed.add_field(name=t(lang, "logs_changes"), value="\n".join(diff)[:1000], inline=False)
    if entry.get("note"):
        embed.add_field(name=t(lang, "logs_note"), value=str(entry["note"])[:1000], inline=False)
    try:
        embed.timestamp = datetime.fromisoformat(entry["created_at"])
    except (ValueError, KeyError):
        pass
    return embed


_cog_ref: "BotAuditLog | None" = None


class BotAuditLog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._sent: dict[int, int] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        global _cog_ref
        _cog_ref = self
        print("[audit_log] cog carregada — loop de canal de logs ativo")
        if not audit_log_loop.is_running():
            audit_log_loop.start(self)

    async def cog_unload(self) -> None:
        audit_log_loop.cancel()

    async def ensure_logs_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        cfg = await _guild_command_config(guild.id)
        channel_id = cfg.get("logs_channel_id")
        if channel_id:
            try:
                cid = int(channel_id)
            except (ValueError, TypeError):
                cid = None
            if cid is not None:
                # get_channel só vê cache local; canais que o bot nunca acessou
                # não estão lá. fetch_channel bate na API — custa 1 request mas
                # garante que o canal configurado é encontrado em vez de criar
                # um logs-bot duplicado só porque a cache não tinha o canal.
                channel = guild.get_channel(cid)
                if channel is None:
                    try:
                        ch = await guild.fetch_channel(cid)
                        if isinstance(ch, discord.TextChannel):
                            return ch
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass  # canal foi deletado de verdade — recria abaixo
                else:
                    return channel
        # Idempotência por nome: se já existe um logs-bot (ex.: criado num tick
        # anterior cujo POST de persistência ainda não refluiu pelo cache de 60s
        # de _guild_command_config, ou o site perdeu o id), reusa em vez de
        # criar outro. Sem isso, on_ready + audit_log_loop (8s) + reconexões
        # criavam um canal a cada tick enquanto logs_channel_id estava None.
        existing = discord.utils.get(guild.text_channels, name=_CHANNEL_NAME)
        if existing is not None:
            await _post(f"/bot/guilds/{guild.id}/logs-channel", {"channel_id": str(existing.id)})
            return existing
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        try:
            channel = await guild.create_text_channel(
                _CHANNEL_NAME, overwrites=overwrites,
                reason="Canal de logs do bot (auto-criado, admin-only)",
            )
        except (discord.Forbidden, discord.HTTPException):
            return None
        await _post(f"/bot/guilds/{guild.id}/logs-channel", {"channel_id": str(channel.id)})
        return channel

    async def sync_guild(self, guild: discord.Guild) -> None:
        lock = self._locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            await self._sync_guild_unlocked(guild)

    async def _sync_guild_unlocked(self, guild: discord.Guild) -> None:
        cfg = await _guild_command_config(guild.id)
        if not cfg["bot_logs_enabled"]:
            return
        channel = await self.ensure_logs_channel(guild)
        if channel is None:
            return
        data = await _get(f"/bot/guilds/{guild.id}/audit-log")
        entries = [
            entry for entry in ((data or {}).get("entries") or [])
            if entry["id"] > self._sent.get(guild.id, 0)
        ]
        if not entries:
            return
        lang = await guild_lang_for(guild.id)
        last_id = None
        for entry in entries:
            try:
                await channel.send(embed=_build_log_embed(lang, entry))
            except (discord.Forbidden, discord.HTTPException):
                break  # para no primeiro erro — ack só até o último enviado com sucesso
            last_id = entry["id"]
            self._sent[guild.id] = last_id
        if last_id is not None:
            await _post(f"/bot/guilds/{guild.id}/audit-log-synced", {"last_id": last_id})


@tasks.loop(seconds=8)
async def audit_log_loop(cog: BotAuditLog) -> None:
    for guild in cog.bot.guilds:
        try:
            await cog.sync_guild(guild)
        except Exception as e:
            print(f"[audit_log] erro no loop ({guild.id}): {type(e).__name__}: {e}")


@audit_log_loop.before_loop
async def _before() -> None:
    # discord.py chama before_loop SEM os args de .start(cog) (só o corpo
    # principal do loop recebe) — declarar `cog` aqui derruba a task com
    # TypeError a CADA .start(), antes do primeiro tick. Era por isto que o
    # canal de logs só recebia entradas no catch-up do on_ready (main.py),
    # nunca deste loop — mesma raiz já corrigida em regear_threads.py,
    # event_embeds.py e voice_presence.py. Usa o _cog_ref global (setado em
    # cog_load) em vez de receber como parâmetro.
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@audit_log_loop.error
async def _on_error(error: BaseException) -> None:
    # Confirmado empiricamente: se ISTO roda, o loop MORREU — tasks.loop só
    # chama .error() pra log e deixa a task terminar, nunca reagenda sozinho.
    # Loga alto E reinicia — autocura em vez de ficar morto pro resto do
    # processo.
    import traceback
    print(f"[audit_log] LOOP MORREU, reiniciando: {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if _cog_ref is not None:
        # .error() roda ANTES do _loop() interno terminar a task de verdade —
        # chamar .start() aqui de forma síncrona corre com esse encerramento.
        # call_soon empurra pro próximo tick do event loop, depois que a task
        # atual já terminou.
        asyncio.get_running_loop().call_soon(lambda: audit_log_loop.start(_cog_ref))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BotAuditLog(bot))
