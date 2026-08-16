"""Regras do /register pós-vigilância opcional — contador de tentativas do
ID sintético manual:* (verificação retroativa) e guarda-rails dos fluxos.
Roda direto: PYTHONPATH=. python tests/test_registration_checker.py"""
from app.services.registration_checker import (
    VERIFY_MAX_ATTEMPTS,
    _manual_attempt_id,
)


def test_manual_id_conta_tentativas():
    # 1ª falha: sem sufixo → #1; 2ª → #2; 3ª → esgotou (None = revoga)
    assert _manual_attempt_id("manual:foo", "foo") == "manual:foo#1"
    assert _manual_attempt_id("manual:foo#1", "foo") == "manual:foo#2"
    assert _manual_attempt_id("manual:foo#2", "foo") is None
    assert VERIFY_MAX_ATTEMPTS == 3


def test_manual_id_reseta_se_nick_mudou():
    # O contador mora no ID; se o nick do ID atual difere (ex: linha editada),
    # recomeça do #1 do nick NOVO — nunca herda contador de outro nick.
    assert _manual_attempt_id("manual:bar#2", "foo") == "manual:foo#1"


def test_manual_id_sufixo_nao_numerico_ignorado():
    assert _manual_attempt_id("manual:foo#x", "foo") == "manual:foo#1"


if __name__ == "__main__":
    test_manual_id_conta_tentativas()
    test_manual_id_reseta_se_nick_mudou()
    test_manual_id_sufixo_nao_numerico_ignorado()
    print("registration checker OK")
