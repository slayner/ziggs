"""Verificação recorrente de acesso ao mass-info sem registro.

Loop de 1h que lista, no canal logs-bot, os usuários com `View Channel` no
canal mass-info (events_channel_id) que NÃO têm BotRegistration ativo — instrui
os admins a usar /register ou /bypass. /bypass adiciona o usuário a uma lista
de bypass persistida no backend (Guild.settings.massinfo_access_bypass_user_ids)
pra ele parar de ser anunciado mesmo sem registro (ex.: alt de admin, bot, ou
conta que o admin decidiu liberar manualmente).

Canal mass-info = events_channel_id (Config → Canal de Eventos no site).
Canal de anúncio = logs-bot (mesmo do audit_log.py).
"""
import asyncio
import os
from typing import Optional

import discord
from discord import app_commands, Interaction
from discord.ext import commands, tasks

import http_client
from cogs.general import _guild_command_config, check_command_access, guild_lang, resolve_user_or_guild
from i18n import t
from localization import loc

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")

# 1h entre anúncios — frequência alta vira spam e dilui o sinal.
_LOOP_INTERVAL = 15 * 60  # 15min — cadência pedida pelo dono (staff precisa agir)
# Discord API pode pendurar sem timeout — teto de 15s transforma travamento
# em skip; próximo tick tenta de novo (mesmo padrão de audit_log.py).
_API_TIMEOUT = 15
_SKIP_EXC = (discord.NotFound, discord.Forbidden, discord.HTTPException, asyncio.TimeoutError)

_cog_ref: "MassinfoAccess | None" = None


async def _get(path: str) -> Optional[dict]:
    return await http_client.get_json(path, tag="massinfo_access")


async def _post(path: str, body: dict) -> Optional[dict]:
    return await http_client.post_json(path, body, tag="massinfo_access", attempts=2)


def _has_massinfo_view(member: discord.Member, channel: discord.abc.GuildChannel) -> bool:
    """Reaproveita a MESMA checagem do on_member_update em registration.py:
    `channel.permissions_for(member).view_channel` (overwrites + cargos)."""
    try:
        return bool(channel.permissions_for(member).view_channel)
    except Exception:
        return False


async def _unregistered_with_access(
    guild: discord.Guild, channel: discord.abc.GuildChannel,
    registered_ids: set[int], bypass_ids: set[int],
) -> list[discord.Member]:
    """Varrer todos os membros com View Channel no mass-info que não estão
    registrados e não estão na lista de bypass. Bots ficam de fora."""
    out: list[discord.Member] = []
    for member in guild.members:
        if member.bot:
            continue
        if member.id in bypass_ids:
            continue
        if member.id in registered_ids:
            continue
        if _has_massinfo_view(member, channel):
            out.append(member)
    return out


def _build_embed(lang: str, members: list[discord.Member]) -> discord.Embed:
    embed = discord.Embed(color=discord.Color.orange(), title=t(lang, "massinfo_access_title"))
    if not members:
        embed.description = t(lang, "massinfo_access_empty")
        return embed
    embed.description = t(lang, "massinfo_access_desc", count=len(members))
    # Quebra em fields de 1024 chars (limite do Discord por field).
    chunk, chunk_len, first = [], 0, True
    for m in members:
        line = f"• {m.mention} ({m.name})"
        needed = len(line) + (1 if chunk else 0)
        if chunk_len + needed > 1024:
            embed.add_field(
                name=t(lang, "massinfo_access_field") if first else "​",
                value="\n".join(chunk), inline=False,
            )
            first = False
            chunk, chunk_len = [], 0
        chunk.append(line)
        chunk_len += needed
    if chunk:
        embed.add_field(
            name=t(lang, "massinfo_access_field") if first else "​",
            value="\n".join(chunk), inline=False,
        )
    embed.add_field(
        name=t(lang, "massinfo_access_actions_title"),
        value=t(lang, "massinfo_access_actions_body"),
        inline=False,
    )
    return embed


async def _do_bypass(interaction: Interaction, target: discord.Member | discord.User) -> None:
    """Espera que `interaction` já tenha uma resposta em andamento (defer)."""
    assert interaction.guild_id and interaction.guild
    lang = await guild_lang(interaction)
    guild_id = interaction.guild_id
    result = await _post(
        f"/bot/guilds/{guild_id}/massinfo-access/bypass",
        {"action": "add", "user_id": str(target.id)},
    )
    if result is None:
        await interaction.followup.send(t(lang, "retry_later"), ephemeral=True)
        return
    await interaction.followup.send(
        t(lang, "bypass_added", mention=target.mention), ephemeral=True,
    )


class MassinfoAccess(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        global _cog_ref
        _cog_ref = self
        print("[massinfo_access] cog carregada — loop de verificação ativo")
        if not massinfo_access_loop.is_running():
            massinfo_access_loop.start(self)

    async def cog_unload(self) -> None:
        massinfo_access_loop.cancel()

    async def _ensure_logs_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Mesma lógica de audit_log.py: usa logs_channel_id configurado; se
        não houver, cai pro fallback logs-bot por nome (já existente)."""
        cfg = await _guild_command_config(guild.id)
        channel_id = cfg.get("logs_channel_id")
        if channel_id:
            try:
                cid = int(channel_id)
            except (ValueError, TypeError):
                return None
            channel = guild.get_channel(cid)
            if channel is None:
                try:
                    channel = await asyncio.wait_for(
                        guild.fetch_channel(cid), timeout=_API_TIMEOUT,
                    )
                except _SKIP_EXC:
                    return None
            return channel if isinstance(channel, discord.TextChannel) else None
        existing = discord.utils.get(guild.text_channels, name="logs-bot")
        return existing if existing is not None else None

    async def sync_guild(self, guild: discord.Guild) -> None:
        lock = self._locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            await self._sync_guild_unlocked(guild)

    async def _sync_guild_unlocked(self, guild: discord.Guild) -> None:
        cfg = await _guild_command_config(guild.id)
        massinfo_channel_id = cfg.get("events_channel_id")
        if not massinfo_channel_id:
            return  # sem canal mass-info configurado — nada a verificar
        try:
            massinfo_channel = guild.get_channel(int(massinfo_channel_id))
        except (TypeError, ValueError):
            massinfo_channel = None
        if massinfo_channel is None:
            return

        logs_channel = await self._ensure_logs_channel(guild)
        if logs_channel is None:
            return  # sem canal de logs — não tem pra onde postar

        # Lista de registrados ativos — 1 request, não N.
        regs = await _get(f"/bot/registrations/{guild.id}")
        if regs is None:
            return  # backend fora — skipa este tick
        registered_ids = set()
        for uid in regs.get("discord_user_ids", []):
            try:
                registered_ids.add(int(uid))
            except (TypeError, ValueError):
                pass

        bypass_raw = cfg.get("massinfo_access_bypass_user_ids") or []
        bypass_ids = set()
        for uid in bypass_raw:
            try:
                bypass_ids.add(int(uid))
            except (TypeError, ValueError):
                pass

        members = await _unregistered_with_access(
            guild, massinfo_channel, registered_ids, bypass_ids,
        )
        lang = cfg["language"]
        embed = _build_embed(lang, members)

        # Sempre posta novo (não reedita) — canal de logs é append-only, o
        # anúncio precisa ser lido no momento que chega, e @here pingua a staff.
        # Sem membros não há nada a anunciar (evita @here a cada 15min à toa).
        if not members:
            return
        try:
            await asyncio.wait_for(
                logs_channel.send(
                    content="@here", embed=embed,
                    allowed_mentions=discord.AllowedMentions(everyone=True, roles=False, users=False),
                ),
                timeout=_API_TIMEOUT,
            )
        except _SKIP_EXC:
            pass

    # ------------------------------------------------------------------
    # /bypass — admin only
    # ------------------------------------------------------------------
    @app_commands.command(
        name="bypass",
        description=loc(
            "Remove um usuário do anúncio recorrente de não-registrados com acesso ao mass-info",
            "cmd_desc_bypass",
        ),
    )
    @app_commands.describe(
        usuario=loc("Usuário a remover do anúncio (menção, ID ou nome)", "opt_desc_bypass_usuario"),
    )
    @app_commands.rename(usuario=loc("user", "opt_name_alvo"))
    @app_commands.guild_only()
    async def bypass(self, interaction: Interaction, usuario: str) -> None:
        if not await check_command_access(interaction, "bypass"):
            return
        lang = await guild_lang(interaction)

        target = await resolve_user_or_guild(interaction, usuario, fuzzy=False)
        if not isinstance(target, (discord.Member, discord.User)):
            await interaction.response.send_message(
                t(lang, "not_found_target", alvo=usuario), ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await _do_bypass(interaction, target)
        # Força o próximo tick a reeditar o anúncio sem esse usuário.
        asyncio.create_task(self.sync_guild(interaction.guild))


@tasks.loop(seconds=_LOOP_INTERVAL)
async def massinfo_access_loop(cog: MassinfoAccess) -> None:
    for guild in cog.bot.guilds:
        try:
            await cog.sync_guild(guild)
        except Exception as e:
            print(f"[massinfo_access] erro no loop ({guild.id}): {type(e).__name__}: {e}")


@massinfo_access_loop.before_loop
async def _before() -> None:
    # Mesma pegadinha de audit_log.py: before_loop não recebe os args de
    # .start(cog) — usa o _cog_ref global setado em cog_load.
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@massinfo_access_loop.error
async def _on_error(error: BaseException) -> None:
    # tasks.loop mata a task no primeiro erro não tratado — autocura em vez
    # de ficar morto pro resto do processo (mesmo padrão de audit_log.py).
    import traceback
    print(f"[massinfo_access] LOOP MORREU, reiniciando: {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if _cog_ref is not None:
        asyncio.get_running_loop().call_soon(lambda: massinfo_access_loop.start(_cog_ref))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MassinfoAccess(bot))