"""Gate de inscrição em eventos: cascata de abertura de parties por
quantidade + gate por cargo do Discord — porta de `bot/cogs/massinfo.py`
(`_quantity_gate`/`_allowed_roles`) pro backend, usando a árvore
Comp -> CompParty -> CompSlot -> CompSlotRole -> GameRole do site em vez da
planilha/escalação do bot antigo.

Granularidade: a "função" que aparece no picker é `GameRole.name` (não
`CompSlot.fn`, que é só a categoria da vaga, nem `CompSlot.label`, texto
livre). Categoria de agrupamento pro picker do bot vem de
`GameRole.weapon_id -> Weapon.invisible_function` — o bot antigo usava um
emoji prefixado no nome da função, mas os `GameRole` reais daqui não têm essa
convenção (conferido direto no banco).

Uma `CompSlot` = uma vaga (capacidade 1), não importa quantas `GameRole` ela
aceita via `CompSlotRole`. "Preenchida" é uma aproximação: conta usuários
distintos cuja primeira escolha (`function_1`) bate com alguma `GameRole` da
party, até o limite de vagas da party — não há atribuição de vaga específica
(o bot antigo tinha isso via célula nomeada da planilha; aqui é
auto-inscrição, não escalação manual).

Módulo sem I/O — recebe estruturas já montadas pelo chamador
(`app/services/event_signups.py`), pra ficar 100% testável sem sessão de
banco."""
from __future__ import annotations

from dataclasses import dataclass, field

# Mesmos limiares do bot antigo (bot/cogs/massinfo.py).
PARTY_UNLOCK_REMAINING = 2
SIGNUPS_FIRST_STEP = 5
SIGNUPS_PER_PARTY = 20
OPEN_PARTIES_AT_START = 2


@dataclass
class PartyDef:
    """Só o que o gate precisa de uma party: capacidade e nomes de função."""
    total_slots: int
    role_names: set[str] = field(default_factory=set)


def _party_signup_threshold(party_num: int) -> int:
    """party_num é 1-indexado. Parties 1-2 sempre abertas (OPEN_PARTIES_AT_START);
    a partir da 3ª, o limiar cresce 20 por party: pt3=5, pt4=25, pt5=45..."""
    if party_num < 3:
        return 0
    return SIGNUPS_FIRST_STEP + SIGNUPS_PER_PARTY * (party_num - 3)


def quantity_gate(
    parties: list[PartyDef],
    signup_function_1s: list[str],
    functions_released: bool,
) -> set[str]:
    """Funções liberadas pela cascata de abertura de parties. Fail-open (libera
    tudo) se não há dados de vaga, se `functions_released=True` (equivalente
    ao /liberarfuncoes do bot antigo), ou se tudo já está cheio/fechado."""
    all_roles: set[str] = set().union(*(p.role_names for p in parties)) if parties else set()
    if not all_roles or functions_released:
        return all_roles

    total_signups = len(signup_function_1s)
    open_functions: set[str] = set()
    for idx, party in enumerate(parties):
        party_num = idx + 1
        if party_num <= OPEN_PARTIES_AT_START:
            is_open = True
        else:
            prev = parties[idx - 1]
            prev_filled = sum(1 for fn in signup_function_1s if fn in prev.role_names)
            prev_remaining = max(0, prev.total_slots - prev_filled)
            is_open = prev_remaining <= PARTY_UNLOCK_REMAINING or total_signups >= _party_signup_threshold(party_num)
        if not is_open:
            continue
        filled = sum(1 for fn in signup_function_1s if fn in party.role_names)
        if filled < party.total_slots:
            open_functions |= party.role_names

    return open_functions or all_roles  # nada aberto -> fail-open


def role_gate_allows(
    function_name: str,
    discord_role_ids: set[int],
    event_role_gates: dict[str, list[str]],
) -> bool:
    """Sem gate configurado pra essa função = livre pra qualquer um (fail-open,
    igual ao bot antigo)."""
    required = event_role_gates.get(function_name.lower())
    if not required:
        return True
    try:
        required_ids = {int(r) for r in required}
    except (TypeError, ValueError):
        return True
    return bool(discord_role_ids & required_ids)


def eligible_functions(
    parties: list[PartyDef],
    signup_function_1s: list[str],
    discord_role_ids: set[int],
    event_role_gates: dict[str, list[str]],
    functions_released: bool,
    is_staff: bool,
) -> tuple[list[str], str | None]:
    """Combina quantity_gate ∩ role_gate. `is_staff` (quem tem events.manage)
    ignora os dois gates, igual ao bypass de staff/council do bot antigo.
    Retorna (funções, motivo_da_recusa) — motivo em (None, "no_slots", "no_role")."""
    all_roles: set[str] = set().union(*(p.role_names for p in parties)) if parties else set()
    if not all_roles:
        return [], "no_slots"

    if is_staff:
        return sorted(all_roles), None

    quantity_allowed = quantity_gate(parties, signup_function_1s, functions_released)
    if not quantity_allowed:
        return [], "no_slots"

    role_allowed = {f for f in quantity_allowed if role_gate_allows(f, discord_role_ids, event_role_gates)}
    if not role_allowed:
        return [], "no_role"

    return sorted(role_allowed), None
