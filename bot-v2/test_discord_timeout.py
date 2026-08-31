"""Regressão do travamento de awaits: chamadas ao Discord API que penduram
para sempre (rate-limit silencioso / rede morta) não podem prender o loop de
background segurando o asyncio.Lock da guilda até o restart.

Sintoma original: awaits presos que só se soltavam no restart do bot; logs,
juicy-kills e embeds de evento acumulados eram postados de uma vez no boot.

Run directly: python test_discord_timeout.py"""
import asyncio
import time
from types import SimpleNamespace

import discord

import cogs.juicy_kills as jk
from cogs._discord_timeout import API_TIMEOUT, SKIP_EXC, dtimeout


async def _hang_forever(*_a, **_kw):
    await asyncio.sleep(3600)
    raise AssertionError("não devia chegar aqui")


def test_dtimeout_converte_hang_em_timeouterror():
    async def run():
        try:
            await dtimeout(_hang_forever(), timeout=0.1)
        except asyncio.TimeoutError:
            return True
        return False
    assert asyncio.run(run())


def test_timeouterror_esta_no_skip_exc():
    # O SKIP_EXC precisa cobrir o TimeoutError do wait_for, senão o timeout
    # explode no loop em vez de virar skip.
    assert asyncio.TimeoutError in SKIP_EXC or issubclass(asyncio.TimeoutError, SKIP_EXC)


def test_juicy_kills_nao_trava_com_channel_send_pendurado():
    """channel.send que NUNCA resolve: sync_guild precisa retornar dentro de
    ~API_TIMEOUT (o wait_for cancela o send), sem prender o lock da guilda.
    O kill não é ackado (ack só após send com sucesso) — próximo tick retenta."""
    sent_states = {"calls": 0}

    class HangingChannel:
        id = 55

        async def send(self, *_a, **_kw):
            sent_states["calls"] += 1
            await asyncio.sleep(3600)  # pendura pra sempre

        async def history(self, *_a, **_kw):
            # Async iterator vazio — simula canal sem histórico (1º poll).
            return
            yield  # pragma: no cover — só pra ser um async generator

    class Guild:
        id = 1

        def get_channel(self, _cid):
            return HangingChannel()

        async def fetch_channel(self, _cid):
            return HangingChannel()

        filesize_limit = 8 * 1024 * 1024

    queue = {"kills": [{
        "id": 7, "region": "americas", "albion_event_id": "123",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "killer": {"name": "K", "id": "k1"},
        "victim": {"name": "V", "id": "v1"},
        "participants": [],
    }]}

    acks: list[dict] = []

    async def fake_config(_gid):
        return {"juicy_kill_channel_id": "55"}

    async def fake_get(path, **_kw):
        assert "juicy-kill/queue" in path
        return queue

    async def fake_bytes(path, **_kw):
        return b"png"

    async def fake_post(path, body, **_kw):
        acks.append(body)
        return {"ok": True}

    cog = jk.JuicyKills(SimpleNamespace())
    original = (jk._guild_command_config, jk.http_client.get_json,
                jk.http_client.get_bytes, jk.http_client.post_json)
    jk._guild_command_config = fake_config
    jk.http_client.get_json = fake_get
    jk.http_client.get_bytes = fake_bytes
    jk.http_client.post_json = fake_post
    try:
        t0 = time.monotonic()
        asyncio.run(asyncio.wait_for(cog.sync_guild(Guild()), timeout=API_TIMEOUT + 10))
        elapsed = time.monotonic() - t0
    finally:
        jk._guild_command_config, jk.http_client.get_json, \
            jk.http_client.get_bytes, jk.http_client.post_json = original

    assert elapsed < API_TIMEOUT + 10, f"sync_guild pendurou {elapsed:.1f}s"
    assert sent_states["calls"] == 1, "channel.send deveria ter sido tentado 1x"
    # Kill NÃO ackado — o send pendurou/falhou, cursor não avança.
    assert not acks, f"ack não deveria acontecer com send pendurado: {acks}"
    # Lock da guilda precisa estar livre pro próximo tick.
    lock = cog._locks[1]
    assert not lock.locked(), "lock da guilda ficou preso"


if __name__ == "__main__":
    test_dtimeout_converte_hang_em_timeouterror()
    test_timeouterror_esta_no_skip_exc()
    test_juicy_kills_nao_trava_com_channel_send_pendurado()
    print("discord timeout regression: ok")