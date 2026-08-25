"""Lootlog anônimo: parse+store dos .csv do lootlogger por evento, peso dos loggers.

O peso de um logger = contagem de COLETAS CORROBORADAS (tratamento bot-v1, em
_compute_weights): 2+ loggers que registram a mesma coleta (mesma chave
item_id/quantity/looted_by/looted_from dentro de 60s) contam +1 cada; 1 logger
só conta todas as únicas. Cópias (mesmo file_hash OU ≥85% de tuplas idênticas)
são excluídas antes. Janela do evento: started_at-5min … ended_at+15min.

O ingest NÃO precifica (antes batia 1 HTTP por item na API albion-online-data e
o bot timed out em logs grandes → "Falha ao enviar o log ao site"). silver_total
fica 0; o carve da fatia `logger_percent` divide o logger_pool pelo peso de
corroboração (hook em services/events._calc_payout; preview admin em
GET /lootlog/preview/{event_id}).
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas.lootlog import (
    LootLogIngestOut, LootLogPreviewOut, LootLogSubmissionOut,
    LoggerPayoutRowOut,
)
from app.models.audit import AuditLog
from app.models.events import Event
from app.models.lootlog import LootLogSubmission
from app.models.tenancy import Guild

# Colunas essenciais do .csv do ao-loot-logger (tolera extras/ordem).
REQUIRED_COLS = {
    "timestamp_utc", "looted_by__guild", "looted_by__name",
    "item_id", "quantity", "looted_from__name",
}
MAX_FILE_BYTES = 15 * 1024 * 1024  # 15 MB
DEFAULT_LOGGER_PERCENT = 5
KEY = "lootlog"  # namespace em guild.settings

# ── tratamento bot-v1 (corroboração) ─────────────────────────────────────────
# Janela do CTA p/ considerar coletas: começo - LEAD até fim + GRACE.
WINDOW_LEAD_MIN = 5
WINDOW_GRACE_MIN = 15
# Mesma coleta vista por loggers diferentes em até ~1 min (o lootlogger gera
# timestamps defasados entre máquinas; janela curta marcava 'fonte única').
DEDUP_WINDOW_S = 60.0
# >=85% de tuplas (ts,item,looter,vítima) idênticas entre 2 submissões = cópia.
COPY_OVERLAP_THRESHOLD = 0.85
COPY_MIN_EVENTS = 5  # ignora logs minúsculos (pouca evidência)

_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$"
)


class LootLogServiceError(Exception):
    pass


# ── config (Guild.settings["lootlog"]) ──────────────────────────────────────

@dataclass
class LootLogSettings:
    logger_percent: int = DEFAULT_LOGGER_PERCENT
    # Feature era sempre-ativa (sem toggle no site) — default True preserva o
    # comportamento de guildas existentes que nunca setaram isto explicitamente.
    enabled: bool = True

    def to_dict(self) -> dict:
        return {"logger_percent": self.logger_percent, "enabled": self.enabled}


def get_lootlog_settings(guild: Guild) -> LootLogSettings:
    s = (guild.settings or {}).get(KEY) or {}
    pct = s.get("logger_percent", DEFAULT_LOGGER_PERCENT)
    try:
        pct = max(0, min(100, int(pct)))
    except (TypeError, ValueError):
        pct = DEFAULT_LOGGER_PERCENT
    return LootLogSettings(logger_percent=pct, enabled=bool(s.get("enabled", True)))


def apply_lootlog_settings(guild: Guild, data: dict) -> LootLogSettings:
    cur = get_lootlog_settings(guild)
    if "logger_percent" in data and data["logger_percent"] is not None:
        try:
            cur.logger_percent = max(0, min(100, int(data["logger_percent"])))
        except (TypeError, ValueError):
            pass
    if "enabled" in data and data["enabled"] is not None:
        cur.enabled = bool(data["enabled"])
    settings = dict(guild.settings or {})
    settings[KEY] = cur.to_dict()
    guild.settings = settings
    return cur


# ── parse do .csv (portado do bot-v1, simplificado: sem janela/reconciliação) ─

def _parse_header_cols(text: str) -> set[str]:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return {c.strip() for c in line.split(";")}
    return set()


def _parse_ts(s: str) -> str | None:
    """Devolve ISO string normalizada (UTC), ou None se ilegível."""
    if not s:
        return None
    s = s.strip()
    m = _TS_RE.match(s)
    if m:
        base, frac, tz = m.group(1), m.group(2), m.group(3)
        iso = base.replace(" ", "T")
        if frac:
            iso += "." + frac[:6]
        if not tz or tz == "Z":
            iso += "+00:00"
        elif len(tz) == 5:
            iso += tz[:3] + ":" + tz[3:]
        else:
            iso += tz
        return iso
    return s.replace("Z", "+00:00") or None


def parse_loot_rows(text: str) -> list[dict]:
    """Extrai as COLETAS (linhas com item_id + looted_by) do .csv do lootlogger.
    Sem filtro de janela aqui — a janela do evento (started-5min…ended+15min) é
    aplicada depois em _load_events/_compute_weights, na hora de pesar."""
    header: list[str] | None = None
    idx: dict[str, int] = {}
    rows: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = [c.strip() for c in line.split(";")]
        if header is None:
            header = cols
            idx = {name: i for i, name in enumerate(cols)}
            continue

        def get(name: str) -> str:
            i = idx.get(name)
            return cols[i] if (i is not None and i < len(cols)) else ""

        item_id = get("item_id")
        looted_by = get("looted_by__name")
        if not item_id or not looted_by:
            continue  # linha de morte / sem coleta
        try:
            qty = int(get("quantity") or 1)
        except ValueError:
            qty = 1
        rows.append({
            "ts": _parse_ts(get("timestamp_utc")),
            "item_id": item_id,
            "item_name": get("item_name"),
            "quantity": qty,
            "looted_by": looted_by,
            "looted_by_guild": get("looted_by__guild"),
            "looted_from": get("looted_from__name"),
        })
    return rows


def _row_dt(s: str | None):
    """datetime aware a partir do ISO string guardado em loot_rows (ou None)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _event_window(ev: Event):
    """(início - LEAD, fim + GRACE) do evento. (None,None) se não há anchor."""
    start = ev.started_at or ev.scheduled_at
    if start is None:
        return None, None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    end = ev.ended_at
    if end is None:
        end = datetime.now(timezone.utc)
    elif end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return (start - timedelta(minutes=WINDOW_LEAD_MIN),
            end + timedelta(minutes=WINDOW_GRACE_MIN))


def _mk_canon(cluster: list[tuple]) -> dict:
    """Evento canônico de um cluster de detecções (mesma coleta)."""
    witness_ids = {c[2] for c in cluster}  # c = (dt, row, submitter_id)
    dt0, e0 = cluster[0][0], cluster[0][1]
    return {
        "ts": dt0,
        "item_id": e0.get("item_id"),
        "quantity": int(e0.get("quantity") or 1),
        "looted_by": e0.get("looted_by"),
        "looted_from": e0.get("looted_from"),
        "witness_ids": witness_ids,
    }


def _reconcile(events: list[tuple]) -> list[dict]:
    """Funde detecções de vários submitters em eventos canônicos. Chave:
    (item_id, quantity, looted_by, looted_from) + timestamps em até DEDUP_WINDOW_S.
    witness_ids = set de submitters que viram (1 = fonte única)."""
    groups: dict[tuple, list[tuple]] = defaultdict(list)
    for dt, row, sid in events:
        if dt is None:
            continue
        key = (row.get("item_id"), int(row.get("quantity") or 1),
               row.get("looted_by"), row.get("looted_from"))
        groups[key].append((dt, row, sid))
    canonical: list[dict] = []
    for _key, lst in groups.items():
        lst.sort(key=lambda x: x[0])
        cluster: list[tuple] = []
        for dt, row, sid in lst:
            if cluster and (dt - cluster[-1][0]).total_seconds() > DEDUP_WINDOW_S:
                canonical.append(_mk_canon(cluster))
                cluster = []
            cluster.append((dt, row, sid))
        if cluster:
            canonical.append(_mk_canon(cluster))
    return canonical


def _detect_copies(
    events: list[tuple], subs: list[dict],
) -> tuple[set[int], list[tuple]]:
    """Detecta submissões COPIADAS: mesmo file_hash OU >=85% de tuplas
    (ts,item_id,looted_by,looted_from) idênticas. Mantém o submitter MAIS ANTIGO
    (created_at menor) e exclui os posteriores. Retorna (excluded_ids, notes)."""
    # ordem de envio (mais antigo primeiro) decide quem fica.
    order: dict[int, int] = {}
    for i, s in enumerate(sorted(subs, key=lambda x: (x.get("created_at") or datetime.min))):
        sid = s.get("submitter_id")
        if sid is not None:
            order[sid] = i

    def keeper(a: int, b: int) -> int:
        return a if order.get(a, 1 << 30) <= order.get(b, 1 << 30) else b

    by_sub: dict[int, set] = defaultdict(set)
    for dt, row, sid in events:
        if sid is None:
            continue
        by_sub[sid].add((row.get("ts"), row.get("item_id"),
                         row.get("looted_by"), row.get("looted_from")))
    hash_by_sub = {s.get("submitter_id"): s.get("file_hash") for s in subs}

    ids = [i for i in by_sub if i is not None]
    excluded: set[int] = set()
    notes: list[tuple] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            ta, tb = by_sub[a], by_sub[b]
            if not ta or not tb:
                continue
            ha, hb = hash_by_sub.get(a), hash_by_sub.get(b)
            same_hash = bool(ha) and ha == hb
            ratio = len(ta & tb) / min(len(ta), len(tb))
            if same_hash or (min(len(ta), len(tb)) >= COPY_MIN_EVENTS
                             and ratio >= COPY_OVERLAP_THRESHOLD):
                keep = keeper(a, b)
                drop = b if keep == a else a
                if drop not in excluded:
                    excluded.add(drop)
                    motivo = ("arquivo idêntico" if same_hash
                              else f"{ratio:.0%} de timestamps iguais")
                    notes.append((drop, keep, motivo))
    return excluded, notes


def _load_events(db: Session, guild_id: int, event_id: int):
    """Carrega submissions + rows taggeadas p/ o tratamento. Devolve
    (events, subs) onde events = [(dt, row, submitter_id)] (dentro da janela) e
    subs = [{submitter_id, created_at, file_hash}]. Pula submitter None."""
    ev = db.get(Event, event_id)
    if ev is None or ev.guild_id != guild_id:
        return None, None
    win_start, win_end = _event_window(ev)
    subs_rows = db.scalars(select(LootLogSubmission).where(
        LootLogSubmission.guild_id == guild_id,
        LootLogSubmission.event_id == event_id,
    )).all()
    events: list[tuple] = []
    subs: list[dict] = []
    for sub in subs_rows:
        sid = sub.submitter_user_id
        if sid is None:
            continue
        subs.append({"submitter_id": sid, "created_at": sub.created_at,
                     "file_hash": sub.file_hash})
        for r in (sub.loot_rows or []):
            dt = _row_dt(r.get("ts"))
            if dt is None:
                continue
            if win_start is not None and dt < win_start:
                continue
            if win_end is not None and dt > win_end:
                continue
            events.append((dt, r, sid))
    return events, subs


def _compute_weights(events: list[tuple], subs: list[dict]) -> dict[int, int]:
    """Núcleo bot-v1: detecta cópias, reconcilia o resto e calcula o peso de
    cada logger = contagem de coletas corroboradas (2+ loggers), ou todas as
    únicas se só há 1 logger válido. Solo-only (2+ loggers) contam zero.

    Importante: com 1 logger o peso NÃO espera 2º p/ corroborar — conta todas
    as únicas imediatamente. O 'esperar mais logs p/ só considerar duplicados'
    é o caminho 2+ loggers (só overlap conta); single-logger foge dele.

    Logger = quem VIU coleta dentro da janela. Submissão com todas as rows
    fora da janela (ou vazia) NÃO é logger: é filtrada aqui, não conta como
    cópia, não corrobora nada e não pode rebaixar o único logger real pro
    caminho 2+ (onde só overlap conta → peso 0 pra quem logou tudo sozinho)."""
    if not subs:
        return {}
    # Filtra subs: só quem tem pelo menos 1 evento in-window é logger.
    sids_with_events = {e[2] for e in events if e[2] is not None}
    active_subs = [s for s in subs if s.get("submitter_id") in sids_with_events]
    if not active_subs:
        return {}
    excluded, _notes = _detect_copies(events, active_subs)
    clean = [e for e in events if e[2] not in excluded]
    canonical = _reconcile(clean)
    seen_sids = {e[2] for e in clean}
    single_logger = len(seen_sids) <= 1
    weights: dict[int, int] = defaultdict(int)
    for c in canonical:
        wids = c["witness_ids"]
        if single_logger:
            for sid in wids:
                weights[sid] += 1
        elif len(wids) >= 2:
            for sid in wids:
                weights[sid] += 1
    return dict(weights)


def _validate_text(text: str) -> None:
    cols = _parse_header_cols(text)
    missing = REQUIRED_COLS - cols
    if missing:
        raise LootLogServiceError(f"arquivo não parece um log do lootlogger; "
                                  f"faltam colunas: {', '.join(sorted(missing))}")


# ── ingest ──────────────────────────────────────────────────────────────────

def ingest(
    db: Session, guild_id: int, event_id: int,
    submitter_user_id: int | None, submitter_name: str | None,
    file_name: str, file_bytes: bytes,
) -> LootLogIngestOut:
    """Cria (ou sobrescreve) a submissão do logger p/ este CTA. Só parse+store do
    texto cru — sem precificação (o bot timed out quando o cache de preços estava
    frio; o carve usa peso de corroboração, não silver)."""
    ev = db.get(Event, event_id)
    if ev is None or ev.guild_id != guild_id:
        raise LootLogServiceError("CTA não encontrado nesta guilda")

    guild = db.get(Guild, guild_id)
    if guild is not None and not get_lootlog_settings(guild).enabled:
        raise LootLogServiceError("lootlog desativado nesta guilda")

    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LootLogServiceError("arquivo não é texto UTF-8 válido")
    _validate_text(text)
    rows = parse_loot_rows(text)
    if not rows:
        raise LootLogServiceError("log vazio (nenhuma coleta com item_id)")

    # ponytail: NÃO precifica mais no ingest. Era 1 HTTP por item_id não-cachêd
    # (loot.get_price → API albion-online-data, 5s cada) — num log real com dezenas
    # de itens únicos e cache frio o ingest passava de 20s e o bot timed out
    # ("Falha ao enviar o log ao site"). silver_total não é load-bearing em payout
    # nenhum (_calc_payout usa compute_logger_weights, corroboração) e a UI admin
    # só mostra raw_text. Removido o valor → ingest vira parse+store só.
    silver_total = 0

    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # Upsert por (guild_id, event_id, submitter_user_id). Reenvio sobrescreve.
    existing: LootLogSubmission | None = None
    if submitter_user_id is not None:
        existing = db.scalar(select(LootLogSubmission).where(
            LootLogSubmission.guild_id == guild_id,
            LootLogSubmission.event_id == event_id,
            LootLogSubmission.submitter_user_id == submitter_user_id,
        ))
    is_update = existing is not None
    if existing is None:
        existing = LootLogSubmission(
            guild_id=guild_id, event_id=event_id,
            submitter_user_id=submitter_user_id, submitter_name=submitter_name,
        )
        db.add(existing)

    existing.submitter_name = submitter_name
    existing.file_name = file_name
    existing.file_hash = file_hash
    existing.row_count = len(rows)
    existing.loot_rows = rows
    existing.silver_total = silver_total
    existing.raw_text = text  # texto cru do arquivo — a UI admin mostra isto
    db.flush()

    db.add(AuditLog(
        guild_id=guild_id, actor_id=submitter_user_id, actor_type="site", source="site",
        action="lootlog.ingest", entity="lootlog_submission", entity_id=str(existing.id),
        after={"event_id": event_id, "rows": len(rows), "update": is_update},
    ))
    db.commit()
    db.refresh(existing)
    return LootLogIngestOut(
        id=existing.id, row_count=len(rows), silver_total=silver_total, is_update=is_update,
    )


# ── leitura (área admin) ────────────────────────────────────────────────────

def _to_out(sub: LootLogSubmission) -> LootLogSubmissionOut:
    """Saída só-admin: texto cru do arquivo (idêntico ao .csv, copiável) + quem
    enviou. Sem parse/valor — o carve do logger_pool vive em compute_logger_weights
    (chamado por _calc_payout no finalize), não aqui."""
    return LootLogSubmissionOut(
        id=sub.id, event_id=sub.event_id,
        submitter_user_id=sub.submitter_user_id, submitter_name=sub.submitter_name,
        file_name=sub.file_name, raw_text=sub.raw_text,
        created_at=sub.created_at,
    )


def list_submissions(db: Session, guild_id: int, event_id: int) -> list[LootLogSubmissionOut]:
    rows = db.scalars(select(LootLogSubmission).where(
        LootLogSubmission.guild_id == guild_id,
        LootLogSubmission.event_id == event_id,
    ).order_by(LootLogSubmission.created_at.desc())).all()
    return [_to_out(r) for r in rows]


def get_submission(db: Session, guild_id: int, submission_id: int) -> LootLogSubmissionOut | None:
    sub = db.get(LootLogSubmission, submission_id)
    if sub is None or sub.guild_id != guild_id:
        return None
    return _to_out(sub)


def remove_submission(db: Session, guild_id: int, submission_id: int, actor_id: int | None) -> None:
    sub = db.get(LootLogSubmission, submission_id)
    if sub is None or sub.guild_id != guild_id:
        raise LootLogServiceError("submissão não encontrada")
    db.delete(sub)
    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
        action="lootlog.remove", entity="lootlog_submission", entity_id=str(submission_id),
        before={"event_id": sub.event_id, "submitter": sub.submitter_name},
    ))
    db.commit()


# ── peso dos loggers + preview da fatia ─────────────────────────────────────

def compute_logger_weights(db: Session, guild_id: int, event_id: int) -> dict[int, int]:
    """{submitter_user_id: peso} via tratamento bot-v1: detecção de cópia +
    reconciliação + contagem de coletas corroboradas (2+ loggers, ou todas as
    únicas se só 1 logger). Usado por services/events._calc_payout p/ carve da
    fatia. Ponytail: re-roda a cada _calc_payout (não persiste) — mesmas rows
    JSON do ingest, sem custo extra de API."""
    events, subs = _load_events(db, guild_id, event_id)
    if events is None or not subs:
        return {}
    return _compute_weights(events, subs)


def preview(
    db: Session, guild: Guild, event_id: int,
) -> LootLogPreviewOut | None:
    """Preview da fatia do logger p/ um CTA (área admin do site). Peso =
    corroboração bot-v1 (mesmo compute_logger_weights)."""
    ev = db.get(Event, event_id)
    if ev is None or ev.guild_id != guild.id:
        return None
    settings = get_lootlog_settings(guild)
    weights = compute_logger_weights(db, guild.id, event_id)
    total_w = sum(weights.values())
    pool = (ev.tab_value * settings.logger_percent) // 100

    name_of = {s.submitter_user_id: s.submitter_name for s in
               db.scalars(select(LootLogSubmission).where(
                   LootLogSubmission.guild_id == guild.id,
                   LootLogSubmission.event_id == event_id,
               )) if s.submitter_user_id is not None}
    rows = []
    for uid, w in sorted(weights.items(), key=lambda x: -x[1]):
        amount = (pool * w) // total_w if total_w > 0 else 0
        pct = round(100 * w / total_w) if total_w > 0 else 0
        rows.append(LoggerPayoutRowOut(
            submitter_user_id=uid, submitter_name=name_of.get(uid),
            weight=w, amount=amount, percent=pct,
        ))
    return LootLogPreviewOut(
        event_id=event_id, tab_value=ev.tab_value,
        logger_percent=settings.logger_percent, logger_pool=pool,
        total_weight=total_w, rows=rows,
    )


# ── self-check ──────────────────────────────────────────────────────────────

def _demo() -> None:
    csv = ("timestamp_utc;looted_by__guild;looted_by__name;item_id;item_name;quantity;"
           "looted_from__name\n"
           "2026-07-03T20:00:00Z;GUILD;LoggerA;T4_BAG;Bag;1;Mob\n"
           "2026-07-03T20:01:00Z;GUILD;LoggerA;T8_MOUNT_OX;Ox;1;Boss\n"
           "2026-07-03T20:02:00Z;GUILD;LoggerB;T6_HEAD_PLATE_SET1;Helm;2;Boss\n")
    assert _parse_header_cols(csv) >= REQUIRED_COLS
    rows = parse_loot_rows(csv)
    assert len(rows) == 3
    assert rows[0]["looted_by"] == "LoggerA" and rows[1]["quantity"] == 1
    assert rows[2]["quantity"] == 2
    # parse_loot_rows não precifica (unit_price é added no ingest); silver é só cache.

    # ── corroboração bot-v1 ─────────────────────────────────────────────────

    # ── corroboração bot-v1 ─────────────────────────────────────────────────
    base_dt = datetime(2026, 7, 3, 20, 0, tzinfo=timezone.utc)
    A, B, C = 100, 200, 300  # submitter_ids

    def row(ts: str, item: str, qty: int, by: str, frm: str) -> dict:
        return {"ts": ts, "item_id": item, "quantity": qty,
                "looted_by": by, "looted_from": frm}

    def ev(ts_min, r, sid):
        return (base_dt + timedelta(minutes=ts_min), r, sid)

    # 2 loggers viram as mesmas 3 coletas (corroboradas) + 1 exclusiva de cada.
    shared = [
        row("2026-07-03T20:00:00Z", "T4_BAG", 1, "P1", "Mob"),
        row("2026-07-03T20:01:00Z", "T8_OX", 1, "P2", "Boss"),
        row("2026-07-03T20:02:00Z", "T6_HELM", 2, "P1", "Boss"),
    ]
    only_a = [row("2026-07-03T20:03:00Z", "T4_BAG", 1, "P3", "Mob")]
    only_b = [row("2026-07-03T20:04:00Z", "T4_BAG", 1, "P4", "Mob")]
    events = ([ev(0, shared[0], A), ev(1, shared[1], A), ev(2, shared[2], A), ev(3, only_a[0], A)]
              + [ev(0, shared[0], B), ev(1, shared[1], B), ev(2, shared[2], B), ev(4, only_b[0], B)])
    subs = [{"submitter_id": A, "created_at": base_dt, "file_hash": "hA"},
            {"submitter_id": B, "created_at": base_dt + timedelta(seconds=1), "file_hash": "hB"}]
    w = _compute_weights(events, subs)
    # 3 corroboradas contam p/ cada um (A e B = 3); as exclusivas NÃO contam.
    assert w == {A: 3, B: 3}, w

    # single logger → todas as únicas contam (4).
    w1 = _compute_weights([ev(0, shared[0], A), ev(1, shared[1], A),
                           ev(2, shared[2], A), ev(3, only_a[0], A)],
                          [{"submitter_id": A, "created_at": base_dt, "file_hash": "hA"}])
    assert w1 == {A: 4}, w1

    # 2ª submissão VAZIA (sem evento na janela) não é logger: A continua
    # single → conta todas as dele (não rebaixa pro caminho só-overlap).
    w_empty = _compute_weights([ev(0, shared[0], A), ev(1, shared[1], A)],
                               [{"submitter_id": A, "created_at": base_dt, "file_hash": "hA"},
                                {"submitter_id": B, "created_at": base_dt, "file_hash": "hB"}])
    assert w_empty == {A: 2}, w_empty

    # cópia: C manda as mesmas 3 coletas do A (mesmo file_hash) → C excluído.
    events_copy = (events + [ev(0, shared[0], C), ev(1, shared[1], C), ev(2, shared[2], C)])
    subs_copy = subs + [{"submitter_id": C, "created_at": base_dt + timedelta(seconds=2),
                         "file_hash": "hA"}]
    wc = _compute_weights(events_copy, subs_copy)
    assert C not in wc, wc
    assert wc == {A: 3, B: 3}, wc

    print("lootlog self-check ok")


if __name__ == "__main__":
    _demo()