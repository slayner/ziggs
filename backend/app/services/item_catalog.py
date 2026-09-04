"""Catálogo autoritativo de itens Albion: índice numérico → UniqueName.

Carrega uma vez no startup do backend a partir de data/ao-bin-dump/items.txt
(o mesmo dump do ao-data). Não faz download em tempo de execução — se o
arquivo não existir, o backend inicia mas a resolução falha explicitamente.
"""
from __future__ import annotations
from pathlib import Path
import threading

_DUMP_DIR = Path(__file__).resolve().parents[2] / "data" / "ao-bin-dump"
_ITEMS_TXT_FILE = _DUMP_DIR / "items.txt"

_index_to_name: dict[int, str] = {}
_index_to_display_name: dict[int, str] = {}
_load_lock = threading.Lock()
_loaded = False


def load_catalog() -> None:
    """Carrega o items.txt para memória. Deve ser chamado no startup."""
    global _index_to_name, _index_to_display_name, _loaded
    with _load_lock:
        if _loaded:
            return
        try:
            text = _ITEMS_TXT_FILE.read_text(encoding="utf-8")
        except OSError:
            # Arquivo não provisionado — resolução vai falhar explicitamente.
            _loaded = True
            return
        mapping: dict[int, str] = {}
        display_names: dict[int, str] = {}
        for line in text.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 2:
                continue
            try:
                num = int(parts[0].strip())
            except ValueError:
                continue
            uid = parts[1].strip()
            if uid:
                mapping[num] = uid
                display_names[num] = parts[2].strip() if len(parts) > 2 else uid
        _index_to_name = mapping
        _index_to_display_name = display_names
        _loaded = True


def resolve_index(index: int) -> str | None:
    """Retorna o UniqueName para o índice, ou None se índice desconhecido.
    Carrega o catálogo sob demanda se ainda não estiver carregado."""
    if not _loaded:
        load_catalog()
    return _index_to_name.get(index)


def resolve_index_name(index: int) -> str | None:
    """Retorna o nome inglês para o índice, ou None se for desconhecido."""
    if not _loaded:
        load_catalog()
    return _index_to_display_name.get(index)


def is_catalog_ready() -> bool:
    return _loaded and bool(_index_to_name)


def catalog_size() -> int:
    return len(_index_to_name)