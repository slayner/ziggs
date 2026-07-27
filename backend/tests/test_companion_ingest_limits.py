"""
Atribuição e teto de vazão nos ingests do companion.

Contexto: o companion roda na máquina do usuário, então NADA que ele reporta é
confiável — dá pra injetar pacote UDP forjado localmente, patchear o binário ou
falar direto com a API. Estas duas defesas não tornam a fraude impossível (nada
torna, do lado do cliente); elas a tornam CARA, VISÍVEL e REVERSÍVEL:

  - `source_install` em item_prices (append-only) → expurgo retroativo vira um
    DELETE por coluna, e o log diz de quem veio.
  - teto por linhas/instalação → corta flood barato e loga quem estourou.

A defesa de verdade contra preço mentiroso continua sendo estatística
(mediana + corte de outlier por IQR no pipeline de leitura).

Roda com pytest OU: PYTHONPATH=. python tests/test_companion_ingest_limits.py
"""
from app.api.routes.companion import (
    _MAX_ROWS_PER_REQUEST,
    _RATE_MAX_ROWS,
    _install_id,
    _rate_log,
    _rate_ok,
)

INST_A = "a" * 32
INST_B = "b" * 32


def setup_function(_=None):
    _rate_log.clear()


def test_install_id_so_aceita_o_formato_certo():
    assert _install_id(INST_A) == INST_A
    assert _install_id(INST_A.upper()) == INST_A, "normaliza pra minúsculo"
    assert _install_id("  " + INST_A + " ") == INST_A
    # Qualquer coisa fora do formato vira None (= companion antigo, sem header).
    for ruim in [None, "", "xyz", "a" * 31, "a" * 33, "g" * 32, "../../etc/passwd"]:
        assert _install_id(ruim) is None, ruim


def test_teto_por_instalacao():
    _rate_log.clear()
    assert _rate_ok(INST_A, _RATE_MAX_ROWS - 1), "abaixo do teto passa"
    assert not _rate_ok(INST_A, 2), "o que ultrapassa é recusado"


def test_instalacoes_nao_compartilham_cota():
    """Senão um flooder derrubaria o envio de todo mundo — negação de serviço
    contra os usuários legítimos em vez de contra o atacante."""
    _rate_log.clear()
    assert not _rate_ok(INST_A, _RATE_MAX_ROWS + 1)
    assert _rate_ok(INST_B, 10), "outra instalação segue livre"


def test_sem_install_id_todos_dividem_o_mesmo_balde():
    """Trocar de id burla o teto — é limitação conhecida (o id não é auth).
    Mas quem NÃO se identifica não pode ganhar cota ilimitada por isso."""
    _rate_log.clear()
    assert _rate_ok(None, _RATE_MAX_ROWS)
    assert not _rate_ok(None, 1)
    assert not _rate_ok("formato-invalido", 1), "id inválido cai no mesmo balde"


def test_teto_conta_linhas_e_nao_requests():
    """200 requests de 1 linha fazem o mesmo estrago que 1 de 200."""
    _rate_log.clear()
    for _ in range(_RATE_MAX_ROWS):
        assert _rate_ok(INST_A, 1)
    assert not _rate_ok(INST_A, 1)


def test_limite_por_request_e_menor_que_a_janela():
    """Senão um único request já estouraria a cota e o teto por request não
    filtraria nada antes de tocar o banco."""
    assert _MAX_ROWS_PER_REQUEST < _RATE_MAX_ROWS


if __name__ == "__main__":
    for fn in [
        test_install_id_so_aceita_o_formato_certo,
        test_teto_por_instalacao,
        test_instalacoes_nao_compartilham_cota,
        test_sem_install_id_todos_dividem_o_mesmo_balde,
        test_teto_conta_linhas_e_nao_requests,
        test_limite_por_request_e_menor_que_a_janela,
    ]:
        setup_function()
        fn()
    print("companion ingest limits OK")
