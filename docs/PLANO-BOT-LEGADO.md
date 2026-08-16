# Plano — Bot legado (Hideout) reduzido e re-hospedado

> Ago/2026. O bot antigo (`referencia/hideout`) volta a funcionar para a guild
> `1511238681829314630` (a mesma que usa a plataforma nova), com SÓ as features
> que não foram portadas pro bot-v2. Hospedado no mesmo VPS do backend, falando
> localhost com a API.

## Decisões do dono (registradas aqui)

1. **"CTA timer" = relógio UTC** (`cogs/clock.py`: categoria renomeada com hora
   UTC a cada 10min). NÃO é o sistema de CTA — `cta.py` (129KB) sai inteiro.
2. **`/register` usa o do bot novo** (backend Postgres). O registration do bot
   antigo sai; o energy control passa a casar nick→usuário via API do backend.
3. **Extras mantidos**: relógio UTC, temp voice, mentoria/trial (fórum).
4. Prioridades: **recrutamento, relógio UTC, energy control**.

## Escopo dos cogs

### Removidos — colidem com bot-v2/site
| Cog | Colide com |
|---|---|
| `cta.py` | eventos/máquina de estados do site + bot-v2 (`event_cmd`) |
| `economy.py` (comandos) | `bot-v2/cogs/economy.py` (balance/pay/bank/leaderboard) |
| `regears.py` | `bot-v2/cogs/regears*.py` |
| `nodes.py` | `bot-v2/cogs/nodes.py` |
| `lootlog.py` | `bot-v2/cogs/lootlogs*.py` |
| `battleboard.py` | `bot-v2/cogs/battle_feed.py` |
| `escalacao.py` | escalacao do site |
| `massinfo.py` | escalacao do site |
| `splits.py` | payout do site |
| `registration.py` | `bot-v2/cogs/registration.py` (decisão 2) |
| `perfil.py` | perfis do site (`/profile` no bot-v2) |
| `avatar.py` | `bot-v2/cogs/general.py` (avatar/banner) |
| `management.py` | `/attendance` `/lowattendance` no bot-v2 (members) |
| `tabsell.py` | não selecionado pelo dono |

### Mantidos
`clock.py`, `recruitment.py`, `energia.py` (adaptado), `tempvoice.py`,
`mentoria.py` (adaptado), `misc.py` (`/ativar` — gate de infraestrutura),
`help.py` (auto-adapta à lista de comandos registrados).

### Transformado: `economy.py` → `setup.py`
O economy.py carrega infra que os cogs mantidos precisam:
- `has_configured_role()` — checagem de permissão usada por recruitment/energia/mentoria
- `/setup` — ÚNICO lugar que configura canais/cargos: recrutamento
  (`channel_recruitment`, `recruiter_roles`, `role_member`, `role_trial` +
  publica o painel), energia (`channel_energyalerts`, `energy_alert_threshold`,
  `role_council`), relógio UTC (`publish_utc` do clock), tempvoice
  (`voice_temp_mother`), mentoria (canal do fórum), cargos base
  (`role_lead`/`role_logistic`)
- `load/update_economy_config` + `_post_economy_log` (audit)

**Sai**: balance/pay/addmoney/guildbank/leaderboard/economystats, seções de
sheets/nodes/CTA do /setup, integração Google Sheets.

### Removidos fora de cogs
- `sheets.py` + `sheets_appscript.gs` (só cta/massinfo/escalacao usavam)
- `albion.py` (código órfão — ninguém importa)
- `blackzone_maps.py` (só nodes.py usava)

### `database.py` — INTACTO (decisão deliberada)
186KB, 4319 linhas. As funções/tabelas órfãs (de cogs removidos) são
inofensivas: schema é `CREATE TABLE IF NOT EXISTS` idempotente, os bancos
existentes (`data/guild_*.db`) abrem igual, e podar 4 mil linhas à mão é risco
puro sem benefício. Arquivos mortos são problema de quem faz code review, não
de runtime. Reavaliar só se um dia o bot legado for congelado de vez.

## Adaptações (o único código novo do bot)

### 1. Cliente HTTP mínimo (~60 linhas)
Padrão do `bot-v2/http_client.py` reduzido: sessão aiohttp singleton
(keep-alive), `Bearer BOT_API_SECRET`, `BOT_SITE_URL` (prod:
`http://127.0.0.1:8000`), timeout 5s. **Sem** offline queue/reachability — o
único caller é o energylog, operação manual cujo erro é visível na hora.

### 2. `energia.py`: nick→usuário via backend
Hoje: `get_registration_by_nick(player)` (SQLite local). Passa a ser
`GET /bot/registration-lookup/{guild_id}?nick=X` com cache em memória
(TTL 5min — um log colado tem dezenas de nicks repetidos). Sem registro:
mantém o comportamento atual (lançamento sem uid).

### 3. `mentoria.py`: duas amputações
- `get_registration(member.id)` → `GET /bot/registration-lookup/{guild_id}?user_id=Y`
- `forfeit_balance_to_bank()` → **removido** (economy interno não existe mais).
  Após 7 dias o post arquiva sem confiscar saldo. Mudança de comportamento —
  comunicar à guild.

### 4. Backend: rota nova
`backend/app/api/routes/auth.py`: `GET /bot/registration-lookup/{guild_id}`
(query `?nick=` ou `?user_id=`), protegida por `_require_bot_secret`, lê
`BotRegistration`. Case-insensitive no nick (o jogo não distingue).

## Deploy

- Repo: pasta nova `bot-legacy/` (cópia cirúrgica de `referencia/hideout`).
  `referencia/` volta a ser só referência intocada.
- VPS: `/home/ziggs/ziggs/bot-legacy`, venv próprio
  (`discord.py==2.7.1`, `python-dotenv`, `aiosqlite`), unit systemd
  `ziggs-bot-legacy.service` (`After=ziggs-backend.service`, `Restart=always`).
- `.env`: `DISCORD_TOKEN` (token do bot antigo), `OWNER_ID`, `BOT_DB_DIR=data`,
  `BOT_SITE_URL=http://127.0.0.1:8000`, `BOT_API_SECRET`.
- Dados: subir `data/guild_1511238681829314630.db` + `registry.db` do snapshot
  local. **⚠️ Confirmar com o dono se o snapshot é o mais recente** (o bot
  rodava na Discloud — se lá houver banco mais novo, baixar antes).
- **Decomissar a instância Discloud ANTES de ligar** — mesmo token = gateway
  duplicado = dois bots respondendo tudo 2×.

## Ordem de execução

| Fase | O quê | Verificação |
|---|---|---|
| F1 ✅ | Rota `/bot/registration-lookup` no backend + teste | curl com secret, por nick e por user_id — deployado 15/ago, verificado com nick real (case-insensitive ok), user_id ok, 400 sem param, 422 sem secret |
| F2 ✅ | `bot-legacy/`: cópia, remoção de cogs/arquivos, `setup.py`, adaptações energia/mentoria, http client | load test local: 8 cogs, 11 slash (ativar, disarray, energy, energylog, energywl, help, setenergy, setup, stoputc, teamspeak, trial); /help itera comandos vivos por design; smoke end-to-end via túnel SSH contra prod: nick→uid, uid→nick, guards DM/inexistente, cache |
| F3 ✅ | Dry-run local contra backend prod (túnel SSH): load, sync global limpando fantasmas da Discloud (/register, /cta…), testes do dono. Extra: `/trial` REMOVIDO por decisão do dono (atribuição de cargo agora é externa; on_member_update segue reagindo) | 10 comandos globais server-side via REST; mentoria/clock/energia ok |
| F4 ✅ | Deploy VPS: `/home/ziggs/ziggs/bot-legacy` + venv py3.12 + unit `ziggs-bot-legacy` (enabled, `After=ziggs-backend`, `PYTHONUNBUFFERED=1` — sem isso o journal fica mudo: stdout bufferiza em pipe) | `active`, conectado como SIGHT#9397, 10 comandos sincronizados, lookup ok a partir do VPS |
| F5 | Verificação em produção com a guild | ticket real, relógio renomeando, energylog real |

## Riscos / pendências

- Snapshot de dados stale (Discloud) — resolver em F4, antes do primeiro boot.
- Token do bot antigo: existe no `.env` local ✓ — validar no portal se a app
  ainda existe e os intents (members, message_content) seguem ligados.
- Energy depende do backend no ar. Se cair: logs entram sem uid (não perdem),
  atribuição volta quando o backend volta. Aceitável.
- Mentoria sem confiscate de saldo: comunicar à guild antes de ligar.
