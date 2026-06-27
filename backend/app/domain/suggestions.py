"""
Motor de SUGESTÃO de build — a "gourmetzada" das composições.

Ideia (da visão): cada arma tem uma "função invisível" (support, tank, healer,
dps_melee, ...). Funções da mesma família costumam usar o mesmo equipamento. Então,
ao preencher um slot, sugerimos a build a partir das funções JÁ presentes na comp
que compartilham a mesma função invisível.

Isto é lógica PURA (sem ORM, sem web): recebe dicts simples e devolve uma
sugestão + a confiança baseada em quantas funções concordaram. A camada de
serviço mapeia ORM → estes dicts.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

# Peças que a sugestão tenta preencher (a arma em si NÃO é sugerida — ela é a
# entrada). Espelha os campos de build de GameRole.
SUGGESTABLE_FIELDS = (
    "offhand", "helmet", "armor", "boots", "cape", "food",
    "abilities", "play_style",
)


@dataclass(frozen=True)
class RoleBuild:
    """Uma função existente na comp, achatada para o que importa à sugestão."""
    invisible_function: str | None
    offhand: str | None = None
    helmet: str | None = None
    armor: str | None = None
    boots: str | None = None
    cape: str | None = None
    food: str | None = None
    abilities: str | None = None
    play_style: str | None = None


@dataclass
class FieldSuggestion:
    value: str
    votes: int        # quantas funções da família usam esse valor
    total: int        # total de funções da família com esse campo preenchido


@dataclass
class BuildSuggestion:
    target_function: str
    sample_size: int                                   # nº de funções da família
    fields: dict[str, FieldSuggestion] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.fields


def _norm(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def suggest_build(
    target_function: str | None,
    existing_roles: list[RoleBuild],
) -> BuildSuggestion:
    """
    Sugere uma build para um slot da função `target_function`, olhando as funções
    da comp que têm a MESMA função invisível.

    Para cada peça, propõe o valor mais comum (votação) e diz quantos concordaram —
    a UI pode mostrar isso como confiança e deixar o usuário aceitar/editar.
    """
    target = _norm(target_function)
    suggestion = BuildSuggestion(target_function=target or "", sample_size=0)
    if not target:
        return suggestion

    family = [r for r in existing_roles if _norm(r.invisible_function) == target]
    suggestion.sample_size = len(family)
    if not family:
        return suggestion

    for fname in SUGGESTABLE_FIELDS:
        values = [_norm(getattr(r, fname)) for r in family]
        values = [v for v in values if v]
        if not values:
            continue
        value, votes = Counter(values).most_common(1)[0]
        suggestion.fields[fname] = FieldSuggestion(
            value=value, votes=votes, total=len(values)
        )

    return suggestion
