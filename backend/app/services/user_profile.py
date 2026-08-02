"""Perfil customizado do usuário: tema, avatar e banner.

Desbloqueado só depois de verificar um personagem (RegisteredCharacter, via
/claims — o /register do bot NÃO conta, é só filiação de guilda, sem prova de
posse). Aplicado ao perfil de jogador (PlayerProfilePage) de CADA personagem
verificado desse usuário — ver get_public_customization, chamado por
app/api/routes/players.py.
"""
from __future__ import annotations

import io
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageSequence
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.claims import RegisteredCharacter
from app.models.audit import AuditLog
from app.models.profile_media import ProfileMediaSubmission
from app.models.tenancy import User

_IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "_profile_images")
_MAX_BYTES = {"avatar": 25 * 1024 * 1024, "banner": 100 * 1024 * 1024}
# Dimensão MÍNIMA da imagem ORIGINAL (antes do crop) — espelhada no frontend
# (IMG_LIMITS em ClaimsPanel.tsx), ver docs/PLANO-PERFIL-V2.md.
_MIN_SIZE = {"avatar": (128, 128), "banner": (320, 100)}
# (largura, altura) máximas — thumbnail() preserva proporção, nunca estica.
_MAX_SIZE = {"avatar": (512, 512), "banner": (1920, 600)}
_MAX_GIF_FRAMES = 400  # acima disso trunca (não rejeita) — segura memória/CPU
_MAX_GIF_PIXELS = 25_000_000  # pixels somados após crop/resize, antes de alocar frames
_MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # precisa caber no upload básico do Discord
THEMES = {"gold", "blue", "green", "red", "purple", "teal"}
DEFAULT_THEME = "gold"
MEDIA_BLOCK_DAYS = 90

# Retângulo de crop como frações 0..1 da imagem original: (x, y, w, h).
Crop = tuple[float, float, float, float]


class ProfileServiceError(Exception):
    pass


def is_verified(db: Session, user_id: int) -> bool:
    return db.scalar(select(RegisteredCharacter.id).where(RegisteredCharacter.user_id == user_id)) is not None


def _crop_box(size: tuple[int, int], crop: Crop) -> tuple[int, int, int, int]:
    """Frações 0..1 → box em px (clampado dentro da imagem)."""
    w, h = size
    x0 = min(max(crop[0], 0.0), 1.0) * w
    y0 = min(max(crop[1], 0.0), 1.0) * h
    x1 = min(max(crop[0] + crop[2], 0.0), 1.0) * w
    y1 = min(max(crop[1] + crop[3], 0.0), 1.0) * h
    box = (round(x0), round(y0), round(x1), round(y1))
    if box[2] - box[0] < 1 or box[3] - box[1] < 1:
        raise ProfileServiceError("crop inválido")
    return box


def _save_gif(img: Image.Image, kind: str, crop_px: tuple[int, int, int, int] | None, out_path: str) -> None:
    """GIF animado: crop/resize frame a frame, preservando durações e loop —
    é por isso que o crop acontece no servidor (re-encodar GIF no browser é
    inviável). Pillow ≥9.1 já entrega cada frame composto (disposal resolvido)."""
    frames: list[Image.Image] = []
    durations: list[int] = []
    for i, frame in enumerate(ImageSequence.Iterator(img)):
        if i >= _MAX_GIF_FRAMES:
            break
        f = frame.convert("RGBA")
        if crop_px:
            f = f.crop(crop_px)
        f.thumbnail(_MAX_SIZE[kind], Image.LANCZOS)
        # ponytail: transparência de GIF vira fundo escuro (quantização sem
        # alpha); tratar máscara de transparência se alguém reclamar.
        frames.append(f.convert("P", palette=Image.ADAPTIVE))
        durations.append(frame.info.get("duration", 100))
    kwargs: dict = {"save_all": True, "append_images": frames[1:], "duration": durations, "disposal": 2}
    loop = img.info.get("loop")
    if loop is not None:  # sem netscape extension = toca 1 vez; não force loop
        kwargs["loop"] = loop
    frames[0].save(out_path, "GIF", **kwargs)


def _save_image(
    kind: str, user_id: int, image_bytes: bytes, crop: Crop | None = None,
    stem: str | None = None,
) -> str:
    limit = _MAX_BYTES[kind]
    if len(image_bytes) > limit:
        raise ProfileServiceError(f"imagem grande demais (limite {limit // (1024 * 1024)} MB)")
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()  # Image.open é lazy — só decodifica de verdade aqui, é onde arquivo corrompido/não-imagem falha (inclui DecompressionBombError)
    except Exception:
        raise ProfileServiceError("arquivo não é uma imagem válida")

    min_w, min_h = _MIN_SIZE[kind]
    if img.width < min_w or img.height < min_h:
        raise ProfileServiceError(f"imagem muito pequena (mínimo {min_w}×{min_h})")

    crop_px = _crop_box(img.size, crop) if crop else None

    animated = img.format == "GIF" and getattr(img, "is_animated", False)
    if animated:
        frame_w = crop_px[2] - crop_px[0] if crop_px else img.width
        frame_h = crop_px[3] - crop_px[1] if crop_px else img.height
        scale = min(1.0, _MAX_SIZE[kind][0] / frame_w, _MAX_SIZE[kind][1] / frame_h)
        pixels = round(frame_w * scale) * round(frame_h * scale)
        if pixels * min(getattr(img, "n_frames", 1), _MAX_GIF_FRAMES) > _MAX_GIF_PIXELS:
            raise ProfileServiceError("GIF complexo demais para processar")

    sub = os.path.join(_IMAGES_DIR, str(user_id))
    os.makedirs(sub, exist_ok=True)
    # GIF animado mantém .gif (senão a animação morre); o resto vira .jpg.
    # Pendências usam stem único; os helpers legados mantêm nome fixo por kind.
    ext = "gif" if animated else "jpg"
    stem = stem or kind
    out_path = os.path.join(sub, f"{stem}.{ext}")

    try:
        if animated:
            _save_gif(img, kind, crop_px, out_path)
        else:
            if crop_px:
                img = img.crop(crop_px)
            img.thumbnail(_MAX_SIZE[kind], Image.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")  # RGBA/paleta não salva em JPEG
            img.save(out_path, "JPEG", quality=88)
        if os.path.getsize(out_path) > _MAX_OUTPUT_BYTES:
            raise ProfileServiceError("imagem processada excede 10 MB")
    except Exception as error:
        try:
            os.remove(out_path)
        except OSError:
            pass
        if isinstance(error, ProfileServiceError):
            raise
        raise ProfileServiceError("não foi possível processar a imagem") from error

    # A extensão varia por formato: apaga a irmã pra troca gif↔jpg nunca
    # deixar arquivo órfão (mesmo se o path no DB estiver dessincronizado).
    other = os.path.join(sub, f"{stem}.{'jpg' if animated else 'gif'}")
    if os.path.isfile(other):
        try:
            os.remove(other)
        except OSError:
            pass
    return f"{user_id}/{stem}.{ext}"


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


def purge_user_images(user_id: int) -> None:
    shutil.rmtree(os.path.join(_IMAGES_DIR, str(user_id)), ignore_errors=True)


def _active_block_until(user: User) -> datetime | None:
    blocked = user.profile_media_blocked_until
    if blocked is None:
        return None
    if blocked.tzinfo is None:
        blocked = blocked.replace(tzinfo=timezone.utc)
    return blocked if blocked > datetime.now(timezone.utc) else None


def _lock_user(db: Session, user_id: int) -> User | None:
    return db.scalar(
        select(User).where(User.id == user_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )


def submit_media(
    db: Session, user: User, kind: str, image_bytes: bytes, crop: Crop | None = None,
) -> ProfileMediaSubmission:
    if kind not in ("avatar", "banner"):
        raise ProfileServiceError("tipo de imagem inválido")
    user = _lock_user(db, user.id) or user
    blocked = _active_block_until(user)
    if blocked:
        raise ProfileServiceError(
            f"uploads bloqueados até {blocked.astimezone(timezone.utc).strftime('%d/%m/%Y')}"
        )
    existing = db.scalar(select(ProfileMediaSubmission).where(
        ProfileMediaSubmission.user_id == user.id,
        ProfileMediaSubmission.kind == kind,
    ))
    if existing is not None:
        raise ProfileServiceError(f"já existe {kind} aguardando aprovação")

    stem = f"pending-{kind}-{uuid.uuid4().hex}"
    rel = _save_image(kind, user.id, image_bytes, crop, stem=stem)
    submission = ProfileMediaSubmission(user_id=user.id, kind=kind, path=rel)
    db.add(submission)
    return submission


def remove_media(db: Session, user: User, kind: str) -> list[str]:
    user = _lock_user(db, user.id) or user
    submission = db.scalar(select(ProfileMediaSubmission).where(
        ProfileMediaSubmission.user_id == user.id,
        ProfileMediaSubmission.kind == kind,
    ))
    paths = [submission.path] if submission else []
    if submission:
        db.delete(submission)
    attr = "profile_avatar_path" if kind == "avatar" else "profile_banner_path"
    current = getattr(user, attr)
    if current:
        paths.append(current)
    setattr(user, attr, None)
    return paths


def approve_submission(db: Session, submission_id: int, actor_id: int) -> tuple[dict, str | None]:
    candidate = db.get(ProfileMediaSubmission, submission_id)
    if candidate is None:
        raise ProfileServiceError("upload pendente não encontrado")
    user = _lock_user(db, candidate.user_id)
    submission = db.scalar(select(ProfileMediaSubmission).where(
        ProfileMediaSubmission.id == submission_id,
    ).with_for_update())
    if submission is None:
        raise ProfileServiceError("upload pendente não encontrado")
    if user is None:
        raise ProfileServiceError("usuário não encontrado")

    attr = "profile_avatar_path" if submission.kind == "avatar" else "profile_banner_path"
    old_path = getattr(user, attr)
    setattr(user, attr, submission.path)
    db.add(AuditLog(
        guild_id=0, actor_id=actor_id, actor_type="bot", source="bot",
        action="profile_media.approve", entity="profile_media", entity_id=str(submission.id),
        before={"kind": submission.kind, "path": old_path},
        after={"kind": submission.kind, "path": submission.path},
    ))
    out = {"decision": "approved", "user_id": user.id, "kind": submission.kind}
    db.delete(submission)
    return out, old_path


def reject_submission(db: Session, submission_id: int, actor_id: int) -> dict:
    candidate = db.get(ProfileMediaSubmission, submission_id)
    if candidate is None:
        raise ProfileServiceError("upload pendente não encontrado")
    user = _lock_user(db, candidate.user_id)
    submission = db.scalar(select(ProfileMediaSubmission).where(
        ProfileMediaSubmission.id == submission_id,
    ).with_for_update())
    if submission is None:
        raise ProfileServiceError("upload pendente não encontrado")
    if user is None:
        raise ProfileServiceError("usuário não encontrado")

    blocked_until = datetime.now(timezone.utc) + timedelta(days=MEDIA_BLOCK_DAYS)
    user.profile_media_blocked_until = blocked_until
    user.profile_avatar_path = None
    user.profile_banner_path = None
    pending = db.scalars(select(ProfileMediaSubmission).where(
        ProfileMediaSubmission.user_id == user.id,
    ).with_for_update()).all()
    message_ids = [str(item.discord_message_id) for item in pending if item.discord_message_id]
    for item in pending:
        db.delete(item)
    db.add(AuditLog(
        guild_id=0, actor_id=actor_id, actor_type="bot", source="bot",
        action="profile_media.reject", entity="profile_media", entity_id=str(submission.id),
        before={"kind": submission.kind},
        after={"blocked_until": blocked_until.isoformat(), "all_images_removed": True},
    ))
    return {
        "decision": "rejected", "user_id": user.id, "kind": submission.kind,
        "blocked_until": blocked_until.isoformat(), "discord_message_ids": message_ids,
    }


def set_theme(user: User, theme: str) -> None:
    if theme not in THEMES:
        raise ProfileServiceError(f"tema inválido; opções: {', '.join(sorted(THEMES))}")
    user.profile_theme = theme


def set_avatar(user: User, image_bytes: bytes, crop: Crop | None = None) -> None:
    _remove_existing(user.profile_avatar_path)
    user.profile_avatar_path = _save_image("avatar", user.id, image_bytes, crop)


def remove_avatar(user: User) -> None:
    _remove_existing(user.profile_avatar_path)
    user.profile_avatar_path = None


def set_banner(user: User, image_bytes: bytes, crop: Crop | None = None) -> None:
    _remove_existing(user.profile_banner_path)
    user.profile_banner_path = _save_image("banner", user.id, image_bytes, crop)


def remove_banner(user: User) -> None:
    _remove_existing(user.profile_banner_path)
    user.profile_banner_path = None


def _image_url(user_id: int, rel: str | None, kind: str) -> str | None:
    if not rel:
        return None
    # ?v=mtime — sem isso, reenviar avatar/banner mantém a MESMA URL
    # (nome fixo por kind) e o browser continua mostrando a imagem velha do
    # cache até um hard refresh.
    try:
        v = int(os.path.getmtime(image_abs_path(rel)))
    except OSError:
        v = 0
    return f"/profile/image/{kind}/{user_id}?v={v}"


def my_profile_dict(db: Session, user: User) -> dict:
    pending = db.scalars(select(ProfileMediaSubmission.kind).where(
        ProfileMediaSubmission.user_id == user.id,
    )).all()
    blocked = _active_block_until(user)
    return {
        "theme": user.profile_theme or DEFAULT_THEME,
        "avatar_url": _image_url(user.id, user.profile_avatar_path, "avatar"),
        "banner_url": _image_url(user.id, user.profile_banner_path, "banner"),
        "pending_kinds": list(pending),
        "blocked_until": blocked.isoformat() if blocked else None,
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
    # Alt → aponta pra main do mesmo dono (badge "Alt de X" no perfil).
    main_character = None
    if not reg.is_main:
        m = db.scalar(select(RegisteredCharacter).where(
            RegisteredCharacter.user_id == reg.user_id,
            RegisteredCharacter.is_main == True,  # noqa: E712
        ))
        if m is not None:
            main_character = {"name": m.albion_player_name, "region": m.region}
    return {
        "theme": user.profile_theme or DEFAULT_THEME,
        "avatar_url": _image_url(user.id, user.profile_avatar_path, "avatar"),
        "banner_url": _image_url(user.id, user.profile_banner_path, "banner"),
        "is_main": reg.is_main,
        "main_character": main_character,
    }
