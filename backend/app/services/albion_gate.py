"""Fila de prioridade para requests ao API gameinfo do Albion.

O site ficava lento ao abrir perfis porque 14 tasks de background saturavam o
gameinfo (~15 concorrentes) e os requests on-demand de perfil competiam no
mesmo pool. Aqui centralizamos a concorrência em 2 pools:

- **reserved** (16 slots): alta prioridade (perfis, registers, claims, regear).
  Background NÃO usa → perfil nunca espera atrás de backfill/sweeper.
- **bg** (20 slots, heap por prioridade): processamento de batalhas e outros.
  Maior prioridade é servida primeiro quando um slot libera.

``_rate_limiter`` abaixo é o teto REAL de carga na Albion (requests/segundo
agregado) — mesma fila de prioridade dos pools, só que o "slot" é devolvido por
tempo (após ``1/rate`` segundos) em vez de quando o corpo do ``async with``
termina. Concorrência sozinha não limita TAXA (9 slots com requests de ~100ms
sustentam dezenas de req/s — foi o que gerou 429 em cascata antes do rate
limiter existir, ver battle_tracker.py).

**Dimensionamento dos pools (por que 16/20, não 3/6):** o slot de concorrência é
segurado durante a request INTEIRA (read timeout até 40s). Com N slots e request
de T segundos, a vazão máx do pool é N/T. Se N/T < rate, o POOL vira o gargalo —
não o rate limiter — e a taxa efetiva cai ABAIXO do limite mesmo com milhares de
requests pendentes (era o caso com 3/6: bg 6/40s = 0.15 req/s < rate; reserved
3/40s = 0.075). O pool só precisa cobrir a concorrência NORMAL no rate atual
(rate × T_típico ≈ 0.7 × ~4s ≈ 3) com folga; 16/20 sobram. Se a Albion degrada e
tudo bate no timeout de 40s, o pool enche e a vazão cai — backoff de fato, certo
quando a API sofre. Pool maior NÃO aumenta a carga na Albion: o rate limiter
segue liberando no mesmo ritmo; só evita que request lenta trave a fila.

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
import logging
from contextlib import asynccontextmanager

log = logging.getLogger(__name__)

# Prioridades (lower = mais alto). Cadeia de FUNDO (pedido do dono): batalha
# NOVA primeiro, depois o WARMER (perfis), depois batalha ANTIGA — o warmer não
# pode furar a descoberta de batalha nova (era o que derrubava "batalhas
# encontradas": o warm de fundo rodava ACIMA das batalhas, em PROFILE=0).
# Espaçadas pras SMALL = ELIGIBLE+1 (ver battle_priority).
PROFILE = 0          # perfil USER-FACING: cold-load da página + ⟳ manual (humano esperando)
BOT_REGISTER = 1
CLAIM_VERIFY = 1      # user: mesmo nível dos registers (membro esperando)
REGEAR_RECOG = 1      # user: mesmo nível dos registers (membro esperando)
# ponytail: verificação recorrente de guilda — abaixo das pesquisas emergentes
# (perfil/register/claim, user-facing) mas acima da descoberta de batalha nova.
# Poder escalar pro scan_dispatcher se a lista crescer muito, pelo comentário
# em guild_verifier.run_forever — enquanto isso, cabe folgada no pool reserved.
GUILD_VERIFY = 5
NEW_ELIGIBLE = 10     # batalha NOVA (descoberta/deep-fetch) — topo da cadeia de fundo
NEW_SMALL = 11
WARM = 12            # profile_warmer de FUNDO (companion warm + backfill de participantes)
OLD_ELIGIBLE = 14     # batalha ANTIGA (backfill) — abaixo do warmer
OLD_SMALL = 15
OTHER = 20            # sweeper / diversos — piso

ELIGIBLE_MIN_PLAYERS = 20  # tunável: >= isto = "elegível" (ZvZ/grande), < = "pequeno"

_HIGH_MAX = 1          # prio <= _HIGH_MAX → reserved pool
# Slots segurados durante a request INTEIRA (read timeout até 40s). O rate
# limiter (adaptativo, teto 0.7/s) é o teto de carga na Albion; o pool só precisa
# cobrir a concorrência NORMAL nesse rate (rate × T_típico ≈ 0.7 × ~4s ≈ 3, com
# folga larga) pra não virar o gargalo. 3/6 (antigo) travava a ~0.15/0.075 req/s
# mesmo com a fila cheia. 16/20 dão folga de sobra; se a Albion degradar
# (requests batendo no timeout de 40s) o pool enche e a vazão cai sozinha — um
# backoff de fato, que é o certo quando a API sofre. Subiu o teto? Suba estes.
_RESERVED_SLOTS = 16
_BG_SLOTS = 20

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
    """Teto de requests/segundo agregado (um recurso só — é o que a Albion vê do
    nosso lado), ADAPTATIVO. Reaproveita _PriorityPool: em vez de devolver o
    "slot" quando o caller termina (concorrência), devolve ``1/rate`` segundos
    depois de adquirido — vira limite de taxa, com a mesma fila por prioridade
    (heap) já testada acima. O ``rate`` NÃO é fixo: sobe/desce por AIMD conforme
    os status da Albion (ver ``observe`` e RATE_MAX). ``burst`` = quantos
    requests podem estar "em resfriamento" ao mesmo tempo."""

    def __init__(self, rate: float, burst: int):
        self._pool = _PriorityPool(burst)
        self._rate = rate
        self._last_decrease = float("-inf")  # 1º recuo sempre permitido

    async def acquire(self, prio: int) -> None:
        await self._pool.acquire(prio)
        # lê o rate ATUAL a cada acquire — mudança do AIMD vale já no próximo tick.
        asyncio.get_running_loop().call_later(1.0 / self._rate, self._pool.release)

    def observe(self, status: int) -> None:
        """Feedback de UM response da Albion. 429/502/503/504 = sobrecarga →
        recua; 2xx → recupera de leve rumo ao teto."""
        if status in _RATE_ERROR_CODES:
            self._decrease()
        elif 200 <= status < 300 and self._rate < RATE_MAX:
            self._rate = min(RATE_MAX, self._rate + RATE_INCREASE)

    def _decrease(self) -> None:
        # No máx um recuo por rodada (1/rate): uma cascata de N erros concorrentes
        # (típico num lote de batalhas) conta como UM backoff, não N — senão 20
        # respostas 504 quase juntas derrubariam a taxa ao piso de uma vez.
        now = asyncio.get_running_loop().time()
        if now - self._last_decrease < 1.0 / self._rate:
            return
        self._last_decrease = now
        new_rate = max(RATE_MIN, self._rate * RATE_DECREASE)
        if new_rate != self._rate:
            log.info("albion_gate: sobrecarga (Albion) — recuo %.2f → %.2f req/s",
                     self._rate, new_rate)
            self._rate = new_rate


# ── Rate limiter ADAPTATIVO (AIMD) ─────────────────────────────────────────
# A Albion não publica limite oficial pro gameinfo, e o teto sustentável varia
# com a saúde da API. Um número fixo sempre erra: 3/s dava 504; 0.2/s era folga
# demais (nem com milhares pendentes chegávamos perto — o gargalo era o POOL).
# Então a taxa se AUTO-AJUSTA: parte do TETO e recua a cada sinal de sobrecarga,
# recuperando de volta enquanto a Albion responde 200. Alimentado pelos status
# REAIS via response hook do make_client (observe_response) — nenhum call site
# muda. Histórico do burst: 5 deixava 5 requests saírem no mesmo instante e
# gerava 429 em cascata; cortado pra 1 → zero rajada, tudo espaçado por 1/rate.
#   - Teto (RATE_MAX): pico PERMITIDO. Nunca passa disso — é o knob principal.
#   - Piso (RATE_MIN): não trava de vez nem numa Albion ruim.
#   - Sucesso (2xx): recuperação ADITIVA de leve (+RATE_INCREASE por resposta).
#   - Sobrecarga (429/502/503/504): recuo MULTIPLICATIVO (×RATE_DECREASE), no
#     máx um por rodada (1/rate) — cascata de erros concorrentes = 1 backoff.
# Sawtooth clássico (TCP): converge no rate sustentável logo abaixo do teto.
RATE_MAX = 0.7        # teto: pico permitido (req/s agregado, todas as prioridades)
RATE_MIN = 0.1        # piso: nunca recua abaixo disso
RATE_INCREASE = 0.01  # +req/s por resposta 2xx (recuperação gradual até o teto)
RATE_DECREASE = 0.5   # ×req/s por rodada com sobrecarga (backoff)
_RATE_ERROR_CODES = frozenset({429, 502, 503, 504})  # sinais de sobrecarga da Albion
ALBION_RATE_BURST = 1  # sem rajada — cada request espaçado em 1/rate segundos

_rate_limiter = _RateLimiter(RATE_MAX, ALBION_RATE_BURST)  # parte do teto, recua sob erro


def observe_response(status: int) -> None:
    """Alimenta o rate limiter adaptativo com o status de UM response do
    gameinfo. Chamado pelo response hook do make_client (player_tracker) — todo
    request ao gameinfo passa por ele, então o feedback cobre o tráfego todo sem
    nenhum call site precisar reportar nada."""
    _rate_limiter.observe(status)


def rate_status() -> dict:
    """Estado corrente do rate limiter adaptativo — pro dashboard de ops (ele é
    outro processo, lê isto via HTTP). Só leitura do estado em memória, sem
    efeito colateral. `rate` sobe até `ceiling` e recua sob sobrecarga; `queue`
    = requests bloqueados esperando slot/rate agora."""
    return {
        "rate": round(_rate_limiter._rate, 3),
        "ceiling": RATE_MAX,
        "floor": RATE_MIN,
        "queue": queue_depth(OTHER),
    }


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


def queue_depth(prio: int = OTHER) -> int:
    """Quantos requests serão servidos ANTES de um request de prioridade `prio`
    — NÃO o total global. Perfil (refresh/cold-load) roda em PROFILE (0), que
    FURA a fila: os milhares de requests de background (OTHER=20) não passam na
    frente dele. Somá-los dava um número enorme e mentiroso ('1208 na fila'
    enquanto o refresh já estava sendo servido). Conta só os waiters que empatam
    ou superam `prio` (prio_do_waiter <= prio, pois heap serve por prioridade
    crescente) no pool relevante àquela prioridade + no rate limiter
    (compartilhado). Lê os heaps direto — contar não precisa de ordem; pula
    future cancelado (mesma checagem do release)."""
    def ahead(pool: _PriorityPool) -> int:
        return sum(1 for wp, _s, fut in pool._waiters if wp <= prio and not fut.done())
    own_pool = _reserved if prio <= _HIGH_MAX else _bg
    return ahead(own_pool) + ahead(_rate_limiter._pool)


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

        # (d) battle_priority + cadeia de prioridade (novas > warmer > antigas)
        assert PROFILE < NEW_ELIGIBLE < WARM < OLD_ELIGIBLE < OTHER, "cadeia de prioridade quebrada"

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

        # (f) queue_depth é POR PRIORIDADE: waiters OTHER no bg não contam pra um
        # request PROFILE (fura a fila) — era o bug do '1208 na fila' no refresh.
        holds = [await _hold(OTHER) for _ in range(_BG_SLOTS)]  # bg cheio
        gate2 = asyncio.Event()

        async def _bgwaiter():
            async with albion_scope(OTHER):
                async with slot():  # bloqueia no bg (cheio)
                    await gate2.wait()

        bgs = [asyncio.create_task(_bgwaiter()) for _ in range(3)]
        await asyncio.sleep(0.01)
        assert queue_depth(OTHER) >= 3, f"OTHER deve ver os 3 waiters, viu {queue_depth(OTHER)}"
        assert queue_depth(PROFILE) == 0, f"PROFILE não espera atrás de OTHER, viu {queue_depth(PROFILE)}"
        gate2.set()  # abre o gate ANTES de liberar os slots (sem corrida)
        for cm in holds:  # libera o bg → os 3 waiters entram e terminam na hora
            await cm.__aexit__(None, None, None)
        await asyncio.wait_for(asyncio.gather(*bgs), timeout=1)

        # (g) rate limiter AIMD: 2xx recupera até o teto, erro recua, cascata na
        # mesma rodada = 1 backoff, e clamp no piso.
        rl2 = _RateLimiter(rate=RATE_MAX, burst=1)
        rl2.observe(200)                                  # já no teto → não passa
        assert rl2._rate == RATE_MAX, rl2._rate
        rl2.observe(504)                                  # recua ×RATE_DECREASE
        after1 = RATE_MAX * RATE_DECREASE
        assert rl2._rate == after1, rl2._rate
        rl2.observe(504)                                  # 2º erro na mesma rodada: ignorado
        assert rl2._rate == after1, "cascata deve contar como 1 recuo"
        rl2.observe(200)                                  # recupera de leve
        assert rl2._rate == min(RATE_MAX, after1 + RATE_INCREASE), rl2._rate
        for _ in range(40):                               # muitos erros → clamp no piso
            rl2._last_decrease = float("-inf")            # fura o cooldown pra testar o clamp
            rl2.observe(504)
        assert rl2._rate == RATE_MIN, rl2._rate

        print("albion_gate OK")

    asyncio.run(_main())