"""Admin de energia — /guilds/{guild_id}/energy-admin/*.

Cobre o escopo pedido:
  - permissão: membro sem `energy.manage` → 403; admin de guilda → 200.
  - log-import: aplica, dedup em re-import, reporta unregistered.
  - manual set via rota: emite ajuste compensatório.
  - whitelist toggle: add/remove via mesma rota.
  - overview tenancy: bounded por guilda, flag low-energy pelo threshold.

Abordagem: app FastAPI com SÓ o router energy_admin + deps override
(db_session, optional_user → require_permission). SQLite em memória com os
shims JSONB/BigInteger já consagrados nos outros testes. Mesma estrutura
de test_member_portal.py.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.api.routes import energy_admin
from app.models import Base  # noqa: F401
from app.models.energy import EnergyBalance, EnergyEntry, EnergyWhitelist
from app.models.registration import BotRegistration
from app.models.tenancy import Guild, GuildMember, User


# ── shims (mesmo padrão de test_member_portal.py) ────────────────────────────

@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "INTEGER"


def _engine():
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
    def _get():
        yield db
    return _get


# ── fixtures ─────────────────────────────────────────────────────────────────

GUILD_ID = 3001
ADMIN_UID = 4001   # admin da guilda (is_guild_admin=True)
MEMBER_UID = 4002  # membro sem permissão energy.manage
BOB_UID = 4003     # alvo de ajustes (membro comum)


def _seed(db: Session) -> None:
    db.add(Guild(id=GUILD_ID, name="Guilda Energia"))
    db.add(User(id=ADMIN_UID, username="admin", global_name="Admin"))
    db.add(User(id=MEMBER_UID, username="membro", global_name="Membro"))
    db.add(User(id=BOB_UID, username="bob", global_name="Bob"))
    db.add(GuildMember(guild_id=GUILD_ID, user_id=ADMIN_UID, is_guild_admin=True))
    db.add(GuildMember(guild_id=GUILD_ID, user_id=MEMBER_UID, is_guild_admin=False))
    db.add(GuildMember(guild_id=GUILD_ID, user_id=BOB_UID, is_guild_admin=False))
    db.flush()


def _registration(db: Session, user_id: int, nick: str, guild_id: int = GUILD_ID) -> None:
    """Cria um BotRegistration ativo (mesma fonte que o name resolver usa)."""
    db.add(BotRegistration(
        guild_id=guild_id, discord_user_id=user_id,
        albion_player_id=f"manual:{nick.lower()}", albion_player_name=nick,
        region="americas", role_id=9999, active=True,
    ))
    db.flush()


def _client(db: Session, *, acting_uid: int) -> TestClient:
    """App FastAPI com SÓ o router energy_admin. O acting_uid vira o user
    logado; require_permission roda de verdade (procura GuildMember do uid
    e checa has_permission / is_guild_admin)."""
    app = FastAPI()
    app.include_router(energy_admin.router)
    app.dependency_overrides = {
        deps.db_session: _session_factory(db),
        deps.optional_user: lambda: db.get(User, acting_uid),
    }
    return TestClient(app)


LOG = (
    '"Date"  "Player"  "Reason"  "Amount"\n'
    '"2026-06-01 01:04:26"  "Andzada"  "Deposit"  "6"\n'
    '"2026-06-01 00:29:55"  "S1GNE"    "Withdrawal"  "-10"\n'
    '"2026-06-01 02:00:00"  "Desconhecido"  "Deposit"  "5"\n'
)


# ── 1. permissão ──────────────────────────────────────────────────────────────

def test_membro_sem_permissao_recebe_403_no_overview():
    db = _db()
    _seed(db)
    client = _client(db, acting_uid=MEMBER_UID)
    r = client.get(f"/guilds/{GUILD_ID}/energy-admin/overview")
    assert r.status_code == 403, r.text


def test_admin_de_guilda_tem_acesso_ao_overview():
    db = _db()
    _seed(db)
    client = _client(db, acting_uid=ADMIN_UID)
    r = client.get(f"/guilds/{GUILD_ID}/energy-admin/overview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["threshold"] == 50  # default
    # Admin e Bob (membros ativos da guilda) aparecem; Membro também.
    names = {m["display_name"] for m in body["members"]}
    assert {"Admin", "Membro", "Bob"} <= names


def test_membro_sem_permissao_recebe_403_no_log_import():
    db = _db()
    _seed(db)
    client = _client(db, acting_uid=MEMBER_UID)
    r = client.post(
        f"/guilds/{GUILD_ID}/energy-admin/log-import",
        json={"log_text": LOG},
    )
    assert r.status_code == 403, r.text


# ── 2. log-import: aplica + dedup + unregistered ──────────────────────────────

def test_log_import_aplica_lancamentos_e_reporta_unregistered():
    db = _db()
    _seed(db)
    # Registros ativos casam "Andzada" → ADMIN_UID e "S1GNE" → BOB_UID.
    _registration(db, ADMIN_UID, "Andzada")
    _registration(db, BOB_UID, "S1GNE")
    client = _client(db, acting_uid=ADMIN_UID)
    r = client.post(
        f"/guilds/{GUILD_ID}/energy-admin/log-import",
        json={"log_text": LOG},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 2 aplicados (Andzada, S1GNE); "Desconhecido" 1 não registrado.
    assert body["result"]["applied"] == 2
    assert body["result"]["duplicates"] == 0
    assert body["unregistered"] == {"Desconhecido": 1}
    # AuditLog gravado.
    from app.models.audit import AuditLog
    logs = db.scalars(select(AuditLog).where(AuditLog.guild_id == GUILD_ID)).all()
    assert len(logs) == 1
    assert logs[0].action == "energy.log_import"


def test_log_import_dedup_nao_aplica_duplicatas_na_segunda_rodada():
    db = _db()
    _seed(db)
    _registration(db, ADMIN_UID, "Andzada")
    _registration(db, BOB_UID, "S1GNE")
    _registration(db, MEMBER_UID, "Desconhecido")
    client = _client(db, acting_uid=ADMIN_UID)
    # 1ª rodada.
    r1 = client.post(
        f"/guilds/{GUILD_ID}/energy-admin/log-import",
        json={"log_text": LOG},
    )
    assert r1.status_code == 200
    assert r1.json()["result"]["applied"] == 3  # agora Desconhecido casa
    # 2ª rodada — mesma log colada de novo.
    r2 = client.post(
        f"/guilds/{GUILD_ID}/energy-admin/log-import",
        json={"log_text": LOG},
    )
    assert r2.status_code == 200
    res2 = r2.json()["result"]
    assert res2["applied"] == 0
    assert res2["duplicates"] == 3  # tudo duplicado
    # Saldos não mudaram.
    assert db.scalar(select(EnergyBalance.balance).where(
        EnergyBalance.guild_id == GUILD_ID,
        EnergyBalance.discord_user_id == ADMIN_UID,
    )) == 6  # Andzada Deposit 6


def test_log_import_vazio_devolve_400():
    db = _db()
    _seed(db)
    client = _client(db, acting_uid=ADMIN_UID)
    r = client.post(
        f"/guilds/{GUILD_ID}/energy-admin/log-import",
        json={"log_text": "sem aspas"},
    )
    assert r.status_code == 400, r.text


# ── 3. manual set ─────────────────────────────────────────────────────────────

def test_manual_set_via_rota_emite_ajuste_compensatorio():
    db = _db()
    _seed(db)
    # Bob começa com saldo 100 (via log aplicada antes).
    db.add(EnergyBalance(guild_id=GUILD_ID, discord_user_id=BOB_UID, balance=100))
    db.add(EnergyEntry(
        guild_id=GUILD_ID, discord_user_id=BOB_UID, kind="log",
        ts="2026-01-01 00:00:00", player="bob", amount=100,
    ))
    db.flush()
    client = _client(db, acting_uid=ADMIN_UID)
    r = client.post(
        f"/guilds/{GUILD_ID}/energy-admin/set",
        json={"user_id": BOB_UID, "value": 250, "reason": "ajuste"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": BOB_UID, "balance": 250}
    # Saldo foi atualizado.
    bal = db.scalar(select(EnergyBalance.balance).where(
        EnergyBalance.guild_id == GUILD_ID,
        EnergyBalance.discord_user_id == BOB_UID,
    ))
    assert bal == 250
    # Ledger tem 2 entries: log (100) + adjustment (150).
    rows = db.scalars(select(EnergyEntry).where(
        EnergyEntry.guild_id == GUILD_ID,
        EnergyEntry.discord_user_id == BOB_UID,
    )).all()
    kinds = sorted(r.kind for r in rows)
    amounts = [r.amount for r in rows]
    assert kinds == ["adjustment", "log"]
    assert sum(amounts) == 250
    # AuditLog.
    from app.models.audit import AuditLog
    logs = db.scalars(select(AuditLog).where(
        AuditLog.guild_id == GUILD_ID, AuditLog.action == "energy.manual_set",
    )).all()
    assert len(logs) == 1
    assert logs[0].before == {"balance": 100}


# ── 4. whitelist toggle ────────────────────────────────────────────────────────

def test_whitelist_toggle_adiciona_e_remove():
    db = _db()
    _seed(db)
    client = _client(db, acting_uid=ADMIN_UID)
    # 1ª chamada: adiciona Bob.
    r1 = client.post(f"/guilds/{GUILD_ID}/energy-admin/whitelist/{BOB_UID}")
    assert r1.status_code == 200
    assert r1.json() == {"user_id": BOB_UID, "whitelisted": True}
    assert BOB_UID in energy_svc_list(db, GUILD_ID)
    # 2ª chamada: remove.
    r2 = client.post(f"/guilds/{GUILD_ID}/energy-admin/whitelist/{BOB_UID}")
    assert r2.status_code == 200
    assert r2.json() == {"user_id": BOB_UID, "whitelisted": False}
    assert BOB_UID not in energy_svc_list(db, GUILD_ID)
    # AuditLog (uma pra add, outra pra remove).
    from app.models.audit import AuditLog
    logs = db.scalars(select(AuditLog).where(
        AuditLog.guild_id == GUILD_ID, AuditLog.action == "energy.whitelist_toggle",
    )).all()
    assert len(logs) == 2


def energy_svc_list(db: Session, guild_id: int) -> list[int]:
    """Atalho pros testes: lista discord_user_ids da whitelist."""
    from app.services import energy as energy_svc
    return energy_svc.list_whitelist(db, guild_id)


# ── 5. overview tenancy ────────────────────────────────────────────────────────

def test_overview_eh_tenant_scoped_e_nao_vaza_outra_guilda():
    db = _db()
    _seed(db)
    # Outra guilda com um membro que NÃO deve aparecer no overview da GUILD_ID.
    OTHER = 3002
    db.add(Guild(id=OTHER, name="Outra"))
    db.add(User(id=5001, username="other", global_name="Other"))
    db.add(GuildMember(guild_id=OTHER, user_id=5001, is_guild_admin=True))
    db.add(EnergyBalance(guild_id=OTHER, discord_user_id=5001, balance=999))
    # Membro da GUILD_ID com energia baixa (10 < 50).
    db.add(EnergyBalance(guild_id=GUILD_ID, discord_user_id=BOB_UID, balance=10))
    db.flush()
    client = _client(db, acting_uid=ADMIN_UID)
    r = client.get(f"/guilds/{GUILD_ID}/energy-admin/overview")
    assert r.status_code == 200
    body = r.json()
    uids = {m["user_id"] for m in body["members"]}
    assert 5001 not in uids  # outra guilda
    # Bob tem energia baixa.
    bob_row = next(m for m in body["members"] if m["user_id"] == BOB_UID)
    assert bob_row["low_energy"] is True
    assert bob_row["balance"] == 10


def test_overview_respeita_threshold_custom_da_guilda():
    db = _db()
    _seed(db)
    # Threshold custom = 20.
    g = db.scalar(select(Guild).where(Guild.id == GUILD_ID))
    g.settings = {"energy_alert_threshold": 20}
    db.add(EnergyBalance(guild_id=GUILD_ID, discord_user_id=BOB_UID, balance=25))
    db.flush()
    client = _client(db, acting_uid=ADMIN_UID)
    r = client.get(f"/guilds/{GUILD_ID}/energy-admin/overview")
    assert r.json()["threshold"] == 20
    bob_row = next(m for m in r.json()["members"] if m["user_id"] == BOB_UID)
    assert bob_row["low_energy"] is False  # 25 >= 20


def test_overview_nao_lista_membros_que_sairam():
    db = _db()
    _seed(db)
    # Marca o Bob como saiu (left_at set).
    bob = db.scalar(select(GuildMember).where(
        GuildMember.guild_id == GUILD_ID, GuildMember.user_id == BOB_UID,
    ))
    from datetime import datetime, timezone
    bob.left_at = datetime.now(timezone.utc)
    db.flush()
    client = _client(db, acting_uid=ADMIN_UID)
    r = client.get(f"/guilds/{GUILD_ID}/energy-admin/overview")
    uids = {m["user_id"] for m in r.json()["members"]}
    assert BOB_UID not in uids


if __name__ == "__main__":
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