"""Guarda: o botão ⟳ de refresh do perfil tem que (1) aplicar cooldown de 5min
pós-atualização, (2) mostrar estado compartilhado enquanto
refresh_requested_at != null, (3) não apagar refresh_requested_at quando o
fetch falha (retry automático no próximo ciclo).

Já regrediu uma vez: o polling do front desistia em 15s mas o warmer demora
minutos com a fila cheia — o usuário via o botão parar de girar mas o refresh
continuava no backend, e clicar de novo sobrescrevia sem ganho. Outro usuário
olhando o mesmo perfil não sabia que tinha refresh em andamento."""
import inspect
from datetime import timedelta

import app.services.profile_warmer as pw
import app.services.player_tracker as pt
import app.api.routes.players as pl


def test_warm_player_retorna_bool():
    """Sem retorno bool o caller não consegue distinguir sucesso de falha pra
    decidir se limpa refresh_requested_at — volta pro estado 'None' silencioso
    e o pedido se perde."""
    sig = inspect.signature(pw._warm_player)
    # from __future__ import annotations faz virar string 'bool'
    assert sig.return_annotation in (bool, "bool"), "_warm_player deve declarar -> bool"


def test_sync_refresh_requests_so_limpa_em_sucesso_ou_host_invalido():
    """O loop que limpa refresh_requested_at tem que respeitar o retorno de
    _warm_player (ok) — limpa só se ok=True OU host=None (host inválido não
    vale retry). Sem isso, fetch falho limpava o pedido e o usuário tinha que
    clicar de novo no ⟳. Timeout também esquece (não adianta refazer se a
    Albion travou por 15min)."""
    src = inspect.getsource(pw.sync_refresh_requests)
    assert "_warm_with_timeout" in src, "deve envolver _warm_player com timeout"
    assert "if ok or host is None" in src, "só limpa refresh_requested_at em sucesso ou host inválido"
    assert "error:timeout" in src, "timeout esquece o pedido (não fica em loop)"


def test_refresh_cooldown_keyed_no_refresh_de_verdade_nao_no_last_seen():
    """POST /refresh recusa dentro de 10min do ÚLTIMO REFRESH COMPLETO — NÃO da
    última vez que o jogador foi visto. Keying no last_seen_at era um bug: o
    player_tracker bumpa last_seen_at a cada aparição no kill feed global, então
    jogador ATIVO (o que mais se quer atualizar) ficava com last_seen_at sempre
    < 10min e o botão ⟳ era recusado pra sempre ('não surtia efeito'). O cooldown
    agora usa _refresh_done_at, setado só quando o warmer termina um refresh real."""
    src = inspect.getsource(pl.request_player_refresh)
    assert "timedelta(minutes=10)" in src, "cooldown de 10min pós-refresh"
    assert "cooldown_seconds" in src, "devolve cooldown_seconds pro front"
    assert "_refresh_done_at" in src, "cooldown keyed no refresh de verdade"
    # `player.last_seen_at` = o acesso ao atributo que ERA o bug (o feed bumpa →
    # recusa eterna). Casa o código, não a explicação em comentário.
    assert "player.last_seen_at" not in src, "cooldown NÃO pode gatear no last_seen_at do jogador"


def test_refresh_done_at_armado_so_em_sucesso():
    """O cooldown só arma quando o refresh REALMENTE aconteceu — timeout/host
    inválido não pode bloquear o retry do usuário."""
    src = inspect.getsource(pw.sync_refresh_requests)
    assert "_refresh_done_at[player.albion_id]" in src, "arma o cooldown em sucesso"
    assert "if ok:" in src, "só em sucesso (não em timeout/host inválido)"


def test_payload_expose_refresh_requested_at():
    """Sem expor refresh_requested_at no payload _ziggs, o front não consegue
    saber que tem refresh em andamento — cada visitante só vê o próprio estado
    local e clica de novo. Com o campo exposto, TODOS vêem 'atualizando'."""
    src = inspect.getsource(pl._build_profile_payload)
    assert "refresh_requested_at" in src, "_ziggs deve expor refresh_requested_at"


def test_queue_refresh_if_stale_respeita_em_andamento():
    """Se já tem refresh em andamento (refresh_requested_at != None), não
    enfileira outro — senão a fila dupla acelera o rate limit sem ganho."""
    src = inspect.getsource(pl._queue_refresh_if_stale)
    assert "is not None" in src and "return" in src, "não enfileira se já em andamento"


def test_warm_player_atualiza_stage_do_refresh():
    """O botão ⟳ mostra progresso detalhado (na fila → buscando perfil →
    sincronizando kills). Sem setar _refresh_progress em cada etapa, o usuário
    vê só 'atualizando…' e parece travado em refreshes longos (rate limiter da
    Albion pode levar minutos)."""
    src = inspect.getsource(pw._warm_player)
    assert '_refresh_progress[albion_id] = "fetching"' in src, "stage fetching"
    assert '_refresh_progress[albion_id] = "kills"' in src, "stage kills"


def test_warm_player_grava_nucleo_antes_da_sync_de_kills():
    """Perf: grava fama/guilda/lifetime stats (upsert_player) ANTES da sync de
    kills (~2 requests/~10s no rate limit). O front reaplica o perfil a cada
    poll durante o refresh, então os dados principais aparecem após 1 request
    em vez dos 3 — a sync de kills só completa o feed de atividade."""
    src = inspect.getsource(pw._warm_player)
    assert src.index("upsert_player(db, raw, region)") < src.index("sync_player_kills("), \
        "upsert do núcleo do perfil deve vir ANTES da sync de kills"


def test_sync_player_kills_dedupe_antes_do_upsert_pesado():
    """Perf: evento já no ledger pula _upsert_event_players (um db.commit() por
    player). Sem o dedupe ANTES, um refresh de jogador ativo refazia centenas de
    commits à toa (a maioria das kills já está no ledger). O SELECT de
    existência tem que vir antes de _upsert_event_players."""
    src = inspect.getsource(pt.sync_player_kills)
    # `_upsert_event_players(db,` = a CHAMADA (o comentário acima cita o nome
    # sem parênteses; casar a chamada evita tropeçar na explicação).
    assert src.index("albion_event_id == event_id") < src.index("_upsert_event_players(db,"), \
        "dedupe (SELECT de existência) deve vir antes do upsert dos players"


def test_sync_refresh_reseta_stage_pr_queued_em_falha():
    """Fetch falhou → pedido fica na fila (retry automático). Stage volta pra
    'queued' — senão o usuário vê 'buscando perfil' pra sempre num pedido que
    já falhou e está esperando refazer."""
    src = inspect.getsource(pw.sync_refresh_requests)
    assert "_refresh_progress[player.albion_id] = \"queued\"" in src, "reseta stage em falha"
    assert "_refresh_progress.pop(player.albion_id, None)" in src, "limpa stage em sucesso"


def test_endpoint_refresh_progress_existe():
    """GET /players/refresh-progress/{albion_id} — sem ele o front não consegue
    ler o stage do profile_warmer e só mostra 'atualizando…' genérico."""
    assert hasattr(pl, "get_refresh_progress"), "endpoint /refresh-progress deve existir"


def test_cold_load_de_player_desacoplado_da_request():
    """O cold load (primeira visita) rodava dentro da request HTTP — reload
    na tab cancelava a coroutine e o trabalho morria no meio. Agora roda como
    asyncio.create_task em background (sobrevive ao client desconectar); a
    request só enfileira e retorna stub. Sem isso, reload recomeçava do zero."""
    assert hasattr(pl, "_cold_load_player"), "deve ter _cold_load_player (task em background)"
    assert hasattr(pl, "_cold_load_tasks"), "deve ter dict de tasks em background"
    src = inspect.getsource(pl.get_player_by_name)
    assert "asyncio.create_task" in src, "deve disparar task em background"
    assert "_cold_load" in src, "deve retornar stub com _cold_load=true"


def test_timeout_de_15min_em_refresh_e_cold_load():
    """Refresh e cold load têm timeout de 15min a partir do processamento (não
    conta tempo em fila). Se exceder, o pedido é esquecido (refresh_requested_at
    limpo, stage vira error:timeout). Sem isso, um pedido travado (rate limiter
    infinito) ficava pra sempre e o usuário via 'atualizando…' eternamente."""
    assert pw.PROCESSING_TIMEOUT.total_seconds() == 900, "warmer timeout = 15min"
    assert pl.COLD_LOAD_TIMEOUT.total_seconds() == 900, "cold load timeout = 15min"
    assert hasattr(pw, "_warm_with_timeout"), "deve ter helper de timeout no warmer"

    # Cold load de guild/aliança também tem timeout
    import app.api.routes.profiles as pr
    assert pr.COLD_LOAD_TIMEOUT.total_seconds() == 900, "guild/alliance cold load timeout = 15min"
    assert hasattr(pr, "_check_cold_timeout"), "deve ter helper de checagem de timeout"


def test_timeout_em_fila_nao_conta():
    """O tempo em fila (esperando slot do albion_gate ou ciclo do warmer) não
    conta pro timeout — só começa a contar quando o item entra em processamento.
    O timeout é por-item (asyncio.wait_for envolve o _warm_*), não na fila
    inteira."""
    src = inspect.getsource(pw._warm_with_timeout)
    assert "asyncio.wait_for" in src, "timeout envolve o _warm_* (por-item)"
    assert "PROCESSING_TIMEOUT" in src, "usa o timeout de processamento"


if __name__ == "__main__":
    test_warm_player_retorna_bool()
    test_sync_refresh_requests_so_limpa_em_sucesso_ou_host_invalido()
    test_refresh_cooldown_keyed_no_refresh_de_verdade_nao_no_last_seen()
    test_refresh_done_at_armado_so_em_sucesso()
    test_payload_expose_refresh_requested_at()
    test_queue_refresh_if_stale_respeita_em_andamento()
    print("ok")