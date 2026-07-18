# Ziggs — Plano multi-workstream (6 frentes, para execução por agentes independentes)

## Contexto

O usuário pediu um planejamento executável por outras IAs cobrindo 6 frentes: (1) correção de segurança de permissões, (2) performance da barra de busca global, (3) ads com tamanhos corretos (AdSense), (4) customização de perfil que "não funciona", (5) histórico de gold no nosso banco (independência da AODP), (6) redesign da página de comps. A exploração revelou fatos que moldam o plano:

- **Segurança**: vulnerabilidade real de escalação — `POST /auth/select-guild` aceita `is_admin: true` do cliente sem verificação (auth.py:228). Qualquer usuário logado vira admin de qualquer guilda.
- **Busca**: `_search` roda 8-12 queries por tecla, 5-7 delas full-scan nas duas maiores tabelas, com `norm_sql()` não-sargável e zero cache.
- **Ads**: AdBanner só define altura (largura = 100% do pai) — nenhum dos 3 spots bate com tamanhos IAB.
- **Perfil**: o código está completo (avatar/banner/tema, upload, gate); a causa provável do "não funciona" é `/profile` NÃO estar no proxy do Vite (dev quebra silenciosamente) + cache de imagem (URL fixa em re-upload).
- **Gold**: AODP `gold.json` retorna histórico completo desde 2017 → backfill único + snapshots periódicos (escolha do usuário).
- **Comps**: monólito de 2209 linhas com accordion inline que reflui a página; padrão master-detail já existe no CSS (`.comp-layout`, usado pela Escalação).

## Notas globais (valem para todo workstream)

- Backend: rodar de `backend/` com `scripts\python.exe` (venv embutido). Migrações: `scripts\python.exe -m alembic upgrade head`. Head atual: `m9b4d7e2a1c8`.
- **Colisão de head Alembic**: WS2 e WS5 criam migração cada. O segundo a implementar DEVE rodar `alembic heads` antes e encadear `down_revision` no head real — nunca ambos em `m9b4d7e2a1c8`.
- Tudo deve funcionar em SQLite (dev) E Postgres (prod) — usar `op.batch_alter_table` e `JSONB().with_variant(sa.JSON(), "sqlite")` (template: migração `m9b4d7e2a1c8`).
- i18n: toda string nova de UI nas 3 línguas (pt/en/es) em `frontend/src/i18n/index.ts`.
- **Vite config duplicado**: `frontend/vite.config.ts` E `vite.config.js` existem (o `.js` carrega primeiro). Mudança de proxy → nos dois (ou deletar o `.js`).
- Scripts one-off: `backend/scripts/*.py`, padrão em `add_player_region.py`.

## Ordem de execução sugerida

1. **WS1 Segurança** (crítico, pequeno, independente)
2. **WS2 Busca** (maior ganho de infra; tem migração)
3. **WS4 Perfil** (bug reportado; minúsculo)
4. **WS5 Gold** (migração — encadear após WS2)
5. **WS3 Ads** (frontend-only, independente)
6. **WS6 Comps** (maior; 3 fases)

WS3, WS4 e WS6-fase-1/2 não têm migração e podem rodar em paralelo com qualquer outro.

---

## WS1 — SEGURANÇA: escalação de privilégio (CRÍTICO)

**Vulnerabilidade verificada**: `backend/app/api/routes/auth.py:228` — `member.is_guild_admin = body.is_admin` (campo do cliente, `SelectGuildIn.is_admin` linha 185), sem verificação de membership nem de admin no Discord. `compute_permissions` (backend/app/auth/permissions.py) retorna tudo-true para `is_guild_admin`; `_require_admin` (auth.py:2468) passa; o atacante pode até reescrever `GuildRolePermission`. `POST /auth/switch-guild` (auth.py:246) nunca re-deriva a flag.

**A derivação correta JÁ EXISTE**: `deps.py:_provision_member` (linhas 120-215) — bot token primário (owner + bit ADMINISTRATOR), fallback OAuth do usuário (MANAGE_GUILD). Helpers em `backend/app/auth/discord.py`.

**Arquivos**: `backend/app/api/deps.py`, `backend/app/api/routes/auth.py`, `backend/scripts/reverify_guild_admins.py` (novo), `frontend/src/api.ts:656` (selectGuild), `frontend/src/components/GuildPicker.tsx:59`, `frontend/src/components/EscalacaoPage.tsx:271`.

**Passos**:
1. Extrair de `_provision_member` uma função módulo-level `verify_guild_membership(user, guild_id) -> (name, icon, is_admin, role_ids)` — só chamadas Discord, sem DB (preserva a disciplina "sem flush antes de Discord" do select_guild, ver comentário auth.py:215). 403 se não-membro, 502 se inverificável.
2. `_provision_member` passa a chamar o helper (comportamento igual).
3. `select_guild`: remover `is_admin` do `SelectGuildIn`; chamar `verify_guild_membership` ANTES de qualquer write; `is_guild_admin` e `discord_role_ids` vêm do helper; nome/ícone do Discord preferidos sobre os do body.
4. `switch_guild`: re-derivar via helper; 403 → deleta o GuildMember e recusa; 502 → mantém a row sem mexer na flag.
5. Frontend: `selectGuild` perde o parâmetro `is_admin` (+2 call sites). O `is_admin` de `GET /auth/guilds` (display-only) fica.
6. Script de remediação `reverify_guild_admins.py`: para cada `is_guild_admin=True`, re-deriva via bot token; reseta False quando não confirma (inclusive quando o bot não está na guilda — o dono real recupera no próximo select-guild). Sleep 0.5s entre chamadas; imprimir resumo. Rodar uma vez pós-deploy.

**Verificação**:
- `curl -X POST /auth/select-guild` com cookie de sessão + `{"guild_id":"<guilda que NÃO sou membro>","is_admin":true}` → 403, sem row criada.
- Mesmo request para guilda onde sou membro comum → 200, `is_guild_admin=0` no banco, `/auth/my-permissions` sem admin.
- `UPDATE guild_members SET is_guild_admin=1` manual num user comum → `POST /auth/switch-guild` reseta pra 0.
- Rodar o script numa cópia do app.db → imprime rebaixamentos; admins reais mantêm.
- UI: GuildPicker funciona normal; admin real de verdade continua vendo Config.

---

## WS2 — Performance da busca global (tabela-índice SearchEntry)

**Problema verificado**: `GET /public/search` → `_search` (backend/app/api/routes/profiles.py:575-788): 5-7 full-scans por tecla em `battle_participants`/`battle_guilds` (colunas `name`/`guild_name`/`alliance_name` SEM índice), `norm_sql()` = `lower(replace(×10))` não-sargável (backend/app/services/search_norm.py:76), LIKE `%q%`, N+1 (`_latest_affil`, `_factions_summary`), 2 passes fuzzy (LIMIT 300 + levenshtein em Python), zero cache. Frontend `GlobalSearch.tsx` sem AbortController nem guard de sequência. `players.py:search_players` (linha 445) tem o mesmo problema não-sargável.

**Abordagem**: tabela-índice pré-normalizada + hooks no write-path + rebuild periódico (padrão `weapon_stats`).

**Arquivos**: `backend/app/models/players.py` (+`SearchEntry`), migração nova, `backend/app/services/search_index.py` (novo), hooks em `battle_tracker.py` (~linha 204 BattleGuild, ~402 BattleParticipant) e `player_tracker.py` (`upsert_player`), `main.py` lifespan, `profiles.py` `_search` reescrito, `players.py` `search_players`, `frontend/src/components/GlobalSearch.tsx`.

**Modelo**:
```python
class SearchEntry(Base):
    __tablename__ = "search_entries"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id"),
        Index("ix_search_entries_type_norm", "entity_type", "norm_name"),
        Index("ix_search_entries_type_len", "entity_type", "name_len"),
    )
    # entity_type: player|guild|alliance; entity_id: id Albion
    # display_name, norm_name (normalize() na escrita), name_len
    # region (players), guild_name/alliance_name (denorm, mata o N+1)
    # guild_count (alianças), weight (nº batalhas — ordenação)
```
A migração também cria `ix_battle_guilds_alliance_id` (necessário pra seção de batalhas).

**Passos**:
1. Modelo + migração (atenção ao encadeamento de head).
2. `search_index.py`: `upsert_entry(...)` (sem commit — transação do caller), `rebuild(db)` (truncate + repopula de AlbionPlayer/battle_guilds com pesos agregados, commit em chunks de 5k), `run_forever()` (rebuild se vazio no startup; re-rebuild a cada 6h).
3. Hooks: pós-insert de BattleGuild/BattleParticipant/upsert_player → `upsert_entry` com try/except-log (falha de índice nunca quebra ingestão).
4. `_search` reescrito: por tipo, passo 1 prefixo `norm_name LIKE 'q%'` (sargável, ORDER BY weight DESC LIMIT 6); passo 2 substring `%q%` só se <6 (scan de tabela compacta — ok); passo 3 fuzzy só se <6 e len≥4 (`name_len BETWEEN` + `search_norm.match()` — semântica igual à atual). Payload de players direto da SearchEntry (deleta `_latest_affil`). Seção batalhas: resolve IDs no índice → consulta batalhas por FK indexada → fluxo atual de top-6 + `_factions_summary` intacto.
5. `search_players`: passo local via SearchEntry (prefixo + substring, filtrado por region), mapeia de volta pra AlbionPlayer via `albion_id IN`; fallback Albion ao vivo intacto.
6. `GlobalSearch.tsx`: AbortController em ref (aborta o anterior), request-id monotônico (ignora resposta antiga), engolir `AbortError`. Debounce 300ms fica.

**Verificação**:
- `alembic upgrade head`; startup loga rebuild; `SELECT COUNT(*), entity_type FROM search_entries GROUP BY entity_type` plausível.
- Latência: `curl -w "%{time_total}"` em `/public/search?q=...` antes/depois (esperar ≥10x).
- `EXPLAIN QUERY PLAN` do passo-prefixo usa `ix_search_entries_type_norm`.
- Semântica preservada: `q=pivas` → PLVAS (fuzzy); `q=requ` → "R E Q U 1 3 M" (self-checks de search_norm.py:88-111); seção batalhas ainda popula.
- Frescor: batalha nova ingerida → player/guilda aparece na busca sem esperar rebuild.
- UI: digitação rápida sem flicker/resultados fora de ordem (testar com throttle no devtools).

---

## WS4 — Customização de perfil: consertar o que não funciona

**Causas-raiz verificadas**:
1. **`/profile` NÃO está no proxy do Vite** (só `/auth,/guilds,/meta,/players,/render,/claims`) → em dev, `GET /profile/me` cai no fallback SPA do Vite (devolve index.html com 200) → `res.json()` explode → ClaimsPanel fica em "Carregando…" pra sempre; imagens `/profile/image/...` 404 igual. **Provavelmente é exatamente o "não funciona" do usuário.** (`/craft` tem o mesmo gap — corrigir junto.)
2. **Imagem cacheada**: URL e filename fixos por kind (`user_profile.py:52,99`) → re-upload mantém a URL e o browser mostra a imagem velha. Sem cache-busting.
3. Sem validação/redimensionamento de imagem (só cap de 5MB).
4. DELETEs `/profile/avatar|banner` sem `_require_verified` (assimetria inofensiva, arrumar).

**Arquivos**: `frontend/vite.config.ts` E `.js` (add `/profile` e `/craft` no proxy), `backend/app/services/user_profile.py`, `backend/app/api/routes/user_profile.py`. Pillow JÁ é dependência (requirements.txt, usado pelo OCR de regear).

**Passos**:
1. Proxy do Vite nos dois arquivos (ou deletar o `.js` se for cópia morta).
2. Cache-busting em `_image_url`: anexar `?v={int(mtime)}` (guard OSError). Cobre painel próprio e perfil público (ambos fluem por ela).
3. Pillow em `_save_image`: `Image.open` com try/except → 400 se não for imagem; `thumbnail((512,512))` avatar, `(1920,600)` banner; `convert("RGB")` antes de salvar JPEG. Cap de 5MB fica.
4. `_require_verified` nos 2 DELETEs.
5. **Matriz de teste (workstream verification-first)**:

| # | Fluxo | Esperado |
|---|---|---|
| 1 | `GET /profile/me` em dev logado | JSON, não index.html |
| 2 | Upload avatar no ClaimsPanel | thumbnail aparece na hora |
| 3 | Re-upload de avatar DIFERENTE | imagem nova sem hard refresh (`?v=` mudou) |
| 4 | Idem 2-3 pra banner | idem |
| 5 | Trocar tema | perfil público reflete (`data-profile-theme`) |
| 6 | User não-verificado: PUT/POST/DELETE | 403 em todos |
| 7 | Visitante deslogado no perfil do jogador | avatar/banner/tema visíveis |
| 8 | Arquivo 6MB / .txt renomeado .png | 400 com mensagem clara |

---

## WS5 — Histórico de gold no nosso banco (backfill + snapshots)

**Estado atual**: Dashboard.tsx (linhas 547-693) busca AODP gold.json direto do browser por servidor. Padrão a espelhar: `player_count_snapshot.py` (loop 15min) + `PlayerCountSnapshot` + rota `GET /battles/active-players/history` + `ActivePlayersCard`. AODP retorna histórico completo desde 2017 (horário) — user escolheu backfill total.

**Arquivos**: `backend/app/models/prices.py` (+`GoldPriceSnapshot`), migração nova (encadear head!), `backend/app/services/gold_price.py` (novo), `main.py` lifespan, `backend/app/api/routes/battles.py` (rota ao lado de `active_players_history`, linha ~195), `frontend/src/components/Dashboard.tsx` (GoldPriceCard).

**Modelo**: `GoldPriceSnapshot(region: americas|europe|asia, price: int, recorded_at, UniqueConstraint(region, recorded_at))`. Mapa de hosts: americas→west, europe→europe, asia→east (`SERVER_TO_REGION` já existe em Dashboard.tsx:700). Padrão httpx: `services/prices.py`.

**Passos**:
1. Modelo + migração.
2. `gold_price.py`: `_fetch_chunk(region, start, end)` (`gold.json?date=&end_date=`, timeout 20, tz-normalizar); `backfill(db)` — por região, cursor = `MAX(recorded_at)` ou 2017-01-01, janelas de 180 dias, insere só `> max` (idempotente e resumível por construção), commit por janela, sleep 2s entre requests (gentil com a AODP), log de progresso; `run_forever()` — backfill primeiro (cobre tabela vazia E gap pós-downtime), depois poll de janela de 2 dias a cada 900s.
3. Task no lifespan.
4. Rota `GET /battles/gold/history?range=1m|6m|1y|all` → `{collected_since, series: {region: [{t, price}]}}` com **bucketing server-side** ≤400 pontos/região (média por bucket; algoritmo igual ao `downsample` do Dashboard linhas 589-601) + cache TTL 10min por range ("all" lê ~210k rows).
5. Frontend: GoldPriceCard troca o fetch AODP por UM fetch do nosso endpoint; inverte `SERVER_TO_REGION` pros labels/cores atuais; ranges/zoom/refresh de 5min ficam; downsample cliente vira no-op de segurança. Todos os ranges habilitados desde o dia 1 (backfill dá `collected_since` ≈ 2017).

**Verificação**:
- `alembic upgrade head`; logs mostram janelas de backfill avançando; `SELECT region, COUNT(*), MIN(recorded_at), MAX(recorded_at) ... GROUP BY region` → ~70k rows/região até 2017.
- Ctrl+C no meio do backfill + restart → retoma do max, sem duplicatas.
- `curl /battles/gold/history?range=all` → ≤400 pts/região; segunda chamada em <10min instantânea (cache).
- UI: card renderiza os 4 ranges incl. "Tudo"; zoom ok; **zero requests para `*.albion-online-data.com` na aba Network**.
- Após 15min rodando: ponto mais novo avança.

---

## WS3 — Ads (AdBanner pronto pra AdSense)

**Problema verificado**: AdBanner.tsx é placeholder com só 2 variantes (altura fixa, largura 100% do pai): App.tsx:505 estica ~1200px+; Dashboard.tsx:819 estica ~992px; ManagementPage.tsx:65 espremido a ~196px. Detector de adblock existe e FICA.

**API do componente**:
```tsx
type AdVariant = "leaderboard" | "mediumRectangle" | "largeRectangle" | "skyscraper" | "mobileBanner";
const AD_SIZES = { leaderboard: {w:728,h:90}, mediumRectangle: {w:300,h:250},
  largeRectangle: {w:336,h:280}, skyscraper: {w:160,h:600}, mobileBanner: {w:320,h:50} };
function AdBanner({ variant, mobileVariant, slot }: { variant: AdVariant; mobileVariant?: AdVariant; slot?: string })
```
- Wrapper `.ad-slot` (flex center) > `.ad-box` (width/height fixos da variante, `max-width:100%`, overflow hidden).
- Dev ou sem `VITE_ADSENSE_CLIENT` → placeholder atual dimensionado pela variante (layout realista em dev). Bloqueado → `.ad-slot-blocked` atual.
- Prod: `<ins className="adsbygoogle" style={{display:"inline-block",width,height}} data-ad-client={ADS_CLIENT} data-ad-slot={slot} />` + `useEffect(() => (window.adsbygoogle ||= []).push({}), [])`. Loader script em `index.html`.
- `mobileVariant`: resolve via `matchMedia("(max-width:767px)")`; remontar via `key={resolvedVariant}` se trocar (AdSense não re-renderiza `<ins>` vivo).

**Spots**: App.tsx:505 → `leaderboard` + `mobileVariant="mobileBanner"`; Dashboard.tsx:819 → `mediumRectangle`; ManagementPage.tsx:65 (rail 220px) → `skyscraper` (160×600 cabe).

**Verificação**: placeholders com tamanho certo nos 3 spots; sem scrollbar horizontal em 1280px e 375px; leaderboard vira 320×50 no mobile; com adblocker → mensagem em todos; `VITE_ADSENSE_CLIENT=ca-pub-TEST npm run build && npm run preview` → `<ins>` presente com data-attrs corretos no view-source.

---

## WS6 — Redesign da página de comps (master-detail, 3 fases)

**Barra de aceitação das fases 1-2 = paridade total de comportamento** (flex builds/BuildTabs, swap de equipamento alternativo, price chart, build codes, undo, sugestões, gating comps.view/create/manage, modo offline/demo).

**Ativos verificados**: componentes extraíveis já no CompBuilder.tsx — `SpellPicker`(189), `EquipStrip`(274), `PriceHistoryChart`(354, exportado), `BuildTabs`(552), `RoleViewBlock`(588), `EquipGrid`(703, exportado), `AltEquipSection`(781), `ColorPicker`(849) + helpers (compToDraft, roleToPayload, build-code codec). **BattlePage.tsx:5 importa `{EquipGrid, PriceHistoryChart, DraftEquip}` de ./CompBuilder — manter re-exports.** CSS master-detail já existe: `.comp-layout/.comp-left/.comp-right` (styles.css:299-312, usado pela Escalação). fn-types hoje em localStorage `hideout_fn_types` (por-browser, não por-guilda!). Guild.settings JSON + api.updateGuildSettings já existem.

**Fase 1 — extração, zero mudança visual**:
1. Criar `frontend/src/components/comp/`: `types.ts`, `helpers.ts`, `EquipGrid.tsx`, `PriceHistoryChart.tsx`, `BuildTabs.tsx`, `RoleViewBlock.tsx`, `SpellPicker.tsx`, `ColorPicker.tsx`.
2. Separar telas: `comp/CompList.tsx` (lista + criar/deletar, gating) e `comp/CompEditor.tsx` (header + parties + estado de edição). `CompBuilder.tsx` vira container fino que re-exporta `EquipGrid`/`PriceHistoryChart`/`DraftEquip` pro BattlePage.
3. Mesmos classNames, mesmo DOM.
- Aceitação: `npx tsc --noEmit` limpo; click-through de todo fluxo idêntico; BattlePage intacto; `git diff --stat` mostra moves quase-puros.

**Fase 2 — layout master-detail**:
1. CompEditor renderiza `.comp-layout`: esquerda = party cards compactos (fn chip colorido + nome + `EquipStrip` mini), clique seleciona. Accordion `openCard` → `selected: [pi,si]|null`, **sem expansão inline** (mata o reflow).
2. Direita = `comp/RoleDetailPanel.tsx` em `.comp-right` com modificador `.comp-detail` (`width:420px; max-height:calc(100vh-32px); overflow-y:auto` — 320px é estreito pro EquipGrid+chart; chart ABAIXO do grid no painel). View = RoleViewBlock + BuildTabs; Edit = o form inline atual movido inteiro. Seleção vazia → `.comp-right-hint`.
3. Responsivo: `@media (max-width:1100px)` empilha (painel vira estático full-width).
- Aceitação: paridade + lista da esquerda nunca reflui ao trocar seleção; painel rola independente; funciona a 900px.

**Fase 3 — fn-types por guilda**:
1. Backend em `comps.py`: `GET /guilds/{gid}/comps/fn-types` (comps.view) e `PUT` (comps.manage; valida keys únicas, cores `#rrggbb`, ≤30), armazenado em `Guild.settings["fn_types"]` (padrão de merge do auth.py). Sem migração (coluna JSON existe). Endpoint próprio de comps de propósito — `PATCH /auth/guild-settings` é admin-only, fn-types pertencem a comps.manage.
2. `api.ts`: `getCompFnTypes`/`putCompFnTypes`.
3. Frontend: carrega do API no mount; migração one-shot do localStorage (se server vazio + localStorage custom + user tem comps.manage → PUT e limpa a key); `DEFAULT_FN_TYPES` como fallback offline; editor read-only sem comps.manage.
- Aceitação: 2 browsers/contas na mesma guilda veem o mesmo fn-type após reload; PUT sem comps.manage → 403; outras páginas inalteradas (a key de localStorage era só do CompBuilder — verificado por grep).

**Verificação (todas as fases)**: `npx tsc --noEmit`; `npm run build`; matriz de click-through por fase.
