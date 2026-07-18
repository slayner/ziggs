"""Companion — endpoints públicos para o app Tauri.

Sem auth (cookie Discord). Battle scan, DNS e prices são APIs públicas do
Albion — não precisa saber quem está pedindo. O dado reportado é validado
contra a API pública do Albion no upsert, não confiamos cegamente no client.

Rotas:
  POST /companion/scan/claim       → pega próxima tarefa pendente (204 = sem trabalho)
  POST /companion/scan/report      → reporta resultados (found/missing/errors)
  GET  /companion/dns/targets      → hostnames dos 3 servidores Albion (pra DNS test)
  POST /companion/prices/submit    → ingere preços capturados via packet capture (Fase 2)
  GET  /companion/latest.json      → manifest do auto-updater (Tauri updater plugin)
  GET  /companion/auth/start       → inicia login Discord (redirect pro OAuth)
  GET  /companion/auth/done        → pós-OAuth: emite token companion, mostra HTML de sucesso
  GET  /companion/auth/poll        → companion faz polling pelo token (nonce)
  GET  /companion/lootlog/active-events → eventos em andamento onde o user está inscrito
  POST /companion/lootlog/ingest   → envia CSV do lootlog pra um evento
"""
from __future__ import annotations

import json
import logging
import re
import time
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.auth.session import make_companion_token
from app.db import get_session
from app.models.events import Event, EventSignup
from app.models.lootlog import LootLogSubmission
from app.models.tenancy import User
from app.services import companion_scan, lootlog as lootlog_svc, market_history, prices
from app.services.player_tracker import HOSTS

router = APIRouter(tags=["companion"])
log = logging.getLogger(__name__)


def _install_id(raw: str | None) -> str | None:
    """Id de instalação do header X-Ziggs-Install (hex de 32 chars).

    NÃO é auth — só identidade, pra 3 processos do mesmo PC não valerem 3
    companions. Por isso a validação é só de formato: qualquer coisa fora do
    padrão vira None (trata como companion antigo, sem header).
    """
    raw = (raw or "").strip().lower()
    return raw if re.fullmatch(r"[0-9a-f]{32}", raw) else None


def _client_ip(request: Request) -> str:
    # ponytail: X-Forwarded-For primeiro (proxy/reverse-proxy), senão socket direto.
    # Sem validação rigorosa — só pra log, não é decisão de segurança.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


# ─── Auto-updater manifest ──────────────────────────────────────────────────
# ponytail: lê um JSON estático do disco — publicar update = editar 1 arquivo.
# Sem arquivo = 204 (sem update). Com arquivo = retorna o JSON; o Tauri compara
# versões sozinho e só instala se for mais nova.
_RELEASE_FILE = Path(__file__).resolve().parents[3] / "data" / "companion-release.json"


@router.get("/companion/latest.json")
def companion_latest():
    """Manifest do auto-updater. Tauri compara version vs current_version;
    se igual/menor, ignora (sem update). 204 se não houver manifest publicado."""
    if not _RELEASE_FILE.exists():
        raise HTTPException(204)
    data = json.loads(_RELEASE_FILE.read_text(encoding="utf-8"))
    return Response(content=json.dumps(data), media_type="application/json")


# ─── Discord OAuth pairing (companion login, opcional) ─────────────────────
# ponytail: cache em memória nonce → {uid, token, username}. O companion gera
# um nonce, abre o navegador pra /companion/auth/start?nonce=X, o backend faz
# o OAuth normal (cookie de sessão), e /companion/auth/done cunha o token e
# guarda aqui. O companion faz poll em /companion/auth/poll?nonce=X.
# Em memória: se o backend reinicia no meio de um pairing, o user re-loga. OK.
_PAIRING_CACHE: dict[str, dict] = {}
_PAIRING_TTL = 300  # 5 min


@router.get("/companion/auth/start")
def companion_auth_start(nonce: str = Query(...)):
    """Inicia login Discord vindo do companion. Redireciona pro OAuth normal
    com next=/companion/auth/done?nonce=... — depois do callback, o backend
    cai em /companion/auth/done que cunha o token."""
    next_url = f"/companion/auth/done?nonce={nonce}"
    state = __import__("secrets").token_urlsafe(24)
    import app.auth.discord as _discord
    from app.config import get_settings
    resp = RedirectResponse(_discord.build_authorize_url(state))
    resp.set_cookie("ziggs_oauth_state", state, max_age=600, httponly=True, samesite="lax")
    resp.set_cookie("ziggs_oauth_next", next_url, max_age=600, httponly=True, samesite="lax")
    return resp


@router.get("/companion/auth/done")
def companion_auth_done(
    nonce: str = Query(...),
    user: User = Depends(deps.require_user),
):
    """Pós-OAuth: o user já está logado (cookie de sessão definido pelo callback).
    Cunha o token companion, guarda no cache de pairing, mostra HTML de sucesso."""
    token = make_companion_token(user.id)
    _PAIRING_CACHE[nonce] = {
        "uid": user.id,
        "username": user.username,
        "global_name": user.global_name,
        "token": token,
        "ts": time.monotonic(),
    }
    return HTMLResponse("""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{background:#0e0f13;color:#e7e9ee;font-family:system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.box{text-align:center}.ok{font-size:48px;margin-bottom:12px}
.hint{color:#6b7280;font-size:14px;margin-top:8px}</style></head><body>
<div class="box"><div class="ok">✓</div><h2>Login concluído</h2>
<div class="hint">Volte para o Ziggs Companion — você já pode fechar esta aba.</div>
</div></body></html>""")


@router.get("/companion/auth/poll")
def companion_auth_poll(nonce: str = Query(...)):
    """Companion faz poll aqui até o pairing ficar pronto (ou expirar)."""
    _expire_old_pairings()
    entry = _PAIRING_CACHE.pop(nonce, None)
    if entry is None:
        raise HTTPException(408, "aguardando login")  # companion re-tenta
    return {
        "token": entry["token"],
        "user_id": str(entry["uid"]),
        "username": entry["username"],
        "global_name": entry["global_name"],
    }


def _expire_old_pairings():
    now = time.monotonic()
    expired = [k for k, v in _PAIRING_CACHE.items() if now - v["ts"] > _PAIRING_TTL]
    for k in expired:
        del _PAIRING_CACHE[k]


# ─── Lootlog auto-submit (companion auth) ──────────────────────────────────

class CompanionEventOut(BaseModel):
    event_id: int
    title: str | None
    scheduled_at: str | None


@router.get("/companion/lootlog/active-events")
def companion_active_events(
    guild_id: int = Query(...),
    user: User = Depends(deps.require_companion_user),
    db: Session = Depends(get_session),
) -> list[CompanionEventOut]:
    """Eventos em andamento (IN_PROGRESS) onde o usuário está inscrito.
    O companion lista esses pro user escolher pra onde mandar o lootlog."""
    rows = db.execute(
        select(Event, EventSignup)
        .join(EventSignup, EventSignup.event_id == Event.id)
        .where(
            Event.guild_id == guild_id,
            Event.state == "in_progress",
            EventSignup.user_id == user.id,
        )
        .order_by(Event.id.desc())
    ).all()
    return [
        CompanionEventOut(
            event_id=ev.id,
            title=ev.title,
            scheduled_at=ev.scheduled_at.isoformat() if ev.scheduled_at else None,
        )
        for ev, _ in rows
    ]


class CompanionLootlogIngestIn(BaseModel):
    guild_id: int
    event_id: int
    csv_text: str
    file_name: str = "companion-lootlog.csv"


class CompanionLootlogIngestOut(BaseModel):
    id: int
    row_count: int
    silver_total: int
    is_update: bool


@router.post("/companion/lootlog/ingest")
def companion_lootlog_ingest(
    body: CompanionLootlogIngestIn,
    user: User = Depends(deps.require_companion_user),
    db: Session = Depends(get_session),
) -> CompanionLootlogIngestOut:
    """Submete o CSV do lootlog (texto normalizado) pra um evento.
    Mesmo upsert do /guilds/{g}/lootlog/ingest — 1 submissão por
    (guild_id, event_id, submitter_user_id)."""
    result = lootlog_svc.ingest(
        db, body.guild_id, body.event_id, user.id,
        user.global_name or user.username,
        body.file_name, body.csv_text.encode("utf-8"),
    )
    return CompanionLootlogIngestOut(
        id=result.id, row_count=result.row_count,
        silver_total=result.silver_total, is_update=result.is_update,
    )


# ─── Scan distribuído (sem auth) ─────────────────────────────────────────────

class ScanClaimOut(BaseModel):
    task_id: int
    battle_id_start: int
    battle_id_end: int
    server: str


@router.post("/companion/scan/claim")
def scan_claim(
    request: Request,
    db: Session = Depends(get_session),
    x_ziggs_install: str | None = Header(None),
) -> ScanClaimOut | None:
    """Pega a próxima tarefa de scan. Retorna 204 se não houver trabalho.

    X-Ziggs-Install identifica a INSTALAÇÃO (1 por PC): a mesma instalação
    pedindo de novo recebe o range que já tem, em vez de acumular ranges."""
    task = companion_scan.claim_task(db, _install_id(x_ziggs_install))
    if task is None:
        log.info("companion/scan/claim [%s] sem trabalho", _client_ip(request))
        raise HTTPException(204)  # No Content
    log.info("companion/scan/claim [%s] install=%s region=%s range=%d-%d (task=%d)",
             _client_ip(request), _install_id(x_ziggs_install) or "?", task.region,
             task.battle_id_start, task.battle_id_end, task.id)
    return ScanClaimOut(
        task_id=task.id,
        battle_id_start=task.battle_id_start,
        battle_id_end=task.battle_id_end,
        server=task.region,
    )


class ScanReportIn(BaseModel):
    task_id: int
    region: str
    found: list[dict] = []
    missing: list[int] = []
    errors: list[int] = []
    # Nick configurado no companion — crédito (found_by) nas batalhas novas.
    character_name: str | None = None


class ScanReportOut(BaseModel):
    accepted: int
    rejected: int


@router.post("/companion/scan/report")
def scan_report(
    payload: ScanReportIn,
    request: Request,
    db: Session = Depends(get_session),
) -> ScanReportOut:
    nick = payload.character_name or "anônimo"
    log.info("companion/scan/report [%s] nick=%s region=%s found=%d missing=%d errors=%d (task=%d)",
             _client_ip(request), nick, payload.region,
             len(payload.found), len(payload.missing), len(payload.errors),
             payload.task_id)
    accepted, rejected = companion_scan.report_task(
        db, payload.task_id, payload.found, payload.missing, payload.errors,
        character_name=payload.character_name,
    )
    log.info("companion/scan/report [%s] task=%d aceito=%d rejeitado=%d",
             _client_ip(request), payload.task_id, accepted, rejected)
    return ScanReportOut(accepted=accepted, rejected=rejected)


# ─── Nomes de feitiço (damage meter) ─────────────────────────────────────────
# Gerado por scripts/seed_spell_names.py a partir do spells.xml do ao-bin-dumps.
# Lista ORDENADA: a posição no array é o índice que assumimos vir no pacote.
_SPELLS_FILE = Path(__file__).resolve().parents[3] / "data" / "spell_names.json"


@lru_cache(maxsize=1)
def _spell_names() -> list[dict]:
    try:
        return json.loads(_SPELLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


@router.get("/companion/spells")
def companion_spells(response: Response) -> list[dict]:
    """Nomes de feitiço por índice, pro damage meter do companion.

    Estático por deploy (o companion baixa uma vez e cacheia em disco). Lista
    vazia = dump não seedado; o companion cai no fallback "Habilidade {id}".

    ATENÇÃO: índice = posição no documento é HIPÓTESE não verificada — o dump
    não traz índice explícito. Precisa de calibração contra tráfego real.
    """
    response.headers["Cache-Control"] = "public, max-age=86400"
    return _spell_names()


class CompanionStatsOut(BaseModel):
    active: int


@router.get("/companion/stats")
def companion_stats(db: Session = Depends(get_session)) -> CompanionStatsOut:
    """Quantos companions ativos agora (instalações distintas, não processos)."""
    return CompanionStatsOut(active=companion_scan.count_active_companions(db))


# ─── DNS targets ─────────────────────────────────────────────────────────────

class DnsTarget(BaseModel):
    region: str
    hostname: str


@router.get("/companion/dns/targets")
def dns_targets() -> dict:
    """Retorna hostnames dos 3 servidores Albion por região — o companion
    testa latência/jitter contra esses hostnames."""
    servers = [DnsTarget(region=r, hostname=h) for r, h in HOSTS.items()]
    return {"servers": servers}


# ─── Prices (Fase 2 — packet capture) ────────────────────────────────────────

class PriceRowIn(BaseModel):
    item_id: str
    city: str
    quality: int = 1
    sell_price_min: int
    # ISO string — companion envia o timestamp do capture.
    price_date: str | None = None


class PriceSubmitIn(BaseModel):
    rows: list[PriceRowIn]


class PriceSubmitOut(BaseModel):
    accepted: int
    rejected: int


# ─── Market history (captura própria do gráfico do jogo) ─────────────────────

class MarketHistoryRowIn(BaseModel):
    albion_id: int
    region: str = "west"  # servidor do Albion detectado pelo companion (IP)
    quality: int = 1
    location: str = ""
    timescale: int = 1
    bucket_ts: int
    item_count: int
    silver_amount: int


class MarketHistorySubmitIn(BaseModel):
    rows: list[MarketHistoryRowIn]


class MarketHistorySubmitOut(BaseModel):
    accepted: int
    rejected: int


@router.post("/companion/market-history/submit")
def market_history_submit(
    payload: MarketHistorySubmitIn,
    request: Request,
    db: Session = Depends(get_session),
) -> MarketHistorySubmitOut:
    """Ingere histórico de mercado capturado pelo companion (dado público do
    jogo, sem auth). Cada row é um bucket do gráfico do próprio jogo."""
    sample = next(iter(payload.rows), None)
    sample_str = ""
    if sample:
        sample_str = f" item_id={getattr(sample, 'albion_id', '?')} loc={getattr(sample, 'location', '') or '?'}"
    log.info("companion/market-history/submit [%s] rows=%d%s",
             _client_ip(request), len(payload.rows), sample_str)
    rows = [r.model_dump() for r in payload.rows]
    accepted, rejected = market_history.ingest_history(db, rows)
    log.info("companion/market-history/submit [%s] aceito=%d rejeitado=%d",
             _client_ip(request), accepted, rejected)
    return MarketHistorySubmitOut(accepted=accepted, rejected=rejected)


@router.post("/companion/prices/submit")
def prices_submit(
    payload: PriceSubmitIn,
    request: Request,
    db: Session = Depends(get_session),
) -> PriceSubmitOut:
    """Ingere preços de mercado capturados por companions via packet capture.

    Sem auth — preços de marketplace são dados públicos. Cada row é validada
    (item_id + price não-vazio) e upsertada em item_prices/item_prices_latest
    pela mesma lógica do sync_prices.
    """
    sample = next(iter(payload.rows), None)
    sample_str = ""
    if sample:
        sample_str = f" item_id={getattr(sample, 'item_id', '?')} city={getattr(sample, 'city', '') or '?'}"
    log.info("companion/prices/submit [%s] rows=%d%s",
             _client_ip(request), len(payload.rows), sample_str)
    rows = [r.model_dump() for r in payload.rows]
    accepted, rejected = prices.upsert_companion_prices(db, rows)
    log.info("companion/prices/submit [%s] aceito=%d rejeitado=%d",
             _client_ip(request), accepted, rejected)
    return PriceSubmitOut(accepted=accepted, rejected=rejected)