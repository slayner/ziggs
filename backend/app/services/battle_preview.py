"""Geração de imagem PNG de resumo de batalha para embeds do Discord.

Reproduz o estilo da bracket do BattleTracker.tsx: factions lado-a-lado com
cores de heat (quem matou mais = mais brilhante) e player count.

Cache em disco (data/battle_preview_cache/) — mesma batalha sempre gera a
mesma imagem, nunca regenera. ponytail: sem TTL, batalhas são imutáveis.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.battles import Battle, BattleGroup, BattleGroupMember, BattleGuild, BattleParticipant, BattleSide
from app.services import battle_groups

_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "battle_preview_cache"

# Cores de heat — dourado do site (--gold: #d4a338) pra mais kills
HEAT_MAX = (0xD4, 0xA3, 0x38)   # dourado — mais kills
HEAT_MIN = (0x70, 0x7A, 0x80)   # cinza-azulado claro — menos kills
BG_COLOR = (0x0E, 0x0F, 0x13)   # fundo escuro do site
TEXT_COLOR = (0xF5, 0xF5, 0xF7) # texto claro (mais branco)
# Cor "discreta" = cinza claro (não mais heat mínimo — era apagado demais)
DIM_COLOR = (0xA8, 0xA8, 0xB2) # cinza claro, legível no escuro sem expandir
SEP_COLOR = (0x3A, 0x3A, 0x42) # separador mais visível

# Escala: multiplica todas as dimensões e fontes. 3 = 3x maior (1800px largura).
S = 2

# Dimensões — largura de banner/tira, altura compacta (cresce só com o conteúdo)
IMG_W = int(600 * S)
PADDING = int(18 * S)
MAX_FACTIONS = 4

# Fontes (carregadas em _load_fonts)
_FONT_HEADER = None      # título "vs" — Cascadia Code (com stroke pra simular bold)
_FONT_LIST = None         # nomes das guildas — Cascadia Code regular
_FONT_NUMBERS = None      # números (kills/deaths/players) — Consolas Bold
_FONT_PERIOD = None      # info (ID/data/região) — Consolas Bold
_FONT_COL_HEADER = None  # headers das colunas — Segoe UI Bold (sans-serif diferente)


def _load_fonts() -> None:
    global _FONT_HEADER, _FONT_LIST, _FONT_NUMBERS, _FONT_PERIOD, _FONT_COL_HEADER
    if _FONT_HEADER is not None:
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
                _FONT_LIST = ImageFont.truetype(path, int(16 * S))
                _FONT_HEADER = ImageFont.truetype(path, int(28 * S))
            elif kind == "consolasbold":
                _FONT_NUMBERS = ImageFont.truetype(path, int(16 * S))
                _FONT_PERIOD = ImageFont.truetype(path, int(13 * S))
            elif kind == "segoebold":
                _FONT_COL_HEADER = ImageFont.truetype(path, int(13 * S))
        except Exception:
            pass
    if _FONT_HEADER is None:
        _FONT_HEADER = ImageFont.load_default()
    if _FONT_LIST is None:
        _FONT_LIST = ImageFont.load_default()
    if _FONT_NUMBERS is None:
        _FONT_NUMBERS = _FONT_LIST
    if _FONT_PERIOD is None:
        _FONT_PERIOD = _FONT_LIST
    if _FONT_COL_HEADER is None:
        _FONT_COL_HEADER = _FONT_LIST


def _heat_color(kills: int, max_kills: int, min_kills: int) -> tuple[int, int, int]:
    if max_kills == min_kills:
        return HEAT_MAX
    t = (max_kills - kills) / (max_kills - min_kills)
    return tuple(round(HEAT_MAX[i] + (HEAT_MIN[i] - HEAT_MAX[i]) * t) for i in range(3))


def _factions_summary(db: Session, battle_id: int) -> list[dict]:
    """Mesma lógica de battles.py _factions_summary — sem import circular."""
    real_side_ids = db.scalars(
        select(BattleSide.id).where(BattleSide.battle_id == battle_id, BattleSide.is_rats == False)
    ).all()
    if not real_side_ids:
        return []
    guilds = db.scalars(
        select(BattleGuild).where(
            BattleGuild.battle_id == battle_id, BattleGuild.side_id.in_(real_side_ids)
        )
    ).all()
    if not guilds:
        return []
    player_counts = dict(
        db.execute(
            select(BattleParticipant.guild_id, func.count(BattleParticipant.id))
            .where(BattleParticipant.battle_id == battle_id, BattleParticipant.guild_id.isnot(None))
            .group_by(BattleParticipant.guild_id)
        ).all()
    )
    agg: dict[str, dict] = {}
    for g in guilds:
        key = g.alliance_id or f"g:{g.albion_guild_id}"
        row = agg.get(key)
        pc = player_counts.get(g.albion_guild_id, 0)
        if row is None:
            agg[key] = {
                "guild_name": g.guild_name,
                "alliance_name": g.alliance_name,
                "kills": g.kills,
                "deaths": g.deaths,
                "player_count": pc,
            }
        else:
            row["kills"] += g.kills
            row["deaths"] += g.deaths
            row["player_count"] += pc
    rows = sorted(agg.values(), key=lambda r: r["kills"], reverse=True)
    return rows


def _text_w(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _draw_text(draw: ImageDraw.ImageDraw, text: str, pos: tuple[int, int],
               color: tuple, font: ImageFont.FreeTypeFont) -> None:
    draw.text(pos, text, fill=color, font=font)


def _draw_centered(draw: ImageDraw.ImageDraw, text: str, center_x: int, y: int,
                   color: tuple, font: ImageFont.FreeTypeFont) -> None:
    w = _text_w(draw, text, font)
    draw.text((center_x - w // 2, y), text, fill=color, font=font)


def _truncate(text: str, max_len: int) -> str:
    return text[:max_len - 1] + "…" if len(text) > max_len else text


def _truncate_to_w(draw, text: str, max_w: int, font) -> str:
    if _text_w(draw, text, font) <= max_w:
        return text
    while len(text) > 1 and _text_w(draw, text + "…", font) > max_w:
        text = text[:-1]
    return text + "…" if text else text


def render_battle_preview(db: Session, public_id: str) -> Path | None:
    """Gera (ou serve do cache) a imagem PNG de resumo da batalha."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _CACHE_DIR / f"{public_id}.png"
    if cache_path.exists():
        return cache_path

    _load_fonts()

    # battle_groups.get_group_battle_ids é async — aqui rodamos sync (a rota
    # _render_preview já abre SyncSession em to_thread).
    group = db.scalar(select(BattleGroup).where(BattleGroup.public_id == public_id))
    if group is None:
        return None
    battle_ids = [m.battle_id for m in db.scalars(
        select(BattleGroupMember)
        .where(BattleGroupMember.group_id == group.id)
        .order_by(BattleGroupMember.position)
    ).all()]
    if not battle_ids:
        return None

    # Soma dados de todas as batalhas do grupo (KB combinada)
    all_factions: dict[str, dict] = {}
    total_kills = 0
    total_players = 0
    first_start: datetime | None = None
    last_end: datetime | None = None
    cluster = None
    is_multi = len(battle_ids) > 1

    for bid in battle_ids:
        b = db.get(Battle, bid)
        if b is None:
            continue
        total_kills += b.kill_count or 0
        total_players += b.players_total or 0
        if first_start is None or (b.start_time and b.start_time < first_start):
            first_start = b.start_time
        if last_end is None or (b.end_time and b.end_time > last_end):
            last_end = b.end_time
        if not cluster:
            cluster = b.cluster
        for f in _factions_summary(db, bid):
            key = f["alliance_name"] or f["guild_name"]
            existing = all_factions.get(key)
            if existing:
                existing["kills"] += f["kills"]
                existing["deaths"] = existing.get("deaths", 0) + f.get("deaths", 0)
                existing["player_count"] += f["player_count"]
            else:
                all_factions[key] = dict(f)

    if not all_factions:
        return None

    # Filtra factions de baixo impacto (mesma lógica do BattlePage.tsx)
    all_rows = sorted(all_factions.values(), key=lambda r: r["kills"], reverse=True)
    total_engagement = sum(f["kills"] + f.get("deaths", 0) for f in all_rows)
    if total_engagement > 0:
        factions = [f for f in all_rows if (f["kills"] + f.get("deaths", 0)) / total_engagement >= 0.03]
    else:
        factions = all_rows
    factions = factions[:MAX_FACTIONS]

    kills_list = [f["kills"] for f in factions]
    max_kills = max(kills_list) if kills_list else 1
    min_kills = min(kills_list) if kills_list else 0

    # ── Layout: banner/tira — título "vs" + info + lista de guildas ──
    # Altura começa pequena e cresce com o número de guildas.
    header_h = int(44 * S)
    info_h = int(20 * S)
    sep_h = int(12 * S)
    col_h = int(20 * S)
    col_gap = int(12 * S)  # gap extra entre header das colunas e a lista
    row_h = int(24 * S)
    n = len(factions)
    img_h = PADDING + info_h + header_h + sep_h + col_h + col_gap + n * row_h + PADDING
    img = Image.new("RGB", (IMG_W, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)
    cx = IMG_W // 2

    # ── Linha de info no TOPO: servidor (dir) · ID (esq) · data (centro) ──
    region_map = {"americas": "AM", "europe": "EU", "asia": "AS"}
    battle = db.get(Battle, battle_ids[0])
    info_y = PADDING

    # ID à esquerda
    if is_multi:
        id_label = "MULTI"
    elif battle:
        id_label = f"#{battle.albion_id}"
    else:
        id_label = ""
    if id_label:
        draw.text((PADDING, info_y), id_label, fill=DIM_COLOR, font=_FONT_PERIOD)

    # Servidor à direita
    if battle:
        region_tag = region_map.get(battle.region, battle.region.upper())
        rw = _text_w(draw, region_tag, _FONT_PERIOD)
        draw.text((IMG_W - PADDING - rw, info_y), region_tag, fill=DIM_COLOR, font=_FONT_PERIOD)

    # Data no centro
    if first_start:
        fs = first_start if first_start.tzinfo else first_start.replace(tzinfo=timezone.utc)
        if is_multi and last_end:
            le = last_end if last_end.tzinfo else last_end.replace(tzinfo=timezone.utc)
            if fs.strftime("%d/%m/%Y") == le.strftime("%d/%m/%Y"):
                footer = f"{fs.strftime('%d/%m %H:%M')} → {le.strftime('%H:%M')} UTC"
            else:
                footer = f"{fs.strftime('%d/%m %H:%M')} → {le.strftime('%d/%m %H:%M')} UTC"
        else:
            footer = fs.strftime("%d/%m/%Y %H:%M UTC")
        _draw_centered(draw, footer, cx, info_y, DIM_COLOR, _FONT_PERIOD)
    y = info_y + info_h

    # ── Cabeçalho: factions vs factions com heatmap ──
    # Cada tag pega a cor de heat da sua guilda — quem matou mais brilha mais.
    # Sem truncagem por char: mede a largura real e encurta só se não couber.
    def _faction_tag(f):
        if f["alliance_name"]:
            return f"[{f['alliance_name']}]"
        return f["guild_name"]

    # Calcula espaço total disponível e distribui entre as tags
    raw_tags = [(_faction_tag(f), _heat_color(f["kills"], max_kills, min_kills)) for f in factions[:4]]
    # stroke_width simula bold no Cascadia Code (fonte variável sem peso bold
    # selecionável pelo PIL). Espessura proporcional à escala.
    stroke = max(1, int(S))
    vs_text = "  vs  "
    vs_w = _text_w(draw, vs_text, _FONT_HEADER)
    avail_w = IMG_W - 2 * PADDING - vs_w * (len(raw_tags) - 1)
    # Mede cada tag; se a soma passar do disponível, encurta as maiores primeiro
    tag_widths = [_text_w(draw, t, _FONT_HEADER) for t, _ in raw_tags]
    total_tags_w = sum(tag_widths)
    if total_tags_w > avail_w:
        # Trunca proporcionalmente — cada tag tem no máximo sua fração do espaço
        per_tag = avail_w // len(raw_tags)
        truncated = []
        for (tag, color), w in zip(raw_tags, tag_widths):
            if w <= per_tag:
                truncated.append((tag, color))
            else:
                truncated.append((_truncate_to_w(draw, tag, per_tag, _FONT_HEADER), color))
        raw_tags = truncated
    total_w = sum(_text_w(draw, t, _FONT_HEADER) for t, _ in raw_tags) + vs_w * (len(raw_tags) - 1)
    x = cx - total_w // 2
    for i, (tag, color) in enumerate(raw_tags):
        if i > 0:
            draw.text((x, y), vs_text, fill=DIM_COLOR, font=_FONT_HEADER, stroke_width=stroke, stroke_fill=DIM_COLOR)
            x += vs_w
        draw.text((x, y), tag, fill=color, font=_FONT_HEADER, stroke_width=stroke, stroke_fill=color)
        x += _text_w(draw, tag, _FONT_HEADER)
    y += header_h

    # ── Separador ──
    sep_y = y + int(2 * S)
    draw.line([(PADDING, sep_y), (IMG_W - PADDING, sep_y)], fill=SEP_COLOR, width=max(1, int(1 * S)))
    y = sep_y + sep_h

    # ── Colunas: PLAYERS | KILLS | DEATHS (right-aligned) ──
    col_deaths_x = IMG_W - PADDING
    col_kills_x = col_deaths_x - int(70 * S)
    col_players_x = col_kills_x - int(70 * S)
    name_max_w = col_players_x - PADDING - int(10 * S)

    _draw_text(draw, "GUILD / ALLIANCE", (PADDING, y), DIM_COLOR, _FONT_COL_HEADER)
    for label, x in [("PLAYERS", col_players_x), ("KILLS", col_kills_x), ("DEATHS", col_deaths_x)]:
        lw = _text_w(draw, label, _FONT_COL_HEADER)
        draw.text((x - lw, y), label, fill=DIM_COLOR, font=_FONT_COL_HEADER)
    y += col_h + col_gap

    KILLS_C = (0x4C, 0xE0, 0x4C)
    DEATHS_C = (0xF0, 0x4C, 0x4C)

    def _draw_right(text: str, x: int, y: int, color: tuple, font) -> None:
        w = _text_w(draw, text, font)
        draw.text((x - w, y), text, fill=color, font=font)

    # ── Linhas das factions com heatmap no nome ──
    for f in factions:
        raw_name = f"[{f['alliance_name']}]" if f["alliance_name"] else f["guild_name"]
        name = _truncate_to_w(draw, raw_name, name_max_w, _FONT_LIST)
        kills = f["kills"]
        deaths = f.get("deaths", 0)
        players = f["player_count"]

        name_color = _heat_color(kills, max_kills, min_kills)
        _draw_text(draw, name, (PADDING, y), name_color, _FONT_LIST)
        _draw_right(str(players), col_players_x, y, DIM_COLOR, _FONT_NUMBERS)

        k_color = KILLS_C if kills > deaths else DIM_COLOR
        _draw_right(str(kills), col_kills_x, y, k_color, _FONT_NUMBERS)

        d_color = DEATHS_C if deaths > kills else DIM_COLOR
        _draw_right(str(deaths), col_deaths_x, y, d_color, _FONT_NUMBERS)

        y += row_h

    # Render em 2x e mantém — o Discord respeita imagens maiores e não amassa.
    img.save(cache_path, "PNG")
    return cache_path