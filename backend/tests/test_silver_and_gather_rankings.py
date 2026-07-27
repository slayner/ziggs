"""Sanity checks do silver_dropped worker e dos rankings de coleta/prata.

Usa SQLite em memória apenas para conferir a agregação SQL do ranking.
"""
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes.highscores import _GATHER_KINDS, _SILVER_KIND, _silver_ranking
from app.models.base import Base
from app.models.players import AlbionPlayer, PlayerKillEvent
from app.services.silver_dropped import _has_gear


def _fake_ev(eq=None, inv=None):
    """PlayerKillEvent mockado só com victim_equipment/inventory — o que
    _has_gear lê. silver_dropped não entra no filtro (NULL no banco)."""
    class _Ev:
        def __init__(self, eq, inv):
            self.victim_equipment = eq
            self.victim_inventory = inv
    return _Ev(eq, inv)


def test_has_gear_equipped():
    eq = {"MainHand": {"Type": "T6_1H_SWORD"}, "OffHand": None}
    assert _has_gear(_fake_ev(eq=eq, inv=None)) is True


def test_has_gear_inventory_only():
    """Sem nada equipado mas com item carregado conta como gear (vai ser
    dropado na morte igual)."""
    eq = {"MainHand": None}
    inv = [{"Type": "T4_BAG", "Count": 1}]
    assert _has_gear(_fake_ev(eq=eq, inv=inv)) is True


def test_no_gear_naked_victim():
    """Vítima pelada (sem equipar, sem carregar) — não é candidata a
    precificação. Fica NULL pra sempre no banco, nunca reprocessada."""
    eq = {"MainHand": None, "OffHand": None}
    assert _has_gear(_fake_ev(eq=eq, inv=None)) is False


def test_empty_inventory_list_is_no_gear():
    """[] é diferente de None — mas ambos sem gear."""
    assert _has_gear(_fake_ev(eq=None, inv=[])) is False


def test_gather_kinds_cover_all_resources():
    """Os 8 kinds (total + 5 recursos + fishing + crafting) precisam existir —
    dropdown de coleta depende disso. Se faltar um, o ranking daquele recurso
    404. crafting_fame e gathering_fame já eram escalares em AlbionPlayer
    (preenchidos pelo upsert_player há muito tempo), então não precisaram de
    migration — só o kind no highscore."""
    assert set(_GATHER_KINDS) == {
        "gather_total", "gather_wood", "gather_hide",
        "gather_ore", "gather_rock", "gather_fiber",
        "fishing", "crafting",
    }


def test_silver_kind_is_player_kind():
    """silver_dropped é ranking de jogador (não de guilda) — precisa estar no
    PLAYER_KINDS do frontend, e a rota precisa ter o ramo dele."""
    assert _SILVER_KIND == "silver_dropped"
    # O ramo _silver_ranking existe no _compute_rankings (ver código); aqui
    # só guarda que o kind é o esperado, pra não renomear e quebrar a rota.


def test_silver_ranking_aggregates_filters_and_paginates_in_sql():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[AlbionPlayer.__table__, PlayerKillEvent.__table__])
    db = sessionmaker(bind=engine)()
    players = [
        AlbionPlayer(albion_id="a", name="Alpha", region="americas"),
        AlbionPlayer(albion_id="b", name="Beta", region="europe"),
        AlbionPlayer(albion_id="c", name="Gamma", region="americas"),
    ]
    db.add_all(players)
    db.flush()
    now = datetime.now(timezone.utc)
    for i, (player, silver, fame) in enumerate([
        (players[0], 100, 1), (players[0], 50, 1),
        (players[1], 1000, 1), (players[2], 20, 1),
        (players[2], 9999, 0),
    ]):
        db.add(PlayerKillEvent(
            region=player.region, albion_event_id=str(i), timestamp=now,
            fame=fame, victim_player_id=player.id, silver_dropped=silver,
        ))
    db.commit()

    result = _silver_ranking(db, ["americas"], "a", 1, 1)
    assert result["total"] == 2
    assert result["rows"] == [{
        "albion_id": "c", "name": "Gamma", "region": "americas",
        "guild_name": None, "alliance_name": None,
        "value": 20, "rank": 2,
    }]


def _price_silver(price_by_id, eq, inv):
    """Mesma lógica de _price_events (soma equipment + inventory*count)."""
    total = 0
    for item in (eq or {}).values():
        if item and item.get("Type"):
            total += price_by_id.get(item["Type"], 0)
    for it in (inv or []):
        if it and it.get("Type"):
            total += price_by_id.get(it["Type"], 0) * (it.get("Count") or 1)
    return total


def test_price_silver_equipment_only():
    prices = {"T6_1H_SWORD": 50000, "T4_BAG": 3000}
    eq = {"MainHand": {"Type": "T6_1H_SWORD"}, "OffHand": None}
    assert _price_silver(prices, eq, None) == 50000


def test_price_silver_inventory_multiplies_by_count():
    """Itens carregados vêm com Count — o preço é unitário * quantidade."""
    prices = {"T4_ORE": 120}
    inv = [{"Type": "T4_ORE", "Count": 50}]
    assert _price_silver(prices, None, inv) == 120 * 50


def test_price_silver_missing_price_is_zero():
    """Item sem cotação no cache de preço contribui 0, não quebra a soma."""
    prices = {}
    eq = {"MainHand": {"Type": "T8_2H_BOW@3"}}
    assert _price_silver(prices, eq, None) == 0


def test_price_silver_combined_equipment_and_inventory():
    prices = {"T6_HEAD_PLATE_SET1": 40000, "T4_HIDE": 80}
    eq = {"Head": {"Type": "T6_HEAD_PLATE_SET1"}}
    inv = [{"Type": "T4_HIDE", "Count": 100}]
    assert _price_silver(prices, eq, inv) == 40000 + 8000


if __name__ == "__main__":
    test_has_gear_equipped()
    test_has_gear_inventory_only()
    test_no_gear_naked_victim()
    test_empty_inventory_list_is_no_gear()
    test_gather_kinds_cover_all_resources()
    test_silver_kind_is_player_kind()
    test_silver_ranking_aggregates_filters_and_paginates_in_sql()
    test_price_silver_equipment_only()
    test_price_silver_inventory_multiplies_by_count()
    test_price_silver_missing_price_is_zero()
    test_price_silver_combined_equipment_and_inventory()
    print("ok")
