# Ziggs Platform — Guia para Codex

Plataforma web para guildas de Albion Online: gerencia composições de batalha (comps), eventos, batalhas, crafting e regear. Login exclusivo por Discord OAuth.

> **Contexto histórico:** o resumo consolidado das 19 sessões anteriores do opencode
> está em `HISTORICO-SESSOES.md` (raiz do projeto). Leia quando precisar de decisões
> passadas, migrations criadas, arquitetura de perfis/highscores/companion, ou
> pendências em aberto — evita re-explicar o que já foi decidido.

## Sistema de equipe (team orchestrator)

O trabalho no Ziggs é despachado pelo agente **`team`** (`.opencode/agents/team.md`),
um agente primário que tria a dificuldade e delega pra um worker. Troque pra
ele com **Tab** no TUI (ou invoque com `@team`).

**3 tiers × 4 providers = 12 workers** (todos em `.opencode/agents/worker-*.md`):

| Tier | Primary (provider 1) | Fallback 1 | Fallback 2 | Fallback 3 | Quando |
|------|----------------------|------------|------------|------------|-------|
| **Hard** | `worker-hard-zai` (zai-coding-plan/glm-5.2) | `worker-hard-ollama` (ollama-cloud/glm-5.2) | `worker-hard-ollama2` (ollama-cloud-2/glm-5.2) | `worker-hard-go` (opencode-go/glm-5.2) | substancial/maduro/sensível, design, migrations |
| **Medium** | `worker-medium-zai` (zai-coding-plan/glm-5-turbo) | `worker-medium-ollama` (ollama-cloud/kimi-k2.7-code) | `worker-medium-ollama2` (ollama-cloud-2/kimi-k2.7-code) | `worker-medium-go` (opencode-go/kimi-k2.7-code) | claro+delimitado, feature single-file, refactor mecânico |
| **Easy** | `worker-easy-ollama` (ollama-cloud/deepseek-v4-pro) | `worker-easy-ollama2` (ollama-cloud-2/deepseek-v4-pro) | `worker-easy-go` (opencode-go/deepseek-v4-pro) | `worker-easy-zai` (zai-coding-plan/glm-4.7) | trivial, grep-and-report, doc edits, lookups |

**Failover (mesmo tier, próximo provider):** se um worker falha por tokens
esgotados / erro de provider (resposta vazia ou erro de credit/limit/quota),
o orchestrator re-envia a MESMA task pro próximo worker do mesmo tier. Se os 4
providers falharem, escala pro usuário.

**Escalada (sobe de tier):** se o worker devolve `NEEDS_ESCALATION` (a task é
genuinamente difícil demais pra o tier, não falha de infra), o orchestrator
promove: Easy→Medium→Hard. No Hard, se ainda escalou, relaxa o teto ou pergunta
o usuário.

**Não confundir:** worker que **falhou pra rodar** (token/provider) = failover
(mesmo tier). Worker que **trabalhou mas travou** (tarefa difícil) =
escalada (sobe tier).

O worker recebe no briefing: teto de escopo, orçamento (2 retries por
sub-task), e o critério de "pronto". Ele lê `AGENTS.md` antes de começar.
Se o trabalho ultrapassar o teto, ele para e devolve `NEEDS_ESCALATION` com
evidências — não amplia escopo nem continua tentando em silêncio.

Esta regra vale somente para trabalho no Ziggs, não para outros projetos.

### Comandos legados

`/ziggs` e `/ziggs-intake` (`.opencode/command/`) ainda existem como wrappers
de triagem, mas o agente `team` é agora a porta de entrada preferida — ele faz
a mesma triagem inline e despacha direto pros workers via Task tool do opencode
nativo, sem depender do mecanismo Traycer (que é bugado e não avisa quando os
tokens do z.ai-coding-plan acabam).

## Estrutura do projeto

```
hideout-platform/
├── backend/          FastAPI + SQLAlchemy + SQLite (dev) / Postgres (prod)
├── frontend/         React 18 + TypeScript + Vite
├── companion/        App desktop Tauri (Rust + React/TS) — battle scanner distribuído, DNS optimizer, lootlog
├── bot/              Bot Discord legado (discord.py) — não mexa sem precisar
├── bot-v2/           Bot Discord novo (discord.py + cogs) — em uso; fala com o backend via http_client (sessão aiohttp singleton, keep-alive)
└── start-all.cmd     Inicia tudo junto no Windows
```

## Como rodar

```bash
# Backend (porta 8000)
cd backend
scripts/uvicorn.exe app.main:app --reload

# Frontend (porta 5173) — vite faz proxy de /auth, /guilds, /meta → localhost:8000
cd frontend
npm run dev
```

O Vite já está configurado com proxy, então o frontend usa caminhos relativos (`/guilds/...`). Em dev não precisa configurar CORS além do que já está.

```bash
# Companion (Tauri app — precisa de Rust toolchain + MSVC Build Tools)
cd companion
npm run tauri dev          # dev: sobe Vite (porta 1420) + Rust dev
npm run tauri build        # build: gera .msi/.exe em src-tauri/target/release/bundle
```

## Companion (Tauri)

App desktop (Windows/Linux/macOS) em **Tauri 2.x** (Rust + React/TS). Fase 1: sem admin, sem memory read, sem packet capture.

**Stack:** Rust 1.77+ (Cargo), Tauri 2, React 18 + TypeScript + Vite (mesmo padrão do frontend), plugins Tauri: `autostart`, `shell`, `opener`.

**Estrutura:**
```
companion/
├── src-tauri/                  Rust core (binário nativo)
│   ├── src/
│   │   ├── lib.rs              ponto de entrada, commands Tauri, tray, run()
│   │   ├── main.rs             thin wrapper que chama lib::run()
│   │   ├── config.rs           CompanionConfig — JSON em dirs::config_dir()/ziggs-companion/
│   │   ├── api.rs              ApiClient (reqwest, sem cookie — APIs públicas)
│   │   ├── scanner.rs          Scanner worker — claim/report cycle contra /companion/scan/*
│   │   ├── dns.rs              DNS tester — ping Cloudflare/Google/Quad9/OpenDNS + score
│   │   ├── tunnel.rs           WireGuard tunnel via wintun (Windows) — split-tunneling só pro Albion
│   │   ├── albion_ips.rs       resolver hostnames do Albion em IPs (cache 5 min)
│   │   └── lootlog.rs          parser de /loot e CSV lootlogger (local, só copiar)
│   ├── resources/
│   │   └── wintun.dll          driver de rede virtual Windows (baixado de wintun.net)
│   ├── tauri.conf.json         config Tauri (frontendDist=../dist, tray, bundle targets, resources)
│   ├── capabilities/           permissões Tauri v2 (core, shell, opener, autostart)
│   ├── icons/                  ícones (gerados via `tauri icon app-icon.png`)
│   └── Cargo.toml
├── docs/
│   └── companion-vps-setup.sh  script de provisionamento VPS WireGuard (rodar como root)
└── src/                        React/TS UI
    ├── App.tsx                 COCKPIT (jul/2026): abas com Rota/Túnel como foco — ver nota de identidade visual
    ├── main.tsx                bootstrap React
    └── styles.css              CSS global (mesmas variáveis do frontend)
```

**Login: nenhum.** Companion não tem auth — battle scan e DNS são APIs públicas do Albion. Lootlog é só local (parser + copiar CSV, o usuário cola no site da guilda manualmente). Sem cookie, sem sessão, sem Discord OAuth fluindo pelo companion.

**Scan distribuído (Fase 1 — sem admin, sem ToS):**
- Backend tem `companion_scan_tasks` (tabela) com ranges de IDs de batalha por região
- Companion: `POST /companion/scan/claim` → pega tarefa; sonda `https://{host}/api/gameinfo/battles/{id}`; `POST /companion/scan/report` com `found`/`missing`/`errors`
- Backend aplica `upsert_battle_light` nos found (mesma lógica do `battle_sweeper`), grava `BattleIdProbe` nos 404
- **Batalhas pequenas (< 10 jogadores) NEM são armazenadas** — `upsert_battle_light` devolve `None` e o ID vira probe 'missing' (não re-sonda). Kills continuam no `PlayerKillEvent` (independente de batalha), então 1v1/gank ainda conta pro `weapon_stats`; só appearances/assists/healing de lutas <10 somem. Migration `z7c8d9e0f1a2` purga as existentes.
- Claims expiram em 15min (companion caiu → tarefa volta a pending)
- `claimed_by` = install_id (header `X-Ziggs-Install`), **identidade, não auth**: garante 1 range por PC. A mesma instalação pedindo de novo recebe o range que já tem (com TTL renovado) em vez de acumular ranges — 3 processos abertos no mesmo PC durante um rebuild pegavam 3 ranges e contavam como 3 companions. O dado reportado continua sendo validado contra a API pública do Albion no upsert, nunca se confia no client
- `GET /companion/stats` → `{active}` = instalações distintas que pediram trabalho dentro do `CLAIM_TTL`. Companion antigo (sem header) não entra na conta
- **Throttle adaptativo no companion** (`scanner.rs`): delay entre sondagens começa em 150ms, dobra em 429 (teto 5s), recupera -50ms por 200 sustentado (piso 150ms). AIMD igual ao `albion_gate` do backend, mas no client — o companion fala direto com a API pública, não passa pelo rate limiter do servidor. `throttle_ms` exposto no `ScanStats` pra transparência.

**Hosts por região (usar sempre esses — bater com `player_tracker.HOSTS`):**
```python
HOSTS = {
    "americas": "gameinfo.albiononline.com",
    "europe":   "gameinfo-ams.albiononline.com",
    "asia":     "gameinfo-sgp.albiononline.com",
}
```

**Rotas backend `/companion/*` (`app/api/routes/companion.py`):**
- `POST /companion/scan/claim` — pega próxima tarefa de scan (204 = sem trabalho)
- `POST /companion/scan/report` — reporta resultados
- `GET  /companion/dns/targets` — hostnames dos 3 servidores Albion
- `GET  /companion/latest.json` — manifest do auto-updater (204 = sem update, 200 = JSON)
- `GET  /companion/auth/start` — inicia login Discord (redirect pro OAuth, opcional)
- `GET  /companion/auth/done` — pós-OAuth: cunha token companion, mostra HTML de sucesso
- `GET  /companion/auth/poll` — companion faz polling pelo token (408 = aguardando login)
- `GET  /companion/lootlog/active-events` — eventos IN_PROGRESS onde o user está inscrito (bearer auth)
- `POST /companion/lootlog/ingest` — envia CSV do lootlog pra um evento (bearer auth)
- `POST /companion/warm` — nomeia um personagem (o próprio do usuário) pra manter o perfil quente (ver "Warm de perfil" abaixo)
- `POST /companion/warm/seen` — fase 2: nomeia players vistos em jogo (refresh-only, ver "Warm de perfil")

**Login Discord opcional no companion:**
- Companion não tem auth nativa — scan/DNS/prices são APIs públicas.
- Login Discord é OPCIONAL — só pra auto-submit de lootlog. Sem login, tudo funciona igual.
- Fluxo: companion gera nonce → abre browser em `/companion/auth/start?nonce=X` → OAuth normal → `/companion/auth/done` cunha token (cache em memória) → companion faz poll em `/companion/auth/poll?nonce=X` → recebe `{token, user_id, username}`.
- Token = `URLSafeTimedSerializer(secret_key, salt="ziggs-companion-token-v1")`, 30 dias de validade. Guardado em `CompanionConfig.discord_token`.
- Rotas bearer-auth usam `deps.require_companion_user` (valida token, devolve `User`).
- `COMPANION_API_SECRET` no `.env` (mesmo padrão do `BOT_API_SECRET`).

**Lootlog — itens e CSV:**
- O pacote de loot traz o item como **índice numérico**, não como id. `GET /companion/items` serve `[{i, id, en, pt, es}]` onde `i` é a **numeração de documento do `formatted/items.txt` do ao-data** (`market_history._index_to_name`/`get_index_catalog`), o MESMO índice que o pacote carrega e que o `ao-loot-logger` usa. **NÃO é o campo `Index` do `items.json`** — essa é outra numeração (subconjunto reordenado do dump, delta que CRESCE com o id: 0 no topo, ~600 lá pelo id 4000) e saía item errado (pacote 4172 = `T8_HEAD_LEATHER_SET3`, mas `Index==4172` = `T7_HEAD_CLOTH_SET3`). Validado contra 286 eventos de um log real do ao-loot-logger (20/07/2026). O `items.json` fica só pros nomes PT/ES (join por UniqueName); EN sai do próprio items.txt. `items.txt` mora no dump local (`data/ao-bin-dump/`, gitignored igual ao items.json); se faltar, `_read_items_txt` baixa 1× do ao-data e cacheia — senão o loot inteiro viraria `IDX_n` calado.
- Variantes `@enchant` ficam no catálogo (índice próprio por encantamento: `T7_HEAD_PLATE_SET3@1` = 2958). O nome localizado é o do item base — o encantamento aparece no id e no badge de tier da UI.
- Companion cacheia em `<config_dir>/ziggs-companion/item_names_v2.json` (o `_v2` abandona o cache v1, que guardava o índice `Index` errado — bump o nome do arquivo em qualquer mudança de esquema de índice, o loader confia no cache local e só refaz fetch se ele sumir/estiver vazio), com o mesmo retry de 60s do `load_spell_names`. Índice fora do dump vira `IDX_{n}` — **mesma convenção do `market_history.resolve_item`**, então o backend não perde a coleta.
- CSV (`lootlog.rs`): header ao-loot-logger completo, com as colunas de alliance/guild (vazias — o pacote não traz). `parse_loot_rows` no backend casa por **nome de coluna** e tolera extras/ordem, então acrescentar coluna é seguro; **renomear não é**, e é silencioso. Coberto pelo teste em `lootlog::tests`.
- `item_name` vai em **inglês** no CSV de propósito: o arquivo é interoperável (comparado com log de outra pessoa, possivelmente noutro idioma). Tradução é coisa de UI.
- Download vai pra **pasta Downloads** (`dirs::download_dir`, fallback Documents), arquivo avulso com nome datado.
- **O terminal da UI NÃO espelha o CSV.** O arquivo é o formato canônico (ISO completo, id cru, inglês); o terminal é pra ler no meio de um CTA: `23:07 [LOOT] DLO3 looted 4.3 Smuggler's Cape from PouLyD`. Ou seja — só HH:MM (UTC, igual ao CSV, pra cruzar os dois), tier em notação `4.3` (mesma do site em "8.4 Capuz do Asceta"), nome traduzido, e `12×` só aparece quando a quantidade passa de 1. Mudar a exibição não é motivo pra mexer no CSV, e vice-versa.
- No terminal o nome perde a palavra de tier: `8.4 Guardian Boots`, não `8.4 Elder's Guardian Boots` (o número já diz). `TIER_WORDS` no `App.tsx` — EN prefixa (`Elder's X`), PT/ES sufixam (`X do Ancião` / `X del anciano`). **Só a exibição encurta; o CSV mantém o nome completo**, que é o que o ao-loot-logger espera.
  - Tabela na mão de propósito. Dá pra derivar do dump comparando os tiers de cada item, e eu tentei: `Raw Beef` (nome que muda inteiro por tier, não só o adjetivo) virava `Raw`. Cobre 63-68% dos itens; os outros 32% são recurso/peixe/comida, que não têm adjetivo de tier e ficam inteiros — correto.
- **Duas dimensões, dois canais visuais:** a COR do texto é o TIER (`.t-tier-1..8`), o SUBLINHADO é o ENCANTAMENTO (`.t-ench-u-1..4`, cores do jogo: verde/azul/roxo/dourado). Item `.0` não sublinha, pra encantado pular aos olhos. Se usar o mesmo canal pras duas, uma esconde a outra.

**City-markers do reconcile — ABANDONADO (decisão de 19/07/2026):** existiu por algumas horas uma "Phase 2" em que o companion marcava entradas em cidade e o reconcile partia a sessão em viagens (morte só matava o pego depois da última ida à cidade). Removida por decisão do dono, e o defeito é conceitual, não de implementação: **entrar na cidade não implica ter depositado** — quem só passa por ela carregando o loot e morre depois seria COBRADO por itens que morreram junto. Punia o inocente no caso que devia proteger. Não reintroduza sem resolver isso (o sinal correto seria o DEPÓSITO, que o `GuildChestEntry` já cobre). Resíduo inofensivo: a coluna `markers` pode existir no banco de dev, órfã e não mapeada.

**Lootlog auto-submit:**
- **O usuário nunca informa guilda.** A inscrição (`EventSignup`) já diz de quais eventos ele participa e em que guilda cada um está — pedir o snowflake era trabalho manual pra descobrir algo que o backend já sabia. Não reintroduza `lootlog_guild_id`.
- `GET /companion/lootlog/active-events` (sem query param) lista eventos do usuário logado em `in_progress` **ou** `review`, de TODAS as guildas, já com `guild_id`/`guild_name`/`state`.
- `POST /companion/lootlog/ingest` recebe `{event_id, csv_text}`. A guilda vem do `EventSignup`, **nunca do cliente**, e exige inscrição no evento (403 sem ela) — antes qualquer conta logada podia despejar lootlog em evento de qualquer guilda.
- Gatilho do envio automático: evento entra em **REVIEW** (`auto_lootlog_worker` no `lib.rs`, poll de 60s). É quando a guilda fecha o CTA e confere os logs — antes disso o log está pela metade, depois é tarde.
- O worker guarda os `event_id` já enviados só em memória; reenvio é inofensivo (upsert por guild+event+submitter). Não envia CSV vazio, pra não sobrescrever uma submissão manual boa com nada.
- **Não** coloque auto-submit no React. Existia um efeito disparando a cada loot novo: mandava log incompleto dezenas de vezes por CTA, cada uma sobrescrevendo a anterior.

**Warm de perfil pelo companion (jul/2026):** o companion mantém perfis quentes no site nomeando personagens pro backend aquecer. Cobre o buraco do `profile_warmer`, que só aquece participante de batalha rastreada. **Único ponto de escrita:** `warm_self_worker` (`lib.rs`, loop de 5min), enquanto o jogo está aberto (`sniffer.stats.online`). Região vem do servidor AODP detectado (`AodpServer::region()`: west/east/europe → **americas/asia/europe**, a nomenclatura do gameinfo).
- **Doutrina (igual battle scan): só NOMEAÇÃO — o backend busca o dado na API pública da Albion, NUNCA confia em stats vindos do cliente.**
- **Fase 1 — próprio char** (`POST /companion/warm {name, region}`, a cada ~20min = a cada 4º ciclo): o nome vem da resposta de Join (`stats.player_name`). `profile_warmer.warm_by_name`: conhecido e velho (> `STALE_AFTER` = 7d) → enfileira `refresh_requested_at`; **desconhecido → resolve nome→id e faz o BOOTSTRAP** (o caso que importa: cria o perfil de gatherer/solo que nunca cai em ZvZ). Teto `_call_ok(_warm_log, 12/h)` — apertado porque bootstrap dispara busca na Albion.
- **Fase 2 — players vistos** (`POST /companion/warm/seen {region, names}`, todo ciclo = 5min): manda os nomes de `sniffer.entities` (dedup, sem o próprio, teto 100). `profile_warmer.queue_refresh_seen` é **REFRESH-ONLY**: só re-aquece quem JÁ conhecemos e está velho; **nome desconhecido é IGNORADO, não faz bootstrap** — de propósito, senão um cliente mentiroso amplificaria busca na Albion com nome aleatório (bootstrap fica só pro próprio char). Cobre briga sub-limiar/roaming que o battle tracker pula. Teto `_call_ok(_seen_log, 20/h)`. É idempotente (re-enviar `entities` é de graça).
- **Não aumenta a vazão de warming** — o teto real é a cota da Albion no `albion_gate`; os refreshes das duas fases são drenados pelo loop normal (`sync_refresh_requests`). Serve pra manter fresco quem importa (o próprio char + ativos-agora), a custo baixo.
- `_call_ok(bucket, install, window, max)` em `companion.py` é o teto genérico por-chamada; `_warm_log` e `_seen_log` são baldes separados pra uma rota não comer a cota da outra.

**Confiança no dado do companion — o que dá e o que não dá pra garantir:**
- **Proveniência no cliente é IMPOSSÍVEL, não difícil.** O companion lê pacotes da máquina do usuário: dá pra injetar UDP local com IP de origem forjado (o Npcap não distingue), patchear o binário, ou pular o app e falar direto com a API. O tráfego Photon não é assinado pela SBI, então não há nada pra verificar. Não gaste tempo tentando "provar" origem no cliente.
- **O padrão certo, quando existe fonte autoritativa:** o cliente diz só ONDE olhar e o backend busca a verdade. É o que o battle scan faz (`upsert_battle_light` consulta a API pública do Albion). Cliente não consegue inventar batalha.
- **Quando não existe fonte autoritativa, use consenso:** lootlog corrobora entre testemunhas (`COPY_OVERLAP_THRESHOLD`, marca "fonte única"); preço usa mediana + corte de outlier por IQR. **É aqui que mora a defesa real contra dado mentiroso** — um mentiroso isolado é descartado por estatística.
- **`item_prices.source_install`** grava a instalação que reportou (NULL = nosso próprio sync). Não é auth — o cliente escolhe o id. Serve pra ATRIBUIR: como a tabela é append-only, expurgar uma fonte envenenada é um `DELETE WHERE source_install = ?`. Só no histórico; em `item_prices_latest` o último a escrever vence e a fonte diria pouco.
- **Teto de vazão** (`_rate_ok`) por LINHAS, não por request — 200 requests de 1 linha fazem o mesmo estrago que 1 de 200. Preços e market history dividem a mesma cota, senão bastaria alternar as rotas pra dobrar o limite. `_rate_ok` revalida o formato do id: se um call site passar o header cru, id inventado por request criaria um balde por request e o teto viraria decoração.
- **A meta não é tornar fraude impossível** (não está no cardápio quando o código roda na máquina do adversário) — é torná-la **detectável, atribuível e reversível**.

**Modelos backend (`app/models/companion.py`):**
- `CompanionScanTask` — uma tarefa (range de IDs por região, status pending/claimed/done/failed)

**Config persistido (`CompanionConfig`):**
- `api_base_url`, `character_name`, `region`
- Toggles transparentes: `collect_battles`, `collect_prices`, `collect_damage_meter`, `collect_auto_lootlog`
- `autostart`, `minimize_to_tray`
- WireGuard: `tunnel_enabled`, `tunnel_endpoint`, `tunnel_server_pubkey`, `tunnel_client_privkey`
- Discord login (opcional): `discord_token`, `discord_user_id`, `discord_username`
- Lootlog auto-submit: `auto_lootlog_submit` (só o toggle — a guilda é derivada no backend)
- `spell_index_offset` — ajuste do índice de feitiço do damage meter (ver acima). **Sem UI desde jul/2026** (era campo misterioso que quebrava todos os nomes com um typo): calibração é NOSSA, editando `config.json` direto. Campo e `set_config` continuam vivos.
- `install_id` — hex de 32 chars gerado no 1º uso e persistido. Leia SEMPRE via `config::install_id()` (gera+salva se vazio, cacheado num `OnceLock`), nunca direto do campo. `ApiClient::new` já o manda como default header em toda request, então nenhum call site precisa passá-lo.

**Rota tipo ExitLag (WireGuard + VPS):**
- Companion cria interface wintun "Ziggs" (10.99.0.2/24), túnela UDP dos IPs do Albion via WireGuard pra VPS
- `tunnel.rs` usa `boringtun` (WireGuard userspace) + `wintun` (driver virtual) — sem kernel module
- Split-tunneling: só tráfego dos IPs do Albion vai pelo túnel, resto fica direto
- **Teste antes de ativar:** mede latência direta vs túnel, só ativa rotas se túnel for melhor
- **Fallback automático:** VPS cai → volta pra rota direta
- Requer admin (criar interface virtual + adicionar rotas) — auto-elevate em runtime via `ShellExecuteW("runas")` quando `tunnel_enabled=true`
- **`is_windows_admin()` é a ÚNICA checagem de elevação — não escreva outra.** Ela usa `TOKEN_QUERY` (0x0008), não `PROCESS_QUERY_INFORMATION` (0x0400), que algumas configs de segurança negam mesmo em processo elevado. Já custou dois bugs: primeiro um loop de relaunch no startup, depois — porque `tunnel_is_admin` tinha uma **cópia** do check que ninguém corrigiu junto — a aba Túnel dizendo "precisa de admin" com o app já aberto como administrador. Hoje o command só delega. Precisa do check em outro lugar? Chame essa função.
- VPS: script de provisionamento em `companion/docs/companion-vps-setup.sh` (Ubuntu/Debian, ~$5/mês Vultr/Hetzner)
- Fluxo de setup: companion gera keypair → usuário roda script VPS passando pubkey do client → VPS retorna endpoint + server pubkey → usuário cola no companion

**Damage meter (`photon_parser::DamageAcc`, tab Damage):**
- **Só DANO.** Cura é descartada no sniffer de propósito — o diferencial é a listagem de dano ser completa, não competir com o painel de cura do AAT. Não re-adicione cura sem pedido explícito.
- Fonte: evento HealthUpdate (código 6), `change < 0`. Params (target=0, change=2, causer=6, spell=7) vêm do AAT e **mudam a cada patch** — o sniffer loga os params do 1º HealthUpdate da sessão pra recalibrar.
- `DamageAcc::record(spell_id, amount, now)` é o único ponto de escrita: acumula total, por-skill (`hits`/`total`/`max_hit`), timeline e first/last hit. Testes em `photon_parser::tests`.
- `hits` = GOLPES (eventos de dano), **não casts**: um DoT de 5 ticks conta 5. Contar cast de verdade exige escutar o evento de conjuração, que hoje não lemos. A UI diz "Golpes"/"Hits" por isso — não relabele pra "casts".
- Timeline: `VecDeque<(epoch_sec, dano)>` com janela `TIMELINE_SECS` (180s). O command `get_damage_meter` converte pra array denso de 180 posições alinhado em `now` (índice 0 = 3 min atrás), então a UI só desenha o array sem saber de timestamp.
- `dps` usa tempo ATIVO (primeiro→último golpe, mínimo 1s), não duração da sessão — justo pra quem entrou na luta no meio.
- **Nomes de skill — MAPEAMENTO NÃO VERIFICADO.** `scripts/seed_spell_names.py` baixa o `spells.xml` do ao-bin-dumps e gera `data/spell_names.json` (8936 entradas, ordem de documento); `GET /companion/spells` serve; o companion cacheia em `<config_dir>/ziggs-companion/spell_names.json` e resolve `spell_id + spell_index_offset` → nome, com fallback `Habilidade {id}`.
  - Tem que ser o **XML**: o `spells.json` agrupa por tipo (activespell/passivespell/togglespell) e destrói a ordem, e nenhum dos dois traz índice explícito (diferente de `items.json`, que tem `Index`).
  - **CONFIRMADO com 19 pares medidos no dummy** (skill usada → id observado): índice = posição no documento contando `activespell`, `passivespell`, `togglespell` **e `channelingspell`**, com `spell_index_offset = 0`. 19/19 batem.
  - **`channelingspell` é a pegadinha que quebrava tudo.** Ele não é irmão dos outros: é **filho** de um `activespell` e **não tem `uniquename`** — mas ocupa um índice no jogo. São 276. Sem contá-lo o índice atrasava ~1 a cada 26 feitiços, e o erro CRESCIA com o índice (delta -39 lá pelos 2800, -80 lá pelos 3900), então todo nome saía errado e cada vez mais longe. `build()` monta um mapa de pais e faz o canalizado herdar id/`namelocatag` do pai — assim nome, tradução, ícone e família saem certos de graça. Coberto por `test_channelingspell_ocupa_indice_e_herda_o_pai`.
  - Como foi diagnosticado, se voltar a quebrar num patch: pares (skill real → id observado) → `delta = índice_real - id`. Delta **constante** = offset, resolve no `spell_index_offset`. Delta **crescente** = elemento faltando na contagem; ache a tag cuja densidade bate com o crescimento do delta. O dump `[CALIB nn]` no debug log dá os ids sem precisar copiar da tela.
  - Ordem é tudo: qualquer mudança que reordene os feitiços troca TODOS os nomes de uma vez e falha em silêncio (nome errado, não erro). Coberto por `tests/test_spell_names.py`.
  - **Traduções:** o seed também baixa o `localization.xml` (TMX, ~73 MB, streaming com `iterparse`) e resolve `namelocatag` — ou a convenção `@SPELLS_{uniquename}` quando ele falta — pra PT-BR/ES-ES. Cobre ~4.4k dos 8936 (o resto são sub-feitiços internos tipo `AIR_RAID_BOLTS_DAMAGE`, que caem no nome normalizado em inglês). `pt`/`es` só entram no JSON quando diferem do inglês. O idioma mora no `localStorage` do webview, não no config do Rust, então `get_damage_meter` manda `name`/`name_pt`/`name_es` e a UI escolhe.
  - **Dump de calibração `[CALIB nn]`:** os 15 primeiros eventos de dano da sessão vão pro debug com TODOS os params e seus VALORES (`Documents/ziggs-companion/companion-debug.log`, zerado a cada sessão; também aparece no terminal da aba Lootlog). Antes logava só as chaves, uma vez — inútil justamente quando a suspeita é que o param do feitiço mudou de lugar no patch. É a primeira coisa a olhar quando nome de skill vier errado: compare o param 7 com os outros e veja qual varia por skill.
- **spell 0 é descartado no acúmulo** (`sniffer.rs`): é dano sem feitiço atribuído — o que aparece quando alguém dá `/die`. Caía no índice 0 da tabela e o meter mostrava "Trudge". Fora do acúmulo, não só da exibição: no total, bagunçaria a % das outras skills.
- **Dano NÃO vem só de arma.** Habilidade de armadura/bota também causa dano, então "a skill tem que ter família de arma" **não** é um teste válido de calibração — já me levou a uma conclusão errada. O sintoma bom de mapeamento errado é o contrário: aparecer na lista skill que **não causa dano nenhum** (buff defensivo tipo Ethereal Form).
- **Nome esquisito ≠ offset errado.** O dano vem creditado ao SUB-FEITIÇO interno, não à habilidade que o jogador clicou. A maioria aponta o `namelocatag` do pai e sai certa, mas os sem tag caíam no normalizador do uniquename e viravam nomes que não existem no jogo — o caso real foi `DASH_KNOCKBACK_COOLDOWN_REDUCTION` → "Dash Knockback Cooldown Reduction", cujo pai `DASH_KNOCKBACK` é **"Soaring Swipe"**. `adopt_from_parent` no seed anda pra trás nos `_` até achar um ancestral localizado e herda nome + `icon` (1053 entradas). Antes de mexer em `spell_index_offset`, cheque se o `#id` cru cai na FAMÍLIA certa: se cai, é herança faltando, não offset.
  - **Ícone vem do NOSSO backend, não da CDN da Albion:** `GET /render/spell/{uniquename}` (`app/api/routes/render.py`). Baixa da Albion na primeira vez, salva em `data/render_cache/spells/` e depois serve do disco — mesmo esquema que `/render/item/` já usava no site. A UI monta a URL com `config.api_base_url`, campo `skip_deserializing` que vem SEMPRE do binário (config velho não pode apontar o companion novo pro backend antigo).
  - **A CDN de spell NUNCA dá 404.** Pra chave sem arte devolve 200 com um PNG vazio (~281 B) **ou** com um placeholder branco de 26178 B, sempre o mesmo sha1 `7b910616…` — este último é o "render totalmente branco". `_is_placeholder()` pega os dois; sem isso o lixo era gravado em disco e servido pra sempre.
  - **Fallback por NOME:** em algum momento a Albion passou a chavear a arte de skill nova/reworkada pelo NOME (`/spell/Powerful%20Swing.png`) em vez do uniquename. Sub-feitiço como `HAMMER_SHOVE_SWING_EFFECT` volta placeholder pelo id e resolve pelo nome. O proxy tenta uniquename → nome em inglês (de `spell_names.json`) → 404. Grava sempre sob a chave ORIGINAL, porque quem pede é sempre pelo uniquename. Coberto por `tests/test_render.py`.
  - Nada de arte NÃO é cacheado: sem arte hoje pode ter arte no próximo patch.
  - **Dois caches guardam o estrago, e os dois precisam ser furados:** (1) o disco do backend — `_cache_usable()` apaga e rebaixa arquivo com o tamanho do placeholder, então se cura sozinho; (2) o webview, que recebeu `max-age=31536000, immutable` e não pergunta de novo por um ano — furado com `?v=N` na URL do `SkillIcon`. Aconteceu de novo? Bump o `v`.
  - URL antiga direta (não usar mais): `https://render.albiononline.com/v1/spell/{uniquename}.png` — chaveia pelo **uniquename**, não pelo `uisprite` (o site usa a mesma URL em `EquipGrid`/`SpellPicker`). Falhou/sem rede, o `onError` esconde a `<img>`. Sub-feitiço usa o campo `icon` (id do pai): o CDN TEM arte pro sub-feitiço, mas é um ícone genérico de passiva em vez do da habilidade. **Nome inexistente devolve 200 com ~281 bytes, não 404** — status code não serve pra medir cobertura, tem que olhar o tamanho.
- **Toggle "Só dano em jogadores" — acumulador separado, NÃO é filtro de linha.** `party only` e `dano mínimo` filtram as rows no React; este não pode, porque o alvo do golpe morre no `record` (o `DamageAcc` é indexado por causer e descarta o `target_id`). Então o sniffer mantém `damage_vs_players` em paralelo a `damage`, alimentado só quando o alvo está em `entities`, e `get_damage_meter(vs_players)` escolhe qual mapa ler — por isso o toggle entra na dependência do poll no React. `clear_damage_meter` zera **os dois**. Guardar breakdown por alvo pra filtrar depois custaria muito mais memória.
- Critério de "alvo é jogador" = estar em `entities`, o MESMO que decide quais linhas aparecem (só `NewCharacter`/evento 29 alimenta o mapa, e isso só vem de player). Se um dia mob entrar em `entities`, os dois quebram juntos — é de propósito, um critério só.
- **Um jogador tem VÁRIOS entity ids na sessão** — o jogo dá id novo quando ele sai e reentra no teu alcance de visão, e `entities` só cresce (nada nunca remove). `damage` é indexado por causer_id, então `get_damage_meter` **junta por nome** (`DamageAcc::merge`) antes de montar as linhas. Sem isso o dano da pessoa saía picado em várias linhas, e como o React usa o nome como `key` as chaves colidiam e a lista duplicava visualmente a cada troca de fonte de dados (toggle "só jogadores"). Qualquer leitura nova de `damage`/`damage_vs_players` tem que agregar por nome também.
- **Cor da família colore as BARRAS**, não o texto: `--wfam` é definida em `.dmg-entry.w-{familia}` e puxada pela barra principal (`.dmg-bar`) e pelas barras da bracket expandida (`.dmg-skill-bar`, mesma cor com `opacity: 0.18` — evita declarar variante translúcida de cada família). Sem arma inferida cai no dourado. Trocar uma cor = mexer numa linha só.
- **Render da arma no ranking está BLOQUEADO por falta de dado.** Mostrar a arma com tier/encantamento/qualidade exige ler o EQUIPAMENTO, e `extract_new_character` só lê id (param 0) e nome (param 1). Das skills só dá pra tirar a família — tier e qualidade não estão em feitiço nenhum. O dump `[CHAR n]` (3 primeiros NewCharacter, com o conteúdo dos arrays aberto por `deep()`) existe pra achar o array de equipamento; quando ele for identificado, `/render/item/{id}` já resolve a imagem.
- **Arma no ranking é INFERIDA pelas skills, não lida do equipamento.** O `NewCharacter` que parseamos só traz id e nome; o array de equipamento existe no pacote mas não é lido. Então: cada feitiço carrega a família da arma (`fam`: bow/dagger/sword/…) na tabela de feitiços, e a arma da linha é a família da skill que **mais deu dano** (a lista já vem ordenada). Quem só deu auto-attack fica sem arma — a coluna tem largura fixa pra não desalinhar os nomes.
  - `weapon_families` no seed resolve `craftingspelllist/@reference` + deltas `craftspell`/`removespell`: **629 das 771 armas não têm lista própria**, só apontam pra uma arma-base. Ignorar essa indireção derrubava a cobertura pela metade e sumia com a família `dagger` inteira e com `AIR_RAID`. Sub-feitiço herda a família por prefixo (o dano vem creditado a ele, não à habilidade).
  - **São 17 famílias, e a 17ª mora em outra lista.** Os cajados de shapeshifter (polymorph) ficam em `items.transformationweapon`, NÃO em `items.weapon` — olhando só `weapon`, a família `shapeshifterstaff` inteira sumia e essas armas apareciam sem cor nem rótulo. `items.equipmentitem` (665) e `items.mount` (34) também têm `craftingspelllist`, mas são armadura/capa/montaria e **não** entram na coluna de arma.
  - Feitiço que aparece em duas famílias é **descartado**, não chutado — melhor sem arma do que com a errada. 990 das 8936 entradas têm família; o resto é mob/consumível, que não vem de arma.
  - **Ícone de auto attack** sai da família da arma da LINHA (a skill de ataque básico não tem entrada no dump): melee / arco-besta / mágico, em `AUTO_ATTACK_ICON`. São ícones de habilidade reaproveitados — o jogo não expõe arte pro ataque básico. Os três saem em preto e branco (`.dmg-skill-icon.gray`) — a falta de cor é o que separa ataque básico de skill de verdade na lista. As 16 famílias estão todas classificadas; **família nova cai em "mágico" calada**, então ao adicionar arma nova confira `MELEE_FAMS`/`RANGED_FAMS`.
  - O CDN também aceita nome localizado (`/spell/Speed%20Shot.png?locale=en`) e devolve a MESMA imagem, byte a byte, do uniquename. Usamos o uniquename: nome quebra se a Albion renomear.
  - **NÃO use `weapon_spells` do banco pra isso.** Ela existe pro CompBuilder e tem só 312 feitiços, sem resolver referência — `AIR_RAID` não está em arma nenhuma lá.
- Copy: **`1. nome — dano (%)`**. Mapa, DPS e cura ficam de fora — a lista tem que ser legível no celular.
  - **Nada de `#` no começo da linha:** no chat do Albion `#` é caractere de comando e engolia a linha colada. Por isso `1.` e não `#1`.
  - A `%` é sobre o total **filtrado** (o que está na tela), não sobre a sessão inteira — senão a soma da lista colada não daria 100%.
- **A família da arma NÃO aparece escrita no ranking**, só na cor das barras. O nome fica no `title` da linha: sem ele, ninguém teria como traduzir "barra rosa" em "adaga".

**Custo de máquina — quando o companion pode trabalhar:**
- **O critério é ZONA, não janela.** Minimizado na bandeja ele continua trabalhando normalmente; o que decide é `heavy_work_ok()` no `lib.rs`: seguro = **jogo fechado** (`sniffer.stats.online == false`, de graça, sem varrer processo) **ou** jogador fora de zona PvP. Não introduza um segundo eixo de pausa (visibilidade de janela, foco, etc.) — dois critérios tornam o comportamento imprevisível de depurar. Coberto por `policy_tests`.
- **Nada de rajada.** `TransferQueue::flush_some(api, max)` manda poucos itens por vez com 300ms entre eles. Existe **um único uploader** (task no `setup` do `lib.rs`, tick de 5s, 3 itens) que é o ÚNICO lugar com política de rede. Produtor (preços, market history, scanner) só **enfileira**. Antes cada um dava seu `flush_all` e a volta pra zona azul despejava a fila inteira de uma vez — pico de rede logo depois da luta.
- `flush_all` sobrou só pro command manual (`flush_transfer_queue`), onde a rajada é pedida pelo usuário.
- **O PoW do AODP NÃO é caro — medido, não estimado:** desafio real do servidor (`want_len=41`, ~20 bits) resolve em **123 ms** num release a 3,2M hash/s, uma vez por lote. Não deixe o comentário "CPU-bound" no `upload()` sugerir outra coisa: ele justifica o `spawn_blocking` (não travar o executor), não um custo alto. Passa pelo `heavy_work_ok` mesmo assim, porque é grátis fazer isso.
  - **`want_len` NÃO é bits.** É a expansão ASCII do hex: cada 8 chars prendem 1 char hex = 4 bits, então `entropia ≈ want_len/2`. A trava era `want_len > 40`, que rejeitava o desafio REAL de 41 chars — `solve_pow` devolvia None em 2µs e **o feed ao AODP nunca subiu nada**, silenciosamente (o erro só saía como "PoW não resolvido" numa linha de debug). Hoje `too_hard()` corta em 56 chars (~28 bits ≈ 1,5 min), com teste travando os 41.
- **`get_damage_meter` roda em 2 fases:** sob os locks do sniffer só AGREGA por nome; a formatação cara (timeline densa de 180 posições por jogador, clones, sort, lookup de feitiço) acontece com os locks soltos. O loop de pacotes precisa desses mesmos locks a cada golpe — segurar durante a formatação travava a captura a cada 2s, justo em ZvZ.

**Layout — COCKPIT com ABAS VIVAS (jul/2026, ver `companion/docs/PLANO-ABAS-VIVAS.md`):** o companion NÃO é app de configurações, é um monitor. **Três abas** (strip sob a barra de comando), cada uma com **badge AO VIVO que atualiza mesmo sem foco**: **Rota/Túnel** (default e foco — latência + ▲ verde quando túnel ativo), **Damage Meter** (total da sessão) e **Lootlog** (nº de loots). Os dados dos badges vêm SEMPRE de estado do App() — `tunnelStatus`, `sniff_stats.damage_total` (somado na LEITURA em `get_sniff_stats` no lib.rs, nunca no hot loop) e `loot_count` — nunca de componente de aba, porque aba desmonta ao perder o foco. Pelo mesmo motivo o histórico do gráfico túnel×direto vive no App (poll de 5s) e desce por prop pro `TunnelHero`. A aba Rota é SÓ túnel: hero (latência gigante + ganho, hops Você→VPS→Albion, gráfico alto; sem VPS renderiza aguardando, SEM número inventado — e o empty do gráfico checa `hasData`, não só length, porque hist enche de amostra nula sem VPS) + rail com painel **Conexão** (endpoint/tráfego/split/fallback/erro — dados reais, saíram do hero) e ad 300×250. Header só tem sessão+pacotes (loot/damage moram nos badges). **Ads em toda aba:** 728×90 no hero da Rota e no rodapé de Damage/Lootlog (`.ck-full`), 300×250 no rail da Rota. Config+Túnel em modal no ⚙. Toggles de captura nos headers dos painéis, e `collect_damage_meter`/`collect_auto_lootlog` nascem LIGADOS (default + serde(default) no config.rs; JSON salvo com false é respeitado). **Cores de família:** as vars --wfam vivem nos seletores `.dmg-entry.w-*` — componente novo que mostre barra por classe precisa entrar nessa lista, senão cai no dourado silenciosamente (o alias `.ck-mini.w-*` morreu junto com o DamageRail). **Armadilha que já mordeu:** hook depois do early-return da splash = React #310 quando o config carrega — todo usePoll novo no App() vai ANTES do if (!config). **A mesma armadilha vale pra qualquer estado de UI de uma aba, não só pros badges:** os toggles do Damage Meter (`partyOnly`, `vsPlayers`, `minDamage`) eram `useState` DENTRO de `DamageTab` até 20/07/2026 — trocar de aba e voltar desmontava o componente e resetava os três pra desligado, mesmo com o dano continuando a ser capturado. Fix: os três agora são estado do `App()` (junto de `tunnelStatus`/`hist`) e descem por prop; `DamageTab` só guarda o que É correto perder ao trocar de aba (`expanded` das linhas, `copied` do botão, `rows` — todos derivados do poll, recarregam sozinhos). Qualquer controle novo numa aba que precise sobreviver à troca de aba tem que nascer em `App()`, nunca dentro do componente da aba.

**Identidade visual — v2 "war room" (jul/2026):** o companion usa a MESMA linguagem do site (`docs/PLANO-DESIGN-V2.md` na raiz): painéis em gradiente com cantos fixos TL/BR que douram no hover, headers editoriais uppercase + régua, grid técnico de fundo. Plano e fases em `companion/docs/PLANO-DESIGN-COMPANION.md`. Regra de ouro: valor visual novo que não existe no site **não entra** — é transferência, não redesign. Movimento: barras de dano e timeline transicionam entre polls (`width`/`height` com ease), toggle tem squish no press. Verificação visual SEM rebuildar o Tauri: harness em `dist/` (stub de `__TAURI_INTERNALS__`) + Edge headless — receita no plano.

**System tray:** ícone na bandeja, menu "Abrir/Pausar/Sair". Fechar janela com `minimize_to_tray=true` esconde em vez de sair. Autostart via `tauri-plugin-autostart` (Registry no Win, LaunchAgent no macOS, .desktop no Linux).

**Arranque com o Windows — nada que roda no startup pode falhar uma vez só.** Com autostart o companion sobe antes da rede e do serviço do Npcap estarem prontos, e o que era tentativa única morria calado pela sessão inteira. Pontos já mordidos:
- `Sniffer::run` — `pcap::Device::list` + abrir interfaces vive num loop que repete a cada 15s até abrir pelo menos uma (`open_all` devolve `0` enquanto nenhuma abre). Antes: zero interface = `return`, e o Albion nunca era detectado.
- **Interface certa aparecendo TARDE não era coberta pelo retry acima — mordida separada (20/07/2026).** O retry só reagia a "zero interfaces abriram"; se QUALQUER interface com IPv4 abrisse primeiro — um adaptador virtual (Hyper-V, Docker/WSL vEthernet, VirtualBox Host-Only, VPN client) que já tem IP na hora do boot, antes da placa de rede real terminar DHCP/associação Wi-Fi — o sniffer parava de procurar pra sempre, escutando só a interface errada, e o Albion nunca era detectado NAQUELA sessão (log mostrava `raw=0` sem parar). Fix: `open_all` agora recebe `tx`/`opened: &mut HashSet<String>` de fora e é chamado de novo a cada 30s (`last_iface_scan`) pra abrir qualquer interface NOVA que apareça depois — o channel vive a sessão inteira em vez de fechar depois da primeira leva. Interfaces que falharam ao abrir NÃO entram em `opened` (a falha pode ser temporária), só as que abriram com sucesso.
- `load_spell_names` — repete a cada 60s até baixar. Antes: uma falha e o damage meter passava a sessão em "Habilidade 2972". Backend respondendo lista **vazia** não é repetido (dump não seedado; insistir não resolve).

**Npcap — instalação é MANUAL de propósito (não re-tentar automatizar):** o instalador free ABORTA o `/S` ("silent installation is only supported in Npcap OEM") e a licença free também proíbe redistribuí-lo embutido no nosso NSIS — as duas coisas são exatamente o produto da licença OEM (npcap.com/oem). Já existiu `nsis-hooks.nsh` + `resources/npcap-installer.exe` tentando isso; removidos em 19/07/2026. Fluxo vigente: sniffer sem Npcap seta `stats.error` mencionando "Npcap" → banner `.ck-npcap` sob as abas com botão que chama `open_npcap_download` (abre npcap.com no browser) → usuário instala com as opções padrão (modo WinPcap NÃO é necessário — `ensure_npcap_dll_path` resolve o subdir) → o retry de 15s do `Sniffer::run` pega a instalação sozinho, sem reiniciar o app. Enquanto isso o resto (túnel, scanner, DNS) funciona normal. Se um dia comprarmos OEM, ressuscitar o hook do git log.
- **Resíduo perigoso removido em 20/07/2026:** sobrevivia em `sniffer.rs` um `try_install_npcap_silently` que baixava o installer oficial e rodava `/S /winpcap` **de forma síncrona no thread principal, ANTES da janela do Tauri abrir** — exatamente o auto-install que o parágrafo acima diz pra não reintroduzir. Como o installer free aborta silent install (às vezes com um dialog que ninguém via, sem janela nenhuma atrás pra dar contexto), quem instalava o companion pela primeira vez sem Npcap via o app "travar" no boot por até 2 minutos (timeout do download) sem entender por quê. `ensure_npcap_dll_path` agora só ajusta o PATH quando o Npcap JÁ está instalado; sem ele, não faz nada — o banner de download manual cobre o resto.
- **Tutorial (20/07/2026):** o banner compacto na sidebar (`.ck-side-npcap`) sempre existiu mas era discreto demais pra quem tá instalando o companion pela primeira vez. Agora, sempre que `npcapMissing` (mesmo regex `/npcap/i` em `sniffStats.error`, computado uma vez em `App()`) é verdadeiro E o usuário não dispensou nesta sessão, aparece um modal com 3 passos numerados (baixar → instalar com opções padrão → pronto, detecta sozinho). Reavaliado a cada abertura do app a partir do erro REAL do sniffer — não é uma flag "primeira vez" persistida, então se o usuário fechar sem instalar, reaparece na próxima sessão. Dispensar (`npcapTutorialDismissed`) só esconde o modal; o banner da sidebar continua.
- **Autostart NÃO se registra sem Npcap (20/07/2026):** `sniffer::npcap_installed()` só confere se a chave do registry existe (sem mexer em PATH, ao contrário de `ensure_npcap_dll_path`). Os dois pontos que chamam `set_autostart`/`autolaunch` no Windows (`setup()` no boot e o handler de `set_config` quando o toggle muda) agora exigem `npcap_installed()` além do toggle — sem Npcap, a tarefa do Task Scheduler nunca é criada, mesmo com `autostart: true` (o default). Quem baixa o companion e nunca instala o Npcap não tem o processo reabrindo sozinho a cada boot pra nada. Como `setup()` reavalia a cada launch (não só quando o toggle muda), instalar o Npcap depois e reabrir o app já registra sozinho — sem precisar mexer no toggle. Não cobre o sentido inverso (Npcap desinstalado DEPOIS de já ter registrado) — fora do escopo do que foi pedido, e a tarefa de qualquer forma não faz nada útil sem o driver.

**Auto-updater (forçado, sem opção de ficar na versão antiga):**
- Plugin: `tauri-plugin-updater` + `tauri-plugin-process` (relaunch).
- No startup (`setup`), task async roda `auto_update()`: checa endpoint, se há update baixa+instala+relaunch automaticamente. Sem popup de confirmação, sem botão "agora não".
- Notificação sutil: emite evento `update-status` (`downloading`/`installed`) pro frontend, que mostra um toast discreto no canto inferior direito. Some sozinho.
- Windows: `installMode: "passive"` — barra de progresso mínima do installer NSIS, sem interação.
- Endpoint: `GET /companion/latest.json` (backend). Sem manifest publicado → 204 (sem update). Com manifest → 200 + JSON. O Tauri compara versões sozinho (só instala se for mais nova).
- Manifest: arquivo estático em `backend/data/companion-release.json` (exemplo em `.example.json`). Publicar update = copiar o JSON pro `data/` e subir os artefatos (.exe/.AppImage/.app.tar.gz + .sig) pro CDN.
- Assinatura: par de chaves em `~/.tauri/ziggs-companion.key` (privada, senha `ziggs`) e `.key.pub` (pública, já no `tauri.conf.json`). Build com `TAURI_SIGNING_PRIVATE_KEY` + `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` no env gera os `.sig`.
- **Atenção:** se perder a chave privada OU a senha, não consegue publicar updates pra installs existentes. Guarde bem.

**Bug não-óbvio do Tauri 2:** `#[tauri::command] pub fn ...` em `lib.rs` (crate-type lib) gera erro `E0255: __cmd__X defined multiple times`. Solução: usar `fn` (privada) nos commands — o `generate_handler!` reexporta eles internamente. Não usar `pub fn` em commands Tauri quando o crate é lib+bin.

**Smart App Control bloqueia o companion — DIAGNOSTICADO em 20/07/2026, fix em andamento:** confirmado nesta máquina que o `.exe`/instalador NSIS saem `NotSigned` (`Get-AuthenticodeSignature`) — o projeto NUNCA teve certificado Authenticode, só a chave minisign do auto-updater do Tauri (que verifica integridade do update, não tem nada a ver com o que o Windows confia pra RODAR o binário). Confirmado também que o Smart App Control está ativo e enforced (`HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy!VerifiedAndReputablePolicyState = 1`) e que a mesma policy já bloqueou DLL não assinada durante um `cargo build` normal nesta sessão — ou seja, qualquer binário sem assinatura é tratado como desconhecido e bloqueado, sem opção de "confiar" por app (diferente do SmartScreen clássico). O comportamento do companion (elevação UAC, driver Npcap, adaptador virtual WireGuard, auto-updater que baixa e roda binário novo sozinho) é exatamente o padrão que o classificador da Microsoft trata como suspeito, então mesmo assinado a reputação começa do zero.
- **Caminho escolhido: Microsoft Trusted Signing (Azure).** Mais barato que certificado OV/EV tradicional, feito especificamente pra esse cenário (dev pequeno/individual), aceita verificação de pessoa física.
- **Wiring já feito** (`tauri.conf.json` → `bundle.windows.signCommand` chama `scripts/sign-windows.ps1`, que roda `signtool /dlib /dmdf` com as 3 env vars da conta Trusted Signing). Sem essas env vars o script PULA a assinatura de propósito (exit 0) — build local/dev continua saindo sem assinar, igual sempre saiu, até o cadastro no Azure estar pronto. Não trave o dev loop tentando forçar assinatura sem conta configurada.
- **Ainda falta (só o dono consegue fazer — verificação de identidade):** criar o recurso Trusted Signing no Azure Portal (região East US/East US 2/West US 2), passar pela verificação de identidade, criar o certificate profile (Public Trust), dar a role "Trusted Signing Certificate Profile Signer" pra quem for assinar, instalar o Windows SDK (`signtool.exe`) + pacote NuGet `Microsoft.Trusted.Signing.Client` (dlib) nesta máquina, e setar `ZIGGS_TRUSTED_SIGNING_ENDPOINT`/`_ACCOUNT`/`_PROFILE`/`_DLIB` no ambiente antes de rodar `npm run tauri build`. **Confira os flags exatos do `signtool` contra a documentação ATUAL da Microsoft antes do primeiro uso real** — o nome do dlib e a sintaxe já mudaram de versão pra versão do pacote cliente, e isso nunca foi testado contra uma conta real (sem acesso ao Azure do usuário).
- Mesmo assinado, certificado NOVO começa com reputação zero — a Microsoft pode segurar/avisar nos primeiros downloads até acumular telemetria. Isso é esperado, não é bug de configuração.
- **Não** oriente usuário final a desligar o Smart App Control manualmente — não é toggle por-app, é on/off geral do Windows, e a Microsoft avisa que pode não dar pra religar sem reinstalar. Não é conselho que escala pra guildas inteiras.

**Fases:**
- **Fase 1 — FEITA:** battle scanner distribuído + DNS tester + lootlog + tray + autostart.
- **Fase 2 — FEITA (jul/2026):** packet capture (Npcap) pra price/gold/market-history. Requer admin (auto-elevate no boot).
- **Fase 2.5 — OBSOLETA:** era "memory read pra damage meter + auto lootlog", mas os dois objetivos saíram VIA PACKET CAPTURE (HealthUpdate + OtherGrabbedLoot), sem ler memória — mais estável que offsets de RAM que quebram a cada patch. Não implemente memory read sem um objetivo NOVO que o sniffing não alcance.
- **Pendências reais** (não são fases): validação em jogo dos dumps [CALIB]/[CHAR] (o item_index do loot JÁ foi validado e corrigido em 20/07/2026 — ver seção de lootlog), render da arma no ranking (aguarda dump [CHAR n]), 1ª execução real do feed AODP, chave de assinatura do updater no ambiente de build (`TAURI_SIGNING_PRIVATE_KEY` — sem ela o build termina com erro DEPOIS de gerar os bundles, e artefato de update não sai), e o `event.listen not allowed. Plugin not found` no webview (toast de update/scanner-pause não funcionam; `core:event:default` explícito NÃO resolveu — hipótese: descompasso de versão `@tauri-apps/api` npm vs crate `tauri`; ver com `npm ls @tauri-apps/api` × `cargo tree -p tauri` — provavelmente presente em dev também, só nunca notado).

**Tela branca do release — RESOLVIDA (19/07/2026):** era bug de COMPOSIÇÃO do WebView2, não de código: o renderer tinha os pixels (screenshot via CDP mostrava o app perfeito por dentro) mas a janela não apresentava a superfície até um resize — o clássico "white until resize" com `visible: false` + `show()` tardio. Fix: `present_window()` no `lib.rs` (show + unminimize + focus + DOIS nudges de ±1px com 150ms/900ms de atraso — logo-após-show a superfície pode nem existir), usado nos 3 pontos que apresentam a janela (setup, tray "Abrir", single-instance). Confirmado pelo usuário: o empurrão manual via SetWindowPos renderizou a janela branca na hora, e o build com o fix abriu certo sozinho.
  - **Receita do diagnóstico (reusar se voltar):** a env var `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` é IGNORADA (o Tauri define args programaticamente) — o caminho é `additionalBrowserArgs: "--remote-debugging-port=9223"` TEMPORÁRIO no `tauri.conf.json` (o schema não aceita chave de comentário — lembrar de reverter!) + sonda CDP em node (WebSocket nativo, `Page.captureScreenshot`/`Runtime.evaluate`) pra ver a página POR DENTRO da janela. O teste barato de composição é resize de ±1px via SetWindowPos: se renderizar, é apresentação, não código. Single-instance NÃO atravessa elevação (4 processos simultâneos já observados) e processo elevado não morre de shell comum — kill+relaunch num só UAC via `Start-Process powershell -Verb RunAs`.

## Backend

**Stack:** FastAPI + SQLAlchemy (síncrono) + SQLite dev / Postgres prod + Alembic para migrações.

**Autenticação:** Discord OAuth → cookie de sessão assinado (`ziggs_session`). Todas as rotas de guilda exigem o cookie. `app/auth/session.py` gerencia. `app/api/deps.py` tem as dependências FastAPI (`current_user`, `require_guild`, etc.).

**Rotas principais:**
- `/auth/*` — login/logout Discord
- `/guilds/{guild_id}/comps/*` — composições
- `/guilds/{guild_id}/catalog/*` — catálogo de funções (GameRole) e armas (Weapon)
- `/guilds/{guild_id}/events/*` — eventos com máquina de estados
- `/guilds/{guild_id}/battles/*` — batalhas (tracker automático)
- `/guilds/{guild_id}/players/*` — tracker de jogadores
- `/guilds/{guild_id}/loot/*` — divisão de loot

**Modelos importantes:**
- `Comp → CompParty → CompSlot → CompSlotRole → GameRole` (comps.py)
- `GameRole` = função reutilizável: arma + build completa + feitiços + build_items JSON (catalog.py)
- `build_items: list[dict]` — cada dict: `{slot, item_id, name, quality, quantity}`. Slots válidos: `weapon, offhand, helmet, armor, boots, cape, food, potion`. **Slots alt:** `helmet_alt_0`, `helmet_alt_1`, etc.
- `gear_spells: str` — JSON serializado: `{"helmet_Q": "SPELL_ID", "helmet_alt_0_Q": "SPELL_ID", ...}`
- `Weapon` = catálogo global, tem `invisible_function` (tank/healer/support/dps/...) que dirige sugestões
- `WeaponSpell` indexada por `weapon_base_id` (sem prefixo T e sem @enchant)

**Tenancy:** cada guilda é isolada por `guild_id`. Não existe admin cross-guild no código normal.

**Background tasks:** `battle_tracker` e `player_tracker` rodam como tasks asyncio no lifespan do app, consultando a API pública do Albion.

## Frontend

**Stack:** React 18 + TypeScript + Vite. Sem framework de estado global (tudo useState/useEffect local). CSS puro em `src/styles.css` com variáveis CSS (`--border`, `--surface-2`, `--hint`, `--muted`, `--gold`, etc.).

**Arquivos principais:**
```
src/
├── App.tsx               Roteamento por view (batalhas/jogadores/craft/comps/eventos/config)
├── api.ts                Todas as chamadas ao backend (funções tipadas, cookie incluso)
├── styles.css            CSS global — TUDO aqui, sem módulos
├── mock.ts               Dados offline para demo sem backend
├── types.ts              Tipos legados (Role/Slot/Party/Comp) — pouco usado
├── i18n/index.ts         Strings PT/EN/ES + GameServer (europe/west/east) + itemLocalName
├── data/albion-items.ts  Lista completa de itens do Albion (ALBION_ITEMS[]) + helpers
└── components/
    ├── CompBuilder.tsx   ← O MAIOR E MAIS COMPLEXO — ver seção abaixo
    ├── ItemPicker.tsx    Dropdown de busca de itens por slot
    ├── EventsPage.tsx    Gerenciamento de eventos com máquina de estados
    ├── BattleTracker.tsx Listagem e detalhes de batalhas
    ├── PlayerLookup.tsx  Busca de jogadores
    ├── CraftCalculator.tsx Calculadora de craft
    ├── MarketPage.tsx    Mercado — ver nota abaixo
    ├── GuildPicker.tsx   Seleção/adição de servidor Discord
    └── GuildConfig.tsx   Configuração da guilda
```

**MarketPage.tsx (jul/2026):**
- Categorias são um `<select>` na searchbar (a fileira de chips foi removida). Cada linha mostra a categoria como badge CLICÁVEL que aplica o filtro — mesmo padrão da guilda na lista de jogadores. O badge é `<span role="button">` de propósito: a linha inteira já é `<button>` e button aninhado é HTML inválido.
- **Destaques (movers) têm DOIS pisos:** demanda (`MOVER_MIN_DEMAND`) **e giro em prata** (`MOVER_MIN_TURNOVER` = preço × vendidos 7d ≥ 1M). O segundo existe porque o primeiro não segura o caso clássico: T3 de 300 de prata com +2000% e 150 vendas passa na demanda, mas são 45k de giro — variação sem liquidez é ruído, não oportunidade. Knobs no topo do arquivo.

## CompBuilder.tsx — o componente central

É o maior arquivo (~1900 linhas). Entender ele é entender 80% do trabalho recente.

### Hierarquia de dados (Draft = estado local editável)
```
Draft { id, name, parties: DraftParty[] }
  DraftParty { name, slots: DraftSlot[] }
    DraftSlot { fn, roles: DraftRole[] }
      DraftRole {
        catalog_id,     ← null se role nova não salva ainda
        name, fn, color,
        weapon_db_id,
        equip: DraftEquip,
        equip_loaded,   ← false enquanto carrega do catálogo
        play_style, abilities, obs,
        q_spell, w_spell, passive_spell,
        gear_spells: Record<string, string|null>,
        potion_qty, food_qty,
        flex_of?,       ← se for role alternativa (flex), referencia o slot pai
      }
```

### DraftEquip — equipamento de uma role
```typescript
type DraftEquip = {
  weapon?, offhand?, helmet?, armor?, boots?, cape?, food?, potion?: EquipItem;
  // Itens alternativos (apenas offhand/helmet/armor/boots/cape suportam):
  offhand_alt?: EquipItem[];  // até 2 itens
  helmet_alt?: EquipItem[];
  armor_alt?: EquipItem[];
  boots_alt?: EquipItem[];
  cape_alt?: EquipItem[];
};
type EquipItem = { id: string; name: string };  // id = ID Albion (ex: "T5_HEAD_PLATE_SET1@2")
```

### Serialização (Draft ↔ API)
- `compToDraft(ApiComp)` — converte resposta do backend para Draft editável
- `roleToPayload(DraftRole)` — converte de volta para o formato da API ao salvar
- `buildItemsToEquip(RegearItem[])` — reconstrói DraftEquip a partir de `build_items[]`
  - Slots normais: `bi.slot === "helmet"` → `eq.helmet = {id, name}`
  - Slots alt: `bi.slot === "helmet_alt_0"` → `eq.helmet_alt[0] = {id, name}`
- `gear_spells` keys: `"helmet_Q"`, `"helmet_alt_0_Q"`, `"armor_W"`, etc.

### safeAltArr — SEMPRE use isso para ler arrays alt
```typescript
function safeAltArr(v: unknown): EquipItem[] {
  if (!v) return [];
  if (Array.isArray(v)) return v.filter(x => !!(x?.id));  // filtra { id: "" }
  if (typeof v === "object" && (v as EquipItem).id) return [v as EquipItem];
  return [];
}
```
**Atenção:** `safeAltArr` filtra items com `id === ""`. No formulário de edição, itens alt vazios recém-adicionados precisam ser lidos do array RAW (não via safeAltArr) para que o ItemPicker apareça antes de selecionar um item.

### Modo View — RoleViewBlock
Renderizado quando `!editing`. Componente `RoleViewBlock` dentro do painel direito (`.comp-right`, 320px largura).

Layout interno:
```
<flex-col gap-8>
  <flex-row gap-12 align-start>
    <flex-col width-212 shrink-0>   ← coluna esquerda fixa
      EquipGrid (3×3, 68px por célula)
      play_style section
      obs section
      "EQUIPAMENTOS ALTERNATIVOS" (título, só se totalAlts < 5)
    </flex-col>
    <flex-1 aspect-ratio-2/1>       ← coluna direita (gráfico)
      PriceHistoryChart
    </flex-1>
  </flex-row>
  AltEquipSection (full width, abaixo)
</flex-col>
```

### Sistema de Itens Alternativos (swap)
- `swapMap: Record<string, number>` — mapeia slot → índice do alt que está no lugar do principal
- `displayEquip` = cópia de `r.equip` com alts ativos substituindo o principal
- `altMap: Record<string, number>` — passado para EquipGrid para ler gear_spells corretos
- Clicar no alt em `AltEquipSection` → `handleSwap(slot, altIdx)` → toggle no swapMap
- Quando swapped: `displayEquip[slot] = alts[idx]` e `altMap[slot] = idx`
- `AltEquipSection` exibe o item principal na posição do alt quando swapped (troca visual)
- Título "EQUIPAMENTOS ALTERNATIVOS": inline na coluna esquerda se `totalAlts < 5`, acima da seção full-width se `>= 5`

### EquipGrid — grade de equipamentos
```typescript
function EquipGrid({ equip, weaponIs2H, weaponSpells, selectedQ, selectedW,
  selectedPassive, potionQty, foodQty, gearSpells, altMap, onFocus })
```
- Layout 3×3: `[[null, helmet, cape], [weapon, armor, offhand], [potion, boots, food]]`
- `null` cell → `rc-equip-empty` (invisível)
- `altMap?.[key]` → se definido, usa gear_spells de `${key}_alt_${altIdx}_*` em vez de `${key}_*`
- Células são clicáveis → `onFocus(item.id)` para destacar no gráfico

### PriceHistoryChart
- Busca histórico de preços em `albion-online-data.com` (API pública, sem auth)
- `slice(-28)` → sempre 28 datas, podem cobrir ~35-40 dias corridos (gaps de trading)
- Gráfico de linhas SVG normalizado (% desvio da média própria de cada item)
- `viewBox="0 0 320 160"`, `width="100%"`, `overflow: visible`
- Container tem `aspect-ratio: 2/1` → tamanho fixo independente do conteúdo
- **Skeleton de loading:** mesmo SVG sem linhas de dados (28 linhas verticais + marcadores 1w-4w)
- **Bug histórico resolvido:** `items.find(i => i.id === s.id)` pode retornar undefined se `items` mudou mas `data` (fetch anterior) ainda não atualizou — sempre null-guard

### ItemPicker
- Props: `slot, valueId, valueName, onChange, placeholder?, disabled?, excludeIds?`
- `excludeIds` faz exclusão por BASE (sem tier/enchant) — selecionar T4 Soldier Helmet exclui TODOS os tiers de Soldier Helmet
- Suporta filtro por tier: digitar "t8.3 capacete" para filtrar T8@3

## Dados de itens do Albion (albion-items.ts)

```typescript
interface AlbionItem {
  id: string;    // ex: "T5_HEAD_PLATE_SET1@2"
  name: string;  // nome PT
  nameEn?: string;
  slot: ItemSlot;
}
type ItemSlot = "weapon"|"offhand"|"helmet"|"armor"|"boots"|"cape"|"food"|"potion"
```

Funções úteis:
- `itemRenderUrl(item | id)` — URL da imagem do item no CDN do Albion
- `ITEM_BY_ID: Map<string, AlbionItem>` — lookup O(1) por ID
- `wBase(id)` em CompBuilder — extrai base sem tier/enchant para lookup de spells: `T5_HEAD_PLATE_SET1@2` → `HEAD_PLATE_SET1`
- `is2H(weaponId)` — verifica se arma é duas mãos (esconde slot offhand)

## Permissões

```typescript
type Permissions = {
  "comps.view": boolean;   // ver comps da guilda
  "comps.create": boolean; // criar nova comp
  "comps.manage": boolean; // editar/deletar comp, salvar roles no catálogo
  "events.view/create/manage": boolean;
  "guild.admin": boolean;
}
```

## CSS — convenções

Prefixos de classes:
- `rc-` — componentes React (equip grid, spells, alt section)
- `sd-` — slot detail panel (painel direito de visualização)
- `comp-` — layout da comp builder
- `btn` — botões genéricos
- `badge` — badges inline

Variáveis CSS importantes: `--border`, `--surface`, `--surface-2`, `--hint`, `--muted`, `--text`, `--gold`, `--gold-soft`, `--info`, `--info-soft`, `--green`.

## Padrões não-óbvios

**Modo offline/demo:** se `api.listComps()` falha, carrega `MOCK_API_COMP` e seta `offline: true`. Badge "demonstração" aparece no header.

**updRole vs updRoleQuiet:** `updRole` registra undo history, `updRoleQuiet` (para textarea onChange) não registra cada keystroke para não explodir o histórico.

**captureHistory / releaseFocus:** padrão de undo — captura snapshot ao focar um campo, descarta capturas intermediárias ao sair.

**Flex roles:** um slot pode ter múltiplas roles (roles alternativas). `role.flex_of` identifica que é alternativa. `editRi` é o índice da role sendo editada no slot atual.

**`equip_loaded: false`:** roles carregadas do catálogo chegam sem `build_items` detalhados. O frontend faz um `api.getRole(id)` separado e seta `equip_loaded: true` quando chega. Enquanto false, EquipGrid não renderiza.

**Preços:** `gear_spells` no backend é `Text` (JSON string). No frontend é `Record<string, string|null>`. A conversão acontece em `roleToPayload` e `compToDraft`.

**Albion item base ID:** `"T5_HEAD_PLATE_SET1@2"` → base = `"HEAD_PLATE_SET1"` (remove `T\d+_` do início e `@\d+` do fim). Usado para busca de spells de arma e exclusão tier-agnóstica no ItemPicker.
