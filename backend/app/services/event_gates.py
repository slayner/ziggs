"""Gate de inscrição em eventos: cascata de abertura de parties por
quantidade + gate por cargo do Discord — porta de `bot/cogs/massinfo.py`
(`_quantity_gate`/`_allowed_roles`) pro backend, usando a árvore
Comp -> CompParty -> CompSlot -> CompSlotRole -> GameRole do site em vez da
planilha/escalação do bot antigo.

Granularidade (ago/2026): a identidade de uma opção de signup é o par
(Weapon.id, CompSlot.fn) — ver `pair_key`. Longbow+DPS e Longbow+Support são
opções DISTINTAS no picker, no gate de quantidade e no autofill. A escolha de
GameRole concreta (build) continua existindo só na atribuição de escalação
(EventAssignment.game_role_id).

Uma `CompSlot` = uma vaga (capacidade 1), não importa quantas `GameRole` ela
aceita via CompSlotRole. "Preenchida" é uma aproximação: conta usuários
distintos cuja primeira escolha (`weapon_fns[0]`) bate com algum par da
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


def function_key(name: str) -> str:
    """Identity of a legacy GameRole-name option; display names stay readable."""
    return " ".join(name.casefold().split())


def fn_key(fn: str | None) -> str:
    """Normaliza o fn do CompSlot (casefold/strip). Vazio/None vira 'other' —
    mesma convenção de agrupamento do picker (FALLBACK_CATEGORY)."""
    return " ".join((fn or "").casefold().split()) or "other"


def pair_key(weapon_id: int, fn: str | None) -> str:
    """Identidade de uma opção de signup: o par (Weapon.id, CompSlot.fn).
    Longbow+DPS e Longbow+Support são chaves distintas."""
    return f"w{int(weapon_id)}:{fn_key(fn)}"


@dataclass
class PartyDef:
    """Só o que o gate precisa de uma party: capacidade e pares oferecidos."""
    total_slots: int
    pair_keys: set[str] = field(default_factory=set)


def _party_signup_threshold(party_num: int) -> int:
    """party_num é 1-indexado. Parties 1-2 sempre abertas (OPEN_PARTIES_AT_START);
    a partir da 3ª, o limiar cresce 20 por party: pt3=5, pt4=25, pt5=45..."""
    if party_num < 3:
        return 0
    return SIGNUPS_FIRST_STEP + SIGNUPS_PER_PARTY * (party_num - 3)


def quantity_gate(
    parties: list[PartyDef],
    signup_first_pairs: list[str],
    functions_released: bool,
) -> set[str]:
    """Pares liberados pela cascata de abertura de parties. Fail-open (libera
    tudo) se não há dados de vaga, se `functions_released=True` (equivalente
    ao /liberarfuncoes do bot antigo), ou se tudo já está cheio/fechado."""
    all_pairs: set[str] = set().union(*(p.pair_keys for p in parties)) if parties else set()
    if not all_pairs or functions_released:
        return all_pairs

    total_signups = len(signup_first_pairs)
    open_pairs: set[str] = set()
    for idx, party in enumerate(parties):
        party_num = idx + 1
        if party_num <= OPEN_PARTIES_AT_START:
            is_open = True
        else:
            prev = parties[idx - 1]
            prev_filled = sum(1 for k in signup_first_pairs if k in prev.pair_keys)
            prev_remaining = max(0, prev.total_slots - prev_filled)
            is_open = prev_remaining <= PARTY_UNLOCK_REMAINING or total_signups >= _party_signup_threshold(party_num)
        if not is_open:
            continue
        filled = sum(1 for k in signup_first_pairs if k in party.pair_keys)
        if filled < party.total_slots:
            open_pairs |= party.pair_keys

    return open_pairs or all_pairs  # nada aberto -> fail-open


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


def weapon_gate_allows(
    weapon_ids: set[int], discord_role_ids: set[int], weapon_gates: dict[str, list[str]],
) -> bool:
    """Any restricted weapon in a grouped role restricts that signup option."""
    if not weapon_ids:
        return True
    return all(role_gate_allows(str(weapon_id), discord_role_ids, weapon_gates) for weapon_id in weapon_ids)


def eligible_options(
    parties: list[PartyDef],
    signup_first_pairs: list[str],
    discord_role_ids: set[int],
    event_weapon_gates: dict[str, list[str]],
    functions_released: bool,
    is_staff: bool,
    option_weapons: dict[str, int] | None = None,
) -> tuple[list[str], str | None]:
    """Combina quantity_gate ∩ weapon_gate sobre CHAVES DE PAR
    (weapon_id, slot fn). `is_staff` (quem tem events.manage) ignora os dois
    gates, igual ao bypass de staff/council do bot antigo. `option_weapons`
    mapeia pair_key -> weapon_id (cada opção tem exatamente uma arma).
    Retorna (pair_keys, motivo_da_recusa) — motivo em (None, "no_slots", "no_role")."""
    all_pairs: set[str] = set().union(*(p.pair_keys for p in parties)) if parties else set()
    if not all_pairs:
        return [], "no_slots"

    if is_staff:
        return sorted(all_pairs), None

    quantity_allowed = quantity_gate(parties, signup_first_pairs, functions_released)
    if not quantity_allowed:
        return [], "no_slots"

    option_weapons = option_weapons or {}
    weapon_allowed = {
        k for k in quantity_allowed
        if weapon_gate_allows(
            {option_weapons[k]} if k in option_weapons else set(),
            discord_role_ids, event_weapon_gates,
        )
    }
    if not weapon_allowed:
        return [], "no_role"

    return sorted(weapon_allowed), None
