"""Rotas do perfil customizado do usuário (tema, avatar, banner).

Escrita exige personagem verificado (ver app/services/user_profile.py); leitura
das imagens é pública (o perfil de jogador é visto por qualquer visitante).
"""
from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import deps
from app.models.profile_media import ProfileMediaSubmission
from app.models.tenancy import User
from app.services import user_profile

router = APIRouter(prefix="/profile", tags=["profile"])
bot_router = APIRouter(prefix="/bot/profile-moderation", tags=["bot"])


def _require_verified(db: Session, user: User) -> None:
    if not user_profile.is_verified(db, user.id):
        raise HTTPException(403, "verifique um personagem em 'Meus personagens' antes de customizar o perfil")


def _profile_response(db: Session, user: User) -> dict:
    return {
        **user_profile.my_profile_dict(db, user),
        "verified": user_profile.is_verified(db, user.id),
    }


@router.get("/me")
def get_my_profile(
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    return _profile_response(db, user)


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
    return _profile_response(db, user)


def _crop_tuple(x: float | None, y: float | None, w: float | None, h: float | None) -> user_profile.Crop | None:
    if None in (x, y, w, h):
        return None
    return (x, y, w, h)


# Uploads são `def` (não async) DE PROPÓSITO: FastAPI roda rota sync em
# threadpool, então re-encodar um GIF de 100 MB no Pillow não congela o event
# loop onde rodam battle_tracker e os demais serviços de fundo.
@router.post("/avatar")
def upload_avatar(
    file: UploadFile = File(...),
    crop_x: float | None = Form(None),
    crop_y: float | None = Form(None),
    crop_w: float | None = Form(None),
    crop_h: float | None = Form(None),
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    _require_verified(db, user)
    data = file.file.read()
    if not data:
        raise HTTPException(400, "arquivo vazio")
    submission = None
    try:
        submission = user_profile.submit_media(
            db, user, "avatar", data, _crop_tuple(crop_x, crop_y, crop_w, crop_h),
        )
        db.commit()
    except user_profile.ProfileServiceError as e:
        raise HTTPException(400, str(e))
    except IntegrityError:
        db.rollback()
        if submission:
            user_profile._remove_existing(submission.path)
        raise HTTPException(409, "já existe avatar aguardando aprovação")
    except Exception:
        db.rollback()
        if submission:
            user_profile._remove_existing(submission.path)
        raise
    return _profile_response(db, user)


@router.delete("/avatar")
def delete_avatar(
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    paths = user_profile.remove_media(db, user, "avatar")
    db.commit()
    for path in paths:
        user_profile._remove_existing(path)
    return _profile_response(db, user)


@router.post("/banner")
def upload_banner(
    file: UploadFile = File(...),
    crop_x: float | None = Form(None),
    crop_y: float | None = Form(None),
    crop_w: float | None = Form(None),
    crop_h: float | None = Form(None),
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    _require_verified(db, user)
    data = file.file.read()
    if not data:
        raise HTTPException(400, "arquivo vazio")
    submission = None
    try:
        submission = user_profile.submit_media(
            db, user, "banner", data, _crop_tuple(crop_x, crop_y, crop_w, crop_h),
        )
        db.commit()
    except user_profile.ProfileServiceError as e:
        raise HTTPException(400, str(e))
    except IntegrityError:
        db.rollback()
        if submission:
            user_profile._remove_existing(submission.path)
        raise HTTPException(409, "já existe banner aguardando aprovação")
    except Exception:
        db.rollback()
        if submission:
            user_profile._remove_existing(submission.path)
        raise
    return _profile_response(db, user)


@router.delete("/banner")
def delete_banner(
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    paths = user_profile.remove_media(db, user, "banner")
    db.commit()
    for path in paths:
        user_profile._remove_existing(path)
    return _profile_response(db, user)


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


# ── fila privada consumida pelo bot oficial ──────────────────────────────────

@bot_router.get("/pending", dependencies=[Depends(deps.require_bot_api)])
def moderation_pending(db: Session = Depends(deps.db_session)) -> dict:
    rows = db.scalars(select(ProfileMediaSubmission).order_by(ProfileMediaSubmission.created_at)).all()
    submissions = []
    for row in rows:
        user = db.get(User, row.user_id)
        if user is None:
            continue
        submissions.append({
            "id": row.id,
            "user_id": str(row.user_id),
            "username": user.global_name or user.username,
            "kind": row.kind,
            "image_name": os.path.basename(row.path),
            "discord_message_id": str(row.discord_message_id) if row.discord_message_id else None,
            "created_at": row.created_at.isoformat(),
        })
    return {"submissions": submissions}


@bot_router.get("/{submission_id}/image", dependencies=[Depends(deps.require_bot_api)])
def moderation_image(submission_id: int, db: Session = Depends(deps.db_session)):
    submission = db.get(ProfileMediaSubmission, submission_id)
    if submission is None:
        raise HTTPException(404, "upload pendente não encontrado")
    path = user_profile.image_abs_path(submission.path)
    if not path.startswith(user_profile._IMAGES_DIR) or not os.path.isfile(path):
        raise HTTPException(404, "imagem não encontrada")
    return FileResponse(path, filename=os.path.basename(path))


class ModerationMessageIn(BaseModel):
    message_id: int | None


@bot_router.post("/{submission_id}/message", dependencies=[Depends(deps.require_bot_api)])
def moderation_message(
    submission_id: int,
    body: ModerationMessageIn,
    db: Session = Depends(deps.db_session),
) -> dict:
    submission = db.get(ProfileMediaSubmission, submission_id)
    if submission is None:
        raise HTTPException(404, "upload pendente não encontrado")
    submission.discord_message_id = body.message_id
    db.commit()
    return {"ok": True}


class ModerationDecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    actor_id: int


@bot_router.post("/{submission_id}/decision", dependencies=[Depends(deps.require_bot_api)])
def moderation_decision(
    submission_id: int,
    body: ModerationDecisionIn,
    db: Session = Depends(deps.db_session),
) -> dict:
    try:
        if body.decision == "approve":
            result, old_path = user_profile.approve_submission(db, submission_id, body.actor_id)
            db.commit()
            user_profile._remove_existing(old_path)
            return result
        result = user_profile.reject_submission(db, submission_id, body.actor_id)
        db.commit()
        user_profile.purge_user_images(result["user_id"])
        return result
    except user_profile.ProfileServiceError as e:
        raise HTTPException(404, str(e))
