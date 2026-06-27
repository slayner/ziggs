"""Pool de itens para desafios de verificação de personagem.

Cada entrada: (slot_api, item_id, qty_min, qty_max, display_name)
  slot_api  — nome do slot no JSON de equipamento do Albion (Food, Potion, Head, etc.)
  item_id   — Type do item na API do Albion
  qty_min   — quantidade mínima (inclusive) do desafio
  qty_max   — quantidade máxima (inclusive) do desafio

A lista abaixo é um placeholder — será substituída pelo usuário com itens e
ranges reais. Não use itens com qty_min == qty_max == 1 (não verificável).
"""
from __future__ import annotations

import random

# ---------------------------------------------------------------------------
# EDITE AQUI — adicione os itens de verificação reais quando tiver a lista
# ---------------------------------------------------------------------------
CHALLENGE_POOL: list[tuple[str, str, int, int, str]] = [
    # (slot,    item_id,            min, max, nome)
    ("Food",   "T1_MEAL_STEW",      30,  70,  "Beef Stew"),
    ("Potion", "T1_POTION_HEAL",    20,  60,  "Healing Potion"),
    ("Food",   "T2_MEAL_STEW",      30,  70,  "Goose Omelette"),
    ("Potion", "T2_POTION_HEAL",    20,  60,  "Major Healing Potion"),
]
# ---------------------------------------------------------------------------

CHALLENGE_SLOTS = 3  # quantos itens compõem o desafio


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
            "display_name": display_name,
        }
        for slot, item_id, qty_min, qty_max, display_name in picked
    ]


def check_equipment(equipment: dict | None, challenge: list[dict]) -> bool:
    """Verifica se um dict de equipamento (do kill event) satisfaz o desafio."""
    if not equipment:
        return False
    for item in challenge:
        slot_data = equipment.get(item["slot"])
        if not slot_data:
            return False
        if slot_data.get("Type") != item["item_id"]:
            return False
        if slot_data.get("Count") != item["quantity"]:
            return False
    return True
