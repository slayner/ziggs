"""Guarda: o profile_warmer agora aquece guilds e alianças além de players.
Cada tipo tem sua própria fila (GuildProfile/AllianceProfile com
refresh_requested_at), processada pelo loop principal (run_forever drena os
três). O botão ⟳ da página de guild/aliança funciona igual ao de player:
enfileira no warmer, estado compartilhado entre visitantes, cooldown de 5min,
retry automático em falha."""
import asyncio
import inspect
import threading
from datetime import timedelta

import app.services.profile_warmer as pw
import app.api.routes.profiles as pr


def test_warm_guild_e_warm_alliance_existem():
    """Sem essas funções o warmer não consegue buscar guilds/alianças na
    Albion — o botão ⟳ da página de guild/aliança enfileira mas nada
    processa."""
    assert hasattr(pw, "_warm_guild"), "deve ter _warm_guild"
    assert hasattr(pw, "_warm_alliance"), "deve ter _warm_alliance"


def test_warm_guild_e_alliance_retornam_bool():
    """Mesmo contrato do _warm_player: sem retorno bool o caller não sabe se
    limpa refresh_requested_at (sucesso) ou mantém na fila (retry)."""
    sig_g = inspect.signature(pw._warm_guild)
    sig_a = inspect.signature(pw._warm_alliance)
    assert sig_g.return_annotation in (bool, "bool"), "_warm_guild -> bool"
    assert sig_a.return_annotation in (bool, "bool"), "_warm_alliance -> bool"


def test_refresh_loop_drena_as_tres_filas():
    """Se o loop de refresh não chama sync_guild_refresh_requests e
    sync_alliance_refresh_requests, o botão ⟳ de guild/aliança enfileira mas
    nada processa — pedido fica pra sempre na fila. As três filas vivem no
    run_refresh_forever (loop dedicado, concorrente ao backfill em run_forever)."""
    src = inspect.getsource(pw.run_refresh_forever)
    assert "sync_refresh_requests()" in src, "drena fila de players"
    assert "sync_guild_refresh_requests()" in src, "drena fila de guilds"
    assert "sync_alliance_refresh_requests()" in src, "drena fila de alianças"


def test_stages_de_guild_e_alliance_usam_prefixo():
    """Stages de guild/alliance usam prefixo g:/a: em _refresh_progress pra
    não colidir com players (que usam o albion_id direto). Sem o prefixo, o
    stage de uma guild sobrescrevia o de um player com o mesmo ID."""
    src_g = inspect.getsource(pw._warm_guild)
    src_a = inspect.getsource(pw._warm_alliance)
    assert "f\"g:{albion_id}\"" in src_g, "guild usa prefixo g:"
    assert "f\"a:{albion_id}\"" in src_a, "alliance usa prefixo a:"


def test_rotas_de_refresh_existem():
    """POST /public/guilds/{id}/refresh e /public/alliances/{id}/refresh —
    sem eles o botão ⟳ do front não tem onde enfileirar."""
    assert hasattr(pr, "refresh_guild"), "POST /guilds/{id}/refresh"
    assert hasattr(pr, "refresh_alliance"), "POST /alliances/{id}/refresh"


def test_rotas_de_refresh_tem_cooldown_10min():
    """Mesmo cooldown do player — senão spam no botão ⟳ de guild/aliança
    dispara rate limit na Albion sem ganho. Guild/aliança keyam o cooldown no
    last_seen_at DELES, que só é escrito pelo warmer (não há feed bumpando),
    então aqui está correto — diferente do player (ver test_profile_refresh)."""
    src_g = inspect.getsource(pr.refresh_guild)
    src_a = inspect.getsource(pr.refresh_alliance)
    assert "REFRESH_COOLDOWN" in src_g, "guild respeita cooldown"
    assert "REFRESH_COOLDOWN" in src_a, "alliance respeita cooldown"
    assert pr.REFRESH_COOLDOWN.total_seconds() == 600, "cooldown = 10min"


def test_payload_expose_refresh_requested_at():
    """Sem expor refresh_requested_at no payload, o front não sabe que tem
    refresh em andamento — cada visitante só vê o próprio estado local."""
    src_g = inspect.getsource(pr._build_guild_payload_sync)
    src_a = inspect.getsource(pr._build_alliance_payload_sync)
    assert "refresh_requested_at" in src_g, "guild expõe refresh_requested_at"
    assert "refresh_requested_at" in src_a, "alliance expõe refresh_requested_at"


def test_endpoint_refresh_progress_existe():
    """GET /public/refresh-progress/{entity_type}/{albion_id} — sem ele o
    front não consegue ler o stage do warmer e só mostra 'atualizando…'."""
    assert hasattr(pr, "get_entity_refresh_progress"), "endpoint de stage"


def test_cold_load_de_guild_alliance_desacoplado_da_request():
    """O cold load (primeira visita) rodava dentro da request HTTP — reload
    na tab cancelava a coroutine e a agregação pesada (~1min) morria no meio.
    Agora roda como asyncio.create_task em background (sobrevive ao client
    desconectar) e o resultado é cacheado em _cold_cache (5min). Sem isso,
    reload recomeçava do zero e a barra voltava pro início."""
    assert hasattr(pr, "_cold_load_guild"), "deve ter _cold_load_guild (task em background)"
    assert hasattr(pr, "_cold_load_alliance"), "deve ter _cold_load_alliance"
    assert hasattr(pr, "_cold_cache"), "deve ter cache do resultado"
    assert hasattr(pr, "_cold_cache_get"), "deve ter getter do cache com TTL"

    src_g = inspect.getsource(pr.guild_profile)
    src_a = inspect.getsource(pr.alliance_profile)
    assert "asyncio.create_task" in src_g, "guild dispara task em background"
    assert "asyncio.create_task" in src_a, "alliance dispara task em background"
    assert "_cold_cache_get" in src_g, "guild serve do cache quando válido"
    assert "_cold_cache_get" in src_a, "alliance serve do cache quando válido"


def test_cold_load_timeout_mantem_worker_rastreado_e_fecha_sessao(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class FakeSession:
        closed = False

        def close(self):
            self.closed = True

    session = FakeSession()

    def build(db, albion_id):
        assert db is session
        started.set()
        release.wait(1)
        return {"albion_id": albion_id}

    monkeypatch.setattr(pr, "SessionLocal", lambda: session)
    monkeypatch.setattr(pr, "_build_guild_payload_sync", build)
    monkeypatch.setattr(pr, "COLD_LOAD_TIMEOUT", timedelta(milliseconds=10))

    async def run():
        key = "guild:test"
        task = asyncio.create_task(pr._cold_load_guild("test"))
        pr._cold_load_tasks[key] = task
        while not started.is_set():
            await asyncio.sleep(0)
        await asyncio.sleep(0.03)
        assert pr._cold_load_tasks.get(key) is task
        assert not task.done()
        assert not session.closed
        release.set()
        await asyncio.wait_for(task, 1)
        assert key not in pr._cold_load_tasks
        assert session.closed

    try:
        asyncio.run(run())
    finally:
        release.set()


if __name__ == "__main__":
    test_warm_guild_e_warm_alliance_existem()
    test_warm_guild_e_alliance_retornam_bool()
    test_run_forever_drena_as_tres_filas()
    test_stages_de_guild_e_alliance_usam_prefixo()
    test_rotas_de_refresh_existem()
    test_rotas_de_refresh_tem_cooldown_10min()
    test_payload_expose_refresh_requested_at()
    test_endpoint_refresh_progress_existe()
    print("ok")
