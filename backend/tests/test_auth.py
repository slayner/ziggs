"""
Testes de auth que NÃO precisam de rede nem banco: construção da URL de
autorização e o round-trip do cookie de sessão assinado. Roda direto:
    PYTHONPATH=. python tests/test_auth.py
"""
from urllib.parse import parse_qs, urlparse

from app.auth import discord
from app.auth.session import make_session, verify_session


def test_authorize_url_tem_os_parametros():
    url = discord.build_authorize_url("xyz-state")
    q = parse_qs(urlparse(url).query)
    assert q["response_type"] == ["code"]
    assert q["state"] == ["xyz-state"]
    assert q["client_id"][0]            # veio do .env
    assert {"identify", "guilds", "email"} <= set(q["scope"][0].split())
    assert q["redirect_uri"][0].endswith("/auth/discord/callback")


def test_sessao_round_trip():
    token = make_session(123456789)
    assert verify_session(token) == 123456789


def test_sessao_adulterada_eh_rejeitada():
    token = make_session(42)
    assert verify_session(token + "x") is None
    assert verify_session("lixo") is None
    assert verify_session(None) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("todos passaram")
