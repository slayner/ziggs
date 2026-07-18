"""
Gera `data/spell_names.json` — nomes de feitiço em ORDEM DE DOCUMENTO, pro
damage meter do companion traduzir o índice numérico que vem no pacote.

Uso:
  cd backend && python -m scripts.seed_spell_names

Por que XML e não o spells.json:
  O `spells.json` do ao-bin-dumps agrupa por tipo (activespell/passivespell/
  togglespell), o que DESTRÓI a ordem original — e nenhum dos dois formatos traz
  um campo de índice explícito (diferente de items.json, que tem `Index`). Como a
  hipótese é que o índice do pacote é a posição no documento, só o XML serve.

ATENÇÃO — o mapeamento índice→nome é uma HIPÓTESE não verificada:
  Não há campo de índice no dump. Assumimos posição no documento, contando todos
  os elementos de feitiço na ordem em que aparecem. Isso PRECISA ser calibrado
  contra tráfego real (ver `spell_index_offset` no companion) antes de ser
  tratado como verdade. Por isso a UI mostra o id cru junto do nome.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

import httpx

sys.path.insert(0, ".")

from scripts.seed_weapons_spells import _spell_name

SPELLS_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/spells.xml"
OUT_FILE = Path(__file__).resolve().parents[1] / "data" / "spell_names.json"

# Elementos que contam como "um feitiço" pra numeração.
SPELL_TAGS = ("activespell", "passivespell", "togglespell")


def build(xml_text: str) -> list[dict[str, str]]:
    """[{id, name}] na ordem em que os feitiços aparecem no XML."""
    root = ElementTree.fromstring(xml_text)
    out: list[dict[str, str]] = []
    # iter() percorre em ordem de documento — é isso que o JSON perdia.
    for el in root.iter():
        if el.tag not in SPELL_TAGS:
            continue
        uid = el.get("uniquename")
        if not uid:
            continue
        out.append({"id": uid, "name": _spell_name(uid)})
    return out


def main() -> None:
    print(f"baixando {SPELLS_URL} …")
    xml_text = httpx.get(SPELLS_URL, timeout=180, follow_redirects=True).text
    spells = build(xml_text)
    if len(spells) < 5000:
        raise SystemExit(f"só {len(spells)} feitiços — dump suspeito, abortando")
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(spells, ensure_ascii=False), encoding="utf-8")
    print(f"{len(spells)} feitiços → {OUT_FILE}")
    for i in (0, 1, len(spells) // 2, len(spells) - 1):
        print(f"  [{i}] {spells[i]['id']} → {spells[i]['name']}")


if __name__ == "__main__":
    main()
