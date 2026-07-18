"""
Tabela de nomes de feitiço do damage meter.
Roda com pytest OU: PYTHONPATH=. python tests/test_spell_names.py

O que importa aqui é a ORDEM: o índice que vem no pacote é a posição no
documento, então qualquer coisa que reordene os feitiços quebra todos os nomes
de uma vez (silenciosamente — vira só nome errado, não erro).
"""
from fastapi.testclient import TestClient

from app.main import app
from scripts.seed_spell_names import build

# Ordem de propósito embaralhada entre tipos: no XML real activespell e
# passivespell se intercalam, e é essa ordem que tem que sobreviver.
XML = """<spells>
  <activespell uniquename="HEROICSTRIKE" />
  <passivespell uniquename="PASSIVE_TOUGHNESS" />
  <activespell uniquename="GENEROUSHEAL" />
  <notaspell uniquename="IGNORE_ME" />
  <togglespell uniquename="MOUNTSPELL_SPRINT" />
  <activespell />
</spells>"""


def test_ordem_do_documento_e_preservada():
    spells = build(XML)
    assert [s["id"] for s in spells] == [
        "HEROICSTRIKE", "PASSIVE_TOUGHNESS", "GENEROUSHEAL", "MOUNTSPELL_SPRINT",
    ], "a posição é o índice — reordenar quebra todos os nomes"


def test_ignora_tag_desconhecida_e_entrada_sem_uniquename():
    assert len(build(XML)) == 4


def test_nome_legivel_vem_do_normalizador_do_projeto():
    spells = build(XML)
    assert spells[0]["name"] == "Heroicstrike"
    assert spells[1]["name"] == "Toughness", "prefixo PASSIVE_ some"


def test_endpoint_serve_a_lista():
    r = TestClient(app).get("/companion/spells")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    if body:  # vazio = dump não seedado nesta máquina, e isso é aceitável
        assert {"id", "name"} <= set(body[0])
        assert r.headers.get("cache-control", "").startswith("public")


if __name__ == "__main__":
    test_ordem_do_documento_e_preservada()
    test_ignora_tag_desconhecida_e_entrada_sem_uniquename()
    test_nome_legivel_vem_do_normalizador_do_projeto()
    test_endpoint_serve_a_lista()
    print("spell names OK")
