"""
Tabela de nomes de feitiço do damage meter.
Roda com pytest OU: PYTHONPATH=. python tests/test_spell_names.py

O que importa aqui é a ORDEM: o índice que vem no pacote é a posição no
documento, então qualquer coisa que reordene os feitiços quebra todos os nomes
de uma vez (silenciosamente — vira só nome errado, não erro).
"""
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from app.main import app
from scripts.seed_spell_names import (
    adopt_from_parent, apply_families, build, localize, weapon_families,
)

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


# O bug que quebrou TODOS os nomes: `channelingspell` é filho do activespell,
# não tem uniquename, mas OCUPA UM ÍNDICE no jogo. Sem contá-lo o índice
# atrasava ~1 a cada 26 feitiços — e o erro crescia, então quanto mais alto o
# id, mais longe do certo. Confirmado com 19 pares medidos no dummy.
CHANNEL_XML = """<spells>
  <activespell uniquename="ICESHARD" />
  <activespell uniquename="FROSTBEAM" namelocatag="@SPELLS_FROSTBEAM">
    <channelingspell effectinterval="0.3" />
  </activespell>
  <activespell uniquename="FROSTBEAM_EFFECT" />
</spells>"""


def test_channelingspell_ocupa_indice_e_herda_o_pai():
    spells = build(CHANNEL_XML)
    assert [s["id"] for s in spells] == [
        "ICESHARD", "FROSTBEAM", "FROSTBEAM", "FROSTBEAM_EFFECT",
    ], "o canalizado entra ENTRE o pai e o próximo feitiço, ou tudo desloca"
    assert spells[2]["id"] == "FROSTBEAM", "herda identidade do pai"
    assert spells[2]["loctag"] == "@SPELLS_FROSTBEAM", "herda a tag de tradução do pai"


def test_tag_sem_uniquename_que_nao_e_channeling_continua_ignorada():
    spells = build('<spells><activespell /><activespell uniquename="X" /></spells>')
    assert [s["id"] for s in spells] == ["X"]


def test_nome_legivel_vem_do_normalizador_do_projeto():
    spells = build(XML)
    assert spells[0]["name"] == "Heroicstrike"
    assert spells[1]["name"] == "Toughness", "prefixo PASSIVE_ some"


TMX = """<tmx><body>
  <tu tuid="@SPELLS_HEROICSTRIKE">
    <tuv xml:lang="EN-US"><seg>Heroic Strike</seg></tuv>
    <tuv xml:lang="PT-BR"><seg>Golpe Heroico</seg></tuv>
    <tuv xml:lang="DE-DE"><seg>Heldenhafter Schlag</seg></tuv>
  </tu>
  <tu tuid="@NAO_PEDIDO"><tuv xml:lang="PT-BR"><seg>nao deve entrar</seg></tuv></tu>
</body></tmx>"""


def test_loctag_usa_namelocatag_com_fallback_de_convencao():
    spells = build('<spells><activespell uniquename="X" namelocatag="@CUSTOM" />'
                   '<activespell uniquename="Y" /></spells>')
    assert spells[0]["loctag"] == "@CUSTOM"
    assert spells[1]["loctag"] == "@SPELLS_Y", "sem tag, cai na convenção"


def test_localize_pega_so_os_idiomas_pedidos_e_so_as_tags_pedidas():
    with TemporaryDirectory() as d:
        p = Path(d) / "loc.xml"
        p.write_text(TMX, encoding="utf-8")
        loc = localize(p, {"@SPELLS_HEROICSTRIKE"})
    assert loc == {"@SPELLS_HEROICSTRIKE": {"en": "Heroic Strike", "pt": "Golpe Heroico"}}, \
        "DE-DE não é idioma do companion; @NAO_PEDIDO não foi pedido"


def test_subfeitico_sem_traducao_herda_nome_e_icone_do_pai():
    """O caso real: DASH_KNOCKBACK_COOLDOWN_REDUCTION virava
    "Dash Knockback Cooldown Reduction", que não é skill nenhuma."""
    spells = [
        {"id": "DASH_KNOCKBACK", "name": "Soaring Swipe", "pt": "Pancada Elevada"},
        {"id": "DASH_KNOCKBACK_COOLDOWN_REDUCTION", "name": "Dash Knockback Cooldown Reduction"},
        # Sem ancestral localizado: continua no fallback, sem `icon`.
        {"id": "ORFAO_QUALQUER", "name": "Orfao Qualquer"},
    ]
    adotados = adopt_from_parent(spells, localized={"DASH_KNOCKBACK"})

    assert adotados == 1
    filho = spells[1]
    assert filho["name"] == "Soaring Swipe"
    assert filho["pt"] == "Pancada Elevada"
    assert filho["icon"] == "DASH_KNOCKBACK", "ícone tem que ser o da habilidade, não o da passiva"
    assert "icon" not in spells[0], "quem tem nome próprio não ganha icon redundante"
    assert spells[2]["name"] == "Orfao Qualquer" and "icon" not in spells[2]


def test_heranca_pega_o_ancestral_mais_proximo():
    """Andando pra trás nos `_`, o primeiro pai localizado ganha — senão um
    prefixo curto e genérico sequestraria o nome."""
    spells = [
        {"id": "FIRE", "name": "Fogo"},
        {"id": "FIRE_WALL", "name": "Muralha de Fogo"},
        {"id": "FIRE_WALL_TICK", "name": "Fire Wall Tick"},
    ]
    adopt_from_parent(spells, localized={"FIRE", "FIRE_WALL"})
    assert spells[2]["name"] == "Muralha de Fogo"
    assert spells[2]["icon"] == "FIRE_WALL"


# 629 das 771 armas do dump só têm `@reference` + deltas. Ignorar isso deixava
# metade dos feitiços sem arma — AIR_RAID, do arco de cristal, era um deles.
WEAPONS = [
    {"@uniquename": "T4_2H_BOW", "@shopsubcategory1": "bow",
     "craftingspelllist": {"craftspell": [
         {"@uniquename": "MULTISHOT2"}, {"@uniquename": "SPEEDARCHER_KITE"}]}},
    {"@uniquename": "T4_2H_BOW_CRYSTAL", "@shopsubcategory1": "bow",
     "craftingspelllist": {"@reference": "T4_2H_BOW",
                           "removespell": {"@uniquename": "SPEEDARCHER_KITE"},
                           "craftspell": {"@uniquename": "AIR_RAID"}}},
    {"@uniquename": "T4_MAIN_DAGGER", "@shopsubcategory1": "dagger",
     "craftingspelllist": {"craftspell": {"@uniquename": "DEADLYSWIPE"}}},
    # Compartilhado entre famílias: não pode virar nem uma nem outra.
    {"@uniquename": "T4_MAIN_SWORD", "@shopsubcategory1": "sword",
     "craftingspelllist": {"craftspell": [
         {"@uniquename": "HEROICSTRIKE"}, {"@uniquename": "COMPARTILHADO"}]}},
    {"@uniquename": "T4_2H_AXE", "@shopsubcategory1": "axe",
     "craftingspelllist": {"craftspell": {"@uniquename": "COMPARTILHADO"}}},
    # Ferramenta de coleta não é arma de combate.
    {"@uniquename": "T4_2H_TOOL_PICK", "@shopsubcategory1": "ore",
     "craftingspelllist": {"craftspell": {"@uniquename": "MINERAR"}}},
]


def test_familia_resolve_referencia_e_deltas():
    fam = weapon_families(WEAPONS)
    assert fam["MULTISHOT2"] == "bow"
    assert fam["AIR_RAID"] == "bow", "craftspell do delta tem que entrar"
    assert fam["DEADLYSWIPE"] == "dagger"
    assert "SPEEDARCHER_KITE" not in fam or fam.get("SPEEDARCHER_KITE") == "bow", \
        "removespell tira do arco de cristal, mas o arco base ainda tem"
    assert "COMPARTILHADO" not in fam, "feitiço de 2 famílias é descartado, não chutado"
    assert "MINERAR" not in fam, "picareta não é arma de combate"


def test_subfeitico_herda_familia_por_prefixo():
    spells = [
        {"id": "AIR_RAID", "name": "Sky's Fury"},
        {"id": "AIR_RAID_BOLTS_DAMAGE", "name": "Sky Bolt"},
        {"id": "MOB_QUALQUER_COISA", "name": "Mob"},
    ]
    n = apply_families(spells, {"AIR_RAID": "bow"})
    assert n == 2
    assert spells[1]["fam"] == "bow", "o dano vem do sub-feitiço, ele precisa da família"
    assert "fam" not in spells[2], "feitiço de mob não tem arma"


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
    test_channelingspell_ocupa_indice_e_herda_o_pai()
    test_tag_sem_uniquename_que_nao_e_channeling_continua_ignorada()
    test_nome_legivel_vem_do_normalizador_do_projeto()
    test_loctag_usa_namelocatag_com_fallback_de_convencao()
    test_localize_pega_so_os_idiomas_pedidos_e_so_as_tags_pedidas()
    test_subfeitico_sem_traducao_herda_nome_e_icone_do_pai()
    test_heranca_pega_o_ancestral_mais_proximo()
    test_familia_resolve_referencia_e_deltas()
    test_subfeitico_herda_familia_por_prefixo()
    test_endpoint_serve_a_lista()
    print("spell names OK")
