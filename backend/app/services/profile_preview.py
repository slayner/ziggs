"""Geração de imagem PNG de perfil de jogador para embeds do Discord.

Reproduz o estilo do battle_preview.py: card escuro com nome, guilda/aliança,
e stats principais (KillFame, DeathFame, K/D ratio, PvE, Gathering, Crafting).

Cache em disco (data/profile_preview_cache/) — TTL de 1h (perfis mudam, mas
não a cada minuto).
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone, timedelta

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.players import AlbionPlayer, PlayerKillEvent

_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "profile_preview_cache"
_CACHE_TTL = timedelta(hours=1)

BG_COLOR = (0x0E, 0x0F, 0x13)
TEXT_COLOR = (0xF5, 0xF5, 0xF7)
DIM_COLOR = (0xA8, 0xA8, 0xB2)
SEP_COLOR = (0x3A, 0x3A, 0x42)
GOLD = (0xD4, 0xA3, 0x38)
GREEN = (0x4C, 0xE0, 0x4C)
RED = (0xF0, 0x4C, 0x4C)
BLUE = (0x4C, 0x9C, 0xE0)

S = 2
IMG_W = int(600 * S)
PADDING = int(18 * S)

_FONT_NAME = None
_FONT_STATS = None
_FONT_LABEL = None
_FONT_INFO = None


def _load_fonts() -> None:
    global _FONT_NAME, _FONT_STATS, _FONT_LABEL, _FONT_INFO
    if _FONT_NAME is not None:
        return
    paths = [
        (r"C:\Windows\Fonts\CascadiaCode.ttf", "cascadia"),
        (r"C:\Windows\Fonts\consolab.ttf", "consolasbold"),
        (r"C:\Windows\Fonts\segoeuib.ttf", "segoebold"),
        ("/home/ziggs/ziggs/backend/data/cascadia_code.ttf", "cascadia"),
        ("/home/ziggs/ziggs/backend/data/consolab.ttf", "consolasbold"),
        ("/home/ziggs/ziggs/backend/data/segoeuib.ttf", "segoebold"),
    ]
    for path, kind in paths:
        if not Path(path).exists():
            continue
        try:
            if kind == "cascadia":
                _FONT_NAME = ImageFont.truetype(path, int(24 * S))
                _FONT_STATS = ImageFont.truetype(path, int(18 * S))
            elif kind == "consolasbold":
                _FONT_STATS = ImageFont.truetype(path, int(18 * S))
                _FONT_INFO = ImageFont.truetype(path, int(13 * S))
            elif kind == "segoebold":
                _FONT_LABEL = ImageFont.truetype(path, int(12 * S))
                _FONT_INFO = ImageFont.truetype(path, int(13 * S))
        except Exception:
            pass
    if _FONT_NAME is None:
        _FONT_NAME = ImageFont.load_default()
    if _FONT_STATS is None:
        _FONT_STATS = _FONT_NAME
    if _FONT_LABEL is None:
        _FONT_LABEL = _FONT_NAME
    if _FONT_INFO is None:
        _FONT_INFO = _FONT_NAME


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _draw_text(draw, text, pos, color, font):
    draw.text(pos, text, fill=color, font=font)


def _draw_right(draw, text, x, y, color, font):
    w = _text_w(draw, text, font)
    draw.text((x - w, y), text, fill=color, font=font)


def _truncate_to_w(draw, text, max_w, font):
    if _text_w(draw, text, font) <= max_w:
        return text
    while len(text) > 1 and _text_w(draw, text + "\u2026", font) > max_w:
        text = text[:-1]
    return text + "\u2026" if text else text


def _fmt_num(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def render_player_preview(db: Session, albion_id: str, region: str) -> Path | None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = f"{region}_{albion_id}.png"
    cache_path = _CACHE_DIR / cache_key
    if cache_path.exists():
        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime, tz=timezone.utc)
        if datetime.now(timezone.utc) - mtime < _CACHE_TTL:
            return cache_path

    _load_fonts()

    player = db.scalar(
        select(AlbionPlayer).where(
            AlbionPlayer.albion_id == albion_id,
            AlbionPlayer.region == region,
        )
    )
    if player is None:
        return None

    kill_fame = player.kill_fame or 0
    death_fame = player.death_fame or 0
    pve_fame = player.pve_fame or 0
    crafting_fame = player.crafting_fame or 0
    gathering_fame = player.gathering_fame or 0
    fishing_fame = player.fishing_fame or 0

    kd_ratio = kill_fame / death_fame if death_fame > 0 else float("inf")

    kill_count = db.scalar(
        select(func.count()).select_from(PlayerKillEvent).where(
            PlayerKillEvent.killer_player_id == player.id,
            PlayerKillEvent.fame > 0,
        )
    ) or 0
    death_count = db.scalar(
        select(func.count()).select_from(PlayerKillEvent).where(
            PlayerKillEvent.victim_player_id == player.id,
            PlayerKillEvent.fame > 0,
        )
    ) or 0

    region_map = {"americas": "AM", "europe": "EU", "asia": "AS"}
    region_tag = region_map.get(region, region.upper())

    guild_str = ""
    if player.guild_name:
        guild_str = player.guild_name
        if player.alliance_name:
            guild_str += f" [{player.alliance_name}]"
    elif player.alliance_name:
        guild_str = f"[{player.alliance_name}]"

    stats = [
        ("KILL FAME", _fmt_num(kill_fame), GOLD),
        ("DEATH FAME", _fmt_num(death_fame), RED),
        ("K/D RATIO", f"{kd_ratio:.2f}" if kd_ratio != float("inf") else "\u221e", GOLD),
        ("KILLS", str(kill_count), GREEN),
        ("DEATHS", str(death_count), RED),
        ("PvE FAME", _fmt_num(pve_fame), BLUE),
        ("GATHERING", _fmt_num(gathering_fame), BLUE),
        ("CRAFTING", _fmt_num(crafting_fame), BLUE),
        ("FISHING", _fmt_num(fishing_fame), BLUE),
    ]

    name_h = int(32 * S)
    guild_h = int(22 * S) if guild_str else 0
    info_h = int(18 * S)
    sep_h = int(10 * S)
    label_h = int(16 * S)
    row_h = int(22 * S)
    n = len(stats)
    img_h = PADDING + name_h + guild_h + info_h + sep_h + label_h + n * row_h + PADDING
    img = Image.new("RGB", (IMG_W, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = PADDING

    name_display = _truncate_to_w(draw, player.name, IMG_W - 2 * PADDING, _FONT_NAME)
    _draw_text(draw, name_display, (PADDING, y), TEXT_COLOR, _FONT_NAME)
    rw = _text_w(draw, region_tag, _FONT_INFO)
    draw.text((IMG_W - PADDING - rw, y + int(4 * S)), region_tag, fill=DIM_COLOR, font=_FONT_INFO)
    y += name_h

    if guild_str:
        guild_display = _truncate_to_w(draw, guild_str, IMG_W - 2 * PADDING, _FONT_STATS)
        _draw_text(draw, guild_display, (PADDING, y), DIM_COLOR, _FONT_STATS)
        y += guild_h

    if player.last_seen_at:
        ls = player.last_seen_at
        if ls.tzinfo is None:
            ls = ls.replace(tzinfo=timezone.utc)
        ago = datetime.now(timezone.utc) - ls
        if ago.days > 0:
            updated = f"atualizado há {ago.days}d"
        elif ago.seconds > 3600:
            updated = f"atualizado há {ago.seconds // 3600}h"
        else:
            updated = f"atualizado há {ago.seconds // 60}min"
        _draw_text(draw, updated, (PADDING, y), DIM_COLOR, _FONT_INFO)
        y += info_h

    sep_y = y + int(2 * S)
    draw.line([(PADDING, sep_y), (IMG_W - PADDING, sep_y)], fill=SEP_COLOR, width=max(1, int(S)))
    y = sep_y + sep_h

    value_x = IMG_W - PADDING
    for label, value, color in stats:
        _draw_text(draw, label, (PADDING, y), DIM_COLOR, _FONT_LABEL)
        _draw_right(draw, value, value_x, y, color, _FONT_STATS)
        y += row_h

    img.save(cache_path, "PNG")
    return cache_path