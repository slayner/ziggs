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
  POST /companion/crash-report     → forwards a bounded diagnostic to Discord
"""
from __future__ import annotations

import asyncio
import json
import ipaddress
import logging
import re
import time
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.auth.session import make_companion_token
from app.db import get_async_session, get_session
from app.models.events import Event, EventSignup
from app.models.loot import ItemPriceCache
from app.models.lootlog import LootLogSubmission
from app.models.tenancy import Guild, User
from app.services import companion_scan, companion_kill_scan, lootlog as lootlog_svc, market_history, prices, profile_warmer
from app.domain.states import EventState
from app.models.companion import RANGE_SIZE
from app.models.prices import ItemPriceLatest
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


# ── Limite de vazão por instalação ────────────────────────────────────────────
#
# Nem o `install_id` nem o IP são confiáveis (o cliente escolhe os dois), então
# isto NÃO impede um atacante decidido — ele troca de id e continua. O objetivo
# é outro: cortar o flood barato e, principalmente, tornar o abuso VISÍVEL, já
# que ultrapassar o teto vira log com id e IP.
#
# O teto é por LINHAS, não por request: 200 requests de 1 linha fazem o mesmo
# estrago que 1 de 200, e o que interessa é quanto dado entra.
#
# ponytail: janela em memória, morre com o processo e não é compartilhada entre
# workers. Serve enquanto o backend é um processo só; com vários, o teto real
# vira N× o configurado. Sobe pra Redis quando isso importar.
_RATE_WINDOW_S = 300.0        # 5 min
_RATE_MAX_ROWS = 5_000        # por instalação, na janela
_MAX_ROWS_PER_REQUEST = 50_000  # teto anti-DoS: acima disso rejeita. Chunk interno em _CHUNK_SIZE
_CHUNK_SIZE = 2_000  # tamanho de cada chunk no processamento interno
_rate_log: dict[str, deque[tuple[float, int]]] = defaultdict(deque)


def _rate_ok(install: str | None, rows: int) -> bool:
    """Registra `rows` e diz se a instalação ainda está dentro do teto.

    Sem install_id (companion antigo) todos caem no mesmo balde "anon" — de
    propósito: quem não se identifica divide a cota com os outros anônimos.

    Revalida o formato em vez de confiar em quem chamou: se um call site passar
    o header cru, id inventado a cada request criaria um balde novo por request
    e o teto viraria decoração. Normalizar aqui mata essa classe de erro.
    """
    key = _install_id(install) or "anon"
    now = time.monotonic()
    win = _rate_log[key]
    while win and now - win[0][0] > _RATE_WINDOW_S:
        win.popleft()
    total = sum(n for _, n in win)
    if total + rows > _RATE_MAX_ROWS:
        return False
    win.append((now, rows))
    return True


# Teto POR CHAMADA das rotas de warm (não por linha): `/warm` porque char
# desconhecido dispara busca na Albion; `/warm/seen` é refresh-only (barato) mas
# ainda capado pra não virar flood. Baldes separados pra um não comer o outro.
_warm_log: dict[str, deque[float]] = defaultdict(deque)
_seen_log: dict[str, deque[float]] = defaultdict(deque)
_crash_log: dict[str, deque[float]] = defaultdict(deque)


def _call_ok(bucket: dict[str, deque[float]], install: str | None, window_s: float, max_calls: int) -> bool:
    key = _install_id(install) or "anon"
    now = time.monotonic()
    win = bucket[key]
    while win and now - win[0] > window_s:
        win.popleft()
    if len(win) >= max_calls:
        return False
    win.append(now)
    return True


def _crash_rate_ok(install: str, ip: str) -> bool:
    """At most 3 reports/hour per install AND per IP."""
    now = time.monotonic()
    buckets = (_crash_log[f"install:{install}"], _crash_log[f"ip:{ip}"])
    for bucket in buckets:
        while bucket and now - bucket[0] > 3600.0:
            bucket.popleft()
    if any(len(bucket) >= 3 for bucket in buckets):
        return False
    for bucket in buckets:
        bucket.append(now)
    return True


def _client_ip(request: Request) -> str:
    # ponytail: X-Forwarded-For primeiro (proxy/reverse-proxy), senão socket direto.
    # Sem validação rigorosa — só pra log, não é decisão de segurança.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _crash_client_ip(request: Request) -> str:
    """Use XFF only when the connection came from a private/local reverse proxy."""
    peer = request.client.host if request.client else "?"
    try:
        behind_proxy = ipaddress.ip_address(peer).is_private
    except ValueError:
        behind_proxy = False
    return _client_ip(request) if behind_proxy else peer


# ─── Crash reports ───────────────────────────────────────────────────────────

_CRASH_REPORT_CHANNEL_ID = "1535988555413979156"


class CrashReportIn(BaseModel):
    kind: Literal["rust_panic", "frontend"]
    version: str = Field(max_length=32, pattern=r"^[A-Za-z0-9._+-]+$")
    os: str = Field(max_length=32, pattern=r"^[A-Za-z0-9._+-]+$")
    arch: str = Field(max_length=32, pattern=r"^[A-Za-z0-9._+-]+$")
    created_at: str = Field(max_length=64)
    uptime_ms: int = Field(ge=0)
    process_id: int = Field(ge=0)
    thread: str = Field(max_length=128)
    message: str = Field(max_length=4_000)
    location: str = Field(max_length=512)
    backtrace: str = Field(max_length=24_000)
    logs: str = Field(max_length=24_000)


async def _send_crash_to_discord(
    report: CrashReportIn,
    install: str,
    bot_token: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    summary = (
        f"Ziggs Companion crash | `{report.kind}` | v`{report.version}` | "
        f"`{report.os}/{report.arch}` | install `{install}`"
    )
    attachment = json.dumps(
        {"install_id": install, **report.model_dump()}, ensure_ascii=False, indent=2,
    ).encode("utf-8")
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        response = await client.post(
            f"https://discord.com/api/channels/{_CRASH_REPORT_CHANNEL_ID}/messages",
            headers={"Authorization": f"Bot {bot_token}"},
            data={"payload_json": json.dumps({
                "content": summary,
                "allowed_mentions": {"parse": []},
            })},
            files={"files[0]": ("crash-report.json", attachment, "application/json")},
        )
        response.raise_for_status()
    finally:
        if own_client:
            await client.aclose()


@router.post("/companion/crash-report", status_code=204)
async def companion_crash_report(
    report: CrashReportIn,
    request: Request,
    x_ziggs_install: str | None = Header(default=None),
) -> Response:
    install = _install_id(x_ziggs_install)
    if install is None:
        raise HTTPException(400, "invalid X-Ziggs-Install")
    ip = _crash_client_ip(request)
    if not _crash_rate_ok(install, ip):
        log.warning("companion/crash-report throttled ip=%s install=%s", ip, install)
        raise HTTPException(429, "too many crash reports")

    from app.config import get_settings
    token = get_settings().discord_bot_token
    if not token:
        log.error("companion/crash-report without DISCORD_BOT_TOKEN configured")
        raise HTTPException(503, "crash reporting unavailable")
    try:
        await _send_crash_to_discord(report, install, token)
    except httpx.HTTPError as exc:
        log.exception("companion/crash-report failed to reach Discord")
        raise HTTPException(502, "Discord unavailable") from exc
    return Response(status_code=204)


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


# ─── VPS manifest dinâmico (tunnel) ──────────────────────────────────────────
# Antes era um JSON estático em frontend/public/vps-manifest.json editado à mão.
# Agora vem do banco: scan_workers com vps_endpoint preenchido e heartbeat recente
# aparecem automaticamente no companion e no site. Adicionar/remover VPS = ligar/
# desligar o scanner da VPS — o heartbeat expira em WORKER_HEARTBEAT_TIMEOUT.
_vps_manifest_cache: list = []  # [monotonic, payload]


async def _build_vps_manifest(db: AsyncSession) -> list[dict]:
    """Lê scan_workers com tunnel metadata e monta o manifest.
    Lista quem tem tunnel configurado, não quem está pingando agora —
    o companion pinga cada VPS pra decidir se está online."""
    from app.models.scan_worker import ScanWorker
    rows = (await db.scalars(
        select(ScanWorker).where(
            ScanWorker.vps_endpoint.is_not(None),
            ScanWorker.vps_endpoint != "",
            ScanWorker.status != "quarantined",
            ScanWorker.credential_revoked.is_(False),
        ).order_by(ScanWorker.id)
    )).all()
    return [
        {
            "id": w.worker_id,
            "label": w.vps_label or w.name,
            "country": w.vps_country or "",
            "endpoint": w.vps_endpoint or "",
            "server_pubkey": w.vps_server_pubkey or "",
            "ping_url": w.vps_ping_url or "",
        }
        for w in rows
    ]


@router.get("/vps-manifest.json")
async def vps_manifest(db: AsyncSession = Depends(get_async_session)):
    """Manifest dinâmico das VPS tunnel — lido do banco (scan_workers).
    O companion busca esta URL em runtime (5min de cache no client).
    Cache de 30s no servidor pra não bater no DB a cada request."""
    now = time.monotonic()
    if _vps_manifest_cache and now - _vps_manifest_cache[0] < 30:
        return JSONResponse({"vps": _vps_manifest_cache[1]})
    vps = await _build_vps_manifest(db)
    _vps_manifest_cache[:] = [now, vps]
    return JSONResponse({"vps": vps})


_vps_pings_cache: list = []  # [timestamp, payload]


@router.get("/companion/vps-pings")
async def companion_vps_pings(db: AsyncSession = Depends(get_async_session)):
    """Pings das VPS até os servidores do Albion, buscados server-side.
    O browser não pode fetchar http:// de VPS numa página https:// (mixed content),
    então o backend faz o proxy. Cache de 30s pra não pingar a cada visitante.
    Lê a lista de VPS do banco (scan_workers), não de um JSON estático."""
    now = time.monotonic()
    if _vps_pings_cache and now - _vps_pings_cache[0] < 30:
        return _vps_pings_cache[1]
    vps_list = await _build_vps_manifest(db)

    async def ping_one(url: str) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=4) as client:
                r = await client.get(url)
                r.raise_for_status()
                return r.json()
        except Exception:
            return None

    results = await asyncio.gather(*(ping_one(v["ping_url"]) for v in vps_list if v["ping_url"]))
    out = [
        {"label": v["label"], "country": v["country"], "pings": p}
        for v, p in zip(vps_list, results)
    ]
    _vps_pings_cache[:] = [now, out]
    return out


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
    guild_id: int
    guild_name: str | None
    title: str | None
    scheduled_at: str | None
    # in_progress = rolando; review = fechou e está sendo revisado (é aqui que
    # o auto-submit dispara).
    state: str


# Estados em que faz sentido o companion mandar loot.
_LOOTLOG_STATES = ("in_progress", "review")


@router.get("/companion/lootlog/active-events")
async def companion_active_events(
    user: User = Depends(deps.require_companion_user_async),
    db: AsyncSession = Depends(get_async_session),
) -> list[CompanionEventOut]:
    """Eventos do usuário em andamento ou em revisão, de TODAS as guildas.

    Sem guild_id: a inscrição (EventSignup) já diz de quais eventos o usuário
    participa e em qual guilda cada um está — pedir o snowflake da guilda ao
    usuário era trabalho manual pra descobrir algo que o backend já sabe.
    """
    rows = (await db.execute(
        select(Event, Guild.name)
        .join(EventSignup, EventSignup.event_id == Event.id)
        .outerjoin(Guild, Guild.id == Event.guild_id)
        .where(
            Event.state.in_(_LOOTLOG_STATES),
            EventSignup.user_id == user.id,
        )
        .order_by(Event.id.desc())
    )).all()
    return [
        CompanionEventOut(
            event_id=ev.id,
            guild_id=ev.guild_id,
            guild_name=guild_name,
            title=ev.title,
            scheduled_at=ev.scheduled_at.isoformat() if ev.scheduled_at else None,
            state=ev.state.value if hasattr(ev.state, "value") else str(ev.state),
        )
        for ev, guild_name in rows
    ]


class CompanionLootlogIngestIn(BaseModel):
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
    (guild_id, event_id, submitter_user_id).

    A guilda vem do EVENTO, não do cliente: antes o companion mandava guild_id
    junto e nada checava se o usuário tinha alguma relação com aquele evento —
    qualquer conta logada podia despejar lootlog em evento de qualquer guilda.
    Agora exige inscrição (EventSignup) no evento, que é o mesmo critério que
    faz o evento aparecer em /active-events.
    """
    signup = db.scalar(
        select(EventSignup).where(
            EventSignup.event_id == body.event_id,
            EventSignup.user_id == user.id,
        )
    )
    if signup is None:
        raise HTTPException(403, "você não está inscrito neste evento")
    event = db.get(Event, body.event_id)
    if event is None:
        raise HTTPException(404, "evento não encontrado")
    if event.guild_id != signup.guild_id:
        raise HTTPException(403, "inscrição não pertence à guilda do evento")
    if event.state != EventState.REVIEW:
        raise HTTPException(409, "lootlog só pode ser enviado durante revisão")

    result = lootlog_svc.ingest(
        db, signup.guild_id, body.event_id, user.id,
        user.global_name or user.username,
        body.file_name, body.csv_text.encode("utf-8"),
    )
    return CompanionLootlogIngestOut(
        id=result.id, row_count=result.row_count,
        silver_total=result.silver_total, is_update=result.is_update,
    )


class SilverEstimateItemIn(BaseModel):
    item_id: str = Field(min_length=1, max_length=128)
    quantity: int = Field(default=1, gt=0, le=1_000_000)


class SilverEstimateIn(BaseModel):
    items: list[SilverEstimateItemIn] = Field(max_length=10_000)


class SilverEstimateOut(BaseModel):
    silver_total: int


@router.post("/companion/lootlog/silver-estimate", response_model=SilverEstimateOut)
async def companion_lootlog_silver_estimate(
    body: SilverEstimateIn,
    db: AsyncSession = Depends(get_async_session),
) -> SilverEstimateOut:
    """Estimativa ILUSTRATIVA do valor em prata dos loots capturados nesta
    sessão. Só pra alimentar o badge da aba Lootlog no companion — não é
    usado em payout/reconcile (o ingest não precifica de propósito, ver
    lootlog.ingest).

    Usa somente preços já presentes no cache local. Itens sem preço são
    ignorados; esta rota pública nunca dispara HTTP por linha."""
    quantities: dict[str, int] = defaultdict(int)
    for item in body.items:
        quantities[item.item_id] += item.quantity

    # Chunkar o SELECT IN — Postgres tem limite de ~32k params por query,
    # e payloads grandes podem ter milhares de item_ids únicos.
    all_ids = list(quantities.keys())
    prices_by_id: dict[str, int] = {}
    for i in range(0, len(all_ids), 500):
        chunk = all_ids[i : i + 500]
        for row in (await db.scalars(
            select(ItemPriceCache).where(ItemPriceCache.item_type.in_(chunk))
        )):
            prices_by_id[row.item_type] = row.silver_value

    return SilverEstimateOut(silver_total=sum(
        prices_by_id.get(item_id, 0) * quantity
        for item_id, quantity in quantities.items()
    ))


# ─── Scan distribuído (sem auth) ─────────────────────────────────────────────

class ScanClaimOut(BaseModel):
    task_id: int
    battle_id_start: int
    battle_id_end: int
    server: str


@router.post("/companion/scan/claim")
async def scan_claim(
    request: Request,
    db: AsyncSession = Depends(get_async_session),
    x_ziggs_install: str | None = Header(None),
) -> ScanClaimOut | None:
    """Pega a próxima tarefa de scan. Retorna 204 se não houver trabalho.

    X-Ziggs-Install identifica a INSTALAÇÃO (1 por PC): a mesma instalação
    pedindo de novo recebe o range que já tem, em vez de acumular ranges."""
    install = _install_id(x_ziggs_install)
    if install is None:
        raise HTTPException(400, "X-Ziggs-Install inválido")
    task = await companion_scan.claim_task(db, install)
    if task is None:
        log.info("companion/scan/claim [%s] sem trabalho", _client_ip(request))
        raise HTTPException(204)  # No Content
    log.info("companion/scan/claim [%s] install=%s region=%s range=%d-%d (task=%d)",
             _client_ip(request), install, task.region,
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
    found: list[int] = Field(default_factory=list, max_length=RANGE_SIZE)
    missing: list[int] = Field(default_factory=list, max_length=RANGE_SIZE)
    errors: list[int] = Field(default_factory=list, max_length=RANGE_SIZE)
    # Nick configurado no companion — crédito (found_by) nas batalhas novas.
    character_name: str | None = None


class ScanReportOut(BaseModel):
    accepted: int
    rejected: int


@router.post("/companion/scan/report")
async def scan_report(
    payload: ScanReportIn,
    request: Request,
    x_ziggs_install: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> ScanReportOut:
    install = _install_id(x_ziggs_install)
    if install is None:
        raise HTTPException(400, "X-Ziggs-Install inválido")
    nick = payload.character_name or "anônimo"
    log.info("companion/scan/report [%s] nick=%s region=%s found=%d missing=%d errors=%d (task=%d)",
             _client_ip(request), nick, payload.region,
             len(payload.found), len(payload.missing), len(payload.errors),
             payload.task_id)
    try:
        accepted, rejected = await companion_scan.report_task(
            db, payload.task_id, payload.found, payload.missing, payload.errors,
            install, payload.region, character_name=payload.character_name,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    log.info("companion/scan/report [%s] task=%d aceito=%d rejeitado=%d",
             _client_ip(request), payload.task_id, accepted, rejected)
    return ScanReportOut(accepted=accepted, rejected=rejected)


# ─── Kill scan distribuído (sem auth) ────────────────────────────────────────

class KillScanClaimOut(BaseModel):
    region: str
    event_id_start: int
    event_id_end: int


@router.post("/companion/kill-scan/claim")
async def kill_scan_claim(
    request: Request,
    region: str | None = Query(None),
    db: AsyncSession = Depends(get_async_session),
    x_ziggs_install: str | None = Header(None),
) -> KillScanClaimOut | None:
    """Pega um range de EventIds pra sondar. 204 = sem trabalho."""
    install = _install_id(x_ziggs_install)
    if install is None:
        raise HTTPException(400, "X-Ziggs-Install inválido")
    result = await companion_kill_scan.claim_kill_range(db, install, region)
    if result is None:
        raise HTTPException(204)
    log.info("companion/kill-scan/claim [%s] install=%s region=%s range=%d-%d",
             _client_ip(request), install, result["region"],
             result["start"], result["end"])
    return KillScanClaimOut(
        region=result["region"],
        event_id_start=result["start"],
        event_id_end=result["end"],
    )


class KillScanReportIn(BaseModel):
    region: str
    event_id_start: int
    event_id_end: int
    found: list[int] = Field(default_factory=list, max_length=200)
    missing: list[int] = Field(default_factory=list, max_length=200)
    errors: list[int] = Field(default_factory=list, max_length=200)


class KillScanReportOut(BaseModel):
    accepted: int
    rejected: int


@router.post("/companion/kill-scan/report")
async def kill_scan_report(
    payload: KillScanReportIn,
    request: Request,
    x_ziggs_install: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> KillScanReportOut:
    install = _install_id(x_ziggs_install)
    if install is None:
        raise HTTPException(400, "X-Ziggs-Install inválido")
    log.info("companion/kill-scan/report [%s] region=%s found=%d missing=%d errors=%d",
             _client_ip(request), payload.region,
             len(payload.found), len(payload.missing), len(payload.errors))
    try:
        accepted, rejected = await companion_kill_scan.report_kill_range(
            db, install, payload.region,
            payload.event_id_start, payload.event_id_end,
            payload.found, payload.missing, payload.errors,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return KillScanReportOut(accepted=accepted, rejected=rejected)


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


@router.get("/companion/items")
def companion_items(response: Response) -> list[dict]:
    """Itens por índice do jogo, pro lootlog do companion virar id + nome.

    O índice (`i`) é a numeração de documento do `formatted/items.txt` do
    ao-data — o MESMO índice que o pacote de loot carrega e que o
    `ao-loot-logger` usa. NÃO é o campo `Index` do `items.json` (numeração
    interna diferente, com delta que cresce — dava item errado no lootlog).
    Ver `market_history._index_to_name`. Inclui variantes `@enchant`.
    """
    response.headers["Cache-Control"] = "public, max-age=86400"
    return market_history.get_index_catalog()


@router.get("/companion/items-map")
def companion_items_map(response: Response) -> dict[str, str]:
    """Mapeamento UniqueName → nome do jogo (EN). O companion usa pra converter
    o ItemTypeId do pacote do mercado em game_name antes de mandar pro backend.
    O frontend usa pra converter catalogId (UniqueName) em game_name antes de
    buscar preços. Cache de 24h — só muda em patch."""
    response.headers["Cache-Control"] = "public, max-age=86400"
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent.parent.parent / "data" / "item_names.json"
    if not p.exists():
        return {}
    return json.loads(p.read_bytes())


@router.get("/companion/price-quotes")
async def companion_price_quotes(
    items: str = Query(description="IDs separados por vírgula"),
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Preços do nosso banco — público, sem auth, sem guild_id.
    Aceita UniqueName (T4_CLOTH_LEVEL2) ou game_name ("Rare Fine Cloth").
    Converte pra game_name (formato do DB) antes de buscar."""
    from app.services.prices import _unique_to_game
    raw_ids = [i.strip() for i in items.split(",") if i.strip()]
    if not raw_ids:
        return {"prices": []}
    item_ids = list(dict.fromkeys(_unique_to_game(i) for i in raw_ids))
    out: list[dict] = []
    for i in range(0, len(item_ids), 500):
        chunk = item_ids[i : i + 500]
        for row in (await db.scalars(
            select(ItemPriceLatest).where(ItemPriceLatest.item_id.in_(chunk))
        )):
            out.append({
                "item_id": row.item_id,
                "city": row.city,
                "quality": row.quality,
                "sell_price_min": row.sell_price_min,
                "price_date": row.price_date.isoformat() if row.price_date else None,
            })
    return {"prices": out}


class CompanionStatsOut(BaseModel):
    active: int


@router.get("/companion/stats")
async def companion_stats(db: AsyncSession = Depends(get_async_session)) -> CompanionStatsOut:
    """Quantos companions ativos agora (instalações distintas, não processos)."""
    return CompanionStatsOut(active=await companion_scan.count_active_companions(db))


class WarmIn(BaseModel):
    name: str
    region: str


@router.post("/companion/warm")
async def companion_warm(body: WarmIn, x_ziggs_install: str | None = Header(default=None)):
    """Companion nomeia um personagem (o próprio do usuário) pra manter o perfil
    quente — útil pra quem raramente cai em ZvZ rastreada (gatherer/solo) e por
    isso nunca era aquecido pelo fluxo de batalhas.

    NOMEAÇÃO só: o backend busca o dado na API pública da Albion, nunca confia
    em stats vindos do cliente (doutrina do battle scan). Teto por install
    porque char desconhecido dispara uma busca na Albion."""
    install = _install_id(x_ziggs_install)
    name = (body.name or "").strip()
    region = (body.region or "").strip().lower()
    if not name or region not in HOSTS:
        raise HTTPException(400, "name/region inválidos")
    if not _call_ok(_warm_log, install, 3600.0, 12):  # ~1×/20min sobra
        raise HTTPException(429, "muitos pedidos de warm")
    return await profile_warmer.warm_by_name(name, region)


class WarmSeenIn(BaseModel):
    region: str
    names: list[str]


@router.post("/companion/warm/seen")
def companion_warm_seen(body: WarmSeenIn, x_ziggs_install: str | None = Header(default=None)):
    """Fase 2: companion reporta players que VÊ em jogo pra mantê-los quentes —
    cobre quem aparece em briga sub-limiar/roaming que o battle tracker pula.

    REFRESH-ONLY de propósito: só enfileira refresh de quem JÁ conhecemos e está
    velho. NÃO faz bootstrap de nome desconhecido (evita amplificar busca na
    Albion com nome aleatório — bootstrap fica só pro próprio char, `/warm`).
    Nome que não casa é ignorado, então lixo do cliente não custa nada."""
    install = _install_id(x_ziggs_install)
    region = (body.region or "").strip().lower()
    names = (body.names or [])[:100]  # teto de tamanho; resto ignorado
    if region not in HOSTS or not names:
        raise HTTPException(400, "region/names inválidos")
    if not _call_ok(_seen_log, install, 3600.0, 20):  # 1×/5min = 12/h, folga
        raise HTTPException(429, "muitos pedidos de warm/seen")
    return profile_warmer.queue_refresh_seen(names, region)


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
    x_ziggs_install: str | None = Header(default=None),
    db: Session = Depends(get_session),
) -> MarketHistorySubmitOut:
    """Ingere histórico de mercado capturado pelo companion (dado público do
    jogo, sem auth). Cada row é um bucket do gráfico do próprio jogo.

    Payloads grandes são chunkados internamente — o companion não precisa
    fragmentar. O upsert já compara age e descarta buckets antigos, então
    não há teto de vazão aqui — aceita tudo (só o anti-DoS de 50k).
    """
    install = _install_id(x_ziggs_install)
    n = len(payload.rows)
    if n > _MAX_ROWS_PER_REQUEST:
        log.warning("companion/market-history/submit [%s] install=%s payload absurdo: %d rows",
                    _client_ip(request), install or "?", n)
        raise HTTPException(413, "payload grande demais")

    sample = next(iter(payload.rows), None)
    sample_str = ""
    if sample:
        sample_str = f" item_id={getattr(sample, 'albion_id', '?')} loc={getattr(sample, 'location', '') or '?'}"
    log.info("companion/market-history/submit [%s] install=%s rows=%d%s",
             _client_ip(request), install or "?", n, sample_str)

    all_rows = [r.model_dump() for r in payload.rows]
    total_accepted = 0
    total_rejected = 0
    for i in range(0, len(all_rows), _CHUNK_SIZE):
        chunk = all_rows[i : i + _CHUNK_SIZE]
        accepted, rejected = market_history.ingest_history(db, chunk)
        total_accepted += accepted
        total_rejected += rejected

    log.info("companion/market-history/submit [%s] aceito=%d rejeitado=%d",
             _client_ip(request), total_accepted, total_rejected)
    return MarketHistorySubmitOut(accepted=total_accepted, rejected=total_rejected)


@router.post("/companion/prices/submit")
async def prices_submit(
    payload: PriceSubmitIn,
    request: Request,
    x_ziggs_install: str | None = Header(default=None),
    db: AsyncSession = Depends(get_async_session),
) -> PriceSubmitOut:
    """Ingere preços de mercado capturados por companions via packet capture.

    Sem auth — preços de marketplace são dados públicos. Cada row é validada
    (item_id + price não-vazio) e upsertada em item_prices/item_prices_latest
    pela mesma lógica do sync_prices.

    Payloads grandes são chunkados internamente. A instalação de origem é
    gravada em `item_prices.source_install` pra dado ruim ser rastreável.
    """
    install = _install_id(x_ziggs_install)
    n = len(payload.rows)
    if n > _MAX_ROWS_PER_REQUEST:
        log.warning("companion/prices/submit [%s] install=%s payload absurdo: %d rows",
                    _client_ip(request), install or "?", n)
        raise HTTPException(413, "payload grande demais")

    sample = next(iter(payload.rows), None)
    sample_str = ""
    if sample:
        sample_str = f" item_id={getattr(sample, 'item_id', '?')} city={getattr(sample, 'city', '') or '?'}"
    log.info("companion/prices/submit [%s] install=%s rows=%d%s",
             _client_ip(request), install or "?", n, sample_str)

    all_rows = [r.model_dump() for r in payload.rows]
    total_accepted = 0
    total_rejected = 0
    for i in range(0, len(all_rows), _CHUNK_SIZE):
        chunk = all_rows[i : i + _CHUNK_SIZE]
        accepted, rejected = await prices.upsert_companion_prices(db, chunk, source_install=install)
        total_accepted += accepted
        total_rejected += rejected

    log.info("companion/prices/submit [%s] aceito=%d rejeitado=%d",
             _client_ip(request), total_accepted, total_rejected)
    return PriceSubmitOut(accepted=total_accepted, rejected=total_rejected)
