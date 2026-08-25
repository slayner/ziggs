"""Energia de guilda — saldo por membro Discord, ledger imutável e whitelist.

Porta tenant-scoped do sistema de energia do bot legado (bot-legacy/cogs/
energia.py + database.py), fundação do portal do membro. Núcleo puro de serviço:
sem rotas, sem auth de membro ativo, sem dependência direta da fonte de registro
— a resolução nick→discord_user_id vem por callback `name_resolver`, injetado
pelo caller (roteador/bot que sabe qual é a fonte vigente de registro).

Invariante: `EnergyBalance.balance == sum(EnergyEntry.amount)` por
(guild_id, discord_user_id) SEMPRE. Quem precisa corrigir saldo emite lançamento
compensatório (kind='adjustment'), nunca UPDATE/DELETE em linha de ledger.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.energy import EnergyBalance, EnergyControlMessage, EnergyEntry, EnergyWhitelist
from app.models.registration import BotRegistration

log = logging.getLogger(__name__)

# Regex da log do jogo — campos entre aspas, separados por tab ou espaços.
# Mesma regex do bot legado (bot-legacy/cogs/energia.py::_parse_energy_log).
_QUOTED = re.compile(r'"([^"]*)"')


class NameResolver(Protocol):
    """Resolve um nick do jogo → discord_user_id dentro de uma guilda.

    Pode ser sync ou async; o caller decide a fonte (BotRegistration hoje,
    RegisteredCharacter amanhã). Devolve None = nick não registrado → entrada
    é ignorada (igual ao bot legado)."""

    def __call__(self, db: Session, guild_id: int, nick: str) -> int | None: ...


def parse_energy_log(text: str) -> list[tuple[str, str, str, int]]:
    """Porta direta do `_parse_energy_log` do bot legado.

    Parseia a log colada (campos entre aspas). Robusto a tab OU espaços, desde
    que os campos venham entre aspas. Retorna lista de (ts, player, reason,
    amount:int). Pula o cabeçalho e linhas inválidas.
    """
    entries: list[tuple[str, str, str, int]] = []
    for line in (text or "").splitlines():
        fields = _QUOTED.findall(line)
        if len(fields) < 4:
            continue
        ts, player, reason, amount = (f.strip() for f in fields[:4])
        if ts.lower() == "date" or player.lower() == "player":
            continue  # cabeçalho
        if not player:
            continue
        try:
            amt = int(amount.replace(",", ""))
        except ValueError:
            continue
        entries.append((ts, player, reason, amt))
    return entries


def get_balance(db: Session, guild_id: int, discord_user_id: int) -> int:
    """Saldo de energia do membro (0 se não tem linha ainda)."""
    bal = db.scalar(select(EnergyBalance).where(
        EnergyBalance.guild_id == guild_id,
        EnergyBalance.discord_user_id == discord_user_id,
    ))
    return int(bal.balance) if bal is not None else 0


def _get_or_create_balance(db: Session, guild_id: int, discord_user_id: int) -> EnergyBalance:
    bal = db.scalar(select(EnergyBalance).where(
        EnergyBalance.guild_id == guild_id,
        EnergyBalance.discord_user_id == discord_user_id,
    ))
    if bal is None:
        bal = EnergyBalance(guild_id=guild_id, discord_user_id=discord_user_id, balance=0)
        db.add(bal)
        db.flush()
    return bal


def _is_whitelisted(db: Session, guild_id: int, discord_user_id: int) -> bool:
    return db.scalar(select(EnergyWhitelist.id).where(
        EnergyWhitelist.guild_id == guild_id,
        EnergyWhitelist.discord_user_id == discord_user_id,
    )) is not None


@dataclass
class ApplyResult:
    applied: int = 0
    duplicates: int = 0
    whitelisted_applied: int = 0  # entradas de whitelisted que foram aplicadas (sim contam)
    unregistered: dict[str, int] | None = None


def apply_parsed_entries(
    db: Session,
    guild_id: int,
    entries: Iterable[tuple[str, str, str, int]],
    name_resolver: NameResolver | Callable[[Session, int, str], int | None],
) -> ApplyResult:
    """Aplica lançamentos parseados da log do jogo no saldo de cada membro.

    Todas as entradas são aplicadas — whitelisted NÃO é mais ignorado. A
    whitelist só controla alertas de energia baixa (quem cuida da guilda não
    é notificado); o saldo e o ledger são tratados igual pra todo mundo.

    `name_resolver` resolve nick→discord_user_id dentro da guilda. Devolve None
    = não registrado → entrada vai pra `unregistered` e é ignorada.
    """
    res = ApplyResult(unregistered={})
    new_deltas: dict[int, int] = defaultdict(int)

    # Whitelist — só pra contabilizar quantas entradas são de whitelisted
    # (informativo; o alerta é responsabilidade do caller, não daqui).
    whitelisted = set(db.scalars(select(EnergyWhitelist.discord_user_id).where(
        EnergyWhitelist.guild_id == guild_id,
    )).all())

    nick_to_uid: dict[str, int | None] = {}
    for ts, player, reason, amt in entries:
        if player not in nick_to_uid:
            nick_to_uid[player] = name_resolver(db, guild_id, player)
        uid = nick_to_uid[player]
        if uid is None:
            res.unregistered[player] = res.unregistered.get(player, 0) + 1
            continue
        # Dedup via INSERT — a unique parcial (kind='log') faz o trabalho.
        # Cada insert roda num SAVEPOINT próprio: a duplicata estoura a
        # constraint e o savepoint é descartado, sem tocar o resto do lote.
        entry = EnergyEntry(
            guild_id=guild_id, discord_user_id=uid, kind="log",
            ts=ts, player=player, reason=reason, amount=amt,
        )
        try:
            nested = db.begin_nested()
            db.add(entry)
            db.flush()
            nested.commit()
        except IntegrityError:  # Duplicata da unique parcial.
            nested.rollback()
            res.duplicates += 1
            continue
        res.applied += 1
        if uid in whitelisted:
            res.whitelisted_applied += 1
        new_deltas[uid] += amt

    # Aplica os deltas acumulados nos saldos.
    for uid, delta in new_deltas.items():
        bal = _get_or_create_balance(db, guild_id, uid)
        bal.balance += delta
    db.flush()
    return res


def manual_set(
    db: Session,
    guild_id: int,
    discord_user_id: int,
    new_value: int,
    actor_discord_id: int | None = None,
    reason: str | None = None,
) -> int:
    """Define o saldo de energia de um membro (ajuste manual, /setenergy).

    Emite um ÚNICO lançamento compensatório (kind='adjustment') cujo amount é a
    diferença entre o novo saldo e o atual — assim `balance == sum(amount)` se
    mantém sem nunca UPDATE/DELETE no ledger. Igual ao `set_energy` do bot
    legado em efeito, sem quebrar a invariante.

    Devolve o novo saldo.
    """
    current = get_balance(db, guild_id, discord_user_id)
    delta = new_value - current
    if delta == 0:
        return current  # nada a fazer, não polui o ledger
    bal = _get_or_create_balance(db, guild_id, discord_user_id)
    bal.balance = new_value
    db.add(EnergyEntry(
        guild_id=guild_id, discord_user_id=discord_user_id, kind="adjustment",
        ts="", player="", reason=reason, amount=delta,
        actor_discord_id=actor_discord_id,
    ))
    db.flush()
    return new_value


def toggle_whitelist(
    db: Session,
    guild_id: int,
    discord_user_id: int,
    added_by: int | None = None,
) -> bool:
    """Alterna a whitelist de energia do membro.

    Devolve True se ADICIONOU (era ausente), False se REMOVEU (estava presente)
    — mesmo contrato do `add_energy_whitelist` do bot legado, mas toggle num
    só passo (igual ao comando /energywl do bot, que é add-ou-remove).
    """
    existing = db.scalar(select(EnergyWhitelist).where(
        EnergyWhitelist.guild_id == guild_id,
        EnergyWhitelist.discord_user_id == discord_user_id,
    ))
    if existing is not None:
        db.delete(existing)
        db.flush()
        return False
    db.add(EnergyWhitelist(
        guild_id=guild_id, discord_user_id=discord_user_id, added_by=added_by,
    ))
    db.flush()
    return True


def list_whitelist(db: Session, guild_id: int) -> list[int]:
    """Lista os discord_user_ids na whitelist de energia da guilda."""
    return list(db.scalars(select(EnergyWhitelist.discord_user_id).where(
        EnergyWhitelist.guild_id == guild_id,
    )).all())


def ledger_reconciles(
    db: Session, guild_id: int, discord_user_id: int
) -> bool:
    """Verifica a invariante: balance == sum(amount) das entries do membro.

    Função de checagem (não muta nada). Usada pelos testes e disponível pra
    auditoria/CI do portal.
    """
    bal = get_balance(db, guild_id, discord_user_id)
    summed = db.scalar(select(func.coalesce(func.sum(EnergyEntry.amount), 0)).where(
        EnergyEntry.guild_id == guild_id,
        EnergyEntry.discord_user_id == discord_user_id,
    ))
    return bal == int(summed or 0)


# --- Energy Control embed (canal de saldos negativos) ------------------------
def get_control_message(db: Session, guild_id: int) -> tuple[int | None, int | None]:
    """(channel_id, message_id) do embed de energy-control. None se não há."""
    row = db.scalar(select(EnergyControlMessage).where(EnergyControlMessage.guild_id == guild_id))
    if row is None:
        return None, None
    return row.channel_id, row.message_id


def set_control_message(db: Session, guild_id: int, channel_id: int | None, message_id: int | None) -> None:
    row = db.get(EnergyControlMessage, guild_id)
    if row is None:
        row = EnergyControlMessage(guild_id=guild_id)
        db.add(row)
    row.channel_id = channel_id
    row.message_id = message_id
    db.flush()


def control_rows(db: Session, guild_id: int, threshold: int = 100) -> list[dict]:
    """Jogadores com saldo < threshold, registrados, não-whitelisted, e que
    já tiveram pelo menos um log no passado (não lista saldo 0 sem histórico).
    Retorna lista de {user_id, display_name, balance} ordenada por saldo asc."""
    wl_ids = set(list_whitelist(db, guild_id))
    # Saldos
    bal_rows = db.scalars(select(EnergyBalance).where(EnergyBalance.guild_id == guild_id)).all()
    balances = {b.discord_user_id: int(b.balance) for b in bal_rows}
    # Registrados (BotRegistration ativa) — mapa uid → primeiro nome de personagem.
    reg_rows = db.execute(
        select(BotRegistration.discord_user_id, BotRegistration.albion_player_name).where(
            BotRegistration.guild_id == guild_id,
            BotRegistration.active.is_(True),
        )
    ).all()
    reg_ids = {uid for uid, _ in reg_rows}
    reg_names: dict[int, str] = {}
    for uid, name in reg_rows:
        if uid not in reg_names:
            reg_names[uid] = name
    # Usuários com pelo menos uma entry (histórico)
    has_history_ids = set(db.scalars(
        select(EnergyEntry.discord_user_id).where(
            EnergyEntry.guild_id == guild_id,
        ).distinct()
    ).all())
    # Nomes (GuildMember + User) como fallback
    from app.models.tenancy import GuildMember, User
    name_map: dict[int, str] = {}
    for gm, user in db.execute(
        select(GuildMember, User).join(User, GuildMember.user_id == User.id).where(
            GuildMember.guild_id == guild_id,
        )
    ).all():
        name_map[gm.user_id] = user.global_name or user.username or str(gm.user_id)
    rows = []
    for uid, bal in balances.items():
        if uid in wl_ids:
            continue
        if uid not in reg_ids:
            continue
        if bal >= threshold:
            continue
        if bal == 0 and uid not in has_history_ids:
            continue
        rows.append({"user_id": uid, "display_name": reg_names.get(uid) or name_map.get(uid, str(uid)), "balance": bal})
    rows.sort(key=lambda r: (r["balance"], r["display_name"]))
    return rows
