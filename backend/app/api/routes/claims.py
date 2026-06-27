"""Rotas de reivindicação e registro de personagens."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.models.claims import CharacterClaim, RegisteredCharacter
from app.models.tenancy import User
from app.services.challenge_pool import generate_challenge

router = APIRouter(prefix="/claims", tags=["claims"])


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
    """Cria uma nova reivindicação de personagem para o usuário logado."""
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
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "verified_at": c.verified_at.isoformat() if c.verified_at else None,
    }


def _reg_dict(r: RegisteredCharacter) -> dict:
    return {
        "id": r.id,
        "albion_player_id": r.albion_player_id,
        "albion_player_name": r.albion_player_name,
        "region": r.region,
        "registered_at": r.registered_at.isoformat() if r.registered_at else None,
    }
