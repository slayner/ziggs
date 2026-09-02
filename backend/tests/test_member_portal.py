"""Foco do portal do membro — /guilds/{guild_id}/member/*.

Cobre os eixos pedidos no escopo:
  - autorização: não-membro (403), membro que saiu (403), membro ativo (200);
  - published-event filtering: draft/review/cancelled/deleted NÃO aparecem;
  - cross-user wallet/energy isolation: o membro só vê o seu;
  - self-signup nunca aceita user/roles do client (só pair keys, identidade
    derivada server-side);
  - read-only finalized settlement: usa silver_received persistido, não
    recomputa settings.

Abordagem: app FastAPI mínimo com SÓ o router member + overrides de deps
(db_session, optional_user → require_active_guild_member). SQLite em
memória com os shims JSONB/BigInteger já consagrados nos outros testes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.api.routes import member as member_routes
from app.models import Base  # noqa: F401 ( importa tudo p/ metadata )
from app.models.catalog import GameRole, Weapon
from app.models.comp_preferences import WeaponFnPreference
from app.models.comps import Comp, CompParty, CompSlot, CompSlotRole
from app.models.economy import EconomyBalance, EconomyTransaction
from app.models.energy import EnergyBalance, EnergyEntry
from app.models.events import Event, EventParticipant, EventSignup
from app.models.tenancy import Guild, GuildMember, User
from app.domain.states import EventState


# ── shims (mesmo padrão de test_weapon_fn_identity.py) ────────────────────────

@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "INTEGER"


def _engine():
    # StaticPool + check_same_thread=False: SQLite em memória vive num ÚNICO
    # connection compartilhado entre todas as threads (TestClient roda handlers
    # em thread própria). Sem StaticPool, cada commit libera o connection de
    # volta pro pool — e em `sqlite://` (in-memory) cada connection NOVO vem com
    # banco vazio, perdendo todas as tabelas. StaticPool mantém o mesmo
    # connection (e portanto o mesmo DB in-memory) pra toda a vida do engine.
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _db() -> Session:
    engine = _engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _session_factory(db: Session):
    # Devolve a MESMA Session compartilhada — os handlers do FastAPI leem e
    # escrevem nela; o teste semeia e assevera no mesmo objeto. Funciona porque
    # os testes rodam 1 request por vez (sem concorrência real).
    def _get():
        yield db
    return _get


# ── fixture: guilda + 3 membros (ativo, outro ativo, saiu) ────────────────────

GUILD_ID = 1001
ALICE_UID = 2001   # membro ativo (o "eu" da maioria dos testes)
BOB_UID = 2002     # outro membro ativo (cross-user isolation)
CAROL_UID = 2003   # membro que saiu (left_at set)

def _seed_members(db: Session) -> None:
    db.add(Guild(id=GUILD_ID, name="Guilda Teste"))
    db.add(User(id=ALICE_UID, username="alice"))
    db.add(User(id=BOB_UID, username="bob"))
    db.add(User(id=CAROL_UID, username="carol"))
    db.add(GuildMember(guild_id=GUILD_ID, user_id=ALICE_UID))
    db.add(GuildMember(guild_id=GUILD_ID, user_id=BOB_UID))
    carol = GuildMember(guild_id=GUILD_ID, user_id=CAROL_UID)
    carol.left_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.add(carol)
    db.flush()


def _client(db: Session, *, acting_uid: int | None, member_override=None) -> TestClient:
    """App FastAPI com SÓ o router member e deps override.

    `acting_uid=None` simula deslogado. `member_override` injeta o membro
    que require_active_guild_member devolve (None = deixa a dep real rodar,
    que vai achar o GuildMember do acting_uid)."""
    app = FastAPI()
    app.include_router(member_routes.router)

    overrides = {deps.db_session: _session_factory(db)}
    if acting_uid is not None:
        overrides[deps.optional_user] = lambda: db.get(User, acting_uid)
    if member_override is not None:
        overrides[deps.require_active_guild_member] = lambda: member_override

    app.dependency_overrides = overrides
    return TestClient(app)


# ── 1. autorização ────────────────────────────────────────────────────────────

def test_nao_membro_recebe_403_na_carteira():
    db = _db()
    _seed_members(db)
    # Dave não é membro da guilda.
    db.add(User(id=9999, username="dave"))
    db.flush()
    client = _client(db, acting_uid=9999)
    r = client.get(f"/guilds/{GUILD_ID}/member/wallet")
    assert r.status_code == 403, r.text


def test_membro_que_saiu_recebe_403_na_carteira():
    db = _db()
    _seed_members(db)
    client = _client(db, acting_uid=CAROL_UID)
    r = client.get(f"/guilds/{GUILD_ID}/member/wallet")
    assert r.status_code == 403, r.text


def test_membro_ativo_recebe_200_na_carteira():
    db = _db()
    _seed_members(db)
    client = _client(db, acting_uid=ALICE_UID)
    r = client.get(f"/guilds/{GUILD_ID}/member/wallet")
    assert r.status_code == 200, r.text


# ── 2. published-event filtering ─────────────────────────────────────────────

def _event(db: Session, state: EventState, *, comp_id: int | None = None, **kw) -> Event:
    ev = Event(
        guild_id=GUILD_ID, state=state, comp_id=comp_id,
        signup_mode="signup", functions_released=True, **kw,
    )
    db.add(ev)
    db.flush()
    return ev


def test_lista_só_eventos_publicados():
    db = _db()
    _seed_members(db)
    sched = _event(db, EventState.SCHEDULED, title="Agendado")
    in_prog = _event(db, EventState.IN_PROGRESS, title="Andamento")
    review = _event(db, EventState.REVIEW, title="Revisao")
    finalized = _event(db, EventState.FINALIZED, title="Finalizado")
    _event(db, EventState.DRAFT, title="Rascunho")
    _event(db, EventState.CANCELLED, title="Cancelado")
    _event(db, EventState.DELETED, title="Excluido")
    db.flush()

    client = _client(db, acting_uid=ALICE_UID)
    r = client.get(f"/guilds/{GUILD_ID}/member/events")
    assert r.status_code == 200, r.text
    ids = {e["id"] for e in r.json()}
    # Review agora aparece (membro vê que está pendente, sem poder agir).
    assert ids == {sched.id, in_prog.id, review.id, finalized.id}, ids
    # can_signup só em scheduled/in_progress.
    by_id = {e["id"]: e for e in r.json()}
    assert by_id[sched.id]["can_signup"] is True
    assert by_id[in_prog.id]["can_signup"] is True
    assert by_id[review.id]["can_signup"] is False
    assert by_id[finalized.id]["can_signup"] is False


def test_detalhe_de_evento_nao_publicado_da_404():
    db = _db()
    _seed_members(db)
    draft = _event(db, EventState.DRAFT, title="Secreto")
    review = _event(db, EventState.REVIEW, title="Em revisao")
    db.flush()
    client = _client(db, acting_uid=ALICE_UID)
    # Draft nunca aparece.
    assert client.get(f"/guilds/{GUILD_ID}/member/events/{draft.id}").status_code == 404
    # Review aparece — membro vê que existe, sem controles admin.
    r = client.get(f"/guilds/{GUILD_ID}/member/events/{review.id}")
    assert r.status_code == 200
    assert r.json()["state"] == "review"


# ── 3. cross-user wallet/energy isolation ─────────────────────────────────────

def test_carteira_só_mostra_as_transações_do_proprio_membro():
    db = _db()
    _seed_members(db)
    # Alice recebe 500 de payout; Bob paga 300; Carol recebe 100 (mas Carol
    # saiu — mesmo assim o dado existe).
    db.add(EconomyBalance(guild_id=GUILD_ID, discord_user_id=ALICE_UID, balance=500, total_earned=500))
    db.add(EconomyBalance(guild_id=GUILD_ID, discord_user_id=BOB_UID, balance=-300, total_earned=0))
    db.add(EconomyTransaction(
        guild_id=GUILD_ID, kind="event_payout", actor_discord_id=0,
        to_user_id=ALICE_UID, total_earned_user_id=ALICE_UID, amount=500,
    ))
    db.add(EconomyTransaction(
        guild_id=GUILD_ID, kind="pay", actor_discord_id=BOB_UID,
        from_user_id=BOB_UID, to_user_id=ALICE_UID, total_earned_user_id=ALICE_UID, amount=300,
    ))
    db.add(EconomyTransaction(
        guild_id=GUILD_ID, kind="event_payout", actor_discord_id=0,
        to_user_id=BOB_UID, total_earned_user_id=BOB_UID, amount=100,
    ))
    db.flush()
    client = _client(db, acting_uid=ALICE_UID)
    r = client.get(f"/guilds/{GUILD_ID}/member/wallet")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["balance"] == 500
    tx_ids = {t["id"] for t in body["transactions"]}
    # Alice vê só as 2 transações onde ela aparece (payout recebido + pay
    # recebido); NÃO vê a do Bob.
    all_ids = {t.id for t in db.scalars(select(EconomyTransaction))}
    assert tx_ids <= all_ids
    assert len(tx_ids) == 2, f"Alice não deve ver transações do Bob: {tx_ids}"
    # Bob não aparece no ledger da Alice.
    kinds = {t["kind"] for t in body["transactions"]}
    assert "event_payout" in kinds


def test_energia_só_mostra_as_entradas_do_proprio_membro():
    db = _db()
    _seed_members(db)
    db.add(EnergyBalance(guild_id=GUILD_ID, discord_user_id=ALICE_UID, balance=42))
    db.add(EnergyBalance(guild_id=GUILD_ID, discord_user_id=BOB_UID, balance=99))
    db.add(EnergyEntry(guild_id=GUILD_ID, discord_user_id=ALICE_UID, kind="log",
                       ts="2026-01-01 00:00:00", player="alice", amount=42))
    db.add(EnergyEntry(guild_id=GUILD_ID, discord_user_id=BOB_UID, kind="log",
                       ts="2026-01-01 00:00:00", player="bob", amount=99))
    db.flush()
    client = _client(db, acting_uid=ALICE_UID)
    r = client.get(f"/guilds/{GUILD_ID}/member/energy")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["balance"] == 42
    assert len(body["entries"]) == 1
    assert body["entries"][0]["player"] == "alice"


def test_direção_da_transação_é_derivada_server_side():
    db = _db()
    _seed_members(db)
    # pay: Alice paga → direction "out" pro ponto de vista dela.
    db.add(EconomyTransaction(
        guild_id=GUILD_ID, kind="pay", actor_discord_id=ALICE_UID,
        from_user_id=ALICE_UID, to_user_id=BOB_UID, total_earned_user_id=BOB_UID, amount=150,
    ))
    db.flush()
    client = _client(db, acting_uid=ALICE_UID)
    r = client.get(f"/guilds/{GUILD_ID}/member/wallet")
    tx = r.json()["transactions"][0]
    assert tx["direction"] == "out"
    assert tx["counterparty_name"] == "bob"


# ── 4. self-signup: nunca aceita user/roles do client ─────────────────────────

def _seed_comp_with_pairs(db: Session) -> dict:
    """Cria uma comp com slots cujos pares são deterministicos."""
    db.add(Weapon(item_id="T8_MAIN_LONGBOW", name="Longbow"))
    db.flush()
    wid = db.scalar(select(Weapon.id))
    db.add(GameRole(guild_id=GUILD_ID, name="Arqueiro DPS", weapon_id=wid))
    db.flush()
    rid = db.scalar(select(GameRole.id))
    c = Comp(guild_id=GUILD_ID, name="ZvZ")
    db.add(c); db.flush()
    p = CompParty(comp_id=c.id, position=1, name="P1")
    db.add(p); db.flush()
    s = CompSlot(party_id=p.id, position=1, fn="dps")
    db.add(s); db.flush()
    db.add(CompSlotRole(slot_id=s.id, game_role_id=rid, position=0))
    db.flush()
    return {"comp_id": c.id, "weapon_id": wid, "pair_key": f"w{wid}:dps"}


def test_signup_aceita_só_pair_keys_e_deriva_identidade_server_side():
    db = _db()
    _seed_members(db)
    ids = _seed_comp_with_pairs(db)
    ev = _event(db, EventState.SCHEDULED, comp_id=ids["comp_id"])
    db.flush()
    client = _client(db, acting_uid=ALICE_UID)
    # O body manda SÓ options (pair keys); NÃO manda user_id nem discord_role_ids.
    r = client.post(
        f"/guilds/{GUILD_ID}/member/events/{ev.id}/signup",
        json={"options": [ids["pair_key"]]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # A inscrição gravada é da Alice (uid 2001), derivada server-side.
    row = db.scalar(select(EventSignup).where(EventSignup.event_id == ev.id))
    assert row is not None
    assert row.user_id == ALICE_UID
    assert row.user_name == "alice"  # nome derivado do User, não do body
    assert row.weapon_fns == [{"weapon_id": ids["weapon_id"], "fn": "dps"}]


def test_signup_não_aceita_pair_key_desconhecida():
    db = _db()
    _seed_members(db)
    ids = _seed_comp_with_pairs(db)
    ev = _event(db, EventState.SCHEDULED, comp_id=ids["comp_id"])
    db.flush()
    client = _client(db, acting_uid=ALICE_UID)
    r = client.post(
        f"/guilds/{GUILD_ID}/member/events/{ev.id}/signup",
        json={"options": ["w99999:dps"]},  # weapon inexistente
    )
    assert r.status_code == 400, r.text


def test_signup_em_evento_não_publicado_é_bloqueado_pelo_serviço():
    db = _db()
    _seed_members(db)
    ids = _seed_comp_with_pairs(db)
    # REVIEW não aceita signup (signup_block_reason).
    ev = _event(db, EventState.REVIEW, comp_id=ids["comp_id"])
    db.flush()
    client = _client(db, acting_uid=ALICE_UID)
    # A rota de signup-options retorna 400 com block_reason (review bloqueia).
    r = client.post(
        f"/guilds/{GUILD_ID}/member/events/{ev.id}/signup",
        json={"options": [ids["pair_key"]]},
    )
    assert r.status_code == 400, r.text


def test_delete_signup_remove_só_a_inscrição_do_proprio_membro():
    db = _db()
    _seed_members(db)
    ids = _seed_comp_with_pairs(db)
    ev = _event(db, EventState.SCHEDULED, comp_id=ids["comp_id"])
    db.flush()
    # Alice e Bob se inscrevem.
    db.add(EventSignup(event_id=ev.id, guild_id=GUILD_ID, user_id=ALICE_UID, user_name="alice"))
    db.add(EventSignup(event_id=ev.id, guild_id=GUILD_ID, user_id=BOB_UID, user_name="bob"))
    db.flush()
    client = _client(db, acting_uid=ALICE_UID)
    r = client.delete(f"/guilds/{GUILD_ID}/member/events/{ev.id}/signup")
    assert r.status_code == 204, r.text
    # A inscrição da Alice sumiu; a do Bob continua.
    remaining = {s.user_id for s in db.scalars(select(EventSignup).where(EventSignup.event_id == ev.id))}
    assert remaining == {BOB_UID}, remaining


# ── 5. read-only finalized settlement ────────────────────────────────────────

def test_settlement_de_finalizado_usa_silver_received_persistido():
    db = _db()
    _seed_members(db)
    # Evento finalizado com tab_value=10000, 2 participantes com silver_received
    # já persistido (o finalize gravou isso). Se recomputássemos _calc_payout
    # com settings atuais, poderíamos dar número diferente.
    ev = _event(
        db, EventState.FINALIZED, comp_id=None, title="CTA Antigo",
        tab_value=10000,
    )
    db.flush()
    db.add(EventParticipant(
        event_id=ev.id, guild_id=GUILD_ID, user_id=ALICE_UID, user_name="alice",
        percent=60, base_percent=60, silver_received=6000,
    ))
    db.add(EventParticipant(
        event_id=ev.id, guild_id=GUILD_ID, user_id=BOB_UID, user_name="bob",
        percent=40, base_percent=40, silver_received=4000,
    ))
    # Um participante que recebeu 0 não aparece na divulgação.
    db.add(EventParticipant(
        event_id=ev.id, guild_id=GUILD_ID, user_id=CAROL_UID, user_name="carol",
        percent=0, base_percent=0, silver_received=0,
    ))
    db.flush()
    client = _client(db, acting_uid=ALICE_UID)
    r = client.get(f"/guilds/{GUILD_ID}/member/events/{ev.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["settlement"] is not None
    s = body["settlement"]
    assert s["tab_value"] == 10000
    assert s["total_paid"] == 10000
    # Só os 2 que receberam > 0 aparecem; Carol (0) não.
    names = {p["display_name"] for p in s["participants"]}
    assert names == {"alice", "bob"}, names
    # Valores são os persistidos, não recomputados.
    amounts = {p["display_name"]: p["silver_received"] for p in s["participants"]}
    assert amounts == {"alice": 6000, "bob": 4000}, amounts


def test_evento_não_finalizado_não_divulga_settlement():
    db = _db()
    _seed_members(db)
    ev = _event(db, EventState.SCHEDULED, comp_id=None, title="Futuro")
    db.flush()
    client = _client(db, acting_uid=ALICE_UID)
    r = client.get(f"/guilds/{GUILD_ID}/member/events/{ev.id}")
    assert r.status_code == 200, r.text
    assert r.json()["settlement"] is None


def test_detalhe_member_safe_não_expoem_campos_admin():
    db = _db()
    _seed_members(db)
    ev = _event(db, EventState.SCHEDULED, comp_id=None, title="CTA")
    db.flush()
    client = _client(db, acting_uid=ALICE_UID)
    r = client.get(f"/guilds/{GUILD_ID}/member/events/{ev.id}")
    body = r.json()
    # Campos administrativos que NÃO devem aparecer.
    for forbidden in (
        "escalation_token", "allowed_transitions", "verification",
        "participants", "deaths", "signups", "battle_absentees",
        "regear_summary", "payout",
    ):
        assert forbidden not in body, f"membro não deve ver {forbidden!r}"


# ── 6. preferências arma+fn ──────────────────────────────────────────────────

def test_put_preferences_valida_contra_comps_ativas():
    db = _db()
    _seed_members(db)
    ids = _seed_comp_with_pairs(db)
    client = _client(db, acting_uid=ALICE_UID)
    # Par válido (presente na comp ativa) é aceito.
    r = client.put(
        f"/guilds/{GUILD_ID}/member/weapon-fn-preferences",
        json={"preferences": [{"weapon_id": ids["weapon_id"], "fn": "dps"}]},
    )
    assert r.status_code == 200, r.text
    prefs = r.json()["preferences"]
    assert len(prefs) == 1
    assert prefs[0]["weapon_id"] == ids["weapon_id"]
    assert prefs[0]["fn"] == "dps"

    # Par desconhecido (weapon inexistente) é rejeitado.
    r = client.put(
        f"/guilds/{GUILD_ID}/member/weapon-fn-preferences",
        json={"preferences": [{"weapon_id": 999999, "fn": "dps"}]},
    )
    assert r.status_code == 400, r.text


def test_put_preferences_rejeita_fn_vazio():
    db = _db()
    _seed_members(db)
    ids = _seed_comp_with_pairs(db)
    client = _client(db, acting_uid=ALICE_UID)
    r = client.put(
        f"/guilds/{GUILD_ID}/member/weapon-fn-preferences",
        json={"preferences": [{"weapon_id": ids["weapon_id"], "fn": ""}]},
    )
    assert r.status_code == 400, r.text


def test_put_preferences_sobrescreve_e_preserva_só_válidos():
    db = _db()
    _seed_members(db)
    ids = _seed_comp_with_pairs(db)
    # Preferência pré-existente.
    db.add(WeaponFnPreference(
        guild_id=GUILD_ID, user_id=ALICE_UID,
        weapon_id=ids["weapon_id"], fn="dps",
    ))
    db.flush()
    client = _client(db, acting_uid=ALICE_UID)
    # PUT com lista vazia apaga a preferência existente.
    r = client.put(
        f"/guilds/{GUILD_ID}/member/weapon-fn-preferences",
        json={"preferences": []},
    )
    assert r.status_code == 200, r.text
    assert r.json()["preferences"] == []


if __name__ == "__main__":
    # Roda todos como funções simples (sem pytest) — mesmo padrão dos outros
    # testes do repo (test_weapon_fn_identity.py, test_energy_portal.py).
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)