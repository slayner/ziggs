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
    # O loop desempacota `for albion_id, region in refresh_list:` — usa a var
    # de loop, não `player.albion_id`. Casar o que o código realmente faz.
    assert "_refresh_done_at[albion_id]" in src, "arma o cooldown em sucesso"
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
    assert src.index("await upsert_player(") < src.index("sync_player_kills("), \
        "upsert do núcleo do perfil deve vir ANTES da sync de kills"


def test_cold_load_grava_nucleo_antes_da_sync_de_kills():
    """Bug do 'primeira carga não salva': o cold load (by-name) rodava
    sync_player_kills ANTES do upsert_player. O jogador podia NÃO existir no
    banco ainda (primeira visita de verdade), e _record_kill_event resolve
    killer_player_id/victim_player_id por lookup no banco — sem a linha do
    jogador, os FKs ficam NULL. Como o dedupe é por (region, albion_event_id),
    as kills/deaths ficavam orfanadas PRA SEME, e nem o refresh (warmer)
    recuperava (os eventos já estavam no ledger, o sync pula). Regrediu em
    produção: perfil mostrava fame (escalar, salva pelo upsert final) mas
    as listas de kills/deaths vinham vazias até a galera clicar em ⟳ — que
    também não resolvia. Mesma ordem do _warm_player (ver teste acima)."""
    cold_load_src = inspect.getsource(pl._cold_load_player)
    assert cold_load_src.index("await upsert_player(") < cold_load_src.index(
        "_sync_cold_load_activities("
    ), "cold load deve gravar o núcleo antes de disparar a sync de kills"
    activities_src = inspect.getsource(pl._sync_cold_load_activities)
    assert "sync_player_kills(" in activities_src, "a task separada deve sincronizar kills/deaths"


def test_get_player_by_id_grava_nucleo_antes_da_sync_de_kills():
    """Mesmo bug do cold load, na rota /players/{albion_id}: o jogador pode não
    existir no banco ainda (deep link por ID cru, sem ter sido visto antes).
    Mesma ordem do _warm_player / _cold_load_player."""
    src = inspect.getsource(pl.get_player)
    assert src.index("upsert_player(db, raw") < src.index("sync_player_kills("), \
        "get_player deve gravar o núcleo (upsert_player) ANTES da sync de kills"


def test_warm_by_name_grava_nucleo_antes_da_sync_de_kills():
    """Mesmo bug no bootstrap do companion (warm_by_name — próprio char de
    quem nunca caiu em ZvZ rastreada). O jogador é desconhecido por definição,
    então o upsert primeiro é OBRIGATÓRIO — sem ele, TODA kill da primeira
    sincronização fica órfã."""
    src = inspect.getsource(pw.warm_by_name)
    assert src.index("await upsert_player(") < src.index("sync_player_kills("), \
        "warm_by_name (bootstrap) deve gravar o núcleo ANTES da sync de kills"


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
    # O loop desempacota `for albion_id, region in refresh_list:` — usa a var
    # de loop, não `player.albion_id`. Casar o que o código realmente faz.
    assert "_refresh_progress[albion_id] = \"queued\"" in src, "reseta stage em falha"
    assert "_refresh_progress.pop(albion_id, None)" in src, "limpa stage em sucesso"


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
    assert "_start_cold_load" in src, "deve disparar task em background"
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


def test_embed_usa_snapshot_verificado_e_nao_rejeita_famas_zero():
    """Um GET direto válido pode conter todas as famas zeradas. O embed deve
    usar esse snapshot e só aquecer de novo quando stats_updated_at passa de 8h,
    nunca tratar os zeros como se o perfil não tivesse carregado."""
    # Snapshot real: Timestamp presente, famas podem ser zero legitimamente.
    assert pl._raw_has_profile_data({"LifetimeStatistics": {"PvE": {"Total": 0}, "Timestamp": "2026-01-01T00:00:00"}})
    # Snapshot bugado da Albion: Timestamp null → rejeita e reintenta.
    assert not pl._raw_has_profile_data({"LifetimeStatistics": {"PvE": {"Total": 0}, "Timestamp": None}})
    assert not pl._raw_has_profile_data({"LifetimeStatistics": {"PvE": {"Total": 0}}})
    assert hasattr(pl, "_player_has_bad_snapshot"), "debe ter helper para detectar snapshot bugado"
    preview_src = inspect.getsource(pl._preview_version)
    assert "stats_updated_at" in preview_src
    assert "_player_has_all_zero_stats" not in preview_src
    assert "PREVIEW_RENDER_VERSION" in preview_src
    assert "_player_has_bad_snapshot" in preview_src
    embed_queue_src = inspect.getsource(pl._queue_embed_refresh_if_stale)
    assert "EMBED_STATS_MAX_AGE" in embed_queue_src
    assert "stats_updated_at" in embed_queue_src


def test_preview_arma_usa_render_real_antes_do_png():
    """A imagem não pode desenhar uma arma inventada quando o cache está frio:
    resolve primeiro o mesmo T7 excelente que o widget do perfil pede."""
    src = inspect.getsource(pl._render_profile_preview)
    assert "render_item_for_card" in src
    assert "available_weapon_bases" in src
    import app.services.profile_preview as pp
    preview_src = inspect.getsource(pp.render_player_preview)
    assert 'label = "T7"' not in preview_src
    assert "PREVIEW_RENDER_VERSION" in inspect.getsource(pp._cache_path)
    assert "SegUIVar.ttf" in inspect.getsource(pp._load_fonts)
    font_src = inspect.getsource(pp._load_fonts)
    assert "cascadia_code.ttf" in font_src
    assert "_FONT_STATS = load_cascadia" in font_src
    render_src = inspect.getsource(pp.render_player_preview)
    assert '("Crafting",' in render_src
    assert '("Crafting Fame"' not in render_src
    assert '("Madeira"' not in render_src
    assert '("Wood"' in render_src
    assert "stroke_width" not in inspect.getsource(pp._draw_metric)
    assert preview_src.count("_draw_metric(") == 2
    assert "_age_label(player.last_seen_at)" not in preview_src
    assert '("Ratio",' in preview_src
    assert "grid_step" not in preview_src


def test_preview_crafting_fica_a_direita_de_pve_e_cache_de_armas_e_reutilizado(monkeypatch):
    """PvE não some com zero e ícones prontos não voltam a consultar a CDN."""
    import app.services.profile_preview as pp

    preview_src = inspect.getsource(pp.render_player_preview)
    assert 'if pve_fame:' not in preview_src
    assert preview_src.index('("PvE Fame"') < preview_src.index('("Crafting"')

    monkeypatch.setattr(pp, "_item_icon", lambda base: object() if base == "2H_KNUCKLES_SET2" else None)
    assert pp.cached_preview_weapon_bases(["2H_KNUCKLES_SET2", "2H_ARCANESTAFF"]) == {"2H_KNUCKLES_SET2"}


def test_upsert_player_rejeita_snapshot_sem_timestamp():
    """A Albion retorna intermitentemente LifetimeStatistics com Timestamp
    null e todas as contagens zeradas. upsert_player deve tratar isso como
    ausente (has_lifetime=False) para nunca sobrescrever stats boas com
    zeros — independente do caller passar stats_verified=True."""
    src = inspect.getsource(pt.upsert_player)
    assert "Timestamp" in src, "upsert_player deve checar Timestamp no LifetimeStatistics"
    # Todos os callers com stats_verified=True devem usar _raw_has_profile_data
    warm_src = inspect.getsource(pw._warm_player)
    assert "_raw_has_profile_data" in warm_src, "_warm_player deve validar snapshot"
    warm_by_name_src = inspect.getsource(pw.warm_by_name)
    assert "_raw_has_profile_data" in warm_by_name_src, "warm_by_name deve validar snapshot"
    get_player_src = inspect.getsource(pl.get_player)
    assert get_player_src.count("_raw_has_profile_data") >= 2, "get_player deve validar snapshot em ambos os caminhos"
    get_by_name_src = inspect.getsource(pl.get_player_by_name)
    assert "_raw_has_profile_data" in get_by_name_src, "get_player_by_name deve validar snapshot"
    # O profile JSON (cache-first) também precisa detectar snapshot bugado
    assert "_player_has_bad_snapshot" in get_by_name_src, "get_player_by_name cache-first deve checar snapshot bugado"
    assert "_player_has_bad_snapshot" in get_player_src, "get_player cache-first deve checar snapshot bugado"


def test_upsert_guild_e_alliance_rejeita_snapshot_vazio():
    """Guildas e alianças também sofrem com snapshots vazios da Albion.
    _upsert_guild_profile e _upsert_alliance_profile devem retornar False
    quando o snapshot não tem nome nem stats/membros, preservando dados
    existentes em vez de sobrescrever com zeros."""
    guild_src = inspect.getsource(pw._upsert_guild_profile)
    assert "return False" in guild_src, "_upsert_guild_profile deve retornar False em snapshot vazio"
    assert "has_name" in guild_src, "deve checar se tem nome"
    assert "has_stats" in guild_src, "deve checar se tem stats/membros"
    alliance_src = inspect.getsource(pw._upsert_alliance_profile)
    assert "return False" in alliance_src, "_upsert_alliance_profile deve retornar False em snapshot vazio"
    assert "has_name" in alliance_src, "deve checar se tem nome"
    assert "has_guilds" in alliance_src, "deve checar se tem guildas"
    # _warm_guild e _warm_alliance devem propagar o False
    warm_guild_src = inspect.getsource(pw._warm_guild)
    assert "if not ok:" in warm_guild_src, "_warm_guild deve checar retorno do upsert"
    warm_alliance_src = inspect.getsource(pw._warm_alliance)
    assert "if not ok:" in warm_alliance_src, "_warm_alliance deve checar retorno do upsert"


if __name__ == "__main__":
    test_warm_player_retorna_bool()
    test_sync_refresh_requests_so_limpa_em_sucesso_ou_host_invalido()
    test_refresh_cooldown_keyed_no_refresh_de_verdade_nao_no_last_seen()
    test_refresh_done_at_armado_so_em_sucesso()
    test_payload_expose_refresh_requested_at()
    test_queue_refresh_if_stale_respeita_em_andamento()
    print("ok")
