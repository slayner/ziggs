# Sobre o Ziggs

Plataforma web completa para gerenciamento de guildas de **Albion Online**. Integra composições de batalha (comps), eventos com máquina de estados, tracking de batalhas e jogadores, calculadora de crafting e sistema de regear — tudo com login exclusivo via Discord.

## Visão Geral

O Ziggs surgiu da evolução de um bot Discord legado (Hideout) para uma plataforma web com versões **grátis** e **premium**. O objetivo é centralizar toda a operação de uma guilda de Albion num único lugar, com dados compartilhados entre o site e o bot Discord, ambos operando sobre o mesmo banco de dados.

### Funcionalidades

| Módulo | Descrição |
|---|---|
| **Comps** | Composições de batalha com slots flexíveis (N funções por slot), equipamentos alternativos, histórico de preços e sugestões automáticas de build pela função da arma |
| **Eventos** | Ciclo de vida completo com 8 estados explícitos, máquina de estados compartilhada entre site e bot, audit log append-only |
| **Batalhas** | Tracker automático via API pública do Albion, listagem e detalhes |
| **Jogadores** | Busca e tracking de jogadores da guilda |
| **Crafting** | Calculadora de crafting com dados de pesos, journals e nomes de itens |
| **Loot** | Divisão de loot entre os membros |
| **Regear** | Sistema de regear com preços históricos |
| **Configuração** | Gestão de guilda, catálogo de funções (GameRole) e armas (Weapon) |

## Stack Tecnológica

### Backend

- **Framework:** FastAPI (Python)
- **ORM:** SQLAlchemy 2.0 (síncrono)
- **Banco:** SQLite (dev) / PostgreSQL (prod)
- **Migrações:** Alembic
- **Auth:** Discord OAuth2 com cookie de sessão assinado (`ziggs_session`)

### Frontend

- **Framework:** React 18
- **Linguagem:** TypeScript
- **Build tool:** Vite 5
- **Estilo:** Tailwind CSS 4
- **Estado:** Local (useState/useEffect, sem Redux/Zustand)
- **Proxy:** Vite configura proxy automático para `/auth`, `/guilds` e `/meta` → backend na porta 8000

### Bot Discord

- **Bot legado:** `bot/` — discord.py (não mexer sem necessidade)
- **Bot novo:** `bot-v2/` — em desenvolvimento
- **Integração:** Compartilha o mesmo Postgres; usa a mesma máquina de estados do backend

## Estrutura do Projeto

```
ziggs/
├── backend/                API + lógica de negócio
│   ├── app/
│   │   ├── api/            Rotas FastAPI (routes/ e deps.py)
│   │   ├── auth/           Discord OAuth + sessão por cookie
│   │   ├── config.py       Settings via .env
│   │   ├── db.py           Engine + sessão SQLAlchemy
│   │   ├── domain/         Regras puras: estados + transições + guards
│   │   ├── main.py         FastAPI app (lifespan, rotas, CORS)
│   │   ├── models/         Schema SQLAlchemy (comps, eventos, catalog, etc.)
│   │   └── services/       Lógica de negócio (comps, eventos, etc.)
│   ├── alembic/            Migrações do banco
│   ├── data/               Dados de seed e referência
│   └── tests/              Testes automatizados
├── frontend/               SPA React
│   └── src/
│       ├── App.tsx         Roteamento por view
│       ├── api.ts          Chamadas ao backend (funções tipadas)
│       ├── styles.css      CSS global com variáveis
│       ├── mock.ts         Dados offline para demo
│       ├── i18n/           Internacionalização (PT/EN/ES)
│       ├── data/           Lista de itens do Albion + helpers
│       └── components/     Componentes React (CompBuilder, ItemPicker, etc.)
├── bot/                    Bot Discord legado (discord.py)
├── bot-v2/                 Bot Discord novo (em desenvolvimento)
├── docs/                   Documentação técnica
└── start-all.cmd           Inicia backend + frontend no Windows
```

## Autenticação e Multi-Tenancy

### Login

O login é **exclusivo via Discord OAuth2**. O fluxo é:

1. Usuário clica "Entrar com Discord" → redireciona para o Discord
2. Callback do Discord cria sessão assinada (`ziggs_session` cookie)
3. Toda rota de guilda exige esse cookie

A identidade global do usuário (`users`) é ligada a guildas via `guild_members`, que guarda tanto o vínculo com a guilda quanto o nick in-game.

### Multi-Tenancy

Cada guilda é isolada pelo campo `guild_id`, presente em quase toda tabela. Todas as queries da API filtram por `guild_id` da sessão do usuário. Não existe admin cross-guild no código normal.

## Arquitetura Interna do Backend

O backend segue uma separação em camadas:

```
domain/     → Regras puras (sem dependência de framework)
models/     → Schema SQLAlchemy (fonte da verdade do banco)
services/   → Lógica de negócio
api/        → Rotas HTTP + dependências FastAPI
```

O `domain/` não importa nada de web, podendo ser usado tanto pela API quanto pelo bot.

### Modelos Principais

- **Comp:** `Comp → CompParty → CompSlot → CompSlotRole → GameRole`
- **GameRole:** Função reutilizável com arma, build completa, feitiços e `build_items` (JSON)
- **Weapon:** Catálogo global de armas com `invisible_function` (tank/healer/support/dps) que dirige as sugestões de build
- **Events:** Máquina de estados com 8 estados, transições auditadas e guards

### Audit Log

Toda mudança sensível (vinda de site, bot ou sistema) é registrada em `audit_log` com `before`/`after` em JSONB. É append-only: nunca há UPDATE ou DELETE.

### Background Tasks

Duas tasks asyncio rodam no lifespan do app:

- **battle_tracker:** Consulta a API pública do Albion para buscar batalhas
- **player_tracker:** Consulta a API pública do Albion para buscar dados de jogadores

## Frontend — CompBuilder

O componente central do frontend é o **CompBuilder** (`src/components/CompBuilder.tsx`, ~1900 linhas). Ele é responsável por:

- Criar e editar composições de batalha (comps)
- Gerenciar slots flexíveis (N roles por slot)
- Renderizar equipamentos com grade 3x3 (EquipGrid)
- Exibir histórico de preços de itens (PriceHistoryChart via API pública)
- Gerenciar equipamentos alternativos (swap de itens)
- Busca de itens com ItemPicker (filtro por slot e tier)

### Hierarquia de Dados (Draft)

```
Draft
  └─ DraftParty[]
       └─ DraftSlot[]
            └─ DraftRole[]
                 ├─ equip: DraftEquip (weapon, helmet, armor, boots, etc.)
                 ├─ gear_spells: Record<string, string|null>
                 ├─ weapon_db_id
                 ├─ play_style, abilities, obs
                 └─ flex_of? (se for role alternativa)
```

### Outros Componentes

| Componente | Função |
|---|---|
| `ItemPicker.tsx` | Dropdown de busca de itens por slot, com filtro por tier |
| `EventsPage.tsx` | Gerenciamento de eventos com máquina de estados |
| `BattleTracker.tsx` | Listagem e detalhes de batalhas |
| `PlayerLookup.tsx` | Busca de jogadores |
| `CraftCalculator.tsx` | Calculadora de crafting |
| `GuildPicker.tsx` | Seleção e adição de servidor Discord |
| `GuildConfig.tsx` | Configuração da guilda |

## Como Rodar (Dev)

### Backend (porta 8000)

```bash
cd backend
python -m venv venv && venv/Scripts/activate     # Windows (1ª vez)
pip install -r requirements.txt                   # (1ª vez)
cp .env.example .env                              # (1ª vez) preencha o Discord OAuth
python -m scripts.init_db --seed                  # (1ª vez) cria o SQLite + dados de exemplo
uvicorn app.main:app --reload
```

### Frontend (porta 5173)

```bash
cd frontend
npm install                                       # (1ª vez)
npm run dev
```

Acesse **http://localhost:5173**. O Vite proxya automaticamente as rotas da API para o backend.

### Testes

```bash
cd backend
PYTHONPATH=. python tests/test_suggestions.py
PYTHONPATH=. python tests/test_auth.py
```

## Próximos Passos

1. Auth Discord + sessão completa
2. Migração inicial Alembic + dados de seed
3. CRUD de comps + sugestão de build
4. Rotas de evento + canal de comunicação site→bot
5. Economia (saldos, banco, payouts) e regears
6. Nodes com regra de premium, batalhas e perfis
7. `bot-v2/` — novo bot Discord integrado
