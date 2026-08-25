# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## O que é

Monorepo da **Ziggs** — plataforma de gestão de guildas de **Albion Online** (site + bot de Discord + app desktop), com versões grátis e premium. Login **somente via Discord OAuth**. **Multi-tenant por `guild_id`** (snowflake do Discord): um banco Postgres central, quase toda tabela carrega `guild_id`.

## Componentes

| Diretório | O que é |
|---|---|
| `backend/` | FastAPI + SQLAlchemy 2 + Postgres central. **Fonte de verdade** de tudo. |
| `frontend/` | React 18 + TS + Vite 5 + Tailwind 4. Sem framework de testes. |
| `bot-v2/` | Bot Discord **ativo em produção** (serviço `ziggs-bot`). Stateless: proxy HTTP do backend, **nunca toca no banco**. |
| `companion/` | App desktop Tauri 2 (scanner distribuído do feed Albion, túnel WireGuard split-tunnel, damage meter via packet capture, lootlog). |
| `bot/` | Bot **legado** (SQLite por servidor). Tem o próprio `bot/CLAUDE.md`, que descreve a arquitetura antiga — **não vale para o bot-v2**. |
| `bot-legacy/` | Versão ainda mais antiga/reduzida. Não alterar. |
| `deploy/` | Provisionamento (`server-setup.sh`), hotfix (`publish-backend.ps1` / `publish-bot.ps1`) e `PRODUCAO.md` (SOP operacional da VPS). |
| `router/` | Proxy local de modelos do próprio Claude Code (tiers + failover de quota). Tooling, fora do deploy. |

## Idioma

**PT-BR** em todo texto de usuário, comentário, docstring e `detail` de erro HTTP. i18n pt/en/es no frontend e no bot-v2 (pt é default).

## Roteamento de modelos (tiers)

Este projeto roda num **router local** (`router/`, porta 8787) que fica entre o
Claude Code e os provedores (z.ai + 2 chaves Ollama cloud) — veja
`router/README.md`. **Sem o router no ar, o Claude Code não conecta neste
projeto** (`router\start.cmd` sobe).

Ao despachar subagentes, escolha a tier pela dificuldade (agents
`.claude/agents/tier-*.md`; o alias `model` também funciona via `/model`):

- **easy** (`tier-easy`) — mecânico: buscas, listagens, renames, rodar comando
  e reportar output, edições já especificadas.
- **medium** (`tier-medium`) — escopo claro: feature definida, testes, portar
  padrão existente, ajuste em um módulo. Default para código do dia a dia.
- **hard** (`tier-hard`) — debugging cross-file, design de API, refactor
  multi-arquivo, migrations, serviços de background.
- **ultrahard** (`tier-ultrahard`) — arquitetura, bugs que sobreviveram a
  tentativas, race conditions, revisão adversarial de código crítico. Use com
  parcimônia (quota cara).
- **vision** (`tier-vision`) — análise de imagens/screenshots. O router TAMBÉM
  força essa tier sozinho quando o request contém imagem.

Regras de economia: em dúvida entre duas tiers, prefira a menor; tarefas
independentes vão em paralelo; antes de fan-out grande, cheque a quota com
`router.cmd status`. Failover/degradação de quota é automático e transparente
(429 semanal → outra chave → tier inferior). Modelos/provedores se editam em
`router/config.yaml` (validar com `router.cmd check --live`); o loop principal
deste projeto roda em `claude-hard`.

## Comandos

Não há venv commitado — crie um por componente Python (`python -m venv venv`) e sempre use o Python da venv.

### backend

```bash
cd backend
python -m venv venv && venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env        # exige Postgres local (SQLite foi removido; README raiz está defasado nisso)
venv/Scripts/python -m scripts.init_db --seed      # bootstrap dev (create_all); produção usa alembic
venv/Scripts/python -m uvicorn app.main:app --reload
```

- Rodar leve: `DISABLE_BACKGROUND_FETCHERS=true` no `.env` desliga as ~30 tasks de background do lifespan (útil em rede limitada). `DISABLE_FEED_FETCHERS` desliga só os pollers do feed Albion.
- Migrations (da pasta `backend/`): `alembic revision --autogenerate -m "..."` + `alembic upgrade head`. O `alembic/env.py` injeta a URL do `.env`; **models novos precisam estar importados em `app/models/__init__.py`** ou o autogenerate não os vê.
- Testes (`tests/`, sem conftest — cada arquivo roda com pytest **ou** direto via `if __name__ == "__main__"`; usam SQLite em memória com shims de JSONB/BigInteger):
  ```bash
  PYTHONPATH=. venv/Scripts/python -m pytest tests -q                                  # todos
  PYTHONPATH=. venv/Scripts/python -m pytest tests/test_auth.py -q                     # um arquivo
  PYTHONPATH=. venv/Scripts/python -m pytest tests/test_auth.py::test_sessao_round_trip -q  # um teste
  ```
  Exceção: `test_prices_ingest_pg.py` exige Postgres real (`ZIGGS_TEST_DATABASE_URL`) e roda direto, sem pytest.
- `run-api.cmd` / `run-dashboard.cmd` referenciam um layout de venv que não existe mais — prefira invocar uvicorn/`scripts/companion_dashboard.py` direto da venv.

### frontend

```bash
cd frontend && npm install && npm run dev    # 5173; proxy /auth /guilds /meta /players /render
                                             # /claims /profile /craft /market-history /companion
                                             # /scan /health → 127.0.0.1:8000
```

- `npm run build` = `tsc -b && vite build` (TS `strict`). `emptyOutDir: false` no Vite é **de propósito** (chunks hasheados sobrevivem a deploys com abas abertas). Sem lint e sem testes.
- `docs.html` é um segundo entry (documentação de comandos do bot, validada por `backend/scripts/check_docs_commands.py`).

### bot-v2

```bash
cd bot-v2
cp .env.example .env    # DISCORD_TOKEN, BOT_SITE_URL=http://localhost:8000, BOT_PUBLIC_URL,
                         # BOT_API_SECRET (mesmo valor do backend/.env)
python main.py           # bloqueia até o backend responder /health
```

- Testes: cada `test_*.py` roda como script puro (`python test_i18n.py`); alguns também são coletáveis por pytest (`python -m pytest test_juicy_kills.py`). **Rode sempre de dentro de `bot-v2/`** (test_i18n usa caminhos relativos). `python http_client.py` faz self-check do cliente.
- Cogs leem `BOT_SITE_URL`/`BOT_API_SECRET` no import — o `.env` precisa existir antes de carregar cogs.

### companion

```bash
cd companion && npm install && npm run tauri dev    # frontend na porta estrita 1420
```

- Release: `scripts/release.ps1` (bump de versão em `tauri.conf.json` → build+sign → `publish.ps1`: gh release + scp pra VPS + manifesto do updater `latest.json` + restart do backend). A versão autoritativa é a do `tauri.conf.json`, não a do package.json.

## Arquitetura — o grande mapa

### Multi-tenancy e auth (backend)

- `app/models/tenancy.py`: `Guild` = tenant; `User` = identidade Discord global; `GuildMember` = vínculo por guilda (com `discord_role_ids`, `is_guild_admin`). PKs são snowflakes (`BigInteger`); **no frontend, guild IDs são strings** (precisão > 2^53).
- Escada de dependências em `app/api/deps.py`: `optional_user` → `require_user` (401) → `tenant_guild` (404) → `require_guild_member` (403) → `require_active_guild_member` → `require_permission(key)` (`app/auth/permissions.py`; admin de guilda faz bypass).
- Sessão stateless: cookie assinado (`itsdangerous`, 7 dias) com só o `uid` (`app/auth/session.py`).
- Auth de máquina, sem cookie: `/bot/*` usa `Bearer BOT_API_SECRET`; `/companion/*` usa bearer assinado (30 dias, via `/companion/auth/start|poll`) ou é anônimo; `/scan/*` usa header `X-Scan-Secret` (workers VPS).
- Famílias de rota: tenant (`/guilds/{guild_id}/...`), globais (`/battles`, `/players`, `/highscores`, `/craft`...), públicas (`/public/...`), máquina (`/bot/`, `/companion/`, `/scan/`).

### bot-v2 — modelo de polling (o site não empurra pro bot)

- **Zero estado persistente local.** IDs de mensagens/threads/cursors vivem no backend; o bot baixa trabalho e acka via endpoints `*-synced`.
- `on_ready`: espera `/health` → `_catch_up()` re-sincroniza embeds/threads/calendários por guilda.
- Loops: heartbeat (5 min, `POST /bot/heartbeat/{gid}`); `pending-work` (5 s, `GET /bot/events/{gid}/pending-work` — massinfo sync, DMs de função); `offline_queue` (3 s — refila só falhas de **conexão**, nunca timeout, para evitar duplo apply; perdida por design no restart).
- `cogs/general.py` centraliza a config por guilda (cache 60 s de `/bot/guild-commands/{id}`): idioma da guilda, comandos desabilitados, cargos por comando, canais. Os demais cogs usam `check_command_access` dali.
- i18n **em código** (`i18n.py`): `T` = textos de resposta no idioma **da guilda**; `CMD_I18N` = nomes/descrições de comando no locale **do membro** (`localization.py`, `ZiggsTranslator`).
- `ephemeral_guard.py` monkeypatcha o discord.py para autodestruir efêmeros após 60 s; `error_handler.py` cobre tree, views e modais. `cogs/_discord_timeout.py` (`dtimeout()`) evita loops presos em chamadas do Discord.

### Backend — serviços de background e render

- O lifespan sobe ~30 tasks asyncio (trackers de players/batalhas/preços, warmers, checkers, caches pré-computados) — cada módulo de `app/services/` expõe `run_forever()`.
- `services/scan_dispatcher.py`: varredura distribuída do feed Albion em 3 regiões — o backend é dono dos cursors; companion clients e workers VPS reclamam tarefas atômicas e reportam payloads (`/companion/scan/*`, `/scan/*`). O backend revalida tudo contra a API pública.
- `routes/render.py`: cache em disco de ícones da CDN (`data/render_cache/`, semáforo 8) + loop de prerender. `_spa.install(app)` (catch-all SPA/OG-tags) **deve ser o último registro** e só ativa se `frontend/dist/index.html` existe.
- Disciplina async: rota `async def` → `async_db_session`; rota `def` sync → `db_session` (threadpool); trabalho sync pesado → `asyncio.to_thread`.
- Rate limit em memória (`app/api/rate_limit.py`) em 3 camadas; `/bot/`, `/render/item`, `/companion/`, `/scan/` são isentos.

### Frontend

- `App.tsx` (~900 linhas) é o orquestrador único (estado de view/auth/guilda); `api.ts` é o cliente HTTP único (`req()` com cookie de sessão; guilda ativa em `api.setGuild(id)`/`g()`).
- Roteamento custom em `router.ts` (history API, sem react-router) com parsers regex para batalha (`/{code}`), jogador (`/{am|as|eu}/{name}`), escalacao etc. Trocar por react-router se crescer navegação aninhada.
- i18n custom (`src/i18n/index.ts`, `S = {pt, en, es}` + `useT()`), persistido em localStorage junto com a seleção de servidores de jogo.

## Produção (VPS Hetzner)

- **`deploy/PRODUCAO.md` é o SOP operacional** — leia antes de acessar/publicar na VPS.
- Hotfix de arquivo já validado (sem commit/pull): `powershell -ExecutionPolicy Bypass -File deploy/publish-backend.ps1 [-Migrate] app/...` e `deploy/publish-bot.ps1 cogs/...`. `-Migrate` roda Alembic antes do restart.
- Ordem de restart obrigatória: `ziggs-backend` → aguardar `/health` ok → `ziggs-bot` (o bot depende da API pra iniciar).
- Frontend é servido pelo Caddy direto do `dist/` — build + cópia, **sem restart de nada**. Não reinicie o backend por mudança só de frontend, nem o bot por mudança só de backend.
- Scripts one-off de diagnóstico em `backend/scripts/` (`check_*`, `explain_*`, `fix_*`, etc.) são **gitignored de propósito** (têm credenciais hardcoded) — nunca commitar.

## Armadilhas conhecidas

- **README raiz defasado**: diz que dev usa SQLite e linka `docs/` que não existe. O backend é Postgres-only (ver `app/config.py`).
- `bot/CLAUDE.md` descreve o bot **antigo** (SQLite por servidor, sheets, ContextVar). Não aplique essas convenções ao bot-v2.
- `backend/reset_bot_data.py` é relicto da era SQLite.
- Windows: prefixe scripts soltos com `PYTHONIOENCODING=utf-8` (cp1252 não aguenta ✓/emojis).
- `backend/data/` é runtime (caches de render, `ao-bin-dump/` de 27 MB, catálogos gerados pelos `seed_*`). Nunca commitar `*.db`.
- `deploy/server-setup.sh` tem `ExecStart` desatualizado (`-m bot` não existe; entrypoint real é `python main.py`) — o unit na VPS foi corrigido à mão.
