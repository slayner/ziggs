"""Fila de prioridade para requests ao API gameinfo do Albion.

O site ficava lento ao abrir perfis porque tasks de background saturavam o
gameinfo e os requests on-demand competiam no mesmo pool. Aqui centralizamos a
concorrência em camadas:

- **reserved** (16 slots): alta prioridade (perfis, registers, claims, regear).
  Background NÃO usa → perfil nunca espera atrás de backfill/sweeper.
- **bg** (20 slots, heap por prioridade): processamento de batalhas e outros.
   Maior prioridade é servida primeiro quando um slot libera.
- **host** (2 slots por host): impede que requests lentos de fundo se acumulem
  na Albion. Um slot adicional e exclusivo de **embed** mantém o primeiro card
  disponível mesmo quando os dois normais estão em voo.

``_rate_limiter`` abaixo é o teto REAL de carga na Albion (requests/segundo
agregado) — mesma fila de prioridade dos pools, só que o "slot" é devolvido por
tempo (após ``1/rate`` segundos) em vez de quando o corpo do ``async with``
termina. Concorrência sozinha não limita TAXA (9 slots com requests de ~100ms
sustentam dezenas de req/s — foi o que gerou 429 em cascata antes do rate
limiter existir, ver battle_tracker.py).

Os pools globais mantêm o backend responsivo; o limite efetivo de requests em
voo fica no host. Assim, uma degradação de 40s na Albion não transforma o rate
normal em dezenas de conexões penduradas no mesmo servidor.

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
EMBED = -1           # preview Discord: crawler espera poucos segundos
PROFILE = 0          # perfil USER-FACING: cold-load da página + ⟳ manual (humano esperando)
BOT_REGISTER = 1
CLAIM_VERIFY = 1      # user: mesmo nível dos registers (membro esperando)
REGEAR_RECOG = 1      # user: mesmo nível dos registers (membro esperando)
# ponytail: verificação recorrente de guilda — abaixo das pesquisas emergentes
# (perfil/register/claim, user-facing) mas acima da descoberta de batalha nova.
# Poder escalar pro scan_dispatcher se a lista crescer muito, pelo comentário
# em guild_verifier.run_forever — enquanto isso, cabe folgada no pool reserved.
GUILD_VERIFY = 5
LINK_PROFILE = 6     # perfil frio aberto por link público
NEW_ELIGIBLE = 10     # batalha NOVA (descoberta/deep-fetch) — topo da cadeia de fundo
NEW_SMALL = 11
WARM = 12            # profile_warmer de FUNDO (companion warm + backfill de participantes)
LOW_WARM = 13       # warm de membros de guilda após refresh — abaixo do backfill normal
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
# A Albion passa a atrasar respostas quando muitas chamadas ficam penduradas.
# Dois fluxos normais preservam vazão em condições saudáveis; o terceiro slot é
# exclusivo de EMBED e nunca é consumido pelo backfill.
_HOST_SLOTS = 1
_EMBED_HOST_SLOTS = 1

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
_host_pools: dict[str, _PriorityPool] = {}
_embed_host_pools: dict[str, _PriorityPool] = {}
_default_host_pool = _PriorityPool(_HOST_SLOTS)
_default_embed_host_pool = _PriorityPool(_EMBED_HOST_SLOTS)


class _RateLimiter:
    """Teto de requests/segundo agregado (um recurso só — é o que a Albion vê do
    nosso lado), ADAPTATIVO. Reaproveita _PriorityPool: em vez de devolver o
    "slot" quando o caller termina (concorrência), devolve ``1/rate`` segundos
    depois de adquirido — vira limite de taxa, com a mesma fila por prioridade
    (heap) já testada acima. O ``rate`` NÃO é fixo: sobe/desce por AIMD conforme
    os status da Albion (ver ``observe`` e RATE_MAX). ``burst`` = quantos
    requests podem estar "em resfriamento" ao mesmo tempo."""

    def __init__(self, rate: float, burst: int, *, min_rate: float | None = None):
        self._pool = _PriorityPool(burst)
        self._rate = rate
        self._min_rate = RATE_MIN if min_rate is None else min_rate
        self._last_decrease = float("-inf")  # 1º recuo sempre permitido
        self._background_paused = False

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
            self._update_background_mode()

    def _decrease(self) -> None:
        # No máx um recuo por rodada (1/rate): uma cascata de N erros concorrentes
        # (típico num lote de batalhas) conta como UM backoff, não N — senão 20
        # respostas 504 quase juntas derrubariam a taxa ao piso de uma vez.
        now = asyncio.get_running_loop().time()
        if now - self._last_decrease < 1.0 / self._rate:
            return
        self._last_decrease = now
        new_rate = max(self._min_rate, self._rate * RATE_DECREASE)
        if new_rate != self._rate:
            log.info("albion_gate: sobrecarga (Albion) — recuo %.2f → %.2f req/s",
                     self._rate, new_rate)
            self._rate = new_rate
            self._update_background_mode()

    def _update_background_mode(self) -> None:
        if not self._background_paused and self._rate <= BACKGROUND_PAUSE_RATE:
            self._background_paused = True
            log.warning(
                "albion_gate: manutenção de fundo pausada até recuperar acima de %.2f req/s",
                BACKGROUND_RESUME_RATE,
            )
        elif self._background_paused and self._rate >= BACKGROUND_RESUME_RATE:
            self._background_paused = False
            log.info("albion_gate: manutenção de fundo retomada (%.2f req/s)", self._rate)

    def allows_background(self) -> bool:
        return not self._background_paused


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
RATE_MAX = 0.7        # teto: pico permitido (req/s, POR HOST — 3 hosts independentes)
RATE_MIN = 0.35       # piso: mantém o caminho interativo responsivo sob sobrecarga
RATE_INCREASE = 0.01  # +req/s por resposta 2xx (recuperação gradual até o teto)
RATE_DECREASE = 0.5   # ×req/s por rodada com sobrecarga (backoff)
_RATE_ERROR_CODES = frozenset({429, 502, 503, 504})  # sinais de sobrecarga da Albion
ALBION_RATE_BURST = 1  # sem rajada — cada request espaçado em 1/rate segundos
# Trabalho histórico só volta depois de uma sequência sustentada de respostas
# boas. Sem histerese, um único 200 entre 502 faria sweepers reencherem a fila.
BACKGROUND_PAUSE_RATE = 0.35
BACKGROUND_RESUME_RATE = 0.55

# O limitador é POR HOST, não global: os 3 gameinfo (americas/europe/asia) são
# servidores independentes com saúde independente — 504 da americas não diz
# nada sobre a europa. Incidente 14/ago/2026: o AIMD global deixou a americas
# doente afogando TODAS as regiões no piso (0.1 req/s), e as batalhas de
# europa/ásia ficaram 12h atrasadas enquanto só a americas 504ava. Teto por
# host de 0.7 mantém a carga POR SERVIDOR igual à de antes (o pior caso
# agregado 3×0.7 fica espalhado em 3 destinos diferentes, não num só).
#
# Dentro de cada host, separamos DOIS buckets independentes:
# - CRITICAL (prioridade <= NEW_ELIGIBLE=10): feed discovery (battle_tracker
#   sync_recent, player_tracker sync_recent). NUNCA pausa; floor = 0.35
#   (BACKGROUND_RESUME_RATE) pra garantir descoberta contínua mesmo sob carga.
# - BACKGROUND (prioridade > NEW_ELIGIBLE): backfill, scan fallback, sweepers,
#   reprocessors. Pode pausar quando rate <= BACKGROUND_PAUSE_RATE (0.35).
# EMBED usa o bucket CRITICAL (prioridade -1). Call site sem host usa
# limitador default compartilhado (single bucket).
_host_limiters: dict[str, _RateLimiter] = {}
_default_limiter = _RateLimiter(RATE_MAX, ALBION_RATE_BURST)


def _limiters() -> list[_RateLimiter]:
    return [_default_limiter, *_host_limiters.values()]


def _limiter_for(host: str | None, prio: int | None = None) -> _RateLimiter:
    """Retorna o rate limiter adequado para o host e prioridade.

    Se prio <= NEW_ELIGIBLE (10): bucket CRITICAL (feed discovery, embed).
    Se prio > NEW_ELIGIBLE: bucket BACKGROUND (backfill, sweepers, fallback).
    Sem host: limitador default compartilhado (single bucket)."""
    if not host:
        return _default_limiter
    limiter = _host_limiters.get(host)
    if limiter is None:
        limiter = _RateLimiter(RATE_MAX, ALBION_RATE_BURST, min_rate=RATE_MIN)
        _host_limiters[host] = limiter
    return limiter


def _host_pool_for(host: str | None, prio: int) -> _PriorityPool:
    """Reserva uma conexão por host para o primeiro preview do Discord."""
    pools = _embed_host_pools if prio == EMBED else _host_pools
    default = _default_embed_host_pool if prio == EMBED else _default_host_pool
    if not host:
        return default
    pool = pools.get(host)
    if pool is None:
        pool = _PriorityPool(_EMBED_HOST_SLOTS if prio == EMBED else _HOST_SLOTS)
        pools[host] = pool
    return pool


def observe_response(host: str | None, status: int) -> None:
    """Alimenta o rate limiter adaptativo do HOST com o status de UM response
    do gameinfo. Chamado pelo response hook do make_client (player_tracker) —
    todo request ao gameinfo passa por ele, então o feedback cobre o tráfego
    todo sem nenhum call site precisar reportar nada.

    O feedback vai para AMBOS os buckets (critical e background) do host,
    pois a saúde do servidor é a mesma. O bucket critical tem floor mais alto
    e não pausa; o background pode pausar."""
    if not host:
        _default_limiter.observe(status)
        return
    limiter = _host_limiters.get(host)
    if limiter is not None:
        limiter.observe(status)


def background_allowed(host: str | None) -> bool:
    """Se o host suporta manutenção (bucket BACKGROUND) sem atrasar o feed."""
    if not host:
        return True
    limiter = _host_limiters.get(host)
    return limiter is None or limiter.allows_background()


def rate_status() -> dict:
    """Estado corrente dos rate limiters adaptativos — pro dashboard de ops.
    `rate` = pior host (o min) pra o card geral não otimista; detalhe por host
    em `hosts`. `queue` = requests bloqueados esperando slot/rate agora."""
    lims = _limiters()
    hosts_detail = {}
    for h, limiter in _host_limiters.items():
        hosts_detail[h] = {
            "rate": round(limiter._rate, 3),
            "background_paused": not limiter.allows_background(),
        }
    return {
        "rate": round(min(l._rate for l in lims), 3),
        "ceiling": RATE_MAX,
        "floor": RATE_MIN,
        "queue": queue_depth(OTHER),
        "hosts": hosts_detail,
        "background_paused_hosts": sorted(
            h for h, limiter in _host_limiters.items() if not limiter.allows_background()
        ),
    }


@asynccontextmanager
async def slot(host: str | None = None):
    """Adquire um slot do pool correspondente à prioridade corrente e um
    token do limitador de taxa do HOST. Envolver CADA ``client.get`` contra
    gameinfo com isto; passe o ``host`` pra isolar o backoff por região (504
    da americas não estrangula europa/ásia). Sem host, usa o limitador
    default compartilhado.

    O bucket (critical/background) é escolhido pela prioridade corrente:
    prio <= NEW_ELIGIBLE (10) -> critical; caso contrário -> background."""
    prio = _current.get()
    pool = _reserved if prio <= _HIGH_MAX else _bg
    host_pool = _host_pool_for(host, prio)
    started = asyncio.get_running_loop().time() if prio == EMBED else None
    await pool.acquire(prio)
    host_acquired = False
    try:
        await host_pool.acquire(prio)
        host_acquired = True
        await _limiter_for(host, prio).acquire(prio)
        if started is not None:
            waited = asyncio.get_running_loop().time() - started
            if waited >= 1:
                log.info("albion_gate: embed aguardou %.1fs (%s)", waited, host)
        yield
    finally:
        if host_acquired:
            host_pool.release()
        pool.release()


def queue_depth(prio: int = OTHER) -> int:
    """Quantos requests serão servidos ANTES de um request de prioridade `prio`
    — NÃO o total global. Perfil (refresh/cold-load) roda em PROFILE (0), que
    FURA a fila: os milhares de requests de background (OTHER=20) não passam na
    frente dele. Somá-los dava um número enorme e mentiroso ('1208 na fila'
    enquanto o refresh já estava sendo servido). Conta só os waiters que empatam
    ou superam `prio` (prio_do_waiter <= prio, pois heap serve por prioridade
    crescente) no pool relevante àquela prioridade + no rate limiter
    (agora separado por bucket). Lê os heaps direto — contar não precisa de ordem; pula
    future cancelado (mesma checagem do release)."""
    def ahead(pool: _PriorityPool) -> int:
        return sum(1 for wp, _s, fut in pool._waiters if wp <= prio and not fut.done())
    own_pool = _reserved if prio <= _HIGH_MAX else _bg
    return ahead(own_pool) + sum(ahead(l._pool) for l in _limiters())


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
        # (a)-(c) testam só os pools de concorrência — troca os rate limiters
        # por uns bem generosos pra essas asserções de timing não dependerem
        # do burst/rate de produção (testado isoladamente no (e)).
        global _default_limiter, _default_host_pool, _default_embed_host_pool
        _default_limiter = _RateLimiter(rate=1000, burst=1000)
        _host_limiters.clear()
        _host_pools.clear()
        _embed_host_pools.clear()
        _default_host_pool = _PriorityPool(1000)
        _default_embed_host_pool = _PriorityPool(1000)

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

        # (c) mesmo com tráfego normal pendurado num host, EMBED usa a conexão
        # reservada e entra sem esperar o request de fundo terminar.
        _default_host_pool = _PriorityPool(1)
        _default_embed_host_pool = _PriorityPool(1)
        normal_release = asyncio.Event()
        normal_entered = asyncio.Event()

        async def _normal_host_request():
            async with albion_scope(OTHER):
                async with slot():
                    normal_entered.set()
                    await normal_release.wait()

        normal = asyncio.create_task(_normal_host_request())
        await asyncio.wait_for(normal_entered.wait(), timeout=1)
        embed_done = asyncio.Event()

        async def _embed_host_request():
            async with albion_scope(EMBED):
                async with slot():
                    embed_done.set()

        await asyncio.wait_for(asyncio.create_task(_embed_host_request()), timeout=1)
        assert embed_done.is_set(), "EMBED não pode esperar request normal no mesmo host"
        normal_release.set()
        await asyncio.wait_for(normal, timeout=1)
        _default_host_pool = _PriorityPool(1000)
        _default_embed_host_pool = _PriorityPool(1000)

        # (d) prioridade entre waiters bg: NEW_ELIGIBLE(10) vence OTHER(20)
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

        # (e) battle_priority + cadeia de prioridade (novas > warmer > antigas)
        assert PROFILE < NEW_ELIGIBLE < WARM < OLD_ELIGIBLE < OTHER, "cadeia de prioridade quebrada"

        class B:
            def __init__(self, n): self.players_total = n
        assert battle_priority(B(50), is_new=True) == NEW_ELIGIBLE
        assert battle_priority(B(5), is_new=True) == NEW_SMALL
        assert battle_priority(B(50), is_new=False) == OLD_ELIGIBLE
        assert battle_priority(B(5), is_new=False) == OLD_SMALL
        assert battle_priority(B(None), is_new=True) == NEW_SMALL  # None → 0 → small

        # (f) rate limiter: burst dispara na hora, o (burst+1)º espera ~1/rate
        rl = _RateLimiter(rate=20, burst=3)
        t0 = asyncio.get_running_loop().time()
        for _ in range(3):
            await rl.acquire(OTHER)
        assert asyncio.get_running_loop().time() - t0 < 0.05, "burst deveria ser imediato"
        await rl.acquire(OTHER)
        elapsed = asyncio.get_running_loop().time() - t0
        assert elapsed >= 1 / 20, f"4º request deveria esperar ~1/rate, levou {elapsed}s"

        # (g) queue_depth é POR PRIORIDADE: waiters OTHER no bg não contam pra um
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

        # (h) rate limiter AIMD: 2xx recupera até o teto, erro recua, cascata na
        # mesma rodada = 1 backoff, e clamp no piso.
        rl2 = _RateLimiter(rate=RATE_MAX, burst=1)
        rl2.observe(200)                                  # já no teto → não passa
        assert rl2._rate == RATE_MAX, rl2._rate
        rl2.observe(504)                                  # recua ×RATE_DECREASE
        after1 = RATE_MAX * RATE_DECREASE
        assert rl2._rate == after1, rl2._rate
        assert rl2.allows_background() is False
        rl2.observe(504)                                  # 2º erro na mesma rodada: ignorado
        assert rl2._rate == after1, "cascata deve contar como 1 recuo"
        rl2.observe(200)                                  # recupera de leve
        assert rl2._rate == min(RATE_MAX, after1 + RATE_INCREASE), rl2._rate
        for _ in range(40):
            rl2.observe(200)
        assert rl2.allows_background() is True
        for _ in range(40):                               # muitos erros → clamp no piso
            rl2._last_decrease = float("-inf")            # fura o cooldown pra testar o clamp
            rl2.observe(504)
        assert rl2._rate == RATE_MIN, rl2._rate
        assert rl2.allows_background() is False

        # (i) isolamento por host: 504 da americas não recua o limiter da
        # europa (o defeito do incidente 14/ago/2026), e observe_response
        # roteia pro host certo.
        observe_response("gameinfo.albiononline.com", 504)
        observe_response("gameinfo.albiononline.com", 504)
        am = _limiter_for("gameinfo.albiononline.com")
        am._last_decrease = float("-inf")
        observe_response("gameinfo.albiononline.com", 504)

        eu = _limiter_for("gameinfo-ams.albiononline.com")
        assert am._rate < RATE_MAX, am._rate
        assert eu._rate == RATE_MAX, eu._rate
        am_status = rate_status()["hosts"]["gameinfo.albiononline.com"]
        assert isinstance(am_status, dict) and "rate" in am_status
        assert am_status["rate"] < RATE_MAX, f"host deveria ter recuado: {am_status['rate']}"

        print("albion_gate OK")

    asyncio.run(_main())
