"""Companion kill scan — distribui ranges de kill EventIds para companions
escanearem, mesmo padrão do companion_scan de batalhas.

Gera tarefas a partir dos buracos na sequência de EventIds por região,
entrega o range pro companion sondar via /api/gameinfo/events/{id} e
reportar de volta. O backend revalida cada ID na API pública do Albion
antes de persistir — nunca confia no client.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import distinct, func, select, update
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.players import PlayerKillEvent, KillIdProbe
from app.services.kill_sweeper import _probe_kill_event
from app.services.player_tracker import HOSTS, make_client, _record_kill_event, _upsert_event_players

log = logging.getLogger(__name__)

KILL_RANGE_SIZE = 200
KILL_CLAIM_TTL = timedelta(minutes=15)
KILL_MAX_PENDING_PER_REGION = 30
KILL_CANDIDATES_PER_REGION = KILL_MAX_PENDING_PER_REGION * KILL_RANGE_SIZE

COMPANION_KILL_REGIONS = ("americas", "europe", "asia")

_SCAN_TASK_INTERVAL = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _kill_region_candidates(
    ids_desc: list[int],
    probed: set[int],
    limit: int,
) -> list[int]:
    """Buracos entre EventIds conhecidos, do mais novo pro mais antigo."""
    out: list[int] = []
    if not ids_desc:
        return out
    for i in range(len(ids_desc) - 1):
        hi, lo = ids_desc[i], ids_desc[i + 1]
        for c in range(hi - 1, lo, -1):
            if c not in probed:
                out.append(c)
                if len(out) >= limit:
                    return out
    bottom = ids_desc[-1]
    for c in range(bottom - 1, bottom - 201, -1):
        if c <= 0:
            break
        if c not in probed:
            out.append(c)
            if len(out) >= limit:
                return out
    return out


# --- estado em memória (mesmo padrão do companion_scan, sem tabela dedicada) ---
# Como kill scan é mais leve que battle scan, usamos claims em memória em vez
# de uma tabela de tasks. Claims expiram por TTL; se o companion cair, o range
# volta a ser disponível.
_kill_claims: dict[str, dict] = {}  # install_id -> {region, start, end, claimed_at}


def _generate_kill_range(db: Session, region: str) -> tuple[int, int] | None:
    """Gera um range de EventIds pra sondar, baseado nos buracos da região."""
    raw = db.scalars(
        select(PlayerKillEvent.albion_event_id).where(PlayerKillEvent.region == region)
    ).all()
    ids: set[int] = set()
    for a in raw:
        try:
            ids.add(int(a))
        except (TypeError, ValueError):
            continue
    if not ids:
        return None

    ids_desc = sorted(ids, reverse=True)
    probed = {int(x) for x in db.scalars(select(KillIdProbe.albion_event_id)) if str(x).isdigit()}
    candidates = _kill_region_candidates(ids_desc, probed | ids, KILL_RANGE_SIZE)
    if not candidates:
        return None
    return (min(candidates), max(candidates))


def claim_kill_range(
    db: Session, install_id: str, region: str | None = None,
) -> dict | None:
    """Companion pede trabalho de kill scan. Retorna {region, start, end} ou None."""
    now = _now()

    # Limpa claims expirados
    expired = [
        k for k, v in _kill_claims.items()
        if v["claimed_at"] + KILL_CLAIM_TTL < now
    ]
    for k in expired:
        del _kill_claims[k]

    # Se já tem claim vivo, devolve o mesmo
    if install_id in _kill_claims:
        c = _kill_claims[install_id]
        c["claimed_at"] = now  # renova TTL
        return {"region": c["region"], "start": c["start"], "end": c["end"]}

    # Escolhe região (preferida ou round-robin)
    regions = [region] if region and region in COMPANION_KILL_REGIONS else list(COMPANION_KILL_REGIONS)

    for r in regions:
        rng = _generate_kill_range(db, r)
        if rng:
            _kill_claims[install_id] = {
                "region": r, "start": rng[0], "end": rng[1], "claimed_at": now,
            }
            return {"region": r, "start": rng[0], "end": rng[1]}

    return None


async def report_kill_range(
    db: Session,
    install_id: str,
    region: str,
    event_id_start: int,
    event_id_end: int,
    found: list[int],
    missing: list[int],
    errors: list[int],
) -> tuple[int, int]:
    """Revalida os IDs reportados na API do Albion e persiste.
    Retorna (accepted, rejected)."""
    claim = _kill_claims.get(install_id)
    if claim is None or claim["region"] != region:
        raise PermissionError("claim não encontrado ou região não corresponde")
    if claim["start"] != event_id_start or claim["end"] != event_id_end:
        raise ValueError("range não corresponde ao claim")

    reported = found + missing + errors
    if len(reported) > KILL_RANGE_SIZE:
        raise ValueError("range grande demais")

    # Revalida na API pública — não confia no client
    async with make_client() as client:
        verified = await asyncio.gather(*(
            _probe_kill_event(client, HOSTS[region], str(eid)) for eid in reported
        ))

    accepted = 0
    for eid, (status, raw) in zip(reported, verified):
        if status == "found" and raw is not None and str(raw.get("EventId")) == str(eid):
            try:
                await _upsert_event_players(db, raw, region)
                _record_kill_event(db, raw, region, commit=False)
                accepted += 1
            except Exception as e:
                log.debug("kill_scan: erro ao ingerir event %s (%s): %s", eid, region, e)
                db.rollback()
                status = "missing"
        elif status == "missing":
            pass
        else:
            status = "error"

        if status != "error":
            probe = db.get(KillIdProbe, str(eid))
            if probe is None:
                db.add(KillIdProbe(
                    albion_event_id=str(eid), status=status,
                    region=region, probed_at=_now(),
                ))
            else:
                probe.status = status
                probe.region = region
                probe.probed_at = _now()

    db.commit()
    del _kill_claims[install_id]
    return (accepted, len(reported) - accepted)


async def run_forever() -> None:
    """Scheduler — mantém claims expirados limpos. Ranges são gerados sob demanda."""
    log.info("companion_kill_scan: scheduler iniciado (interval=%ds)", _SCAN_TASK_INTERVAL)
    while True:
        now = _now()
        expired = [
            k for k, v in _kill_claims.items()
            if v["claimed_at"] + KILL_CLAIM_TTL < now
        ]
        for k in expired:
            del _kill_claims[k]
        await asyncio.sleep(_SCAN_TASK_INTERVAL)