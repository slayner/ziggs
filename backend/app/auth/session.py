"""
Sessão do site = cookie ASSINADO (itsdangerous), sem estado no servidor.

Guarda só o id do usuário do Discord, assinado com SECRET_KEY e com expiração.
Cookie adulterado ou expirado -> verify_session devolve None.
"""
from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

_SALT = "ziggs-session-v1"
_COMPANION_SALT = "ziggs-companion-token-v1"
_COMPANION_MAX_AGE = 60 * 60 * 24 * 30  # 30 dias


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt=_SALT)


def _companion_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt=_COMPANION_SALT)


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


def make_companion_token(user_id: int) -> str:
    """Token de portador (bearer) para o app companion — assinado, 30 dias de validade.
    Diferente do cookie de sessão: vida mais longa, salt diferente, usado como header
    Authorization pelo companion em vez de cookie httpOnly."""
    return _companion_serializer().dumps({"uid": user_id})


def verify_companion_token(token: str | None) -> int | None:
    if not token:
        return None
    try:
        data = _companion_serializer().loads(token, max_age=_COMPANION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    return int(uid) if uid is not None else None
