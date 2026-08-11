"""Cadeia de presunção de preço (qualidade -> equivalência -> craft cost).

Cobre os helpers síncronos de `app.services.prices`:
- `_equivalent_tier_chain` — gera T±k/E∓k dentro de T∈[4,8] e E∈[0,4]
- `_craft_cost_estimate` — soma materiais × (1 − RRR bonus city); artefato sem
  preço é ignorado; material principal sem preço aborta
- `_RRR_BONUS_CITY_FACTOR` — igual a 1 − 33/133
- `_parse_tier_enchant` — extrai tier/enchant de UniqueName

O fluxo async `get_battle_prices_with_presumption` depende de Postgres
upsert paths e fica fora do auto-teste aqui — exercitado em staging pela
worker silver_dropped (logs `'bases=...'`).
"""
from app.services.prices import (
    _RRR_BONUS_CITY_FACTOR,
    _craft_cost_estimate,
    _equivalent_tier_chain,
    _parse_tier_enchant,
    item_base_id,
    journal_empty_fallback,
)


def test_parse_tier_enchant_handles_flat_and_enchanted():
    assert _parse_tier_enchant("T7_HEAD_PLATE_SET3@4") == (7, 4)
    assert _parse_tier_enchant("T4_METALBAR") == (4, 0)
    assert _parse_tier_enchant("T8_2H_ARCANESTAFF@1") == (8, 1)
    assert _parse_tier_enchant("") == (0, 0)


def test_equivalent_chain_higher_tier_first():
    """7.4 -> primeiro equivalente é 8.3 (T+1, E-1). Regra do usuário."""
    chain = _equivalent_tier_chain("T7_HEAD_PLATE_SET3@4")
    assert chain
    assert chain[0] == "T8_HEAD_PLATE_SET3@3"
    # Não inclui o próprio uid.
    assert "T7_HEAD_PLATE_SET3@4" not in chain


def test_equivalent_chain_caps_at_t8_e4():
    """8.4 é ponta — T+1 estoura T8, E-1 desce a 3 (válido, mas dst não é
    equivalente pois base só troca tier, mantém enchant). Verifica limites."""
    chain = _equivalent_tier_chain("T8_HEAD_PLATE_SET3@4")
    # T+1/E-1 -> T9 (estoura) -> inválido; T-1/E+1 -> E5 (estoura) -> inválido.
    # T+2/E-2, T-2/E+2 — todos estouram. Lista vazia.
    assert chain == [], f"4×4 (T8,E4) não deveria ter equivalente, veio {chain}"


def test_equivalent_chain_explores_lower_tiers():
    """6.0 -> 5.1 (T-1, E+1) e 4.2 (T-2, E+2), nessa ordem (tier maior 1º)."""
    chain = _equivalent_tier_chain("T6_HEAD_PLATE_SET3")
    assert "T5_HEAD_PLATE_SET3@1" in chain
    assert "T4_HEAD_PLATE_SET3@2" in chain
    # 7.-1 (T+1, E-1 = T7/E-1) é inválido (E<0), não entra.
    assert not any(c.startswith("T7_") and c.endswith("@-1") for c in chain)
    # T+1 (=7) com E-1 (=−1) inválido, T-1 (=5) com E+1 (=1): primeiro na chain.
    assert chain[0] == "T5_HEAD_PLATE_SET3@1"


def test_rrr_factor_value():
    """Fator = 1 − 33/133. Ponytail: erros de float abaixo de 1e-9 ok."""
    assert abs(_RRR_BONUS_CITY_FACTOR - (1 - 33 / 133)) < 1e-9
    # Sanity: ≈ 0.7519 (75.19% do custo de materiais).
    assert 0.74 < _RRR_BONUS_CITY_FACTOR < 0.76


def test_craft_cost_basic_materials():
    """8 metalbar @ 1000 = 8000. Presumo = 8000 × RRR_factor ≈ 6015."""
    var = {"resources": [{"uniqueName": "T4_METALBAR", "count": 8}]}
    expected = round(8000 * _RRR_BONUS_CITY_FACTOR)
    assert _craft_cost_estimate(var, {"T4_METALBAR": 1000}) == expected


def test_craft_cost_artifact_without_price_is_ignored():
    """Regra do usuário: artefato sem preço -> ignora, soma só materiais."""
    var = {"resources": [
        {"uniqueName": "T4_METALBAR", "count": 8},
        {"uniqueName": "T4_ARTEFACT_X", "count": 1, "noReturn": True},
    ]}
    materials_only = round(8000 * _RRR_BONUS_CITY_FACTOR)
    assert _craft_cost_estimate(var, {"T4_METALBAR": 1000}) == materials_only


def test_craft_cost_artifact_with_price_sums_full():
    """Artefato NÃO sofre RRR — valor cheio entra na soma."""
    var = {"resources": [
        {"uniqueName": "T4_METALBAR", "count": 8},
        {"uniqueName": "T4_ARTEFACT_X", "count": 1, "noReturn": True},
    ]}
    materials_only = round(8000 * _RRR_BONUS_CITY_FACTOR)
    full = _craft_cost_estimate(var, {"T4_METALBAR": 1000, "T4_ARTEFACT_X": 50000})
    assert full == materials_only + 50000


def test_craft_cost_missing_main_material_aborts():
    """Material principal (não-artefato) sem preço -> presume aborta (devolve 0).
    Não inventa número — regra do usuário."""
    var = {"resources": [{"uniqueName": "T4_METALBAR", "count": 8}]}
    assert _craft_cost_estimate(var, {}) == 0
    assert _craft_cost_estimate(var, {"T4_METALBAR": 0}) == 0


def test_item_base_id_compat():
    """`item_base_id` continua sendo o helper pra gerar equivalentes."""
    assert item_base_id("T7_HEAD_PLATE_SET3@4") == "HEAD_PLATE_SET3"
    assert item_base_id("T4_METALBAR") == "METALBAR"


def test_journal_empty_fallback_maps_full_and_bare():
    """Jornal não-EMPTY (bare ou _FULL) -> versão _EMPTY correspondente.
    Regra do usuário: jornal não-vazio sem preço usa preço do EMPTY."""
    assert journal_empty_fallback("T7_JOURNAL_HIDE") == "T7_JOURNAL_HIDE_EMPTY"
    assert journal_empty_fallback("T7_JOURNAL_HIDE_FULL") == "T7_JOURNAL_HIDE_EMPTY"
    assert journal_empty_fallback("T6_JOURNAL_TROPHY_WOOD") == "T6_JOURNAL_TROPHY_WOOD_EMPTY"
    assert journal_empty_fallback("T6_JOURNAL_TROPHY_WOOD_FULL") == "T6_JOURNAL_TROPHY_WOOD_EMPTY"


def test_journal_empty_fallback_returns_none_for_already_empty():
    """Já é _EMPTY -> retorna None (não há fallback a aplicar)."""
    assert journal_empty_fallback("T7_JOURNAL_HIDE_EMPTY") is None
    # Não-journal -> None.
    assert journal_empty_fallback("T7_HEAD_PLATE_SET3@4") is None
    assert journal_empty_fallback("T4_METALBAR") is None
    assert journal_empty_fallback("") is None


if __name__ == "__main__":
    test_parse_tier_enchant_handles_flat_and_enchanted()
    test_equivalent_chain_higher_tier_first()
    test_equivalent_chain_caps_at_t8_e4()
    test_equivalent_chain_explores_lower_tiers()
    test_rrr_factor_value()
    test_craft_cost_basic_materials()
    test_craft_cost_artifact_without_price_is_ignored()
    test_craft_cost_artifact_with_price_sums_full()
    test_craft_cost_missing_main_material_aborts()
    test_item_base_id_compat()
    test_journal_empty_fallback_maps_full_and_bare()
    test_journal_empty_fallback_returns_none_for_already_empty()
    print("ok")