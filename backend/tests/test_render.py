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
from unittest.mock import AsyncMock, patch

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


def test_placeholder_antigo_entra_na_fila_de_recuperacao():
    with TemporaryDirectory() as d:
        cache_dir = Path(d)
        key = "T8_HEAD_PLATE_SET1"
        cache_path = cache_dir / "T8_HEAD_PLATE_SET1_q1_s128.png"
        placeholder = render_module._generate_placeholder(key)
        assert placeholder is not None
        cache_path.write_bytes(placeholder)

        misses = render_module.discover_cached_render_misses(cache_dir)

        assert misses == [("item", key, 1, 128)]
        assert render_module._missing_path(cache_path).exists()
        assert not render_module._cache_has_real_render(cache_path, key)


def test_retentar_render_usa_backoff_limitado():
    assert render_module.retry_delay(1).total_seconds() == 6 * 3600
    assert render_module.retry_delay(2).total_seconds() == 24 * 3600
    assert render_module.retry_delay(3).total_seconds() == 7 * 24 * 3600
    assert render_module.retry_delay(99).total_seconds() == 7 * 24 * 3600


def test_placeholder_nao_fica_immutavel_no_navegador():
    with TemporaryDirectory() as d:
        cache_path = Path(d) / "T8_HEAD_PLATE_SET1_q1_s128.png"
        placeholder = render_module._generate_placeholder("T8_HEAD_PLATE_SET1")
        assert placeholder is not None
        cache_path.write_bytes(placeholder)
        render_module._missing_path(cache_path).touch()

        with patch("app.api.routes.render._record_render_miss", AsyncMock()):
            response = asyncio.run(render_module._cached_render("item", "T8_HEAD_PLATE_SET1", cache_path, {}))

        assert response.headers["cache-control"] == "public, max-age=300"


def test_retentativa_preserva_placeholder_pequeno():
    async def run(cache_dir: Path):
        old_cache_dir = render_module._CACHE_DIR
        render_module._CACHE_DIR = cache_dir
        try:
            cache_path = cache_dir / "T1_TEST_q0_s0.png"
            cache_path.write_bytes(b"x" * 500)
            render_module._missing_path(cache_path).touch()
            with patch("app.api.routes.render._fetch_render", AsyncMock(return_value=None)):
                result = await render_module.recover_render_miss("item", "T1_TEST", 0, 0)
            return result, cache_path
        finally:
            render_module._CACHE_DIR = old_cache_dir

    with TemporaryDirectory() as d:
        result, cache_path = asyncio.run(run(Path(d)))
        assert result is False
        assert cache_path.exists()


def test_render_rejeita_tamanho_que_criaria_cache_sem_limite():
    try:
        asyncio.run(render_module.render_item("T8_HEAD_PLATE_SET1", size=1))
    except render_module.HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("size arbitrário não deve criar uma chave de cache")


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
    test_placeholder_antigo_entra_na_fila_de_recuperacao()
    test_retentar_render_usa_backoff_limitado()
    test_placeholder_nao_fica_immutavel_no_navegador()
    test_retentativa_preserva_placeholder_pequeno()
    test_render_rejeita_tamanho_que_criaria_cache_sem_limite()
    test_cache_frio_concorrente_baixa_uma_vez()
    print("render OK")
