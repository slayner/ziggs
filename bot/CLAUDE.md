# CLAUDE.md

Guia de contexto pro Claude Code. Leia antes de mexer no projeto.

## O que é

Bot de Discord (Python / `discord.py`) para **guilda de Albion Online**. Faz gestão
de membros, eventos de CTA (Call-to-Arms), economia em prata (silver), loot/regear,
nodes/territórios, energia, mentoria e mais. Tudo em **português**.

**Arquitetura central: multi-tenant — um banco SQLite POR servidor (guild).**

## Como rodar / testar (Windows)

- **Executar o bot:** `venv\Scripts\python.exe -u main.py` (ou a config "Discord Bot" do `.claude/launch.json`).
- **Sempre use o Python da venv** (`venv/Scripts/python.exe`), nunca o global — as deps (`discord.py 2.7.1`, `aiosqlite`, `python-dotenv`) estão na venv.
- **Não há framework de testes.** Verificação é feita com:
  - Compilação: `venv/Scripts/python.exe -m py_compile <arquivos>` (ou `-m compileall -q cogs database.py main.py sheets.py utils.py blackzone_maps.py`).
  - Smoke tests ad-hoc: scripts `_tmp_*.py` / `_t_*.py` na raiz, rodados com `PYTHONIOENCODING=utf-8 venv/Scripts/python.exe _tmp_x.py`, usando `BOT_DB_DIR` apontando pra um diretório temporário pra não tocar nos bancos reais. **Apague esses scripts ao terminar.**
- **Saída UTF-8:** o `main.py` faz `sys.stdout.reconfigure(encoding='utf-8')`. Em scripts soltos no Windows, prefixe com `PYTHONIOENCODING=utf-8` (o cp1252 não aguenta ✓ ✗ → emojis).

## Variáveis de ambiente (`.env`, fora do git)

- `DISCORD_TOKEN` — token do bot. **Nunca** logar/commitar.
- `OWNER_ID` — ID do dono (acesso a `/setup`, `/ativar`, etc.).
- `BOT_DB_DIR` — sobrescreve o diretório dos bancos (usado por testes). Default: `../data` relativo ao módulo.
- `BOT_DB_POOL_SIZE` — teto de conexões por banco (default 4).

## Layout

```
main.py            # entrada; monkeypatch do guild-context + carrega todos os cogs
database.py        # ~3500 linhas: schema + TODA a camada de dados (multi-tenant)
sheets.py          # ponte com Google Sheets via Apps Script web app (POR servidor)
sheets_appscript.gs# código Apps Script que vive na planilha de cada servidor
utils.py           # parse/format de silver + helpers de embed (tema do bot)
blackzone_maps.py  # dados de mapas da blackzone (nodes)
cogs/              # uma feature por cog (carregados automaticamente)
data/              # bancos por servidor (runtime): guild_<id>.db + registry.db
```

## Multi-tenant — como o servidor é resolvido

`database.py` guarda o guild atual num **`ContextVar`** (`_current_guild`). `_db()` resolve
o arquivo `data/guild_<id>.db` a partir dele. **Sem guild no contexto → erro de propósito**
(falha barulhenta em vez de misturar dados entre servidores).

O contexto é ligado em CADA ponto de entrada:
1. **Interações** (slash, autocomplete, botões, selects, modais): monkeypatch em
   `ConnectionState.parse_interaction_create` no `main.py` — **tem que vir ANTES de criar o bot**
   (o `ConnectionState` captura o parser no `__init__`).
2. **Comandos de texto/prefixo:** `@bot.before_invoke`.
3. **Loops e listeners de gateway:** setam o contexto explícito via `database.using_guild(gid)` ou `set_current_guild(gid)`.

Banco GLOBAL único: `data/registry.db` (`activated_servers`) — só a lista de servidores ativados via `/ativar`.

Pool de conexões **lazy** por banco: 1 conexão baseline, cresce sob demanda até `_POOL_SIZE`,
fecha ociosas após TTL (cada conexão aiosqlite = 1 thread; isso mantém ~1 thread/servidor em repouso).

## Convenções (siga ao escrever código novo)

- **Embeds:** sempre via `utils.py` (`make_embed`, `ok_embed`/`err_embed`/`warn_embed`/`info_embed`,
  `send_ok`/`send_err`/`send_warn`/`send_info`). Tema **minimalista**: a COR carrega o significado,
  títulos secos, SEM footer/thumbnail/author, emoji só quando é DADO (não decoração).
- **Silver:** entrada com `utils.parse_silver` (aceita `1.5m`, `150k`, `2,298,291`, `2.192.281`);
  saída com `utils.format_silver` (`1,500,000`).
- **Comandos híbridos:** `@commands.hybrid_command` (funcionam por `/` e por `!`). `send_*` lida com
  Interaction e Context. Erros globais tratados em `main.py` (`on_app_command_error` responde de forma
  segura mesmo com interação expirada/já respondida).
- **Permissões:** NÃO são declarativas. Cada comando checa cargo no corpo (cargos configurados por
  servidor no `economy_config`: `role_council`, `role_logistic`, `role_caller`, `role_officer`,
  `role_mentor`, `role_lead`). O `cogs/help.py` mantém o mapa `_ACCESS` espelhando esses checks —
  **ao mudar a permissão de um comando, atualize os dois lugares.**
- **Sheets é best-effort:** o bot NUNCA deve quebrar por causa da planilha — falha de rede/parse
  retorna `None`/`[]` e loga. Webhook + secret ficam no `economy_config` de cada servidor (`/setup` → Planilha).
- **Idioma:** todo texto pro usuário, comentário e nome de comando em **português**.

## Mapa de cogs (categoria → cogs)

- **Geral:** `misc` (`/ativar`, `/disarray`, `/teamspeak`), `avatar`, `perfil`, `help`
- **CTA:** `cta` (o maior — `/cta`, `/deleteevent`, `/punishment`, `/callout`, `/openregear`, `/adiarcta`),
  `battleboard` (`/bb`), `lootlog` (`/enviarlog`, `/reconciliarlog`), `regears`, `splits`, `escalacao`,
  `massinfo` (`/liberarfuncoes` — desliga o gate de vagas/parties do registro de funções;
  `/gateinfo` — diagnóstico do gate)
- **Economia:** `economy` (`/setup`, `/balance`, `/guildbalance`, `/pay`, `/addmoney`, `/removemoney`,
  `/addguildmoney`, `/removeguildmoney`, `/economystats`, `/leaderboard`), `tabsell` (leilões de tab)
- **Energia:** `energia` (`/energy`, `/setenergy`, `/energylog`, `/energywl`)
- **Nodes:** `nodes` (`/stopnode`, `/addnodemap`, `/removenodemap`, `/nodemaps`) — publicar o
  calendário é no `/setup` → **Nodes**
- **Membros:** `registration` (`/register`), `mentoria` (`/trial`),
  `recruitment` (sem comandos) — ticket CLÁSSICO de recrutamento em thread privada: painel com
  botão → abre thread, adiciona toda a staff e posta um embed de instruções. Decisão sem botões:
  ganhar cargo de membro/trial = aprovado (arquiva + apaga em 4h); deletar a thread = recusado
  (DM ao candidato). **Config (canal/cargos/painel) fica no `/setup` → Recrutamento.**
- **Gestão:** `management` (`/attendance`, `/lowattendance`)
- **Configuração:** `clock` (`/stoputc`) — definir o relógio UTC é no `/setup` → **Relógio UTC**

## Cuidados

- **Não há git neste projeto** (nenhum `.git`). Considere `git init` — o código-fonte não tem backup versionado.
- `database.py` é gigante e tem migrações idempotentes embutidas em `_init_schema`. Ao adicionar coluna,
  siga o padrão de `ALTER TABLE ... ` protegido (try/except ou checagem de coluna) já usado lá.
