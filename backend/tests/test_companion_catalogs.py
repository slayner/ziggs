"""Contratos públicos dos catálogos consumidos pelo Ziggs Companion."""

from pathlib import Path

from app.api.routes import companion as companion_module


def test_catalogos_do_companion_definem_cache_de_um_dia(monkeypatch):
    class Response:
        def __init__(self):
            self.headers: dict[str, str] = {}

    monkeypatch.setattr(companion_module, "_spell_names", lambda: [])
    monkeypatch.setattr(companion_module.market_history, "get_index_catalog", lambda: [])
    spells_response = Response()
    items_response = Response()

    assert companion_module.companion_spells(spells_response) == []
    assert companion_module.companion_items(items_response) == []

    assert spells_response.headers["Cache-Control"] == "public, max-age=86400"
    assert items_response.headers["Cache-Control"] == "public, max-age=86400"


def test_catalogo_de_habilidades_ausente_devolve_lista_vazia():
    original = companion_module._SPELLS_FILE
    try:
        companion_module._SPELLS_FILE = Path("arquivo-de-spells-ausente.json")
        companion_module._spell_names.cache_clear()

        assert companion_module._spell_names() == []
    finally:
        companion_module._SPELLS_FILE = original
        companion_module._spell_names.cache_clear()
