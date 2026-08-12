"""Gera a imagem PNG de uma juicy kill — simula a aba de kill do Albion.

Layout (estilo Albion killboard, all text in English):
  Killer à esquerda (nome + guilda/aliança + set de equipamento)
  Info no centro (fama, silver por extenso, data/hora UTC, servidor)
  Vítima à direita (nome + guilda/aliança + set de equipamento)
  Linha separadora
  Inventário da vítima abaixo (grid de ícones + quantidade, alinhado com os grids)
  Awakened weapon (killer e vítima): valor abaixo do render da arma

Reaproveita fontes e cores do battle_preview.py — mesmo visual do site.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.api.routes.render import render_item
from app.models.players import AlbionPlayer, PlayerKillEvent
from app.services.awakened import awakened_value, is_awakened
from app.services.lethality import is_likely_lethal

# Diretórios
_BACKEND = Path(__file__).resolve().parents[2]
_OUTPUT = _BACKEND / "data" / "juicy_kill_images"
_LOCKS: dict[int, asyncio.Lock] = {}

# ── Cores (alinhadas com battle_preview.py) ──────────────────────────────────
BG_COLOR = (0x0E, 0x0F, 0x13)
TEXT_COLOR = (0xF5, 0xF5, 0xF7)
DIM_COLOR = (0xA8, 0xA8, 0xB2)
BORDER = (0x3A, 0x3A, 0x42)   # SEP_COLOR do battle_preview
GOLD = (0xD4, 0xA3, 0x38)
KILLER_C = (0x5B, 0x8C, 0xE4)
VICTIM_C = (0xE4, 0x5B, 0x6B)
ALLIANCE_C = (0x8B, 0x8B, 0x9E)

# ── Escala: mesma do battle_preview (S=2) ─────────────────────────────────────
S = 2

# Fontes
_FONT_REGULAR = None
_FONT_SMALL = None
_FONT_SMALL_BOLD = None
_FONT_TITLE = None
_FONT_GUILD = None
_FONT_STATS = None
_FONT_STATS_LABEL = None
_FONT_CENTER = None
_FONT_ITEM_PRICE = None
_FONT_QTY = None


def _load_fonts() -> None:
    global _FONT_REGULAR, _FONT_SMALL, _FONT_SMALL_BOLD, _FONT_TITLE, _FONT_GUILD
    global _FONT_STATS, _FONT_STATS_LABEL, _FONT_CENTER, _FONT_ITEM_PRICE, _FONT_QTY
    if _FONT_REGULAR is not None:
        return
    # Mesmas fontes do battle_preview.py: Cascadia Code, Consolas Bold, Segoe UI Bold
    casc = r"C:\Windows\Fonts\CascadiaCode.ttf"
    casc_vps = "/home/ziggs/ziggs/backend/data/cascadia_code.ttf"
    consb = r"C:\Windows\Fonts\consolab.ttf"
    consb_vps = "/home/ziggs/ziggs/backend/data/consolab.ttf"
    seg = r"C:\Windows\Fonts\segoeuib.ttf"
    seg_vps = "/home/ziggs/ziggs/backend/data/segoeuib.ttf"
    if not Path(casc).exists():
        casc = casc_vps
        consb = consb_vps
        seg = seg_vps
    try:
        _FONT_REGULAR = ImageFont.truetype(casc, int(14 * S))
        _FONT_SMALL = ImageFont.truetype(casc, int(11 * S))
        _FONT_SMALL_BOLD = ImageFont.truetype(casc, int(11 * S))
        _FONT_GUILD = ImageFont.truetype(casc, int(12 * S))
        _FONT_ITEM_PRICE = ImageFont.truetype(casc, int(10 * S))
        _FONT_STATS_LABEL = ImageFont.truetype(seg, int(11 * S))
        _FONT_TITLE = ImageFont.truetype(casc, int(18 * S))
        _FONT_QTY = ImageFont.truetype(consb, int(9 * S))
        _FONT_STATS = ImageFont.truetype(consb, int(18 * S))
        _FONT_CENTER = ImageFont.truetype(consb, int(12 * S))
    except Exception:
        pass
    if _FONT_REGULAR is None:
        _FONT_REGULAR = ImageFont.load_default()
    for n, f in [
        ("_FONT_SMALL", _FONT_REGULAR), ("_FONT_SMALL_BOLD", _FONT_REGULAR),
        ("_FONT_GUILD", _FONT_REGULAR), ("_FONT_STATS", _FONT_REGULAR),
        ("_FONT_STATS_LABEL", _FONT_REGULAR), ("_FONT_CENTER", _FONT_REGULAR),
        ("_FONT_ITEM_PRICE", _FONT_REGULAR), ("_FONT_QTY", _FONT_REGULAR),
    ]:
        if globals()[n] is None:
            globals()[n] = f


# ── Helpers ───────────────────────────────────────────────────────────────────
def _text_w(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _draw_text(draw, text, pos, color, font):
    draw.text(pos, text, fill=color, font=font)


def _draw_centered(draw, text, center_x, y, color, font):
    w = _text_w(draw, text, font)
    draw.text((center_x - w // 2, y), text, fill=color, font=font)


def _draw_right(draw, text, x, y, color, font):
    w = _text_w(draw, text, font)
    draw.text((x - w, y), text, fill=color, font=font)


def _draw_qty(draw, text, x, y, color):
    """Quantidade alinhada à direita, mas centralizada como se tivesse 2 chars.
    Assim "5" fica na mesma posição que "55". Usa _FONT_QTY."""
    w = _text_w(draw, text, _FONT_QTY)
    two_w = _text_w(draw, "00", _FONT_QTY)
    draw.text((x - two_w // 2 - w // 2 - int(1 * S), y - int(2 * S)), text, fill=color, font=_FONT_QTY)


def _truncate_to_w(draw, text, max_w, font):
    if _text_w(draw, text, font) <= max_w:
        return text
    while len(text) > 1 and _text_w(draw, text + "…", font) > max_w:
        text = text[:-1]
    return text + "…" if text else text


def _silver_full(n: int) -> str:
    return f"{n:,}"


def _silver(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


# ── Grid de equipamento ───────────────────────────────────────────────────────
EQUIP_SLOTS = [
    ("Bag", None),
    ("Head", None),
    ("Cape", None),
    ("MainHand", None),
    ("Armor", None),
    ("OffHand", None),
    None,
    ("Shoes", None),
    ("Mount", None),
]

ICON_SIZE = int(56 * S)
SLOT_GAP = int(4 * S)
GRID_COLS = 3
GRID_W = GRID_COLS * (ICON_SIZE + SLOT_GAP)

# Tamanho do render baixado do CDN — 128 é o maior size que o CDN serve
#可靠 pra TODOS os itens (256 retorna 500 em vários). Upscale via LANCZOS.
CDN_ICON_SIZE = 128


async def _fetch_item_icon(item_id: str, quality: int = 0) -> Image.Image | None:
    try:
        response = await render_item(item_id, quality, CDN_ICON_SIZE)
    except HTTPException:
        return None
    try:
        img = Image.open(BytesIO(response.body)).convert("RGBA")
        return img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    except Exception:
        return None


async def _load_icons(items: list[dict], icon_cache: dict) -> bool:
    """Carrega ícones da CDN. Itens sem render (kill trophies do Mists, etc.)
    ficam None no cache e o grid desenha '—' no lugar — não aborta a imagem
    inteira por causa de um item sem arte."""
    keys = list(dict.fromkeys(
        (item["Type"], item.get("Quality", 0))
        for item in items if item and item.get("Type")
    ))
    missing = [key for key in keys if key not in icon_cache]
    if missing:
        icons = await asyncio.gather(*(_fetch_item_icon(*key) for key in missing))
        icon_cache.update(zip(missing, icons))
    return True


def _draw_equip_grid(draw, img, equipment, x0, y0, icon_cache, show_awakened=False):
    """Desenha o grid 3×3 de equipamento — sem borda.
    Quantidade no canto inferior direito do render (subida).
    Arma awakened: valor abaixo do render (+ pra vítima, sem + pra killer)."""
    cell = ICON_SIZE + SLOT_GAP
    for i, slot in enumerate(EQUIP_SLOTS):
        col = i % GRID_COLS
        row = i // GRID_COLS
        x = x0 + col * cell
        y = y0 + row * cell

        if slot is None:
            continue

        slot_key, _ = slot
        item = (equipment or {}).get(slot_key)
        if not item or not item.get("Type"):
            continue

        t = item["Type"]
        icon = icon_cache.get((t, item.get("Quality", 0)))
        if icon is not None:
            img.paste(icon, (x, y), icon)
        else:
            draw.text((x + int(20 * S), y + int(20 * S)), "—", fill=DIM_COLOR, font=_FONT_SMALL)

        # Quantidade no canto inferior direito (centralizada como 2 chars)
        count = item.get("Count", 1)
        if count > 1:
            _draw_qty(draw, str(count), x + ICON_SIZE - int(8 * S), y + ICON_SIZE - int(20 * S), TEXT_COLOR)

        # Preço awakened da arma: abaixo do render
        soul = item.get("LegendarySoul")
        if show_awakened and slot_key == "MainHand" and is_awakened(t, soul):
            awake_val = awakened_value(t, soul)
            if awake_val > 0:
                price_str = f"+{_silver(awake_val)}"
                _draw_centered(draw, price_str, x + ICON_SIZE // 2, y + ICON_SIZE + int(2 * S), GOLD, _FONT_ITEM_PRICE)

        # Killer awakened: valor sem o +
        if not show_awakened and slot_key == "MainHand" and is_awakened(t, soul):
            awake_val = awakened_value(t, soul)
            if awake_val > 0:
                price_str = _silver(awake_val)
                _draw_centered(draw, price_str, x + ICON_SIZE // 2, y + ICON_SIZE + int(2 * S), GOLD, _FONT_ITEM_PRICE)


async def render_juicy_kill_image(db: Session, kill_id: int) -> Path | None:
    # A mesma kill pode entrar simultaneamente nas filas de várias guildas.
    async with _LOCKS.setdefault(kill_id, asyncio.Lock()):
        return await _render_juicy_kill_image(db, kill_id)


async def _render_juicy_kill_image(db: Session, kill_id: int) -> Path | None:
    """Gera e cacheia o PNG de uma kill verificada."""
    _load_fonts()
    # v4 ignora PNGs incompletos e formulas antigas de awakened.
    out_path = _OUTPUT / f"{kill_id}_v4.png"
    if out_path.exists():
        return out_path
    ev = db.get(PlayerKillEvent, kill_id)
    if ev is None:
        return None
    if not is_likely_lethal(ev.fame, ev.victim_equipment, ev.group_member_count):
        return None
    try:

        killer = db.get(AlbionPlayer, ev.killer_player_id) if ev.killer_player_id else None
        victim = db.get(AlbionPlayer, ev.victim_player_id) if ev.victim_player_id else None

        killer_name = killer.name if killer else "???"
        killer_guild = killer.guild_name if killer else None
        killer_alliance = killer.alliance_name if killer else None
        victim_name = victim.name if victim else "???"
        victim_guild = victim.guild_name if victim else None
        victim_alliance = victim.alliance_name if victim else None

        killer_eq = ev.killer_equipment or {}
        victim_eq = dict(ev.victim_equipment or {})
        victim_inv = ev.victim_inventory or []

        # Libera read tx antes dos HTTP (download de ícones da CDN).
        db.commit()

        # Food e Potion do set da vítima vão pro inventário (primeiros)
        inv_items = [i for i in victim_inv if i and i.get("Type")]
        for slot_key in ("Food", "Potion"):
            item = victim_eq.get(slot_key)
            if item and item.get("Type"):
                inv_items.insert(0, {"Type": item["Type"], "Count": item.get("Count", 1), "Quality": item.get("Quality", 0)})
                victim_eq = {k: v for k, v in victim_eq.items() if k != slot_key}

        inv_list = [(i["Type"], i.get("Quality", 0), i.get("Count", 1)) for i in inv_items]

        # Um PNG incompleto ficaria cacheado para sempre. Falha inteira e deixa
        # o bot tentar de novo no proximo ciclo se qualquer render nao chegou.
        icon_cache: dict[tuple[str, int], Image.Image | None] = {}
        icon_items = [
            *(item for item in killer_eq.values() if item),
            *(item for item in victim_eq.values() if item),
            *inv_items,
        ]
        if not await _load_icons(icon_items, icon_cache):
            return None

        # --- Layout ---
        W = int(640 * S)
        MARGIN = int(16 * S)
        inv_area_w = W - 2 * MARGIN
        PER_ROW = inv_area_w // (ICON_SIZE + SLOT_GAP)

        # Grid: 3 linhas + extra se arma awakened
        killer_main = killer_eq.get("MainHand") or {}
        victim_main = victim_eq.get("MainHand") or {}
        killer_awake = is_awakened(killer_main.get("Type", ""), killer_main.get("LegendarySoul"))
        victim_awake = is_awakened(victim_main.get("Type", ""), victim_main.get("LegendarySoul"))
        grid_extra = int(14 * S) if (killer_awake or victim_awake) else 0
        grid_h = 3 * (ICON_SIZE + SLOT_GAP) + grid_extra

        # Inventário
        inv_row_h = ICON_SIZE + SLOT_GAP
        inv_rows = max(1, (len(inv_list) + PER_ROW - 1) // PER_ROW) if inv_list else 0
        inv_block_h = (int(24 * S) + inv_rows * inv_row_h) if inv_list else int(24 * S)

        # Altura total
        H = int(64 * S) + int(18 * S) + int(16 * S) + int(6 * S) + grid_h + int(8 * S) + inv_block_h + int(12 * S)

        img = Image.new("RGB", (W, H), BG_COLOR)
        draw = ImageDraw.Draw(img)
        draw.rectangle([1, 1, W - 2, H - 2], outline=BORDER, width=1)

        cx = W // 2

        # ── Header: killer (esq) | victim (dir) ──
        header_y = int(12 * S)
        text_inset = int(9 * S)

        k_name = _truncate_to_w(draw, killer_name, cx - int(30 * S), _FONT_TITLE)
        _draw_text(draw, k_name, (MARGIN + text_inset, header_y), KILLER_C, _FONT_TITLE)
        if killer_alliance or killer_guild:
            kx = MARGIN + text_inset
            if killer_alliance:
                a_text = _truncate_to_w(draw, f"[{killer_alliance}]", cx - int(30 * S), _FONT_GUILD)
                _draw_text(draw, a_text, (kx, header_y + int(22 * S)), ALLIANCE_C, _FONT_GUILD)
                kx += _text_w(draw, a_text + " ", _FONT_GUILD)
            if killer_guild:
                g_text = _truncate_to_w(draw, killer_guild, cx - int(30 * S) - (kx - MARGIN - text_inset), _FONT_GUILD)
                _draw_text(draw, g_text, (kx, header_y + int(22 * S)), DIM_COLOR, _FONT_GUILD)

        v_name = _truncate_to_w(draw, victim_name, cx - int(30 * S), _FONT_TITLE)
        _draw_right(draw, v_name, W - MARGIN - text_inset, header_y, VICTIM_C, _FONT_TITLE)
        if victim_alliance or victim_guild:
            vx = W - MARGIN - text_inset
            if victim_guild:
                g_text = _truncate_to_w(draw, victim_guild, cx - int(30 * S), _FONT_GUILD)
                gw = _text_w(draw, g_text, _FONT_GUILD)
                _draw_text(draw, g_text, (vx - gw, header_y + int(22 * S)), DIM_COLOR, _FONT_GUILD)
                vx -= gw + _text_w(draw, " ", _FONT_GUILD)
            if victim_alliance:
                a_text = _truncate_to_w(draw, f"[{victim_alliance}]", cx - int(30 * S) - (W - MARGIN - text_inset - vx), _FONT_GUILD)
                aw = _text_w(draw, a_text, _FONT_GUILD)
                _draw_text(draw, a_text, (vx - aw, header_y + int(22 * S)), ALLIANCE_C, _FONT_GUILD)

        # ── Server (isolado, acima da data) ──
        region_map = {"americas": "Americas", "europe": "Europe", "asia": "Asia"}
        region_label = region_map.get(ev.region, ev.region)
        server_y = header_y + int(44 * S)
        _draw_centered(draw, region_label, cx, server_y, DIM_COLOR, _FONT_CENTER)

        # ── Date/time UTC (abaixo do servidor) ──
        ts = ev.timestamp.strftime("%d/%m/%Y %H:%M UTC") if ev.timestamp else "?"
        date_y = server_y + int(16 * S)
        _draw_centered(draw, ts, cx, date_y, DIM_COLOR, _FONT_CENTER)

        # ── Info central: fame + silver ──
        info_x = cx
        info_y = date_y + int(40 * S)
        _draw_centered(draw, _silver(ev.fame), info_x, info_y, GOLD, _FONT_STATS)
        _draw_centered(draw, "fame", info_x, info_y + int(22 * S), DIM_COLOR, _FONT_STATS_LABEL)

        silver = ev.silver_dropped or 0
        _draw_centered(draw, _silver_full(silver), info_x, info_y + int(56 * S), TEXT_COLOR, _FONT_STATS)
        _draw_centered(draw, "silver dropped", info_x, info_y + int(78 * S), DIM_COLOR, _FONT_STATS_LABEL)

        if not ev.is_solo:
            _draw_centered(draw, f"{ev.participant_count} participants", info_x, info_y + int(100 * S), DIM_COLOR, _FONT_STATS_LABEL)

        # ── Sets de equipamento (grids 3×3) ──
        grid_y = header_y + int(44 * S)
        grid_inset = int(9 * S)
        killer_x = MARGIN + grid_inset
        victim_x = W - MARGIN - GRID_W - grid_inset

        _draw_equip_grid(draw, img, killer_eq, killer_x, grid_y, icon_cache, show_awakened=False)
        _draw_equip_grid(draw, img, victim_eq, victim_x, grid_y, icon_cache, show_awakened=True)

        # ── Linha separadora (acima do texto do inventário) ──
        sep_y = grid_y + grid_h + int(8 * S)
        draw.line([(MARGIN, sep_y), (W - MARGIN, sep_y)], fill=BORDER, width=1)

        # ── Inventário da vítima ──
        inv_y = sep_y + int(8 * S)
        _draw_centered(draw, "VICTIM INVENTORY", cx, inv_y, DIM_COLOR, _FONT_SMALL_BOLD)
        inv_y += int(24 * S)

        if not inv_list:
            _draw_centered(draw, "Empty", cx, inv_y, DIM_COLOR, _FONT_SMALL)
        else:
            ix = MARGIN
            iy = inv_y
            for idx, (item_id, quality, count) in enumerate(inv_list):
                col = idx % PER_ROW
                if col == 0 and idx > 0:
                    iy += inv_row_h
                    ix = MARGIN

                icon = icon_cache.get((item_id, quality))
                if icon is not None:
                    img.paste(icon, (ix, iy), icon)

                if count > 1:
                    _draw_qty(draw, str(count), ix + ICON_SIZE - int(8 * S), iy + ICON_SIZE - int(20 * S), TEXT_COLOR)

                ix += ICON_SIZE + SLOT_GAP

        # Salvar
        _OUTPUT.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(".tmp.png")
        img.save(tmp_path, "PNG")
        tmp_path.replace(out_path)
        return out_path
    finally:
        pass
