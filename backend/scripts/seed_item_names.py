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
import sys
from pathlib import Path

sys.path.insert(0, ".")

DUMP = Path("data/ao-bin-dump/items.json")
OUT = Path("data/item_names.json")


def main() -> None:
    if not DUMP.exists():
        print(f"ERRO: {DUMP} não encontrado. Baixe o ao-bin-dump primeiro.", file=sys.stderr)
        sys.exit(1)

    raw = json.loads(DUMP.read_bytes())
    items = raw if isinstance(raw, list) else raw.get("items", [])

    # Indexa por UniqueName
    by_unique: dict[str, str | None] = {}
    for it in items:
        if not it or not isinstance(it, dict):
            continue
        u = it.get("UniqueName")
        if not u:
            continue
        names = it.get("LocalizedNames") or {}
        en = names.get("EN-US")
        by_unique[u] = en

    out: dict[str, str] = {}

    for unique, en in by_unique.items():
        if not en:
            continue

        is_resource = "_LEVEL" in unique  # T4_FIBER_LEVEL1@1, T4_CLOTH_LEVEL2@2
        has_at = "@" in unique
        ench = 0
        if has_at:
            try:
                ench = int(unique.rsplit("@", 1)[1])
            except ValueError:
                pass

        if is_resource:
            # Recurso encantado: nome próprio do localization, sem @
            # T4_FIBER_LEVEL1@1 → "Uncommon Hemp"
            out[unique] = en
            # Também gera sem o @n (catálogo usa assim)
            if has_at:
                base = unique.rsplit("@", 1)[0]
                out[base] = en
        elif has_at and ench > 0:
            # Equipamento encantado: nome do base + @n
            # T4_BAG@1 → "Adept's Bag@1"
            out[unique] = f"{en}@{ench}"
        else:
            # Flat: equipamento ganha @0, recurso/other fica sem
            # T4_BAG → "Adept's Bag@0", T4_FIBER → "Hemp"
            # Distinguir equipamento de recurso: equipamentos têm slot type
            # no dump. Mas o dump antigo nem sempre tem. Heurística simples:
            # se o UniqueName começa com T\d_ e não é recurso/consumível/mount,
            # é equipamento. Na prática, o @0 só importa pra render, e o
            # render aceita tanto "Adept's Bag" quanto "Adept's Bag@0".
            # Pra segurança, equipamentos ganham @0, resto fica sem.
            if _is_equipment(unique, items):
                out[unique] = f"{en}@0"
            else:
                out[unique] = en

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=0, separators=(",", ":")), encoding="utf-8")
    print(f"Gerado {OUT}: {len(out)} entradas")


def _is_equipment(unique: str, items: list) -> bool:
    r"""Heurística: equipamentos são T\d_ com slot type conhecido."""
    # Busca rápida pelo item
    for it in items:
        if not it or not isinstance(it, dict):
            continue
        if it.get("UniqueName") == unique:
            # Se tem slot type no dump, é equipamento
            slot = it.get("@slottype") or it.get("SlotType")
            if slot:
                return True
            # Heurística pelo prefixo: armas, armaduras, capas, bolsas, etc.
            # Recursos: FIBER, CLOTH, WOOD, PLANKS, ORE, METALBAR, HIDE, LEATHER, ROCK, STONEBLOCK
            # Consumíveis: POTION, MEAL, FOOD
            # Mounts: MOUNT
            # Equipamentos: o resto que começa com T\d_
            u = unique
            if not u.startswith("T"):
                return False
            non_equip = (
                "_FIBER", "_CLOTH", "_WOOD", "_PLANKS", "_ORE", "_METALBAR",
                "_HIDE", "_LEATHER", "_ROCK", "_STONEBLOCK",
                "_POTION", "_MEAL", "_FOOD", "_SEED", "_FISH",
                "_MOUNT", "_BUTTER", "_MILK", "_YARROW", "_PUMPKIN",
                "_CABBAGE", "_AGARIC", "_MULLEIN", "_MEAT", "_SOUL",
                "_ARTEFACT", "_TREASURE", "_FARM",
            )
            return not any(x in u for x in non_equip)
    return False


if __name__ == "__main__":
    main()