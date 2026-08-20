"""weapon+fn signup identity: preferences table, snapshot column, backfill

Revision ID: zw3a4b5c6d7f
Revises: zv2a3b4c5d6e

Nova identidade de signup: o par (Weapon.id, CompSlot.fn) — não a arma sozinha
e não o nome da GameRole. Longbow+DPS e Longbow+Support são preferências
DISTINTAS.

- `weapon_fn_preferences`: preferência persistente guild-scoped (user, weapon, fn).
- `event_signups.weapon_fns`: snapshot JSON [{"weapon_id", "fn"}] por signup.
  `functions` (nomes de GameRole) permanece legível — histórico finalizado
  intacto; novos signups continuam gravando o snapshot de nomes pra exibição.
- Backfill: APENAS eventos ativos (scheduled/in_progress) com comp, e só onde
  o nome -> par é confiável (todas as roles de mesmo nome na comp concordam
  num único par e têm arma). Ambíguo fica vazio — a leitura legada cobre.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.base import json_type


revision: str = "zw3a4b5c6d7f"
down_revision: Union[str, None] = "zv2a3b4c5d6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fn_key(value: str | None) -> str:
    return " ".join((value or "").casefold().split()) or "other"


def _pair_key(weapon_id: int, fn: str | None) -> str:
    return f"w{int(weapon_id)}:{_fn_key(fn)}"


def upgrade() -> None:
    bind = op.get_bind()
    bigint = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

    op.create_table(
        "weapon_fn_preferences",
        sa.Column("id", bigint, autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("weapon_id", bigint, nullable=False),
        sa.Column("fn", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["weapon_id"], ["weapons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guild_id", "user_id", "weapon_id", "fn", name="uq_weapon_fn_pref"),
    )
    op.create_index("ix_weapon_fn_preferences_guild_id", "weapon_fn_preferences", ["guild_id"])
    op.create_index("ix_weapon_fn_preferences_user_id", "weapon_fn_preferences", ["user_id"])
    op.create_index("ix_weapon_fn_preferences_weapon_id", "weapon_fn_preferences", ["weapon_id"])

    # default '[]' — postgres precisa de cast ::jsonb; sqlite guarda JSON como texto.
    # Offline (`alembic upgrade --sql`) não tem bind: assume postgres (prod) —
    # o DDL gerado é o canônico de produção.
    dialect = bind.dialect.name if bind is not None else "postgresql"
    wf_default = sa.text("'[]'::jsonb") if dialect == "postgresql" else sa.text("'[]'")
    op.add_column(
        "event_signups",
        sa.Column("weapon_fns", json_type(), nullable=False, server_default=wf_default),
    )

    # Backfill data-dependent — o guard contra mock offline vive dentro de
    # _backfill_active_signups (bind.execute() retorna None em `--sql`).
    _backfill_active_signups(bind)


def _backfill_active_signups(bind) -> None:
    # Postgres guarda Enum(EventState) pelos NOMES ("SCHEDULED"), enquanto
    # SQLite dev guarda os values ("scheduled"). Normaliza o texto para o
    # backfill funcionar nos dois bancos.
    functions_len = (
        "jsonb_array_length(s.functions)"
        if bind.dialect.name == "postgresql"
        else "json_array_length(s.functions)"
    )
    result = bind.execute(sa.text(f"""
        select s.id, s.event_id, s.functions
        from event_signups s
        join events e on e.id = s.event_id
        where lower(cast(e.state as text)) in ('scheduled', 'in_progress')
          and e.comp_id is not null
          and s.functions is not null
          and {functions_len} > 0
    """))
    # `alembic upgrade --sql` (offline) entrega um bind mock cujo execute()
    # retorna None — nada a backfillar, só o DDL foi emitido.
    if result is None:
        return
    signups = result.fetchall()
    if not signups:
        return

    updates = sa.table(
        "event_signups",
        sa.column("id", sa.BigInteger),
        sa.column("weapon_fns", json_type()),
    )
    comp_slots: dict[int, list[tuple[str | None, int]]] = {}  # comp_id -> [(slot_fn, role_id)]
    roles_by_id: dict[int, tuple[str, int | None]] = {}       # role_id -> (name, weapon_id)

    def _load_comp(comp_id: int) -> list[tuple[str | None, int]]:
        rows = bind.execute(sa.text("""
            select cs.fn, csr.game_role_id
            from comp_parties cp
            join comp_slots cs on cs.party_id = cp.id
            join comp_slot_roles csr on csr.slot_id = cs.id
            where cp.comp_id = :comp
            order by cp.position, cs.position, csr.position
        """), {"comp": comp_id}).fetchall()
        comp_slots[comp_id] = rows
        return rows

    events_comp: dict[int, int] = {}
    for sid, event_id, functions in signups:
        if event_id not in events_comp:
            row = bind.execute(
                sa.text("select comp_id from events where id = :e"), {"e": event_id},
            ).fetchone()
            events_comp[event_id] = row[0] if row and row[0] else 0
        comp_id = events_comp[event_id]
        if not comp_id:
            continue
        slots = comp_slots.get(comp_id) or _load_comp(comp_id)

        needed = {role_id for _fn, role_id in slots}
        missing = needed - roles_by_id.keys()
        if missing:
            for rid, name, weapon_id in bind.execute(
                sa.text("select id, name, weapon_id from game_roles where id in :ids").bindparams(
                    sa.bindparam("ids", expanding=True)
                ),
                {"ids": list(missing)},
            ):
                roles_by_id[rid] = (name, weapon_id)

        # nome (casefold) -> pares (weapon_id, fn) das roles DESSA comp
        by_name: dict[str, dict[str, tuple[int, str]]] = {}
        for slot_fn, role_id in slots:
            role = roles_by_id.get(role_id)
            if role is None or role[1] is None:
                continue  # sem arma não forma par
            name, weapon_id = role
            entry = by_name.setdefault(" ".join(name.casefold().split()), {})
            entry.setdefault(_pair_key(weapon_id, slot_fn), (weapon_id, slot_fn))

        pairs: list[dict] = []
        seen: set[str] = set()
        for name in functions:
            candidates = by_name.get(" ".join(str(name).casefold().split()), {})
            if len(candidates) != 1:
                continue  # ambíguo/desconhecido — permanece legado
            key, (weapon_id, slot_fn) = next(iter(candidates.items()))
            if key not in seen:
                seen.add(key)
                pairs.append({"weapon_id": weapon_id, "fn": slot_fn})
        if pairs:
            bind.execute(
                updates.update().where(updates.c.id == sid).values(weapon_fns=pairs)
            )


def downgrade() -> None:
    op.drop_column("event_signups", "weapon_fns")
    op.drop_index("ix_weapon_fn_preferences_weapon_id", table_name="weapon_fn_preferences")
    op.drop_index("ix_weapon_fn_preferences_user_id", table_name="weapon_fn_preferences")
    op.drop_index("ix_weapon_fn_preferences_guild_id", table_name="weapon_fn_preferences")
    op.drop_table("weapon_fn_preferences")
