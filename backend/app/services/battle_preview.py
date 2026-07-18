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

from app.models.battles import Battle, BattleGuild, BattleParticipant, BattleSide
from app.services import battle_groups

_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "battle_preview_cache"

# Cores de heat (mesmas do BattleTracker.tsx)
HEAT_MAX = (0x66, 0x71, 0x60)   # #667160 — mais kills
HEAT_MIN = (0x52, 0x52, 0x5C)   # #52525C — menos kills
BG_COLOR = (0x0E, 0x0F, 0x13)   # fundo escuro do site
TEXT_COLOR = (0xE4, 0xE4, 0xE7) # texto claro
# Cor "discreta" = heat mínimo (a guilda que menos se destacou) — usada pra
# player count, servidor e horario, pra não competir com o nome da guilda.
DIM_COLOR = HEAT_MIN

# Dimensões — largura fixa, altura dinâmica conforme nº de factions
IMG_W = 600
PADDING = 16
MAX_FACTIONS = 4
# Altura fixa do topo (header + destaque + horário) até a linha separadora
_TOP_H = 118
_ROW_H = 20  # altura de cada linha da lista detalhada (fonte maior)
_LIST_HEADER_H = 24

# Fonte Segoe UI (mesma do site)
_FONT_REGULAR = None
_FONT_BOLD = None
_FONT_SEMIBOLD = None
_FONT_SMALL = None       # footer + player count
_FONT_SMALL_BOLD = None  # mesmo size do _FONT_SMALL, só bold
_FONT_LIST = None        # lista detalhada — maior que _FONT_SMALL
_FONT_LIST_BOLD = None   # lista detalhada bold
_FONT_HEADER = None      # players · kills no topo
_FONT_PERIOD = None      # período multi-batalha — Consolas (monospace) pra setra → destacar


def _load_fonts() -> None:
    global _FONT_REGULAR, _FONT_BOLD, _FONT_SEMIBOLD, _FONT_SMALL, _FONT_SMALL_BOLD, _FONT_LIST, _FONT_LIST_BOLD, _FONT_HEADER, _FONT_PERIOD
    if _FONT_REGULAR is not None:
        return
    paths = [
        (r"C:\Windows\Fonts\segoeui.ttf", "regular"),
        (r"C:\Windows\Fonts\segoeuib.ttf", "bold"),
        (r"C:\Windows\Fonts\seguisb.ttf", "semibold"),
        (r"C:\Windows\Fonts\consola.ttf", "mono"),
    ]
    for path, kind in paths:
        if not Path(path).exists():
            continue
        try:
            if kind == "regular":
                _FONT_REGULAR = ImageFont.truetype(path, 15)
                _FONT_SMALL = ImageFont.truetype(path, 11)
                _FONT_LIST = ImageFont.truetype(path, 13)
                _FONT_HEADER = ImageFont.truetype(path, 14)
            elif kind == "bold":
                _FONT_BOLD = ImageFont.truetype(path, 14)
                _FONT_SMALL_BOLD = ImageFont.truetype(path, 11)
                _FONT_LIST_BOLD = ImageFont.truetype(path, 13)
            elif kind == "semibold":
                _FONT_SEMIBOLD = ImageFont.truetype(path, 15)
            elif kind == "mono":
                _FONT_PERIOD = ImageFont.truetype(path, 10)
        except Exception:
            pass
    if _FONT_REGULAR is None:
        _FONT_REGULAR = ImageFont.load_default()
    if _FONT_BOLD is None:
        _FONT_BOLD = _FONT_REGULAR
    if _FONT_SEMIBOLD is None:
        _FONT_SEMIBOLD = _FONT_REGULAR
    if _FONT_SMALL is None:
        _FONT_SMALL = _FONT_REGULAR
    if _FONT_SMALL_BOLD is None:
        _FONT_SMALL_BOLD = _FONT_BOLD
    if _FONT_LIST is None:
        _FONT_LIST = _FONT_REGULAR
    if _FONT_LIST_BOLD is None:
        _FONT_LIST_BOLD = _FONT_BOLD
    if _FONT_HEADER is None:
        _FONT_HEADER = _FONT_BOLD
    if _FONT_PERIOD is None:
        _FONT_PERIOD = _FONT_SMALL


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


def render_battle_preview(db: Session, public_id: str) -> Path | None:
    """Gera (ou serve do cache) a imagem PNG de resumo da batalha.
    Retorna o caminho do arquivo, ou None se a batalha não existir."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _CACHE_DIR / f"{public_id}.png"
    if cache_path.exists():
        return cache_path

    _load_fonts()

    battle_ids = battle_groups.get_group_battle_ids(db, public_id)
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

    # Filtra factions de baixo impacto — mesma lógica do BattlePage.tsx
    # splitByImpact: engagement (kills+deaths) < 3% do total = rato/gank que
    # não fez diferença, escondido da imagem pra não inflar a altura.
    all_rows = sorted(all_factions.values(), key=lambda r: r["kills"], reverse=True)
    total_engagement = sum(f["kills"] + f.get("deaths", 0) for f in all_rows)
    if total_engagement > 0:
        factions = [f for f in all_rows if (f["kills"] + f.get("deaths", 0)) / total_engagement >= 0.03]
    else:
        factions = all_rows
    # Máximo 4 — sem "Others", o excesso só não aparece (kills/players totais
    # no header já contabilizam todo mundo).
    factions = factions[:MAX_FACTIONS]

    kills_list = [f["kills"] for f in factions]
    max_kills = max(kills_list) if kills_list else 1
    min_kills = min(kills_list) if kills_list else 0

    # Altura dinâmica: topo fixo + cabeçalho da lista + 1 linha por faction
    n = len(factions)
    img_h = _TOP_H + _LIST_HEADER_H + n * _ROW_H + PADDING
    img = Image.new("RGB", (IMG_W, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)
    cx = IMG_W // 2

    # Cabeçalho centralizado: players · kills (fonte menor)
    header = f"{total_players} players  ·  {total_kills} kills"
    _draw_centered(draw, header, cx, PADDING, TEXT_COLOR, _FONT_HEADER)

    # Servidor no canto superior direito (siglas: AM/EU/AS) — Consolas
    region_map = {"americas": "AM", "europe": "EU", "asia": "AS"}
    battle = db.get(Battle, battle_ids[0])
    if battle:
        region_tag = region_map.get(battle.region, battle.region.upper())
        rw = _text_w(draw, region_tag, _FONT_PERIOD)
        draw.text((IMG_W - PADDING - rw, PADDING + 2), region_tag, fill=DIM_COLOR, font=_FONT_PERIOD)

    # ID do Albion no canto superior esquerdo (ou "MULTI" se multi-batalha) — Consolas
    if is_multi:
        id_label = "MULTI"
    elif battle:
        id_label = f"#{battle.albion_id}"
    else:
        id_label = ""
    if id_label:
        draw.text((PADDING, PADDING + 2), id_label, fill=DIM_COLOR, font=_FONT_PERIOD)

    # Factions em destaque — agrupadas no centro, espaço fixo pra até 4
    faction_spacing = min(120, (IMG_W - 2 * PADDING) // max(n, 2))
    total_w = (n - 1) * faction_spacing
    start_x = cx - total_w // 2
    y = PADDING + 32

    for i, f in enumerate(factions):
        x = start_x + i * faction_spacing
        color = _heat_color(f["kills"], max_kills, min_kills)

        if f["alliance_name"]:
            tag = f"[{_truncate(f['alliance_name'], 10)}]"
        else:
            tag = _truncate(f["guild_name"], 12)
        _draw_centered(draw, tag, x, y, color, _FONT_SEMIBOLD)
        _draw_centered(draw, str(f["player_count"]), x, y + 22, DIM_COLOR, _FONT_SMALL)

    # Horário UTC — separado do destaque. Multi-batalha mostra período
    # (início → fim); batalha única mostra só a data.
    footer_y = y + 50
    battle = db.get(Battle, battle_ids[0])
    if first_start:
        fs = first_start if first_start.tzinfo else first_start.replace(tzinfo=timezone.utc)
        if is_multi and last_end:
            le = last_end if last_end.tzinfo else last_end.replace(tzinfo=timezone.utc)
            if fs.strftime("%d/%m/%Y") == le.strftime("%d/%m/%Y"):
                footer = f"{fs.strftime('%d/%m/%Y %H:%M')} → {le.strftime('%H:%M')} UTC"
            else:
                footer = f"{fs.strftime('%d/%m/%Y %H:%M')} → {le.strftime('%d/%m/%Y %H:%M')} UTC"
        else:
            footer = fs.strftime("%d/%m/%Y %H:%M UTC")
        # Consolas em toda linha de período/data/ID/servidor
        _draw_centered(draw, footer, cx, footer_y, DIM_COLOR, _FONT_PERIOD)

    # ── Lista detalhada ──
    sep_y = footer_y + 18
    draw.line([(PADDING, sep_y), (IMG_W - PADDING, sep_y)], fill=(0x26, 0x26, 0x2B), width=1)

    # Colunas right-aligned: PLAYERS | KILLS | DEATHS
    col_deaths_x = IMG_W - PADDING
    col_kills_x = col_deaths_x - 60
    col_players_x = col_kills_x - 55
    # Largura máxima disponível pro nome da guilda (esquerda até col_players)
    name_max_w = col_players_x - PADDING - 10

    list_y = sep_y + 8
    _draw_text(draw, "GUILD / ALLIANCE", (PADDING, list_y), DIM_COLOR, _FONT_LIST)
    for label, x in [("PLAYERS", col_players_x), ("KILLS", col_kills_x), ("DEATHS", col_deaths_x)]:
        lw = _text_w(draw, label, _FONT_LIST)
        draw.text((x - lw, list_y), label, fill=DIM_COLOR, font=_FONT_LIST)

    # Cores de kills/deaths — opacas (não saturadas)
    KILLS_C = (0x3A, 0x8A, 0x2E)    # verde muted
    DEATHS_C = (0x9A, 0x3A, 0x3A)   # vermelho muted

    def _draw_right(text: str, x: int, y: int, color: tuple, font) -> None:
        w = _text_w(draw, text, font)
        draw.text((x - w, y), text, fill=color, font=font)

    def _truncate_to_w(text: str, max_w: int, font) -> str:
        """Trunca o texto pra caber em max_w pixels, medindo largura real."""
        if _text_w(draw, text, font) <= max_w:
            return text
        while len(text) > 1 and _text_w(draw, text + "…", font) > max_w:
            text = text[:-1]
        return text + "…" if text else text

    list_y += 20
    for f in factions:
        raw_name = f"[{f['alliance_name']}]" if f["alliance_name"] else f["guild_name"]
        name = _truncate_to_w(raw_name, name_max_w, _FONT_LIST)
        kills = f["kills"]
        deaths = f.get("deaths", 0)
        players = f["player_count"]

        # Sem bold na lista — só a cor distingue o maior número
        _draw_text(draw, name, (PADDING, list_y), TEXT_COLOR, _FONT_LIST)
        _draw_right(str(players), col_players_x, list_y, DIM_COLOR, _FONT_LIST)

        k_color = KILLS_C if kills > deaths else DIM_COLOR
        _draw_right(str(kills), col_kills_x, list_y, k_color, _FONT_LIST)

        d_color = DEATHS_C if deaths > kills else DIM_COLOR
        _draw_right(str(deaths), col_deaths_x, list_y, d_color, _FONT_LIST)

        list_y += _ROW_H

    img.save(cache_path, "PNG")
    return cache_path