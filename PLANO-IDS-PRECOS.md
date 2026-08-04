# Plano: ID canônico = nome do jogo

## Problema

O sistema de preços usa 3 formatos diferentes de item_id:
- **Catálogo** (`refining.json`, `catalog.json`): `T4_PLANKS_LEVEL1` (materiais), `T4_BAG@1` (equipamentos)
- **Banco nosso** (`item_prices`): `T4_PLANKS@1` (materiais), `T4_BAG@1` (equipamentos)
- **ADP**: `T4_PLANKS_LEVEL1@1` (materiais), `T4_BAG@1` (equipamentos)

Conversão nas fronteiras causa bugs (normalize_item_id errado, IDs não batem, preços não aparecem).
ADP inventou IDs (`T4_FIBER_LEVEL1@1`) que não existem no jogo.

## Decisão

ID canônico em todo o sistema = **nome em inglês do jogo** (o que o render CDN aceita).

### Formato
- **Equipamento flat**: `"Adept's Cursed Staff@0"` (nome + `@0`)
- **Equipamento encantado**: `"Adept's Cursed Staff@1"` (nome + `@n`)
- **Recurso flat**: `"Hemp"` (nome, sem `@`)
- **Recurso encantado**: `"Uncommon Hemp"` (nome completo do localization, sem `@`)

### Por que
- É o que o jogo usa no render CDN (`render.albiononline.com/v1/item/Hemp.png`)
- É o que o jogador vê no mercado
- Sobrevive a patches (itens novos aparecem com nome, não com UniqueName inventado)
- Não precisa de `_LEVELn` nem conversão entre formatos

## Fonte do mapeamento

O dump antigo (`backend/data/ao-bin-dump/items.json`, 17MB) já tem `UniqueName` + `LocalizedNames` embutido.
O dump novo (`referencia/ao-bin-dumps-master/`) tem a mesma info mas em formato diferente (cruzar com `localization.json`).

Usar o **dump antigo** pra gerar o mapeamento (mais simples, já tem nomes embutidos).
Atualizar o dump antigo com o novo quando necessário (copiar arquivos).

## Fases

### Fase 1 — Sistema de preços (este plano)

Só o sistema de preços migra para game_name. Catálogo, comp builder, render, spells continuam com UniqueName por enquanto.

#### 1.1 Gerar mapeamento UniqueName → game_name

Script Python: `backend/scripts/seed_item_names.py`

Lê `items.json` (dump antigo), gera `data/item_names.json`:
```json
{
  "T4_FIBER": "Hemp",
  "T4_FIBER_LEVEL1@1": "Uncommon Hemp",
  "T4_FIBER_LEVEL2@2": "Rare Hemp",
  "T4_2H_CURSEDSTAFF": "Adept's Great Cursed Staff",
  "T4_BAG": "Adept's Bag",
  ...
}
```

Regras:
- Para cada item no dump, `UniqueName → LocalizedNames["EN-US"]`
- Para equipamentos encantados (não têm entrada própria), gerar variantes:
  `T4_BAG@1 → "Adept's Bag@1"`, `T4_BAG@0 → "Adept's Bag@0"`
- Para recursos encantados (têm entrada própria com `_LEVELn@n`):
  `T4_FIBER_LEVEL1@1 → "Uncommon Hemp"` (sem `@`)

Total esperado: ~11.800 entradas (5.961 base + 5.844 encantamentos).

#### 1.2 Backend serve o mapeamento

`GET /companion/items-map` — devolve `{unique_name: game_name}`.

Companion baixa e cacheia em `<config_dir>/ziggs-companion/item_names_v3.json` (bump de versão).
Frontend baixa e cacheia em localStorage (ou fetch sob demanda).

#### 1.3 Companion: manda game_name

`photon_parser.rs`:
- `normalize_item_id` → renomear pra `to_game_name(ItemTypeId, ench) -> String`
- Recebe `ItemTypeId` do jogo (UniqueName), converte pra game_name usando o mapeamento
- Se não encontra no mapa, usa o UniqueName cru (fallback)
- `extract_market` manda game_name no `item_id` do buffer de preços

O mapeamento é embarcado no binário (include_bytes do JSON) OU baixado do backend no startup e cacheado.
**Recomendado: baixar do backend** (como já faz com spell_names e item_names). Companion não precisa recompilar pra atualizar nomes.

#### 1.4 Backend: guarda game_name

`prices.py`:
- `upsert_companion_prices`: recebe `item_id` = game_name, guarda direto. Sem conversão.
- `_fetch_spot_prices` / `_fetch_history` (sync ADP): converte ADP UniqueName → game_name usando o mapeamento antes de gravar.
- `get_battle_prices`: já usa o mapeamento pra converter.
- `_bank_to_adp` / `_adp_to_bank` / `_MATERIAL_SUFFIXES`: **remover**. Substituir por `_unique_to_game` / `_game_to_unique`.
- `sync_5city_prices`: converte ADP UniqueName → game_name ao gravar.

`companion.py`:
- `/prices/submit`: recebe game_name, guarda direto. Sem conversão.
- `/market-history/submit`: mesmo.

#### 1.5 Frontend: usa game_name

`adp.ts`:
- `toMarketId(catalogId)` → `toUniqueId(gameName)`: converte game_name → UniqueName pra chamar ADP
- `fromMarketId(marketId)` → `fromUniqueId(uniqueId)`: reverte
- `fetchAdpPrices`: manda UniqueNames pro ADP, converte respostas de UniqueName → game_name
- `toBankId`: **remover**

`ziggs.ts`:
- `fetchZiggsPrices`: manda game_name direto (sem toBankId), recebe game_name direto
- `parseDate`: mantém (já funciona)

`CraftCalculator.tsx`:
- `allItems` já contém catalogIds (UniqueNames do catálogo). Precisa converter pra game_name antes de mandar pro fetch.
- OU: o catálogo é a Fase 2. Por ora, o CraftCalculator precisa de um mapeamento catalogId → game_name.
- `sellPriceId`: ajustar pra game_name (STONEBLOCK sem @ continua igual)

`RefiningCalculator.tsx`:
- Mesmo ajuste do CraftCalculator.

#### 1.6 Purgar DB

```sql
TRUNCATE item_prices, item_prices_latest;
```

Dados antigos são inúteis (formato errado). Recomeçar do zero.
Companion regrava com game_name ao capturar preços.
Sync ADP regrava com game_name ao sincronizar.

#### 1.7 Mapeamento catálogo → game_name

O catálogo (`catalog.json`, `refining.json`) usa UniqueNames (`T4_FIBER_LEVEL2`).
O CraftCalculator precisa mandar game_name pro fetch de preços.

Opções:
- a) Gerar um segundo mapeamento catalogId → game_name no seed
- b) O frontend baixa o mapeamento UniqueName → game_name e converte catalogId (que é UniqueName) → game_name antes de chamar fetch

**Recomendado: b.** O catálogo JÁ usa UniqueName. O mapeamento UniqueName → game_name cobre isso.
`T4_FIBER_LEVEL2` (catálogo) → mapeamento → `"Rare Hemp"` (game_name).

Mas o catálogo usa `T4_FIBER_LEVEL2` (sem `@2`), e o mapeamento tem `T4_FIBER_LEVEL2@2`.
Precisa de um passo de normalização: `T4_FIBER_LEVEL2` → `T4_FIBER_LEVEL2@2` (adicionar `@n` baseado no `_LEVELn`) antes de consultar o mapeamento.

OU: gerar o mapeamento com AMBAS as chaves:
```json
{
  "T4_FIBER_LEVEL2": "Rare Hemp",
  "T4_FIBER_LEVEL2@2": "Rare Hemp",
  ...
}
```

**Recomendado: gerar com ambas as chaves.** Custo mínimo, evita lógica de normalização no frontend.

### Fase 2 — Catálogo e comp builder (futuro)

- `catalog.json` e `refining.json` migrados para game_name
- `EquipGrid`, `ItemPicker`, `CompBuilder` usam game_name
- Render proxy (`render.py`) aceita game_name
- Weapon spells indexados por game_name

Não detalhado aqui — faremos quando a Fase 1 estiver sólida.

## Ordem de implementação

1. **Script seed** (`seed_item_names.py`) — gera `data/item_names.json` com ambas as chaves
2. **Backend serve** `/companion/items-map` — devolve o mapeamento
3. **Backend prices.py** — `_unique_to_game` / `_game_to_unique`, aplicar em sync ADP e ingest companion
4. **Backend companion.py** — `/prices/submit` e `/market-history/submit` recebem game_name direto
5. **Companion** — baixa mapeamento, `to_game_name()` no `extract_market`
6. **Frontend adp.ts** — `toUniqueId` / `fromUniqueId` com mapeamento
7. **Frontend ziggs.ts** — manda/recebe game_name direto
8. **Frontend CraftCalculator.tsx** — converte catalogId → game_name antes de fetch
9. **Purgar DB** — `TRUNCATE item_prices, item_prices_latest`
10. **Testar** — companion captura, backend grava, frontend mostra

## Arquivos afetados

### Novos
- `backend/scripts/seed_item_names.py` — script que gera o mapeamento
- `backend/data/item_names.json` — mapeamento UniqueName → game_name (gerado)

### Backend
- `backend/app/services/prices.py` — remover `_bank_to_adp`/`_adp_to_bank`, adicionar `_unique_to_game`/`_game_to_unique`
- `backend/app/api/routes/companion.py` — `/prices/submit` e `/market-history/submit` sem conversão
- `backend/app/api/routes/catalog.py` — `/price-quotes` devolve game_name (já vem do DB)
- `backend/app/api/routes/render.py` — sem mudança na Fase 1 (render continua com UniqueName)

### Companion
- `companion/src-tauri/src/photon_parser.rs` — `to_game_name()` substitui `normalize_item_id()`
- `companion/src-tauri/src/lib.rs` — baixa mapeamento no startup (como spell_names)

### Frontend
- `frontend/src/lib/prices/adp.ts` — `toUniqueId`/`fromUniqueId` com mapeamento
- `frontend/src/lib/prices/ziggs.ts` — sem `toBankId`, manda game_name direto
- `frontend/src/components/CraftCalculator.tsx` — converte catalogId → game_name
- `frontend/src/components/RefiningCalculator.tsx` — mesmo

### DB
- `item_prices` — TRUNCATE (dados antigos em formato errado)
- `item_prices_latest` — TRUNCATE

## Riscos

1. **Mapeamento incompleto**: 817 itens sem nome no localization (quest items, etc.) — não têm mercado, não afeta preços.
2. **Itens novos em patches**: o mapeamento precisa ser regenerado a cada patch. Script automatiza isso.
3. **ADP usa UniqueName**: conversão nas fronteiras continua necessária, mas agora é só uma direção (game_name ↔ UniqueName), não três formatos.
4. **Catálogo ainda usa UniqueName**: o CraftCalculator precisa converter catalogId → game_name. Mapeamento com ambas as chaves resolve isso.
5. **DB purge**: dados antigos são perdidos. Companion regrava ao capturar. Sync ADP regrava ao sincronizar. Aceitável.

## Validação

1. Companion captura preços em Lymhurst → backend grava com game_name
2. CraftCalculator recarrega → mostra preços do nosso banco (game_name) com idade correta
3. ADP continua funcionando (conversão game_name ↔ UniqueName nas chamadas)
4. `resolveFreshest` escolhe o mais fresco entre nosso banco e ADP (ambos em game_name)
5. Itálico (ADP) vs normal (nosso banco) funciona corretamente