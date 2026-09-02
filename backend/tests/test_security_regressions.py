"""Isolamento de tenant em /auth/guild-info e rotas removidas do Companion."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.api.routes import auth, companion, lootlog
from app.models import Base
from app.models.tenancy import Guild, GuildMember, User


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "INTEGER"


ATTACKER_ID = 1001
VICTIM_ID = 1002
ATTACKER_GUILD_ID = 2001
VICTIM_GUILD_ID = 2002


def _db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _session_factory(db: Session):
    def _get():
        yield db
    return _get


def _client(db: Session, user_id: int | None) -> TestClient:
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(companion.router)
    app.include_router(lootlog.router)
    app.dependency_overrides = {deps.db_session: _session_factory(db)}
    if user_id is not None:
        app.dependency_overrides[deps.optional_user] = lambda: db.get(User, user_id)
    return TestClient(app)


def _seed(db: Session) -> None:
    db.add_all([
        User(id=ATTACKER_ID, username="atacante"),
        User(id=VICTIM_ID, username="vitima"),
        Guild(id=ATTACKER_GUILD_ID, name="Guilda atacante", bank_balance=10),
        Guild(
            id=VICTIM_GUILD_ID,
            name="Guilda vítima",
            bank_balance=999,
            settings={"logs_channel_id": "segredo", "guild_tax_percent": 15},
        ),
        GuildMember(guild_id=ATTACKER_GUILD_ID, user_id=ATTACKER_ID),
        GuildMember(guild_id=VICTIM_GUILD_ID, user_id=VICTIM_ID),
    ])
    db.commit()


def test_guild_info_rejeita_usuario_de_outra_guilda():
    db = _db()
    _seed(db)

    response = _client(db, ATTACKER_ID).get(f"/auth/guild-info/{VICTIM_GUILD_ID}")

    assert response.status_code == 403
    assert "Guilda vítima" not in response.text
    assert "segredo" not in response.text
    assert "999" not in response.text


def test_guild_info_entrega_dados_ao_membro():
    db = _db()
    _seed(db)

    response = _client(db, VICTIM_ID).get(f"/auth/guild-info/{VICTIM_GUILD_ID}")

    assert response.status_code == 200
    assert response.json()["settings"]["logs_channel_id"] == "segredo"
    assert response.json()["bank_balance"] == 999


def test_guild_info_exige_sessao():
    db = _db()
    _seed(db)

    response = _client(db, None).get(f"/auth/guild-info/{VICTIM_GUILD_ID}")

    assert response.status_code == 401


def test_rotas_web_legacy_e_oauth_do_companion_nao_existem():
    db = _db()
    _seed(db)
    client = _client(db, ATTACKER_ID)

    assert client.post(
        f"/guilds/{ATTACKER_GUILD_ID}/lootlog/ingest",
        files={"file": ("log.csv", b"x", "text/csv")},
        data={"event_id": "1"},
    ).status_code == 405
    assert client.get("/companion/auth/start?nonce=teste").status_code == 404
    assert client.get("/companion/auth/poll?nonce=teste").status_code == 404
    assert client.get("/companion/lootlog/active-events").status_code == 404
    assert client.post("/companion/lootlog/ingest", json={}).status_code == 404
