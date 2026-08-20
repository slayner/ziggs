"""Desambiguação de personagens de mesmo nome no /register.

O search do Albion pode devolver um personagem DELETADO com o mesmo nome de
um ativo (ex.: "Xmonkkeyx" deletado + "xMonkkeyx" ativo na SIGHT). Sem
desambiguar, o .lower() pegava o deletado (GuildId vazio → "not_in_guild"
injusto). _pick_best_match prefere o que está na guilda/aliança configurada.

Run directly: PYTHONPATH=. python tests/test_register_name_disambiguation.py
"""
from app.api.routes.auth import _pick_best_match


def test_prefere_membro_da_guilda_configurada():
    owned = {"G_SIGHT"}
    matches = [
        {"Id": "1", "Name": "Xmonkkeyx", "GuildId": "", "AllianceId": ""},        # deletado
        {"Id": "2", "Name": "xMonkkeyx", "GuildId": "G_SIGHT", "AllianceId": "A1"},  # ativo
    ]
    best = _pick_best_match(matches, owned, "A1")
    assert best["Id"] == "2", f"devia pegar o ativo da SIGHT, pegou {best}"


def test_prefere_membro_da_alianca_se_nao_na_guilda():
    owned = {"G_OTHER"}
    matches = [
        {"Id": "1", "Name": "X", "GuildId": "", "AllianceId": ""},               # deletado
        {"Id": "2", "Name": "x", "GuildId": "G_ALLY", "AllianceId": "A1"},       # aliado
    ]
    best = _pick_best_match(matches, owned, "A1")
    assert best["Id"] == "2"


def test_prefere_com_guilda_se_nenhum_bate_com_config():
    owned = {"G_DIFFERENT"}
    matches = [
        {"Id": "1", "Name": "X", "GuildId": "", "AllianceId": ""},               # deletado
        {"Id": "2", "Name": "x", "GuildId": "G_UNKNOWN", "AllianceId": "A9"},    # ativo, guilda desconhecida
    ]
    best = _pick_best_match(matches, owned, "A1")
    assert best["Id"] == "2", "deve preferir o que tem guilda (ativo) sobre o deletado"


def test_fallback_primeiro_se_todos_sem_guilda():
    owned = set()
    matches = [{"Id": "1", "Name": "X", "GuildId": "", "AllianceId": ""}]
    best = _pick_best_match(matches, owned, None)
    assert best["Id"] == "1"


def test_choocollate_cenario_real():
    """Cenário real observado: 2 matches em americas, um sem guilda
    (deletado) e outro na SIGHT. _pick_best_match deve pegar o da SIGHT."""
    owned = {"RSEBjfVpS5Oj3O_57QRUkw"}  # SIGHT guild id
    matches = [
        {"Id": "lpimjQSaT4Odf6a6LCUHJg", "Name": "choocollate", "GuildId": "", "AllianceId": ""},
        {"Id": "EmWgJqEqS3Otq8rcf5z84Q", "Name": "Choocollate", "GuildId": "RSEBjfVpS5Oj3O_57QRUkw", "AllianceId": "HwKI13JmTTmEzkUPOAJkgw"},
    ]
    best = _pick_best_match(matches, owned, "HwKI13JmTTmEzkUPOAJkgw")
    assert best["Id"] == "EmWgJqEqS3Otq8rcf5z84Q", f"devia pegar o Choocollate da SIGHT, pegou {best}"


def test_xmonkkeyx_cenario_real():
    owned = {"RSEBjfVpS5Oj3O_57QRUkw"}
    matches = [
        {"Id": "1tPLqAncT5SYDkQiqCl1Ug", "Name": "Xmonkkeyx", "GuildId": "", "AllianceId": ""},
        {"Id": "r4Ip-X8nQBCaK2REDRsQwg", "Name": "xMonkkeyx", "GuildId": "RSEBjfVpS5Oj3O_57QRUkw", "AllianceId": "HwKI13JmTTmEzkUPOAJkgw"},
    ]
    best = _pick_best_match(matches, owned, "HwKI13JmTTmEzkUPOAJkgw")
    assert best["Id"] == "r4Ip-X8nQBCaK2REDRsQwg", f"devia pegar o xMonkkeyx da SIGHT, pegou {best}"


if __name__ == "__main__":
    test_prefere_membro_da_guilda_configurada()
    test_prefere_membro_da_alianca_se_nao_na_guilda()
    test_prefere_com_guilda_se_nenhum_bate_com_config()
    test_fallback_primeiro_se_todos_sem_guilda()
    test_choocollate_cenario_real()
    test_xmonkkeyx_cenario_real()
    print("register name disambiguation OK")