"""
Seed weapons, weapon_spells e gear_spells (capacete/armadura/botas/offhand)
a partir dos dados públicos do Albion Online.

Fontes:
  - https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json
  - https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/formatted/items.json

Uso:
  cd backend && python -m scripts.seed_weapons_spells
"""
from __future__ import annotations

import re
import sys
import httpx
from sqlalchemy.orm import Session

sys.path.insert(0, ".")

from app.db import engine
from app.models.catalog import Weapon, WeaponSpell


ITEMS_URL  = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"
NAMES_URL  = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/formatted/items.json"

# Nomes corretos para IDs que o _normalize_name erra
SPELL_OVERRIDES: dict[str, str] = {
    "HEALINGBEAM":    "Holy Beam",
    "GENEROUSHEAL":   "Generous Heal",
    "PULSINGHEAL":    "Pulsing Heal",
    "HOLYHOT":        "Desperate Prayer",
    "HOLYORB":        "Holy Orb",
    "RESURRECTION":   "Resurrection",
    "HOLYFLASH":      "Holy Flash",
    "SMITE_AOE":      "Smite",
    # Shapeshifter
    "SHAPE_Q_CAST":                            "Unstable Projectile",
    "SHAPE_Q_SKILLSHOT":                       "Reality Fissure",
    "SHAPE_Q_DAMAGE_AND_SHIELD":               "Adapting Matter",
    "SHAPE_Q_CONE_MELEE":                      "Pulse Shock",
    "SHAPE_W_DAMAGE_AOE":                      "Distortion",
    "SHAPE_W_AREA_PULL":                       "Positional Drift",
    "SHAPE_W_TETHERBEAM":                      "Tether Shift",
    "SHAPE_W_POLYMORPH":                       "Polymorph",
    "PASSIVE_SHAPESHIFT_ATTACK_BUFF":          "Altered Beast",
    "PASSIVE_SHAPESHIFT_Q_CAST_DAMAGE_REDUCE": "Intimidating Presence",
    "PASSIVE_SHAPESHIFT_GATHER_CHARGES":       "Innate Power",
    "PASSIVE_SHAPESHIFT_W_CAST_SPEED_BUFF":    "Rule Bender",
}

# Função invisível por ARMA (não por categoria) — taxonomia oficial:
# dps / tank / healer / support / pierce. Curada manualmente porque a mesma
# categoria do jogo (ex. "mace") mistura funções bem diferentes (Mace=tank,
# Bedrock_Mace=support, Incubus_Mace=pierce). Chave = uniquename T4 completo
# (mesmo valor salvo em Weapon.item_id). Arma nova que a Albion lançar e não
# estiver aqui cai em `_FUNC_FALLBACK` (heurística grosseira por categoria).
_FUNC_OVERRIDES: dict[str, str] = {
    "T4_2H_ARCANESTAFF": "tank",
    "T4_2H_ARCANESTAFF_CRYSTAL": "dps",
    "T4_2H_ARCANESTAFF_HELL": "support",
    "T4_2H_ARCANE_RINGPAIR_AVALON": "tank",
    "T4_2H_AXE": "dps",
    "T4_2H_AXE_AVALON": "pierce",
    "T4_2H_BOW": "dps",
    "T4_2H_BOW_AVALON": "dps",
    "T4_2H_BOW_CRYSTAL": "dps",
    "T4_2H_BOW_HELL": "dps",
    "T4_2H_BOW_KEEPER": "dps",
    "T4_2H_CLAWPAIR": "dps",
    "T4_2H_CLAYMORE": "dps",
    "T4_2H_CLAYMORE_AVALON": "dps",
    "T4_2H_CLEAVER_HELL": "pierce",
    "T4_2H_COMBATSTAFF_MORGANA": "tank",
    "T4_2H_CROSSBOW": "dps",
    "T4_2H_CROSSBOWLARGE": "dps",
    "T4_2H_CROSSBOWLARGE_MORGANA": "dps",
    "T4_2H_CROSSBOW_CANNON_AVALON": "dps",
    "T4_2H_CURSEDSTAFF": "dps",
    "T4_2H_CURSEDSTAFF_MORGANA": "pierce",
    "T4_2H_DAGGERPAIR": "dps",
    "T4_2H_DAGGERPAIR_CRYSTAL": "dps",
    "T4_2H_DAGGER_KATAR_AVALON": "dps",
    "T4_2H_DEMONICSTAFF": "tank",
    "T4_2H_DIVINESTAFF": "healer",
    "T4_2H_DOUBLEBLADEDSTAFF": "dps",
    "T4_2H_DOUBLEBLADEDSTAFF_CRYSTAL": "dps",
    "T4_2H_DUALAXE_KEEPER": "dps",
    "T4_2H_DUALCROSSBOW_CRYSTAL": "dps",
    "T4_2H_DUALCROSSBOW_HELL": "dps",
    "T4_2H_DUALHAMMER_HELL": "dps",
    "T4_2H_DUALMACE_AVALON": "support",
    "T4_2H_DUALSCIMITAR_UNDEAD": "dps",
    "T4_2H_DUALSICKLE_UNDEAD": "dps",
    "T4_2H_DUALSWORD": "dps",
    "T4_2H_ENIGMATICORB_MORGANA": "support",
    "T4_2H_ENIGMATICSTAFF": "support",
    "T4_2H_FIRESTAFF": "dps",
    "T4_2H_FIRESTAFF_HELL": "dps",
    "T4_2H_FIRE_RINGPAIR_AVALON": "dps",
    "T4_2H_FLAIL": "tank",
    "T4_2H_FROSTSTAFF": "dps",
    "T4_2H_FROSTSTAFF_CRYSTAL": "dps",
    "T4_2H_GLACIALSTAFF": "dps",
    "T4_2H_GLAIVE": "support",
    "T4_2H_GLAIVE_CRYSTAL": "dps",
    "T4_2H_HALBERD": "dps",
    "T4_2H_HALBERD_MORGANA": "dps",
    "T4_2H_HAMMER": "tank",
    "T4_2H_HAMMER_AVALON": "tank",
    "T4_2H_HAMMER_CRYSTAL": "tank",
    "T4_2H_HAMMER_UNDEAD": "tank",
    "T4_2H_HARPOON_HELL": "pierce",
    "T4_2H_HOLYSTAFF": "healer",
    "T4_2H_HOLYSTAFF_CRYSTAL": "healer",
    "T4_2H_HOLYSTAFF_HELL": "healer",
    "T4_2H_HOLYSTAFF_UNDEAD": "healer",
    "T4_2H_ICECRYSTAL_UNDEAD": "dps",
    "T4_2H_ICEGAUNTLETS_HELL": "tank",
    "T4_2H_INFERNOSTAFF": "tank",
    "T4_2H_INFERNOSTAFF_MORGANA": "dps",
    "T4_2H_IRONCLADEDSTAFF": "tank",
    "T4_2H_IRONGAUNTLETS_HELL": "dps",
    "T4_2H_KNUCKLES_AVALON": "dps",
    "T4_2H_KNUCKLES_CRYSTAL": "pierce",
    "T4_2H_KNUCKLES_HELL": "dps",
    "T4_2H_KNUCKLES_KEEPER": "dps",
    "T4_2H_KNUCKLES_MORGANA": "dps",
    "T4_2H_KNUCKLES_SET1": "dps",
    "T4_2H_KNUCKLES_SET2": "dps",
    "T4_2H_KNUCKLES_SET3": "dps",
    "T4_2H_LONGBOW": "dps",
    "T4_2H_LONGBOW_UNDEAD": "dps",
    "T4_2H_MACE": "tank",
    "T4_2H_MACE_MORGANA": "tank",
    "T4_2H_NATURESTAFF": "healer",
    "T4_2H_NATURESTAFF_HELL": "healer",
    "T4_2H_NATURESTAFF_KEEPER": "healer",
    "T4_2H_POLEHAMMER": "tank",
    "T4_2H_QUARTERSTAFF": "dps",
    "T4_2H_QUARTERSTAFF_AVALON": "tank",
    "T4_2H_RAM_KEEPER": "tank",
    "T4_2H_REPEATINGCROSSBOW_UNDEAD": "dps",
    "T4_2H_ROCKSTAFF_KEEPER": "tank",
    "T4_2H_SCYTHE_CRYSTAL": "dps",
    "T4_2H_SCYTHE_HELL": "dps",
    "T4_2H_SHAPESHIFTER_AVALON": "dps",
    "T4_2H_SHAPESHIFTER_CRYSTAL": "tank",
    "T4_2H_SHAPESHIFTER_HELL": "dps",
    "T4_2H_SHAPESHIFTER_KEEPER": "tank",
    "T4_2H_SHAPESHIFTER_MORGANA": "dps",
    "T4_2H_SHAPESHIFTER_SET1": "dps",
    "T4_2H_SHAPESHIFTER_SET2": "support",
    "T4_2H_SHAPESHIFTER_SET3": "pierce",
    "T4_2H_SKULLORB_HELL": "dps",
    "T4_2H_SPEAR": "support",
    "T4_2H_TRIDENT_UNDEAD": "support",
    "T4_2H_TWINSCYTHE_HELL": "tank",
    "T4_2H_WARBOW": "dps",
    "T4_2H_WILDSTAFF": "healer",
    "T4_MAIN_1HCROSSBOW": "dps",
    "T4_MAIN_ARCANESTAFF": "tank",
    "T4_MAIN_ARCANESTAFF_UNDEAD": "dps",
    "T4_MAIN_AXE": "dps",
    "T4_MAIN_CURSEDSTAFF": "dps",
    "T4_MAIN_CURSEDSTAFF_AVALON": "pierce",
    "T4_MAIN_CURSEDSTAFF_CRYSTAL": "support",
    "T4_MAIN_CURSEDSTAFF_UNDEAD": "support",
    "T4_MAIN_DAGGER": "dps",
    "T4_MAIN_DAGGER_HELL": "dps",
    "T4_MAIN_FIRESTAFF": "dps",
    "T4_MAIN_FIRESTAFF_CRYSTAL": "dps",
    "T4_MAIN_FIRESTAFF_KEEPER": "dps",
    "T4_MAIN_FROSTSTAFF": "tank",
    "T4_MAIN_FROSTSTAFF_AVALON": "dps",
    "T4_MAIN_FROSTSTAFF_KEEPER": "support",
    "T4_MAIN_HAMMER": "tank",
    "T4_MAIN_HOLYSTAFF": "healer",
    "T4_MAIN_HOLYSTAFF_AVALON": "healer",
    "T4_MAIN_HOLYSTAFF_MORGANA": "healer",
    "T4_MAIN_MACE": "tank",
    "T4_MAIN_MACE_CRYSTAL": "pierce",
    "T4_MAIN_MACE_HELL": "pierce",
    "T4_MAIN_NATURESTAFF": "healer",
    "T4_MAIN_NATURESTAFF_AVALON": "support",
    "T4_MAIN_NATURESTAFF_CRYSTAL": "support",
    "T4_MAIN_NATURESTAFF_KEEPER": "healer",
    "T4_MAIN_RAPIER_MORGANA": "dps",
    "T4_MAIN_ROCKMACE_KEEPER": "support",
    "T4_MAIN_SCIMITAR_MORGANA": "dps",
    "T4_MAIN_SPEAR": "dps",
    "T4_MAIN_SPEAR_KEEPER": "dps",
    "T4_MAIN_SPEAR_LANCE_AVALON": "dps",
    "T4_MAIN_SWORD": "dps",
    "T4_MAIN_SWORD_CRYSTAL": "dps",
}

# Heurística grosseira só pra arma nova ainda não curada em _FUNC_OVERRIDES.
_FUNC_FALLBACK: dict[str, str] = {
    "holystaff": "healer", "naturestaff": "healer", "arcanestaff": "support",
    "cursestaff": "dps", "firestaff": "dps", "froststaff": "dps",
    "bow": "dps", "crossbow": "dps", "warbow": "dps",
    "spear": "dps", "halberd": "dps", "pike": "dps",
    "sword": "dps", "claymore": "dps", "dualsword": "dps",
    "axe": "dps", "battleaxe": "dps", "hatchet": "dps",
    "hammer": "tank", "mace": "tank", "flail": "tank",
    "knuckles": "dps", "quarterstaff": "dps", "dagger": "dps",
    "shapeshifter": "dps",
}

_TWOHANDED_PREFIXES = {"2H_", "SHAPESHIFTER_"}


def _base_id(unique_name: str) -> str:
    """T4_MAIN_HOLYSTAFF → MAIN_HOLYSTAFF"""
    return re.sub(r"^T\d+_", "", unique_name)


def _normalize_name(spell_id: str) -> str:
    """GENEROUSHEAL → Generous Heal  |  PASSIVE_ENERGYCHANCE_HOLYSTAFF → Energy Chance"""
    s = re.sub(r"_(HOLYSTAFF|NATURESTAFF|ARCANESTAFF|CURSESTAFF|FIRESTAFF|FROSTSTAFF|"
               r"SPEAR|HAMMER|MACE|BOW|SWORD|AXE|CASTER|CHANNELER|SHOES|ARMOR|HEAD)$", "",
               spell_id, flags=re.IGNORECASE)
    s = re.sub(r"^PASSIVE_", "", s)
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    s = s.replace("_", " ")
    return s.title()


def _spell_name(spell_id: str) -> str:
    return SPELL_OVERRIDES.get(spell_id, _normalize_name(spell_id))


def _infer_fn(weapon: dict) -> str | None:
    uid = weapon.get("@uniquename", "")
    if uid in _FUNC_OVERRIDES:
        return _FUNC_OVERRIDES[uid]
    cat = weapon.get("@shopsubcategory1", "").lower()
    for key, fn in _FUNC_FALLBACK.items():
        if key in cat:
            return fn
    return None


def _slot_label(slots_val: str | None) -> str | None:
    if slots_val == "1": return "Q"
    if slots_val == "2": return "W"
    if slots_val == "3": return "E"
    return None


def _collect_spells(w: dict, weapon_idx: dict, base: str) -> list[WeaponSpell]:
    cs = w.get("craftingspelllist") or {}
    if isinstance(cs, dict) and "@reference" in cs:
        ref_w = weapon_idx.get(cs["@reference"])
        base_cs = (ref_w.get("craftingspelllist") or {}) if ref_w else {}
        base_spells: list = base_cs.get("craftspell", []) if isinstance(base_cs, dict) else []
        if isinstance(base_spells, dict): base_spells = [base_spells]
        removes = cs.get("removespell", [])
        if isinstance(removes, dict): removes = [removes]
        remove_ids = {s.get("@uniquename") for s in removes}
        own = cs.get("craftspell", [])
        if isinstance(own, dict): own = [own]
        spell_list = [s for s in base_spells if s.get("@uniquename") not in remove_ids] + own
    else:
        spell_list = cs.get("craftspell", []) if isinstance(cs, dict) else []
        if isinstance(spell_list, dict): spell_list = [spell_list]

    rows: list[WeaponSpell] = []
    slot_counters: dict[str, int] = {"Q": 0, "W": 0, "passive": 0}
    for spell in spell_list:
        raw_slot = _slot_label(spell.get("@slots"))
        if raw_slot == "E":
            continue
        slot = raw_slot if raw_slot else "passive"
        spell_id = spell["@uniquename"]
        rows.append(WeaponSpell(
            weapon_base_id=base, slot=slot,
            order_idx=slot_counters[slot],
            spell_id=spell_id,
            name=_spell_name(spell_id),
            uisprite=spell_id,
        ))
        slot_counters[slot] += 1
    return rows


def main() -> None:
    print("Buscando items.json…")
    items_data = httpx.get(ITEMS_URL, timeout=30).json()
    print("Buscando nomes localizados…")
    names_list = httpx.get(NAMES_URL, timeout=30).json()

    name_map: dict[str, str] = {
        x["UniqueName"]: (x.get("LocalizedNames") or {}).get("EN-US", x["UniqueName"])
        for x in names_list if isinstance(x, dict) and "UniqueName" in x
    }

    # ── Weapons ───────────────────────────────────────────────────────────────
    all_weapons: list[dict] = items_data["items"]["weapon"]
    combat = [
        w for w in all_weapons
        if w.get("@shopcategory") == "weapons" and w.get("@tier") == "4"
    ]
    print(f"Armas de combate (T4): {len(combat)}")

    weapon_idx: dict[str, dict] = {w["@uniquename"]: w for w in all_weapons}

    seen_bases: set[str] = set()
    weapon_rows: list[Weapon] = []
    spell_rows: list[WeaponSpell] = []

    for w in combat:
        uid  = w["@uniquename"]
        base = _base_id(uid)
        if base in seen_bases:
            continue
        seen_bases.add(base)

        display_name = name_map.get(uid, base.replace("_", " ").title())
        category = "two-hand" if any(base.startswith(p) for p in _TWOHANDED_PREFIXES) else "one-hand"

        weapon_rows.append(Weapon(
            item_id=uid, name=display_name,
            invisible_function=_infer_fn(w), category=category,
        ))

        spell_rows.extend(_collect_spells(w, weapon_idx, base))

    # ── Shapeshifter weapons (transformationweapon) ───────────────────────────
    all_shifters: list[dict] = items_data["items"]["transformationweapon"]
    if isinstance(all_shifters, dict): all_shifters = [all_shifters]
    weapon_idx.update({w["@uniquename"]: w for w in all_shifters})

    t4_shifters = [w for w in all_shifters if w.get("@tier") == "4"]
    print(f"Shapeshifter weapons (T4): {len(t4_shifters)}")

    for w in t4_shifters:
        uid  = w["@uniquename"]
        base = _base_id(uid)
        if base in seen_bases:
            continue
        seen_bases.add(base)

        weapon_rows.append(Weapon(
            item_id=uid,
            name=name_map.get(uid, base.replace("_", " ").title()),
            invisible_function=_infer_fn(w),
            category="two-hand",
        ))
        spell_rows.extend(_collect_spells(w, weapon_idx, base))

    # ── Armor gear (capacetes, armaduras, botas) ──────────────────────────────
    equip_items: list[dict] = items_data["items"]["equipmentitem"]
    equip_idx: dict[str, dict] = {i["@uniquename"]: i for i in equip_items}

    armor_cats = {"head", "armors", "shoes"}
    t4_armor = [
        i for i in equip_items
        if i.get("@shopcategory") in armor_cats and i.get("@tier") == "4"
    ]
    print(f"Itens de armadura T4 (head/armors/shoes): {len(t4_armor)}")

    armor_seen: set[str] = set()
    for item in t4_armor:
        uid  = item["@uniquename"]
        base = _base_id(uid)
        if base in armor_seen:
            continue
        armor_seen.add(base)

        # Resolve @reference para encontrar os feitiços reais
        cs = item.get("craftingspelllist", {})
        if isinstance(cs, dict) and "@reference" in cs:
            ref_item = equip_idx.get(cs["@reference"])
            if ref_item:
                cs = ref_item.get("craftingspelllist", {})

        spell_list = cs.get("craftspell", []) if isinstance(cs, dict) else []
        if isinstance(spell_list, dict):
            spell_list = [spell_list]
        if not spell_list:
            continue

        slot_counters = {"active": 0, "passive": 0}
        for spell in spell_list:
            spell_id = spell.get("@uniquename", "")
            if not spell_id:
                continue
            slot = "passive" if spell_id.startswith("PASSIVE_") else "active"
            spell_rows.append(WeaponSpell(
                weapon_base_id=base, slot=slot,
                order_idx=slot_counters[slot],
                spell_id=spell_id,
                name=_spell_name(spell_id),
                uisprite=spell_id,
            ))
            slot_counters[slot] += 1

    print(f"Inserindo {len(weapon_rows)} armas e {len(spell_rows)} spells…")
    with Session(engine) as db:
        db.query(WeaponSpell).delete()
        db.query(Weapon).delete()
        db.add_all(weapon_rows)
        db.flush()
        db.add_all(spell_rows)
        db.commit()
    print(f"Concluído. Armas: {len(weapon_rows)}, spells (weapon+gear): {len(spell_rows)}")


if __name__ == "__main__":
    main()
