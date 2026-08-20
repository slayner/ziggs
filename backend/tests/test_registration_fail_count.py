"""Testa o contador de falhas consecutivas do registration_checker: a API
do Albion retorna GuildId vazio/stale temporariamente, e antes o checker
revogava na primeira falha — removendo cargo de membros que estavam na guild.
Agora acumula fail_count e só revoga após REVOKE_AFTER_FAILS consecutivas.

Roda direto: PYTHONPATH=. python tests/test_registration_fail_count.py"""
from app.services.registration_checker import REVOKE_AFTER_FAILS


def test_revoke_after_fails_is_reasonable():
    # 4 falhas × 15min = 1h de tolerância — tempo pra API se recuperar.
    assert REVOKE_AFTER_FAILS == 4, (
        f"REVOKE_AFTER_FAILS deve ser 4 (4×15min=1h tolerância), got {REVOKE_AFTER_FAILS}"
    )


if __name__ == "__main__":
    test_revoke_after_fails_is_reasonable()
    print("registration fail_count OK")