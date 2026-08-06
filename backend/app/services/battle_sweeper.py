"""Background task — busca batalhas por ID que caíram fora da janela de listagem.

A listagem da API (`/api/gameinfo/battles?sort=recent&offset=N`) tem teto duro
de offset 10000 — além disso HTTP 500, não lista vazia (ver
battle_tracker.BATTLES_API_OFFSET_LIMIT). Cada região acumula >10k batalhas em
~3 dias, então batalhas antigas saem da janela e o backfill nunca as alcança.

O endpoint de DETALHE `/api/gameinfo/battles/{albion_id}` não tem esse teto.
IDs de batalha são um CONTADOR SEQUENCIAL POR REGIÃO (cada host regional tem a
própria sequência — ver comment em models/battles.py: o mesmo número em duas
regiões é duas batalhas sem relação). Logo, o espaço de busca verdadeiro de uma
região é exatamente os BURACOS entre IDs consecutivos que já conhecemos dela —
não um raio arbitrário sondado às cegas nos 3 hosts, como a 1ª versão fazia
(raio fixo sobrepõe janelas em zona densa, pula buracos maiores que o raio, e
sondar hosts alheios triplica o custo pra achar no máximo coincidência).

Este serviço, por ciclo:

  1. Por região: enumera os buracos entre IDs conhecidos, do mais NOVO pro mais
     antigo (recente importa mais e tem melhor retenção na API), mais uma
     janela de BELOW_MIN_WINDOW abaixo do menor ID conhecido — é ela que segue
     estendendo o histórico para trás, um ciclo de cada vez.
  2. Sonda cada candidato SÓ no host da própria região.
  3. Light-captura os válidos (upsert_battle_light) e marca
     reprocess_reason='sweeper' — o battle_reprocessor faz o deep-process
     (eventos/lados/builds) na fila de fundo, sem lógica nova aqui.
  4. Memoriza cada ID sondado em BattleIdProbe (found/missing) pra nunca
     re-sondar o mesmo buraco.

Acima do maior ID conhecido não sondamos: batalhas novas o sync_recent pega."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db import AsyncSessionLocal, SyncSessionLocal
from app.models.battles import Battle, BattleIdProbe
from app.services.albion_gate import OTHER, albion_scope, slot
from app.services.battle_tracker import REPROCESS_REASON_SWEEPER, upsert_battle_light
from app.services.player_tracker import HOSTS, make_client

log = logging.getLogger(__name__)

BELOW_MIN_WINDOW = 100        # quanto estender abaixo do menor ID conhecido, por ciclo
MAX_CANDIDATES_PER_CYCLE = 1200
CYCLE_INTERVAL = 180          # segundos entre ciclos
MAX_429_RETRIES = 3
DB_LOCK_RETRIES = 3

# Companion-aware: quando há companions ativos sondando, o sweeper reduz seus
# candidatos — eles cobrem os mesmos buracos de graça (IP deles, não nosso rate
# limit). Sem companion → throughput total; com companion → só o que o companion
# não vai alcançar (a janela abaixo do mínimo, que o companion também gera, mas
# o sweeper chega primeiro por ser mais direto).
COMPANION_AWARE_DIVISOR = 4  # com N companions: MAX / 4 (não divide por N — companion cai, sweeper assume)

# ponytail: taxa agregada = 1200 candidatos × 1 host / 180s ≈ 6.7 req/s — o
# MESMO budget da versão anterior (400 × 3 hosts), só que 100% dele gasto na
# região certa em vez de ⅓. Se virar 429 sustentado, baixa
# MAX_CANDIDATES_PER_CYCLE ou sobe CYCLE_INTERVAL antes de mexer em concorrência.
# Concorrência agora vive no bg pool do albion_gate (slot() em _probe_detail,
# prioridade OTHER — sweeper é sondagem especulativa, fica atrás de tudo).


def _region_candidates(ids_desc: list[int], probed: set[int], limit: int) -> list[int]:
    """Buracos da sequência de UMA região, do mais novo pro mais antigo, sem
    materializar ranges gigantes (um gap de milhões de IDs é percorrido só até
    encher o limite; o resto fica pros próximos ciclos, já que cada sondado
    entra em BattleIdProbe e sai do espaço de busca)."""
    out: list[int] = []
    prev: int | None = None
    for cur in ids_desc:
        if prev is not None and prev - cur > 1:
            for c in range(prev - 1, cur, -1):
                if c not in probed:
                    out.append(c)
                    if len(out) >= limit:
                        return out
        prev = cur
    # Janela abaixo do mínimo — segue cavando o passado, bounded por ciclo.
    lo = ids_desc[-1]
    for c in range(lo - 1, max(0, lo - BELOW_MIN_WINDOW), -1):
        if c not in probed:
            out.append(c)
            if len(out) >= limit:
                return out
    return out


def generate_candidates(db: Session, active_companions: int = 0) -> list[tuple[str, int]]:
    """[(region, albion_id_int), ...] — buracos por região, novos primeiro.
    Exclusão de sondados é GLOBAL por albion_id (PK única da BattleIdProbe):
    um número sondado numa região não re-entra pra outra. Perde no máximo o
    gêmeo de número coincidente em outra região — raro e barato; se um dia
    importar, o upgrade é PK composta (region, albion_id) na probe table.

    `active_companions`: se >0, reduz o teto de candidatos — companions ativos
    sondam os mesmos buracos de graça (IP deles), então o sweeper gasta menos
    do nosso rate limit e deixa espaço pra backfill/warmer.
    """
    limit = MAX_CANDIDATES_PER_CYCLE
    if active_companions > 0:
        limit = max(100, MAX_CANDIDATES_PER_CYCLE // COMPANION_AWARE_DIVISOR)
        log.info("battle_sweeper: %d companion(s) ativo(s) — teto reduzido pra %d candidatos",
                 active_companions, limit)

    probed: set[int] = set()
    for x in db.scalars(select(BattleIdProbe.albion_id)):
        try:
            probed.add(int(x))
        except (TypeError, ValueError):
            continue

    per_region_limit = max(1, limit // len(HOSTS))
    out: list[tuple[str, int]] = []
    for region in HOSTS:
        raw = db.scalars(select(Battle.albion_id).where(Battle.region == region)).all()
        ids: set[int] = set()
        for a in raw:
            try:
                ids.add(int(a))
            except (TypeError, ValueError):
                continue
        if not ids:
            continue
        ids_desc = sorted(ids, reverse=True)
        for c in _region_candidates(ids_desc, probed | ids, per_region_limit):
            out.append((region, c))
    return out[:limit]


async def _probe_detail(client: httpx.AsyncClient, host: str, albion_id: str) -> tuple[str, dict | None]:
    """Sonda UM host. Retorna:
      ("found", data)    — 200 com batalha válida
      ("missing", None)  — 404 (não existe nesse host)
      ("error", None)    — 429 esgotado, 5xx, ou erro de rede (NÃO grava probe;
                          retry no ciclo seguinte)
    429 respeita Retry-After se presente, senão backoff 5*(attempt+1) — mesmo
    padrão do battle_tracker._fetch_deep_data."""
    url = f"https://{host}/api/gameinfo/battles/{albion_id}"
    for attempt in range(MAX_429_RETRIES):
        try:
            async with slot():
                resp = await client.get(url)
        except httpx.RequestError:
            return "error", None
        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                return "error", None
            if isinstance(data, dict) and data.get("id"):
                return "found", data
            return "error", None
        if resp.status_code == 404:
            return "missing", None
        if resp.status_code == 429:
            if attempt == MAX_429_RETRIES - 1:
                return "error", None
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.replace(".", "", 1).isdigit():
                wait = float(retry_after)
            else:
                # jitter (±30%): várias sondas do mesmo ciclo podem 429 juntas;
                # sem isso todas dormiriam igual e retentariam sincronizadas.
                wait = 5.0 * (attempt + 1) * random.uniform(0.7, 1.3)
            await asyncio.sleep(wait)
            continue
        # Outro 4xx/5xx — conservador: não grava probe, re-tenta no próximo ciclo.
        return "error", None
    return "error", None


async def _probe_and_capture(
    client: httpx.AsyncClient, db: AsyncSession, db_lock: asyncio.Lock,
    region: str, albion_id: int,
) -> bool:
    """Sonda o candidato no host da SUA região (o número só faz sentido na
    sequência dela). Found → light-capture + reprocess_reason='sweeper' pro
    battle_reprocessor deep-processar. Grava BattleIdProbe (found/missing;
    error não grava — re-tenta depois). Retorna True sse capturou batalha nova."""
    aid = str(albion_id)
    status, raw = await _probe_detail(client, HOSTS[region], aid)
    if status == "error":
        return False  # não grava probe; re-tenta no próximo ciclo

    async with db_lock:
        for attempt in range(DB_LOCK_RETRIES):
            try:
                battle: Battle | None = None
                if status == "found" and raw is not None:
                    battle = await upsert_battle_light(db, raw, region)
                    if battle is not None:
                        battle.reprocess_reason = REPROCESS_REASON_SWEEPER
                    else:
                        status = "missing"

                existing = await db.get(BattleIdProbe, aid)
                if existing is None:
                    db.add(BattleIdProbe(
                        albion_id=aid, status=status,
                        region=region, battle_id=battle.id if battle else None,
                        probed_at=datetime.now(timezone.utc),
                    ))
                else:
                    existing.status = status
                    existing.region = region
                    existing.battle_id = battle.id if battle else None
                    existing.probed_at = datetime.now(timezone.utc)
                await db.commit()
                return battle is not None
            except OperationalError as e:
                await db.rollback()
                if "database is locked" not in str(e).lower() or attempt == DB_LOCK_RETRIES - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
            except Exception:
                await db.rollback()
                raise
        raise AssertionError("unreachable")


def _generate_candidates_sync(active_companions: int = 0) -> list[tuple[str, int]]:
    db = SyncSessionLocal()
    try:
        return generate_candidates(db, active_companions)
    finally:
        db.close()


async def sweep_cycle(client: httpx.AsyncClient, db: AsyncSession) -> dict:
    # Companion-aware: companions ativos sondam os mesmos buracos de graça.
    # Reduzir o teto aqui libera rate limit pro backfill/warmer sem perder
    # cobertura (o companion cobre o que o sweeper pula).
    active = 0
    try:
        from app.services.companion_scan import count_active_companions
        active = await count_active_companions(db)
    except Exception:
        pass
    candidates = await asyncio.to_thread(_generate_candidates_sync, active)
    if not candidates:
        log.info("battle_sweeper: sem candidatos novos (tudo sondado ou base vazia)")
        return {"candidates": 0, "found": 0, "probed": 0}

    db_lock = asyncio.Lock()

    async def _one(region: str, aid: int) -> bool:
        try:
            return await _probe_and_capture(client, db, db_lock, region, aid)
        except Exception as e:
            log.warning("battle_sweeper: falha ao sondar %s (%s): %r", aid, region, e)
            # Uma falha no commit (ex.: "database is locked" de outro bg task
            # escrevendo ao mesmo tempo) deixa a Session numa transação com
            # rollback pendente. Sem isso, TODO candidato seguinte no mesmo
            # ciclo — que compartilha esta mesma `db` — quebra com
            # PendingRollbackError em vez do erro real, mascarando a causa e
            # perdendo o ciclo inteiro por causa de UMA sondagem.
            return False

    results = await asyncio.gather(*(_one(r, c) for r, c in candidates))
    found = sum(1 for r in results if r)
    log.info("battle_sweeper: ciclo — %d candidatos, %d achados", len(candidates), found)
    return {"candidates": len(candidates), "found": found, "probed": len(candidates)}


async def run_forever() -> None:
    log.info("battle_sweeper: iniciando (below_min=%d, interval=%ds)", BELOW_MIN_WINDOW, CYCLE_INTERVAL)
    while True:
        async with AsyncSessionLocal() as db:
            try:
                async with make_client() as client:
                    async with albion_scope(OTHER):
                        await sweep_cycle(client, db)
            except Exception as e:
                log.error("battle_sweeper: erro no ciclo: %s", e)
        await asyncio.sleep(CYCLE_INTERVAL)


if __name__ == "__main__":
    # Auto-cheque mínimo (sem framework): generate_candidates numa base SQLite
    # temporária. Afirma: candidatos só da região do âncora, ordem nova→antiga,
    # buracos exatos entre IDs conhecidos + janela abaixo do mínimo, exclusão
    # de sondados, e respeito ao teto por ciclo.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.base import Base
    import app.models  # noqa: F401 — registra tudo no Base.metadata

    eng = create_engine("sqlite://", future=True)
    Base.metadata.create_all(eng)
    TestSession = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db = TestSession()
    now = datetime.now(timezone.utc)
    db.add_all([
        # europa: buraco exato de 3 IDs (1000496..1000498) entre dois conhecidos
        Battle(region="europe", albion_id="1000499", start_time=now, fetched_at=now,
               total_fame=0, kill_count=0, players_total=2),
        Battle(region="europe", albion_id="1000495", start_time=now, fetched_at=now,
               total_fame=0, kill_count=0, players_total=2),
        # americas: sequência própria, número parecido de propósito
        Battle(region="americas", albion_id="1000497", start_time=now, fetched_at=now,
               total_fame=0, kill_count=0, players_total=2),
    ])
    # 1000497 já sondado (globalmente) — não pode voltar pra europa
    db.add(BattleIdProbe(albion_id="1000497", status="missing", region="europe",
                         probed_at=now))
    db.commit()

    cands = generate_candidates(db)
    eu = [c for r, c in cands if r == "europe"]
    am = [c for r, c in cands if r == "americas"]

    # buraco da europa: 1000498 e 1000496 (1000497 excluído por probe), nessa ordem
    assert eu[:2] == [1000498, 1000496], eu[:2]
    # depois do buraco vem a janela abaixo do mínimo (1000494, 1000493, ...)
    assert eu[2] == 1000494 and eu[2 + BELOW_MIN_WINDOW - 2] == 1000495 - BELOW_MIN_WINDOW + 1, eu[2:5]
    # americas: só janela abaixo do próprio mínimo, descendente, sem o sondado
    assert am[0] == 1000496 and 1000497 not in am, am[:3]
    # ordem descendente dentro de cada região
    assert eu == sorted(eu, reverse=True) and am == sorted(am, reverse=True)
    # teto global respeitado
    assert len(cands) <= MAX_CANDIDATES_PER_CYCLE
    print(f"OK: {len(cands)} candidatos (eu={len(eu)}, am={len(am)}), gaps+janela corretos")
    db.close()
