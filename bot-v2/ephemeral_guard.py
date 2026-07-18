"""Auto-delete de mensagens ephemeral após 60s de inatividade.

Problema: o `delete_after` do discord.py é um asyncio.sleep simples que
NÃO reseta quando o usuário interage com a mensagem (clica botão/select),
e morre se o processo reiniciar. Mensagens ephemeral ficam vivas até o
Discord limpar por conta própria (pode ser 15min+).

Solução: um tracker por interaction token. Cada ephemeral enviada é
registrada com o message_id ativo. Um timer de 60s roda em background;
quando dispara, apaga a mensagem ativa via webhook. Se o usuário
interage (botão/select) antes do timer, o callback reset o timer.

Sem persistência: se o bot reinicia, as mensagens ficam órfãs (o Discord
as limpa sozinho). O tracker só cobre o caso comum: ephemeral criada,
usuário fica interagindo, para de interagir → some em 60s.

 ponytail: tracker por interaction token (não por message_id) — quando
 _replace_ephemeral apaga a original e manda followup, o token é o
 mesmo, só atualiza o message_id ativo. Assim o timer apaga a versão
 mais recente, não uma mensagem já apagada.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord

log = logging.getLogger(__name__)

_TTL = 60.0  # segundos de inatividade

# {interaction_token: (task, message_id, webhook)}
_active: dict[str, tuple[asyncio.Task, int, discord.Webhook]] = {}


def _schedule_delete(token: str, message_id: int, webhook: discord.Webhook) -> None:
    """(Re)agenda o timer de auto-delete pra essa interação."""
    old = _active.get(token)
    if old is not None and not old[0].done():
        old[0].cancel()

    task = asyncio.create_task(_delete_after(token, message_id, webhook))
    _active[token] = (task, message_id, webhook)


async def _delete_after(token: str, message_id: int, webhook: discord.Webhook) -> None:
    try:
        await asyncio.sleep(_TTL)
        await webhook.delete_message(message_id)
    except (discord.NotFound, discord.HTTPException):
        pass  # já apagada / ephemeral expirou / webhook inválido
    except asyncio.CancelledError:
        pass  # reset ou cleanup
    except Exception as e:
        log.debug("ephemeral_auto_delete: erro apagando %s: %s", message_id, e)
    finally:
        # Só remove se a entry atual é ESTA task — touch/cleanup podem ter
        # trocado a entry antes do finally do task cancelado rodar.
        entry = _active.get(token)
        if entry is not None and entry[0] is asyncio.current_task():
            _active.pop(token, None)


def track(interaction: discord.Interaction, message_id: int) -> None:
    """Registra/atualiza a mensagem ephemeral ativa dessa interação e (re)
    agenda o auto-delete de 60s. Chamado depois de cada send/followup."""
    token = interaction.token
    if not token:
        return
    webhook = interaction.followup.webhook
    _schedule_delete(token, message_id, webhook)


def touch(interaction: discord.Interaction) -> None:
    """Reset do timer de inatividade — chamado quando o usuário interage
    (clica botão, seleciona menu) com a mensagem ephemeral ativa."""
    token = interaction.token
    if not token:
        return
    entry = _active.get(token)
    if entry is None:
        return
    _, message_id, webhook = entry
    _schedule_delete(token, message_id, webhook)


def cleanup(token: str) -> None:
    """Cancela o timer — chamado quando a interação é explicitamente
    dispensada (cancelar/confirmar final)."""
    entry = _active.pop(token, None)
    if entry is not None and not entry[0].done():
        entry[0].cancel()


# ── monkey-patch do discord.py ──────────────────────────────────────────────
# Intercepta todo `interaction.response.send_message(ephemeral=True)` e
# `interaction.followup.send(ephemeral=True)` pra trackear e agendar o
# auto-delete. 1 ponto central, 0 mudanças nos 95 call sites dos cogs.

_original_send = discord.interactions.InteractionResponse.send_message
_original_followup_send = discord.Webhook.send


async def _patched_send(self, *args, **kwargs):
    ephemeral = kwargs.get("ephemeral", False)
    resp = await _original_send(self, *args, **kwargs)
    if ephemeral:
        interaction = self._parent
        mid = getattr(resp, "message_id", None) if resp else None
        if mid is None:
            try:
                msg = await interaction.original_response()
                mid = msg.id
            except Exception:
                mid = None
        if mid is not None:
            track(interaction, mid)
    return resp


async def _patched_followup_send(self, *args, **kwargs):
    ephemeral = kwargs.get("ephemeral", False)
    # followup.send com wait=False (default) retorna None — sem message_id.
    # Forçar wait=True quando ephemeral pra podermos trackear e deletar.
    if ephemeral:
        kwargs["wait"] = True
    msg = await _original_followup_send(self, *args, **kwargs)
    if ephemeral and msg is not None:
        # webhook não tem referência direta à interaction; o token está no
        # webhook (Interaction.followup.webhook tem token), mas precisamos
        # do token da interaction. O caller (_replace_ephemeral) chama
        # track() explicitamente com a interaction — aqui é fallback pros
        # casos de followup.send direto sem _replace_ephemeral.
        token = getattr(self, "token", None)
        if token and hasattr(msg, "id"):
            _schedule_delete(token, msg.id, self)
    return msg


def install() -> None:
    """Aplica os patches. Chamado uma vez no startup do bot (main.py)."""
    discord.interactions.InteractionResponse.send_message = _patched_send
    discord.Webhook.send = _patched_followup_send