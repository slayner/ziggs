"""
Lootlog do companion: descoberta de eventos sem guild_id + autorização.
Roda com pytest OU: PYTHONPATH=. python tests/test_companion_lootlog.py

O companion não sabe (nem pergunta) a guilda: a inscrição do usuário no evento
é que define de quais eventos ele participa E em que guilda cada um está.
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.events import Event, EventSignup
from app.models.loot import ItemPriceCache
from app.models.tenancy import Guild, User
from app.api.routes.companion import (
    CompanionLootlogIngestIn, SilverEstimateIn, SilverEstimateItemIn,
    _LOOTLOG_STATES, companion_active_events, companion_lootlog_ingest,
    companion_lootlog_silver_estimate,
)

USER_ID = 1001
OTHER_USER_ID = 2002


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def _setup(db):
    """Duas guildas, um evento em cada estado, inscrições variadas."""
    for gid, name in ((10, "Guilda A"), (20, "Guilda B")):
        db.add(Guild(id=gid, name=name))
    db.add(User(id=USER_ID, username="slayner"))
    db.add(User(id=OTHER_USER_ID, username="outro"))

    events = [
        (1, 10, "in_progress"),  # inscrito
        (2, 20, "review"),       # inscrito, OUTRA guilda
        (3, 10, "finalized"),    # inscrito, mas estado fora da janela
        (4, 10, "in_progress"),  # NÃO inscrito
    ]
    for eid, gid, state in events:
        db.add(Event(id=eid, guild_id=gid, state=state, title=f"CTA {eid}"))
    for eid in (1, 2, 3):
        db.add(EventSignup(event_id=eid, guild_id=next(g for e, g, _ in events if e == eid),
                           user_id=USER_ID, functions=[]))
    db.add(EventSignup(event_id=4, guild_id=10, user_id=OTHER_USER_ID, functions=[]))
    db.commit()


def test_lista_eventos_de_todas_as_guildas_sem_pedir_guild_id():
    db = _session()
    _setup(db)
    user = db.get(User, USER_ID)
    out = companion_active_events(user=user, db=db)
    ids = sorted(e.event_id for e in out)
    assert ids == [1, 2], "in_progress e review, de guildas diferentes"
    por_id = {e.event_id: e for e in out}
    assert por_id[2].guild_id == 20
    assert por_id[2].guild_name == "Guilda B", "a guilda vem junto — o user não digita"
    assert por_id[2].state == "review"


def test_evento_de_outro_usuario_nao_aparece():
    db = _session()
    _setup(db)
    out = companion_active_events(user=db.get(User, USER_ID), db=db)
    assert 4 not in [e.event_id for e in out]


def test_estado_finalizado_fica_de_fora():
    db = _session()
    _setup(db)
    out = companion_active_events(user=db.get(User, USER_ID), db=db)
    assert 3 not in [e.event_id for e in out]
    assert "finalized" not in _LOOTLOG_STATES


def test_ingest_exige_review_e_guilda_consistente():
    db = _session()
    _setup(db)
    body = CompanionLootlogIngestIn(event_id=1, csv_text="x")
    user = db.get(User, USER_ID)
    try:
        companion_lootlog_ingest(body, user, db)
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("evento em andamento aceitou lootlog")

    signup = db.scalar(select(EventSignup).where(
        EventSignup.event_id == 2, EventSignup.user_id == USER_ID,
    ))
    signup.guild_id = 10
    db.commit()
    body.event_id = 2
    try:
        companion_lootlog_ingest(body, user, db)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("guilda divergente aceitou lootlog")


def test_estimativa_de_prata_usa_apenas_cache_local():
    db = _session()
    db.add(ItemPriceCache(
        item_type="T4_BAG", silver_value=123,
        fetched_at=datetime.now(timezone.utc),
    ))
    db.commit()
    out = companion_lootlog_silver_estimate(SilverEstimateIn(items=[
        SilverEstimateItemIn(item_id="T4_BAG", quantity=2),
        SilverEstimateItemIn(item_id="SEM_CACHE", quantity=9),
    ]), db)
    assert out.silver_total == 246


if __name__ == "__main__":
    test_lista_eventos_de_todas_as_guildas_sem_pedir_guild_id()
    test_evento_de_outro_usuario_nao_aparece()
    test_estado_finalizado_fica_de_fora()
    test_ingest_exige_review_e_guilda_consistente()
    test_estimativa_de_prata_usa_apenas_cache_local()
    print("companion lootlog OK")
