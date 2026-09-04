"""Guarda: o botão ⟳ de refresh do perfil tem que aplicar cooldown após
sucesso e expor estado compartilhado somente enquanto há trabalho ativo.

Falhas da Albion não podem deixar refresh_requested_at preso: o perfil já
cacheado continua utilizável e o usuário pode tentar de novo."""
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


def test_sync_refresh_requests_enfileira_task_vps_sem_limpar_estado():
    """O refresh cria uma task de VPS e mantém o estado até a ingestão canônica."""
    src = inspect.getsource(pw.sync_refresh_requests)
    assert "FEED_PROFILE" in src
    assert "PRIORITY_PROFILE_EXPLICIT" in src
    assert "refresh_requested_at = None" not in src
    assert "_warm_player" not in src


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


def test_refresh_done_at_armado_so_apos_ingestao_vps():
    """O cooldown só arma após persistir e renderizar o report da VPS."""
    from app.services import scan_dispatcher

    src = inspect.getsource(scan_dispatcher._apply_ingest_payload)
    assert "_refresh_done_at[albion_id]" in src
    assert "_render_player_preview_async" in src
    assert src.index("_render_player_preview_async") < src.index("_refresh_done_at[albion_id]")


def test_payload_expoe_estado_de_refresh_e_data_real_do_snapshot():
    """A idade exibida do perfil vem do fetch direto da Albion, não do feed."""
    src = inspect.getsource(pl._build_profile_payload)
    assert "refresh_requested_at" in src, "_ziggs deve expor refresh_requested_at"
    assert "stats_updated_at" in src, "_ziggs deve expor a data do snapshot"


def test_payload_warm_usa_cache_versionado_por_snapshot():
    """F5 de perfil warm não pode recomputar agregações pesadas."""
    src = inspect.getsource(pl._warm_profile_payload)
    assert "_cached_profile_payload" in src
    assert "_cache_profile_payload" in src
    cache_src = inspect.getsource(pl._cached_profile_payload)
    assert "stats_updated_at" in cache_src


def test_payload_de_perfil_nao_cria_battle_groups():
    """Abrir perfil warm é leitura: não pode criar grupos nem disputar escrita."""
    history_src = inspect.getsource(pl._battle_history)
    links_src = inspect.getsource(pl._battle_links_bulk)
    assert "get_existing_groups_bulk" in history_src
    assert "get_existing_groups_bulk" in links_src
    assert "await battle_groups.get_or_create_groups_bulk" not in history_src
    assert "await battle_groups.get_or_create_groups_bulk" not in links_src


def test_eventos_nao_alteram_snapshot_de_perfil_warm():
    """Participar de batalha não equivale a consultar o perfil na Albion."""
    src = inspect.getsource(pt._upsert_event_players)
    assert "touch_last_seen=False" in src
    assert "stats_verified=False" in src


def test_snapshot_completo_so_e_atualizado_por_fetch_direto():
    """Dados embutidos em eventos podem ser antigos ou parciais; não podem
    regressar fama PvP, PvE, crafting ou gathering de um perfil warm."""
    src = inspect.getsource(pt.upsert_player)
    assert "if stats_verified:" in src
    assert src.index("if stats_verified:") < src.index("player.kill_fame = kill_fame")
    assert src.index("if stats_verified:") < src.index("player.lifetime_statistics = lifetime")


def test_warmer_usa_idade_do_snapshot_direto():
    """Um evento recente não pode tornar fresco um perfil sem stats diretas."""
    src = inspect.getsource(pw._warm_player)
    assert "player.stats_updated_at" in src


def test_feed_nao_decodifica_json_grande_no_event_loop():
    """O parser do feed não pode atrasar leituras cacheadas de perfis warm."""
    src = inspect.getsource(pt._fetch_kill_page)
    assert "await asyncio.to_thread(response.json)" in src


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


def test_cold_load_grava_nucleo_e_sync_antes_de_ficar_pronto():
    """A primeira visita só fica warm depois de persistir o snapshot completo
    e sincronizar kills/deaths; assim não exige um refresh manual em seguida."""
    cold_load_src = inspect.getsource(pl._cold_load_player)
    assert cold_load_src.index("await upsert_player(") < cold_load_src.index(
        "await sync_player_kills("
    ), "cold load deve gravar o núcleo antes da sync de kills"
    assert cold_load_src.index("await sync_player_kills(") < cold_load_src.index(
        "await _render_profile_preview("
    ), "cold load renderiza o preview após sincronizar kills/deaths"
    assert cold_load_src.index("await _render_profile_preview(") < cold_load_src.index(
        "ready.set()"
    ), "cold load só fica pronto após publicar o preview"


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


def test_warm_by_name_renderiza_preview_apos_bootstrap():
    """O /profile só pode concluir depois de gerar o PNG que o Discord usa no preview."""
    src = inspect.getsource(pw.warm_by_name)
    assert src.index("sync_player_kills(") < src.index("await _render_player_preview_async("), \
        "bootstrap deve renderizar o preview depois de sincronizar as atividades"


def test_warm_bot_profile_reaproveita_cold_load_e_cache_do_site():
    """O /profile só publica cache pronto ou aguarda o cold-load idêntico ao site."""
    src = inspect.getsource(pw.warm_bot_profile)
    assert "stats_updated_at" in src
    assert "_start_cold_load" in src
    assert "priority=EMBED" in src
    assert "warm_by_name(name, region)" not in src



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


def test_ingestao_vps_limpa_stage_apos_preview():
    """O stage só termina após o worker concluir profile, eventos e preview."""
    from app.services import scan_dispatcher

    src = inspect.getsource(scan_dispatcher._apply_ingest_payload)
    assert "_refresh_progress.pop(albion_id, None)" in src
    assert src.index("_render_player_preview_async") < src.index("_refresh_progress.pop(albion_id, None)")


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


def test_rota_nao_publica_snapshot_invalido_como_perfil_pronto():
    """Payload sem Timestamp pode provar que a conta existe, mas não que o
    perfil foi aquecido. Só snapshot direto válido pode sair do cold-load."""
    by_name = inspect.getsource(pl.get_player_by_name)
    by_id = inspect.getsource(pl.get_player)
    assert "cached.stats_updated_at is not None" in by_name
    assert "cached.stats_updated_at is not None" in by_id
    assert "not _player_has_bad_snapshot(cached)" in by_name
    assert "not _player_has_bad_snapshot(cached)" in by_id


def test_cold_load_preserva_erro_terminal_para_proxima_request():
    src = inspect.getsource(pl._cold_load_player)
    assert 'not entry[1].startswith("error:")' in src


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
    assert "_raw_has_profile_data" in get_player_src, "get_player deve validar snapshot direto"
    get_by_name_src = inspect.getsource(pl.get_player_by_name)
    assert "cached.stats_updated_at is not None" in get_by_name_src, "get_player_by_name só serve snapshot direto"
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
