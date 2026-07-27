"""
Proxy de render (`/render/item/*`, `/render/spell/*`).

O que importa aqui: a CDN da Albion **nunca dá 404** pra chave sem arte. Ela
devolve 200 com um PNG vazio (~281 bytes) ou com um placeholder branco de
26178 bytes. Se o proxy não reconhecer os dois, grava o lixo em disco e passa
a servir ícone branco pra sempre — que foi exatamente o bug relatado.

Roda com pytest OU: PYTHONPATH=. python tests/test_render.py
"""
import re

from pathlib import Path
from tempfile import TemporaryDirectory

from app.api.routes.render import (
    _MIN_RENDER_BYTES,
    _PLACEHOLDER_BYTES,
    _PLACEHOLDER_SHA1,
    _cache_usable,
    _is_placeholder,
    _spell_display_names,
)


def test_png_minusculo_e_placeholder():
    assert _is_placeholder(b"x" * 100), "PNG vazio de ~281 bytes não pode ser cacheado"
    assert _is_placeholder(b""), "resposta vazia também"


def test_render_de_verdade_passa():
    # Arte real tem dezenas de KB; qualquer coisa acima do mínimo e que não
    # seja o placeholder conhecido é válida.
    assert not _is_placeholder(b"x" * (_MIN_RENDER_BYTES * 40))


def test_hash_do_placeholder_esta_bem_formado():
    """Guarda contra erro de digitação: sha1 errado nunca casaria com nada e a
    detecção falharia em silêncio, voltando a servir o ícone branco."""
    assert re.fullmatch(r"[0-9a-f]{40}", _PLACEHOLDER_SHA1)


def test_fallback_por_nome_encontra_a_skill():
    """Skill nova/reworkada tem a arte chaveada pelo NOME, não pelo uniquename.
    O sub-feitiço HAMMER_SHOVE_SWING_EFFECT volta placeholder pelo id e só
    resolve caindo em "Powerful Swing"."""
    nomes = _spell_display_names()
    if not nomes:
        return  # dump não seedado nesta máquina — aceitável
    assert nomes.get("HAMMER_SHOVE") == "Powerful Swing"
    assert nomes.get("HAMMER_SHOVE_SWING_EFFECT") == "Powerful Swing", \
        "sem isso o sub-feitiço fica sem fallback e serve o placeholder branco"


def test_cache_envenenado_e_apagado_e_rebaixado():
    """A versão antiga do proxy gravou a moldura branca em disco. Sem apagar,
    ela continuaria sendo servida pra sempre — o `immutable` de 1 ano no
    header impede até o cliente de perguntar de novo."""
    with TemporaryDirectory() as d:
        placeholder = Path(d) / "poison.png"
        placeholder.write_bytes(b"x" * _PLACEHOLDER_BYTES)
        assert not _cache_usable(placeholder)
        assert not placeholder.exists(), "tem que apagar, senão a próxima request serve de novo"

        vazio = Path(d) / "vazio.png"
        vazio.write_bytes(b"x" * 200)
        assert not _cache_usable(vazio)

        bom = Path(d) / "bom.png"
        bom.write_bytes(b"x" * 50_000)
        assert _cache_usable(bom)
        assert bom.exists(), "render válido não pode ser apagado"

        assert not _cache_usable(Path(d) / "nao_existe.png")


if __name__ == "__main__":
    test_png_minusculo_e_placeholder()
    test_render_de_verdade_passa()
    test_hash_do_placeholder_esta_bem_formado()
    test_fallback_por_nome_encontra_a_skill()
    test_cache_envenenado_e_apagado_e_rebaixado()
    print("render OK")
