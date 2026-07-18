# Plano — Design v2 "war room" no site inteiro

Levar o design da página Início (aprovado em jul/2026) pras demais páginas.
Escrito pra execução por qualquer IA/dev em workstreams paralelos. Leia
`CLAUDE.md` e este doc inteiro antes de mexer.

## O design (o que já existe, não redesenhar)

Implementado na home (`Dashboard.tsx` + seção "Início (dashboard v2)" no fim
de `styles.css`). A linguagem:

- **Cantos retos absolutos.** Nada de border-radius, exceto `rounded-full`
  (avatares, dots) que fica redondo SEMPRE.
- **Painéis** (`.dash-panel`): fundo opaco azulado (`#14151a→#101116`,
  borda `#24262e`), harmônico com o `--bg` (#0e0f13).
- **Cantos fixos (TL+BR)** via `::before/::after`, douram no hover. A
  animação é o **brilho respirando**: radial dourado em cada canto alternando
  entre baixo e alto (keyframe `dash-glow-breathe`, 7s, só opacity — GPU,
  fluido), contido no quadrante (`overflow:hidden`), alpha de pico 0.05.
  Delays aleatórios por painel (inline no componente `Panel`) dessincronizam
  os quadrantes. `prefers-reduced-motion` = brilho estático.
- **Headers editoriais** (`PanelHeader`): título uppercase tracking largo +
  régua em gradiente + ação/filtros à direita. Sem numeração.
- **Chips** (`.dash-chip` / `.dash-chip-on`): filtros de período/toggles,
  1px de borda, ativo dourado.
- **Grid técnico de fundo** (`.dash-root`): 36px, azulado, quase invisível,
  `background-position: center top`, + glow dourado no topo.
- **Esquadramento por regra escopada**:
  `.dash-root :is(.rounded, .rounded-md, .rounded-lg, .rounded-xl) { border-radius: 0 }` —
  esquadra componentes Tailwind sem editá-los (`.rounded-md` entrou no WS4;
  são as 4 únicas variantes usadas no código).
- **Placeholder de anúncio** hachurado (já global).

## Fatos do código que mudam a execução

1. **Componentes CSS-puro usam `var(--radius)`** (classes `rc-`, `sd-`,
   `comp-`, `btn`, dropdowns). A regra `:is(.rounded...)` NÃO os cobre.
   Cobertura em uma linha, a adicionar no WS0:
   `.dash-root { --radius: 0; --radius-sm: 0; }`
2. **Temas de perfil** (`[data-profile-theme=X]` em styles.css ~linha 1299)
   sobrescrevem CLASSES Tailwind amber-*; as primitivas v2 usam
   `var(--gold)`. Pro accent seguir o tema nos perfis, os blocos de tema
   precisam remapear a var (ver WS2). Não converter o resto do site pra
   classes amber — a var é o mecanismo certo, os overrides de classe são o
   legado.
3. `Panel`/`PanelHeader`/`useBreathingCorners` vivem DENTRO de
   `Dashboard.tsx` — extrair antes de qualquer página usar (WS0).
4. Screenshots de verificação: padrão CDP pronto em
   `backend/_tmp_shot.mjs` (headless Edge em :9222; ver também os scripts da
   sessão de jul/2026). Vite dev em :5173 com HMR.

## Workstreams

### WS0 — Extrair primitivas — **FEITO (jul/2026)**

`frontend/src/components/Panel.tsx` criado exportando `Panel` e
`PanelHeader` (hook `useBreathingCorners` interno); Dashboard importa de lá.
`.dash-root { --radius: 0; --radius-sm: 0; }` adicionado ao styles.css.
Home verificada idêntica por screenshot; build limpo. WS1–WS4 estão
desbloqueados: `import { Panel, PanelHeader } from "./Panel";`

### WS1 — Batalhas (`BattleTracker.tsx`, `BattlePage.tsx`) — **FEITO (jul/2026)**

`dash-root` nos raízes; BattlePage: AllianceList/ParticipantTable/
CompositionSection/timeline → `Panel` (os `overflow-hidden` desses cards
foram REMOVIDOS — clipavam os cantos fixos do Panel a -1px; só existiam pro
rounded-xl). Título da composição → `PanelHeader`, chips de função e botão
Multi → `dash-chip`. Headers de tabela (sort) mantidos como estavam — são
funcionais, não editoriais. Verificado por probe CDP (radii 0px, 4 painéis,
8 brilhos) + screenshots.

### WS2 — Perfis (`PlayerProfilePage.tsx`, `GuildProfilePage.tsx`) — **FEITO (jul/2026)**

Headers dos dois perfis → `Panel` (o do jogador perdeu o `overflow-hidden`
— banner inset-0 preenche exato com radius 0, não precisa clipar). Seletor
7d/30d/all da guilda → `dash-chip`. Remap de tema adicionado no fim dos
blocos `[data-profile-theme]` do styles.css (5 temas, `--gold`/`--gold-soft`).
Verificado: com `data-profile-theme="blue"`, `--gold` computa `#3b82f6`
(cantos/links v2 seguem o tema). Obs: chips ativos (`dash-chip-on`) usam
dourado literal e NÃO seguem o tema — mudar exigiria mexer na cor aprovada
da home; fica como está até pedido.

### WS3 — Highscores + PlayerLookup — **FEITO (jul/2026)**

Só `dash-root` no raiz de cada uma — são páginas de lista (invariante 1
proíbe Panel em linhas de ranking/resultado). A regra do root esquadra tudo.

### WS4 — Páginas de guilda — **FEITO (jul/2026)**

`dash-root` nos raízes de EventsPage, comp/CompList, comp/CompEditor (o
antigo CompBuilder virou master-detail), CraftCalculator, ManagementPage,
GuildConfig e EscalacaoPage. GuildConfig: as 4 seções Tailwind → `Panel`
("painel de configurações" do plano). Páginas baseadas em `.card` (comps,
eventos, escalação, management) ficaram SÓ com dash-root: `.card` esquadrado
já lê como painel v2, e trocar por Panel conflita com o cascade
(background/borda dos dois) e com o `overflow-x: hidden` do `.card` (clipa
os cantos) — se um dia quiser cantos respirando nelas, resolver isso antes.
A regra de esquadramento ganhou `.rounded-md` (34 usos no código; antes só
rounded/-lg/-xl). Craft verificado por screenshot; páginas atrás de login
verificadas por typecheck + navegação public-side (sem sessão no headless).

### WS5 — Shell — **FEITO (jul/2026)**

`dash-root` movido das 15 raízes de página pro wrapper de conteúdo do App
(`App.tsx`, div em volta do ErrorBoundary — topbar/banner ficam fora, grid
cobre todas as páginas incluindo as fora dos workstreams). Esquadramento
virou GLOBAL: `--radius`/`--radius-sm` = 0 no `:root` (knob comentado) e a
regra Tailwind promovida pra `:root :is(.rounded, ...)` — cobre dropdowns
do topbar e modais position:fixed (CropModal) que não herdariam de um
wrapper. Literais do shell zerados: `.brand .logo`, `.nav-guild-box`,
`.login-card .logo-big`, `.guild-picker-card`, `.color-picker-popup`.
Micro-raios ≤5px em ícones/barras (4px em imagens de item etc.) mantidos —
mesmo critério aceito nos WS1–4. Verificado por probe CDP (--radius 0px,
dropdown 0px, grid no wrapper, varredura de radius visível = vazia em
home/battles/players/highscores/craft/login-gate) + screenshots + typecheck.

## Invariantes (não renegociar sem atualizar este doc)

1. `Panel` = containers de seção; itens de lista nunca.
2. `rounded-full` nunca é esquadrado.
3. Brilho dos cantos: alpha máx 0.05 — não aumentar sem pedido explícito.
   Animações novas: CSS puro (opacity/transform), nunca timers JS.
4. Grid de fundo só em raiz de página.
5. Accent = `var(--gold)`; temas remapeiam a VAR, nunca criar novos
   overrides de classe.
6. `prefers-reduced-motion` sempre respeitado (media query no CSS do brilho —
   replicar em qualquer animação nova).

## Ordem

WS0 primeiro (bloqueia todos). WS1–WS4 paralelos. WS5 só no final.

**Status jul/2026: WS0–WS5 feitos — plano concluído.** Páginas atrás de
login (Regear, LootLog, Roles, BotDocs) verificadas por typecheck +
navegação public-side, mesmo critério do WS4; se alguma delas parecer
estranha com o grid de fundo, o wrapper é o lugar de olhar (`App.tsx`).
