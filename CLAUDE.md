# Ziggs Platform — Guia para Claude

Plataforma web para guildas de Albion Online: gerencia composições de batalha (comps), eventos, batalhas, crafting e regear. Login exclusivo por Discord OAuth.

## Estrutura do projeto

```
hideout-platform/
├── backend/          FastAPI + SQLAlchemy + SQLite (dev) / Postgres (prod)
├── frontend/         React 18 + TypeScript + Vite
├── companion/        App desktop Tauri (Rust + React/TS) — battle scanner distribuído, DNS optimizer, lootlog
├── bot/              Bot Discord legado (discord.py) — não mexa sem precisar
├── bot-v2/           Bot Discord novo (ainda vazio)
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
    ├── App.tsx                 tabs: Scanner / Rota-Túnel / DNS / Lootlog / Config
    ├── main.tsx                bootstrap React
    └── styles.css              CSS global (mesmas variáveis do frontend)
```

**Login: nenhum.** Companion não tem auth — battle scan e DNS são APIs públicas do Albion. Lootlog é só local (parser + copiar CSV, o usuário cola no site da guilda manualmente). Sem cookie, sem sessão, sem Discord OAuth fluindo pelo companion.

**Scan distribuído (Fase 1 — sem admin, sem ToS):**
- Backend tem `companion_scan_tasks` (tabela) com ranges de IDs de batalha por região
- Companion: `POST /companion/scan/claim` → pega tarefa; sonda `https://{host}/api/gameinfo/battles/{id}`; `POST /companion/scan/report` com `found`/`missing`/`errors`
- Backend aplica `upsert_battle_light` nos found (mesma lógica do `battle_sweeper`), grava `BattleIdProbe` nos 404
- Claims expiram em 15min (companion caiu → tarefa volta a pending)
- `claimed_by` = install_id (header `X-Ziggs-Install`), **identidade, não auth**: garante 1 range por PC. A mesma instalação pedindo de novo recebe o range que já tem (com TTL renovado) em vez de acumular ranges — 3 processos abertos no mesmo PC durante um rebuild pegavam 3 ranges e contavam como 3 companions. O dado reportado continua sendo validado contra a API pública do Albion no upsert, nunca se confia no client
- `GET /companion/stats` → `{active}` = instalações distintas que pediram trabalho dentro do `CLAIM_TTL`. Companion antigo (sem header) não entra na conta

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

**Login Discord opcional no companion:**
- Companion não tem auth nativa — scan/DNS/prices são APIs públicas.
- Login Discord é OPCIONAL — só pra auto-submit de lootlog. Sem login, tudo funciona igual.
- Fluxo: companion gera nonce → abre browser em `/companion/auth/start?nonce=X` → OAuth normal → `/companion/auth/done` cunha token (cache em memória) → companion faz poll em `/companion/auth/poll?nonce=X` → recebe `{token, user_id, username}`.
- Token = `URLSafeTimedSerializer(secret_key, salt="ziggs-companion-token-v1")`, 30 dias de validade. Guardado em `CompanionConfig.discord_token`.
- Rotas bearer-auth usam `deps.require_companion_user` (valida token, devolve `User`).
- `COMPANION_API_SECRET` no `.env` (mesmo padrão do `BOT_API_SECRET`).

**Lootlog auto-submit:**
- Companion parseia o log localmente (`lootlog::parse`), mostra preview, e opcionalmente envia o CSV normalizado pro evento ativo.
- User configura `lootlog_guild_id` (snowflake do Discord) na tab Config.
- `GET /companion/lootlog/active-events?guild_id=X` lista eventos `in_progress` onde o user está inscrito (join `EventSignup`).
- `POST /companion/lootlog/ingest` envia `{guild_id, event_id, csv_text}` — mesmo upsert do `/guilds/{g}/lootlog/ingest` (1 submissão por guild+event+submitter).
- Toggle `auto_lootlog_submit` no companion: quando on, envia automaticamente ao parsear.

**Modelos backend (`app/models/companion.py`):**
- `CompanionScanTask` — uma tarefa (range de IDs por região, status pending/claimed/done/failed)

**Config persistido (`CompanionConfig`):**
- `api_base_url`, `character_name`, `region`
- Toggles transparentes: `collect_battles`, `collect_prices`, `collect_damage_meter`, `collect_auto_lootlog`
- `autostart`, `minimize_to_tray`
- WireGuard: `tunnel_enabled`, `tunnel_endpoint`, `tunnel_server_pubkey`, `tunnel_client_privkey`
- Discord login (opcional): `discord_token`, `discord_user_id`, `discord_username`
- Lootlog auto-submit: `lootlog_guild_id`, `auto_lootlog_submit`
- `spell_index_offset` — ajuste do índice de feitiço do damage meter (ver acima). Só mexa calibrando contra o jogo.
- `install_id` — hex de 32 chars gerado no 1º uso e persistido. Leia SEMPRE via `config::install_id()` (gera+salva se vazio, cacheado num `OnceLock`), nunca direto do campo. `ApiClient::new` já o manda como default header em toda request, então nenhum call site precisa passá-lo.

**Rota tipo ExitLag (WireGuard + VPS):**
- Companion cria interface wintun "Ziggs" (10.99.0.2/24), túnela UDP dos IPs do Albion via WireGuard pra VPS
- `tunnel.rs` usa `boringtun` (WireGuard userspace) + `wintun` (driver virtual) — sem kernel module
- Split-tunneling: só tráfego dos IPs do Albion vai pelo túnel, resto fica direto
- **Teste antes de ativar:** mede latência direta vs túnel, só ativa rotas se túnel for melhor
- **Fallback automático:** VPS cai → volta pra rota direta
- Requer admin (criar interface virtual + adicionar rotas) — auto-elevate em runtime via `ShellExecuteW("runas")` quando `tunnel_enabled=true`
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
  - "Índice = posição no documento" é **hipótese**, não fato confirmado. Por isso a UI mostra sempre o `#id` cru ao lado do nome e existe `spell_index_offset` na aba Config: calibra-se em runtime, sem rebuild. Enquanto ninguém calibrar com o jogo aberto, trate os nomes exibidos como suspeitos.
  - Ordem é tudo: qualquer mudança que reordene os feitiços troca TODOS os nomes de uma vez e falha em silêncio (nome errado, não erro). Coberto por `tests/test_spell_names.py`.
- Copy pro Discord: **só `#rank nome — dano`**. Mapa/%/DPS/cura foram removidos por pedido — a lista tem que ser legível no celular.

**System tray:** ícone na bandeja, menu "Abrir/Pausar/Sair". Fechar janela com `minimize_to_tray=true` esconde em vez de sair. Autostart via `tauri-plugin-autostart` (Registry no Win, LaunchAgent no macOS, .desktop no Linux).

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

**Fases:**
- **Fase 1 (atual):** battle scanner distribuído + DNS tester + lootlog manual + tray + autostart. Sem admin, sem ToS.
- **Fase 2 (futuro):** packet capture (Npcap/libpcap) pra price/gold data. Requer admin na ativação. SBI tolera (AODP faz igual).
- **Fase 2.5:** memory read pra damage meter + auto lootlog. Requer admin. SBI tolera leitura passiva (AAT faz igual há anos), mas offsets quebram a cada patch do jogo.

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
    ├── GuildPicker.tsx   Seleção/adição de servidor Discord
    └── GuildConfig.tsx   Configuração da guilda
```

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
