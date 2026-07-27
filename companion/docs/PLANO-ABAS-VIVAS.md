# Plano — Abas vivas + Rota limpa (jul/2026)

> **Pra quem retomar sem contexto:** este é O documento. Leia inteiro antes de
> mexer. Cada fase tem checkbox — marque `[x]` AO CONCLUIR cada uma (edite este
> arquivo!), pra próxima sessão saber exatamente onde parou. Verificação é por
> harness (receita no fim), NUNCA "parece certo no código".

## O pedido do dono (19/07/2026, literal)

1. **Abas vivas**: as abas mostram informação AO VIVO mesmo sem foco (a ideia
   existia e não foi implementada na primeira passada do sistema de abas).
2. **Damage meter e lootlog ligados por padrão** (hoje nascem off no config).
3. A página inicial (Rota/Túnel) tinha "informações de outras tabs que não
   parecem muito bem feitas" — o rail com mini-damage e lootlog inteiro.
   **Túnel é o foco principal do programa**; o resto sai da frente.
4. Liberdade de design ("pode fazer o que quiser com isso") — mas o plano tem
   que estar completo ANTES, pra sobreviver a corte de contexto.

## Visão

O tab strip vira o monitor de relance: cada aba carrega seu número ao vivo.
Quem está na aba Rota vê o dano subir na própria aba Damage — o rail de
mini-painéis fica redundante e MORRE. A Rota fica 100% túnel: hero maior,
gráfico mais alto, e um painel "Conexão" enxuto no rail direito (só dados
reais do túnel) + ad 300×250.

```
┌──────────────────────────────────────────────────────────────┐
│ Z ZIGGS · SESSION 00:12 · PACKETS 834/s      ● PouLyD · ⚙   │  ← ck-bar (sem chips de loot/damage)
├──────────────────────────────────────────────────────────────┤
│ ROTA/TÚNEL 38ms ▲ │ DAMAGE 1.6M │ LOOTLOG 3                  │  ← abas VIVAS
├──────────────────────────────────────────────┬───────────────┤
│  39 ms   direct 58   gain −19 (33%)  [OFF]   │ CONEXÃO       │
│  Você ─────── Ziggs VPS ─────── Albion       │ vps 198.51…   │
│  [gráfico túnel×direto, ALTO (140px)]        │ tráfego ↑↓    │
│  [ad 728×90]                                 │ split/fallback│
│                                              │ [Configurar]  │
│                                              │ [ad 300×250]  │
├──────────────────────────────────────────────┴───────────────┤
│ ROTA 39ms ▲ · QUEUE 12 · AODP ●                v0.1.0 · zona │  ← ck-strip (fica)
└──────────────────────────────────────────────────────────────┘
```

## Decisões FECHADAS (não re-decidir)

- **Fonte dos números das abas = estado do App()**, nunca de componente de
  aba (aba desmontada não pode alimentar o próprio badge):
  - Rota: `tunnelStatus.tunnel_latency_ms` (poll 5s já existente) + `▲` verde
    quando `using_tunnel`.
  - Damage: campo NOVO `sniff_stats.damage_total` (ver Fase 1) — evita um
    segundo poll de `get_damage_meter`, cuja formatação é cara (timeline densa
    por jogador; ver CLAUDE.md).
  - Lootlog: `sniff_stats.loot_count` (já existe).
- **`damage_total` é somado NA LEITURA** (`get_sniff_stats` no lib.rs soma o
  mapa `sniffer.damage`), não no hot loop de pacotes. Mapa `damage`, não
  `damage_vs_players`. `clear_damage_meter` já zera os mapas → o badge zera
  sozinho. Toggle off → badge mostra "off".
- **Chips DAMAGE e LOOT saem do header** (viraram o badge da aba — duplicado é
  ruído). SESSION e PACKETS ficam. `dmgTotal`/`onTotal` morrem.
- **`DamageRail` morre. `LootlogTab` sai do rail da Rota** (vive só na própria
  aba). Com eles morrem: `.ck-dmgrail`, `.ck-railtotal`, `.ck-mini*`,
  `.ck-openbtn` e os aliases `.ck-mini.w-*` dos 17 seletores de família
  (voltam a ser só `.dmg-entry.w-*` — ATUALIZAR a nota no CLAUDE.md que
  manda incluir componentes novos nessa lista).
- **Histórico do gráfico do túnel sobe pro App()** (hoje é useState do
  TunnelHero, que zera no unmount). App acumula no poll de 5s existente e
  passa `hist` como prop. Com isso o hack `.ck-hide` (grade sempre montada)
  MORRE: toda aba monta/desmonta condicionalmente, simples.
  - Custo aceito: filtros do DamageTab (party only, min damage) resetam ao
    trocar de aba. Se incomodar, subir esses states depois — NÃO adiar o plano
    por isso.
- **Defaults ON no Rust** (`config.rs`): `collect_damage_meter` e
  `collect_auto_lootlog` = `true` no `impl Default` E `#[serde(default =
  "default_true")]` nos campos (config antigo sem o campo também vira on).
  Config JÁ SALVO com false continua false — é escolha do usuário, não mexe.
- **Rota = 2 colunas**: hero do túnel (flex) + rail direito 320px com painel
  "Conexão" + ad 300×250. As métricas `ck-tun-metrics` (tráfego/split/
  fallback/erro) SAEM do hero e viram linhas do painel Conexão — o hero fica
  só com: números grandes, botão, hops, gráfico (height 90→140; o SVG tem
  preserveAspectRatio="none", basta mudar o atributo height), ad strip.
  - Botão "Configurar VPS" no painel Conexão abre o modal ⚙ (mesmo
    `onConfigure`). Sem VPS: painel em modo aguardando (mesma regra de sempre:
    **o mockup mente, o produto não** — nenhum número inventado).
- **i18n**: chave nova `ckConn` ("Conexão"/"Connection"/"Conexión"). Rótulos
  do painel reusam chaves existentes (`traffic`, `ckSplit`, `ckSplitOnly`,
  `ckFallback`, `ckFallbackArmed`, `ckVpsWaiting`, `ckConfigure`). Badge
  "off" pode ser literal minúsculo (igual nos 3 idiomas). Depois de matar o
  DamageRail, grep `ckOpen|ckRailtotal|ckPartyDmg` — chave que ficou sem uso,
  deletar das 3 línguas.
- **Regras da casa continuam valendo**: identidade war-room (nada de cor/raio
  novo), animação só opacity/transform/width ≤0.7s, hook novo no App() vai
  ANTES do early-return da splash (React #310), ads hachurados nos mesmos
  dois formatos (728×90 e 300×250) em TODAS as abas.

## Fases (marcar [x] ao concluir — EDITAR ESTE ARQUIVO)

### Fase 1 — Rust: defaults ON + damage_total — **FEITA**
- [x] `config.rs`: `collect_damage_meter`/`collect_auto_lootlog` → `true` no
      Default + `#[serde(default = "default_true")]`; comentário atualizado
      (teste existente não assume false, passou intacto).
- [x] `sniffer.rs`: campo `damage_total: u64` no `SniffStats` (+ default 0).
- [x] `lib.rs` `get_sniff_stats`: clona stats, soma `damage.lock().values()
      .map(|a| a.damage).sum::<f64>() as u64` (o campo do acc é `damage: f64`).
- [x] `cargo check` limpo + `cargo test --lib config` (2 passed).

### Fase 2 — App: abas vivas + limpeza do header — **FEITA**
- [x] `SniffStats` (tipo TS) ganha `damage_total: number`.
- [x] Badges nas abas: Rota = `{lat}ms` + `▲` verde se `using_tunnel` ("—" sem
      VPS); Damage = `fmtC(damage_total)` ou "off"; Lootlog = `loot_count` ou
      "off". CSS `.ck-tab-val` (número em peso 600, sem uppercase; cor muted →
      text na aba ativa).
- [x] Header: chips LOOT e DAMAGE removidos; estado `dmgTotal` removido.
- [x] Hoist do histórico do gráfico: `hist` acumulado no poll de 5s do App,
      prop de TunnelHero. `.ck-hide` removido — abas montam condicionalmente.
- [x] `DamageRail` deletado; `LootlogTab` fora do rail (vive só na aba).

### Fase 3 — Rota só-túnel — **FEITA**
- [x] TunnelHero: `ck-tun-metrics` removido; gráfico height 140. BÔNUS
      descoberto na verificação: o empty do gráfico agora checa
      `hist.some(h => h.d != null || h.tn != null)` — sem VPS o hist enchia
      de amostras nulas e `length < 2` deixava uma CAIXA VAZIA no lugar do
      estado "medindo".
- [x] `ConnPanel` no rail: linhas VPS/tráfego/split/fallback (+erro), botão
      Configurar, modo aguardando sem VPS (texto reusa `tunnelSoonDesc`).
- [x] Rail: ConnPanel (flex:1, empurra o ad pro pé) + AdSlot 300×250.
- [x] CSS: `.ck-conn-rows/-row/-cfg`; deletados `.ck-dmgrail`/`.ck-railtotal`/
      `.ck-mini*`/`.ck-openbtn` e aliases `.ck-mini.w-*` (via sed).
- [x] i18n: `ckConn` nas 3 línguas; órfãs `ckDamage`/`ckOpen` deletadas.

### Fase 4 — Verificação + docs — **FEITA**
- [x] `npm run build` limpo (2 builds; o 1º pegou comentário JSX dentro de
      braço de ternário — não repetir).
- [x] Harness: 4 screenshots (rota, damage, loot, `?vps=0`) conferidos por
      Read. Badges vivos nas abas sem foco ✓, rota sem mini-damage/loot ✓,
      ads nas 3 abas ✓, aguardando sem número inventado ✓. ATENÇÃO: o
      `vite build` APAGA `dist/harness.html` — recriar com os hashes novos
      a cada build; o stub de `tunnel_status` precisa devolver latência null
      quando `?vps=0`, senão a verificação mente.
- [x] CLAUDE.md: parágrafo COCKPIT reescrito (abas vivas, Conexão, defaults
      ON, seletores w-* sem .ck-mini); nota de superação no
      PLANO-DESIGN-COMPANION.md Fase 6.
- [x] Checkboxes marcados + Status atualizado.

## Receita do harness (verificação SEM rebuildar o Tauri)

1. `cd companion && npm run build` (gera `dist/`).
2. Criar `dist/harness.html`: cópia do `dist/index.html` com, ANTES do script
   do bundle: (a) `<style>*{animation-duration:0s!important;animation-delay:
   0s!important;transition-duration:0s!important}</style>` — sem isso o
   headless congela fade no 1º frame e sai fantasma; (b) stub
   `window.__TAURI_INTERNALS__ = { transformCallback: () => 1, invoke: (cmd)
   => Promise.resolve(handlers[cmd]?.() ?? null) }` com dados realistas pra:
   `get_config` (tunnel_endpoint preenchido; "" pra variante aguardando),
   `get_sniff_stats` (incluir `damage_total`!), `tunnel_status` (latências com
   ruído pra polyline aparecer), `pending_count`, `get_damage_meter`,
   `get_captured_loot`, `get_sniffer_debug`, `get_active_events`; (c) suporte
   a `?tab=damage|loot` que clica no `.ck-tab` certo após o mount (retry de
   100ms até o botão existir).
3. Servir: `backend/scripts/python.exe -m http.server 4173` DENTRO de dist/.
4. Screenshot: `"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
   --headless --disable-gpu --window-size=1280,800 --virtual-time-budget=16000
   --screenshot=SAIDA.png "http://localhost:4173/harness.html?tab=..."`.
5. LER os PNGs (tool Read) e conferir de verdade. Matar o server e apagar o
   harness.html no fim (não é commitado; asset hash muda a cada build).

## Status

**CONCLUÍDO em 19/07/2026** — as 4 fases executadas e verificadas por harness
na mesma sessão que escreveu o plano. Pendências FORA deste plano:
- Rebuild do Tauri (`npm run tauri build`) pra ver movimento e badges no app
  real — screenshot não captura animação nem o poll de verdade.
- Se os filtros do DamageTab resetando na troca de aba incomodarem o dono,
  subir `partyOnly`/`vsPlayers`/`minDamage` pro App (decisão já aceita como
  custo, ver "Decisões FECHADAS").
