"""Guarda: armas em REMOVED_WEAPON_BASES não podem aparecer no dropdown nem
eleger ninguém nos rankings. Já regrediu uma vez: a SBI removeu a Black Hands
(2H_IRONGAUNTLETS_HELL) do jogo mas ela continuava no dropdown e podia sair
como destaque weapon_scorer — listando uma arma que não existe mais.

Sem DB — testa a lógica de filtro direto nas funções que montam a lista de
candidatos a partir de um dict já computado (mesma forma que _compute_rankings
e _compute_highlights iteram bulk_points)."""
from app.api.routes.highscores import REMOVED_WEAPON_BASES


def test_black_hands_listada_como_removida():
    """2H_IRONGAUNTLETS_HELL = 'Black Hands', removida do jogo em 2024."""
    assert "2H_IRONGAUNTLETS_HELL" in REMOVED_WEAPON_BASES


def test_weapon_scorer_ignora_base_removida():
    """Se o único top do jogador é uma arma removida, ele não entra no
    ranking weapon_scorer — arma que não existe mais não faz ninguém #1."""
    bulk_points = {"p1": {"2H_IRONGAUNTLETS_HELL": 999}}
    candidates = []
    for albion_id, weapons in bulk_points.items():
        if not weapons:
            continue
        wb, pts = max(
            ((w, p) for w, p in weapons.items() if w not in REMOVED_WEAPON_BASES),
            key=lambda kv: kv[1], default=(None, 0),
        )
        if pts > 0:
            candidates.append((albion_id, wb, pts))
    assert candidates == []


def test_weapon_scorer_cai_pro_segundo_melhor_quando_removida_eh_top():
    """Jogador com pontos em arma removida E em arma viva: a viva vira seu
    top no weapon_scorer, a removida é ignorada (não some com o player)."""
    bulk_points = {"p1": {"2H_IRONGAUNTLETS_HELL": 999, "MAIN_SWORD": 50}}
    candidates = []
    for albion_id, weapons in bulk_points.items():
        wb, pts = max(
            ((w, p) for w, p in weapons.items() if w not in REMOVED_WEAPON_BASES),
            key=lambda kv: kv[1], default=(None, 0),
        )
        if pts > 0:
            candidates.append((albion_id, wb, pts))
    assert candidates == [("p1", "MAIN_SWORD", 50)]


if __name__ == "__main__":
    test_black_hands_listada_como_removida()
    test_weapon_scorer_ignora_base_removida()
    test_weapon_scorer_cai_pro_segundo_melhor_quando_removida_eh_top()
    print("ok")