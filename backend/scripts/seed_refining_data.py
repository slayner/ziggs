"""Gera `frontend/public/data/refining.json` a partir do ao-bin-dumps items.xml.

Extrai receitas de refino (normais + corações + pedra), transmutações de
recursos brutos, e metadados de especialização (bônus base por cidade/Rest).

Uso:
  cd backend && python -m scripts.seed_refining_data

Verificações canônicas (assert) travam receitas conhecidas — se o dump mudar
e uma receita canônica sumir, o gerador falha em vez de produzir dado errado.
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
OUT_FILE = Path(__file__).resolve().parents[2] / "frontend" / "public" / "data" / "refining.json"

# Famílias de refino: id do refinado -> nome da família, cidade de bônus.
FAMILIES = {
    "CLOTH":      ("fiber",  "Lymhurst"),
    "LEATHER":    ("hide",  "Martlock"),
    "METALBAR":   ("ore",   "Thetford"),
    "PLANKS":     ("wood",  "Fort Sterling"),
    "STONEBLOCK": ("stone", "Bridgewatch"),
}

# Mapeamento coração -> família (T1_FACTION_*_TOKEN_1).
HEART_TO_FAMILY = {
    "T1_FACTION_SWAMP_TOKEN_1":   "fiber",
    "T1_FACTION_STEPPE_TOKEN_1":  "hide",
    "T1_FACTION_MOUNTAIN_TOKEN_1": "ore",
    "T1_FACTION_FOREST_TOKEN_1":  "wood",
    "T1_FACTION_HIGHLAND_TOKEN_1": "stone",
}

# Fame base por tier (verificado na wiki + dump; encantamento multiplica por 2^enchant).
BASE_FAME = {2: 1.5, 3: 7.5, 4: 22.5, 5: 90, 6: 270, 7: 645, 8: 1395}

# Recurso bruto -> refinado (mesmo tier, sem encantamento no nome do refinado).
RAW_TO_REFINED = {
    "FIBER": "CLOTH", "HIDE": "LEATHER", "ORE": "METALBAR",
    "WOOD": "PLANKS", "ROCK": "STONEBLOCK",
}


def tier_from_id(item_id: str) -> int:
    m = re.match(r"T(\d+)_", item_id)
    return int(m.group(1)) if m else 0


def enchant_from_id(item_id: str) -> int:
    m = re.match(r"T\d+_.+?_LEVEL(\d+)(?:@|$)", item_id)
    return int(m.group(1)) if m else 0


def base_id(item_id: str) -> str:
    """Remove tier e enchant: T5_METALBAR_LEVEL2 -> METALBAR."""
    return re.sub(r"^T\d+_(_LEVEL\d+)?$", "", item_id) or item_id.replace("T%d_" % tier_from_id(item_id), "").replace("_LEVEL%d" % enchant_from_id(item_id), "")


def family_of(item_id: str) -> str | None:
    """Retorna o nome da família (fiber/ore/...) ou None."""
    for refined_suffix, (fam, _) in FAMILIES.items():
        if item_id.endswith(refined_suffix) or item_id.startswith(f"T{tier_from_id(item_id)}_{refined_suffix}"):
            return fam
    return None


def load_localized_names() -> dict[str, dict[str, str]]:
    """Carrega items.json para nomes localizados."""
    data = json.loads(ITEMS_JSON.read_text(encoding="utf-8"))
    out: dict[str, dict[str, str]] = {}
    for item in data:
        uid = item.get("UniqueName", "")
        locs = item.get("LocalizedNames", {})
        if uid and locs:
            out[uid] = {
                "en": locs.get("EN-US", ""),
                "pt": locs.get("PT-BR", ""),
                "es": locs.get("ES-ES", ""),
            }
    return out


def is_refined(item_id: str) -> bool:
    """É um recurso refinado (METALBAR, PLANKS, CLOTH, LEATHER, STONEBLOCK)?"""
    return any(item_id.endswith(s) or re.match(rf"T\d+_{s}(_LEVEL\d+)?$", item_id)
               for s in FAMILIES)


def is_raw_resource(item_id: str) -> bool:
    """É um recurso bruto (ORE, WOOD, FIBER, HIDE, ROCK)?"""
    return any(item_id.endswith(s) or re.match(rf"T\d+_{s}(_LEVEL\d+)?$", item_id)
               for s in RAW_TO_REFINED)


def extract_refining_recipes(root: ET.Element) -> list[dict]:
    """Extrai todas as receitas de refino do items.xml."""
    recipes: list[dict] = []
    seen_keys: set[str] = set()

    for el in root.iter("simpleitem"):
        uid = el.get("uniquename", "")
        if not is_refined(uid):
            continue

        tier = tier_from_id(uid)
        enchant = enchant_from_id(uid)
        if tier < 2 or tier > 8:
            continue

        # Família do refinado
        fam = None
        for suffix, (f, _) in FAMILIES.items():
            if uid.endswith(suffix) or re.match(rf"T\d+_{suffix}(_LEVEL\d+)?$", uid):
                fam = f
                break
        if not fam:
            continue

        item_value = el.get("itemvalue")
        item_value = int(item_value) if item_value else 0

        fame = BASE_FAME.get(tier, 0)
        if enchant > 0 and fam != "stone":
            fame = fame * (2 ** enchant)

        # Cada craftingrequirements é uma variante da receita.
        variants = []
        # Para pedra: o output é sempre flat (T4_STONEBLOCK), mas cada
        # craftingrequirements produz uma quantidade diferente (amt=1,2,4,8)
        # a partir de pedra encantada (.0,.1,.2,.3). O encantamento real
        # da receita vem do input (T4_ROCK_LEVEL2 = .2), não do output.
        # Para outros recursos: o output tem @enchant no ID e o encantamento
        # vem do próprio UID (T4_CLOTH_LEVEL2 = .2).
        stone_enchant_offset = 0
        for cr in el.findall("craftingrequirements"):
            focus = cr.get("craftingfocus", "0")
            amt = cr.get("amountcrafted", "1")
            silver = cr.get("silver", "0")
            # Transmutação tem silver != 0 e focus == 0 — não é refino.
            if int(silver or 0) > 0 and int(focus or 0) == 0:
                continue

            focus = int(focus)
            amt = int(amt)
            silver = int(silver)

            resources = []
            for res in cr.findall("craftresource"):
                res_id = res.get("uniquename", "")
                count = int(res.get("count", "1"))
                res_enchant = int(res.get("enchantmentlevel") or 0)
                # maxreturnamount ausente = returnable; "0" = noReturn.
                max_ret = res.get("maxreturnamount")
                returnable = max_ret != "0"
                is_heart = "FACTION" in res_id and "TOKEN" in res_id
                resources.append({
                    "itemId": res_id,
                    "count": count,
                    "enchant": res_enchant,
                    "returnable": returnable,
                    "isHeart": is_heart,
                })

            # Classifica a variante
            has_heart = any(r["isHeart"] for r in resources)
            kind = "heart" if has_heart else "normal"

            # Para pedra: o encantamento da receita vem do input bruto.
            # amt > 1 significa pedra encantada (.1=2, .2=4, .3=8).
            if fam == "stone" and amt > 1:
                stone_enchant = {2: 1, 4: 2, 8: 3}.get(amt, 0)
            elif fam == "stone" and amt == 1:
                stone_enchant = 0
            else:
                stone_enchant = 0

            variants.append({
                "kind": kind,
                "focus": focus,
                "outputCount": amt,
                "inputs": resources,
                "stoneEnchant": stone_enchant,
            })

        if not variants:
            continue

        # Para pedra: cada variante com outputCount diferente é uma "receita"
        # diferente (mesmo output, mas encantamento diferente do input).
        # Geramos uma receita por nível de encantamento.
        if fam == "stone":
            for v in variants:
                se = v.get("stoneEnchant", 0)
                # A receita .0 (amt=1) pode ter normal + heart; as demais só normal.
                if se == 0 and v["outputCount"] == 1:
                    # Receita flat — pode ter normal e heart
                    v.pop("stoneEnchant", None)
                    key = f"{fam}_{tier}_0"
                    if key in seen_keys:
                        existing = next(r for r in recipes if r["key"] == key)
                        existing["variants"].append(v)
                        continue
                    seen_keys.add(key)
                    recipes.append({
                        "key": key,
                        "family": fam,
                        "tier": tier,
                        "enchant": 0,
                        "outputId": uid,
                        "outputCount": 1,
                        "itemValue": item_value,
                        "baseFame": fame,
                        "variants": [v],
                    })
                else:
                    # Receita encantada (.1/.2/.3) — só normal, sem coração
                    v.pop("stoneEnchant", None)
                    key = f"{fam}_{tier}_{se}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    # Fame para pedra encantada: base × 2^enchant
                    stone_fame = BASE_FAME.get(tier, 0) * (2 ** se)
                    recipes.append({
                        "key": key,
                        "family": fam,
                        "tier": tier,
                        "enchant": se,
                        "outputId": uid,
                        "outputCount": v["outputCount"],
                        "itemValue": item_value,
                        "baseFame": stone_fame,
                        "variants": [v],
                    })
        else:
            # Não-pedra: uma receita por tier+enchant, todas as variantes juntas.
            key = f"{fam}_{tier}_{enchant}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            # Limpa stoneEnchant (só pra pedra)
            for v in variants:
                v.pop("stoneEnchant", None)
            recipes.append({
                "key": key,
                "family": fam,
                "tier": tier,
                "enchant": enchant,
                "outputId": uid,
                "outputCount": 1,
                "itemValue": item_value,
                "baseFame": fame,
                "variants": variants,
            })

    return recipes


def extract_transmutations(root: ET.Element) -> list[dict]:
    """Extrai transmutações de recursos brutos (craftingrequirements com silver)."""
    transmutations: list[dict] = []

    for el in root.iter("simpleitem"):
        uid = el.get("uniquename", "")
        if not is_raw_resource(uid):
            continue

        tier = tier_from_id(uid)
        if tier < 4 or tier > 8:
            continue

        enchant = enchant_from_id(uid)

        for cr in el.findall("craftingrequirements"):
            silver = cr.get("silver", "0")
            focus = cr.get("craftingfocus", "0")
            # Transmutação: tem silver, sem focus, sem amountcrafted.
            if int(silver or 0) == 0 or int(focus or 0) != 0:
                continue

            resources = cr.findall("craftresource")
            if len(resources) != 1:
                continue

            source_id = resources[0].get("uniquename", "")
            item_value = el.get("itemvalue")
            item_value = float(item_value) if item_value else 0.0
            transmutations.append({
                "sourceId": source_id,
                "targetId": uid,
                "silverCost": int(silver),
                "itemValue": item_value,
            })

    return transmutations


def extract_heart_conversions(root: ET.Element) -> list[dict]:
    """Extrai conversões de Shadowheart -> corações (5000 silver cada)."""
    conversions: list[dict] = []

    for el in root.iter("simpleitem"):
        uid = el.get("uniquename", "")
        if not uid.startswith("T1_FACTION_") or "TOKEN" not in uid:
            continue
        if uid == "T1_FACTION_CAERLEON_TOKEN_1":
            continue  # Shadowheart não é convertido, é a origem

        for cr in el.findall("craftingrequirements"):
            silver = cr.get("silver", "0")
            if int(silver or 0) == 0:
                continue
            for res in cr.findall("craftresource"):
                if "CAERLEON" in res.get("uniquename", ""):
                    conversions.append({
                        "shadowheartTo": uid,
                        "silverCost": int(silver),
                    })

    return conversions


def canonical_checks(recipes: list[dict], transmutations: list[dict]) -> None:
    """Verifica receitas canônicas conhecidas. Falha se não bater."""

    by_key = {r["key"]: r for r in recipes}

    # T4 METALBAR normal: 2 ore + 1 T3 bar
    r = by_key.get("ore_4_0")
    assert r is not None, "T4 METALBAR normal não encontrada"
    normal = [v for v in r["variants"] if v["kind"] == "normal"]
    assert len(normal) == 1, f"T4 METALBAR normal: esperado 1, got {len(normal)}"
    inputs = normal[0]["inputs"]
    assert len(inputs) == 2, f"T4 METALBAR normal inputs: esperado 2, got {len(inputs)}"
    assert inputs[0]["itemId"] == "T4_ORE" and inputs[0]["count"] == 2
    assert inputs[1]["itemId"] == "T3_METALBAR" and inputs[1]["count"] == 1

    # T4 METALBAR heart: 1 ore + 1 Mountainheart + 1 T3 bar
    heart = [v for v in r["variants"] if v["kind"] == "heart"]
    assert len(heart) == 1, f"T4 METALBAR heart: esperado 1, got {len(heart)}"
    hinputs = heart[0]["inputs"]
    assert len(hinputs) == 3, f"T4 METALBAR heart inputs: esperado 3, got {len(hinputs)}"
    assert hinputs[0]["itemId"] == "T4_ORE" and hinputs[0]["count"] == 1
    assert hinputs[1]["itemId"] == "T1_FACTION_MOUNTAIN_TOKEN_1"
    assert hinputs[2]["itemId"] == "T3_METALBAR"

    # T6 METALBAR normal: 4 ore + 1 T5 bar
    r6 = by_key.get("ore_6_0")
    assert r6 is not None, "T6 METALBAR normal não encontrada"
    n6 = [v for v in r6["variants"] if v["kind"] == "normal"][0]
    assert n6["inputs"][0]["count"] == 4, f"T6 ore count: {n6['inputs'][0]['count']}"

    # T4 STONEBLOCK .2: outputCount=4, 2 T4.2 rock + 4 T3 blocks
    r_stone = by_key.get("stone_4_2")
    assert r_stone is not None, "T4 STONEBLOCK .2 não encontrada"
    v2 = r_stone["variants"][0]
    assert v2["outputCount"] == 4
    assert v2["inputs"][0]["itemId"] == "T4_ROCK_LEVEL2"
    assert v2["inputs"][0]["count"] == 2
    assert v2["inputs"][1]["itemId"] == "T3_STONEBLOCK"
    assert v2["inputs"][1]["count"] == 4

    # Pedra não tem .4
    assert "stone_4_4" not in by_key, "Pedra .4 não deveria existir"

    # Coração de pedra só existe em .0
    stone_0 = by_key.get("stone_4_0")
    stone_1 = by_key.get("stone_4_1")
    assert stone_0 is not None and any(v["kind"] == "heart" for v in stone_0["variants"])
    if stone_1:
        assert not any(v["kind"] == "heart" for v in stone_1["variants"]), "Pedra .1 não deveria ter coração"

    # Transmutação T5_ORE from T4_ORE: silver=781
    t5_ore = [t for t in transmutations if t["targetId"] == "T5_ORE" and t["sourceId"] == "T4_ORE"]
    assert len(t5_ore) == 1, f"T5_ORE transmutação de T4_ORE: esperado 1, got {len(t5_ore)}"
    assert t5_ore[0]["silverCost"] == 781, f"T5_ORE silver: {t5_ore[0]['silverCost']}"

    print("OK - Checagens canonicas passaram")


def main() -> None:
    if not ITEMS_XML.exists():
        print(f"items.xml não encontrado em {ITEMS_XML}", file=sys.stderr)
        sys.exit(1)

    print(f"Lendo {ITEMS_XML}…")
    tree = ET.parse(ITEMS_XML)
    root = tree.getroot()

    print("Extraindo receitas de refino…")
    recipes = extract_refining_recipes(root)
    print(f"  {len(recipes)} receitas de refino")

    print("Extraindo transmutações…")
    transmutations = extract_transmutations(root)
    print(f"  {len(transmutations)} transmutações")

    print("Extraindo conversões de Shadowheart…")
    heart_conversions = extract_heart_conversions(root)
    print(f"  {len(heart_conversions)} conversões")

    print("Executando checagens canônicas…")
    canonical_checks(recipes, transmutations)

    # Cidades de bônus de refino (+40 pontos de especialização).
    refining_cities = {fam: city for _, (fam, city) in FAMILIES.items()}

    # Nomes localizados para itens relevantes.
    names = load_localized_names()

    output = {
        "dump_source": "ao-data/ao-bin-dumps",
        "recipes": recipes,
        "transmutations": transmutations,
        "heartConversions": heart_conversions,
        "refiningCities": refining_cities,
        "names": names,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nEscrito: {OUT_FILE}")
    print(f"  {len(recipes)} receitas, {len(transmutations)} transmutações, {len(heart_conversions)} conversões")


if __name__ == "__main__":
    main()