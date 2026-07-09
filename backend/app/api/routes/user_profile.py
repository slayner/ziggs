"""Rotas do perfil customizado do usuário (tema, avatar, banner).

Escrita exige personagem verificado (ver app/services/user_profile.py); leitura
das imagens é pública (o perfil de jogador é visto por qualquer visitante).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import deps
from app.models.tenancy import User
from app.services import user_profile

router = APIRouter(prefix="/profile", tags=["profile"])


def _require_verified(db: Session, user: User) -> None:
    if not user_profile.is_verified(db, user.id):
        raise HTTPException(403, "verifique um personagem em 'Meus personagens' antes de customizar o perfil")


@router.get("/me")
def get_my_profile(
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    return {
        **user_profile.my_profile_dict(user),
        "verified": user_profile.is_verified(db, user.id),
    }


class ThemeIn(BaseModel):
    theme: str


@router.put("/theme")
def put_theme(
    body: ThemeIn,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    _require_verified(db, user)
    try:
        user_profile.set_theme(user, body.theme)
    except user_profile.ProfileServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return user_profile.my_profile_dict(user)


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    _require_verified(db, user)
    data = await file.read()
    if not data:
        raise HTTPException(400, "arquivo vazio")
    try:
        user_profile.set_avatar(user, data, file.filename or "avatar.png", file.content_type)
    except user_profile.ProfileServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return user_profile.my_profile_dict(user)


@router.delete("/avatar")
def delete_avatar(
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    user_profile.remove_avatar(user)
    db.commit()
    return user_profile.my_profile_dict(user)


@router.post("/banner")
async def upload_banner(
    file: UploadFile = File(...),
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    _require_verified(db, user)
    data = await file.read()
    if not data:
        raise HTTPException(400, "arquivo vazio")
    try:
        user_profile.set_banner(user, data, file.filename or "banner.png", file.content_type)
    except user_profile.ProfileServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return user_profile.my_profile_dict(user)


@router.delete("/banner")
def delete_banner(
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    user_profile.remove_banner(user)
    db.commit()
    return user_profile.my_profile_dict(user)


# ── imagens (públicas — o perfil de jogador é visto por qualquer visitante) ──

@router.get("/image/{kind}/{user_id}")
def serve_image(kind: str, user_id: int, db: Session = Depends(deps.db_session)):
    if kind not in ("avatar", "banner"):
        raise HTTPException(404, "não encontrado")
    user = db.get(User, user_id)
    rel = (user.profile_avatar_path if kind == "avatar" else user.profile_banner_path) if user else None
    if not rel:
        raise HTTPException(404, "não encontrado")
    path = user_profile.image_abs_path(rel)
    if not path.startswith(user_profile._IMAGES_DIR) or not os.path.isfile(path):
        raise HTTPException(404, "não encontrado")
    return FileResponse(path)
