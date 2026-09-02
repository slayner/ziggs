"""Reconhecimento de screenshots de morte do Albion para regear.

Estratégia em duas tentativas (do mais barato/robusto pro mais caro/frágil):

  1. **Por jogador + CTA (primário, sem OCR).** Já sabemos QUEM postou a print
     (requester_user_id → BotRegistration → nick Albion). Buscamos as mortes
     recentes desse personagem na API de killboard e casamos por horário:
     preferência pras que caem na janela de um CTA agendado, senão a mais
     recente. A API devolve os itens EXATOS (tier/enchant/quality) + timestamp
     real. Não precisa de OCR, não sofre com qualidade da imagem, e a
     "esquerda/direita" da screenshot é irrelevante (a API diz quem é a vítima).

  2. **OCR (fallback, último recurso).** Só quando a API está fora do ar, o
     jogador não tem registro, ou nenhuma morte casa. Tesseract lê nomes da
     screenshot e repete a busca na API. Se pytesseract/Tesseract não existir,
     degrada pra "manual": logística preenche os itens na UI.

Nunca trava o fluxo — qualquer falha vira "manual".
"""
from __future__ import annotations

import asyncio
import io
import re
from datetime import datetime, timedelta, timezone

from app.services import albion_events

# pytesseract + Pillow são opcionais — só usados no fallback de OCR.
try:
    from PIL import Image  # type: ignore
    import pytesseract  # type: ignore
    _OCR_OK = True
except Exception:
    _OCR_OK = False

# Janela de "morte recente" pra considerar (a print acabou de ser postada).
_RECENT_WINDOW = timedelta(hours=12)
# Quão perto de um CTA agendado conta como "morte no horizonte do CTA".
_CTA_WINDOW = timedelta(minutes=90)
# OCR: aceita candidatos de nome 3-24 chars, letras/números/espaço, sem símbolos.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{2,23}$")


def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Caminho primário: por jogador + CTA ──────────────────────────────────────

def _pick_best_death(
    deaths: list[dict], cta_times: list[datetime], now: datetime,
    landmark: tuple[datetime, datetime] | None = None,
) -> dict | None:
    """Escolhe a morte que melhor casa com o regear.

    Regras (preferência decrescente):
      (a) morte dentro do landmark (janela do evento vinculado: started_at-buf →
          ended_at+buf) OU da janela de um CTA agendado — mais provável de ser a
          do regear ("morte no horizonte do evento/CTA");
      (b) entre as casadas, a mais recente;
      (c) sem nenhuma casada, a morte mais recente dentro da janela recente.
    Ignora mortes futuras (tz estranho) e mais velhas que a janela recente.
    """
    cutoff = now - _RECENT_WINDOW
    horizon = now + timedelta(minutes=5)
    lo, hi = landmark if landmark else (None, None)
    scored: list[tuple[bool, datetime, dict]] = []
    for ev in deaths:
        t = _parse_time(ev.get("Time"))
        if t is None or t < cutoff or t > horizon:
            continue
        in_landmark = False
        if lo is not None and hi is not None:
            in_landmark = lo <= t <= hi
        if not in_landmark and cta_times:
            in_landmark = any(abs((t - c).total_seconds()) <= _CTA_WINDOW.total_seconds() for c in cta_times)
        scored.append((in_landmark, t, ev))
    if not scored:
        return None
    # in_landmark=True primeiro; dentro de cada grupo, mais recente primeiro.
    scored.sort(key=lambda x: (not x[0], -x[1].timestamp()))
    return scored[0][2]


def _result_from_event(ev: dict, method: str = "death_api") -> dict:
    victim = ev.get("Victim") or {}
    items = albion_events.equipment_items(ev)
    return {
        "status": "recognized",
        "ocr_name": victim.get("Name") or None,
        "albion_event_id": str(ev.get("EventId") or "") or None,
        "death_timestamp": _parse_time(ev.get("Time")),
        "items": items,
        "candidates": [],
        "method": method,
        "confidence": "high" if method == "death_api" else "medium",
        "window_match": None,
        "fallback_reason": None,
    }


async def recognize_by_player(
    names: list[str], cta_times: list[datetime], region: str | None = None,
    landmark: tuple[datetime, datetime] | None = None,
) -> dict | None:
    """Caminho primário: busca mortes recentes dos personagens do requester e
    casa por horário (landmark do evento > CTA > recência). Retorna dict
    reconhecido ou None. `landmark` = (lo, hi) da janela do evento vinculado."""
    names = [n for n in names if n]
    if not names:
        return None

    async def _deaths_for(name: str) -> list[dict]:
        pid = await albion_events.search_player(name, region)
        if not pid:
            return []
        return await albion_events.recent_deaths(pid, region)

    results = await asyncio.gather(*[_deaths_for(n) for n in names], return_exceptions=True)
    deaths: list[dict] = []
    for r in results:
        if isinstance(r, list):
            deaths.extend(r)
    if not deaths:
        return None

    best = _pick_best_death(deaths, cta_times, datetime.now(timezone.utc), landmark)
    if best is None:
        return None
    return _result_from_event(best)


# ── Caminho fallback: OCR ─────────────────────────────────────────────────────

def _extract_name_candidates(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        for tok in re.split(r"[|•·\n\r]+", line):
            tok = tok.strip()
            if not tok:
                continue
            m = re.findall(r"[A-Za-z0-9][A-Za-z0-9 _-]{2,23}", tok)
            for cand in m:
                c = cand.strip()
                if _NAME_RE.match(c) and c.lower() not in seen:
                    seen.add(c.lower())
                    out.append(c)
    return out[:8]


def _ocr_names(image_bytes: bytes) -> list[str]:
    if not _OCR_OK:
        return []
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if max(w, h) < 1280:
            img = img.resize((w * 2, h * 2))
        gray = img.convert("L")
        text = pytesseract.image_to_string(gray, config="--psm 6")
        sparse = pytesseract.image_to_string(gray, config="--psm 11")
        return _extract_name_candidates(text + "\n" + sparse)
    except Exception:
        return []


async def recognize(image_bytes: bytes, region: str | None = None) -> dict:
    """Fallback por OCR. Retorna {status, ocr_name, albion_event_id,
    death_timestamp, items, candidates}. status: "recognized" | "manual"."""
    candidates = _ocr_names(image_bytes)
    if not candidates:
        return {"status": "manual", "ocr_name": None, "albion_event_id": None,
                "death_timestamp": None, "items": [], "candidates": candidates,
                "method": "ocr", "confidence": "low",
                "fallback_reason": "no OCR candidate"}

    async def _deaths_for(name: str) -> list[dict]:
        pid = await albion_events.search_player(name, region)
        if not pid:
            return []
        return await albion_events.recent_deaths(pid, region)

    results = await asyncio.gather(*[_deaths_for(c) for c in candidates], return_exceptions=True)
    deaths: list[dict] = []
    for r in results:
        if isinstance(r, list):
            deaths.extend(r)

    cand_set = {c.lower() for c in candidates}
    now = datetime.now(timezone.utc)
    best: dict | None = None
    best_score = (-1, datetime.min)
    for ev in deaths:
        victim = ev.get("Victim") or {}
        killer = ev.get("Killer") or {}
        vname = str(victim.get("Name", "")).lower()
        kname = str(killer.get("Name", "")).lower()
        if vname not in cand_set:
            continue
        t = _parse_time(ev.get("Time"))
        if t is None or t < now - _RECENT_WINDOW:
            continue
        consistency = 1 if kname in cand_set else 0
        score = (consistency, t)
        if score > best_score:
            best_score = score
            best = ev

    if best is None:
        return {"status": "manual", "ocr_name": candidates[0], "albion_event_id": None,
                "death_timestamp": None, "items": [], "candidates": candidates,
                "method": "ocr", "confidence": "low",
                "fallback_reason": "no unique death match"}
    return _result_from_event(best, "ocr") | {
        "candidates": candidates, "confidence": "medium",
    }


# ── Self-checks ──────────────────────────────────────────────────────────────

def _demo_candidates() -> None:
    text = "You were killed by | XxProSlayer\nSomeNoob321\n---\nT8.2 ..."
    cands = _extract_name_candidates(text)
    assert "XxProSlayer" in cands, cands
    assert "SomeNoob321" in cands, cands
    assert "T8" not in cands, cands
    print("recognition name parser OK:", cands)


def _demo_pick() -> None:
    now = datetime(2026, 7, 3, 20, 0, tzinfo=timezone.utc)
    cta = datetime(2026, 7, 3, 19, 30, tzinfo=timezone.utc)  # CTA 19:30
    deaths = [
        {"EventId": 1, "Time": "2026-07-03T10:00:00Z", "Victim": {"Name": "X"}},   # velha (fora da janela)
        {"EventId": 2, "Time": "2026-07-03T19:35:00Z", "Victim": {"Name": "X"}},   # dentro do CTA
        {"EventId": 3, "Time": "2026-07-03T17:30:00Z", "Victim": {"Name": "X"}},   # recente mas fora do CTA
    ]
    best = _pick_best_death(deaths, [cta], now)
    assert best and best["EventId"] == 2, best  # prefere a do CTA
    # sem CTA → mais recente dentro da janela
    best2 = _pick_best_death(deaths, [], now)
    assert best2 and best2["EventId"] == 2, best2
    # sem nenhuma na janela recente → None
    old = [{"EventId": 9, "Time": "2026-06-01T00:00:00Z", "Victim": {"Name": "X"}}]
    assert _pick_best_death(old, [cta], now) is None
    # landmark do evento: morte fora do CTA mas dentro da janela do evento vence
    # a do CTA (landmark é mais específico). Evento 18:00-20:00 (+buffer).
    landmark = (datetime(2026, 7, 3, 17, 30, tzinfo=timezone.utc),
                datetime(2026, 7, 3, 20, 30, tzinfo=timezone.utc))
    ev_deaths = [
        {"EventId": 2, "Time": "2026-07-03T19:35:00Z", "Victim": {"Name": "X"}},  # no CTA 19:30
        {"EventId": 5, "Time": "2026-07-03T18:10:00Z", "Victim": {"Name": "X"}},  # no landmark, fora CTA
    ]
    best3 = _pick_best_death(ev_deaths, [cta], now, landmark)
    # ambas casam (landmark=True); empate → mais recente primeiro → EventId 2
    assert best3 and best3["EventId"] == 2, best3
    # só a 5 está no landmark (a 2 removida): even sem CTA, landmark a pega
    best4 = _pick_best_death([ev_deaths[1]], [], now, landmark)
    assert best4 and best4["EventId"] == 5, best4
    print("recognition death-picker OK (landmark)")


if __name__ == "__main__":
    _demo_candidates()
    _demo_pick()
    print("OCR available:", _OCR_OK)
