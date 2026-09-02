"""Helper de timeout para chamadas ao Discord API em loops de background.

O discord.py NÃO tem timeout default no channel.send/message.edit/fetch_message
— se o Discord rate-limitar ou a rede falhar silenciosamente, o await fica
preso pra sempre. Em loops que seguram um asyncio.Lock por guilda, isso
paralisa a guilda inteira até o restart do processo.

Uso: `await dtimeout(channel.send(...))` em vez de `await channel.send(...)`.

Exceções de timeout/rede viram None (caller trata) ou você usa o padrão
try/except `_SKIP_EXC` do audit_log.py.

Ver diagnosing-bugs: 27 chamadas ao Discord API nos loops de background do
bot-v2 não tinham timeout; o sintoma era "awaits presos, só se soltam no
restart, logs/juicy-kills/eventos acumulados aparecem de uma vez"."""
from __future__ import annotations

import asyncio
from typing import Any

import discord

# Discord API calls podem pendurar sem timeout — teto de 15s transforma
# travamento num skip; próximo tick tenta de novo. Mesmo valor de audit_log.py.
API_TIMEOUT = 15

# Exceções que significam "falha de rede/Discord" (incl. timeout) — o loop
# skipa a guilda atual e segue pra próxima em vez de morrer.
SKIP_EXC = (discord.NotFound, discord.Forbidden, discord.HTTPException, asyncio.TimeoutError)


async def dtimeout(coro: Any, *, timeout: float = API_TIMEOUT) -> Any:
    """Envolve uma coroutine Discord API em asyncio.wait_for com timeout.

    Uso: `msg = await dtimeout(channel.send(...))`.
    Raises asyncio.TimeoutError no timeout (caller pega com SKIP_EXC)."""
    return await asyncio.wait_for(coro, timeout=timeout)