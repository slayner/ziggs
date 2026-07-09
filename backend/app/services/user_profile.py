"""Perfil customizado do usuário: tema, avatar e banner.

Desbloqueado só depois de verificar um personagem (RegisteredCharacter, via
/claims — o /register do bot NÃO conta, é só filiação de guilda, sem prova de
posse). Aplicado ao perfil de jogador (PlayerProfilePage) de CADA personagem
verificado desse usuário — ver get_public_customization, chamado por
app/api/routes/players.py.
"""
from __future__ import annotations

import os
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.claims import RegisteredCharacter
from app.models.tenancy import User

_IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "_profile_images")
_VALID_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
THEMES = {"gold", "blue", "green", "red", "purple", "teal"}
DEFAULT_THEME = "gold"


class ProfileServiceError(Exception):
    pass


def is_verified(db: Session, user_id: int) -> bool:
    return db.scalar(select(RegisteredCharacter.id).where(RegisteredCharacter.user_id == user_id)) is not None


def _ext(filename: str, content_type: str | None) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in _VALID_EXT:
        ct = (content_type or "").lower()
        ext = ".png" if "png" in ct else ".jpg" if "jpeg" in ct or "jpg" in ct else ".webp" if "webp" in ct else ""
    if ext not in _VALID_EXT:
        raise ProfileServiceError("imagem precisa ser .png, .jpg ou .webp")
    return ext


def _save_image(kind: str, user_id: int, image_bytes: bytes, filename: str, content_type: str | None) -> str:
    if len(image_bytes) > _MAX_BYTES:
        raise ProfileServiceError("imagem grande demais (limite 5 MB)")
    ext = _ext(filename, content_type)
    sub = os.path.join(_IMAGES_DIR, str(user_id))
    os.makedirs(sub, exist_ok=True)
    # nome fixo por kind (não uuid) — reenviar sobrescreve, sem lixo acumulando.
    rel_name = f"{kind}{ext}"
    with open(os.path.join(sub, rel_name), "wb") as f:
        f.write(image_bytes)
    return f"{user_id}/{rel_name}"


def image_abs_path(rel: str) -> str:
    return os.path.join(_IMAGES_DIR, rel)


def _remove_existing(rel: str | None) -> None:
    if not rel:
        return
    path = image_abs_path(rel)
    if path.startswith(_IMAGES_DIR) and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def set_theme(user: User, theme: str) -> None:
    if theme not in THEMES:
        raise ProfileServiceError(f"tema inválido; opções: {', '.join(sorted(THEMES))}")
    user.profile_theme = theme


def set_avatar(user: User, image_bytes: bytes, filename: str, content_type: str | None) -> None:
    _remove_existing(user.profile_avatar_path)
    user.profile_avatar_path = _save_image("avatar", user.id, image_bytes, filename, content_type)


def remove_avatar(user: User) -> None:
    _remove_existing(user.profile_avatar_path)
    user.profile_avatar_path = None


def set_banner(user: User, image_bytes: bytes, filename: str, content_type: str | None) -> None:
    _remove_existing(user.profile_banner_path)
    user.profile_banner_path = _save_image("banner", user.id, image_bytes, filename, content_type)


def remove_banner(user: User) -> None:
    _remove_existing(user.profile_banner_path)
    user.profile_banner_path = None


def _image_url(user_id: int, rel: str | None, kind: str) -> str | None:
    return f"/profile/image/{kind}/{user_id}" if rel else None


def my_profile_dict(user: User) -> dict:
    return {
        "theme": user.profile_theme or DEFAULT_THEME,
        "avatar_url": _image_url(user.id, user.profile_avatar_path, "avatar"),
        "banner_url": _image_url(user.id, user.profile_banner_path, "banner"),
    }


def get_public_customization(db: Session, albion_player_id: str) -> dict | None:
    """Customização do dono verificado de `albion_player_id`, ou None se o
    personagem não tem RegisteredCharacter (não verificado no site)."""
    reg = db.scalar(select(RegisteredCharacter).where(RegisteredCharacter.albion_player_id == albion_player_id))
    if reg is None:
        return None
    user = db.get(User, reg.user_id)
    if user is None:
        return None
    return {
        "theme": user.profile_theme or DEFAULT_THEME,
        "avatar_url": _image_url(user.id, user.profile_avatar_path, "avatar"),
        "banner_url": _image_url(user.id, user.profile_banner_path, "banner"),
    }
