"""Rotas de reivindicação e registro de personagens."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.models.claims import CLAIM_COOLDOWN, CLAIM_EXPIRY, CharacterClaim, RegisteredCharacter
from app.models.tenancy import User
from app.services.challenge_pool import generate_challenge

router = APIRouter(prefix="/claims", tags=["claims"])


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ClaimRequest(BaseModel):
    albion_player_id: str
    albion_player_name: str
    region: str  # americas | europe | asia


@router.post("/character")
def create_claim(
    body: ClaimRequest,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    """Cria uma nova reivindicação de personagem para o usuário logado.

    Várias pessoas diferentes podem ter uma claim pra mesmo personagem (não é
    posse exclusiva até verificar) — o que é exclusivo é o PRÓPRIO usuário não
    poder empilhar tentativas: enquanto a claim mais recente dele pra esse
    personagem estiver pendente (dentro das 24h) ou em cooldown (até 30 dias
    depois de expirar sem cumprir), uma nova claim é bloqueada.
    """
    now = datetime.now(timezone.utc)
    last = db.scalar(
        select(CharacterClaim)
        .where(
            CharacterClaim.user_id == user.id,
            CharacterClaim.albion_player_id == body.albion_player_id,
        )
        .order_by(CharacterClaim.created_at.desc())
        .limit(1)
    )
    if last is not None and last.status != "verified":
        created = _aware(last.created_at)
        expired = last.status == "expired" or (now - created > CLAIM_EXPIRY)
        if not expired:
            restante = CLAIM_EXPIRY - (now - created)
            horas = max(1, int(restante.total_seconds() // 3600))
            raise HTTPException(409, f"Já existe uma verificação em andamento para esse personagem (expira em {horas}h).")
        cooldown_until = created + CLAIM_EXPIRY + CLAIM_COOLDOWN
        if now < cooldown_until:
            dias = max(1, (cooldown_until - now).days)
            raise HTTPException(409, f"A verificação anterior desse personagem não foi cumprida em 24h. Tente de novo em {dias} dia(s).")

    challenge = generate_challenge()
    claim = CharacterClaim(
        user_id=user.id,
        albion_player_id=body.albion_player_id,
        albion_player_name=body.albion_player_name,
        region=body.region,
        challenge=challenge,
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return _claim_dict(claim)


@router.get("/my")
def my_claims(
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    """Lista claims e personagens registrados do usuário logado."""
    claims = db.scalars(
        select(CharacterClaim)
        .where(CharacterClaim.user_id == user.id)
        .order_by(CharacterClaim.created_at.desc())
    ).all()

    registered = db.scalars(
        select(RegisteredCharacter).where(RegisteredCharacter.user_id == user.id)
    ).all()

    return {
        "claims": [_claim_dict(c) for c in claims],
        "registered": [_reg_dict(r) for r in registered],
    }


@router.put("/main/{registered_id}")
def set_main_character(
    registered_id: int,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    """Define qual personagem registrado é a conta principal do usuário.
    Devolve o mesmo shape do GET /claims/my (o frontend substitui a lista)."""
    target = db.get(RegisteredCharacter, registered_id)
    if target is None or target.user_id != user.id:
        raise HTTPException(404, "Personagem não encontrado")
    for rc in db.scalars(
        select(RegisteredCharacter).where(RegisteredCharacter.user_id == user.id)
    ):
        rc.is_main = rc.id == registered_id
    db.commit()
    return my_claims(user, db)


@router.get("/character/{claim_id}")
def get_claim(
    claim_id: int,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
) -> dict:
    claim = db.scalar(select(CharacterClaim).where(CharacterClaim.id == claim_id))
    if not claim or claim.user_id != user.id:
        raise HTTPException(404, "Claim não encontrado")
    return _claim_dict(claim)


def _claim_dict(c: CharacterClaim) -> dict:
    return {
        "id": c.id,
        "albion_player_id": c.albion_player_id,
        "albion_player_name": c.albion_player_name,
        "region": c.region,
        "challenge": c.challenge,
        "status": c.status,
        # _aware: sem isso, SQLite devolve datetime "naive" e o isoformat() sai
        # sem offset — o frontend então interpretaria como hora LOCAL do
        # usuário em vez de UTC, bagunçando o cálculo de "expira em Xh".
        "created_at": _aware(c.created_at).isoformat() if c.created_at else None,
        "verified_at": _aware(c.verified_at).isoformat() if c.verified_at else None,
    }


def _reg_dict(r: RegisteredCharacter) -> dict:
    return {
        "id": r.id,
        "albion_player_id": r.albion_player_id,
        "albion_player_name": r.albion_player_name,
        "region": r.region,
        "registered_at": r.registered_at.isoformat() if r.registered_at else None,
        "is_main": r.is_main,
    }
