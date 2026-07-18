"""Rede de segurança global de erros — a peça que faltava na UX do chat.

Sem isto, QUALQUER exceção não tratada num slash command vira "O aplicativo
não respondeu" mudo pro usuário (e, antes do setup_logging no main, nem log
tinha). Aqui: erros conhecidos ganham resposta educada no idioma da guilda;
erros inesperados ganham log completo + um pedido de desculpa ephemeral, em
vez de silêncio.

Instalado uma vez no main() via install(bot). Um handler compartilhado no
tree cobre todos os comandos de todos os cogs — nenhum cog precisa de
try/except próprio pra isso.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)


async def _reply_ephemeral(interaction: discord.Interaction, msg: str) -> None:
    """Responde ephemeral pelo canal que ainda estiver disponível. Interação
    já expirada (>3s sem ack) não tem conserto — só o log fica."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


def install(bot: commands.Bot) -> None:
    async def on_app_command_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        # Imports tardios: error_handler é importado pelo main antes dos cogs.
        from cogs.general import guild_lang
        from i18n import t

        original: BaseException = error
        if isinstance(error, app_commands.CommandInvokeError):
            original = error.original

        try:
            lang = await guild_lang(interaction)
        except Exception:
            lang = "pt"

        if isinstance(error, app_commands.CommandOnCooldown):
            await _reply_ephemeral(
                interaction, t(lang, "cooldown_wait", seconds=max(1, round(error.retry_after)))
            )
            return
        if isinstance(error, app_commands.CheckFailure):
            # guild_only fora de servidor, permissão de Discord etc. — recusa
            # limpa, sem stack trace (não é um bug).
            await _reply_ephemeral(interaction, t(lang, "no_permission"))
            return

        cmd = interaction.command.qualified_name if interaction.command else "?"
        log.error(
            "comando /%s falhou (guild=%s user=%s)",
            cmd, interaction.guild_id, getattr(interaction.user, "id", "?"),
            exc_info=original,
        )
        await _reply_ephemeral(interaction, t(lang, "unexpected_error"))

    # tree.error(coro) faz exatamente esta atribuição por baixo dos panos.
    bot.tree.on_error = on_app_command_error

    async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
        # Não há comandos de prefixo registrados hoje — isto só evita que
        # mensagens casuais começando com "!" virem traceback no console.
        if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
            return
        log.error("comando de prefixo %s falhou", ctx.command, exc_info=error)

    # add_listener (não override): o handler default do discord.py se cala
    # sozinho quando existe um listener registrado.
    bot.add_listener(on_command_error, "on_command_error")

    # ── Botões e modais ──────────────────────────────────────────────────
    # O default de View.on_error/Modal.on_error só loga — o usuário vê
    # "Falha na interação" sem explicação. Trocamos o DEFAULT da classe por
    # um que também responde ephemeral; Views/Modais com on_error próprio
    # (MassinfoView, EnviarLogModal, …) continuam usando o deles — override
    # de subclasse vence o default da base.
    # ponytail: monkeypatch deliberado do default da lib — a alternativa era
    # uma classe-base repetida em todos os cogs, mesmo efeito com mais toque.
    async def _view_on_error(self, interaction, error, item):  # noqa: ANN001
        from cogs.general import guild_lang
        from i18n import t
        log.error("botão/select %r falhou (guild=%s)", getattr(item, "custom_id", item),
                  interaction.guild_id, exc_info=error)
        try:
            lang = await guild_lang(interaction)
        except Exception:
            lang = "pt"
        await _reply_ephemeral(interaction, t(lang, "unexpected_error"))

    async def _modal_on_error(self, interaction, error):  # noqa: ANN001
        from cogs.general import guild_lang
        from i18n import t
        log.error("modal %r falhou (guild=%s)", type(self).__name__,
                  interaction.guild_id, exc_info=error)
        try:
            lang = await guild_lang(interaction)
        except Exception:
            lang = "pt"
        await _reply_ephemeral(interaction, t(lang, "unexpected_error"))

    discord.ui.View.on_error = _view_on_error
    discord.ui.Modal.on_error = _modal_on_error
