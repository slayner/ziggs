# Arquitetura — Ziggs

Produto web + bot construído a partir do bot Hideout (em `../hideout`, usado só
como referência — **não editar**). Transforma o que o bot faz num produto com
versões **grátis** e **premium**.

## Decisões de fundação (travadas)

| Decisão | Escolha | Por quê |
|---|---|---|
| Banco | **Postgres central**, multi-tenant por `guild_id` | O bot usava 1 SQLite por guild. O produto precisa de login/billing e features **cross-guild** (export de nodes, preços de itens, batalhas, perfis) que exigem dados compartilhados. |
| Backend | **FastAPI (Python)** | Reaproveita o domínio que já existe em Python no bot; mesmo time de linguagem que o bot. |
| Frontend | **React/Next** (próxima etapa) | SPA separada consumindo a API. |
| Login | **Somente Discord OAuth** | Identidade já é o Discord; guildas são servidores. |

## Multi-tenant

No bot, o tenant era resolvido por `ContextVar` + arquivo `guild_<id>.db`. Aqui o
tenant é a coluna **`guild_id`** presente em quase toda tabela (`guilds` é o
tenant). Toda query da API filtra por `guild_id` do contexto da sessão.

`users` é a identidade **global** do Discord; `guild_members` liga usuário↔guilda e
guarda o **link com o nick in-game** separado da posse de cargo (`linked` vs
`has_roles`) — assim, quando alguém sai da guilda do jogo, o bot tira os cargos mas
mantém o vínculo, como a visão pede.

## Como o bot e o site convivem

Os dois compartilham o **mesmo Postgres**. A máquina de estados
(`app/domain/state_machine.py`) é a fonte única da verdade: site e bot só mudam
estado de evento por ela, e toda mudança grava em `event_state_transitions` + no
`audit_log`.

Ações que precisam acontecer **no Discord** (criar a role do evento, abrir/trancar
threads de lootlog/split/regear, pingar a role) são responsabilidade do bot. O
canal de comando site→bot (fila/Redis/endpoint interno) será definido quando as
rotas de evento entrarem — a fundação não depende disso.

## Camadas

```
backend/app/
  domain/        # regras puras (sem framework): estados + transições + guards
  models/        # schema central (SQLAlchemy 2.0) — fonte da verdade do banco
  config.py      # settings via .env
  db.py          # engine + sessão
  main.py        # FastAPI (esqueleto)
```

`domain/` não importa nada de web; pode ser usado tanto pela API quanto pelo bot.

## Audit log (append-only)

`audit_log` registra **toda** mudança sensível (de site, bot ou sistema), com
`before`/`after` em JSONB. Nunca há UPDATE/DELETE nele. Unifica o que no bot estava
espalhado em `economy_logs` (Discord) e `role_change_log`. O Discord vira só uma
**visão** desse log.

## Free vs Premium

`guilds.premium_tier` + `premium_until`. A primeira regra dependente de tier é o
**export de nodes** (guilda premium nunca exporta seus nodes; grátis exporta sob
verificação de integridade). Entra junto com o módulo de nodes.

## O que mudou de propósito vs. o bot

- **Comps saem da planilha** para o banco (`comps`/`comp_parties`/`comp_slots`/
  `comp_slot_roles`). Slots **flexíveis**: 1 slot aceita **N funções**. A planilha
  vira espelho de leitura.
- **Eventos** ganham estado explícito (8 estados) e tipo definido só na fase de
  definição.
- **Sem kills/mortes/MVP** no fluxo de evento (a visão remove o tracking); mantém-se
  percentuais e a parte econômica.

## Próximas etapas (não nesta fundação)

1. Auth Discord + sessão.
2. Migração inicial Alembic (`alembic revision --autogenerate`) + dados de seed.
3. CRUD de comps + sugestão de build pela função invisível da arma.
4. Rotas de evento por fase + canal de comando site→bot.
5. Economia (saldos, banco da guilda negativo, payouts) e regears com DB de preços.
6. Nodes com regra de premium; batalhas; perfis.
