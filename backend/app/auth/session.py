"""
Sessão do site = cookie ASSINADO (itsdangerous), sem estado no servidor.

Guarda só o id do usuário do Discord, assinado com SECRET_KEY e com expiração.
Cookie adulterado ou expirado -> verify_session devolve None.
"""
from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

_SALT = "ziggs-session-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt=_SALT)


def make_session(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def verify_session(token: str | None) -> int | None:
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=get_settings().session_max_age)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    return int(uid) if uid is not None else None
