"""Guarda: TODA seleção de servidores que o usuário pode fazer no site (qualquer
subconjunto não-vazio das 3 regiões) tem que ser cacheável — senão os destaques
caem no cálculo ao vivo (scan semanal de ~470k participantes, ~20s por abertura).
Já regrediu uma vez: só singles + as-3-juntas eram cacheadas, e uma seleção de
DUAS regiões era lenta em toda abertura."""
from itertools import combinations

from app.services import highscores_cache as hc

REGIONS = ["americas", "europe", "asia"]


def _subsets():
    for n in range(1, len(REGIONS) + 1):
        yield from combinations(REGIONS, n)


def test_every_server_selection_caches_highlights():
    for combo in _subsets():
        assert hc.highlights_cache_key(list(combo)) is not None, combo


def test_guild_rankings_cached_for_every_selection():
    for combo in _subsets():
        for window in hc.WINDOWS:
            for kind in hc.GUILD_CACHED_KINDS:
                assert hc.rankings_cache_key(kind, window, list(combo)) is not None, (kind, window, combo)


def test_weapon_scorer_cached_for_full_and_pairs_not_singles():
    """weapon_scorer é pesado (~470k participantes na janela week). Cacheado
    pro full-combo (default do site) e pairs (comum ao desmarcar um servidor);
    singles ficam ao vivo (raros). Já regrediu uma vez: só full era cacheado e
    uma seleção de 2 regiões caía no cálculo ao vivo a cada abertura."""
    for combo in _subsets():
        for window in hc.WINDOWS:
            key = hc.rankings_cache_key("weapon_scorer", window, list(combo))
            if len(combo) >= 2:
                assert key is not None, ("weapon_scorer", window, combo)
            else:
                assert key is None, ("weapon_scorer", window, combo)
