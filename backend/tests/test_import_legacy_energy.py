"""Importação da energia do bot legado — backend/scripts/import_legacy_energy.py.

Cobre o escopo pedido:
  - baseline (1ª rodada): user_balances.energy vira saldo + entry baseline,
    energy_log vira entries log, energy_whitelist é importada.
  - incremental idempotente: 2ª rodada SÓ aplica o delta do energy_log (dedup
    partial-unique pega qualquer sobreposição); saldos estáveis, sem duplicação.
  - whitelist é ADITIVA: re-rodada não remove entrada adicionada pelo site.

Abordagem: cria um SQLite legado em diretório temporário (tmp_path), roda
`import_guild()` direto contra o backend em memória (session SQLite in-memory
com os shims JSONB/BigInteger já consagrados nos outros testes).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Garante que backend/ está no path (pro script importar app.*).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base  # noqa: F401
from app.models.energy import EnergyBalance, EnergyEntry, EnergyWhitelist
from app.models.tenancy import Guild, GuildMember, User


# ── shims (mesmo padrão dos outros testes) ────────────────────────────────────

@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "INTEGER"


def _db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


GUILD_ID = 7777
ALICE_UID = 71001  # existe no backend (User)
BOB_UID = 71002    # existe no backend (User)
GHOST_UID = 71003  # NÃO existe no backend — deve ser pulado no baseline


def _make_legacy_db(path: Path, *, with_log_rows: list[tuple], balances: dict[int, int], wl: list[int]) -> None:
    """Cria um arquivo guild_<id>.db legado com schema mínimo (mesmas tabelas
    do bot-legacy/database.py)."""
    lite = sqlite3.connect(str(path))
    try:
        # user_balances (mesma estrutura do bot legado).
        lite.execute(
            "CREATE TABLE user_balances ("
            "  user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, "
            "  total_earned INTEGER DEFAULT 0, energy INTEGER DEFAULT 0)"
        )
        for uid, energy in balances.items():
            lite.execute(
                "INSERT INTO user_balances (user_id, energy) VALUES (?, ?)",
                (uid, energy),
            )
        # energy_log (mesma estrutura do bot legado).
        lite.execute(
            "CREATE TABLE energy_log ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, player TEXT, "
            "  reason TEXT, amount INTEGER, user_id INTEGER, created_at TEXT, "
            "  UNIQUE(ts, player, amount))"
        )
        for ts, player, reason, amount, user_id in with_log_rows:
            lite.execute(
                "INSERT INTO energy_log (ts, player, reason, amount, user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, player, reason, amount, user_id),
            )
        # energy_whitelist (mesma estrutura).
        lite.execute(
            "CREATE TABLE energy_whitelist (user_id INTEGER PRIMARY KEY, added_by INTEGER, added_at TEXT)"
        )
        for uid in wl:
            lite.execute("INSERT INTO energy_whitelist (user_id) VALUES (?)", (uid,))
        lite.commit()
    finally:
        lite.close()


def _seed_backend(db: Session) -> None:
    """Cria Guild + Users + GuildMembers no backend (só quem existe)."""
    db.add(Guild(id=GUILD_ID, name="Guilda Legada"))
    db.add(User(id=ALICE_UID, username="alice", global_name="Alice"))
    db.add(User(id=BOB_UID, username="bob", global_name="Bob"))
    # GHOST_UID de propósito NÃO é criado — testa o skip de usuário não resolvível.
    db.add(GuildMember(guild_id=GUILD_ID, user_id=ALICE_UID))
    db.add(GuildMember(guild_id=GUILD_ID, user_id=BOB_UID))
    db.flush()


def _n_entries(db: Session) -> int:
    return len(db.scalars(select(EnergyEntry).where(
        EnergyEntry.guild_id == GUILD_ID,
    )).all())


def _bal(db: Session, uid: int) -> int:
    v = db.scalar(select(EnergyBalance.balance).where(
        EnergyBalance.guild_id == GUILD_ID,
        EnergyBalance.discord_user_id == uid,
    ))
    return int(v) if v is not None else 0


# ── testes ────────────────────────────────────────────────────────────────────

def test_baseline_importa_saldos_log_e_whitelist():
    import tempfile

    from scripts import import_legacy_energy as imp

    db = _db()
    _seed_backend(db)

    # Fixture legada: Alice 100, Bob 50, Ghost (sem User no backend) 999.
    # Ghost também é importado — discord_user_id é a identidade, não User.
    balances = {ALICE_UID: 100, BOB_UID: 50, GHOST_UID: 999}
    log_rows = [
        ("2026-01-01 00:00:00", "Alice", "Deposit", 10, ALICE_UID),
        ("2026-01-01 00:01:00", "Bob", "Withdrawal", -5, BOB_UID),
    ]
    wl = [ALICE_UID]  # Alice cuida da energia — whitelist

    with tempfile.TemporaryDirectory() as d:
        legacy_dir = Path(d)
        _make_legacy_db(
            legacy_dir / f"guild_{GUILD_ID}.db",
            with_log_rows=log_rows, balances=balances, wl=wl,
        )
        r = imp.import_guild(db, legacy_dir, GUILD_ID, verbose=False)

    # Baseline: todos importados (Alice, Bob, Ghost) — discord_user_id é a
    # identidade, não precisa de User no backend.
    assert r["baseline_imported"] == 3
    # Log: 2 entries aplicadas.
    assert r["log_applied"] == 2
    # Whitelist: 1 adicionado.
    assert r["whitelist_added"] == 1

    # Saldo final: baseline + log delta. Ghost só baseline (sem log).
    assert _bal(db, ALICE_UID) == 110  # 100 + 10
    assert _bal(db, BOB_UID) == 45     # 50 - 5
    assert _bal(db, GHOST_UID) == 999  # só baseline

    # Ledger: Alice tem 1 baseline (100) + 1 log (10).
    alice_entries = db.scalars(select(EnergyEntry).where(
        EnergyEntry.guild_id == GUILD_ID, EnergyEntry.discord_user_id == ALICE_UID,
    )).all()
    assert sorted(e.kind for e in alice_entries) == ["baseline", "log"]
    assert sorted(e.amount for e in alice_entries) == [10, 100]

    # Invariante: balance == sum(amount).
    from app.services import energy as energy_svc
    assert energy_svc.ledger_reconciles(db, GUILD_ID, ALICE_UID)
    assert energy_svc.ledger_reconciles(db, GUILD_ID, BOB_UID)
    assert energy_svc.ledger_reconciles(db, GUILD_ID, GHOST_UID)

    # Whitelist importada.
    wl_uids = db.scalars(select(EnergyWhitelist.discord_user_id).where(
        EnergyWhitelist.guild_id == GUILD_ID,
    )).all()
    assert set(wl_uids) == {ALICE_UID}

    # Marcador no Guild.settings.
    g = db.scalar(select(Guild).where(Guild.id == GUILD_ID))
    assert g.settings.get("energy_import") is not None
    assert g.settings["energy_import"]["last_log_rowid"] == 2  # 2 rows no energy_log


def test_re_run_apenas_delta_sem_rebaseline_e_sem_duplicacao():
    import tempfile

    from scripts import import_legacy_energy as imp

    db = _db()
    _seed_backend(db)

    # 1ª rodada: 2 entries de log.
    log_rows_1 = [
        ("2026-01-01 00:00:00", "Alice", "Deposit", 10, ALICE_UID),
        ("2026-01-01 00:01:00", "Bob", "Withdrawal", -5, BOB_UID),
    ]
    balances_1 = {ALICE_UID: 100, BOB_UID: 50, GHOST_UID: 999}

    with tempfile.TemporaryDirectory() as d:
        legacy_dir = Path(d)
        _make_legacy_db(
            legacy_dir / f"guild_{GUILD_ID}.db",
            with_log_rows=log_rows_1, balances=balances_1, wl=[ALICE_UID],
        )
        r1 = imp.import_guild(db, legacy_dir, GUILD_ID, verbose=False)
        assert r1["log_applied"] == 2

        alice_after_1 = _bal(db, ALICE_UID)
        bob_after_1 = _bal(db, BOB_UID)
        n_entries_after_1 = _n_entries(db)

        # Adiciona 1 entry NOVA ao SQLite legado (simula nova log do jogo).
        lite = sqlite3.connect(str(legacy_dir / f"guild_{GUILD_ID}.db"))
        lite.execute(
            "INSERT INTO energy_log (ts, player, reason, amount, user_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("2026-01-02 00:00:00", "Alice", "Deposit", 20, ALICE_UID),
        )
        lite.commit()
        lite.close()

        # 2ª rodada: marcador existe → SÓ o delta (1 nova entry), sem
        # re-baselinar e sem duplicar as anteriores.
        r2 = imp.import_guild(db, legacy_dir, GUILD_ID, verbose=False)

    assert r2["already_baselined"] is True
    assert "baseline_imported" not in r2  # não re-baselina
    assert r2["log_applied"] == 1
    assert r2["log_duplicates"] == 0

    # Saldos estáveis + o novo delta.
    assert _bal(db, ALICE_UID) == 130  # 100 + 10 + 20
    assert _bal(db, BOB_UID) == 45     # nada mudou

    # Total de entries: as da 1ª + 1 nova.
    assert _n_entries(db) == n_entries_after_1 + 1

    # Invariante reconcilia.
    from app.services import energy as energy_svc
    assert energy_svc.ledger_reconciles(db, GUILD_ID, ALICE_UID)
    assert energy_svc.ledger_reconciles(db, GUILD_ID, BOB_UID)


def test_re_run_sem_novas_logs_nao_altera_nada():
    import tempfile

    from scripts import import_legacy_energy as imp

    db = _db()
    _seed_backend(db)

    log_rows = [
        ("2026-01-01 00:00:00", "Alice", "Deposit", 10, ALICE_UID),
    ]
    balances = {ALICE_UID: 100, BOB_UID: 50}

    with tempfile.TemporaryDirectory() as d:
        legacy_dir = Path(d)
        _make_legacy_db(
            legacy_dir / f"guild_{GUILD_ID}.db",
            with_log_rows=log_rows, balances=balances, wl=[],
        )
        r1 = imp.import_guild(db, legacy_dir, GUILD_ID, verbose=False)
        assert r1["log_applied"] == 1
        alice_after_1 = _bal(db, ALICE_UID)
        n_entries_1 = _n_entries(db)

        # 2ª rodada sem mudar nada: deve ser no-op (0 aplicadas, 0 dup).
        r2 = imp.import_guild(db, legacy_dir, GUILD_ID, verbose=False)

    assert r2["already_baselined"] is True
    assert r2["log_applied"] == 0
    assert r2["log_duplicates"] == 0

    # Saldo e ledger idênticos.
    assert _bal(db, ALICE_UID) == alice_after_1
    assert _n_entries(db) == n_entries_1


def test_re_run_nao_remove_whitelist_adicionada_pelo_site():
    """A whitelist do import é ADITIVA: quem foi adicionado pela rota admin do
    site DEPOIS da 1ª importação não pode ser removido por uma re-rodada — o
    legado está sendo aposentado, o site é a fonte da verdade dali em diante."""
    import tempfile

    from scripts import import_legacy_energy as imp

    db = _db()
    _seed_backend(db)

    with tempfile.TemporaryDirectory() as d:
        legacy_dir = Path(d)
        _make_legacy_db(
            legacy_dir / f"guild_{GUILD_ID}.db",
            with_log_rows=[], balances={ALICE_UID: 100}, wl=[],
        )
        imp.import_guild(db, legacy_dir, GUILD_ID, verbose=False)

        # Staff adiciona Bob pelo site (mesma rota admin que o teste de rotas
        # cobre) DEPOIS da importação.
        db.add(EnergyWhitelist(guild_id=GUILD_ID, discord_user_id=BOB_UID))
        db.commit()

        # Re-rodada: legado NÃO tem Bob na whitelist — não pode remover.
        r2 = imp.import_guild(db, legacy_dir, GUILD_ID, verbose=False)
        assert r2["whitelist_added"] == 0

    wl_uids = set(db.scalars(select(EnergyWhitelist.discord_user_id).where(
        EnergyWhitelist.guild_id == GUILD_ID,
    )).all())
    assert BOB_UID in wl_uids


def test_guilda_nao_existente_no_backend_e_skipped():
    import tempfile

    from scripts import import_legacy_energy as imp

    db = _db()
    # SEM _seed_backend — a guilda não existe no backend.

    with tempfile.TemporaryDirectory() as d:
        legacy_dir = Path(d)
        _make_legacy_db(
            legacy_dir / f"guild_{GUILD_ID}.db",
            with_log_rows=[], balances={}, wl=[],
        )
        r = imp.import_guild(db, legacy_dir, GUILD_ID, verbose=False)
    assert r.get("skipped") is True


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
