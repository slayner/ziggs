"""Foco da migração de identidade (weapon, fn) — ago/2026.

A identidade de um signup é o par (Weapon.id, CompSlot.fn), não a arma sozinha
nem o nome da GameRole. Cobre:
- Longbow+DPS vs Longbow+Support como opções DISTINTAS;
- pré-seleção global entre comps diferentes (preferência não é comp-scoped);
- semântica de remoção (só apaga o par visível na comp do evento);
- autofill casando por par e persistindo GameRole concreta.

Roda sem banco: SQLite em memória + shim JSONB->JSON (o app é Postgres-only,
ver app/db.py — aqui só exercitamos os serviços com uma Session própria).
"""
from __future__ import annotations

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base  # noqa: F401 ( importa tudo p/ metadata )
from app.models.catalog import GameRole, Weapon
from app.models.comp_preferences import WeaponFnPreference
from app.models.comps import Comp, CompParty, CompSlot, CompSlotRole
from app.models.events import Event, EventSignup
from app.models.tenancy import Guild
from app.domain.states import EventState
from app.services import event_signups
from app.services.event_escalation import autofill_event
from app.services.event_gates import fn_key, pair_key


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # pragma: no cover - shim de teste
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # pragma: no cover - shim de teste
    # sqlite só autoincrementa INTEGER PRIMARY KEY; o app usa BigInteger (PG).
    return "INTEGER"


def _db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    # comp_ids se repetem entre engines — cache de party defs precisa zerar.
    event_signups._party_defs_cache.clear()
    return sessionmaker(bind=engine)()


def _seed(db: Session) -> dict:
    db.add(Guild(id=1, name="Guilda Teste"))
    longbow = Weapon(item_id="T8_MAIN_LONGBOW", name="Longbow")
    greatstaff = Weapon(item_id="T8_2H_GREATSTAFF", name="Greatstaff")
    firestaff = Weapon(item_id="T8_FIRESTAFF", name="Firestaff")
    db.add_all([longbow, greatstaff, firestaff])
    db.flush()
    roles = {
        "lb_dps": GameRole(guild_id=1, name="Arqueiro DPS", weapon_id=longbow.id),
        "lb_dps_b": GameRole(guild_id=1, name="Arqueiro DPS B", weapon_id=longbow.id),
        "lb_sup": GameRole(guild_id=1, name="Arqueiro Suporte", weapon_id=longbow.id),
        "gs_dps": GameRole(guild_id=1, name="Cajado DPS", weapon_id=greatstaff.id),
        "fs_sup": GameRole(guild_id=1, name="Firestaff Suporte", weapon_id=firestaff.id),
    }
    db.add_all(roles.values())
    db.flush()

    def slot(party: CompParty, pos: int, fn: str, role_ids: list[str]) -> CompSlot:
        s = CompSlot(party_id=party.id, position=pos, fn=fn)
        db.add(s)
        db.flush()
        for i, rid in enumerate(role_ids):
            db.add(CompSlotRole(slot_id=s.id, game_role_id=roles[rid].id, position=i))
        db.flush()
        return s

    def comp(name: str, spec: list[list[tuple[str, list[str]]]]) -> Comp:
        c = Comp(guild_id=1, name=name)
        db.add(c)
        db.flush()
        for ppos, rows in enumerate(spec, start=1):
            p = CompParty(comp_id=c.id, position=ppos, name=f"P{ppos}")
            db.add(p)
            db.flush()
            for spos, (fn, rids) in enumerate(rows, start=1):
                slot(p, spos, fn, rids)
        return c

    comp_a = comp("ZvZ", [[("dps", ["lb_dps", "gs_dps"]), ("support", ["lb_sup", "fs_sup"])]])
    comp_b = comp("Brawl", [[("dps", ["lb_dps_b"]), ("support", ["lb_sup", "fs_sup"])]])
    db.flush()
    return {
        "longbow": longbow.id, "greatstaff": greatstaff.id, "firestaff": firestaff.id,
        "roles": {k: r.id for k, r in roles.items()},
        "comp_a": comp_a.id, "comp_b": comp_b.id,
    }


def _event(db: Session, comp_id: int, **kw) -> Event:
    ev = Event(
        guild_id=1, state=EventState.SCHEDULED, comp_id=comp_id,
        signup_mode="signup", functions_released=True, **kw,
    )
    db.add(ev)
    db.flush()
    return ev


def test_longbow_dps_and_support_are_distinct_options():
    db = _db()
    ids = _seed(db)
    ev = _event(db, ids["comp_a"])

    options, reason, current, _min = event_signups.get_eligible_options(db, 1, ev.id, 77, set(), {})
    assert reason is None and current is None
    keys = {o["key"] for o in options}
    lb_dps = pair_key(ids["longbow"], "dps")
    lb_sup = pair_key(ids["longbow"], "support")
    gs_dps = pair_key(ids["greatstaff"], "dps")
    fs_sup = pair_key(ids["firestaff"], "support")
    assert keys == {lb_dps, lb_sup, gs_dps, fs_sup}, "mesma arma em fns diferentes deve ser opções distintas"

    row = event_signups.upsert_signup(db, 1, ev.id, 77, "User", [lb_dps], set(), {})
    db.flush()
    assert row.weapon_fns == [{"weapon_id": ids["longbow"], "fn": "dps"}]
    # snapshot legado de nomes: primeira GameRole que casa com o par
    assert row.functions == ["Arqueiro DPS"]
    # preferência global nasceu
    prefs = db.scalars(select(WeaponFnPreference).where(WeaponFnPreference.user_id == 77)).all()
    assert [(p.weapon_id, p.fn) for p in prefs] == [(ids["longbow"], fn_key("dps"))]


def test_global_preselection_across_comps():
    db = _db()
    ids = _seed(db)
    ev_a = _event(db, ids["comp_a"])
    lb_dps = pair_key(ids["longbow"], "dps")
    gs_dps = pair_key(ids["greatstaff"], "dps")

    # signup na comp A: Longbow+DPS e Greatstaff+DPS
    event_signups.upsert_signup(db, 1, ev_a.id, 77, "User", [lb_dps, gs_dps], set(), {})

    # comp B oferece Longbow+DPS, Longbow+Support, Firestaff+Support
    ev_b = _event(db, ids["comp_b"])
    options, _reason, current, _min = event_signups.get_eligible_options(db, 1, ev_b.id, 77, set(), {})
    by_key = {o["key"]: o for o in options}
    profile = event_signups.get_profile_options(db, 1, ids["comp_b"], 77, by_key)
    # pré-seleção APENAS Longbow+DPS: Longbow+Support é outro par, Greatstaff
    # não aparece na comp B (não é visível, mas continua salvo).
    assert profile == [lb_dps]


def test_removal_only_removes_visible_pair():
    db = _db()
    ids = _seed(db)
    lb_dps = pair_key(ids["longbow"], "dps")
    lb_sup = pair_key(ids["longbow"], "support")
    gs_dps = pair_key(ids["greatstaff"], "dps")
    # preferências pré-existentes: os três pares
    for wid, fn in ((ids["longbow"], "dps"), (ids["longbow"], "support"), (ids["greatstaff"], "dps")):
        db.add(WeaponFnPreference(guild_id=1, user_id=77, weapon_id=wid, fn=fn_key(fn)))
    db.flush()

    # comp B (visível: Longbow dps, Longbow support, Firestaff support) — o
    # jogador salva SÓ Longbow+Support: remove Longbow+DPS (visível e não
    # escolhido), preserva Longbow+Support e NÃO toca em Greatstaff+DPS
    # (par de outra comp, invisível aqui).
    ev_b = _event(db, ids["comp_b"])
    event_signups.upsert_signup(db, 1, ev_b.id, 77, "User", [lb_sup], set(), {})

    prefs = {
        (p.weapon_id, p.fn)
        for p in db.scalars(select(WeaponFnPreference).where(WeaponFnPreference.user_id == 77))
    }
    assert prefs == {
        (ids["longbow"], "support"),
        (ids["greatstaff"], "dps"),
    }, "remover Longbow+DPS não pode apagar Longbow+Support nem Greatstaff+DPS"


def test_autofill_matches_pair_and_keeps_concrete_role():
    db = _db()
    ids = _seed(db)
    ev = _event(db, ids["comp_a"], autofill_mode="manual")

    # slot support = Arqueiro Suporte; slot dps = Arqueiro DPS + Arqueiro DPS B
    # (dois roles COMPARTILHAM o par Longbow+dps).
    db.add(EventSignup(
        event_id=ev.id, guild_id=1, user_id=77, user_name="User",
        functions=["Arqueiro DPS"],
        weapon_fns=[{"weapon_id": ids["longbow"], "fn": "dps"}],
    ))
    db.flush()
    result = autofill_event(db, 1, ev.id)
    assert result["assigned"] == 1
    from app.models.events import EventAssignment
    rows = db.scalars(select(EventAssignment).where(EventAssignment.event_id == ev.id)).all()
    assert len(rows) == 1
    # casou pelo PAR: slot de support (mesma arma, outro fn) NÃO serve.
    assert rows[0].game_role_id == ids["roles"]["lb_dps"], "deve escolher a primeira flex compatível do slot dps"

    # legado: signup sem weapon_fns cai no nome -> par desta comp
    ev2 = _event(db, ids["comp_a"], autofill_mode="manual")
    db.add(EventSignup(
        event_id=ev2.id, guild_id=1, user_id=88, user_name="Velho",
        functions=["Arqueiro Suporte"], weapon_fns=[],
    ))
    db.flush()
    autofill_event(db, 1, ev2.id)
    rows2 = db.scalars(select(EventAssignment).where(EventAssignment.event_id == ev2.id)).all()
    assert len(rows2) == 1
    assert rows2[0].game_role_id == ids["roles"]["lb_sup"]


def test_legacy_names_convert_to_pairs():
    db = _db()
    ids = _seed(db)
    ev = _event(db, ids["comp_a"])
    row = event_signups.upsert_signup(
        db, 1, ev.id, 77, "User", [], set(), {},
        legacy_names=["Arqueiro Suporte"],
    )
    db.flush()
    assert row.weapon_fns == [{"weapon_id": ids["longbow"], "fn": "support"}]
    assert row.functions == ["Arqueiro Suporte"]


if __name__ == "__main__":
    test_longbow_dps_and_support_are_distinct_options()
    test_global_preselection_across_comps()
    test_removal_only_removes_visible_pair()
    test_autofill_matches_pair_and_keeps_concrete_role()
    test_legacy_names_convert_to_pairs()
    print("ok")
