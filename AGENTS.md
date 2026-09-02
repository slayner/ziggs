# AGENTS.md

Monorepo **Ziggs** — gestão de guildas de Albion Online (site + bot Discord + app desktop). Login só via Discord OAuth. Multi-tenant por `guild_id` (snowflake Discord); um Postgres central, quase toda tabela carrega `guild_id`. **PT-BR** em todo texto de usuário, comentário, docstring e `detail` de erro HTTP.

## Layout

| Dir | O que é |
|---|---|
| `backend/` | FastAPI + SQLAlchemy 2 + Postgres. Fonte de verdade. |
| `frontend/` | React 18 + TS + Vite 5 + Tailwind 4. Sem lint, sem testes. |
| `bot-v2/` | Bot Discord ativo em produção. Stateless: proxy HTTP do backend, nunca toca no banco. |
| `companion/` | App desktop Tauri 2 (scanner distribuído do feed, WireGuard split-tunnel, damage meter, lootlog). |
| `bot/`, `bot-legacy/` | Bots legados (SQLite). Não alterar. `bot/CLAUDE.md` descreve arquitetura antiga — não vale para bot-v2. |
| `deploy/` | Provisionamento, hotfix, `PRODUCAO.md` (SOP da VPS). |
| `router/` | Proxy local de modelos LLM (porta 8787). Tooling, fora do deploy. |

`CLAUDE.md` na raiz tem contexto arquitetural mais profundo (mapa de auth, modelo de polling do bot, serviços de background, frontend). Este `AGENTS.md` foca em comandos e armadilhas.

## Dev environment

Não há venv commitado — crie um por componente Python e sempre use o Python da venv.

```bash
# backend (exige Postgres local — SQLite foi removido)
cd backend
python -m venv venv && venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env        # DATABASE_URL, DISCORD_CLIENT_*, SECRET_KEY, BOT_API_SECRET, DISCORD_BOT_TOKEN
venv/Scripts/python -m scripts.init_db --seed          # bootstrap dev (create_all); produção usa alembic
venv/Scripts/python -m uvicorn app.main:app --reload   # :8000

# frontend
cd frontend && npm install && npm run dev              # :5173; proxy /auth /guilds /meta /players /render
                                                         # /claims /profile /craft /market-history /companion
                                                         # /scan /health -> 127.0.0.1:8000

# bot-v2
cd bot-v2
cp .env.example .env    # DISCORD_TOKEN, BOT_SITE_URL=http://localhost:8000, BOT_PUBLIC_URL,
                          # BOT_API_SECRET (mesmo valor do backend/.env)
python main.py           # bloqueia até o backend responder /health

# companion
cd companion && npm install && npm run tauri dev    # frontend na porta estrita 1420
```

Kill-switches de background (em `backend/.env` ou env real):
- `DISABLE_BACKGROUND_FETCHERS=true` — desliga as ~30 tasks de background do lifespan (setting do pydantic, lê do `.env`).
- `DISABLE_FEED_FETCHERS=true` — desliga só os pollers do feed Albion. **Lido via `os.getenv`**, não via pydantic-settings: precisa estar no ambiente real (export), não basta estar no `.env`.
- `DISABLE_DISTRIBUTED_SCAN=true` — desliga o scan distribuído (companion/VPS) sem interromper trackers nativos.
- `DISABLE_BACKGROUND_MAINTENANCE=true` — suspende agregações pesadas sem interromper o polling recente.

## Build & test

```bash
# backend — migrations (sempre da pasta backend/)
cd backend && alembic revision --autogenerate -m "..." && alembic upgrade head

# backend — testes (SQLite em memória com shims de JSONB/BigInteger; sem conftest)
PYTHONPATH=. venv/Scripts/python -m pytest tests -q
PYTHONPATH=. venv/Scripts/python -m pytest tests/test_auth.py -q
PYTHONPATH=. venv/Scripts/python -m pytest tests/test_auth.py::test_sessao_round_trip -q
# test_prices_ingest_pg.py exige Postgres real (ZIGGS_TEST_DATABASE_URL), roda direto sem pytest

# frontend
cd frontend && npm run build      # = tsc -b && vite build (TS strict); sem testes

# bot-v2 — testes rodam como script puro; sempre de dentro de bot-v2/
cd bot-v2 && python test_i18n.py
cd bot-v2 && python -m pytest test_juicy_kills.py    # alguns são coletáveis por pytest
cd bot-v2 && python http_client.py                   # self-check do cliente HTTP
```

## Conventions

- **Idioma**: PT-BR em todo texto de usuário, comentário, docstring, `detail` de erro HTTP. i18n pt/en/es no frontend e bot-v2 (pt é default).
- **bot-v2 é stateless**: IDs de mensagens/threads/cursors vivem no backend; o bot baixa trabalho e acka via endpoints `*-synced`. Nunca adicionar estado persistente local ao bot.
- **Multi-tenancy**: PKs são snowflakes (`BigInteger`); no frontend, guild IDs são **strings** (precisão > 2^53).
- **Async**: rota `async def` → `async_db_session`; rota `def` sync → `db_session` (threadpool); trabalho sync pesado → `asyncio.to_thread`.
- **Auth**: `/bot/*` usa `Bearer BOT_API_SECRET`; `/companion/*` usa bearer assinado (30 dias) ou anônimo; `/scan/*` usa header `X-Scan-Secret`. Sessão do site é cookie assinado (`itsdangerous`, 7 dias) com só o `uid`.
- **Escada de deps** (`app/api/deps.py`): `optional_user` → `require_user` (401) → `tenant_guild` (404) → `require_guild_member` (403) → `require_active_guild_member` → `require_permission(key)` (admin de guilda faz bypass).
- **Models novos**: precisam ser importados em `app/models/__init__.py` ou o `alembic revision --autogenerate` não os vê.
- **`_spa.install(app)`** (catch-all SPA/OG-tags) deve ser o **último** registro de rota no backend e só ativa se `frontend/dist/index.html` existe.
- **`docs.html`** é um segundo entry do Vite (documentação de comandos do bot, validada por `backend/scripts/check_docs_commands.py`). Em dev, acessível direto; em produção, o catch-all entrega `docs.html` quando `DOCS_HOST` bate.
- **Companion**: versão autoritativa é a do `tauri.conf.json`, não a do `package.json`. Release: `companion/scripts/release.ps1`.
- **Commit messages**: prefixo convencional lowercase (`fix:`, `feat:`, `companion:`) — observado no histórico.
- **Rate limit**: `/bot/`, `/render/item`, `/companion/`, `/scan/` são isentos.
- **Tesseract**: backend depende de `pytesseract` (OCR de screenshots de regear). Sem o binário instalado no host, o fluxo degrada pra manual.

## Pitfalls

- **README raiz defasado**: diz que dev usa SQLite e linka `docs/` que não existe. Backend é Postgres-only (`app/config.py`).
- **`run-api.cmd` / `run-dashboard.cmd`** referenciam layout de venv que não existe mais — invoque uvicorn / `scripts/companion_dashboard.py` direto da venv.
- **`backend/reset_bot_data.py`** é relicto da era SQLite — ignorar.
- **Windows**: prefixe scripts soltos com `PYTHONIOENCODING=utf-8` (cp1252 não aguenta ✓/emojis).
- **`backend/data/`** é runtime (render cache, `ao-bin-dump` 27 MB, catálogos dos `seed_*`). Nunca commitar `*.db`.
- **Scripts one-off** em `backend/scripts/` (`check_*`, `explain_*`, `fix_*`) são **gitignored de propósito** (credenciais hardcoded) — nunca commitar.
- **`deploy/server-setup.sh`** tem `ExecStart` desatualizado; unit na VPS foi corrigido à mão.
- **Ordem de restart em produção**: `ziggs-backend` → aguardar `/health` ok → `ziggs-bot`. Ver `deploy/PRODUCAO.md`.
- **Frontend em produção**: servido pelo Caddy direto do `dist/` — build + cópia, sem restart. Não reinicie o backend por mudança só de frontend, nem o bot por mudança só de backend.
- **bot-v2 cogs** leem `BOT_SITE_URL`/`BOT_API_SECRET` no import — o `.env` precisa existir antes de carregar cogs.
- **`emptyOutDir: false`** no Vite é de propósito (chunks hasheados sobrevivem a deploys com abas abertas).
- **Hotfix sem commit/pull**: `deploy/publish-backend.ps1 [-Migrate] app/...` e `deploy/publish-bot.ps1 cogs/...` (scp direto pra VPS + restart). `-Migrate` roda Alembic antes do restart.