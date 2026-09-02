"""Backfill one-shot de batalhas antigas — cava o passado da API do Albion.

O `battle_sweeper` (background do servidor) já cava pra trás, mas com janela
tímida (BELOW_MIN_WINDOW=100 IDs/ciclo, ciclo de 180s) — pra chegar em
batalhas de semanas atrás levaria dias. Este script roda uma vez, no teu PC,
quando quiser popular o banco rápido: mesma lógica do sweeper, mas com janela
GRANDE e sem esperar o ciclo do servidor.

Reaproveita TUDO do sweeper:
  - `_probe_detail` (sonda 1 host, respeita 429 com backoff)
  - `upsert_battle_light` (light-capture)
  - `BattleIdProbe` (memória de sondados — não re-sonda o que já sondou)
  - `albion_scope(OTHER)` + `slot()` (rate limiter adaptativo, 0.7 req/s teto,
    backoff em 429/504 — NÃO dá pra contornar sem derrubar a API pra todo mundo)

Tudo que acha é marcado `reprocess_reason='sweeper'` — o `battle_reprocessor`
do servidor faz o deep-process (eventos/lados/builds) na fila de fundo, sem
lógica nova aqui.

Uso:
    python -m scripts.backfill_battles --window 50000
    python -m scripts.backfill_battles --window 100000 --region europe
    python -m scripts.backfill_battles --from-id 1000000 --to-id 950000

Se o servidor estiver rodando, ambos respeitam o mesmo rate limiter (memória
separada, mas mesmos knobs) — se a Albion 429, os dois recuam sozinhos.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from sqlalchemy import select

from app.db import SessionLocal
from app.models.battles import Battle, BattleIdProbe
from app.services.albion_gate import OTHER, albion_scope
from app.services.battle_sweeper import _probe_and_capture
from app.services.player_tracker import HOSTS, make_client

log = logging.getLogger("backfill_battles")


def _candidates_for_region(
    db, region: str, window: int, from_id: int | None, to_id: int | None,
) -> list[int]:
    """IDs candidatos pra UMA região, do mais novo pro mais antigo.

    Sem --from-id/--to-id: buracos entre IDs conhecidos + janela abaixo do
    menor ID conhecido (igual sweeper, mas com janela GRANDE).
    Com --from-id/--to-id: range explícito, descending, excluso sondados.
    """
    probed: set[int] = set()
    for x in db.scalars(select(BattleIdProbe.albion_id)):
        try:
            probed.add(int(x))
        except (TypeError, ValueError):
            continue

    known: set[int] = set()
    for a in db.scalars(select(Battle.albion_id).where(Battle.region == region)):
        try:
            known.add(int(a))
        except (TypeError, ValueError):
            continue

    if from_id is not None and to_id is not None:
        lo, hi = min(from_id, to_id), max(from_id, to_id)
        return [c for c in range(hi, lo - 1, -1) if c not in probed and c not in known]

    # Modo janela: buracos entre conhecidos + janela abaixo do mínimo.
    if not known:
        return []
    ids_desc = sorted(known, reverse=True)
    out: list[int] = []
    prev: int | None = None
    for cur in ids_desc:
        if prev is not None and prev - cur > 1:
            for c in range(prev - 1, cur, -1):
                if c not in probed:
                    out.append(c)
        prev = cur
    lo = ids_desc[-1]
    for c in range(lo - 1, max(0, lo - window), -1):
        if c not in probed:
            out.append(c)
    return out


async def run(window: int, region: str | None, from_id: int | None, to_id: int | None) -> None:
    db = SessionLocal()
    try:
        regions = [region] if region else list(HOSTS)
        candidates: list[tuple[str, int]] = []
        for r in regions:
            cs = _candidates_for_region(db, r, window, from_id, to_id)
            candidates.extend((r, c) for c in cs)
            log.info("backfill: %s — %d candidatos", r, len(cs))

        if not candidates:
            log.info("backfill: nada a sondar (tudo já conhecido/sondado).")
            return

        log.info("backfill: %d candidatos totais. Iniciando sondagem...", len(candidates))
        db_lock = asyncio.Lock()
        found = 0
        probed = 0
        t0 = datetime.now(timezone.utc)

        async with make_client() as client:
            async with albion_scope(OTHER):
                # Processa em lotes pequenos pra não segurar a db_lock por
                # muito tempo e dar progresso no log. O rate limiter (slot()
                # dentro de _probe_and_capture) é o teto real de vazão.
                BATCH = 50
                for i in range(0, len(candidates), BATCH):
                    batch = candidates[i:i + BATCH]
                    results = await asyncio.gather(*(
                        _probe_and_capture(client, db, db_lock, r, c)
                        for r, c in batch
                    ))
                    found += sum(1 for r in results if r)
                    probed += len(batch)
                    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
                    rate = probed / elapsed if elapsed > 0 else 0
                    log.info(
                        "backfill: %d/%d sondados (%.1f%%) — %d achados — %.1f req/s",
                        probed, len(candidates), 100 * probed / len(candidates),
                        found, rate,
                    )

        log.info(
            "backfill: PRONTO. %d sondados, %d achados em %.0fs.",
            probed, found, (datetime.now(timezone.utc) - t0).total_seconds(),
        )
        log.info(
            "backfill: o battle_reprocessor do servidor vai deep-processar as "
            "%d batalhas novas na fila de fundo (reprocess_reason='sweeper').",
            found,
        )
    finally:
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--window", type=int, default=50000,
                   help="Quantos IDs abaixo do menor conhecido cavar (default 50000).")
    p.add_argument("--region", choices=list(HOSTS), default=None,
                   help="Sondar só uma região (default: todas).")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--from-id", type=int, default=None,
                   help="ID inicial (mais alto) do range explícito. Use com --to-id.")
    g.add_argument("--to-id", type=int, default=None,
                   help="ID final (mais baixo) do range explícito. Use com --from-id.")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if (args.from_id is None) != (args.to_id is None):
        p.error("--from-id e --to-id devem ser usados juntos")

    asyncio.run(run(args.window, args.region, args.from_id, args.to_id))


if __name__ == "__main__":
    main()