# Ziggs Platform — Guia para Claude

Plataforma web para guildas de Albion Online: gerencia composições de batalha (comps), eventos, batalhas, crafting e regear. Login exclusivo por Discord OAuth.

## Estrutura do projeto

```
hideout-platform/
├── backend/          FastAPI + SQLAlchemy + SQLite (dev) / Postgres (prod)
├── frontend/         React 18 + TypeScript + Vite
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

## Bugs pré-existentes (NÃO introduzidos por nós, ignorar no tsc)

1. `focusedItemId` declarado mas nunca lido em CompBuilder (~linha 754)
2. `spells` desestruturado mas não usado em RoleViewBlock (~linha 1360)
3. `mock.ts` tem objetos sem a prop `fn` exigida por `ApiSlot`

## Padrões não-óbvios

**Modo offline/demo:** se `api.listComps()` falha, carrega `MOCK_API_COMP` e seta `offline: true`. Badge "demonstração" aparece no header.

**updRole vs updRoleQuiet:** `updRole` registra undo history, `updRoleQuiet` (para textarea onChange) não registra cada keystroke para não explodir o histórico.

**captureHistory / releaseFocus:** padrão de undo — captura snapshot ao focar um campo, descarta capturas intermediárias ao sair.

**Flex roles:** um slot pode ter múltiplas roles (roles alternativas). `role.flex_of` identifica que é alternativa. `editRi` é o índice da role sendo editada no slot atual.

**`equip_loaded: false`:** roles carregadas do catálogo chegam sem `build_items` detalhados. O frontend faz um `api.getRole(id)` separado e seta `equip_loaded: true` quando chega. Enquanto false, EquipGrid não renderiza.

**Preços:** `gear_spells` no backend é `Text` (JSON string). No frontend é `Record<string, string|null>`. A conversão acontece em `roleToPayload` e `compToDraft`.

**Albion item base ID:** `"T5_HEAD_PLATE_SET1@2"` → base = `"HEAD_PLATE_SET1"` (remove `T\d+_` do início e `@\d+` do fim). Usado para busca de spells de arma e exclusão tier-agnóstica no ItemPicker.
