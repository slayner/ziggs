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

from PIL import Image, ImageSequence
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.claims import RegisteredCharacter
from app.models.tenancy import User

_IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "_profile_images")
_MAX_BYTES = {"avatar": 25 * 1024 * 1024, "banner": 100 * 1024 * 1024}
# Dimensão MÍNIMA da imagem ORIGINAL (antes do crop) — espelhada no frontend
# (IMG_LIMITS em ClaimsPanel.tsx), ver docs/PLANO-PERFIL-V2.md.
_MIN_SIZE = {"avatar": (128, 128), "banner": (320, 100)}
# (largura, altura) máximas — thumbnail() preserva proporção, nunca estica.
_MAX_SIZE = {"avatar": (512, 512), "banner": (1920, 600)}
_MAX_GIF_FRAMES = 400  # acima disso trunca (não rejeita) — segura memória/CPU
THEMES = {"gold", "blue", "green", "red", "purple", "teal"}
DEFAULT_THEME = "gold"

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


def _save_image(kind: str, user_id: int, image_bytes: bytes, crop: Crop | None = None) -> str:
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

    sub = os.path.join(_IMAGES_DIR, str(user_id))
    os.makedirs(sub, exist_ok=True)
    # Nome fixo por kind — reenviar sobrescreve, sem lixo acumulando. GIF
    # animado mantém .gif (senão a animação morre); o resto vira .jpg.
    animated = img.format == "GIF" and getattr(img, "is_animated", False)
    ext = "gif" if animated else "jpg"
    out_path = os.path.join(sub, f"{kind}.{ext}")

    if animated:
        _save_gif(img, kind, crop_px, out_path)
    else:
        if crop_px:
            img = img.crop(crop_px)
        img.thumbnail(_MAX_SIZE[kind], Image.LANCZOS)
        if img.mode != "RGB":
            img = img.convert("RGB")  # RGBA/paleta não salva em JPEG
        img.save(out_path, "JPEG", quality=88)

    # A extensão varia por formato: apaga a irmã pra troca gif↔jpg nunca
    # deixar arquivo órfão (mesmo se o path no DB estiver dessincronizado).
    other = os.path.join(sub, f"{kind}.{'jpg' if animated else 'gif'}")
    if os.path.isfile(other):
        try:
            os.remove(other)
        except OSError:
            pass
    return f"{user_id}/{kind}.{ext}"


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
