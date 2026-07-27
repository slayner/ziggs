# Plano — Identidade v2 "war room" no Companion

Transferir a linguagem visual do site (aprovada em jul/2026, ver
`docs/PLANO-DESIGN-V2.md` na raiz) pro app Tauri. Mesma regra de lá: os
valores vêm do site, **não se inventa** — é transferência, não redesign.

## O que define a identidade (resumo do doc do site)

- Cantos retos absolutos; só `rounded-full` (dots, badges, toggle) é redondo.
- Painéis opacos em gradiente `#14151a→#101116`, borda `#24262e`.
- **Cantos fixos TL+BR** (8px, `#52525b`) que douram no hover.
- Headers editoriais: título 11px uppercase tracking 0.14em + régua em
  gradiente `#27272a→transparent`.
- Grid técnico de fundo 36px azulado + glow dourado no topo, `center top`.
- Brilho "respirando" nos cantos (`dash-glow-breathe`, 7s, opacity pura),
  gate de hover em wrapper separado.

## Fase 1 — base CSS — **FEITO (jul/2026)**

Tudo em `companion/src/styles.css`, sem mudança de markup:

- `.card` → painel v2 (gradiente, borda, cantos TL/BR com hover dourado).
  `box-shadow` antigo removido — painel v2 é flat.
- `.card h2` e `.card-head` → header editorial com régua. A régua nasce de
  `::after` + `order` pra não tocar nos ~15 call sites; dentro de
  `.card-head` o `h2::after` é anulado pra não duplicar régua.
- `.content` → grid técnico + glow (valores exatos do `.dash-root`).
- Botões: borda doura no hover (regra da casa: "doura no hover") +
  `:focus-visible` com outline dourado.
- **Movimento** (pedidos de jul/2026):
  - `.dmg-bar` / `.dmg-skill-bar`: `transition: width 0.7s` — a barra desliza
    a cada poll de 2s em vez de teleportar.
  - `.dmg-tl-svg rect`: `transition: height/y 0.5s` — timeline dos 3 min
    suaviza entre polls. Limite conhecido: barra que nasce/zera aparece seca
    (o rect é condicional no JSX); cobrir exigiria renderizar os 180 sempre.
  - Toggle: squish do knob no press (estica 16→20px, translateX compensa) +
    halo verde no estado on.
- Config: card "calibração" removido da UI (ajuste é nosso, via
  `config.json`; campo e `set_config` continuam vivos).

Verificação: screenshot via harness (servir `dist/` + stub de
`__TAURI_INTERNALS__` + Edge headless — receita na sessão de jul/2026,
`dist/harness.html` é gerado na hora e não é commitado). Conferido: grid,
painel, header editorial, régua, toolbar. **Movimento não é verificável em
screenshot** — conferir no app real.

## Fase 2 — brilho respirando — **FEITO (jul/2026)**

Em vez do `Card.tsx` planejado, saiu mais barato: componente `CardGlow`
(App.tsx) injetado como PRIMEIRO FILHO dos 8 `<div className="card">` — só
adiciona uma linha por call site, sem casar tag de fechamento. Markup e CSS
espelham o `Panel.tsx`/`dash-cglow*` do site verbatim (classes com o MESMO
nome, pra diff futuro contra o site ser trivial); gate de hover adaptado pra
`.card:hover`. Detalhe que importa: delays em `useState` — os cards
re-renderizam a cada poll de 2s, e sortear no render faria a fase do brilho
pular (mesma razão do site). `prefers-reduced-motion` portado.
Verificado por screenshot com hover forçado via CSS injetado (cantos douram,
glow presente); a respiração em si só no app real.

## Fase 3 — refinos

1. **FEITO em parte:** régua editorial no brand da sidebar
   (`.sidebar-brand::after`). `nav-btn` ativo MANTÉM a barra 2px: canto
   dourado em item de navegação não existe no site (a regra "não inventa"
   decide).
2. **FEITO:** `.seg-btn.active` portado pro dourado do site
   (`gold-soft`/`gold`/600 — era `surface-2` cinza, gap real de identidade).
   Inputs NÃO mudaram: o site foca com `--info`/`border-strong`, não dourado
   — o plano assumia errado, o código do site é a autoridade.
3. **DESCARTADO:** o site não tem scrollbar custom (grep `-webkit-scrollbar`
   vazio) — não inventar.
4. **DESCARTADO (YAGNI):** slide direcional no tab switch exigiria estado de
   direção pra ganho marginal; o `tab-fade` atual já é o padrão fade do site.
5. **FEITO:** empty states. Descoberta que mudou o item: o site NÃO tem
   empty "editorial" (o Dashboard nem tem empty state) — a autoridade são
   `.market-empty` (vazio de área, centrado) e `.mover-empty` (inline).
   Portados como `.empty-area`/`.empty-inline` e aplicados nos 4 vazios
   (damage off, damage sem dados, lootlog off, timeline vazia). Verificado
   por screenshot.

## Fase 4 — COCKPIT (redesign real, jul/2026) — **FEITO**

O dono revogou a regra "transferência, não redesign" ("esperava mais, você
só adicionou uma skin") e aprovou por mockup um layout de outra categoria:
o companion é um MONITOR, não um app de configurações. Sem abas, sem
sidebar — uma tela: barra de comando, Damage como herói, loot rail em altura
total, strip de status, Config+Túnel em modal no ⚙. (O painel Viagens que
existiu por algumas horas caiu junto com a feature de city-markers — decisão
de 19/07.) A linguagem war-room (cantos, réguas, brilho, grid) continua —
o que mudou foi a ESTRUTURA. Fluxo que funcionou: mockup HTML estático →
screenshot → aprovação → só então mexer no app. Verificado por harness com
dados realistas.

## Fase 5 — Túnel como tela principal + ads (jul/2026) — **FEITO**

Reposicionamento de produto pedido pelo dono: o companion se apresenta como
OTIMIZADOR DE ROTA (estilo ExitLag); damage e lootlog viram features do rail.
Hero: latência gigante + ganho, hops Você→VPS→Albion, gráfico túnel×direto
(amostras acumuladas no cliente do poll de 5s), métricas reais (tráfego,
split, fallback). Regra dura da fase: **o mockup mente, o produto não** —
nada de per-leg/jitter/uptime que o Rust não mede; sem VPS o hero renderiza
o estado aguardando com o botão levando à config. Damage completo abre em
modal. Dois slots de ad hachurados (728×90 hero, 300×250 rail) na linguagem
de placeholder do site, prontos pro criativo — sem rede de ads embutida.
Verificado por harness; pegou na hora o bug das cores de família (seletores
--wfam não cobriam .ck-mini — corrigido estendendo os 17 seletores).

## Fase 6 — abas com foco no túnel (jul/2026) — **FEITO**

Pedido do dono depois da Fase 5: sistema de ABAS, com Rota/Túnel como foco.
Três abas na strip sob a barra de comando: **Rota/Túnel (default)**, Damage
Meter e Lootlog. O que mudou de verdade:

- O modal de Damage morreu — o meter completo é a aba. O "abrir ▸" do rail
  troca de aba em vez de abrir modal. Config continua modal no ⚙.
- A grade da Rota (hero + rail) fica **sempre montada**, escondida via
  `.ck-hide` quando outra aba está ativa: o gráfico túnel×direto acumula
  histórico em estado do `TunnelHero` e os chips do header vêm do poll do
  rail — desmontar zeraria os dois. Custo aceito: os polls do rail continuam
  rodando em qualquer aba (já era o comportamento com o modal aberto).
- **Ads em toda aba:** os dois slots da Rota (728×90 no hero, 300×250 no
  rail) continuam, e as abas Damage/Lootlog ganham um strip 728×90 no rodapé
  (`.ck-full .ck-ad-strip`) — aba sem ad perderia impressão justamente onde o
  usuário passa o CTA.
- Aba ativa = texto dourado + barra 2px (padrão de nav da casa; canto dourado
  em item de navegação não existe no site). Lootlog é nome próprio, igual nos
  3 idiomas — rótulo literal, sem chave i18n.
- Lootlog em aba cheia libera o terminal do `max-height: 360px`
  (`.ck-full .terminal`).

Verificado por harness (3 screenshots, um por aba — o stub aceita
`?tab=damage|loot` e clica na aba após o mount).

> **Evoluída no mesmo dia por `PLANO-ABAS-VIVAS.md`** (documento vigente):
> abas ganharam badge ao vivo, o rail da Rota trocou mini-damage/lootlog por
> um painel Conexão, `.ck-hide` morreu (hist do gráfico subiu pro App) e os
> toggles de coleta nascem ligados. Detalhes de rail/montagem descritos NESTA
> fase estão superados.

## Status: PLANO CONCLUÍDO (jul/2026)

Tudo que era executável foi feito ou descartado com razão registrada. O que
resta é externo ao plano: rebuildar o Tauri e conferir o MOVIMENTO no app
real (respiração, squish, barras deslizando) — screenshot não captura.

## Regras pra quem continuar

- Cor nova, raio novo, sombra nova: **não**. Se não está no site, não entra.
- Animação: só `opacity`/`transform`/`width` (GPU/layout barato), sempre com
  duração ≤ 0.7s e `prefers-reduced-motion` quando for ambiente.
- Verificar por screenshot do harness ANTES de rebuildar o Tauri — o ciclo é
  10x mais curto.
- **Pegadinha do harness:** sob `--virtual-time-budget` o headless pode
  congelar `fade-in`/`tab-fade` no primeiro frame e o conteúdo sai fantasma
  (opacity ~0). NÃO é bug do app — injete
  `*{animation-duration:0s!important}` no harness antes de concluir qualquer
  coisa sobre um screenshot apagado.
