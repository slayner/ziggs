"""Schemas Pydantic da API de craft (preferências pessoais, sem guilda)."""
from __future__ import annotations

from pydantic import BaseModel


class FocusEfficiencyIn(BaseModel):
    values: dict[str, int]  # familyKey -> pontos de focus efficiency
