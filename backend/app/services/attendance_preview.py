"""Card PNG de attendance para o embed do bot."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw

from app.services.profile_preview import (
    BG_COLOR, CARD_BORDER, CARD_COLOR, DIM_COLOR, GOLD, HINT_COLOR,
    IMG_W, WHITE, _draw_metric, _draw_panel, _draw_text,
    _load_fonts, _truncate_to_w, S,
)

_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "attendance_preview_cache"
_CACHE_TTL = timedelta(minutes=5)
_RENDER_VERSION = 1
_GREEN = (0x4A, 0xD6, 0x8A)


def _cache_path(guild_id: int, user_id: int, lang: str) -> Path:
    return _CACHE_DIR / f"{guild_id}_{user_id}_{lang}_r{_RENDER_VERSION}.png"


def _pct(user_events: int, total_events: int) -> float | None:
    return (100 * user_events / total_events) if total_events else None


def _last_event(value: str | None, lang: str) -> str:
    labels = {
        "pt": ("Nunca", "Hoje", "Há 1 dia", "Há {days} dias"),
        "en": ("Never", "Today", "1 day ago", "{days} days ago"),
        "es": ("Nunca", "Hoy", "Hace 1 día", "Hace {days} días"),
    }
    never, today, one_day, days_ago = labels.get(lang, labels["pt"])
    if not value:
        return never
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "—"
    days = max(0, (datetime.now(timezone.utc) - dt).days)
    if days == 0:
        return today
    if days == 1:
        return one_day
    return days_ago.format(days=days)


def render_attendance_preview(guild_id: int, user_id: int, display_name: str, stats: dict, lang: str) -> Path:
    """Gera ou reutiliza um card de attendance curto, com cache de cinco minutos."""
    path = _cache_path(guild_id, user_id, lang)
    try:
        if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) >= datetime.now(timezone.utc) - _CACHE_TTL:
            return path
    except OSError:
        pass

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _load_fonts()
    from app.services import profile_preview as preview

    width, height = IMG_W, int(190 * S)
    image = Image.new("RGB", (width, height), BG_COLOR)
    draw = ImageDraw.Draw(image)
    panel = (0, 0, width - 1, height - 1)
    _draw_panel(draw, panel)
    inner = int(16 * S)

    title = _truncate_to_w(draw, display_name, width - 2 * inner, preview._FONT_NAME)
    _draw_text(draw, title, (inner, inner), WHITE, preview._FONT_NAME)
    subtitles = {
        "pt": "ATTENDANCE · PRESENÇAS VÁLIDAS EM CTA",
        "en": "ATTENDANCE · VALID CTA PRESENCE",
        "es": "ATTENDANCE · PRESENCIAS VÁLIDAS EN CTA",
    }
    _draw_text(draw, subtitles.get(lang, subtitles["pt"]), (inner, inner + int(28 * S)), HINT_COLOR, preview._FONT_MONO_LABEL)

    total = int(stats.get("total_events") or 0)
    user_total = int(stats.get("user_events") or 0)
    total_7d = int(stats.get("total_events_7d") or 0)
    user_7d = int(stats.get("user_events_7d") or 0)
    pct_total = _pct(user_total, total)
    pct_7d = _pct(user_7d, total_7d)
    trend = (pct_7d - pct_total) if pct_7d is not None and pct_total is not None else None
    trend_color = _GREEN if trend and trend > 0 else (GOLD if trend and trend < 0 else DIM_COLOR)
    trend_value = f"{trend:+.1f} pp" if trend is not None else "—"
    rank = stats.get("rank")

    top = inner + int(60 * S)
    bottom = height - inner
    gap = int(8 * S)
    card_w = (width - inner * 2 - gap * 4) // 5
    labels = {
        "pt": ("HISTÓRICO", "7 DIAS", "TENDÊNCIA", "RANK", "ÚLTIMO CTA"),
        "en": ("LIFETIME", "7 DAYS", "TREND", "RANK", "LAST CTA"),
        "es": ("HISTORIAL", "7 DÍAS", "TENDENCIA", "RANK", "ÚLTIMO CTA"),
    }.get(lang, ("HISTÓRICO", "7 DIAS", "TENDÊNCIA", "RANK", "ÚLTIMO CTA"))
    metrics = [
        (labels[0], f"{pct_total:.1f}%" if pct_total is not None else "—", GOLD),
        (labels[1], f"{pct_7d:.1f}%" if pct_7d is not None else "—", WHITE),
        (labels[2], trend_value, trend_color),
        (labels[3], f"#{rank}" if rank else "—", WHITE),
        (labels[4], _last_event(stats.get("last_event"), lang), DIM_COLOR),
    ]
    for index, (label, value, color) in enumerate(metrics):
        left = inner + index * (card_w + gap)
        right = left + card_w
        draw.rectangle((left, top, right, bottom), fill=CARD_COLOR, outline=CARD_BORDER, width=S)
        _draw_metric(draw, (left, top, right, bottom), label, value, color)

    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    image.save(temp, "PNG")
    temp.replace(path)
    return path
