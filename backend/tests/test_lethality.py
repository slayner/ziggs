from app.services.lethality import expected_kill_fame, is_likely_lethal


RAW_GEAR = {
    "MainHand": {"Type": "T4_MAIN_SWORD"},
    "Armor": {"Type": "T4_ARMOR_PLATE_SET1@1"},
}


def test_expected_fame_accepts_raw_and_simplified_equipment():
    expected = 100 * (2 ** 4 + 2 ** 5)
    assert expected_kill_fame(RAW_GEAR) == expected
    assert expected_kill_fame({"weapon": "T4_MAIN_SWORD", "armor": "T4_ARMOR_PLATE_SET1@1"}) == expected


def test_large_group_proves_not_orange():
    assert is_likely_lethal(1, None, 4) is True


def test_small_or_unknown_group_requires_expected_fame():
    expected = expected_kill_fame(RAW_GEAR)
    assert is_likely_lethal(expected, RAW_GEAR, 3) is True
    assert is_likely_lethal(expected - 1, RAW_GEAR, 3) is False
    assert is_likely_lethal(expected - 1, RAW_GEAR, None) is False


def test_zero_fame_and_unknown_gear_are_rejected():
    assert is_likely_lethal(0, RAW_GEAR, 10) is False
    assert is_likely_lethal(1_000_000, None, 3) is False


if __name__ == "__main__":
    test_expected_fame_accepts_raw_and_simplified_equipment()
    test_large_group_proves_not_orange()
    test_small_or_unknown_group_requires_expected_fame()
    test_zero_fame_and_unknown_gear_are_rejected()
    print("ok")
