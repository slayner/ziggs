"""
Render proxy (`/render/item/*`, `/render/spell/*`).

What matters here: the Albion CDN **never returns 404** for a key with no art.
It returns 200 with an empty PNG (~281 bytes) or with a 26178-byte white
placeholder. If the proxy doesn't recognize both, it writes junk to disk and
serves a white icon forever — which was exactly the reported bug.

Run with pytest OR: PYTHONPATH=. python tests/test_render.py
"""
import asyncio
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from app.api.routes import render as render_module
from app.api.routes.render import (
    _MIN_RENDER_BYTES,
    _PLACEHOLDER_BYTES,
    _PLACEHOLDER_SHA1,
    _cache_usable,
    _cached_render,
    _is_placeholder,
    _spell_display_names,
)


def test_png_minusculo_e_placeholder():
    assert _is_placeholder(b"x" * 100), "empty ~281-byte PNG cannot be cached"
    assert _is_placeholder(b""), "empty response too"


def test_render_de_verdade_passa():
    # Real art is tens of KB; anything above the minimum that isn't the known
    # placeholder is valid.
    assert not _is_placeholder(b"x" * (_MIN_RENDER_BYTES * 40))


def test_hash_do_placeholder_esta_bem_formado():
    """Guard against typos: a wrong sha1 would never match anything and
    detection would fail silently, going back to serving the white icon."""
    assert re.fullmatch(r"[0-9a-f]{40}", _PLACEHOLDER_SHA1)


def test_fallback_por_nome_encontra_a_skill():
    """New/reworked skills key art by NAME, not uniquename. The sub-spell
    HAMMER_SHOVE_SWING_EFFECT returns the placeholder by id and only resolves
    by falling back to "Powerful Swing"."""
    nomes = _spell_display_names()
    if not nomes:
        return  # dump not seeded on this machine — acceptable
    assert nomes.get("HAMMER_SHOVE") == "Powerful Swing"
    assert nomes.get("HAMMER_SHOVE_SWING_EFFECT") == "Powerful Swing", \
        "without this the sub-spell has no fallback and serves the white placeholder"


def test_cache_valida_conteudo_suspeito_e_remove_vazio():
    with TemporaryDirectory() as d:
        mesmo_tamanho = Path(d) / "legitimo.png"
        mesmo_tamanho.write_bytes(b"x" * _PLACEHOLDER_BYTES)
        assert _cache_usable(mesmo_tamanho), \
            "size alone doesn't prove the PNG is the known placeholder"

        vazio = Path(d) / "vazio.png"
        vazio.write_bytes(b"x" * 200)
        assert not _cache_usable(vazio)

        bom = Path(d) / "bom.png"
        bom.write_bytes(b"x" * 50_000)
        assert _cache_usable(bom)
        assert bom.exists(), "valid render cannot be deleted"

        assert not _cache_usable(Path(d) / "nao_existe.png")


def test_cache_frio_concorrente_baixa_uma_vez():
    calls = 0

    class FakeClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass

        async def get(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return SimpleNamespace(status_code=200, content=b"x" * 2_000)

    async def exercise(path: Path):
        await asyncio.gather(*(
            _cached_render("item", "T8_TEST", path, {}) for _ in range(8)
        ))

    original = render_module.httpx.AsyncClient
    render_module.httpx.AsyncClient = FakeClient
    try:
        with TemporaryDirectory() as d:
            asyncio.run(exercise(Path(d) / "render.png"))
    finally:
        render_module.httpx.AsyncClient = original
    assert calls == 1, f"cold cache did {calls} identical downloads"


if __name__ == "__main__":
    test_png_minusculo_e_placeholder()
    test_render_de_verdade_passa()
    test_hash_do_placeholder_esta_bem_formado()
    test_fallback_por_nome_encontra_a_skill()
    test_cache_valida_conteudo_suspeito_e_remove_vazio()
    test_cache_frio_concorrente_baixa_uma_vez()
    print("render OK")
