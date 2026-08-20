"""Fundação de energia do portal do membro — parser, dedup, whitelist e
reconciliação do ledger. SQLite em memória, sem rede. Roda com pytest OU:

    PYTHONPATH=. python tests/test_energy_portal.py

Cobre:
  - parser da log (formato do jogo, cabeçalho, inválidas, vírgula em amount).
  - apply_parsed_entries: aplica, dedup por (ts,player,amount), whitelist skip,
    unregistered ignorados, saldo == soma do ledger.
  - manual_set: emite lançamento compensatório e mantém a invariante.
  - toggle_whitelist/list_whitelist: add, remove, toggle, lista.
  - ledger_reconciles: True quando bate, False quando alguém desbalanceia.

Nota: o helper `pk()` em base.py usa `BigInteger()` puro, que no SQLite vira
`BIGINT NOT NULL PRIMARY KEY` (NÃO auto-incrementa — só `INTEGER PRIMARY KEY`
auto-incrementa). A migration de produção usa `BigInteger().with_variant(
Integer(), "sqlite")` (helper `_bigint()`), então o schema real (Postgres)
está correto. Aqui em teste, o `INTEGER` variant é aplicado compilando o
`BigInteger` como `INTEGER` no dialect SQLite — mesmo efeito da migration.
"""
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.energy import EnergyBalance, EnergyEntry, EnergyWhitelist
from app.services import energy as energy_svc


def _session():
    # `pk()` em base.py usa `BigInteger()` puro, que no SQLite vira
    # `BIGINT NOT NULL PRIMARY KEY` (NÃO auto-incrementa — só `INTEGER PRIMARY
    # KEY` auto-incrementa). A migration de produção usa o variant
    # `BigInteger().with_variant(Integer(), "sqlite")` (helper `_bigint()`),
    # então o schema real (Postgres) está correto; só o `create_all` do modelo
    # no SQLite de teste peca. Compilamos BigInteger como INTEGER só no
    # `create_all` — mesmo efeito do variant da migration, sem mexer em
    # `base.pk()` (compartilhado com tudo).
    _orig = SQLiteTypeCompiler.visit_big_integer
    SQLiteTypeCompiler.visit_big_integer = lambda self, type_, **kw: "INTEGER"
    try:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[
            EnergyBalance.__table__, EnergyEntry.__table__, EnergyWhitelist.__table__,
        ])
    finally:
        SQLiteTypeCompiler.visit_big_integer = _orig
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def _names(uid_map: dict[str, int]):
    """Monta um name_resolver que só conhece o mapa dado."""
    def resolver(db, guild_id, nick):
        return uid_map.get(nick)
    return resolver


LOG = (
    '"Date"  "Player"  "Reason"  "Amount"\n'
    '"2026-06-01 01:04:26"  "Andzada"  "Deposit"  "6"\n'
    '"2026-06-01 00:29:55"  "S1GNE"    "Withdrawal"  "-10"\n'
    '"2026-06-01 02:00:00"  "Desconhecido"  "Deposit"  "5"\n'
    '"linha sem aspas"\n'
    '"2026-06-01 03:00:00"  "Andzada"  "Deposit"  "1,000"\n'  # vírgula em amount
)


# ============================ parser ============================

def test_parser_porta_a_log_do_jogo():
    entries = energy_svc.parse_energy_log(LOG)
    # 4 válidas (cabeçalho pulado, linha sem aspas pulada).
    assert entries == [
        ("2026-06-01 01:04:26", "Andzada", "Deposit", 6),
        ("2026-06-01 00:29:55", "S1GNE", "Withdrawal", -10),
        ("2026-06-01 02:00:00", "Desconhecido", "Deposit", 5),
        ("2026-06-01 03:00:00", "Andzada", "Deposit", 1000),
    ]


def test_parser_pula_cabecalho_e_linhas_invalidas():
    entries = energy_svc.parse_energy_log(
        '"Date" "Player" "Reason" "Amount"\n'
        '"x" "y"\n'  # só 2 campos
        '"2026-01-01 00:00:00" "" "Deposit" "1"\n'  # player vazio
        '"2026-01-01 00:00:00" "X" "D" "nao-numero"\n'  # amount inválido
    )
    assert entries == []


def test_parser_aceita_separador_por_espacos():
    entries = energy_svc.parse_energy_log(
        '"2026-06-01 01:04:26" "Andzada" "Deposit" "6"'
    )
    assert entries == [("2026-06-01 01:04:26", "Andzada", "Deposit", 6)]


# ============================ apply_parsed_entries ============================

def test_apply_aplica_e_soma_ao_saldo():
    db = _session()
    res = energy_svc.apply_parsed_entries(
        db, 7, energy_svc.parse_energy_log(LOG),
        name_resolver=_names({"Andzada": 1, "S1GNE": 2}),
    )
    # Andzada: 6 + 1000 = 1006; S1GNE: -10. Desconhecido: 1 ignorado.
    assert res.applied == 3
    assert res.duplicates == 0
    assert res.whitelisted_applied == 0
    assert res.unregistered == {"Desconhecido": 1}
    assert energy_svc.get_balance(db, 7, 1) == 1006
    assert energy_svc.get_balance(db, 7, 2) == -10
    # Invariante do ledger.
    assert energy_svc.ledger_reconciles(db, 7, 1)
    assert energy_svc.ledger_reconciles(db, 7, 2)


def test_apply_dedup_nao_conta_em_dobro():
    db = _session()
    entries = energy_svc.parse_energy_log(LOG)
    resolver = _names({"Andzada": 1, "S1GNE": 2, "Desconhecido": 3})
    first = energy_svc.apply_parsed_entries(db, 7, entries, name_resolver=resolver)
    second = energy_svc.apply_parsed_entries(db, 7, entries, name_resolver=resolver)
    # segunda vez: tudo duplicado (mesma log colada 2x).
    assert first.applied == 4
    assert second.applied == 0
    assert second.duplicates == 4
    # Saldo não muda na segunda aplicação.
    assert energy_svc.get_balance(db, 7, 1) == 1006
    assert energy_svc.get_balance(db, 7, 2) == -10
    assert energy_svc.get_balance(db, 7, 3) == 5


def test_apply_whitelist_aplica_normalmente():
    """Whitelist não bloqueia mais ingestão — todas as entradas são
    aplicadas, inclusive as de whitelisted. A whitelist só controla alertas
    de energia baixa (que é responsabilidade do caller, não do serviço)."""
    db = _session()
    energy_svc.toggle_whitelist(db, 7, 1, added_by=99)  # Andzada na whitelist
    res = energy_svc.apply_parsed_entries(
        db, 7, energy_svc.parse_energy_log(LOG),
        name_resolver=_names({"Andzada": 1, "S1GNE": 2, "Desconhecido": 3}),
    )
    # Todas as 4 entradas aplicadas (Andzada incluído — whitelist não ignora).
    assert res.applied == 4
    assert res.whitelisted_applied == 2  # Andzada tem 2 lançamentos
    assert energy_svc.get_balance(db, 7, 1) == 1006  # Andzada: 6 + 1000
    assert energy_svc.get_balance(db, 7, 2) == -10
    assert energy_svc.get_balance(db, 7, 3) == 5
    # Ledger tem entries de Andzada (não é mais ignorado).
    rows = db.query(EnergyEntry).filter_by(guild_id=7, discord_user_id=1).all()
    assert len(rows) == 2


def test_apply_unregistered_acumula_por_nick():
    db = _session()
    res = energy_svc.apply_parsed_entries(
        db, 7,
        [
            ("2026-01-01 00:00:00", "Ghost", "Deposit", 1),
            ("2026-01-01 00:01:00", "Ghost", "Deposit", 2),
            ("2026-01-01 00:02:00", "Other", "Deposit", 3),
        ],
        name_resolver=_names({}),  # ninguém registrado
    )
    assert res.applied == 0
    assert res.unregistered == {"Ghost": 2, "Other": 1}


# ============================ manual_set ============================

def test_manual_set_emite_lancamento_compensatorio_e_mantem_invariante():
    db = _session()
    # Primeiro aplica uns lançamentos.
    energy_svc.apply_parsed_entries(
        db, 7,
        [("2026-01-01 00:00:00", "X", "Deposit", 100)],
        name_resolver=_names({"X": 1}),
    )
    assert energy_svc.get_balance(db, 7, 1) == 100

    # Ajuste manual: define pra 250.
    new = energy_svc.manual_set(db, 7, 1, 250, actor_discord_id=9, reason="ajuste")
    assert new == 250
    assert energy_svc.get_balance(db, 7, 1) == 250

    # Ledger: entry de log (100) + entry de adjustment (150) = 250.
    rows = db.query(EnergyEntry).filter_by(guild_id=7, discord_user_id=1).all()
    kinds = sorted(r.kind for r in rows)
    amounts = [r.amount for r in rows]
    assert kinds == ["adjustment", "log"]
    assert sum(amounts) == 250
    assert energy_svc.ledger_reconciles(db, 7, 1)


def test_manual_set_com_mesmo_valor_nao_emite_lancamento():
    db = _session()
    energy_svc.apply_parsed_entries(
        db, 7,
        [("2026-01-01 00:00:00", "X", "Deposit", 100)],
        name_resolver=_names({"X": 1}),
    )
    before = db.query(EnergyEntry).filter_by(guild_id=7, discord_user_id=1).count()
    energy_svc.manual_set(db, 7, 1, 100)
    after = db.query(EnergyEntry).filter_by(guild_id=7, discord_user_id=1).count()
    assert before == after  # nada a fazer, não polui o ledger


def test_manual_set_em_membro_novo_cria_saldo_e_ledger():
    db = _session()
    # Membro que nunca teve log — ajuste manual direto pra 500.
    new = energy_svc.manual_set(db, 7, 42, 500, actor_discord_id=9, reason="seed")
    assert new == 500
    assert energy_svc.get_balance(db, 7, 42) == 500
    rows = db.query(EnergyEntry).filter_by(guild_id=7, discord_user_id=42).all()
    assert len(rows) == 1
    assert rows[0].kind == "adjustment"
    assert rows[0].amount == 500
    assert rows[0].actor_discord_id == 9
    assert rows[0].reason == "seed"
    assert energy_svc.ledger_reconciles(db, 7, 42)


# ============================ toggle/list whitelist ============================

def test_toggle_whitelist_add_depois_remove():
    db = _session()
    assert energy_svc.toggle_whitelist(db, 7, 1, added_by=9) is True  # adicionou
    assert energy_svc.list_whitelist(db, 7) == [1]
    assert energy_svc.toggle_whitelist(db, 7, 1) is False  # removeu
    assert energy_svc.list_whitelist(db, 7) == []


def test_list_whitelist_tenant_scoped_por_guilda():
    db = _session()
    energy_svc.toggle_whitelist(db, 7, 1)
    energy_svc.toggle_whitelist(db, 7, 2)
    energy_svc.toggle_whitelist(db, 8, 1)  # outra guilda
    assert set(energy_svc.list_whitelist(db, 7)) == {1, 2}
    assert energy_svc.list_whitelist(db, 8) == [1]


# ============================ ledger_reconciles ============================

def test_ledger_reconciles_detecta_desequilibrio():
    db = _session()
    energy_svc.apply_parsed_entries(
        db, 7,
        [("2026-01-01 00:00:00", "X", "Deposit", 100)],
        name_resolver=_names({"X": 1}),
    )
    assert energy_svc.ledger_reconciles(db, 7, 1)
    # Sabotagem direta do saldo (simula bug que quebra a invariante).
    bal = db.query(EnergyBalance).filter_by(guild_id=7, discord_user_id=1).one()
    bal.balance = 9999
    db.flush()
    assert energy_svc.ledger_reconciles(db, 7, 1) is False


if __name__ == "__main__":
    mod = __import__("tests.test_energy_portal", fromlist=["x"])
    for name in dir(mod):
        if name.startswith("test_"):
            getattr(mod, name)()
            print(f"  ok: {name}")
    print("energy portal foundation OK")