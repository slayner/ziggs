"""/register — vincula o nick do Albion à conta Discord e libera o cargo
configurado no dashboard do site, se o personagem estiver na guilda certa.

Sem relação com o registro de personagens do site (claims) — aquele é
verificação de posse via morte com itens específicos; este é só "o jogador
está na guilda Albion configurada?" pra liberar cargo no Discord.
"""
import asyncio
import os
import time
import discord
from discord import app_commands, Interaction
from discord.ext import commands
from typing import Optional

import http_client
from cogs.general import check_command_access, guild_lang, resolve_user_or_guild
from i18n import t
from localization import loc

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")

# API da Albion instável/fora do ar durante o /register: em vez de devolver um
# erro que obriga o usuário a rodar o comando de novo, o bot re-tenta sozinho
# — primeiro com feedback ao vivo na própria interação (janela do webhook de
# followup do Discord expira em ~15min), depois em segundo plano com aviso por DM.
_RETRY_INTERVAL = 20            # segundos entre tentativas
_LIVE_RETRY_WINDOW = 10 * 60    # segundos com feedback ao vivo, com folga da janela de 15min
_BACKGROUND_RETRY_CAP = 60 * 60 # 1h tentando em segundo plano antes de desistir de vez
_TRANSIENT_REASONS = {"albion_unavailable"}

# Guarda referência forte das tarefas de fundo — sem isso, o event loop pode
# coletar a task no meio da espera (ela roda por até 1h sem nada mais
# referenciando o objeto Task além da variável local que já saiu de escopo).
_background_tasks: set[asyncio.Task] = set()


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
}


async def _post_register(guild_id: int, discord_user_id: int, nick: str, registering_other: bool = False) -> dict | None:
    # registering_other: com a vigilância de saída desligada no site, o backend
    # pula a checagem de guilda pra registro de terceiros (confiança do admin).
    return await http_client.request_json(
        "POST", f"/bot/register/{guild_id}",
        json={
            "discord_user_id": str(discord_user_id),
            "albion_player_name": nick,
            "registering_other": registering_other,
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
    """Best-effort: usuário saiu/foi kickado/banido — não há cargo pra remover
    (ele já não está mais no servidor), só desliga o registro no banco."""
    await http_client.post_best_effort(
        f"/bot/registration-left-guild/{guild_id}",
        {"discord_user_id": str(discord_user_id)},
    )


async def _post_role_removed(guild_id: int, discord_user_id: int, removed_role_ids: list[int]) -> None:
    """Best-effort: alguém tirou manualmente um cargo do membro — se era o
    cargo de um registro ativo, esse registro perde a validade."""
    if not removed_role_ids:
        return
    await http_client.post_best_effort(
        f"/bot/registration-role-removed/{guild_id}",
        {
            "discord_user_id": str(discord_user_id),
            "removed_role_ids": [str(r) for r in removed_role_ids],
        },
    )


async def _apply_result(guild: discord.Guild, lang: str, invoker_id: int, target: discord.Member, result: dict) -> str:
    """Resultado FINAL (não-transitório) de um /bot/register — aplica o cargo
    se for sucesso e devolve o texto pronto pra mostrar/enviar ao usuário."""
    if not result.get("ok"):
        msg = t(lang, _REASON_KEY.get(result.get("reason"), "register_generic_fail"))
        return f"❌ {msg}"

    role = guild.get_role(int(result["role_id"]))
    if role is None:
        return t(lang, "role_missing")

    try:
        await target.add_roles(role, reason=f"/register — {result['albion_player_name']}")
    except (discord.Forbidden, discord.HTTPException):
        return t(lang, "role_forbidden")

    who = t(lang, "register_who_self") if target.id == invoker_id else t(lang, "register_who_other", mention=target.mention)
    return t(lang, "register_success", who=who, nick=result["albion_player_name"], role=role.mention)


async def _retry_in_background(guild: discord.Guild, lang: str, invoker: discord.Member, target: discord.Member, nick: str, guild_id: int) -> None:
    """Continua tentando depois que a janela de feedback ao vivo da interação
    acabou (o token de followup do Discord expira ~15min após a interação) —
    avisa o usuário por DM quando finalmente resolver, em vez de deixar o
    registro morrer silenciosamente ou forçar o usuário a rodar tudo de novo."""
    start = time.monotonic()
    result: dict | None = None
    while (time.monotonic() - start) < _BACKGROUND_RETRY_CAP:
        await asyncio.sleep(_RETRY_INTERVAL)
        result = await _post_register(guild_id, target.id, nick, invoker.id != target.id)
        if not _is_transient(result):
            break

    if result is None or _is_transient(result):
        return  # API seguiu instável até o limite — desiste sem spammar DM

    content = await _apply_result(guild, lang, invoker.id, target, result)
    try:
        await invoker.send(content)
    except (discord.Forbidden, discord.HTTPException):
        pass


async def _do_register(interaction: Interaction, nick: str, target: discord.Member) -> None:
    """Espera que `interaction` já tenha uma resposta em andamento (defer ou
    edit_message) — sempre edita essa resposta original."""
    assert interaction.guild_id and interaction.guild
    lang = await guild_lang(interaction)
    nick = nick.strip()
    guild, invoker, guild_id = interaction.guild, interaction.user, interaction.guild_id

    result = await _post_register(guild_id, target.id, nick, invoker.id != target.id)
    start = time.monotonic()
    attempt = 0
    # API da Albion instável: em vez de devolver um erro que obriga o usuário
    # a rodar o comando de novo, fica tentando sozinho e mostra o progresso
    # editando a mesma mensagem — "fila" visível pro usuário.
    while _is_transient(result) and (time.monotonic() - start) < _LIVE_RETRY_WINDOW:
        attempt += 1
        await interaction.edit_original_response(content=t(lang, "register_retrying", attempt=attempt))
        await asyncio.sleep(_RETRY_INTERVAL)
        result = await _post_register(guild_id, target.id, nick, invoker.id != target.id)

    if not _is_transient(result):
        content = await _apply_result(guild, lang, invoker.id, target, result)
        await interaction.edit_original_response(content=content)
        return

    await interaction.edit_original_response(content=t(lang, "register_queued_background"))
    _spawn_background(_retry_in_background(guild, lang, invoker, target, nick, guild_id))


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
        await _post_left_guild(member.guild.id, member.id)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        await _post_left_guild(guild.id, user.id)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        removed_ids = {r.id for r in before.roles} - {r.id for r in after.roles}
        if removed_ids:
            await _post_role_removed(after.guild.id, after.id, list(removed_ids))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Registration(bot))
