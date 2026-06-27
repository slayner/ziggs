"""
Testes do motor de sugestão (lógica pura). Roda com pytest OU direto:
    PYTHONPATH=. python tests/test_suggestions.py
"""
from app.domain.suggestions import RoleBuild, suggest_build


def test_sugere_valor_mais_comum_da_mesma_funcao_invisivel():
    roles = [
        RoleBuild("support", helmet="8.4 Capuz do Asceta", food="8.0 Torta",
                  cape="8.3 Capa de Lymhurst", play_style="fica atrás"),
        RoleBuild("support", helmet="8.4 Capuz do Asceta", food="8.0 Torta",
                  cape="8.3 Capa de Martlock"),
        RoleBuild("tank", helmet="8.4 Elmo de Placas", food="8.0 Ensopado"),  # outra família
    ]
    s = suggest_build("support", roles)

    assert s.sample_size == 2                          # só os 2 supports contam
    assert s.fields["helmet"].value == "8.4 Capuz do Asceta"
    assert s.fields["helmet"].votes == 2 and s.fields["helmet"].total == 2
    # cape teve 2 valores distintos (1 voto cada) -> escolhe o primeiro mais comum
    assert s.fields["cape"].votes == 1
    # nada de tank vazou pra sugestão de support
    assert s.fields["helmet"].value != "8.4 Elmo de Placas"


def test_sem_familia_retorna_vazio():
    roles = [RoleBuild("dps_melee", helmet="x")]
    s = suggest_build("healer", roles)
    assert s.is_empty and s.sample_size == 0


def test_funcao_alvo_vazia_nao_quebra():
    s = suggest_build(None, [RoleBuild("support", helmet="x")])
    assert s.is_empty


def test_ignora_campos_em_branco():
    roles = [
        RoleBuild("support", helmet="  ", food="8.0 Torta"),
        RoleBuild("support", helmet="", food="8.0 Torta"),
    ]
    s = suggest_build("support", roles)
    assert "helmet" not in s.fields              # só espaços/vazio -> ignorado
    assert s.fields["food"].value == "8.0 Torta"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("todos passaram")
