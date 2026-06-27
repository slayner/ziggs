# Ziggs

Site + bot de controle de guildas de **Albion Online**, com versões grátis e
premium.

- Login **somente via Discord**.
- Backend **FastAPI** sobre **Postgres central** (multi-tenant por `guild_id`).
- Frontend **React/Next** (próxima etapa).

Veja [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) e
[`docs/STATE_MACHINE.md`](docs/STATE_MACHINE.md).

## Estado atual: fundação

Já existe a fundação (schema + máquina de estados):

- `backend/app/domain/` — estados, transições e guards do ciclo de vida do evento.
- `backend/app/models/` — schema central: tenancy, catálogo/funções, comps com
  slots flexíveis, eventos, passos de verificação, audit log append-only.
- `backend/app/auth/` + `app/api/routes/auth.py` — **login só por Discord** (OAuth2,
  sessão por cookie assinado).
- `backend/app/api/routes/comps.py` + `app/services/comps.py` — CRUD de comps com
  slots flexíveis + **sugestão de build** pela função invisível da arma.
- `backend/app/main.py` — API (`/health`, `/meta/event-states`, `/auth/*`, comps).

## Rodar tudo (dev) — 2 terminais

Dev usa **SQLite** por padrão (zero instalação); produção usa Postgres (troque a
`DATABASE_URL` no `.env`).

Terminal 1 — backend (porta 8000):
```bash
cd backend
python -m venv venv && venv/Scripts/activate     # Windows (1ª vez)
pip install -r requirements.txt                   # (1ª vez)
cp .env.example .env                              # (1ª vez) preencha o Discord OAuth
python -m scripts.init_db --seed                  # (1ª vez) cria o SQLite + dados de exemplo
uvicorn app.main:app --reload
```

Terminal 2 — frontend (porta 5173):
```bash
cd frontend
npm install                                       # (1ª vez)
npm run dev
```

Abra **http://localhost:5173**. O Vite faz proxy de `/auth`, `/guilds` e `/meta`
para o backend, então o site fala com a API sem configuração extra.

### Produção (Postgres)
```bash
# DATABASE_URL=postgresql+psycopg://... no .env, então:
alembic revision --autogenerate -m "schema inicial" && alembic upgrade head
```

### Testar o login Discord

No Discord Developer Portal, cadastre o redirect
`http://localhost:8000/auth/discord/callback`. Então abra
`http://localhost:8000/auth/discord/login` no navegador → autorize → o callback cria
a sessão e redireciona pro `FRONTEND_URL`. Confira o login em `GET /auth/me`.

## Testes

```bash
cd backend
PYTHONPATH=. python tests/test_suggestions.py
PYTHONPATH=. python tests/test_auth.py
```

## Próximos passos

Ver a seção final de [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
