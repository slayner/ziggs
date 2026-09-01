"""Serviço de regear por screenshot: ingestão, fila, revisão e débito.

ingest: salva a imagem, roda o reconhecimento (OCR → API Albion), sugere preço
anti-troll com a % de cobertura da guilda, cria RegearRequest pending.
update: logística edita final_total (sobrescreve o sugerido) e aprova/nega.
  status=paid → guild.bank_balance -= (final_total ?? suggested_total).
Idempotência por (guild_id, screenshot_msg_id).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.catalog import GameRole
from app.models.economy import EconomyTransaction
from app.models.events import Event, EventParticipant
from app.models.regear import RegearRequest
from app.services.economy import get_or_create_balance
from app.models.registration import BotRegistration
from app.models.tenancy import Guild, GuildMember
from app.auth.permissions import has_permission
from app.services import regear_config, regear_recognition
from app.services.prices import suggest_regear_price

_IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "_regear_images")
_VALID_EXT = {".png", ".jpg", ".jpeg", ".webp"}

# Folga da janela do evento (landmark) — started_at-BUFFER → ended_at+BUFFER.
# Mesma ideia do ±30min do near_cta dos nodes.
_LANDMARK_BUFFER = timedelta(minutes=30)

_REGEAR_STATUS_TRANSITIONS = {
    "pending": {"pending", "paid", "denied", "removed"},
    "denied": {"denied", "removed"},
    "removed": {"removed"},
    "paid": {"paid"},
}


class RegearServiceError(Exception):
    pass


def regear_status_transition_allowed(current: str, new: str) -> bool:
    return new in _REGEAR_STATUS_TRANSITIONS.get(current, set())


def _ext(filename: str, content_type: str | None) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in _VALID_EXT:
        ct = (content_type or "").lower()
        ext = ".png" if "png" in ct else ".jpg" if "jpeg" in ct or "jpg" in ct else ".webp" if "webp" in ct else ".png"
    return ext or ".png"


def _save_image(guild_id: int, image_bytes: bytes, filename: str, content_type: str | None) -> str:
    ext = _ext(filename, content_type)
    sub = os.path.join(_IMAGES_DIR, str(guild_id))
    os.makedirs(sub, exist_ok=True)
    rel = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(sub, rel), "wb") as f:
        f.write(image_bytes)
    return f"{guild_id}/{rel}"  # relativo a _IMAGES_DIR


def screenshot_abs_path(rel: str) -> str:
    return os.path.join(_IMAGES_DIR, rel)


def _requester_albion_names(db: Session, guild_id: int, requester_user_id: int | None) -> list[str]:
    """Nicks Albion ativos do Discord user que postou a print (BotRegistration).
    Multi-char: o user pode ter mais de um personagem registrado — tentamos todos."""
    if not requester_user_id:
        return []
    rows = db.scalars(select(BotRegistration).where(
        BotRegistration.guild_id == guild_id,
        BotRegistration.discord_user_id == requester_user_id,
        BotRegistration.active.is_(True),
    )).all()
    return [r.albion_player_name for r in rows if r.albion_player_name]


def _cta_times(db: Session, guild_id: int) -> list[datetime]:
    """Horários agendados dos CTAs da guilda — usados pra casar a morte mais
    provável ("morte no horizonte do CTA"). SQLite devolve naive UTC; normaliza."""
    rows = db.scalars(select(Event).where(
        Event.guild_id == guild_id, Event.scheduled_at.is_not(None),
    )).all()
    out: list[datetime] = []
    for e in rows:
        if e.scheduled_at:
            t = e.scheduled_at
            out.append(t if t.tzinfo else t.replace(tzinfo=timezone.utc))
    return out


def _landmark_window(
    db: Session, guild_id: int, event_id: int | None,
) -> tuple[datetime, datetime] | None:
    """Janela do evento vinculado (landmark): started_at-BUFFER → ended_at+BUFFER.
    Se o evento não existe/sem started_at, cai pra scheduled_at-BUFFER → now+BUFFER.
    Retorna None se não há evento (usa o fallback de CTA)."""
    if not event_id:
        return None
    ev = db.scalar(select(Event).where(
        Event.id == event_id, Event.guild_id == guild_id
    ))
    if ev is None:
        return None
    start = ev.started_at or ev.scheduled_at
    if start is None:
        return None
    start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end = ev.ended_at or ev.callout_at or datetime.now(timezone.utc)
    end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
    return (start - _LANDMARK_BUFFER, end + _LANDMARK_BUFFER)


def _event_id_for_channel(db: Session, guild_id: int, channel_id: str | None) -> int | None:
    """Resolve o evento dono de uma thread/canal de regear pelo regear_thread_id.
    Screenshots soltas (canal topo sem thread) → None (fila geral)."""
    if not channel_id:
        return None
    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        return None
    ev = db.scalar(select(Event).where(
        Event.guild_id == guild_id, Event.regear_thread_id == cid
    ))
    return ev.id if ev is not None else None


async def _recognize(
    db: Session, guild: Guild, image_bytes: bytes,
    requester_user_id: int | None, region: str | None, event_id: int | None = None,
) -> dict:
    """Reconhecimento em 2 tentativas: por jogador+CTA/landmark (sem OCR) →
    fallback OCR. Qualquer falha vira "manual" na downstream."""
    names = _requester_albion_names(db, guild.id, requester_user_id)
    cta_times = _cta_times(db, guild.id)
    landmark = _landmark_window(db, guild.id, event_id)
    if names:
        try:
            rec = await regear_recognition.recognize_by_player(names, cta_times, region, landmark)
            if rec is not None:
                return rec
        except Exception:
            pass  # API fora do ar → cai no fallback de OCR
    try:
        return await regear_recognition.recognize(image_bytes, region)
    except Exception:
        return {"status": "error", "ocr_name": None, "albion_event_id": None,
                "death_timestamp": None, "items": [], "candidates": []}


async def _apply_recognition(
    db: Session, req: RegearRequest, rec: dict, settings: regear_config.RegearSettings,
) -> None:
    """Custa os itens do rec pela config da guilda (coverage + eligibilidade) e
    grava no row. Compartilhado entre ingest e fila de retry. A % de cobertura
    é a do canal por onde ESTA screenshot chegou (req.channel_id) — canais
    diferentes podem pagar percentuais diferentes."""
    suggestion = await suggest_regear_price(
        db, rec.get("items") or [],
        settings.coverage_for(req.channel_id),
        set(settings.enabled_categories),
        set(settings.disabled_items),
    )
    req.detected_items = suggestion["items"]
    req.base_total = suggestion["base_total"]
    req.suggested_total = suggestion["suggested_total"]
    req.coverage_pct = suggestion["coverage_pct"]
    req.price_basis = suggestion["price_basis"]
    if settings.attendance_multiplier_enabled and req.event_participation_snapshot:
        attendance_pct = max(0, min(100, int(req.event_participation_snapshot.get("percent", 0))))
        req.suggested_total = round(req.suggested_total * attendance_pct / 100)
        req.price_basis = f"{req.price_basis} × {attendance_pct}% presença"
    req.ocr_name = rec.get("ocr_name")
    req.albion_event_id = rec.get("albion_event_id")
    req.death_timestamp = rec.get("death_timestamp")
    req.recognition_status = rec.get("status") if rec.get("status") in ("recognized", "manual") else "manual"
    req.recognition_method = rec.get("method") if rec.get("method") in ("death_api", "ocr", "manual") else "manual"
    req.recognition_confidence = rec.get("confidence") if rec.get("confidence") in ("high", "medium", "low") else "low"
    req.recognition_candidates = list(rec.get("candidates") or [])
    req.recognition_window_match = rec.get("window_match")
    req.recognition_fallback_reason = rec.get("fallback_reason")


async def ingest(
    db: Session, guild: Guild, image_bytes: bytes, filename: str,
    content_type: str | None, requester_user_id: int | None,
    requester_name: str | None, msg_id: str | None, channel_id: str | None = None,
    event_id: int | None = None, parent_channel_id: str | None = None, attachment_id: str | None = None,
    attachment_index: int | None = None, requester_role_ids: list[int] | None = None,
) -> dict:
    """Cria (ou retorna existente) RegearRequest a partir de uma screenshot.

    `event_id`: se passado, vincula ao evento (landmark). Se None, deriva do
    `channel_id` (thread de regear do evento → Event.regear_thread_id)."""
    settings = regear_config.get_regear_settings(guild)
    role_ids = {int(role_id) for role_id in (requester_role_ids or [])}
    if not settings.enabled:
        raise RegearServiceError("regear está desativado nesta guilda")
    if not settings.accepts_requester_roles(role_ids):
        raise RegearServiceError("solicitante não possui um cargo autorizado para regear")
    source_message_id = msg_id
    if source_message_id and attachment_id is None and attachment_index is None:
        raise RegearServiceError("id ou índice do anexo é obrigatório")
    if source_message_id and attachment_id:
        existing = db.scalar(select(RegearRequest).where(
            RegearRequest.guild_id == guild.id,
            RegearRequest.source_message_id == source_message_id,
            RegearRequest.source_attachment_id == attachment_id,
        ))
        if existing is not None:
            return _to_out(existing)
    elif source_message_id and attachment_index is not None:
        existing = db.scalar(select(RegearRequest).where(
            RegearRequest.guild_id == guild.id,
            RegearRequest.source_message_id == source_message_id,
            RegearRequest.source_attachment_index == attachment_index,
        ))
        if existing is not None:
            return _to_out(existing)

    linked_event_id = _event_id_for_channel(db, guild.id, channel_id)
    is_event_thread = (
        linked_event_id is not None
        and str(parent_channel_id) == settings.event_thread_parent_channel_id
    )
    if not is_event_thread and str(channel_id) not in {c.channel_id for c in settings.extra_channels}:
        raise RegearServiceError("canal de origem não está autorizado para regear")
    if event_id is None and is_event_thread:
        event_id = linked_event_id
    if event_id is not None and not is_event_thread:
        raise RegearServiceError("somente mensagens de threads de evento podem ser vinculadas a eventos")
    if event_id is not None and db.scalar(select(Event.id).where(
        Event.id == event_id, Event.guild_id == guild.id,
    )) is None:
        raise RegearServiceError("evento não pertence a esta guilda")

    region = (guild.settings or {}).get("albion_guild_region")
    participant = db.scalar(select(EventParticipant).where(
        EventParticipant.event_id == event_id,
        EventParticipant.guild_id == guild.id,
        EventParticipant.user_id == requester_user_id,
    )) if event_id and requester_user_id else None
    participation = {}
    if participant is not None:
        role_name = db.scalar(select(GameRole.name).where(GameRole.id == participant.game_role_id)) if participant.game_role_id else None
        participation = {"percent": participant.percent, "base_percent": participant.base_percent, "is_valid": participant.is_valid, "role_name": role_name}

    # Salva a imagem ANTES de commitar a read tx — se o processo crashar
    # aqui, só perdemos o arquivo (sem pedido órfão).
    rel_path = _save_image(guild.id, image_bytes, filename, content_type)

    # Cria o pedido pendente ANTES do reconhecimento HTTP. Se o processo
    # crashar durante o _recognize (API do Albion), o pedido já existe no
    # banco com status "pending" e recognition_status "manual" — a fila de
    # retry pode retomar, e o bot já reagiu com ✅.
    req = RegearRequest(
        guild_id=guild.id,
        event_id=event_id,
        requester_user_id=requester_user_id,
        requester_name=requester_name,
        screenshot_path=rel_path,
        screenshot_msg_id=msg_id,
        source_message_id=source_message_id,
        source_attachment_id=attachment_id,
        source_attachment_index=attachment_index,
        channel_id=channel_id,
        requester_role_ids_snapshot=sorted(role_ids),
        event_participation_snapshot=participation,
        recognition_status="manual",
        status="pending",
    )
    db.add(req)
    db.flush()
    db.commit()  # persiste o pedido pendente antes do HTTP

    # Reconhecimento HTTP (pode demorar/falhar). Se falhar, o pedido já
    # está persistido como "manual" — a fila de retry cuida do resto.
    rec = await _recognize(db, guild, image_bytes, requester_user_id, region, event_id)
    await _apply_recognition(db, req, rec, settings)
    db.add(AuditLog(
        guild_id=guild.id, actor_id=requester_user_id, actor_type="bot", source="bot",
        action="regear.ingest", entity="regear_request", entity_id=str(req.id),
        after={"recognition": req.recognition_status, "suggested": req.suggested_total,
                "event_id": req.event_id},
    ))
    db.commit()
    db.refresh(req)
    return _to_out(req)


def list_requests(
    db: Session, guild_id: int, status: str | None = None,
    event_id: int | None = None,
) -> list[dict]:
    q = select(RegearRequest).where(RegearRequest.guild_id == guild_id)
    if status:
        q = q.where(RegearRequest.status == status)
    if event_id is not None:
        q = q.where(RegearRequest.event_id == event_id)
    q = q.order_by(RegearRequest.created_at.desc())
    return [_to_out(r) for r in db.scalars(q)]


def get_request(db: Session, guild_id: int, request_id: int) -> dict | None:
    r = db.get(RegearRequest, request_id)
    if r is None or r.guild_id != guild_id:
        return None
    return _to_out(r)


def get_request_row(db: Session, guild_id: int, request_id: int) -> RegearRequest | None:
    r = db.get(RegearRequest, request_id)
    if r is None or r.guild_id != guild_id:
        return None
    return r


def update_request(
    db: Session, guild_id: int, request_id: int, payload: dict, actor_id: int | None,
    actor_role_ids: set[int] | None = None, actor_is_admin: bool = False,
) -> dict:
    """Atualiza um pedido sem permitir mutações financeiras ambíguas.

    Um pedido pendente pode ser editado e seguir uma única vez para paid,
    denied ou removed. `paid` é imutável; repetir `status=paid` sem alterações
    é idempotente e não gera segundo débito.

    Race condition: dois admins aprovando simultaneamente podiam debitar o
    banco 2x. O row é lido com SELECT FOR UPDATE quando o target é 'paid',
    serializando os dois requests — o segundo vê status='paid' e cai no
    caminho idempotente (return sem débito).
    """
    new_status = payload.get("status")
    # Lock pessimista só no caminho financeiro (paid). Outros updates (edit
    # final_total, notes, denied) não precisam — não há débito.
    if new_status == "paid":
        r = db.scalar(select(RegearRequest).where(
            RegearRequest.id == request_id,
            RegearRequest.guild_id == guild_id,
        ).with_for_update())
    else:
        r = get_request_row(db, guild_id, request_id)
    if r is None:
        raise RegearServiceError("pedido de regear não encontrado")

    member = db.scalar(select(GuildMember).where(
        GuildMember.guild_id == guild_id, GuildMember.user_id == actor_id,
    )) if actor_id else None
    has_manage = bool(member and (actor_is_admin or has_permission(db, member, "events.manage")))
    if set(payload) - {"status"} and not has_manage:
        raise RegearServiceError("permissão de eventos necessária para editar este pedido")

    new_status = payload.get("status")
    if new_status == "removed" and not has_manage:
        raise RegearServiceError("permissão de eventos necessária para remover este pedido")
    before = _to_out(r)
    if r.status == "paid":
        if set(payload) <= {"status"} and new_status in (None, "paid"):
            return _to_out(r)
        raise RegearServiceError("pedido pago é imutável")

    if "final_total" in payload:
        value = payload["final_total"]
        r.final_total = None if value is None else int(value)
        if r.final_total is not None and r.final_total < 0:
            raise RegearServiceError("valor final não pode ser negativo")
    if payload.get("notes") is not None:
        r.notes = payload["notes"]
    if "event_participation_pct" in payload:
        if r.event_id is None:
            raise RegearServiceError("participação só pode ser alterada em regear de evento")
        participation = dict(r.event_participation_snapshot or {})
        participation["percent"] = int(payload["event_participation_pct"])
        r.event_participation_snapshot = participation
    if payload.get("event_role_name") is not None:
        if r.event_id is None:
            raise RegearServiceError("função só pode ser alterada em regear de evento")
        participation = dict(r.event_participation_snapshot or {})
        participation["role_name"] = payload["event_role_name"].strip()
        r.event_participation_snapshot = participation
    if payload.get("detected_items") is not None:
        # Re-soma base/suggested a partir dos itens editados (logística pode
        # corrigir a lista reconhecida manualmente).
        items = list(payload["detected_items"])
        for item in items:
            if not isinstance(item, dict):
                raise RegearServiceError("item detectado inválido")
            for key in ("unit_price", "total_price"):
                value = item.get(key, 0)
                if not isinstance(value, (int, float)) or value < 0:
                    raise RegearServiceError(f"{key} não pode ser negativo")
        r.detected_items = items
        r.base_total = sum(int(i.get("total_price", 0)) for i in items if i.get("eligible"))
    if payload.get("detected_items") is not None or "event_participation_pct" in payload:
        r.suggested_total = round(r.base_total * r.coverage_pct / 100)
        guild_for_multiplier = db.get(Guild, guild_id)
        settings_for_multiplier = regear_config.get_regear_settings(guild_for_multiplier) if guild_for_multiplier else None
        if settings_for_multiplier and settings_for_multiplier.attendance_multiplier_enabled and r.event_participation_snapshot:
            r.suggested_total = round(r.suggested_total * int(r.event_participation_snapshot.get("percent", 0)) / 100)

    if new_status is not None and not regear_status_transition_allowed(r.status, new_status):
        raise RegearServiceError(f"transição de regear inválida: {r.status} -> {new_status}")

    if new_status in ("paid", "denied"):
        guild = db.get(Guild, guild_id)
        settings = regear_config.get_regear_settings(guild) if guild else None
        role_ids = set(actor_role_ids or set())
        allowed_role = bool(settings and role_ids.intersection(settings.approver_role_ids))
        if not (has_manage or allowed_role):
            reason = (
                "este regear exige aprovação de um cargo autorizado"
                if settings and settings.require_approval
                else "permissão de eventos necessária para pagar este regear"
            )
            raise RegearServiceError(reason)

    if new_status in ("paid", "denied", "pending", "removed"):
        r.status = new_status
        if new_status == "paid":
            r.handled_by_user_id = actor_id
            r.handled_at = datetime.now(timezone.utc)
            amount = r.final_total if r.final_total is not None else r.suggested_total
            if amount < 0:
                raise RegearServiceError("valor do pagamento não pode ser negativo")
            if amount == 0:
                raise RegearServiceError(
                    "valor do pagamento é zero; negue ou remova o pedido em vez de aprovar"
                )
            guild = db.scalar(select(Guild).where(Guild.id == guild_id).with_for_update())
            if guild is None or r.requester_user_id is None:
                raise RegearServiceError("pedido sem solicitante não pode ser pago diretamente")
            balance = get_or_create_balance(db, guild_id, r.requester_user_id)
            bank_before = guild.bank_balance
            balance.balance += amount
            balance.total_earned += amount
            guild.bank_balance -= amount
            tx = EconomyTransaction(
                guild_id=guild_id, kind="regear", actor_discord_id=actor_id or r.requester_user_id,
                to_user_id=r.requester_user_id, total_earned_user_id=r.requester_user_id,
                amount=amount, event_id=r.event_id,
                payout_context={"regear_request_id": r.id},
            )
            db.add(tx)
            db.flush()
            r.economy_transaction_id = tx.id
            db.add(AuditLog(
                guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
                action="regear.pay", entity="regear_request", entity_id=str(r.id),
                before={"bank": bank_before},
                after={"bank": guild.bank_balance, "amount": amount, "transaction_id": tx.id},
            ))
        elif new_status == "denied":
            r.handled_by_user_id = actor_id
            r.handled_at = datetime.now(timezone.utc)

    db.flush()
    db.commit()
    db.refresh(r)
    return _to_out(r)


def remove_request(db: Session, guild_id: int, request_id: int, actor_id: int | None) -> None:
    r = get_request_row(db, guild_id, request_id)
    if r is None:
        raise RegearServiceError("pedido de regear não encontrado")
    if r.status == "paid":
        raise RegearServiceError("pedido pago é imutável; use uma correção contábil")
    r.status = "removed"
    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
        action="regear.remove", entity="regear_request", entity_id=str(r.id),
    ))
    db.commit()


def _to_out(r: RegearRequest) -> dict:
    return {
        "id": r.id,
        "guild_id": r.guild_id,
        "event_id": r.event_id,
        "event_title": r.event.title if r.event_id and r.event else None,
        "requester_user_id": r.requester_user_id,
        "requester_name": r.requester_name,
        "source_message_id": r.source_message_id,
        "source_attachment_id": r.source_attachment_id,
        "source_attachment_index": r.source_attachment_index,
        "payment_message_id": r.payment_message_id,
        "payment_message_channel_id": r.payment_message_channel_id,
        "economy_transaction_id": r.economy_transaction_id,
        "requester_role_ids_snapshot": [str(role_id) for role_id in (r.requester_role_ids_snapshot or [])],
        "event_participation_snapshot": dict(r.event_participation_snapshot or {}),
        "screenshot_url": f"/guilds/{r.guild_id}/regear/{r.id}/screenshot",
        "ocr_name": r.ocr_name,
        "albion_event_id": r.albion_event_id,
        "death_timestamp": r.death_timestamp,
        "detected_items": list(r.detected_items or []),
        "base_total": r.base_total,
        "suggested_total": r.suggested_total,
        "final_total": r.final_total,
        "coverage_pct": r.coverage_pct,
        "price_basis": r.price_basis,
        "status": r.status,
        "handled_by_user_id": r.handled_by_user_id,
        "handled_at": r.handled_at,
        "notes": r.notes,
        "created_at": r.created_at,
        "recognition_status": r.recognition_status,
        "recognition_method": r.recognition_method,
        "recognition_confidence": r.recognition_confidence,
        "recognition_candidates": list(r.recognition_candidates or []),
        "recognition_window_match": r.recognition_window_match,
        "recognition_fallback_reason": r.recognition_fallback_reason,
    }
