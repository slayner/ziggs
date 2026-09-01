"""
Gera `data/item_names.json` — mapeamento UniqueName → nome do jogo (EN).

O nome do jogo é o ID canônico do sistema de preços: é o que o render CDN
aceita (render.albiononline.com/v1/item/Hemp.png) e o que o jogador vê
no mercado. UniqueNames como T4_FIBER_LEVEL2@2 foram inventados pelo ADP
e não existem no jogo.

Regras:
  - Recurso flat (T4_FIBER): nome direto do localization ("Hemp")
  - Recurso encantado (T4_FIBER_LEVEL1@1): nome próprio do localization
    ("Uncommon Hemp") — sem @ no game_name
  - Equipamento flat (T4_BAG): nome do localization + "@0"
    ("Adept's Bag@0")
  - Equipamento encantado (T4_BAG@1): nome do base + "@n"
    ("Adept's Bag@1") — o localization não tem entrada própria

Gera com DUAS chaves por item encantado de recurso:
  "T4_FIBER_LEVEL2@2" → "Rare Hemp"
  "T4_FIBER_LEVEL2"   → "Rare Hemp"
(o catálogo usa sem @, o jogo/companion manda com @)

Uso:
  cd backend && python -m scripts.seed_item_names
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")

DUMP = Path("data/ao-bin-dump/items.json")
LOC = Path("data/ao-bin-dump/localization.json")
OUT = Path("data/item_names.json")


def _flatten_items(raw: dict) -> list[dict]:
    items_node = raw.get("items", raw)
    items: list = []
    if isinstance(items_node, list):
        return items_node
    if isinstance(items_node, dict):
        for v in items_node.values():
            if isinstance(v, list):
                items.extend(v)
    return items


def _load_localization() -> dict[str, str]:
    if not LOC.exists():
        return {}
    raw = json.loads(LOC.read_bytes())
    tus = raw.get("tmx", {}).get("body", {}).get("tu", [])
    out: dict[str, str] = {}
    if isinstance(tus, dict):
        tus = [tus]
    for tu in tus:
        tuid = tu.get("@tuid", "")
        if not tuid:
            continue
        tuvs = tu.get("tuv", [])
        if isinstance(tuvs, dict):
            tuvs = [tuvs]
        for tuv in tuvs:
            if tuv.get("@xml:lang") == "EN-US":
                seg = tuv.get("seg", "")
                if seg:
                    out[tuid] = seg  # mantém o @ no tuid
                break
    return out


def main() -> None:
    if not DUMP.exists():
        print(f"ERRO: {DUMP} não encontrado. Baixe o ao-bin-dump primeiro.", file=sys.stderr)
        sys.exit(1)

    raw = json.loads(DUMP.read_bytes())
    items = _flatten_items(raw)
    loc = _load_localization()

    # Indexa por @uniquename
    by_unique: dict[str, str | None] = {}
    for it in items:
        if not it or not isinstance(it, dict):
            continue
        u = it.get("@uniquename") or it.get("UniqueName")
        if not u:
            continue
        # Tenta LocalizedNames (formato antigo) depois localization.json
        names = it.get("LocalizedNames") or {}
        en = names.get("EN-US") if names else None
        if not en:
            en = loc.get(u) or loc.get(f"@ITEMS_{u}")
        if not en:
            # Tenta sem @n (base item)
            base = u.rsplit("@", 1)[0] if "@" in u else u
            if base != u:
                en = loc.get(base) or loc.get(f"@ITEMS_{base}")
        by_unique[u] = en

    out: dict[str, str] = {}

    for unique, en in by_unique.items():
        if not en:
            continue

        is_resource = "_LEVEL" in unique
        has_at = "@" in unique
        ench = 0
        if has_at:
            try:
                ench = int(unique.rsplit("@", 1)[1])
            except ValueError:
                pass

        if is_resource:
            out[unique] = en
            if has_at:
                base = unique.rsplit("@", 1)[0]
                out[base] = en
        elif has_at and ench > 0:
            out[unique] = f"{en}@{ench}"
        else:
            if _is_equipment(unique, items):
                out[unique] = f"{en}@0"
            else:
                out[unique] = en

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=0, separators=(",", ":")), encoding="utf-8")
    print(f"Gerado {OUT}: {len(out)} entradas")


_NON_EQUIP = (
    "_FIBER", "_CLOTH", "_WOOD", "_PLANKS", "_ORE", "_METALBAR",
    "_HIDE", "_LEATHER", "_ROCK", "_STONEBLOCK",
    "_POTION", "_MEAL", "_FOOD", "_SEED", "_FISH",
    "_MOUNT", "_BUTTER", "_MILK", "_YARROW", "_PUMPKIN",
    "_CABBAGE", "_AGARIC", "_MULLEIN", "_MEAT", "_SOUL",
    "_ARTEFACT", "_TREASURE", "_FARM",
)


def _is_equipment(unique: str, items: list) -> bool:
    for it in items:
        if not it or not isinstance(it, dict):
            continue
        uid = it.get("@uniquename") or it.get("UniqueName")
        if uid == unique:
            slot = it.get("@slottype") or it.get("SlotType")
            if slot:
                return True
            u = unique
            if not u.startswith("T"):
                return False
            return not any(x in u for x in _NON_EQUIP)
    return False


if __name__ == "__main__":
    main()