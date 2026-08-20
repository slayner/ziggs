"""/register — vincula o nick do Albion à conta Discord e libera o cargo
configurado no dashboard do site, se o personagem estiver na guilda certa.

Sem relação com o registro de personagens do site (claims) — aquele é
verificação de posse via morte com itens específicos; este é só "o jogador
está na guilda Albion configurada?" pra liberar cargo no Discord.
"""
import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands, Interaction
from discord.ext import commands
from typing import Optional

import http_client
from cogs._discord_timeout import SKIP_EXC, dtimeout
from cogs.general import _guild_command_config, check_command_access, guild_lang, resolve_user_or_guild
from i18n import t
from localization import loc

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")

# API da Albion instável durante o /register: o comando retorna imediatamente
# ("verificando") e despacha o trabalho pra background; o resultado chega por DM.
# Teto de 1h de re-tentativas em segundo plano antes de desistir.
_RETRY_INTERVAL = 20            # segundos entre tentativas
_BACKGROUND_RETRY_CAP = 60 * 60 # 1h tentando em segundo plano antes de desistir
_TRANSIENT_REASONS = {"albion_unavailable"}

# Guarda referência forte das tarefas de fundo — sem isso, o event loop pode
# coletar a task no meio da espera (ela roda por até 1h sem nada mais
# referenciando o objeto Task além da variável local que já saiu de escopo).
_background_tasks: set[asyncio.Task] = set()
_recent_human_revocations: dict[tuple[int, int], datetime] = {}


def _note_human_revocation(guild_id: int, user_id: int) -> None:
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(hours=2)
    for key, revoked_at in list(_recent_human_revocations.items()):
        if revoked_at < stale_before:
            del _recent_human_revocations[key]
    _recent_human_revocations[guild_id, user_id] = now


def _request_is_superseded(guild_id: int, user_id: int, requested_at: datetime) -> bool:
    revoked_at = _recent_human_revocations.get((guild_id, user_id))
    return revoked_at is not None and requested_at <= revoked_at


def _spawn_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _is_transient(result: dict | None) -> bool:
    """None = não deu nem pra falar com o backend; "albion_unavailable" = o
    backend falou, mas não conseguiu confirmar nada na API da Albion. Os dois
    casos merecem re-tentativa automática, não um erro definitivo."""
    return result is None or result.get("reason") in _TRANSIENT_REASONS


_REASON_KEY = {
    "no_albion_guild": "reason_no_albion_guild",
    "no_role_configured": "reason_no_role_configured",
    "not_found": "reason_not_found",
    "not_in_guild": "reason_not_in_guild",
    "ally_not_allowed": "reason_ally_not_allowed",
    "already_registered": "reason_already_registered",
    "human_revoked": "reason_human_revoked",
}


async def _post_register(
    guild_id: int, discord_user_id: int, nick: str, registering_other: bool = False,
    requested_at: datetime | None = None,
) -> dict | None:
    # registering_other: com a vigilância de saída desligada no site, o backend
    # pula a checagem de guilda pra registro de terceiros (confiança do admin).
    return await http_client.request_json(
        "POST", f"/bot/register/{guild_id}",
        json={
            "discord_user_id": str(discord_user_id),
            "albion_player_name": nick,
            "registering_other": registering_other,
            "requested_at": (requested_at or datetime.now(timezone.utc)).isoformat(),
        },
        timeout=10, attempts=2, queue_on_failure=False,
    )


async def _post_unregister(
    guild_id: int, *, discord_user_id: Optional[int] = None, albion_player_name: Optional[str] = None,
) -> dict | None:
    body: dict = {}
    if discord_user_id is not None:
        body["discord_user_id"] = str(discord_user_id)
    if albion_player_name is not None:
        body["albion_player_name"] = albion_player_name
    return await http_client.request_json(
        "POST", f"/bot/unregister/{guild_id}", json=body, timeout=10,
        attempts=2, queue_on_failure=False,
    )


async def _post_left_guild(guild_id: int, discord_user_id: int) -> None:
    """A fila preserva a decisão Discord se o backend cair durante a saída."""
    await http_client.request_json(
        "POST", f"/bot/registration-left-guild/{guild_id}",
        json={"discord_user_id": str(discord_user_id)}, timeout=10, attempts=2,
    )


async def _post_role_removed(
    guild_id: int, discord_user_id: int, removed_role_ids: list[int],
    retains_massinfo_access: bool,
) -> dict | None:
    """A fila preserva a remoção manual até o backend poder registrá-la."""
    if not removed_role_ids:
        return None
    return await http_client.request_json(
        "POST", f"/bot/registration-role-removed/{guild_id}",
        json={
            "discord_user_id": str(discord_user_id),
            "removed_role_ids": [str(r) for r in removed_role_ids],
            "retains_massinfo_access": retains_massinfo_access,
        }, timeout=10, attempts=2,
    )


async def _apply_result(
    guild: discord.Guild, lang: str, invoker_id: int, target: discord.Member,
    result: dict, requested_at: datetime,
) -> str:
    """Resultado FINAL (não-transitório) de um /bot/register — aplica o cargo
    se for sucesso e devolve o texto pronto pra mostrar/enviar ao usuário."""
    if not result.get("ok"):
        msg = t(lang, _REASON_KEY.get(result.get("reason"), "register_generic_fail"))
        return f"❌ {msg}"
    if _request_is_superseded(guild.id, target.id, requested_at):
        return f"❌ {t(lang, 'reason_human_revoked')}"

    role = guild.get_role(int(result["role_id"]))
    if role is None:
        return t(lang, "role_missing")

    try:
        await dtimeout(target.add_roles(role, reason=f"/register — {result['albion_player_name']}"))
    except SKIP_EXC:
        return t(lang, "role_forbidden")

    who = t(lang, "register_who_self") if target.id == invoker_id else t(lang, "register_who_other", mention=target.mention)
    return t(lang, "register_success", who=who, nick=result["albion_player_name"], role=role.mention)


async def _retry_in_background(
    guild: discord.Guild, lang: str, invoker: discord.Member, target: discord.Member,
    nick: str, guild_id: int, requested_at: datetime,
) -> None:
    """Continua tentando depois que a janela de feedback ao vivo da interação
    acabou (o token de followup do Discord expira ~15min após a interação) —
    avisa o usuário por DM quando finalmente resolver, em vez de deixar o
    registro morrer silenciosamente ou forçar o usuário a rodar tudo de novo."""
    start = time.monotonic()
    result: dict | None = None
    while (time.monotonic() - start) < _BACKGROUND_RETRY_CAP:
        await asyncio.sleep(_RETRY_INTERVAL)
        result = await _post_register(
            guild_id, target.id, nick, invoker.id != target.id, requested_at,
        )
        if not _is_transient(result):
            break

    if result is None or _is_transient(result):
        return  # API seguiu instável até o limite — desiste sem spammar DM

    content = await _apply_result(guild, lang, invoker.id, target, result, requested_at)
    try:
        await dtimeout(invoker.send(content))
    except SKIP_EXC:
        pass


async def _resolve_register(
    guild: discord.Guild, lang: str, invoker: discord.Member, target: discord.Member,
    nick: str, guild_id: int, requested_at: datetime,
) -> None:
    """Tenta resolver o /register em segundo plano. Primeira tentativa
    imediata; se a API da Albion estiver instável, re-tenta até resolver ou
    até a janela de background expirar. Resultado final chega por DM."""
    result = await _post_register(
        guild_id, target.id, nick, invoker.id != target.id, requested_at,
    )
    start = time.monotonic()
    while _is_transient(result) and (time.monotonic() - start) < _BACKGROUND_RETRY_CAP:
        await asyncio.sleep(_RETRY_INTERVAL)
        result = await _post_register(
            guild_id, target.id, nick, invoker.id != target.id, requested_at,
        )

    if result is None or _is_transient(result):
        try:
            await dtimeout(invoker.send(t(lang, "register_background_giveup", nick=nick, target=target.mention)))
        except SKIP_EXC:
            pass
        return

    content = await _apply_result(guild, lang, invoker.id, target, result, requested_at)
    try:
        await dtimeout(invoker.send(content))
    except SKIP_EXC:
        pass


async def _do_register(interaction: Interaction, nick: str, target: discord.Member) -> None:
    """Fire-and-forget: responde imediatamente com 'verificando' e despacha
    a resolução pra background. O admin pode encadear o próximo /register sem
    esperar — o resultado de cada um chega por DM quando a API da Albion
    responder."""
    assert interaction.guild_id and interaction.guild
    lang = await guild_lang(interaction)
    nick = nick.strip()
    guild, invoker, guild_id = interaction.guild, interaction.user, interaction.guild_id
    requested_at = datetime.now(timezone.utc)

    who = t(lang, "register_who_self") if target.id == invoker.id else t(lang, "register_who_other", mention=target.mention)
    await interaction.edit_original_response(content=t(lang, "register_queued", who=who, nick=nick))
    _spawn_background(_resolve_register(guild, lang, invoker, target, nick, guild_id, requested_at))


class Registration(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="register", description=loc("Links an Albion nickname to a Discord account and unlocks the role", "cmd_desc_register"))
    @app_commands.describe(
        nick=loc("Albion nickname to register", "opt_desc_register_nick"),
        usuario=loc("Discord user to register (blank = yourself)", "opt_desc_register_usuario"),
    )
    @app_commands.guild_only()
    async def register(
        self,
        interaction: Interaction,
        nick: str,
        usuario: Optional[discord.Member] = None,
    ) -> None:
        assert interaction.guild_id and interaction.guild

        if not await check_command_access(interaction, "register"):
            return

        # Registering someone else requires the register_others permission,
        # checked BEFORE deferring — check_command_access sends the first
        # response itself when it refuses.
        if usuario is not None and usuario.id != interaction.user.id:
            if not await check_command_access(interaction, "register_others"):
                return

        await interaction.response.defer(ephemeral=True)
        await _do_register(interaction, nick, usuario or interaction.user)

    # ------------------------------------------------------------------
    # /unregister — comando de controle (admin por padrão)
    # ------------------------------------------------------------------
    @app_commands.command(name="unregister", description=loc("Removes a member's registration and role", "cmd_desc_unregister"))
    @app_commands.describe(alvo=loc("Mention, ID, Discord username, or the member's Albion nickname", "opt_desc_unregister_alvo"))
    @app_commands.rename(alvo=loc("user", "opt_name_alvo"))
    @app_commands.guild_only()
    async def unregister(self, interaction: Interaction, alvo: str) -> None:
        if not await check_command_access(interaction, "unregister"):
            return
        lang = await guild_lang(interaction)

        await interaction.response.defer(ephemeral=True)

        # Só trata como referência ao Discord se for INEQUÍVOCO (menção, ID, ou
        # nome/apelido exato) — qualquer outra coisa é tratada como nick do
        # Albion, repassado pro backend resolver via BotRegistration.
        target = await resolve_user_or_guild(interaction, alvo, fuzzy=False)
        if isinstance(target, (discord.Member, discord.User)):
            result = await _post_unregister(interaction.guild_id, discord_user_id=target.id)
        else:
            result = await _post_unregister(interaction.guild_id, albion_player_name=alvo.strip())

        if result is None:
            await interaction.followup.send(t(lang, "retry_later"), ephemeral=True)
            return
        if not result.get("ok"):
            await interaction.followup.send(t(lang, "unregister_not_found", alvo=alvo), ephemeral=True)
            return

        removed_roles: set[str] = set()
        for uid in result.get("discord_user_ids", []):
            target_member = interaction.guild.get_member(int(uid)) if interaction.guild else None
            if not target_member:
                continue
            for rid in result.get("role_ids", []):
                role = interaction.guild.get_role(int(rid)) if interaction.guild else None
                if role and role in target_member.roles:
                    try:
                        await target_member.remove_roles(role, reason="/unregister")
                        removed_roles.add(role.mention)
                    except discord.Forbidden:
                        pass

        msg = t(lang, "unregister_success", alvo=alvo)
        if removed_roles:
            msg += t(lang, "unregister_roles_removed", roles=", ".join(sorted(removed_roles)))
        await interaction.followup.send(msg, ephemeral=True)

    # ------------------------------------------------------------------
    # Saída/kick/ban/remoção manual de cargo — desliga o registro sozinho
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        # Discord não distingue saída voluntária de kick nesse evento — nos
        # dois casos o membro já não está mais no servidor pra ter cargo.
        _note_human_revocation(member.guild.id, member.id)
        await _post_left_guild(member.guild.id, member.id)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        _note_human_revocation(guild.id, user.id)
        await _post_left_guild(guild.id, user.id)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        removed_ids = {r.id for r in before.roles} - {r.id for r in after.roles}
        if removed_ids:
            cfg = await _guild_command_config(after.guild.id)
            channel_id = cfg.get("events_channel_id")
            try:
                channel = after.guild.get_channel(int(channel_id)) if channel_id else None
            except (TypeError, ValueError):
                channel = None
            retains_massinfo_access = bool(channel and channel.permissions_for(after).view_channel)
            result = await _post_role_removed(
                after.guild.id, after.id, list(removed_ids), retains_massinfo_access,
            )
            role_ids = (result or {}).get("role_ids", [])
            if role_ids:
                _note_human_revocation(after.guild.id, after.id)
            # Se uma resposta velha de /register recolocou o cargo enquanto a
            # revogação era persistida, remove-o agora. O próximo update volta
            # aqui, mas o backend já o marcou inativo e devolve lista vazia.
            member = after.guild.get_member(after.id)
            for role_id in role_ids:
                role = after.guild.get_role(int(role_id))
                if member and role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="Manual registration-role removal prevails")
                    except (discord.Forbidden, discord.HTTPException):
                        pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Registration(bot))
