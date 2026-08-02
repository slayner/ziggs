"""Companion scan — distribui ranges de batalhas para companions escanearem.

Sem auth. Gera tarefas a partir dos buracos na sequência de IDs por região
(igual ao battle_sweeper), mas em vez de sondar síncrono aqui, entrega o
range pro companion fazer a sondagem distribuída e reportar de volta.

Reaproveita upsert_battle_light e BattleIdProbe do battle_tracker/sweeper —
mesma lógica de captura, só muda QUEM faz a sondagem HTTP.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import distinct, func, select, update
from sqlalchemy.orm import Session

from app.models.battles import Battle, BattleIdProbe
from app.models.companion import (
    CLAIM_TTL, CompanionScanTask, RANGE_SIZE, COMPANION_REGIONS,
)
from app.db import SessionLocal
from app.services.battle_sweeper import _probe_detail, _region_candidates
from app.services.battle_tracker import upsert_battle_light, REPROCESS_REASON_SWEEPER
from app.services.player_tracker import HOSTS, make_client

log = logging.getLogger(__name__)

# Quantas tarefas pending manter por região (gera mais conforme consome).
MAX_PENDING_PER_REGION = 50
# Quantos candidatos sondar pra gerar 1 tarefa (cada tarefa = RANGE_SIZE IDs).
CANDIDATES_PER_REGION = MAX_PENDING_PER_REGION * RANGE_SIZE


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _release_expired_claims(db: Session) -> int:
    """Tarefas claim que expiraram voltam a 'pending'.

    synchronize_session=False: o UPDATE roda no banco e pronto. Com a sincronia
    padrão o SQLAlchemy reavalia o WHERE em Python contra os objetos já
    carregados na sessão — e o SQLite devolve datetime SEM tzinfo, então a
    comparação com _now() (aware) explodia com TypeError. Quem precisa do
    estado novo re-consulta logo abaixo.
    """
    result = db.execute(
        update(CompanionScanTask)
        .where(
            CompanionScanTask.status == "claimed",
            CompanionScanTask.claim_expires_at < _now(),
        )
        .values(status="pending", claimed_at=None, claim_expires_at=None),
        execution_options={"synchronize_session": False},
    )
    return result.rowcount or 0


def _generate_tasks_for_region(db: Session, region: str) -> int:
    """Gera tarefas pending para uma região baseadas nos buracos da sequência.

    Reaproveita _region_candidates do battle_sweeper (mesma lógica: buracos
    entre IDs conhecidos, do mais novo pro mais antigo, mais janela abaixo do
    mínimo). Cada RANGE_SIZE IDs vira uma tarefa.
    """
    existing_pending = db.scalar(
        select(CompanionScanTask.id)
        .where(CompanionScanTask.region == region, CompanionScanTask.status == "pending")
        .limit(1)
    )
    if existing_pending is not None:
        return 0  # ainda tem trabalho pending

    raw = db.scalars(select(Battle.albion_id).where(Battle.region == region)).all()
    ids: set[int] = set()
    for a in raw:
        try:
            ids.add(int(a))
        except (TypeError, ValueError):
            continue
    if not ids:
        return 0

    ids_desc = sorted(ids, reverse=True)
    probed = {int(x) for x in db.scalars(select(BattleIdProbe.albion_id)) if str(x).isdigit()}
    candidates = _region_candidates(ids_desc, probed | ids, CANDIDATES_PER_REGION)

    created = 0
    for i in range(0, len(candidates), RANGE_SIZE):
        chunk = candidates[i : i + RANGE_SIZE]
        if not chunk:
            break
        # prioridade: mais perto do topo da sequência = mais recente = prio alta
        prio = 2 if i == 0 else (1 if i < RANGE_SIZE * 5 else 0)
        db.add(CompanionScanTask(
            region=region,
            battle_id_start=chunk[-1],  # menor ID do chunk
            battle_id_end=chunk[0],     # maior ID do chunk
            priority=prio,
            status="pending",
        ))
        created += 1
    if created:
        db.commit()
    return created


def generate_tasks(db: Session) -> int:
    """Garante que cada região tenha tarefas pending disponíveis."""
    _release_expired_claims(db)
    total = 0
    for region in COMPANION_REGIONS:
        try:
            total += _generate_tasks_for_region(db, region)
        except Exception as e:
            log.warning("companion_scan: erro ao gerar tarefas (%s): %s", region, e)
            db.rollback()
    if total:
        db.commit()
    return total


def claim_task(db: Session, install_id: str | None = None) -> CompanionScanTask | None:
    """Companion pede trabalho. Retorna a tarefa de maior prioridade pendente,
    ou None se não houver. Marca como claimed com TTL de CLAIM_TTL.

    `install_id` (header X-Ziggs-Install) = 1 range por INSTALAÇÃO, não por
    processo: se essa instalação já tem um claim vivo, devolve o MESMO range em
    vez de um novo. Reabrir o app (ou ter 2 cópias abertas durante um rebuild)
    continua sendo um companion só. Sem install_id → comportamento antigo.
    """
    _release_expired_claims(db)

    if install_id:
        held = db.scalar(
            select(CompanionScanTask)
            .where(
                CompanionScanTask.claimed_by == install_id,
                CompanionScanTask.status == "claimed",
            )
            .order_by(CompanionScanTask.id.asc())
            .limit(1)
        )
        if held is not None:
            # Pedir de novo prova que a instalação está viva → renova o TTL pra
            # o range não expirar embaixo de quem ainda está trabalhando nele.
            now = _now()
            held.claimed_at = now
            held.claim_expires_at = now + CLAIM_TTL
            db.commit()
            return held

    task = db.scalar(
        select(CompanionScanTask)
        .where(CompanionScanTask.status == "pending")
        .order_by(CompanionScanTask.priority.desc(), CompanionScanTask.id.asc())
        .limit(1)
    )
    if task is None:
        # tenta gerar mais tarefas sob demanda
        generate_tasks(db)
        task = db.scalar(
            select(CompanionScanTask)
            .where(CompanionScanTask.status == "pending")
            .order_by(CompanionScanTask.priority.desc(), CompanionScanTask.id.asc())
            .limit(1)
        )
        if task is None:
            return None

    now = _now()
    task.status = "claimed"
    task.claimed_by = install_id
    task.claimed_at = now
    task.claim_expires_at = now + CLAIM_TTL
    db.commit()
    db.refresh(task)
    return task


def count_active_companions(db: Session) -> int:
    """Instalações distintas que pediram trabalho dentro do CLAIM_TTL.

    Conta install_id, não processo nem IP: 3 cópias abertas no mesmo PC contam
    1. Companion antigo (sem o header) não entra na conta — melhor subestimar
    do que inflar o número.
    """
    since = _now() - CLAIM_TTL
    return db.scalar(
        select(func.count(distinct(CompanionScanTask.claimed_by))).where(
            CompanionScanTask.claimed_by.is_not(None),
            CompanionScanTask.claimed_at >= since,
        )
    ) or 0


async def report_task(
    db: Session,
    task_id: int,
    found: list[int],
    missing: list[int],
    errors: list[int],
    install_id: str,
    reported_region: str,
    character_name: str | None = None,
) -> tuple[int, int]:
    """Revalida no Albion os IDs reportados e persiste só o resultado oficial.

    character_name (opcional): nick configurado no companion. Batalhas que
    este report CRIOU ganham found_by=nick (agradecimento na página pública).
    Endpoint sem auth → só aceita nicks no formato do Albion (alfanumérico
    3-16 chars); qualquer outra coisa é ignorada, não rejeita o report."""
    task = db.get(CompanionScanTask, task_id)
    if task is None:
        raise LookupError("tarefa não encontrada")
    if task.status != "claimed" or not task.claimed_by or task.claimed_by != install_id:
        raise PermissionError("tarefa não pertence a esta instalação")
    if reported_region != task.region:
        raise ValueError("região não corresponde à tarefa")

    partitions = (found, missing, errors)
    if any(len(part) > RANGE_SIZE for part in partitions):
        raise ValueError("partição grande demais")
    reported = found + missing + errors
    if len(reported) > RANGE_SIZE or len(reported) != len(set(reported)):
        raise ValueError("IDs duplicados ou em excesso")
    if any(aid < task.battle_id_start or aid > task.battle_id_end for aid in reported):
        raise ValueError("ID fora do range reivindicado")

    nick = None
    if character_name and re.fullmatch(r"[A-Za-z0-9]{3,16}", character_name.strip()):
        nick = character_name.strip()

    # Libera read tx antes do HTTP (gather de _probe_detail faz N chamadas
    # concorrentes à API do Albion — read tx aberta impede wal_checkpoint).
    db.commit()

    accepted = 0
    region = task.region
    async with make_client() as client:
        verified = await asyncio.gather(*(
            _probe_detail(client, HOSTS[region], str(aid)) for aid in reported
        ))

    verified_missing = 0
    verified_errors = 0
    for aid, (status, raw) in zip(reported, verified):
        battle = None
        try:
            if status == "found" and raw is not None and str(raw.get("id")) == str(aid):
                is_new = nick is not None and db.scalar(
                    select(Battle.id).where(
                        Battle.region == region, Battle.albion_id == str(aid)
                    )
                ) is None
                battle = upsert_battle_light(db, raw, region)
                if battle is not None:
                    battle.reprocess_reason = REPROCESS_REASON_SWEEPER
                    if is_new:
                        battle.found_by = nick
                        log.info("scan: batalha %s (%s) encontrada por %s — %d jogadores",
                                 battle.albion_id, region, nick, battle.players_total or 0)
                    accepted += 1
                else:
                    # A API confirmou o ID, mas lutas pequenas não são armazenadas.
                    status = "found"
            elif status == "missing":
                verified_missing += 1
            else:
                status = "error"
                verified_errors += 1
        except Exception as e:
            log.warning("companion_scan: upsert falhou (%s): %s", aid, e)
            status = "error"
            verified_errors += 1

        if status != "error":
            probe = db.get(BattleIdProbe, str(aid))
            if probe is None:
                db.add(BattleIdProbe(
                    albion_id=str(aid), status=status, region=region,
                    battle_id=battle.id if battle else None, probed_at=_now(),
                ))
            else:
                probe.status = status
                probe.region = region
                probe.battle_id = battle.id if battle else None
                probe.probed_at = _now()

    task.status = "done"
    task.completed_at = _now()
    task.found_count = accepted
    task.missing_count = verified_missing
    task.error_count = verified_errors
    db.commit()
    return (accepted, len(reported) - accepted)


# ─── scheduler periódico ─────────────────────────────────────────────────────

# Intervalo entre ciclos de geração de tarefas. Não precisa ser agressivo:
# companions pedem trabalho sob demanda via claim (que também gera se vazio),
# este scheduler só garante que há pending pronto pra quando chegarem.
SCAN_TASK_INTERVAL = 120  # segundos


async def run_forever() -> None:
    log.info("companion_scan: scheduler iniciado (interval=%ds)", SCAN_TASK_INTERVAL)
    while True:
        db = SessionLocal()
        try:
            n = generate_tasks(db)
            if n:
                log.info("companion_scan: %d tarefas geradas", n)
        except Exception as e:
            log.error("companion_scan: erro no scheduler: %s", e)
            db.rollback()
        finally:
            db.close()
        await asyncio.sleep(SCAN_TASK_INTERVAL)
