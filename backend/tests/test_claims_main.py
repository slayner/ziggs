"""
Testes da invariante de conta main (registered_characters.is_main) — banco
sqlite em memória, sem rede. Roda com pytest OU direto:
    PYTHONPATH=. python tests/test_claims_main.py
"""
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.claims import RegisteredCharacter
from app.services.claim_checker import register_character


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[RegisteredCharacter.__table__])
    return sessionmaker(bind=engine)()


def _claim(user_id: int, pid: str, name: str):
    # register_character só lê estes campos — não precisa de CharacterClaim real
    return SimpleNamespace(id=1, user_id=user_id, albion_player_id=pid,
                           albion_player_name=name, region="americas")


def _mains(db, user_id: int) -> list[str]:
    return list(db.scalars(select(RegisteredCharacter.albion_player_name).where(
        RegisteredCharacter.user_id == user_id, RegisteredCharacter.is_main == True,  # noqa: E712
    )))


def test_primeiro_personagem_vira_main():
    db = _session()
    register_character(db, _claim(1, "P1", "Slayner"))
    db.commit()
    assert _mains(db, 1) == ["Slayner"]


def test_segundo_personagem_e_alt():
    db = _session()
    register_character(db, _claim(1, "P1", "Slayner"))
    register_character(db, _claim(1, "P2", "AltDoSlayner"))
    db.commit()
    assert _mains(db, 1) == ["Slayner"]


def test_reverificacao_pelo_mesmo_dono_nao_mexe_na_main():
    db = _session()
    register_character(db, _claim(1, "P1", "Slayner"))
    register_character(db, _claim(1, "P2", "Alt"))
    db.commit()
    register_character(db, _claim(1, "P1", "Slayner"))  # re-verifica a main
    db.commit()
    assert _mains(db, 1) == ["Slayner"]


def test_personagem_roubado_promove_main_do_dono_anterior():
    db = _session()
    register_character(db, _claim(1, "P1", "MainDoA"))   # main do user 1
    register_character(db, _claim(1, "P2", "AltDoA"))
    db.commit()
    register_character(db, _claim(2, "P1", "MainDoA"))   # user 2 rouba a main do 1
    db.commit()
    assert _mains(db, 1) == ["AltDoA"], "dono anterior deve ter a alt promovida"
    assert _mains(db, 2) == ["MainDoA"], "novo dono sem outros chars: roubado vira main"


def test_personagem_roubado_por_quem_ja_tem_main_entra_como_alt():
    db = _session()
    register_character(db, _claim(1, "P1", "SoloDoA"))
    register_character(db, _claim(2, "P2", "MainDoB"))
    db.commit()
    register_character(db, _claim(2, "P1", "SoloDoA"))  # B rouba o único char de A
    db.commit()
    assert _mains(db, 2) == ["MainDoB"], "main do novo dono não muda"
    assert _mains(db, 1) == [], "dono anterior ficou sem personagens (nada a promover)"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("todos passaram")
