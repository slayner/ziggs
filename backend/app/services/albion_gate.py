"""Fila de prioridade para requests ao API gameinfo do Albion.

O site ficava lento ao abrir perfis porque 14 tasks de background saturavam o
gameinfo (~15 concorrentes) e os requests on-demand de perfil competiam no
mesmo pool. Aqui centralizamos a concorrência em 2 pools:

- **reserved** (3 slots): alta prioridade (perfis, registers, claims, regear).
  Background NÃO usa → perfil nunca espera atrás de backfill/sweeper.
- **bg** (6 slots, heap por prioridade): processamento de batalhas e outros.
  Maior prioridade é servida primeiro quando um slot libera.

Total 9 (era ~15) — abaixo do limiar de 429 (~24) e deixa headroom pra serving.

Concorrência sozinha não limita TAXA: 9 slots com requests rápidos (ida-e-volta
de ~50-100ms) ainda sustentam dezenas de requests/segundo, e foi exatamente
isso que gerou 429 em cascata mesmo com o cap de concorrência já em vigor (ver
battle_tracker.py). ``_rate_limiter`` abaixo complementa com um teto de
requests/segundo agregado — mesma fila de prioridade dos pools, só que o
"slot" é devolvido por tempo (após ``1/rate`` segundos) em vez de quando o
corpo do ``async with`` termina.

**Uso:** tasks/routes envolvem o corpo com ``async with albion_scope(P):``; cada
``client.get`` contra gameinfo envolve com ``async with slot():``. A prioridade
vive num ``ContextVar`` — funções compartilhadas (fetch_battle_detail,
sync_player_kills, …) não precisam receber prioridade por parâmetro, ela flui
do scope do caller.
"""
from __future__ import annotations

import asyncio
import contextvars
import heapq
import itertools
from contextlib import asynccontextmanager

# Prioridades (lower = mais alto). Espaçadas pras SMALL = ELIGIBLE+1.
PROFILE = 0
BOT_REGISTER = 1
CLAIM_VERIFY = 1      # user: mesmo nível dos registers (membro esperando)
REGEAR_RECOG = 1      # user: mesmo nível dos registers (membro esperando)
NEW_ELIGIBLE = 10
NEW_SMALL = 11
OLD_ELIGIBLE = 12
OLD_SMALL = 13
OTHER = 20

ELIGIBLE_MIN_PLAYERS = 20  # tunável: >= isto = "elegível" (ZvZ/grande), < = "pequeno"

_HIGH_MAX = 1          # prio <= _HIGH_MAX → reserved pool
_RESERVED_SLOTS = 3    # user confirmou
_BG_SLOTS = 6          # user confirmou (total 9, era ~15)

_current = contextvars.ContextVar("albion_prio", default=OTHER)


@asynccontextmanager
async def albion_scope(priority: int):
    """Seta a prioridade corrente pra tudo que rodar dentro do bloco (incluindo
    funções chamadas que usam ``slot()``). Aninhável — scope interno sobrepõe."""
    token = _current.set(priority)
    try:
        yield
    finally:
        _current.reset(token)


class _PriorityPool:
    """Pool de N slots; waiters servidos por (priority, seq) — maior prioridade
    primeiro quando um slot libera. asyncio é single-thread cooperativo: leitura
    e escrita do estado sem await no meio → sem lock."""

    def __init__(self, n: int):
        self._n = n
        self._in_use = 0
        self._waiters: list[tuple[int, int, asyncio.Future]] = []
        self._seq = itertools.count()

    async def acquire(self, prio: int) -> None:
        if self._in_use < self._n:
            self._in_use += 1
            return
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (prio, next(self._seq), fut))
        try:
            await fut
        except asyncio.CancelledError:
            # ponytail: O(n) raro (só em cancelamento); esperar heap handle seria mais complexo.
            self._waiters = [w for w in self._waiters if w[2] is not fut]
            raise

    def release(self) -> None:
        self._in_use -= 1
        while self._waiters:
            prio, _, fut = self._waiters[0]
            if fut.done():  # cancelado enquanto esperava
                heapq.heappop(self._waiters)
                continue
            heapq.heappop(self._waiters)
            self._in_use += 1
            fut.set_result(True)
            return


_reserved = _PriorityPool(_RESERVED_SLOTS)
_bg = _PriorityPool(_BG_SLOTS)


class _RateLimiter:
    """Teto de requests/segundo agregado (todas as prioridades, um recurso só
    — é o que a Albion vê do nosso lado). Reaproveita _PriorityPool: em vez
    de devolver o "slot" quando o caller termina (concorrência), devolve
    ``1/rate`` segundos depois de adquirido — vira limite de taxa, com a
    mesma fila por prioridade (heap) já testada acima. ``burst`` = quantos
    requests podem estar "em resfriamento" ao mesmo tempo."""

    def __init__(self, rate: float, burst: int):
        self._pool = _PriorityPool(burst)
        self._interval = 1.0 / rate

    async def acquire(self, prio: int) -> None:
        await self._pool.acquire(prio)
        asyncio.get_running_loop().call_later(self._interval, self._pool.release)


# ponytail: Albion não publica limite oficial pro gameinfo público.
# 10/10 e depois 5/5 ainda deixavam passar 429 — e o padrão nos logs (muitas
# batalhas DIFERENTES falhando na 1ª página quase juntas, sobrevivendo às 3
# tentativas de retry) aponta pra sensibilidade a RAJADA instantânea, não só
# taxa sustentada: burst=5 deixava 5 requests saírem no mesmíssimo instante
# antes de qualquer espaçamento. Burst cortado pra 1 — zero rajada da nossa
# parte, todo request espaçado em pelo menos 1/rate segundos, sem exceção.
# Ajustar rate pra baixo de novo se 429 persistir (battle_tracker/
# battle_reprocessor logam toda falha), pra cima se um período limpo mostrar folga.
ALBION_RATE_LIMIT = 3   # requests/segundo agregado, todas as prioridades
ALBION_RATE_BURST = 1   # sem rajada — cada request espaçado em 1/rate segundos

_rate_limiter = _RateLimiter(ALBION_RATE_LIMIT, ALBION_RATE_BURST)


@asynccontextmanager
async def slot():
    """Adquire um slot do pool correspondente à prioridade corrente e um
    token do limitador de taxa agregado. Envolver CADA ``client.get`` contra
    gameinfo com isto."""
    prio = _current.get()
    pool = _reserved if prio <= _HIGH_MAX else _bg
    await pool.acquire(prio)
    try:
        await _rate_limiter.acquire(prio)
        yield
    finally:
        pool.release()


def battle_priority(battle, *, is_new: bool) -> int:
    """Tier de batalha pra prioridade no bg pool. ``is_new`` vem do caller
    (sync_recent=True, backfill=False, retry_stuck/reprocessor = not _is_frozen).
    ``players_total`` já é conhecido (light upsert roda antes do deep)."""
    base = NEW_ELIGIBLE if is_new else OLD_ELIGIBLE
    return base if (battle.players_total or 0) >= ELIGIBLE_MIN_PLAYERS else base + 1


if __name__ == "__main__":  # ponytail: 1 self-check runnable, sem framework
    async def _hold(prio: int):
        """Adquire um slot com a prioridade dada e devolve o CM (pra liberar depois)."""
        cm = slot()
        await cm.__aenter__()
        return cm

    async def _main():
        # (a)-(c) testam só os pools de concorrência — troca o rate limiter
        # global por um bem generoso pra essas asserções de timing não
        # dependerem do burst/rate de produção (testado isolado no (e)).
        global _rate_limiter
        _rate_limiter = _RateLimiter(rate=1000, burst=1000)

        # (a) bg pool cheio: 7º OTHER bloqueia
        holds = [await _hold(OTHER) for _ in range(_BG_SLOTS)]
        seventh_done = asyncio.Event()

        async def _seventh():
            async with slot():  # OTHER → bg pool, deve bloquear (cheio)
                seventh_done.set()

        t = asyncio.create_task(_seventh())
        await asyncio.sleep(0.01)
        assert not seventh_done.is_set(), "7º bg deveria estar bloqueado"
        await holds[0].__aexit__(None, None, None)  # libera 1 → 7º entra
        await asyncio.wait_for(t, timeout=1)
        assert seventh_done.is_set()
        for cm in holds[1:]:
            await cm.__aexit__(None, None, None)

        # (b) PROFILE ignora bg cheio: reserved pool separado → entra imediato
        holds = [await _hold(OTHER) for _ in range(_BG_SLOTS)]  # enche bg
        profile_done = asyncio.Event()

        async def _profile():
            async with albion_scope(PROFILE):
                async with slot():
                    profile_done.set()

        await asyncio.wait_for(asyncio.create_task(_profile()), timeout=1)
        assert profile_done.is_set(), "PROFILE deveria entrar pelo reserved pool"
        for cm in holds:
            await cm.__aexit__(None, None, None)

        # (c) prioridade entre waiters bg: NEW_ELIGIBLE(10) vence OTHER(20)
        holds = [await _hold(OTHER) for _ in range(_BG_SLOTS)]  # bg cheio
        started: list[int] = []
        gate = asyncio.Event()  # segura o slot do admitido até depois da asserção

        async def _waiter(p):
            async with albion_scope(p):
                async with slot():
                    started.append(p)
                    await gate.wait()  # não solta o slot cedo → não admite o próximo

        t_other = asyncio.create_task(_waiter(OTHER))
        t_new = asyncio.create_task(_waiter(NEW_ELIGIBLE))
        await asyncio.sleep(0.01)
        assert started == [], f"nada deveria ter entrado ainda, veio {started}"
        await holds[0].__aexit__(None, None, None)  # libera 1 → NEW_ELIGIBLE entra
        await asyncio.sleep(0.01)
        assert started == [NEW_ELIGIBLE], f"NEW_ELIGIBLE deveria entrar primeiro, veio {started}"
        gate.set()  # libera o admitido → solta slot → OTHER entra
        await asyncio.wait_for(asyncio.gather(t_new, t_other), timeout=1)
        for cm in holds[1:]:
            await cm.__aexit__(None, None, None)

        # (d) battle_priority
        class B:
            def __init__(self, n): self.players_total = n
        assert battle_priority(B(50), is_new=True) == NEW_ELIGIBLE
        assert battle_priority(B(5), is_new=True) == NEW_SMALL
        assert battle_priority(B(50), is_new=False) == OLD_ELIGIBLE
        assert battle_priority(B(5), is_new=False) == OLD_SMALL
        assert battle_priority(B(None), is_new=True) == NEW_SMALL  # None → 0 → small

        # (e) rate limiter: burst dispara na hora, o (burst+1)º espera ~1/rate
        rl = _RateLimiter(rate=20, burst=3)
        t0 = asyncio.get_running_loop().time()
        for _ in range(3):
            await rl.acquire(OTHER)
        assert asyncio.get_running_loop().time() - t0 < 0.05, "burst deveria ser imediato"
        await rl.acquire(OTHER)
        elapsed = asyncio.get_running_loop().time() - t0
        assert elapsed >= 1 / 20, f"4º request deveria esperar ~1/rate, levou {elapsed}s"

        print("albion_gate OK")

    asyncio.run(_main())