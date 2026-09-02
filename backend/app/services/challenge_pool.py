"""Pool de itens para desafios de verificação de personagem.

Cada entrada: (slot, item_id, qty_min, qty_max)
  slot      — categoria do item, só pra agrupar/exibir no frontend; NÃO entra
              na verificação (o match é por item_id contra a bag, ver check_inventory)
  item_id   — UniqueName do item nos dados do jogo (ao-bin-dumps)
  qty_min   — quantidade mínima (inclusive) do desafio
  qty_max   — quantidade máxima (inclusive) do desafio

Nomes localizados (pt/en/es) ficam no frontend (data/challenge-items.ts),
não aqui — o backend não sabe o idioma do usuário e o nome não deve
congelar no momento em que o challenge é gerado e salvo no banco.
"""
from __future__ import annotations

import random

CHALLENGE_POOL: list[tuple[str, str, int, int]] = [
    # Seed
    ("Seed", "T3_FARM_WHEAT_SEED", 3, 15),
    ("Seed", "T1_FARM_CARROT_SEED", 3, 20),
    ("Seed", "T2_FARM_BEAN_SEED", 3, 20),
    ("Seed", "T4_FARM_TURNIP_SEED", 1, 6),
    ("Seed", "T5_FARM_CABBAGE_SEED", 1, 7),
    ("Seed", "T6_FARM_POTATO_SEED", 1, 5),
    ("Seed", "T2_FARM_AGARIC_SEED", 1, 6),
    ("Seed", "T3_FARM_COMFREY_SEED", 1, 4),
    ("Seed", "T4_FARM_BURDOCK_SEED", 1, 5),
    ("Seed", "T5_FARM_TEASEL_SEED", 1, 4),
    # Calf
    ("Calf", "T3_FARM_OX_BABY", 1, 7),
    ("Calf", "T4_FARM_OX_BABY", 1, 5),
    ("Calf", "T5_FARM_OX_BABY", 1, 3),
    ("Calf", "T3_FARM_HORSE_BABY", 1, 5),
    ("Calf", "T4_FARM_HORSE_BABY", 1, 3),
    ("Calf", "T5_FARM_HORSE_BABY", 1, 2),
    ("Calf", "T4_FARM_GIANTSTAG_BABY", 1, 4),
    ("Calf", "T5_FARM_MOABIRD_FW_BRIDGEWATCH_BABY", 1, 2),
    # Mount
    ("Mount", "T5_MOUNT_MOABIRD_FW_BRIDGEWATCH", 1, 2),
    ("Mount", "T3_MOUNT_OX", 1, 15),
    ("Mount", "T4_MOUNT_OX", 1, 10),
    ("Mount", "T5_MOUNT_OX", 1, 6),
    ("Mount", "T6_MOUNT_OX", 1, 3),
    ("Mount", "T3_MOUNT_HORSE", 1, 15),
    ("Mount", "T4_MOUNT_HORSE", 1, 10),
    ("Mount", "T5_MOUNT_HORSE", 1, 6),
    ("Mount", "T6_MOUNT_HORSE", 1, 3),
    # Toy
    ("Toy", "TREASURE_KNOWLEDGE_RARITY1", 1, 80),
    ("Toy", "TREASURE_KNOWLEDGE_RARITY2", 1, 45),
    ("Toy", "TREASURE_KNOWLEDGE_RARITY3", 1, 10),
    ("Toy", "TREASURE_SILVERWARE_RARITY1", 1, 100),
    ("Toy", "TREASURE_SILVERWARE_RARITY2", 1, 45),
    ("Toy", "TREASURE_SILVERWARE_RARITY3", 1, 10),
    ("Toy", "TREASURE_DECORATIVE_RARITY1", 1, 100),
    ("Toy", "TREASURE_DECORATIVE_RARITY2", 1, 45),
    ("Toy", "TREASURE_DECORATIVE_RARITY3", 1, 10),
    ("Toy", "TREASURE_CEREMONIAL_RARITY1", 1, 100),
    ("Toy", "TREASURE_CEREMONIAL_RARITY2", 1, 45),
    ("Toy", "TREASURE_CEREMONIAL_RARITY3", 1, 10),
    ("Toy", "TREASURE_TRIBAL_RARITY1", 1, 100),
    ("Toy", "TREASURE_TRIBAL_RARITY2", 1, 45),
    ("Toy", "TREASURE_TRIBAL_RARITY3", 1, 15),
    ("Toy", "TREASURE_RITUAL_RARITY1", 1, 100),
    ("Toy", "TREASURE_RITUAL_RARITY2", 1, 45),
    ("Toy", "TREASURE_RITUAL_RARITY3", 1, 10),
    ("Toy", "TREASURE_AVALON_RARITY1", 1, 4),
    # Fish
    ("Fish", "T4_FISH_FRESHWATER_ALL_COMMON", 1, 15),
    ("Fish", "T3_FISH_FRESHWATER_ALL_COMMON", 1, 25),
    ("Fish", "T5_FISH_FRESHWATER_ALL_COMMON", 1, 20),
    ("Fish", "T7_FISH_FRESHWATER_ALL_COMMON", 1, 15),
    ("Fish", "T8_FISH_FRESHWATER_ALL_COMMON", 1, 7),
    ("Fish", "T4_FISH_SALTWATER_ALL_COMMON", 1, 20),
    ("Fish", "T5_FISH_SALTWATER_ALL_COMMON", 1, 15),
    ("Fish", "T6_FISH_SALTWATER_ALL_COMMON", 1, 10),
    ("Fish", "T7_FISH_SALTWATER_ALL_COMMON", 1, 5),
    ("Fish", "T3_FISH_FRESHWATER_MOUNTAIN_RARE", 1, 6),
    ("Fish", "T5_FISH_FRESHWATER_MOUNTAIN_RARE", 1, 5),
    ("Fish", "T7_FISH_FRESHWATER_MOUNTAIN_RARE", 1, 5),
    ("Fish", "T3_FISH_FRESHWATER_SWAMP_RARE", 1, 20),
    ("Fish", "T5_FISH_FRESHWATER_SWAMP_RARE", 1, 15),
    ("Fish", "T7_FISH_FRESHWATER_SWAMP_RARE", 1, 12),
    ("Fish", "T3_FISH_SALTWATER_ALL_RARE", 1, 11),
    # Cook
    ("Cook", "T1_SEAWEED", 1, 250),
    ("Cook", "T1_FISHCHOPS", 1, 300),
    ("Cook", "T1_FISHSAUCE_LEVEL1", 1, 35),
    ("Cook", "T1_FISHSAUCE_LEVEL2", 1, 9),
    ("Cook", "T1_FISHSAUCE_LEVEL3", 1, 3),
    # Token
    ("Token", "T5_MOUNTUPGRADE_HORSE_CURSE", 1, 100),
    ("Token", "T5_MOUNTUPGRADE_HORSE_MORGANA", 1, 100),
    ("Token", "QUESTITEM_TOKEN_AVALON", 1, 50),
    ("Token", "T4_LOOTBAG_EXPEDITION_ROYAL_SIGIL", 1, 3),
    ("Token", "QUESTITEM_TOKEN_ROYAL_T4", 1, 2),
    ("Token", "QUESTITEM_TOKEN_ROYAL_T6", 1, 3),
    ("Token", "QUESTITEM_TOKEN_ROYAL_T5", 1, 2),
    ("Token", "UNIQUE_GVGTOKEN_GENERIC", 1, 6),
    # Ref
    ("Ref", "T4_PLANKS", 1, 290),
    ("Ref", "T4_PLANKS_LEVEL1@1", 1, 250),
    ("Ref", "T4_PLANKS_LEVEL2@2", 1, 210),
    ("Ref", "T4_PLANKS_LEVEL3@3", 1, 25),
    ("Ref", "T5_PLANKS_LEVEL3@3", 1, 10),
    ("Ref", "T5_PLANKS_LEVEL2@2", 1, 40),
    ("Ref", "T5_PLANKS_LEVEL1@1", 1, 85),
    ("Ref", "T5_PLANKS", 1, 150),
    ("Ref", "T3_STONEBLOCK", 1, 300),
    ("Ref", "T4_STONEBLOCK", 1, 200),
    ("Ref", "T5_STONEBLOCK", 1, 100),
    ("Ref", "T6_STONEBLOCK", 1, 85),
    ("Ref", "T3_METALBAR", 1, 300),
    ("Ref", "T4_METALBAR", 1, 200),
    ("Ref", "T5_METALBAR", 1, 100),
    ("Ref", "T6_METALBAR", 1, 34),
    ("Ref", "T7_METALBAR", 1, 8),
    ("Ref", "T8_METALBAR", 1, 4),
    ("Ref", "T3_CLOTH", 1, 300),
    ("Ref", "T4_CLOTH", 1, 200),
    ("Ref", "T5_CLOTH", 1, 100),
    ("Ref", "T6_CLOTH", 1, 85),
    ("Ref", "T7_CLOTH", 1, 32),
    # Res
    ("Res", "T4_WOOD", 1, 300),
    ("Res", "T5_WOOD", 1, 200),
    ("Res", "T6_WOOD", 1, 100),
    ("Res", "T7_WOOD", 1, 50),
    # Food
    ("Food", "T3_MEAL_SOUP", 1, 35),
    ("Food", "T5_MEAL_SOUP", 1, 15),
    ("Food", "T1_MEAL_SOUP", 1, 30),
    ("Food", "T3_MEAL_SOUP_FISH", 1, 10),
    ("Food", "T5_MEAL_SOUP_FISH", 1, 4),
    ("Food", "T1_MEAL_SOUP_FISH", 1, 25),
    ("Food", "T2_MEAL_SALAD", 1, 200),
    ("Food", "T2_MEAL_SALAD_FISH", 1, 80),
    ("Food", "T4_MEAL_SALAD_FISH", 1, 10),
    ("Food", "T6_MEAL_SALAD_FISH", 1, 3),
    ("Food", "T5_MEAL_PIE", 1, 50),
    ("Food", "T3_MEAL_PIE", 1, 45),
    ("Food", "T3_MEAL_OMELETTE", 1, 200),
    ("Food", "T5_MEAL_OMELETTE", 1, 100),
    ("Food", "T5_MEAL_OMELETTE_FISH", 1, 15),
    ("Food", "T3_MEAL_OMELETTE_FISH", 1, 10),
    ("Food", "T4_MEAL_STEW", 1, 300),
    ("Food", "T4_MEAL_STEW_FISH", 1, 50),
    # Pot
    ("Pot", "T2_POTION_HEAL", 1, 150),
    ("Pot", "T4_POTION_HEAL", 1, 50),
    ("Pot", "T2_POTION_ENERGY", 1, 150),
    ("Pot", "T4_POTION_ENERGY", 1, 50),
    ("Pot", "T6_POTION_ENERGY", 1, 35),
    # Tool
    ("Tool", "T3_2H_TOOL_HAMMER", 1, 25),
    ("Tool", "T4_2H_TOOL_HAMMER", 1, 20),
    ("Tool", "T5_2H_TOOL_HAMMER", 1, 10),
    ("Tool", "T3_2H_TOOL_SICKLE", 1, 25),
    ("Tool", "T4_2H_TOOL_SICKLE", 1, 20),
    ("Tool", "T5_2H_TOOL_SICKLE", 1, 10),
    ("Tool", "T3_2H_TOOL_FISHINGROD", 1, 25),
    ("Tool", "T4_2H_TOOL_FISHINGROD", 1, 20),
    ("Tool", "T5_2H_TOOL_FISHINGROD", 1, 10),
    # Gather
    ("Gather", "T4_HEAD_GATHERER_FISH", 1, 10),
    ("Gather", "T5_HEAD_GATHERER_FISH", 1, 5),
    ("Gather", "T6_HEAD_GATHERER_FISH", 1, 3),
    ("Gather", "T4_ARMOR_GATHERER_FISH", 1, 10),
    ("Gather", "T5_ARMOR_GATHERER_FISH", 1, 5),
    ("Gather", "T6_ARMOR_GATHERER_FISH", 1, 3),
    ("Gather", "T4_SHOES_GATHERER_FISH", 1, 10),
    ("Gather", "T5_SHOES_GATHERER_FISH", 1, 5),
    ("Gather", "T6_SHOES_GATHERER_FISH", 1, 3),
    ("Gather", "T4_ARMOR_GATHERER_ROCK", 1, 10),
    ("Gather", "T5_ARMOR_GATHERER_ROCK", 1, 5),
    ("Gather", "T6_ARMOR_GATHERER_ROCK", 1, 3),
    ("Gather", "T4_SHOES_GATHERER_ROCK", 1, 10),
    ("Gather", "T5_SHOES_GATHERER_ROCK", 1, 5),
    ("Gather", "T6_SHOES_GATHERER_ROCK", 1, 3),
    ("Gather", "T4_HEAD_GATHERER_ROCK", 1, 10),
    ("Gather", "T5_HEAD_GATHERER_ROCK", 1, 5),
    ("Gather", "T6_HEAD_GATHERER_ROCK", 1, 3),
    # Bait
    ("Bait", "T1_FISHINGBAIT", 1, 20),
    ("Bait", "T3_FISHINGBAIT", 1, 10),
    ("Bait", "T5_FISHINGBAIT", 1, 5),
    # Worm
    ("Worm", "T1_WORM", 1, 200),
    # Veg
    ("Veg", "T1_CARROT", 1, 150),
    ("Veg", "T2_BEAN", 1, 86),
    ("Veg", "T3_WHEAT", 1, 50),
    ("Veg", "T4_TURNIP", 1, 65),
    ("Veg", "T5_CABBAGE", 1, 60),
    ("Veg", "T6_POTATO", 1, 50),
    ("Veg", "T7_CORN", 1, 35),
    ("Veg", "T8_PUMPKIN", 1, 25),
    # Herb
    ("Herb", "T3_COMFREY", 1, 40),
    ("Herb", "T4_BURDOCK", 1, 50),
    ("Herb", "T5_TEASEL", 1, 50),
    ("Herb", "T6_FOXGLOVE", 1, 50),
    ("Herb", "T7_MULLEIN", 1, 50),
    ("Herb", "T8_YARROW", 1, 50),
    # Litt
    ("Litt", "T4_FARM_GOAT_BABY", 1, 8),
    ("Litt", "T6_FARM_SHEEP_BABY", 1, 5),
    ("Litt", "T5_FARM_GOOSE_BABY", 1, 10),
    # Garden
    ("Garden", "T3_FARM_CHICKEN_GROWN", 1, 6),
    ("Garden", "T5_FARM_GOOSE_GROWN", 1, 4),
    ("Garden", "T6_FARM_SHEEP_GROWN", 1, 4),
    ("Garden", "T4_FARM_GOAT_GROWN", 1, 5),
    ("Garden", "T8_FARM_COW_GROWN", 1, 4),
    # Prod
    ("Prod", "T3_EGG", 1, 500),
    ("Prod", "T5_EGG", 1, 300),
    ("Prod", "T4_MILK", 1, 100),
    ("Prod", "T6_MILK", 1, 80),
    ("Prod", "T8_MILK", 1, 60),
    # Raw
    ("Raw", "T3_MEAT", 1, 40),
    ("Raw", "T4_MEAT", 1, 40),
    ("Raw", "T5_MEAT", 1, 40),
    ("Raw", "T6_MEAT", 1, 30),
    ("Raw", "T7_MEAT", 1, 20),
    ("Raw", "T8_MEAT", 1, 15),
    # Butter
    ("Butter", "T4_BUTTER", 1, 150),
    ("Butter", "T6_BUTTER", 1, 100),
    ("Butter", "T8_BUTTER", 1, 60),
    # Alcohol
    ("Alcohol", "T6_ALCOHOL", 1, 45),
    ("Alcohol", "T7_ALCOHOL", 1, 45),
    ("Alcohol", "T8_ALCOHOL", 1, 50),
    # Bread
    ("Bread", "T4_BREAD", 1, 15),
    # Flour
    ("Flour", "T3_FLOUR", 1, 10),
    # Tome
    ("Tome", "T4_SKILLBOOK_STANDARD", 1, 6),
    # Rune
    ("Rune", "T4_RUNE", 1, 650),
    ("Rune", "T5_RUNE", 1, 450),
    ("Rune", "T6_RUNE", 1, 250),
    ("Rune", "T7_RUNE", 1, 50),
    ("Rune", "T8_RUNE", 1, 10),
]

CHALLENGE_SLOTS = 5  # quantos itens compõem o desafio


def generate_challenge() -> list[dict]:
    """Retorna uma lista de itens com quantidades aleatórias dentro dos ranges."""
    pool = list(CHALLENGE_POOL)
    random.shuffle(pool)
    picked = pool[:CHALLENGE_SLOTS]
    return [
        {
            "slot": slot,
            "item_id": item_id,
            "quantity": random.randint(qty_min, qty_max),
        }
        for slot, item_id, qty_min, qty_max in picked
    ]


def check_inventory(inventory: list[dict] | None, challenge: list[dict]) -> bool:
    """True se a BAG da morte contém EXATAMENTE os itens do desafio.

    `inventory` é a lista crua `Victim.Inventory` do kill event (slots vazios
    vêm como None). O match é exclusivo: os item_ids e quantidades (somadas
    entre stacks do mesmo item) têm que bater com o desafio — nada a mais nem
    a menos. Itens equipados (Equipment) ficam fora: só a bag conta.

    `Quality` (Normal/Boa/Excepcional/...) é ignorada de propósito: o desafio
    nunca exige uma qualidade específica, só o item_id e a quantidade total.
    Duas stacks do mesmo item em qualidades diferentes somam como um só.
    """
    if inventory is None:
        return False
    have: dict[str, int] = {}
    for entry in inventory:
        if not entry:  # slot vazio
            continue
        item_id = entry.get("Type")
        if not item_id:
            continue
        have[item_id] = have.get(item_id, 0) + (entry.get("Count") or 0)
    want = {item["item_id"]: item["quantity"] for item in challenge}
    return have == want


if __name__ == "__main__":  # self-check rápido da regra de match exclusivo
    chal = [
        {"slot": "Worm", "item_id": "T1_WORM", "quantity": 200},
        {"slot": "Rune", "item_id": "T4_RUNE", "quantity": 5},
    ]
    inv = lambda *items: [{"Type": t, "Count": c} for t, c in items]
    assert check_inventory(inv(("T1_WORM", 200), ("T4_RUNE", 5)), chal) is True
    # stacks somam pro mesmo item
    assert check_inventory(inv(("T1_WORM", 150), ("T1_WORM", 50), ("T4_RUNE", 5)), chal) is True
    # stacks de QUALIDADES diferentes do mesmo item também somam — quality não
    # entra no match, só Type (item_id) + quantidade total
    assert check_inventory(
        [{"Type": "T1_WORM", "Count": 120, "Quality": 1},
         {"Type": "T1_WORM", "Count": 80, "Quality": 3},
         {"Type": "T4_RUNE", "Count": 5, "Quality": 4}],
        chal,
    ) is True
    # item a mais reprova
    assert check_inventory(inv(("T1_WORM", 200), ("T4_RUNE", 5), ("T8_RUNE", 1)), chal) is False
    # quantidade errada reprova
    assert check_inventory(inv(("T1_WORM", 199), ("T4_RUNE", 5)), chal) is False
    # item faltando reprova
    assert check_inventory(inv(("T1_WORM", 200)), chal) is False
    # bag vazia / nula reprova
    assert check_inventory([], chal) is False
    assert check_inventory(None, chal) is False
    # slots None são ignorados
    assert check_inventory([None, *inv(("T1_WORM", 200), ("T4_RUNE", 5)), None], chal) is True
    print("check_inventory OK")
