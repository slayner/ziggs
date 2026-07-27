# Histórico Consolidado de Sessões (opencode)

Resumo das 19 sessões anteriores do opencode neste projeto, consolidado em 26/07/2026.
Organizado por tema, não por ordem cronológica — o objetivo é eu (modelo) conseguir
"lembrar" do que já foi decidido/implementado sem precisar reler cada sessão.

## Convenções e decisões de produto

- **Idiomas do site/docs**: PT/EN/ES. Catálogos sincronizados por idioma.
- **Sintaxe de comandos na docs**: placeholders `<user>` `<amount>`, exemplos naturais
  (`/pay @Rivera 100k`), NUNCA `user:@Person amount:100k`.
- **Exemplos de comandos**: sintaxe à esquerda, explicação à direita (duas colunas);
  empilha só em telas estreitas.
- **Login Discord**: obrigatório só pra auto-submit de lootlog e warm distribuído de
  kills. Tudo o resto (scan, DNS, prices, docs) é público.
- **Rate limit da Albion (`albion_gate.py`)**: 1 req/5s agregado do backend. **NÃO mexer** —
  forçar causa 429. Companion fazendo requests diretos do IP do usuário NÃO passa por
  esse rate limiter (é outro IP, outra cota).
- **Doutrina companion**: backend NUNCA confia em stats do cliente. Companion só diz ONDE
  olhar; backend busca a verdade na API autoritativa da Albion (battle scan) ou usa
  consenso entre testemunhas (lootlog, preços). `source_install` em cada row pra expurgo.
- **Ponytail ultra**: ativo por padrão. Deletion before addition, stdlib antes de
  dependency, um arquivo antes de vários. Marcar atalhos com `// ponytail:`.
- **`is_windows_admin()`** é a ÚNICA checagem de elevação — não escrever outra.

## Eventos e Regear (PLANO-EVENTOS-REGEAR-V2.md)

Redesenho completo implementado. Plano em `docs/PLANO-EVENTOS-REGEAR-V2.md`.

- **Lifecycle**: `draft -> scheduled -> in_progress -> review -> finalized`, com
  publish/unpublish auditável. Drafts não disparam pings/mass-info/threads.
- **Criação**: `/event create <utc-time>` — objetivo e comp opcionais. Sem comp,
  signup bloqueado; modo `announcement` publica sem signup.
- **Signup vs Assignment vs Participant**:
  - Signup = presença/intenção (NÃO garante build/role).
  - Assignment = escolha efetiva da build pela administração.
  - Participant = presença + payout.
- **Políticas por evento** (`assignment_mode`): `self_select`, `admin_assign`, `hybrid`
  (default `hybrid`). Autofill (`autofill_mode`): `off`, `manual`, `on_signup`.
- **Autofill**: roda no backend (NÃO no React — removido o autofill local). Modo
  `on_signup` preenche slots livres no signup; `manual` usa botão admin com preview +
  confirmação. Assignments admin travados (`locked=true`) nunca são sobrescritos pelo
  autofill. Cada execução tem run_id, auditoria, e undo até o callout.
- **Segurança financeira (regear)**: pedido pago é imutável; pagamento repetido
  idempotente; pedido negado não vira pago; `final_total` negativo e preços negativos
  rejeitados. Aprovação respeita `require_approval` e cargos configurados. Evidências
  de reconhecimento (método, confiança, candidatos, motivo fallback) persistidas.
- **Invariantes**: composição cross-guild rejeitada em create/edit/escalação;
  signup bloqueado fora de `scheduled`/`in_progress` ou sem comp; mutações após
  finalização bloqueadas; reset de comp exige confirmação e bloqueia com regear vinculado.
- **Migrations**: `z1a2b3c4d5e6f`, `z2a3b4c5d6e7f`, `z3a4b5c6d7e8f`, `z4a5b6c7d8e9f`.
- **Mass-info**: mostra objetivo, diferencia anúncio de signup, mantém eventos além
  do limite de 25 componentes.

## Highscores e Rankings

- **Cache**: `highscores_cache.py` — precompute de 5min via `run_forever()` desde o
  startup (NÃO é triggerado por visita). 71 chaves computadas por ciclo. Singles de
  `weapon_scorer` ficam ao vivo (default do site é 3 regiões); pairs+full cacheados.
- **Armas removidas do jogo**: `REMOVED_WEAPON_BASES = {"2H_IRONGAUNTLETS_HELL"}`
  (Black Hands) em `highscores.py`. Filtra em `/weapons` (dropdown),
  `_compute_rankings` (kind=weapon:base e weapon_scorer), e `_compute_highlights`.
  Pra remover outra: adicionar a base no set (1 linha). Histórico em
  `PlayerWeaponStat` permanece intacto.
- **Rankings de coleta**: kinds `gather_total/wood/hide/ore/rock/fiber`, `fishing`,
  `crafting`, `silver_dropped`. Todos "alltime" só (famas acumulativas da conta).
  - `gather_*` e `fishing_fame`: colunas escalares em `AlbionPlayer`, extraídas do
    `lifetime_statistics.Gathering.{Wood,...}.Total` no `upsert_player`. Backfill
    rodou na migration (946 jogadores gather, 869 fishing).
  - `crafting_fame`: já era escalar em `AlbionPlayer` (só adicionei o kind).
  - `silver_dropped`: worker `services/silver_dropped.py` precifica
    `player_kill_events` em background (NULL=pendente, 0=precificado-zero, >0=real).
    Mesmo padrão do `battle_price_reprocessor`. Ranking agrega `SUM(silver_dropped)
    GROUP BY victim_player_id`.
- **Dropdown de coleta**: item "Coleta" no top-level com seta ▶ sempre visível (gira
  90° quando submenu abre). Submenu lateral aninhado DENTRO do wrapper do item (não
  sibling) pra não cortar na scrollbar. Crafting e silver_dropped ficam no top-level
  (specials), NÃO no submenu de coleta. Sem prefixo "Coleta — " nos tipos dentro do
  submenu. "Coleta" (total) NÃO aparece de novo dentro do submenu.
- **Rank no perfil**: `ResourceCountRow` mostra `#rank` quando jogador está no top500,
  clicável → `/highscores?kind=X&player=ID&rank=N&regions=region`. Pesca incluída como
  tipo. Silver dropped tem bloco custom com rank (FameRow não suporta rank).
- **Deep link highscores**: App detecta `/highscores?kind=&player=&rank=&regions=`,
  seta view + params, limpa URL. `HighscoresPage` aceita `initialKind`/`highlightPlayer`/
  `initialRank`/`initialRegions` — abre na kind certa, página certa (calculada do
  rank), destaca a linha do jogador com borda âmbar.

## Perfis (players/guilds/alianças) — carregamento e cache

- **Players**: cache-first no DB (`AlbionPlayer`). Nunca forçam loading só por abrir.
  `PROFILE_STALE_AFTER = 15 dias` — só enfileira refresh automático por visita se
  passar de 15 dias. Warmer re-aquece em background aos 7 dias (`STALE_AFTER` do
  warmer, independente de visita).
- **Guilds/alianças**: `_cold_cache` (dict em memória, TTL 15 dias). Serve do cache
  instantâneo; loading forçado só se cache inexistente OU idade > 15 dias.
  `_cold_cache_get(key, db)` checa se `GuildProfile.last_seen_at` é mais recente que
  o cache — se sim, warmer rodou refresh, invalida e re-agerrega. TODO: persistir
  no `DashboardCache` (DB) pra sobreviver restarts.
- **Cold load desacoplado da request**: `_cold_load_player`/`_cold_load_guild`/
  `_cold_load_alliance` rodam como `asyncio.create_task` (sobrevive ao client
  desconectar). Reload na tab NÃO recomeça do zero — a task continua em background.
  Rotas retornam stub `{_cold_load: true}` enquanto carrega; front detecta e continua
  polling do `_load_progress` + lê DB quando pronto.
- **Refresh (botão ⟳)**: estado compartilhado via `refresh_requested_at` no
  `AlbionPlayer`/`GuildProfile`/`AllianceProfile`. Todos os visitantes do perfil
  veem "atualizando" ao mesmo tempo. Polling sem deadline (continua até completar,
  mesmo que demore 5min). Usuário fecha a página? Refresh continua no backend.
  - Cooldown 5min baseado em `last_seen_at`. "agora" cobre os primeiros 5min de age.
  - Stages: `queued -> fetching -> kills -> building` (players), `queued -> fetching
    -> building` (guilds/alianças). Endpoint `/players/refresh-progress/{albion_id}`
    e `/public/refresh-progress/{entity_type}/{albion_id}`.
  - Retry automático em falha: `_warm_player`/`_warm_guild`/`_warm_alliance` retornam
    bool; `sync_*_refresh_requests` só limpa `refresh_requested_at` em sucesso.
  - Timeout 15min por item (começa quando entra em processamento, NÃO na fila).
    Helper `_warm_with_timeout` envolve com `asyncio.wait_for`. Em timeout: stage
    `error:timeout`, pedido esquecido, front mostra "tempo esgotado".
- **GuildProfile/AllianceProfile** (models novos, migration `t6e1f9b5d2c8`): tabelas
  com `albion_id`, `name`, `region`, `alliance_id/name`, `kill_fame`, `death_fame`,
  `members`/`guilds` (json), `founder_id`, `last_seen_at`, `refresh_requested_at`.
  Warmer busca em `/gameinfo/guilds/{id}` e `/gameinfo/alliances/{id}`.

## Otimizações de performance (medido no DB dev: 2.5M battle_participants, 770k player_kill_events)

- **`_members` (guilda)**: 30-42s → 11ms (2700-3800x). Subquery `latest_sub` agrupava
  2.5M rows sem filtro de guilda. Pré-filtrar candidates da guilda + passar pra
  subquery com `IN` cortou drasticamente.
- **`_silver_windows`**: 3.7s → 1ms. SUM no SQL + índice em `victim_guild_id`/
  `killer_guild_id`.
- **`_battles_guild` (página)**: 6.8s → 50ms. `big_sub` (batalhas com ≥25 jogadores
  de uma guilda) cacheado em memória 5min (`_big_bids_cache`).
- **`_build_guild_payload_sync` (cold load guilda)**: 37s → 47ms (cache quente) /
  3.7s (frio). Aliança similar.
- **`_weapon_function_map`**: cache 60s em memória (era chamado 2x por request,
  escaneando tabela `weapons` inteira).
- **`_factions_summary_bulk`**: 3 queries fixas vs 3 por batalha. Pra 10 batalhas:
  30 queries → 3. Aplicado em `_battles_guild`/`_battles_alliance`/`_search`/
  `_battle_history`/`_battle_links_bulk`.
- **`_serialize_kill`**: batch de oponentes (1 query `WHERE id IN (...)` vs N
  `db.get` por kill).
- **Migrations de índice**: `BattleParticipant.guild_id`/`alliance_id`,
  `PlayerKillEvent.victim_guild_id`/`killer_guild_id` (migration `u7f2a1b3c4d5`).
- **Multi-processo**: discutido e descartado. Ganho marginal (caches em memória
  precisariam ir pra Redis, rate limiter precisa ser distribuído, coordenação de
  background tasks). Só vale quando gargalo voltar a ser problema.

## Warm distribuído de kills (companion)

- **Só próprio char, só jogo fechado, só logado Discord**. Custo: 2 HTTP requests a
  cada 20min (~1.2MB/hora). Insignificante. NÃO busca kills de players vistos (pesaria
  no PC do usuário — 30 requests/ciclo pra resolver nome→ID).
- **Fluxo**: `/companion/warm {name}` → backend resolve, devolve `{status, albion_id}`
  (antes devolvia só status). Companion guarda `warm_albion_id` no config.
  `warm_kills_worker` (a cada 20min, só com jogo fechado): busca
  `gameinfo/players/{id}/kills` + `/deaths` direto da Albion (IP do usuário, NÃO
  passa pelo rate limiter do backend), POSTa pro backend.
- **Backend `POST /companion/warm/kills`**: bearer auth (Discord), `_rate_ok` por
  linhas por install, valida `event_id` único + timestamp <7d + region bate + IDs
  existem em AlbionPlayer (cria se faltar). Upsert em `PlayerKillEvent` com
  `source_install = install_id` (campo novo, migration). Expurgo:
  `DELETE WHERE source_install = ?`.

## Busca global (GlobalSearch)

- **Loading persiste**: busca local dispara em 300ms; spinner desliga mas `extLoading`
  liga — resultados aparecem, spinner fica indicando "haverão mais". Após 8s, busca
  externa dispara; merge só adiciona quem NÃO estava nos resultados locais.
- **Busca externa** (`GET /public/search/external?q=`): busca nas 3 regiões em
  paralelo (prioridade PROFILE, fura fila de fundo). Persiste players (`upsert_player`),
  guilds e alliances (`search_index.safe_upsert_entry`). Tudo que é encontrado vira
  perfil local — próxima busca pelo mesmo nome resolve instantâneo.
- **Ênfase no servidor**: badge de região em cada card (label localizada). Mesmo nick
  em 3 servidores aparece 3 vezes (albion_id diferente por região).
- **Parse de servidor no input**: "slayner americas" → `{q: "slayner", region:
  "americas"}`. Sinônimos: america/west/am → americas, eu/europe → europe,
  as/asia/asian → asia.
- **`search_index` rebuild**: deriva `region` pra guild/alliance (região mais
  frequente das batalhas onde aparecem). Antes era NULL.

## Companion — redesign e features

- **Layout COCKPIT com abas vivas** (jul/2026, `companion/docs/PLANO-ABAS-VIVAS.md`):
  sidebar vertical à esquerda com 3 abas (Rota/Túnel default, Damage Meter, Lootlog).
  Cada aba carrega badge AO VIVO que atualiza mesmo sem foco (dados vêm de estado do
  App(), nunca de componente de aba que desmonta). Badge damage = `damage_total`
  (número extenso, ex: `1.234.567`). Badge lootlog = `loot_count` (quantidade de logs).
- **Arranque com Windows**: janela NORMAL (não minimizada) — `--minimized` removido
  do XML do Task Scheduler. Anúncios precisam aparecer no boot pra cobrir VPS.
- **Túnel NÃO auto-inicia no boot** de propósito — usuário precisa clicar manualmente
  toda vez. Forçar o clique expõe mais os anúncios da aba Rota. Toggle
  `tunnel_enabled` só decide estado do botão na UI, não liga sozinho.
- **Multi-rotas (estilo ExitLag)**: `TunnelRoute` struct no config (label, endpoint,
  server_pubkey, client_privkey, priority). Modal de gerenciamento (`RoutesModal`)
  com adicionar/remover/reordenar/testar. `tunnel_active_route` (i32, -1 = principal)
  persiste no config. Seletor na aba Túnel mostra "Principal" + rotas extras; trocar
  reinicia o túnel se estiver rodando. `tunnel_start` no Rust resolve a rota ativa.
  Rota que some do DNS/handshake é descartada só da sessão, não apagada do config.
- **Ads**: 1 ad strip 728×90 no rodapé de cada aba (fixo, não rola com a página). 2
  ads side 300×250 na sidebar abaixo das abas. Sidebar 240px, janela 980×760 (min
  780×660).
- **Tela branca do release (WebView2)**: `present_window()` no `lib.rs` — show +
  unminimize + focus + DOIS nudges de ±1px (150ms/900ms). Usado nos 3 pontos que
  apresentam a janela (setup, tray "Abrir", single-instance).
- **Npcap**: instalação MANUAL (não re-tentar automatizar — OEM proíbe). Banner
  `.ck-npcap` + modal tutorial de 3 passos quando ausente. Autostart NÃO se registra
  sem Npcap (`npcap_installed()` checa registry).
- **Damage meter**: só DANO (cura descartada). `DamageAcc::record` único ponto de
  escrita. Timeline 180s. `dps` usa tempo ativo. Nomes de skill do `spells.xml` (não
  `spells.json`), `channelingspell` conta como índice (pegadinha que quebrava tudo).
  `spell_index_offset = 0` confirmado com 19 pares. Render da arma é INFERIDA pelas
  skills (não lida do equipamento). Ícone de skill do backend (`/render/spell/`), não
  da CDN da Albion.
- **Lootlog**: dedup por identidade do evento (`is_duplicate_loot` — quem lootou, de
  quem, item, quantidade, últimos 8 eventos) em vez de hash de bytes. CSV em inglês
  (interoperável), terminal em idioma da UI com tier encurtado. Auto-submit quando
  evento entra em REVIEW (worker no Rust, NÃO no React).
- **Barra de título custom** (sem `decorations` do Windows): header `.ck-bar` é
  arrastável via `onMouseDown` → `getCurrentWindow().startDragging()` (Tauri 2 com
  React: `data-tauri-drag-region` renderiza como `"true"` e não funciona). Duplo-clique
  maximiza/restaura. Botões de janela colados na borda direita (sem padding-right).
- **DiscordButton**: abaixo do botão de settings no rodapé da sidebar. Username em
  uppercase, alinhado à esquerda.

## Backend — logging no terminal

- `logging.basicConfig(level=INFO)` no `main.py` (antes level default WARNING
  silenciava tudo). Agora `log.info` dos serviços aparece no terminal.
- Linhas novas: warm de player/guild/aliança, sync_kills, battle tracker, companion
  scan, companion warm/kills, preços, market_history.

## Smart App Control

- Companion `.exe`/NSIS saem `NotSigned` — bloqueados pelo Smart App Control.
- **Caminho escolhido**: Microsoft Trusted Signing (Azure). Wiring feito em
  `tauri.conf.json` → `bundle.windows.signCommand` chama `scripts/sign-windows.ps1`
  com `signtool /dlib /dmdf` + 3 env vars. Sem as env vars o script PULA a assinatura
  (exit 0) — dev local continua sem assinar.
- **Falta (só dono consegue)**: criar recurso Trusted Signing no Azure, verificação
  de identidade, certificate profile (Public Trust), role "Trusted Signing Certificate
  Profile Signer", instalar Windows SDK + NuGet `Microsoft.Trusted.Signing.Client`,
  setar `ZIGGS_TRUSTED_SIGNING_*` no ambiente. **Confira flags do `signtool` contra
  doc ATUAL da Microsoft antes do primeiro uso real.**

## Pendências reais (não são fases)

- Render da arma no ranking do damage meter (aguarda dump [CHAR n] identificar o
  array de equipamento no pacote NewCharacter).
- 1ª execução real do feed AODP.
- Chave de assinatura do updater no ambiente de build (`TAURI_SIGNING_PRIVATE_KEY`).
- `event.listen not allowed. Plugin not found` no webview (toast de update/scanner-
  pause não funcionam — provável descompasso `@tauri-apps/api` npm vs crate `tauri`).
- Persistir `_cold_cache` no `DashboardCache` (DB) pra sobreviver restarts.
- Subdomínio docs em produção (`DOCS_HOST` + `VITE_DOCS_URL` + DNS).

## Migrations criadas (cadeia)

- `x1a2b3c4d5e6` — silver_dropped em player_kill_events + gather_* em albion_players
- `z1a2b3c4d5e6f` — event drafts + signup_policy
- `z2a3b4c5d6e7f` — event assignment locks
- `z3a4b5c6d7e8f` — (ver cadeia em alembic)
- `z4a5b6c7d8e9f` — (ver cadeia em alembic)
- `s5d0e3f8c9b2` — (ver cadeia em alembic)
- `t6e1f9b5d2c8` — GuildProfile + AllianceProfile
- `u7f2a1b3c4d5` — índices em BattleParticipant.guild_id/alliance_id,
  PlayerKillEvent.victim_guild_id/killer_guild_id
- (migration nova de `source_install` em PlayerKillEvent — ver alembic heads)

## Arquivos de plano criados

- `docs/PLANO-EVENTOS-REGEAR-V2.md` — redesenho de eventos e regear
- `docs/PLANO-DOCUMENTACAO-SUBDOMINIO.md` — subdomínio docs público
- `companion/docs/PLANO-ABAS-VIVAS.md` — cockpit com abas vivas
- `companion/docs/PLANO-DESIGN-COMPANION.md` — identidade visual war room
- `docs/PLANO-DESIGN-V2.md` — design v2 do site (referência)