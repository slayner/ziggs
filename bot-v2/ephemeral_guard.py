"""Auto-delete de mensagens ephemeral após 60s de inatividade.

Problema: o `delete_after` do discord.py é um asyncio.sleep simples que
NÃO reseta quando o usuário interage com a mensagem (clica botão/select),
e morre se o processo reiniciar. Mensagens ephemeral ficam vivas até o
Discord limpar por conta própria (pode ser 15min+).

Solução: um tracker por message_id. Cada ephemeral enviada é registrada e
ganha um timer de 60s em background;
quando dispara, apaga a mensagem ativa via webhook. Se o usuário
interage (botão/select) antes do timer, o callback reset o timer.

Sem persistência: se o bot reinicia, as mensagens ficam órfãs (o Discord
as limpa sozinho). O tracker só cobre o caso comum: ephemeral criada,
usuário fica interagindo, para de interagir → some em 60s.

Cada clique/modal tem um interaction token diferente, por isso o ID da
mensagem é a única identidade estável durante todo o fluxo. Mensagens
ephemeral distintas também precisam de timers distintos para não se
cancelarem e ficarem empilhadas.
"""
from __future__ import annotations

import asyncio
import logging

import discord

log = logging.getLogger(__name__)

_TTL = 60.0  # segundos de inatividade

# Cada origem (usuário + mensagem/comando) pode ter apenas um ephemeral ativo.
# Locks particionados evitam que dois cliques simultâneos passem pelo check antes
# de qualquer um registrar a mensagem nova.
_ORIGIN_LOCKS = tuple(asyncio.Lock() for _ in range(64))
_active: dict[int, tuple[asyncio.Task, discord.Webhook, tuple | None]] = {}
_origins: dict[tuple, int] = {}
_token_origins: dict[str, tuple] = {}
_blocked_tokens: set[str] = set()


def _origin_key(interaction: discord.Interaction) -> tuple | None:
    user_id = getattr(getattr(interaction, "user", None), "id", None)
    if user_id is None:
        return None
    message_id = getattr(getattr(interaction, "message", None), "id", None)
    if message_id is not None:
        tracked = _active.get(message_id)
        if tracked is not None and tracked[2] is not None:
            return tracked[2]
        return user_id, "message", message_id
    raw_data = getattr(interaction, "data", None)
    data = raw_data if isinstance(raw_data, dict) else {}
    command = data.get("name")
    return (user_id, "command", command) if command else None


def observe(interaction: discord.Interaction) -> tuple | None:
    """Associa o token do clique/defer à origem usada pelos followups."""
    origin = _origin_key(interaction)
    token = getattr(interaction, "token", None)
    if origin is not None and token:
        _token_origins[token] = origin
        asyncio.get_running_loop().call_later(
            15 * 60, lambda: _token_origins.pop(token, None),
        )
    return origin


def _schedule_delete(
    message_id: int, webhook: discord.Webhook, origin: tuple | None = None,
) -> None:
    """(Re)agenda o timer de auto-delete pra essa interação."""
    old = _active.get(message_id)
    if old is not None and not old[0].done():
        old[0].cancel()
    if origin is None and old is not None:
        origin = old[2]

    task = asyncio.create_task(_delete_after(message_id, webhook, origin))
    _active[message_id] = (task, webhook, origin)
    if origin is not None:
        _origins[origin] = message_id


async def _delete_after(
    message_id: int, webhook: discord.Webhook, origin: tuple | None,
) -> None:
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
        entry = _active.get(message_id)
        if entry is not None and entry[0] is asyncio.current_task():
            _active.pop(message_id, None)
            if origin is not None and _origins.get(origin) == message_id:
                _origins.pop(origin, None)


async def _clear_origin(origin: tuple | None) -> bool:
    """Apaga o ephemeral anterior antes de permitir outro da mesma origem."""
    if origin is None:
        return True
    message_id = _origins.get(origin)
    if message_id is None:
        return True
    entry = _active.pop(message_id, None)
    if entry is None:
        _origins.pop(origin, None)
        return True
    task, webhook, _ = entry
    if not task.done():
        task.cancel()
    try:
        await webhook.delete_message(message_id)
    except discord.NotFound:
        pass
    except discord.HTTPException:
        _schedule_delete(message_id, webhook, origin)
        return False
    if _origins.get(origin) == message_id:
        _origins.pop(origin, None)
    return True


def track(interaction: discord.Interaction, message_id: int) -> None:
    """Registra/atualiza a mensagem ephemeral ativa dessa interação e (re)
    agenda o auto-delete de 60s. Chamado depois de cada send/followup."""
    webhook = interaction.followup
    current = _active.get(message_id)
    origin = current[2] if current is not None else observe(interaction)
    _schedule_delete(message_id, webhook, origin)


def touch(interaction: discord.Interaction) -> None:
    """Reset do timer de inatividade — chamado quando o usuário interage
    (clica botão, seleciona menu) com a mensagem ephemeral ativa."""
    observe(interaction)
    message_id = getattr(getattr(interaction, "message", None), "id", None)
    if message_id is None:
        return
    entry = _active.get(message_id)
    if entry is None:
        return
    _, webhook, origin = entry
    _schedule_delete(message_id, webhook, origin)


def cleanup(interaction: discord.Interaction) -> None:
    """Cancela o timer — chamado quando a interação é explicitamente
    dispensada (cancelar/confirmar final)."""
    message_id = getattr(getattr(interaction, "message", None), "id", None)
    if message_id is None:
        return
    entry = _active.pop(message_id, None)
    if entry is not None and not entry[0].done():
        entry[0].cancel()
    if entry is not None and entry[2] is not None and _origins.get(entry[2]) == message_id:
        _origins.pop(entry[2], None)


# ── monkey-patch do discord.py ──────────────────────────────────────────────
# Intercepta todo `interaction.response.send_message(ephemeral=True)` e
# `interaction.followup.send(ephemeral=True)` pra trackear e agendar o
# auto-delete. 1 ponto central, 0 mudanças nos 95 call sites dos cogs.

_original_send = discord.interactions.InteractionResponse.send_message
_original_defer = discord.interactions.InteractionResponse.defer
_original_edit_original = discord.Interaction.edit_original_response
_original_followup_send = discord.Webhook.send


async def _patched_send(self, *args, **kwargs):
    ephemeral = kwargs.get("ephemeral", False)
    if not ephemeral:
        return await _original_send(self, *args, **kwargs)
    interaction = self._parent
    origin = observe(interaction)
    lock = _ORIGIN_LOCKS[hash(origin) % len(_ORIGIN_LOCKS)] if origin is not None else None

    async def send():
        if not await _clear_origin(origin):
            return await _original_defer(self)
        resp = await _original_send(self, *args, **kwargs)
        mid = getattr(resp, "message_id", None) if resp else None
        if mid is None:
            try:
                msg = await interaction.original_response()
                mid = msg.id
            except Exception:
                mid = None
        if mid is not None:
            _schedule_delete(mid, interaction.followup, origin)
        return resp

    if lock is None:
        return await send()
    async with lock:
        return await send()


async def _patched_defer(self, *args, **kwargs):
    interaction = self._parent
    origin = observe(interaction)
    if not kwargs.get("ephemeral", False) or origin is None:
        return await _original_defer(self, *args, **kwargs)
    lock = _ORIGIN_LOCKS[hash(origin) % len(_ORIGIN_LOCKS)]
    async with lock:
        if not await _clear_origin(origin):
            token = getattr(interaction, "token", None)
            if token:
                _blocked_tokens.add(token)
        return await _original_defer(self, *args, **kwargs)


async def _patched_edit_original(self, *args, **kwargs):
    message = await _original_edit_original(self, *args, **kwargs)
    token = getattr(self, "token", None)
    if token and token in _blocked_tokens:
        _blocked_tokens.discard(token)
        try:
            await self.followup.delete_message(message.id)
        except (discord.NotFound, discord.HTTPException):
            pass
        return message
    if message is not None and message.flags.ephemeral:
        track(self, message.id)
    return message


async def _patched_followup_send(self, *args, **kwargs):
    ephemeral = kwargs.get("ephemeral", False)
    # followup.send com wait=False (default) retorna None — sem message_id.
    # Forçar wait=True quando ephemeral pra podermos trackear e deletar.
    if ephemeral:
        kwargs["wait"] = True
    if not ephemeral:
        return await _original_followup_send(self, *args, **kwargs)
    origin = _token_origins.get(getattr(self, "token", None))
    lock = _ORIGIN_LOCKS[hash(origin) % len(_ORIGIN_LOCKS)] if origin is not None else None

    async def send():
        if not await _clear_origin(origin):
            return None
        msg = await _original_followup_send(self, *args, **kwargs)
        if msg is not None and hasattr(msg, "id"):
            _schedule_delete(msg.id, self, origin)
        return msg

    if lock is None:
        return await send()
    async with lock:
        return await send()


def install() -> None:
    """Aplica os patches. Chamado uma vez no startup do bot (main.py)."""
    discord.interactions.InteractionResponse.send_message = _patched_send
    discord.interactions.InteractionResponse.defer = _patched_defer
    discord.Interaction.edit_original_response = _patched_edit_original
    discord.Webhook.send = _patched_followup_send
