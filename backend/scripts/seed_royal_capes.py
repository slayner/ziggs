"""Adiciona familias faltantes ao catalog.json: itens Royal (armaduras)
e capas faction (CAPEITEM_*). Baseado em seed_craft_catalog_extras.py.

Itens Royal (T4-T8 armor/head/shoes com sufixo _ROYAL): receitas usam 1
artefato base (T{n}_HEAD_*_SET1/2/3, noReturn) + N tokens de quest (noReturn).
Todos resources são noReturn — sem RRR factor, custo = soma dos noReturn
priced (regra de presunção cobre isso).

Capas faction (CAPEITEM_*): 1 capa base (T{n}_CAPE, noReturn) + 1 BP
faccionário (noReturn) + N faction tokens (noReturn). Mesma estrutura.

Como rodar:
  cd backend && python -m scripts.seed_royal_capes

Requer items.xml em backend/data/ao-bin-dump/. Se faltar, instruções de
download no README do ao-data/ao-bin-dumps.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

DUMP_DIR = Path(__file__).resolve().parents[1] / "data" / "ao-bin-dump"
ITEMS_XML = DUMP_DIR / "items.xml"
ITEMS_JSON = DUMP_DIR / "items.json"
CATALOG_FILE = Path(__file__).resolve().parents[2] / "frontend" / "public" / "data" / "catalog.json"

TIER_PREFIX: dict[int, str] = {
    4: "Adept's", 5: "Expert's", 6: "Master's", 7: "Grandmaster's", 8: "Elder's",
}

TIER_RE = re.compile(r"^T(\d+)_")
ENCH_RE = re.compile(r"@\d+$")


def tier_enchant(uid: str) -> tuple[int, int]:
    tm = TIER_RE.match(uid)
    t = int(tm.group(1)) if tm else 0
    em = ENCH_RE.search(uid)
    e = int(em.group(0)[1:]) if em else 0
    return t, e


def base_id(uid: str) -> str:
    return re.sub(r"@\d+$", "", re.sub(r"^T\d+_", "", uid or ""))


def slot_from_base(base: str) -> str:
    if base.startswith("HEAD_"):
        return "head"
    if base.startswith("ARMOR_"):
        return "armor"
    if base.startswith("SHOES_"):
        return "shoes"
    if base.startswith("CAPEITEM_"):
        return "cape"
    return "mainhand"


def bonus_city_from_family(base: str) -> str:
    # Royal items: craft em Caerleon (guild city).
    if "ROYAL" in base:
        return "Caerleon"
    # Capas faction: craft em Caerleon (guild city) pra FW_*;
    # algumas capas especificas sao de outras cidades. Por simplicidade,
    # Caerleon para todas as capas faction (todas usam BP + token).
    if base.startswith("CAPEITEM_"):
        return "Caerleon"
    return "Caerleon"


def parse_resources(cr: ET.Element) -> list[dict]:
    out = []
    for r in cr.findall("craftresource"):
        uid = r.get("uniquename", "")
        count = int(r.get("count", 0))
        noRet = r.get("maxreturnamount") == "0"
        entry: dict = {"uniqueName": uid, "count": count}
        if noRet:
            entry["noReturn"] = True
        out.append(entry)
    return out


def load_item_values(root: ET.Element) -> dict[str, int]:
    out: dict[str, int] = {}
    for el in root.iter():
        uid = el.get("uniquename", "")
        iv = el.get("itemvalue")
        if not uid or not iv:
            continue
        try:
            out[uid] = int(float(iv))
        except ValueError:
            pass
    return out


def compute_item_value(resources: list[dict], item_values: dict[str, int]) -> int:
    return sum(item_values.get(r["uniqueName"], 0) * r["count"] for r in resources)


def load_localized_names() -> dict[str, dict[str, str]]:
    """items.json do ao-bin-dump tem estrutura de dict (XML→JSON direto),
    não lista de itens como se esperava. Aqui só retornamos {} —
    nomes virão do fallback `base.replace('_', ' ').title()` em build_family.
    Tradução é coisa de UI; backend não usa os nomes pra presunção."""
    return {}


def is_target_family(base: str) -> bool:
    """Royal armor/head/shoes ou CAPEITEM_* faction."""
    if "ROYAL" in base and (
        base.startswith("HEAD_") or base.startswith("ARMOR_") or base.startswith("SHOES_")
    ):
        return True
    if base.startswith("CAPEITEM_"):
        return True
    return False


def find_elements(root: ET.Element, base: str) -> list[ET.Element]:
    matches: list[ET.Element] = []
    for tag in ("weapon", "offhand", "equipmentitem", "transformationweapon"):
        for el in root.iter(tag):
            uid = el.get("uniquename", "")
            if base_id(uid) == base:
                matches.append(el)
    return matches


def build_family(
    root: ET.Element,
    base: str,
    names: dict[str, dict[str, str]],
    item_values: dict[str, int],
) -> dict | None:
    elements = find_elements(root, base)
    if not elements:
        print(f"[WARN] {base}: nenhum elemento no items.xml")
        return None

    variations: list[dict] = []
    seen_uids: set[str] = set()
    for el in elements:
        uid = el.get("uniquename", "")
        t, _ = tier_enchant(uid)
        if t < 4 or t > 8:
            continue
        item_power = int(el.get("itempower", "0") or 0)
        for cr in el.iter("craftingrequirements"):
            enchant = int(cr.get("enchantmentlevel", "0") or 0)
            focus = int(cr.get("craftingfocus", "0") or 0)
            resources = parse_resources(cr)
            if not resources:
                continue
            uid_full = uid if enchant == 0 else f"{uid.split('@')[0]}@{enchant}"
            if uid_full in seen_uids:
                continue
            seen_uids.add(uid_full)
            iv = compute_item_value(resources, item_values)
            variations.append({
                "uniqueName": uid_full,
                "tier": t,
                "enchant": enchant,
                "itemPower": item_power,
                "focus": focus,
                "itemValue": iv,
                "resources": resources,
            })

    if not variations:
        return None

    variations.sort(key=lambda v: (v["tier"], v["enchant"]))
    sample_uid = variations[0]["uniqueName"]
    localized = names.get(sample_uid, {})
    name = localized.get("en") or base.replace("_", " ").title()

    return {
        "familyKey": base,
        "name": name,
        "slot": slot_from_base(base),
        "category": "capes" if base.startswith("CAPEITEM_") else "armor",
        "subcategory": base.lower(),
        "craftCategory": base.lower(),
        "bonusCity": bonus_city_from_family(base),
        "kind": "equipment",
        "variations": variations,
    }


def main() -> None:
    global ITEMS_XML
    if not ITEMS_XML.exists():
        # Permite override via env var para items.xml em outro path.
        alt = Path(__file__).resolve().parents[1] / "data" / "items.xml"
        if alt.exists():
            ITEMS_XML = alt
        else:
            print(f"items.xml não encontrado em {ITEMS_XML} nem {alt}", file=sys.stderr)
            print("Baixe do ao-data/ao-bin-dumps (github.com/ao-data/ao-bin-dumps).", file=sys.stderr)
            sys.exit(1)

    root = ET.parse(ITEMS_XML).getroot()
    names = load_localized_names()
    item_values = load_item_values(root)
    catalog = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))

    existing_keys = {f["familyKey"] for f in catalog}
    added: list[dict] = []

    # Itera todas as familyKeys possíveis: armaduras ROYAL e CAPEITEM_*
    candidates: set[str] = set()
    for tag in ("weapon", "offhand", "equipmentitem", "transformationweapon"):
        for el in root.iter(tag):
            uid = el.get("uniquename", "")
            if not uid:
                continue
            t, _ = tier_enchant(uid)
            if t < 4 or t > 8:
                continue
            base = base_id(uid)
            if is_target_family(base):
                candidates.add(base)

    for base in sorted(candidates):
        if base in existing_keys:
            print(f"[SKIP] {base} já existe no catalog")
            continue
        fam = build_family(root, base, names, item_values)
        if fam:
            added.append(fam)
            print(f"[ADD] {base} ({len(fam['variations'])} variações)")

    added.sort(key=lambda f: f["familyKey"])
    catalog.extend(added)
    CATALOG_FILE.write_text(
        json.dumps(catalog, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nCatalogo atualizado: {len(catalog)} familias ({len(added)} adicionadas).")


if __name__ == "__main__":
    main()