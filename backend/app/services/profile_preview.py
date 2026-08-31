"""Geração do cabeçalho de perfil para embeds do Discord.

O PNG replica a estrutura do PlayerProfilePage: um dash-panel quadrado com
avatar, identidade, armas e os indicadores do cabeçalho. Cache em disco por
uma hora, invalidado assim que um snapshot novo chega da Albion.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from uuid import uuid4

from PIL import Image, ImageChops, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.battles import SUPPORT_ELIGIBLE_FIGHT_POINTS, TANK_ELIGIBLE_FIGHT_POINTS, _wbase
from app.models.catalog import Weapon
from app.models.players import AlbionPlayer, PlayerKillEvent, PlayerWeaponStat

_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "profile_preview_cache"
_ITEM_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "render_cache" / "items"
_AVATAR_DIR = Path(__file__).resolve().parents[3] / "frontend" / "public" / "avatars"
_CACHE_TTL = timedelta(hours=1)
# Precisa acompanhar o cache-buster da og:image para não servir um layout antigo
# quando a URL do Discord já aponta para uma revisão nova.
PREVIEW_RENDER_VERSION = 14


def _cache_path(albion_id: str, region: str) -> Path:
    return _CACHE_DIR / f"{region}_{albion_id}_r{PREVIEW_RENDER_VERSION}.png"


def invalidate_cache(albion_id: str, region: str) -> None:
    """Deleta o PNG em cache — chamado pelo profile_warmer após atualizar
    o perfil, pra que o próximo request ao embed regenere a imagem."""
    # Remove também o formato pré-versionado de versões já publicadas.
    cache_paths = [
        _CACHE_DIR / f"{region}_{albion_id}.png",
        *_CACHE_DIR.glob(f"{region}_{albion_id}_r*.png"),
    ]
    for cache_path in cache_paths:
        try:
            cache_path.unlink()
        except OSError:
            pass

BG_COLOR = (0x0E, 0x0F, 0x13)
TEXT_COLOR = (0xE4, 0xE4, 0xE7)
DIM_COLOR = (0xA1, 0xA1, 0xAA)
HINT_COLOR = (0x71, 0x71, 0x7A)
GUILD_COLOR = (0xD4, 0xD4, 0xD8)
PANEL_TOP = (0x14, 0x15, 0x1A)
PANEL_BOTTOM = (0x10, 0x11, 0x16)
PANEL_BORDER = (0x24, 0x26, 0x2E)
CORNER_COLOR = (0x52, 0x52, 0x5B)
CARD_COLOR = (0x19, 0x19, 0x1C)
CARD_BORDER = (0x27, 0x27, 0x2A)
GOLD = (0xD4, 0xA3, 0x38)
YELLOW = (0xFD, 0xE0, 0x47)
WHITE = (0xF4, 0xF4, 0xF5)

S = 2
IMG_W = int(600 * S)
PADDING = 0
METRIC_TEXT_GAP = int(5 * S)

_FONT_NAME = None
_FONT_STATS = None
_FONT_MONO_LABEL = None
_FONT_GUILD = None


def _load_fonts() -> None:
    global _FONT_NAME, _FONT_STATS, _FONT_MONO_LABEL, _FONT_GUILD
    if _FONT_NAME is not None:
        return

    def load(size: int, variation: str, *, bold: bool = False):
        # Segoe UI Variable e a fonte efetivamente resolvida pelo browser atual
        # para a pilha declarada no frontend.
        for path in (r"C:\Windows\Fonts\SegUIVar.ttf", "/home/ziggs/ziggs/backend/data/segoeuivariable.ttf"):
            if not Path(path).exists():
                continue
            try:
                font = ImageFont.truetype(path, size)
                font.set_variation_by_name(variation)
                return font
            except (AttributeError, OSError, ValueError):
                continue
        paths = (
            (r"C:\Windows\Fonts\segoeuib.ttf", "/home/ziggs/ziggs/backend/data/segoeuib.ttf")
            if bold else
            (r"C:\Windows\Fonts\segoeui.ttf", "/home/ziggs/ziggs/backend/data/segoeui.ttf")
        )
        for path in (*paths, r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            if Path(path).exists():
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue
        return ImageFont.load_default()

    def load_cascadia(size: int):
        for path in (r"C:\Windows\Fonts\CascadiaCode.ttf", "/home/ziggs/ziggs/backend/data/cascadia_code.ttf"):
            if Path(path).exists():
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue
        return ImageFont.load_default()

    _FONT_NAME = load(int(22 * S), "Bold Display", bold=True)
    _FONT_GUILD = load(int(14 * S), "Semibold Text", bold=True)
    _FONT_STATS = load_cascadia(int(12 * S))
    _FONT_MONO_LABEL = load_cascadia(int(10 * S))


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _draw_text(draw, text, pos, color, font):
    draw.text(pos, text, fill=color, font=font)


def _draw_metric(draw, box: tuple[int, int, int, int], label: str, value: str, value_color) -> None:
    """Desenha cada indicador com o mesmo bloco visual de titulo e valor."""
    left, top, right, bottom = box
    label = label.upper()
    label_box = draw.textbbox((0, 0), label, font=_FONT_MONO_LABEL)
    value_box = draw.textbbox((0, 0), value, font=_FONT_STATS)
    label_h = label_box[3] - label_box[1]
    value_h = value_box[3] - value_box[1]
    block_h = label_h + METRIC_TEXT_GAP + value_h
    block_top = top + ((bottom - top) - block_h) // 2
    label_x = left + ((right - left) - (label_box[2] - label_box[0])) // 2
    value_x = left + ((right - left) - (value_box[2] - value_box[0])) // 2
    _draw_text(draw, label, (label_x, block_top - label_box[1]), HINT_COLOR, _FONT_MONO_LABEL)
    _draw_text(draw, value, (value_x, block_top + label_h + METRIC_TEXT_GAP - value_box[1]), value_color, _FONT_STATS)


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


def _fame_color(value: int) -> tuple[int, int, int]:
    if value >= 1_000_000_000:
        return (0xFC, 0xD3, 0x4D)
    if value >= 100_000_000:
        return YELLOW
    if value >= 10_000_000:
        return WHITE
    return DIM_COLOR


def _draw_panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = box
    height = max(1, bottom - top)
    for y in range(top, bottom + 1):
        ratio = (y - top) / height
        color = tuple(round(PANEL_TOP[i] * (1 - ratio) + PANEL_BOTTOM[i] * ratio) for i in range(3))
        draw.line((left, y, right, y), fill=color)
    draw.rectangle(box, outline=PANEL_BORDER, width=S)
    corner = int(8 * S)
    draw.line((left, top, left + corner, top), fill=CORNER_COLOR, width=S)
    draw.line((left, top, left, top + corner), fill=CORNER_COLOR, width=S)
    draw.line((right - corner, bottom, right, bottom), fill=CORNER_COLOR, width=S)
    draw.line((right, bottom - corner, right, bottom), fill=CORNER_COLOR, width=S)


def _default_avatar(name: str) -> str:
    avatars = ("AVATAR_01", "AVATAR_02", "AVATAR_03", "AVATAR_04", "AVATAR_05")
    value = 0
    for char in name:
        value = ((value * 31) + ord(char)) & 0xFFFFFFFF
    if value >= 0x80000000:
        value -= 0x100000000
    return avatars[abs(value) % len(avatars)]


def _avatar_image(player: AlbionPlayer) -> Image.Image | None:
    avatar = player.avatar or _default_avatar(player.name)
    path = _AVATAR_DIR / f"{avatar}.png"
    if not path.is_file():
        path = _AVATAR_DIR / f"{_default_avatar(player.name)}.png"
    try:
        with Image.open(path) as source:
            avatar = source.convert("RGBA")
            alpha_box = avatar.getchannel("A").getbbox()
            return avatar.crop(alpha_box) if alpha_box is not None else avatar
    except OSError:
        return None


def _item_icon(weapon_base: str) -> Image.Image | None:
    key = quote(f"T7_{weapon_base}", safe="")
    # TopWeaponsWidget usa exatamente T7, qualidade 4 e size 128. Reusar a
    # mesma chave aproveita o cache de ícones já aquecido pelo site.
    for size in (128, 64, 0):
        path = _ITEM_CACHE_DIR / f"{key}_q4_s{size}.png"
        try:
            with Image.open(path) as source:
                return source.convert("RGBA")
        except OSError:
            continue
    return None


def _top_weapons(db: Session, player: AlbionPlayer) -> list[tuple[str, int]]:
    """Mesma pontuação do widget de armas do perfil público."""
    kill_rows = db.scalars(
        select(PlayerKillEvent).where(
            PlayerKillEvent.killer_player_id == player.id,
            PlayerKillEvent.region == player.region,
            PlayerKillEvent.fame > 0,
        )
    ).all()
    points: dict[str, int] = {}
    for event in kill_rows:
        weapon_base = _wbase(((event.killer_equipment or {}).get("MainHand") or {}).get("Type"))
        if weapon_base:
            points[weapon_base] = points.get(weapon_base, 0) + 1

    weapon_functions = {
        _wbase(item_id): function
        for item_id, function in db.execute(select(Weapon.item_id, Weapon.invisible_function)).all()
        if function
    }
    for stat in db.scalars(
        select(PlayerWeaponStat).where(PlayerWeaponStat.albion_player_id == player.albion_id)
    ).all():
        role = weapon_functions.get(stat.weapon_base, "dps")
        if role == "pierce":
            points[stat.weapon_base] = points.get(stat.weapon_base, 0) + stat.pierce_points
        elif role == "healer":
            points[stat.weapon_base] = points.get(stat.weapon_base, 0) + stat.healer_points
        elif role == "support":
            points[stat.weapon_base] = points.get(stat.weapon_base, 0) + (
                stat.zero_death_eligible_fights * SUPPORT_ELIGIBLE_FIGHT_POINTS
            )
        elif role == "tank":
            points[stat.weapon_base] = points.get(stat.weapon_base, 0) + (
                stat.tank_ok_fights * TANK_ELIGIBLE_FIGHT_POINTS
            )
    return sorted(((weapon, score) for weapon, score in points.items() if score > 0),
                  key=lambda entry: entry[1], reverse=True)[:5]


def preview_weapon_bases(db: Session, albion_id: str, region: str) -> list[str]:
    player = db.scalar(
        select(AlbionPlayer).where(
            AlbionPlayer.albion_id == albion_id,
            AlbionPlayer.region == region,
        )
    )
    return [weapon_base for weapon_base, _ in _top_weapons(db, player)] if player is not None else []


def cached_preview_weapon_bases(weapon_bases: list[str]) -> set[str]:
    """Armas cuja arte T7 excelente já está pronta no cache de render."""
    return {weapon_base for weapon_base in weapon_bases if _item_icon(weapon_base) is not None}


def render_player_preview(
    db: Session, albion_id: str, region: str, *, available_weapon_bases: set[str] | None = None,
) -> Path | None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _cache_path(albion_id, region)

    player = db.scalar(
        select(AlbionPlayer).where(
            AlbionPlayer.albion_id == albion_id,
            AlbionPlayer.region == region,
        )
    )
    if player is None:
        return None

    if player.lifetime_statistics is None:
        return None

    if cache_path.exists():
        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime, tz=timezone.utc)
        snapshot_at = player.stats_updated_at or player.last_seen_at
        if snapshot_at is not None and snapshot_at.tzinfo is None:
            snapshot_at = snapshot_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - mtime < _CACHE_TTL and (
            snapshot_at is None or mtime >= snapshot_at
        ):
            return cache_path

    _load_fonts()

    kill_fame = player.kill_fame or 0
    death_fame = player.death_fame or 0
    pve_fame = player.pve_fame or 0
    crafting_fame = player.crafting_fame or 0
    kd_ratio = kill_fame / death_fame if death_fame > 0 else float("inf")
    silver_dropped = sum(
        event.silver_dropped or 0
        for event in db.scalars(
            select(PlayerKillEvent).where(
                PlayerKillEvent.victim_player_id == player.id,
                PlayerKillEvent.region == player.region,
            )
        )
    )

    guild_str = ""
    if player.guild_name:
        guild_str = player.guild_name
        if player.alliance_tag or player.alliance_name:
            guild_str = f"[{player.alliance_tag or player.alliance_name}] {guild_str}"
    elif player.alliance_name:
        guild_str = f"[{player.alliance_tag or player.alliance_name}]"

    stats = [
        ("Kill Fame", _fmt_num(kill_fame), _fame_color(kill_fame)),
        ("Death Fame", _fmt_num(death_fame), _fame_color(death_fame)),
        ("Ratio", f"{kd_ratio:.2f}" if kd_ratio != float("inf") else "INF", WHITE),
    ]
    if silver_dropped:
        stats.append(("Silver Dropped", _fmt_num(silver_dropped), _fame_color(silver_dropped)))
    # Mantém Crafting imediatamente à direita de PvE, inclusive em perfis novos.
    stats.append(("PvE Fame", _fmt_num(pve_fame), _fame_color(pve_fame)))
    stats.append(("Crafting", _fmt_num(crafting_fame), _fame_color(crafting_fame)))
    weapons = _top_weapons(db, player)
    if available_weapon_bases is not None:
        weapons = [entry for entry in weapons if entry[0] in available_weapon_bases]

    resource_stats = [
        ("Wood", player.gather_wood or 0),
        ("Hide", player.gather_hide or 0),
        ("Ore", player.gather_ore or 0),
        ("Rock", player.gather_rock or 0),
        ("Fiber", player.gather_fiber or 0),
        ("Fish", player.fishing_fame or 0),
    ]
    inner = int(16 * S)
    avatar_size = int(60 * S)
    card_h = int(48 * S)
    # O preview é uma composição desktop do cabeçalho: as seis métricas cabem
    # na mesma linha, como no PlayerProfilePage em largura normal.
    resource_cols = 6
    resource_rows = (len(resource_stats) + resource_cols - 1) // resource_cols
    resource_gap = int(8 * S)
    resource_row_h = int(34 * S)
    resource_box_h = int(8 * S) + resource_rows * resource_row_h + (resource_rows - 1) * resource_gap + int(8 * S)
    stats_y = PADDING + inner + avatar_size + int(16 * S)
    resource_top = stats_y + card_h + int(12 * S)
    panel_bottom = resource_top + resource_box_h + inner
    img_h = panel_bottom
    img = Image.new("RGBA", (IMG_W, img_h), BG_COLOR + (255,))
    draw = ImageDraw.Draw(img)

    panel = (0, 0, IMG_W - 1, panel_bottom - 1)
    _draw_panel(draw, panel)
    panel_left, panel_top, panel_right, _ = panel

    avatar_x = panel_left + inner
    avatar_y = panel_top + inner
    avatar = _avatar_image(player)
    if avatar is not None:
        avatar = avatar.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
        mask = Image.new("L", (avatar_size, avatar_size))
        ImageDraw.Draw(mask).ellipse((0, 0, avatar_size, avatar_size), fill=255)
        avatar.putalpha(ImageChops.multiply(avatar.getchannel("A"), mask))
        draw.ellipse((avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size), fill=(0x2A, 0x2D, 0x39))
        img.paste(avatar, (avatar_x, avatar_y), avatar)
    else:
        draw.ellipse((avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size), fill=(0x2A, 0x2D, 0x39))
        initial = player.name[:1].upper()
        iw = _text_w(draw, initial, _FONT_NAME)
        _draw_text(draw, initial, (avatar_x + (avatar_size - iw) // 2, avatar_y + int(15 * S)), TEXT_COLOR, _FONT_NAME)

    weapon_icons = [(weapon_base, points, _item_icon(weapon_base)) for weapon_base, points in weapons]
    weapon_icons = [entry for entry in weapon_icons if entry[2] is not None]
    icon_size = int(40 * S)
    icon_gap = int(12 * S)
    weapons_width = len(weapon_icons) * icon_size + max(0, len(weapon_icons) - 1) * icon_gap
    weapons_x = panel_right - inner - weapons_width
    text_x = avatar_x + avatar_size + int(16 * S)
    name_y = avatar_y
    name_display = _truncate_to_w(draw, player.name, max(int(100 * S), weapons_x - text_x), _FONT_NAME)
    _draw_text(draw, name_display, (text_x, name_y), TEXT_COLOR, _FONT_NAME)
    guild_display = _truncate_to_w(draw, guild_str or "No guild", weapons_x - text_x, _FONT_GUILD)
    _draw_text(draw, guild_display, (text_x, avatar_y + int(26 * S)), GUILD_COLOR, _FONT_GUILD)

    for index, (_, points, icon) in enumerate(weapon_icons):
        x = weapons_x + index * (icon_size + icon_gap)
        y = avatar_y + int(8 * S)
        icon.thumbnail((icon_size, icon_size), Image.Resampling.LANCZOS)
        img.paste(icon, (x + (icon_size - icon.width) // 2, y + (icon_size - icon.height) // 2), icon)
        points_text = _fmt_num(points)
        points_width = _text_w(draw, points_text, _FONT_MONO_LABEL)
        _draw_text(draw, points_text, (x + (icon_size - points_width) // 2, y + icon_size + int(3 * S)), GOLD, _FONT_MONO_LABEL)

    gap = int(8 * S)
    content_width = panel_right - panel_left - 2 * inner
    card_w = (content_width - gap * (len(stats) - 1)) // len(stats)
    for index, (label, value, color) in enumerate(stats):
        x = panel_left + inner + index * (card_w + gap)
        draw.rectangle((x, stats_y, x + card_w, stats_y + card_h), fill=CARD_COLOR, outline=CARD_BORDER, width=S)
        _draw_metric(draw, (x, stats_y, x + card_w, stats_y + card_h), label, value, color)

    resource_left = panel_left + inner
    resource_right = panel_right - inner
    draw.rectangle((resource_left, resource_top, resource_right, resource_top + resource_box_h), fill=CARD_COLOR, outline=CARD_BORDER, width=S)
    resource_padding = int(12 * S)
    resource_content_w = resource_right - resource_left - 2 * resource_padding
    resource_cell_w = (resource_content_w - resource_gap * (resource_cols - 1)) // resource_cols
    for index, (label, value) in enumerate(resource_stats):
        col = index % resource_cols
        row = index // resource_cols
        x = resource_left + resource_padding + col * (resource_cell_w + resource_gap)
        y = resource_top + int(8 * S) + row * (resource_row_h + resource_gap)
        value_text = _fmt_num(value)
        _draw_metric(draw, (x, y, x + resource_cell_w, y + resource_row_h), label, value_text, WHITE if value else HINT_COLOR)

    # Discord faz varias requisicoes concorrentes para a mesma og:image.
    # Publicar por rename atomico evita entregar um PNG truncado a um crawler.
    tmp_path = _CACHE_DIR / f".{cache_path.stem}.{uuid4().hex}.png"
    try:
        img.save(tmp_path, "PNG")
        tmp_path.replace(cache_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return cache_path
