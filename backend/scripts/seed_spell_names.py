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
LOCAL_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/localization.xml"
OUT_FILE = Path(__file__).resolve().parents[1] / "data" / "spell_names.json"

# Elementos que contam como "um feitiço" pra numeração do jogo.
#
# `channelingspell` (276) é a pegadinha: não é irmão dos outros, é FILHO de um
# `activespell`, e não tem `uniquename` — mas OCUPA UM ÍNDICE no jogo. Sem
# contá-lo, o índice ia atrasando ~1 a cada 26 feitiços e TODOS os nomes saíam
# errados a partir do primeiro canalizado, cada vez mais deslocados.
# Confirmado com 18 pares (skill usada no dummy → id observado): 18/18 batem
# com ele, 0/18 sem. Frost Beam, que é canalizada, cai exatamente no
# channelingspell do FROSTBEAM.
SPELL_TAGS = ("activespell", "passivespell", "togglespell", "channelingspell")

# TMX lang → chave curta no JSON. Só os idiomas do companion.
LANGS = {"EN-US": "en", "PT-BR": "pt", "ES-ES": "es"}
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# Ícone: https://render.albiononline.com/v1/spell/{id}.png — chaveia pelo
# uniquename, não pelo uisprite. Por isso o JSON não carrega sprite nenhum.


def build(xml_text: str) -> list[dict[str, str]]:
    """[{id, name, loctag}] na ordem em que os feitiços aparecem no XML."""
    root = ElementTree.fromstring(xml_text)
    # ElementTree não dá pai; o canalizado precisa herdar do dele.
    parent = {child: p for p in root.iter() for child in p}
    out: list[dict[str, str]] = []
    # iter() percorre em ordem de documento — é isso que o JSON perdia.
    for el in root.iter():
        if el.tag not in SPELL_TAGS:
            continue
        uid = el.get("uniquename")
        loctag = el.get("namelocatag")
        if not uid and el.tag == "channelingspell":
            # É o componente canalizado do feitiço pai (Frost Beam e cia.):
            # sem nome próprio, mas com índice próprio. Herda a identidade do
            # pai — assim nome, tradução, ícone e família saem certos de graça.
            p = parent.get(el)
            if p is not None:
                uid = p.get("uniquename")
                loctag = loctag or p.get("namelocatag")
        if not uid:
            continue
        # Metade dos feitiços não tem namelocatag; nesses a convenção
        # @SPELLS_{uniquename} resolve boa parte. O resto cai no nome
        # normalizado em inglês, que é melhor que "Habilidade 2972".
        out.append({
            "id": uid,
            "name": _spell_name(uid),
            "loctag": loctag or f"@SPELLS_{uid}",
        })
    return out


def localize(path: Path, wanted: set[str]) -> dict[str, dict[str, str]]:
    """tuid → {lang: texto}, lendo o TMX de 73 MB em streaming."""
    out: dict[str, dict[str, str]] = {}
    cur: str | None = None
    for ev, el in ElementTree.iterparse(path, events=("start", "end")):
        if ev == "start" and el.tag == "tu":
            cur = el.get("tuid")
        elif ev == "end" and el.tag == "tu":
            el.clear()  # sem isso a árvore inteira fica na memória
            cur = None
        elif ev == "end" and el.tag == "tuv" and cur in wanted:
            lang = LANGS.get(el.get(XML_LANG, ""))
            seg = el.find("seg")
            if lang and seg is not None and seg.text:
                out.setdefault(cur, {})[lang] = seg.text
    return out


ITEMS_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"

# `@shopsubcategory1` de arma que NÃO é arma de combate — ferramenta de coleta
# e afins não interessam pro damage meter.
NOT_COMBAT = {"ore", "rock", "wood", "fiber", "hide", "fish", "guilds", "weapons"}


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def weapon_families(weapons: list[dict]) -> dict[str, str]:
    """`{spell_uniquename: família}` (bow, dagger, sword, …) do dump de itens.

    A lista de feitiços de uma arma quase nunca está nela: 629 das 771 armas
    só trazem `craftingspelllist/@reference` apontando pra uma arma-base, mais
    `craftspell`/`removespell` de delta. Sem resolver essa indireção o mapa fica
    com metade dos feitiços — foi por isso que `AIR_RAID` (do arco de cristal,
    que só existe como delta) não aparecia em arma nenhuma.

    Feitiço compartilhado entre famílias diferentes é DESCARTADO: melhor não
    mostrar arma do que mostrar a errada.
    """
    by_id = {w["@uniquename"]: w for w in weapons if w.get("@uniquename")}

    def spells_of(uid: str, seen: set[str] | None = None) -> set[str]:
        seen = seen if seen is not None else set()
        if uid in seen or uid not in by_id:
            return set()          # ciclo ou referência morta
        seen.add(uid)
        csl = by_id[uid].get("craftingspelllist")
        if not isinstance(csl, dict):
            return set()
        out = spells_of(csl["@reference"], seen) if csl.get("@reference") else set()
        for r in _as_list(csl.get("removespell")):
            out.discard(r.get("@uniquename"))
        for c in _as_list(csl.get("craftspell")):
            if c.get("@uniquename"):
                out.add(c["@uniquename"])
        return out

    fam: dict[str, str] = {}
    conflicting: set[str] = set()
    for uid, w in by_id.items():
        sub = w.get("@shopsubcategory1")
        # T4 basta: as outras tiers repetem a mesma lista de feitiços.
        if not uid.startswith("T4_") or not sub or sub in NOT_COMBAT:
            continue
        for sp in spells_of(uid):
            if fam.get(sp, sub) != sub:
                conflicting.add(sp)
            fam[sp] = sub
    for sp in conflicting:
        fam.pop(sp, None)
    return fam


def apply_families(spells: list[dict], fam: dict[str, str]) -> int:
    """Marca `fam` em cada feitiço; sub-feitiço herda pelo prefixo mais longo."""
    prefixes = {
        **fam,
        **{
            sid.removeprefix("SHAPESHIFT_"): family
            for sid, family in fam.items()
            if sid.startswith("SHAPESHIFT_")
        },
    }
    keys = sorted(prefixes, key=len, reverse=True)
    hits = 0
    for s in spells:
        sid = s["id"]
        f = fam.get(sid) or next((prefixes[k] for k in keys if sid.startswith(k + "_")), None)
        if f:
            s["fam"] = f
            hits += 1
    return hits


def adopt_from_parent(spells: list[dict], localized: set[str]) -> int:
    """Sub-feitiço sem tradução herda nome e ÍCONE do feitiço-pai.

    O dano no jogo costuma vir creditado a um sub-feitiço interno, não à
    habilidade que o jogador clicou. A maioria desses sub-feitiços aponta o
    `namelocatag` do pai e já sai certo — mas alguns não têm tag nenhuma e
    caíam no normalizador do uniquename, virando coisas como
    "Dash Knockback Cooldown Reduction", que não é nome de skill nenhuma:
    o pai é `DASH_KNOCKBACK` = "Soaring Swipe".

    Acha o pai andando pra trás nos `_` do uniquename até bater num feitiço
    que EXISTE e foi localizado. `icon` é gravado só quando difere do próprio
    id — o CDN tem arte pro sub-feitiço, mas é um ícone genérico de passiva
    em vez do ícone da habilidade.
    """
    by_id = {s["id"]: s for s in spells}
    adopted = 0
    for s in spells:
        if s["id"] in localized:
            continue
        parts = s["id"].split("_")
        for k in range(len(parts) - 1, 0, -1):
            parent_id = "_".join(parts[:k])
            if parent_id not in localized:
                continue
            parent = by_id[parent_id]
            s["name"] = parent["name"]
            for lang in ("pt", "es"):
                if parent.get(lang):
                    s[lang] = parent[lang]
            s["icon"] = parent_id
            adopted += 1
            break
    return adopted


def main() -> None:
    # Console do Windows é cp1252 e engasga nos nomes acentuados do resumo.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"baixando {SPELLS_URL} …")
    xml_text = httpx.get(SPELLS_URL, timeout=180, follow_redirects=True).text
    spells = build(xml_text)
    if len(spells) < 5000:
        raise SystemExit(f"só {len(spells)} feitiços — dump suspeito, abortando")

    print(f"baixando {LOCAL_URL} (~73 MB) …")
    tmp = OUT_FILE.parent / "localization.tmp.xml"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", LOCAL_URL, timeout=600, follow_redirects=True) as r, tmp.open("wb") as f:
        for chunk in r.iter_bytes(1 << 20):
            f.write(chunk)
    loc = localize(tmp, {s["loctag"] for s in spells})
    tmp.unlink(missing_ok=True)

    hits = 0
    localized: set[str] = set()
    for s in spells:
        tr = loc.get(s.pop("loctag"), {})
        if tr:
            hits += 1
            localized.add(s["id"])
            # `name` fica sendo o inglês oficial quando existe; pt/es só entram
            # quando diferem, pra não inflar o JSON que o companion baixa.
            s["name"] = tr.get("en", s["name"])
            for lang in ("pt", "es"):
                if tr.get(lang) and tr[lang] != s["name"]:
                    s[lang] = tr[lang]

    adopted = adopt_from_parent(spells, localized)

    print(f"baixando {ITEMS_URL} (~17 MB) …")
    items = httpx.get(ITEMS_URL, timeout=600, follow_redirects=True).json()["items"]
    # `transformationweapon` são os cajados de shapeshifter (polymorph) — ficam
    # numa lista SEPARADA de `weapon` no dump. Olhando só `weapon`, a família
    # `shapeshifterstaff` inteira sumia e essas armas ficavam sem cor nem
    # rótulo no ranking. `equipmentitem` e `mount` também têm feitiço, mas são
    # armadura/capa/montaria — não entram na coluna de ARMA.
    weapons = [*items["weapon"], *items.get("transformationweapon", [])]
    with_fam = apply_families(spells, weapon_families(weapons))

    OUT_FILE.write_text(json.dumps(spells, ensure_ascii=False), encoding="utf-8")
    print(f"{len(spells)} feitiços ({hits} localizados, {adopted} herdados, "
          f"{with_fam} com arma) → {OUT_FILE}")
    for i in (0, 1, len(spells) // 2, len(spells) - 1):
        print(f"  [{i}] {spells[i]['id']} → {spells[i]}")


if __name__ == "__main__":
    main()
