"""Geração do PNG de perfil de guilda para embeds do Discord.

Replica o header do GuildProfilePage: painel escuro com nome da guilda,
aliança, heatmap de timers (cor no caractere, Cascadia Code), brackets de
stats dos membros (kill/death/ratio/silver/pve/crafting) e bracket de
gathering por recurso (estimativa T8 com ranking). Cache em disco por 1h.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from PIL import Image, ImageDraw
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.battles import Battle, BattleGuild
from app.models.guild_profiles import AllianceProfile, GuildProfile
from app.models.players import AlbionPlayer, PlayerKillEvent
from app.services import profile_preview
from app.domain.albion_timers import timers_for_region, timer_for_battle

_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "guild_preview_cache"
_ALLIANCE_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "alliance_preview_cache"
_CACHE_TTL = timedelta(hours=1)
PREVIEW_RENDER_VERSION = 3


def _cache_path(albion_id: str) -> Path:
    return _CACHE_DIR / f"{albion_id}_r{PREVIEW_RENDER_VERSION}.png"


def invalidate_cache(albion_id: str) -> None:
    for p in [_CACHE_DIR / f"{albion_id}.png", *_CACHE_DIR.glob(f"{albion_id}_r*.png")]:
        try:
            p.unlink()
        except OSError:
            pass


def invalidate_alliance_cache(albion_id: str) -> None:
    for p in [_ALLIANCE_CACHE_DIR / f"{albion_id}.png", *_ALLIANCE_CACHE_DIR.glob(f"{albion_id}_r*.png")]:
        try:
            p.unlink()
        except OSError:
            pass


# Heatmap color (igual ao battle heatmap do frontend)
_HEAT_HOT = (0x66, 0x71, 0x60)
_HEAT_COLD = (0x52, 0x52, 0x5C)


def _heat_color(battles: int, max_b: int, min_b: int) -> tuple[int, int, int]:
    t = 0 if max_b == min_b else (max_b - battles) / (max_b - min_b)
    return tuple(round(_HEAT_HOT[i] + (_HEAT_COLD[i] - _HEAT_HOT[i]) * t) for i in range(3))  # type: ignore


def render_guild_preview(db: Session, albion_id: str) -> Path | None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _cache_path(albion_id)

    bg = db.scalar(
        select(BattleGuild)
        .where(BattleGuild.albion_guild_id == albion_id)
        .order_by(BattleGuild.id.desc())
        .limit(1)
    )
    if bg is None:
        return None

    gp = db.scalar(select(GuildProfile).where(GuildProfile.albion_id == albion_id))
    if cache_path.exists():
        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime, tz=timezone.utc)
        snapshot_at = gp.last_seen_at if gp is not None else None
        if snapshot_at is not None and snapshot_at.tzinfo is None:
            snapshot_at = snapshot_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - mtime < _CACHE_TTL and (
            snapshot_at is None or mtime >= snapshot_at
        ):
            return cache_path

    profile_preview._load_fonts()

    # Stats dos membros
    member_row = db.execute(
        select(
            func.coalesce(func.sum(AlbionPlayer.kill_fame), 0).label("kill_fame"),
            func.coalesce(func.sum(AlbionPlayer.death_fame), 0).label("death_fame"),
            func.coalesce(func.sum(AlbionPlayer.crafting_fame), 0).label("crafting_fame"),
            func.coalesce(func.sum(AlbionPlayer.pve_fame), 0).label("pve_fame"),
            func.coalesce(func.sum(AlbionPlayer.fishing_fame), 0).label("fishing_fame"),
            func.coalesce(func.sum(AlbionPlayer.gather_wood), 0).label("gather_wood"),
            func.coalesce(func.sum(AlbionPlayer.gather_hide), 0).label("gather_hide"),
            func.coalesce(func.sum(AlbionPlayer.gather_ore), 0).label("gather_ore"),
            func.coalesce(func.sum(AlbionPlayer.gather_rock), 0).label("gather_rock"),
            func.coalesce(func.sum(AlbionPlayer.gather_fiber), 0).label("gather_fiber"),
        ).where(AlbionPlayer.guild_id == albion_id)
    ).first()

    ms = {k: (getattr(member_row, k) or 0) if member_row else 0 for k in
          ("kill_fame", "death_fame", "crafting_fame", "pve_fame", "fishing_fame",
           "gather_wood", "gather_hide", "gather_ore", "gather_rock", "gather_fiber")}

    # Usa a mesma fonte do card atual de guilda: o ledger de mortes dos
    # membros. O campo exibido pela página ainda vem de `PlayerKillEvent.fame`.
    silver_dropped = db.scalar(
        select(func.coalesce(func.sum(PlayerKillEvent.fame), 0))
        .where(
            PlayerKillEvent.victim_guild_id == albion_id,
            PlayerKillEvent.fame > 0,
        )
    ) or 0

    # Região da guilda
    region = db.scalar(
        select(Battle.region)
        .join(BattleGuild, BattleGuild.battle_id == Battle.id)
        .where(BattleGuild.albion_guild_id == albion_id)
        .order_by(Battle.id.desc()).limit(1)
    ) or "americas"

    # Timer heatmap
    timer_rows = db.execute(
        select(Battle.start_time)
        .join(BattleGuild, BattleGuild.battle_id == Battle.id)
        .where(
            BattleGuild.albion_guild_id == albion_id,
            Battle.region == region,
            Battle.players_total > 20,
            Battle.is_lethal.is_(True),
        )
    ).all()
    timers = timers_for_region(region)
    timer_counts: dict[str, int] = {name: 0 for name, _, _ in timers}
    for (start_time,) in timer_rows:
        if start_time is not None:
            tn = timer_for_battle(region, start_time)
            if tn and tn in timer_counts:
                timer_counts[tn] += 1
    max_b = max(timer_counts.values(), default=1) or 1
    min_b = min(timer_counts.values(), default=0)

    # Stats para linha 1
    kd_ratio = ms["kill_fame"] / ms["death_fame"] if ms["death_fame"] > 0 else float("inf")
    stats = [
        ("Kill Fame", profile_preview._fmt_num(ms["kill_fame"]), profile_preview._fame_color(ms["kill_fame"])),
        ("Death Fame", profile_preview._fmt_num(ms["death_fame"]), profile_preview._fame_color(ms["death_fame"])),
        ("Ratio", f"{kd_ratio:.2f}" if kd_ratio != float("inf") else "INF", profile_preview.WHITE),
        ("Silver Dropped", profile_preview._fmt_num(silver_dropped), profile_preview._fame_color(silver_dropped)),
        ("PvE Fame", profile_preview._fmt_num(ms["pve_fame"]), profile_preview._fame_color(ms["pve_fame"])),
        ("Crafting", profile_preview._fmt_num(ms["crafting_fame"]), profile_preview._fame_color(ms["crafting_fame"])),
    ]

    # Gathering por recurso (estimativa T8: fama / 200)
    resources = [
        ("Wood", ms["gather_wood"]),
        ("Hide", ms["gather_hide"]),
        ("Ore", ms["gather_ore"]),
        ("Rock", ms["gather_rock"]),
        ("Fiber", ms["gather_fiber"]),
        ("Fish", ms["fishing_fame"]),
    ]

    return _render_preview_image(
        cache_path,
        bg.guild_name or albion_id,
        f"[{bg.alliance_name}]" if bg.alliance_name else "",
        timers,
        timer_counts,
        max_b,
        min_b,
        stats,
        resources,
    )


def _render_preview_image(
    cache_path: Path,
    name: str,
    subtitle: str,
    timers: list[tuple[str, int, int]],
    timer_counts: dict[str, int],
    max_b: int,
    min_b: int,
    stats: list[tuple[str, str, tuple[int, int, int]]],
    resources: list[tuple[str, int]],
) -> Path:
    """Desenha o cabeçalho compartilhado por guildas e alianças."""
    from PIL import ImageFont

    profile_preview._load_fonts()
    inner = int(16 * profile_preview.S)
    name_h = int(26 * profile_preview.S)
    header_h = name_h + (int(22 * profile_preview.S) if subtitle else 0)
    card_h = int(48 * profile_preview.S)
    gap = int(8 * profile_preview.S)
    resource_h = int(48 * profile_preview.S)
    panel_bottom = (
        inner + header_h + int(12 * profile_preview.S) + card_h
        + int(8 * profile_preview.S) + resource_h + inner
    )

    img = Image.new("RGBA", (profile_preview.IMG_W, panel_bottom), profile_preview.BG_COLOR + (255,))
    draw = ImageDraw.Draw(img)
    panel = (0, 0, profile_preview.IMG_W - 1, panel_bottom - 1)
    profile_preview._draw_panel(draw, panel)
    panel_left, panel_top, panel_right, _ = panel

    timer_font_size = int(24 * profile_preview.S)
    cascadia_path = next(
        (p for p in (r"C:\Windows\Fonts\CascadiaCode.ttf", "/home/ziggs/ziggs/backend/data/cascadia_code.ttf") if Path(p).exists()),
        None,
    )
    timer_font = ImageFont.truetype(cascadia_path, timer_font_size) if cascadia_path else profile_preview._FONT_STATS
    timer_gap = int(10 * profile_preview.S)
    timer_width = sum(profile_preview._text_w(draw, timer, timer_font) for timer, _, _ in timers)
    timer_width += timer_gap * (len(timers) - 1)

    name_x = panel_left + inner
    name_y = panel_top + inner
    timer_x = panel_right - inner - timer_width
    name_display = profile_preview._truncate_to_w(
        draw,
        name,
        max(int(120 * profile_preview.S), timer_x - name_x - int(16 * profile_preview.S)),
        profile_preview._FONT_NAME,
    )
    profile_preview._draw_text(draw, name_display, (name_x, name_y), profile_preview.TEXT_COLOR, profile_preview._FONT_NAME)
    if subtitle:
        subtitle_display = profile_preview._truncate_to_w(
            draw, subtitle, panel_right - name_x - inner, profile_preview._FONT_GUILD,
        )
        profile_preview._draw_text(
            draw, subtitle_display, (name_x, name_y + int(30 * profile_preview.S)),
            profile_preview.GUILD_COLOR, profile_preview._FONT_GUILD,
        )

    # Espelha o header do site: timers ficam no canto superior direito.
    tx = timer_x
    for timer, _, _ in timers:
        color = _heat_color(timer_counts.get(timer, 0), max_b, min_b)
        profile_preview._draw_text(draw, timer, (tx, name_y), color, timer_font)
        tx += profile_preview._text_w(draw, timer, timer_font) + timer_gap

    stats_y = panel_top + inner + header_h + int(12 * profile_preview.S)
    content_width = panel_right - panel_left - 2 * inner
    card_w = (content_width - gap * (len(stats) - 1)) // len(stats)
    for index, (label, value, color) in enumerate(stats):
        x = panel_left + inner + index * (card_w + gap)
        draw.rectangle(
            (x, stats_y, x + card_w, stats_y + card_h),
            fill=profile_preview.CARD_COLOR,
            outline=profile_preview.CARD_BORDER,
            width=profile_preview.S,
        )
        profile_preview._draw_metric(draw, (x, stats_y, x + card_w, stats_y + card_h), label, value, color)

    res_y = stats_y + card_h + int(8 * profile_preview.S)
    draw.rectangle(
        (panel_left + inner, res_y, panel_right - inner, res_y + resource_h),
        fill=profile_preview.CARD_COLOR,
        outline=profile_preview.CARD_BORDER,
        width=profile_preview.S,
    )
    res_cols = len(resources)
    res_content_w = (panel_right - inner) - (panel_left + inner) - 2 * int(12 * profile_preview.S)
    res_cell_w = (res_content_w - gap * (res_cols - 1)) // res_cols
    for index, (label, fame) in enumerate(resources):
        x = panel_left + inner + int(12 * profile_preview.S) + index * (res_cell_w + gap)
        profile_preview._draw_metric(
            draw,
            (x, res_y, x + res_cell_w, res_y + resource_h),
            label,
            str(int(fame // 200)),
            profile_preview.WHITE if fame else profile_preview.HINT_COLOR,
        )

    tmp_path = cache_path.parent / f".{cache_path.stem}.{uuid4().hex}.png"
    try:
        img.save(tmp_path, "PNG")
        tmp_path.replace(cache_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return cache_path


def render_alliance_preview(db: Session, albion_id: str) -> Path | None:
    """Gera o mesmo cabeçalho, somando os membros e batalhas da aliança."""
    _ALLIANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _ALLIANCE_CACHE_DIR / f"{albion_id}_r{PREVIEW_RENDER_VERSION}.png"
    bg = db.scalar(
        select(BattleGuild)
        .where(BattleGuild.alliance_id == albion_id)
        .order_by(BattleGuild.id.desc())
        .limit(1)
    )
    if bg is None:
        return None

    profile = db.scalar(select(AllianceProfile).where(AllianceProfile.albion_id == albion_id))
    if cache_path.exists():
        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime, tz=timezone.utc)
        snapshot_at = profile.last_seen_at if profile is not None else None
        if snapshot_at is not None and snapshot_at.tzinfo is None:
            snapshot_at = snapshot_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - mtime < _CACHE_TTL and (
            snapshot_at is None or mtime >= snapshot_at
        ):
            return cache_path

    member_row = db.execute(
        select(
            func.coalesce(func.sum(AlbionPlayer.kill_fame), 0).label("kill_fame"),
            func.coalesce(func.sum(AlbionPlayer.death_fame), 0).label("death_fame"),
            func.coalesce(func.sum(AlbionPlayer.crafting_fame), 0).label("crafting_fame"),
            func.coalesce(func.sum(AlbionPlayer.pve_fame), 0).label("pve_fame"),
            func.coalesce(func.sum(AlbionPlayer.fishing_fame), 0).label("fishing_fame"),
            func.coalesce(func.sum(AlbionPlayer.gather_wood), 0).label("gather_wood"),
            func.coalesce(func.sum(AlbionPlayer.gather_hide), 0).label("gather_hide"),
            func.coalesce(func.sum(AlbionPlayer.gather_ore), 0).label("gather_ore"),
            func.coalesce(func.sum(AlbionPlayer.gather_rock), 0).label("gather_rock"),
            func.coalesce(func.sum(AlbionPlayer.gather_fiber), 0).label("gather_fiber"),
        ).where(AlbionPlayer.alliance_id == albion_id)
    ).first()
    fields = (
        "kill_fame", "death_fame", "crafting_fame", "pve_fame", "fishing_fame",
        "gather_wood", "gather_hide", "gather_ore", "gather_rock", "gather_fiber",
    )
    ms = {field: (getattr(member_row, field) or 0) if member_row else 0 for field in fields}

    alliance_guild_ids = select(BattleGuild.albion_guild_id).where(
        BattleGuild.alliance_id == albion_id
    ).distinct()
    silver_dropped = db.scalar(
        select(func.coalesce(func.sum(PlayerKillEvent.fame), 0)).where(
            PlayerKillEvent.victim_guild_id.in_(alliance_guild_ids),
            PlayerKillEvent.fame > 0,
        )
    ) or 0
    region = db.scalar(
        select(Battle.region)
        .join(BattleGuild, BattleGuild.battle_id == Battle.id)
        .where(BattleGuild.alliance_id == albion_id)
        .order_by(Battle.id.desc())
        .limit(1)
    ) or "americas"
    timer_rows = db.execute(
        select(Battle.start_time)
        .join(BattleGuild, BattleGuild.battle_id == Battle.id)
        .where(
            BattleGuild.alliance_id == albion_id,
            Battle.region == region,
            Battle.players_total > 20,
            Battle.is_lethal.is_(True),
        )
        .group_by(Battle.id, Battle.start_time)
    ).all()
    timers = timers_for_region(region)
    timer_counts: dict[str, int] = {timer: 0 for timer, _, _ in timers}
    for (start_time,) in timer_rows:
        if start_time is not None:
            timer = timer_for_battle(region, start_time)
            if timer in timer_counts:
                timer_counts[timer] += 1
    max_b = max(timer_counts.values(), default=1) or 1
    min_b = min(timer_counts.values(), default=0)
    kd_ratio = ms["kill_fame"] / ms["death_fame"] if ms["death_fame"] > 0 else float("inf")
    stats = [
        ("Kill Fame", profile_preview._fmt_num(ms["kill_fame"]), profile_preview._fame_color(ms["kill_fame"])),
        ("Death Fame", profile_preview._fmt_num(ms["death_fame"]), profile_preview._fame_color(ms["death_fame"])),
        ("Ratio", f"{kd_ratio:.2f}" if kd_ratio != float("inf") else "INF", profile_preview.WHITE),
        ("Silver Dropped", profile_preview._fmt_num(silver_dropped), profile_preview._fame_color(silver_dropped)),
        ("PvE Fame", profile_preview._fmt_num(ms["pve_fame"]), profile_preview._fame_color(ms["pve_fame"])),
        ("Crafting", profile_preview._fmt_num(ms["crafting_fame"]), profile_preview._fame_color(ms["crafting_fame"])),
    ]
    resources = [
        ("Wood", ms["gather_wood"]), ("Hide", ms["gather_hide"]), ("Ore", ms["gather_ore"]),
        ("Rock", ms["gather_rock"]), ("Fiber", ms["gather_fiber"]), ("Fish", ms["fishing_fame"]),
    ]
    guild_count = db.scalar(
        select(func.count(func.distinct(BattleGuild.albion_guild_id))).where(
            BattleGuild.alliance_id == albion_id
        )
    ) or 0
    return _render_preview_image(
        cache_path,
        bg.alliance_name or albion_id,
        f"{guild_count} guilds",
        timers,
        timer_counts,
        max_b,
        min_b,
        stats,
        resources,
    )
