"""
Criptografia em repouso para tokens de terceiros (ex.: discord_access_token).

Chave derivada do SECRET_KEY já existente (SHA-256 -> base64 urlsafe, formato
exigido pelo Fernet) em vez de pedir mais um segredo no .env pra gerenciar.
Token corrompido, vazio ou de antes desta mudança (texto puro) -> decrypt_token
devolve None em vez de quebrar; quem chama já trata "sem token" como
"faça login de novo".
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(get_settings().secret_key.encode()).digest())
    return Fernet(key)


def encrypt_token(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_token(enc: str | None) -> str | None:
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except InvalidToken:
        return None


if __name__ == "__main__":
    enc = encrypt_token("abc123")
    assert enc != "abc123"
    assert decrypt_token(enc) == "abc123"
    assert decrypt_token(None) is None
    assert decrypt_token("") is None
    assert decrypt_token("token-de-antes-da-criptografia") is None  # legado em texto puro
    print("crypto OK")
