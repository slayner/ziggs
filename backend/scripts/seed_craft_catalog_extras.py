"""Adiciona famílias faltantes (armas/offhands de cristal e cajados polimorfos)
ao `frontend/public/data/catalog.json` usando o dump ao-bin-dumps.

Itens de cristal e polimorfos não vêm no catalog.json original gerado pelo
projeto; eles precisam de render por nome EN e possuem receitas distintas.
Este script faz uma adição incremental: preserva as 271 famílias existentes
e insere só o que falta.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

DUMP_DIR = Path(__file__).resolve().parents[1] / "data" / "ao-bin-dump"
ITEMS_XML = DUMP_DIR / "items.xml"
ITEMS_JSON = DUMP_DIR / "items.json"
CATALOG_FILE = Path(__file__).resolve().parents[2] / "frontend" / "public" / "data" / "catalog.json"

# Armas/offhands de cristal conhecidos (base id sem tier). Mapeamos para nome EN
# usado no render e categoria de craft da árvore base.
CRYSTAL_FAMILIES: dict[str, dict] = {
    "MAIN_SWORD_CRYSTAL": {"nameEn": "Infinity Blade", "craftCategory": "sword"},
    "2H_SCYTHE_CRYSTAL": {"nameEn": "Crystal Reaper", "craftCategory": "axe"},
    "MAIN_MACE_CRYSTAL": {"nameEn": "Dreadstorm Monarch", "craftCategory": "mace"},
    "2H_HAMMER_CRYSTAL": {"nameEn": "Truebolt Hammer", "craftCategory": "hammer"},
    "2H_KNUCKLES_CRYSTAL": {"nameEn": "Forcepulse Bracers", "craftCategory": "knuckles"},
    "2H_DUALCROSSBOW_CRYSTAL": {"nameEn": "Arclight Blasters", "craftCategory": "crossbow"},
    "OFF_SHIELD_CRYSTAL": {"nameEn": "Unbreakable Ward", "craftCategory": "shield"},
    "OFF_TORCH_CRYSTAL": {"nameEn": "Blueflame Torch", "craftCategory": "torch"},
    "OFF_TOME_CRYSTAL": {"nameEn": "Timelocked Grimoire", "craftCategory": "tome"},
    "2H_BOW_CRYSTAL": {"nameEn": "Skystrider Bow", "craftCategory": "bow"},
    "2H_DAGGERPAIR_CRYSTAL": {"nameEn": "Twin Slayers", "craftCategory": "dagger"},
    "2H_GLAIVE_CRYSTAL": {"nameEn": "Rift Glaive", "craftCategory": "spear"},
    "2H_DOUBLEBLADEDSTAFF_CRYSTAL": {"nameEn": "Phantom Twinblade", "craftCategory": "quarterstaff"},
    "2H_ARCANESTAFF_CRYSTAL": {"nameEn": "Astral Staff", "craftCategory": "arcanestaff"},
    "2H_FROSTSTAFF_CRYSTAL": {"nameEn": "Arctic Staff", "craftCategory": "froststaff"},
    "2H_HOLYSTAFF_CRYSTAL": {"nameEn": "Exalted Staff", "craftCategory": "holystaff"},
    "MAIN_FIRESTAFF_CRYSTAL": {"nameEn": "Flamewalker Staff", "craftCategory": "firestaff"},
    "MAIN_CURSEDSTAFF_CRYSTAL": {"nameEn": "Rotcaller Staff", "craftCategory": "cursestaff"},
    "MAIN_NATURESTAFF_CRYSTAL": {"nameEn": "Forgebark Staff", "craftCategory": "naturestaff"},
}

# Cajados polimorfos. SET1/2/3 não usam artefato; os outros sim.
SHAPESHIFTER_FAMILIES: dict[str, dict] = {
    "2H_SHAPESHIFTER_SET1": {"name": "Prowling Staff", "rare": "T3_ALCHEMY_RARE_PANTHER"},
    "2H_SHAPESHIFTER_SET2": {"name": "Rootbound Staff", "rare": "T3_ALCHEMY_RARE_SPRINGLYNX"},
    "2H_SHAPESHIFTER_SET3": {"name": "Primal Staff", "rare": "T3_ALCHEMY_RARE_DIREBEAR"},
    "2H_SHAPESHIFTER_MORGANA": {"name": "Bloodmoon Staff", "rare": "T3_ALCHEMY_RARE_WEREWOLF"},
    "2H_SHAPESHIFTER_HELL": {"name": "Hellspawn Staff", "rare": "T3_ALCHEMY_RARE_DEMON FOX"},
    "2H_SHAPESHIFTER_KEEPER": {"name": "Earthrune Staff", "rare": "T3_ALCHEMY_RARE_RAM"},
    "2H_SHAPESHIFTER_AVALON": {"name": "Lightcaller", "rare": "T3_ALCHEMY_RARE_DRAGON"},
    "2H_SHAPESHIFTER_CRYSTAL": {"name": "Stillgaze Staff", "rare": "T3_ALCHEMY_RARE_PANTHER"},
}

TIER_PREFIX: dict[int, str] = {
    4: "Adept's", 5: "Expert's", 6: "Master's", 7: "Grandmaster's", 8: "Elder's",
}


def crystal_render_name(name_en: str, tier: int, enchant: int = 0) -> str:
    prefix = TIER_PREFIX.get(tier, "Elder's")
    suffix = f"@{enchant}" if enchant else ""
    return f"{prefix} {name_en}{suffix}"


def tier_enchant(uid: str) -> tuple[int, int]:
    m = re.match(r"T(\d+)_.*?(@(\d+))?$", uid)
    tier = int(m.group(1)) if m else 0
    enchant = int(m.group(3)) if m and m.group(3) else 0
    return tier, enchant


def base_id(uid: str) -> str:
    return re.sub(r"^T\d+_", "", uid).replace("@0", "").split("@")[0]


def load_localized_names() -> dict[str, dict[str, str]]:
    data = json.loads(ITEMS_JSON.read_text(encoding="utf-8"))
    out: dict[str, dict[str, str]] = {}
    for item in data:
        uid = item.get("UniqueName", "")
        locs = item.get("LocalizedNames") or {}
        if uid and locs:
            out[uid] = {
                "en": locs.get("EN-US", ""),
                "pt": locs.get("PT-BR", ""),
                "es": locs.get("ES-ES", ""),
            }
    return out


def load_item_values(root: ET.Element) -> dict[str, int]:
    """Carrega item value do dump (atributo itemvalue dos elementos de item)."""
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
    """Item value de um item craftado = soma (itemValue do recurso * count)."""
    return sum(item_values.get(r["uniqueName"], 0) * r["count"] for r in resources)


def load_catalog() -> list[dict]:
    return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))


def save_catalog(catalog: list[dict]) -> None:
    CATALOG_FILE.write_text(json.dumps(catalog, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def item_value_from_xml(el: ET.Element) -> int:
    # O item value vem do enchantmentlevel 0 (craftingrequirements sem enchantment)
    # Se não houver, usa o valor do primeiro craftingrequirements.
    for cr in el.findall("craftingrequirements"):
        if "enchantmentlevel" not in cr.attrib:
            return int(cr.attrib.get("itemvalue", "0"))
    first = el.find("craftingrequirements")
    return int(first.attrib.get("itemvalue", "0")) if first is not None else 0


def build_family(
    family_key: str,
    name: str,
    slot: str,
    category: str,
    subcategory: str,
    craft_category: str,
    bonus_city: str,
    variations: list[dict],
) -> dict:
    return {
        "familyKey": family_key,
        "name": name,
        "slot": slot,
        "category": category,
        "subcategory": subcategory,
        "craftCategory": craft_category,
        "bonusCity": bonus_city,
        "kind": "equipment",
        "variations": variations,
    }


def bonus_city_from_craft_category(cat: str) -> str | None:
    # Mesmo mapeamento implícito do catalog.json existente.
    mapping = {
        "sword": "Bridgewatch", "axe": "Bridgewatch", "mace": "Bridgewatch",
        "hammer": "Bridgewatch", "knuckles": "Bridgewatch", "crossbow": "Bridgewatch",
        "shield": "Fort Sterling", "torch": "Fort Sterling", "tome": "Fort Sterling",
        "bow": "Lymhurst", "dagger": "Lymhurst", "spear": "Lymhurst",
        "quarterstaff": "Lymhurst",
        "arcanestaff": "Lymhurst", "froststaff": "Lymhurst",
        "holystaff": "Lymhurst", "firestaff": "Lymhurst",
        "cursestaff": "Lymhurst", "naturestaff": "Lymhurst",
        "shapeshifterstaff": "Lymhurst",
    }
    return mapping.get(cat)


def slot_from_base(base: str) -> str:
    if base.startswith("HEAD_"):
        return "head"
    if base.startswith("ARMOR_"):
        return "armor"
    if base.startswith("SHOES_"):
        return "shoes"
    if base.startswith("OFF_"):
        return "offhand"
    return "mainhand"


def make_variation(
    uid: str,
    item_power: int,
    focus: int,
    item_value: int,
    resources: list[dict],
) -> dict:
    tier, enchant = tier_enchant(uid)
    return {
        "uniqueName": uid,
        "tier": tier,
        "enchant": enchant,
        "itemPower": item_power,
        "focus": focus,
        "itemValue": item_value,
        "resources": resources,
    }


def parse_resources(craft_req: ET.Element) -> list[dict]:
    out: list[dict] = []
    for res in craft_req.findall("craftresource"):
        uid = res.get("uniquename", "")
        count = int(res.get("count", "0"))
        no_return = res.get("maxreturnamount") == "0"
        entry: dict = {"uniqueName": uid, "count": count}
        if no_return:
            entry["noReturn"] = True
        out.append(entry)
    return out


def find_elements(root: ET.Element, base: str) -> list[ET.Element]:
    matches: list[ET.Element] = []
    for tag in ("weapon", "offhand", "transformationweapon", "equipmentitem"):
        for el in root.iter(tag):
            uid = el.get("uniquename", "")
            if base_id(uid) == base:
                matches.append(el)
    return matches


def uid_with_enchant(uid: str, enchant: int) -> str:
    """Converte T4_MAIN_SWORD_CRYSTAL + enchant 1 em T4_MAIN_SWORD_CRYSTAL@1."""
    if enchant == 0:
        return uid
    # Remove sufixo @N existente para evitar duplicação.
    base_uid = uid.split("@")[0]
    return f"{base_uid}@{enchant}"


def build_crystal_family(
    root: ET.Element,
    base: str,
    meta: dict,
    names: dict[str, dict[str, str]],
    item_values: dict[str, int],
) -> dict | None:
    elements = find_elements(root, base)
    if not elements:
        print(f"[WARN] {base}: nenhum elemento encontrado no dump")
        return None

    variations: list[dict] = []
    for el in elements:
        uid = el.get("uniquename", "")
        tier, _ = tier_enchant(uid)
        if tier < 4 or tier > 8:
            continue
        item_power = int(el.get("itempower", "0"))
        # Cada craftingrequirements representa um enchant (0..4), em ordem. O atributo
        # enchantmentlevel do nó costuma ser 0 mesmo para enchants >0; os recursos
        # internos é que carregam LEVEL1..LEVEL4. Usamos o índice do CR como enchant.
        for enchant, cr in enumerate(el.iter("craftingrequirements")):
            focus = int(cr.get("craftingfocus", "0"))
            resources = parse_resources(cr)
            item_value = compute_item_value(resources, item_values)
            variations.append(make_variation(uid_with_enchant(uid, enchant), item_power, focus, item_value, resources))

    if not variations:
        print(f"[WARN] {base}: nenhuma variação válida")
        return None

    # Ordena por tier, depois enchant.
    variations.sort(key=lambda v: (v["tier"], v["enchant"]))

    # Nome: EN do dump se houver, senão fallback. O catalog usa EN.
    sample_uid = variations[0]["uniqueName"]
    localized = names.get(sample_uid, {})
    name = localized.get("en") or meta["nameEn"] or base

    return build_family(
        family_key=base,
        name=name,
        slot=slot_from_base(base),
        category="weapons",
        subcategory=base.lower(),
        craft_category=meta["craftCategory"],
        bonus_city=bonus_city_from_craft_category(meta["craftCategory"]),
        variations=variations,
    )


def build_shapeshifter_family(
    root: ET.Element,
    base: str,
    meta: dict,
    names: dict[str, dict[str, str]],
    item_values: dict[str, int],
) -> dict | None:
    elements = find_elements(root, base)
    if not elements:
        print(f"[WARN] {base}: nenhum elemento encontrado no dump")
        return None

    variations: list[dict] = []
    for el in elements:
        uid = el.get("uniquename", "")
        tier, _ = tier_enchant(uid)
        if tier < 4 or tier > 8:
            continue
        item_power = int(el.get("itempower", "0"))
        for enchant, cr in enumerate(el.iter("craftingrequirements")):
            focus = int(cr.get("craftingfocus", "0"))
            resources = parse_resources(cr)
            item_value = compute_item_value(resources, item_values)
            variations.append(make_variation(uid_with_enchant(uid, enchant), item_power, focus, item_value, resources))

    if not variations:
        return None

    variations.sort(key=lambda v: (v["tier"], v["enchant"]))

    sample_uid = variations[0]["uniqueName"]
    localized = names.get(sample_uid, {})
    name = localized.get("en") or meta["name"]

    return build_family(
        family_key=base,
        name=name,
        slot="mainhand",
        category="weapons",
        subcategory="shapeshifterstaff",
        craft_category="shapeshifterstaff",
        bonus_city="Lymhurst",
        variations=variations,
    )


def main() -> None:
    if not ITEMS_XML.exists():
        raise FileNotFoundError(f"Dump não encontrado: {ITEMS_XML}")

    root = ET.parse(ITEMS_XML).getroot()
    names = load_localized_names()
    item_values = load_item_values(root)
    catalog = load_catalog()

    existing_keys = {f["familyKey"] for f in catalog}
    added: list[dict] = []

    for base, meta in CRYSTAL_FAMILIES.items():
        if base in existing_keys:
            print(f"[SKIP] cristal {base} já existe")
            continue
        fam = build_crystal_family(root, base, meta, names, item_values)
        if fam:
            added.append(fam)
            print(f"[ADD] cristal {base} ({len(fam['variations'])} variações)")

    for base, meta in SHAPESHIFTER_FAMILIES.items():
        if base in existing_keys:
            print(f"[SKIP] shapeshifter {base} já existe")
            continue
        fam = build_shapeshifter_family(root, base, meta, names, item_values)
        if fam:
            added.append(fam)
            print(f"[ADD] shapeshifter {base} ({len(fam['variations'])} variações)")

    # Ordena as novas famílias por chave para estabilidade e anexa ao fim.
    added.sort(key=lambda f: f["familyKey"])
    catalog.extend(added)

    save_catalog(catalog)
    print(f"\nCatalogo atualizado: {len(catalog)} famílias ({len(added)} adicionadas).")


if __name__ == "__main__":
    main()
