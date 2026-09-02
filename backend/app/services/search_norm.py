"""Normalização de busca para nomes de entidades (jogador/guilda/aliança).

Nomes do Albion são frequentemente estilizados com espaços entre letras
(``S I G H T``), leet (``R E Q U 1 3 M``) e trocas de 1 letra (``PLVAS`` ↔
``pivas``). Este módulo normaliza strings (lower + remove espaços + leet) e
expora um ``match`` com distância de edição ≤ 1 para tolerar typos/variantes.

Usar só em nomes de entidade. Itens/IDs do catálogo têm dígitos de tier
significativos (``T5_HEAD_PLATE``) — leet neles quebraria o matching.
"""

from __future__ import annotations

import sqlalchemy as sa

# ponytail: leet cobre os dígitos/símbolos mais comuns em nick estilizado;
# 2/6/9 ficam como estão (pouco usados como letra em nick).
_LEET = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a",
    "5": "s", "7": "t", "8": "b", "$": "s", "@": "a",
})

# pares (de, para) na mesma ordem usada por norm_sql — manter em sync com _LEET.
_NORM_PAIRS: tuple[tuple[str, str], ...] = (
    (" ", ""),
    ("1", "i"), ("3", "e"), ("0", "o"), ("4", "a"),
    ("5", "s"), ("7", "t"), ("8", "b"), ("$", "s"), ("@", "a"),
)


def normalize(s: str | None) -> str:
    return (s or "").lower().replace(" ", "").translate(_LEET)


def levenshtein(a: str, b: str, max_dist: int = 1) -> int:
    """Distância de edição limitada; retorna ``max_dist + 1`` se exceder (poda)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > max_dist:
        return max_dist + 1
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ai = a[i - 1]
        row_min = cur[0]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if cur[j] < row_min:
                row_min = cur[j]
        if row_min > max_dist:
            return max_dist + 1  # poda: nenhuma célula desta linha já serve
        prev = cur
    return prev[lb]


def match(query: str, name: str, max_dist: int = 1) -> bool:
    """True se ``query`` casa ``name`` após normalização (substring) ou por
    distância de edição ≤ ``max_dist`` (só para queries de entidade ≥ 4 chars
    e comprimento parecido — evita falsos positivos em queries curtas)."""
    nq, nn = normalize(query), normalize(name)
    if not nq:
        return True
    if nq in nn:
        return True
    if len(nq) >= 4 and abs(len(nq) - len(nn)) <= 2 and levenshtein(nq, nn, max_dist) <= max_dist:
        return True
    return False


def prefix_range(nq: str) -> tuple[str, str]:
    """Limites [lo, hi) pra busca por prefixo via `col >= lo AND col < hi` em
    vez de `col LIKE 'nq%'`. Sargável sem pegadinha nenhuma: LIKE com
    wildcard à direita só vira index range scan no SQLite se
    `case_sensitive_like` estiver ON (não está, por padrão, e mudar isso é
    global — afeta todo LIKE do app); `>=`/`<` comparam a coluna direto
    (BINARY no SQLite) e sempre usam o índice."""
    if not nq:
        return "", "￿"
    return nq, nq[:-1] + chr(ord(nq[-1]) + 1)


def norm_sql(col) -> sa.sql.elements.FunctionElement:
    """Expressão SQLAlchemy que normaliza ``col`` igual a ``normalize``:
    ``lower(replace(replace(... col, ' ', ''), '1','i'), ...))``.

    SQLite e Postgres suportam ``replace``/``lower``/``length`` — funciona
    nos dois backends (dev SQLite, prod Postgres)."""
    expr = col
    for a, b in _NORM_PAIRS:
        expr = sa.func.replace(expr, a, b)
    return sa.func.lower(expr)


if __name__ == "__main__":  # self-check — ponytail: 1 check runnable, sem framework
    assert normalize("S I G H T") == "sight"
    assert normalize("R E Q U 1 3 M") == "requiem"
    assert normalize("PLVAS") == "plvas"
    assert normalize("pivas") == "pivas"
    assert normalize(None) == ""
    # 3 exemplos do usuário
    assert match("sight", "S I G H T")
    assert match("requiem", "R E Q U 1 3 M")
    assert match("pivas", "PLVAS")
    # substring normalizado continua funcionando
    assert match("plvas", "PLVAS")
    assert match("sight", "the S I G H T guild")
    assert match("requ", "R E Q U 1 3 M")
    # regressão: nome sem variante acha por substring
    assert match("shadow", "Shadow Legion")
    # guard de comprimento: query curta não casa por edit-distance
    assert not match("ab", "cd")          # 2 chars — sem edit-distance
    assert not match("xy", "abcdefgh")    # não-substring + diff de comprimento > 2
    # edit-distance 1 só: 1 troca casa, 2 trocas não
    assert match("plvbs", "PLVAS")        # 1 troca (b↔a)
    assert not match("plvxx", "PLVAS")    # 2 trocas
    assert not match("pibas", "PLVAS")    # 2 trocas (i↔l, b↔v)
    print("search_norm OK")