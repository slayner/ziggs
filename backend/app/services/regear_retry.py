"""Fila de retry de reconhecimento de regear.

A API de killboard do Albion cai com frequência. Quando o reconhecimento na
ingestão falha (status "manual"/"error" sem albion_event_id), esta task
periódica re-roda o caminho por-jogador+CTA (sem OCR) nos pedidos pending
recentes. Se a API voltou, sobe pra "recognized" e preenche os itens — a
logística aprova como sempre.

Capa por `recognition_attempts` e por idade do pedido (não roda pra sempre nem
martela a API). OCR não é re-rodado (caro, e também depende da API).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import get_session
from app.models.audit import AuditLog
from app.models.regear import RegearRequest
from app.models.tenancy import Guild
from app.services import regear_config, regear_recognition
from app.services.albion_gate import REGEAR_RECOG, albion_scope
from app.services.regear import _apply_recognition, _cta_times, _landmark_window, _requester_albion_names

log = logging.getLogger(__name__)

RETRY_INTERVAL = 60            # segundos entre ciclos
MAX_ATTEMPTS = 8               # tenta no máximo N vezes por pedido
MAX_AGE = timedelta(hours=6)   # só pedidos criados há menos de isso
BATCH = 20                     # por ciclo, pra não estourar


async def _retry_once() -> None:
    db = next(get_session())
    try:
        cutoff = datetime.now(timezone.utc) - MAX_AGE
        rows = db.scalars(
            select(RegearRequest).where(
                RegearRequest.status == "pending",
                RegearRequest.recognition_status.in_(("manual", "error")),
                RegearRequest.albion_event_id.is_(None),
                RegearRequest.recognition_attempts < MAX_ATTEMPTS,
                RegearRequest.created_at >= cutoff,
            ).order_by(RegearRequest.created_at.asc()).limit(BATCH)
        ).all()
        if not rows:
            return

        # Materializa IDs e commit antes do HTTP — read tx aberta durante await
        # (recognize_by_player bate na API do Albion) impede wal_checkpoint.
        req_ids = [r.id for r in rows]
        db.commit()

        # Cacheia settings/region/cta por guild_id neste ciclo.
        for req_id in req_ids:
            req = db.get(RegearRequest, req_id)
            if req is None:
                continue
            # BUG 7: recheck status — logística pode ter aprovado entre o
            # commit da lista e agora. Não sobrescreve itens de pedido pago.
            if req.status != "pending":
                continue
            guild = db.get(Guild, req.guild_id)
            if guild is None:
                continue
            region = (guild.settings or {}).get("albion_guild_region")
            names = _requester_albion_names(db, guild.id, req.requester_user_id)
            if not names:
                continue  # sem registro, OCR já tentou na ingest → não adianta
            cta_times = _cta_times(db, guild.id)
            landmark = _landmark_window(db, guild.id, req.event_id)
            # Libera read tx antes do HTTP (recognize_by_player chama Albion).
            db.commit()
            try:
                rec = await regear_recognition.recognize_by_player(names, cta_times, region, landmark)
            except Exception as e:
                log.debug("regear retry #%s ainda falhou: %s", req.id, e)
                # BUG 4: exceção de rede/API não conta como tentativa — o pedido
                # continua elegível para retry quando a API voltar.
                continue
            if rec is None or not rec.get("items"):
                # Reconhecimento rodou mas não achou nada — conta como tentativa.
                req = db.get(RegearRequest, req_id)
                if req is not None and req.status == "pending":
                    req.recognition_attempts += 1
                    db.commit()
                continue
            # BUG 7: recheck status novamente — pode ter sido aprovado durante
            # o HTTP. Não sobrescreve itens de pedido já pago.
            req = db.get(RegearRequest, req_id)
            if req is None or req.status != "pending":
                continue
            req.recognition_attempts += 1
            settings = regear_config.get_regear_settings(guild)
            await _apply_recognition(db, req, rec, settings)
            db.add(AuditLog(
                guild_id=guild.id, actor_id=None, actor_type="bot", source="bot",
                action="regear.retry_recognize", entity="regear_request", entity_id=str(req.id),
                after={"recognition": req.recognition_status, "albion_event_id": req.albion_event_id},
            ))
            log.info("regear #%s reconhecido na retry (event %s)", req.id, req.albion_event_id)
        db.commit()
    except Exception:
        log.exception("Erro no regear_retry")
        db.rollback()
    finally:
        db.close()


async def run_forever() -> None:
    while True:
        await asyncio.sleep(RETRY_INTERVAL)
        async with albion_scope(REGEAR_RECOG):
            await _retry_once()