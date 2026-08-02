# Plano — Sidebar Vertical (jul/2026)

Redesign do cockpit do companion. **Descarta** o layout de abas horizontais
(`.ck-tabs` em strip sob a barra de comando) e move a navegação pra uma
**coluna vertical fixa à esquerda**. A coluna cresce com o conteúdo ativo.

## Direção

O companion é um **monitor**, não um app de configurações. A barra vertical
reúne em um só lugar o que o usuário olha o tempo todo:

1. **Abas verticais** (Rota/Túnel, Damage, Lootlog) — toggle on/off de cada
   feature no próprio item da aba, e quando ativa a aba **cresce** e mostra
   detalhes pertinentes (latência, total de dano, nº de loots) **independente
   de qual aba está selecionada**. O badge ao vivo continua atualizando sem
   foco — dado vem do estado do `App()`, nunca do componente da aba.
2. **Indicador de jogador / mapa / Albion detectado** no **rodapé da
   sidebar** — sempre visível, independente da aba.
3. **Botão de configuração** no canto inferior da sidebar, atrelado ao
   rodapé (abaixo do indicador de status).

## Layout

```
┌──────────────┬─────────────────────────────────────┐
│  ZIGGS       │  [conteúdo da aba selecionada]      │
│              │                                     │
│  ▸ Rota      │                                     │
│   42ms ▲     │                                     │
│   [on/off]   │                                     │
│              │                                     │
│  ▸ Damage    │                                     │
│   1.2M       │                                     │
│   [on/off]   │                                     │
│              │                                     │
│  ▸ Lootlog   │                                     │
│   7          │                                     │
│   [on/off]   │                                     │
│              │                                     │
│  ─────────   │                                     │
│  ● Player    │                                     │
│    Mapa      │                                     │
│  ⚙ Config    │                                     │
└──────────────┴─────────────────────────────────────┘
```

- `.ck-shell` — `display: grid; grid-template-columns: 220px 1fr;`
- `.ck-side` — coluna esquerda: flex column, fundo `--bg`, borda direita.
- `.ck-main` — coluna direita: o que era o `<div className="ck-grid">`
  ou `.ck-full` da aba ativa.
- A topbar `.ck-bar` (logo + sessão + pacotes + Discord) **continua no
  topo** — não entra na sidebar. Acompanha o app inteiro.
- O rodapé `.ck-strip` (fila, AODP, zona) **sai** — o que importa virou
  indicador de jogador/mapa na sidebar; o resto era redundante com a
  topbar. Mantém só o que for pedido explicitamente.

## Aba vertical — anatomia

Cada aba é um `<button class="ck-side-tab">` com:

- **label** uppercase (Rota/Túnel, Damage, Lootlog)
- **valor ao vivo** (latência, total de dano, nº de loots) — badge que
  atualiza mesmo sem foco
- **toggle on/off** da feature (Damage e Lootlog têm toggle; Rota não
  tem toggle, é o hero)
- **expansível**: quando a feature está **on**, a aba cresce e mostra
  detalhes pertinentes (ganho de latência, pico de DPS, último loot).
  Quando **off**, recolhe pra uma linha.

A aba **selecionada** ganha destaque (borda esquerda dourada, fundo
levemente elevado). A expansão do detalhe é **independente** da seleção:
uma aba pode estar on e mostrando o detalhe mesmo com outra aba
selecionada — é o ponto de "mostrar dados independente de qual tab está
selecionada".

## Rodapé da sidebar

`.ck-side-status` — bloco no pé da sidebar, sempre visível:

- **status dot** do Albion (verde/lima/cinza) + nome do personagem
- **mapa atual** + zona (azul/pvp)
- se Albion fechado: "Albion fechado" + hint
- se erro de sniffer: mensagem curta

`.ck-side-gear` — botão de config no canto inferior, abaixo do status.
Abre o mesmo modal `gearOpen` que já existe.

## Topbar

Mantém `.ck-bar` no topo com: logo, sessão, pacotes, Discord, **sem** o
botão de ⚙ (sai pra sidebar). A zona (`.ck-zone`) **sai** da topbar e
vai pro rodapé da sidebar — é info de jogo, pertence ao indicador de
jogador. O LED de Albion também sai da topbar (vai pro rodapé da
sidebar); fica só sessão + pacotes + Discord.

## Banner Npcap

Sai de baixo das abas horizontais e vai **dentro da sidebar**, entre as
abas e o rodapé — só aparece quando `sniffStats.error` menciona Npcap.

## Modal de config

Inalterado — abre no ⚙ da sidebar, mesmo `ConfigTab` + `TunnelTab`.

## Cores

Tudo reuse as variáveis já existentes (`--gold`, `--green`, `--border`,
etc). A aba selecionada usa `--gold` na borda esquerda (2px) — mesmo
padrão da casa. A aba on/expandida usa `--surface-2` de fundo.

## Decisões ponytail

- **Não** criar componente novo pra cada aba da sidebar — um único
  `<SideTab>` com props (`label`, `value`, `on`, `onToggle`, `expanded`,
  `expandedContent`). Menos um arquivo, menos boilerplate.
- **Não** adicionar framework de animação de expansão — `max-height`
  transition no CSS resolve.
- **Não** mover o modal de config — só muda o botão que abre.
- **Não** adicionar novo tipo de estado — `tab` continua `"route" |
  "damage" | "loot"`, os toggels continuam em `config`.
- Rodapé `.ck-strip` antigo é **removido** — a info que importa foi pra
  sidebar, o resto era redundante. Se precisar de algo de volta, volta
  como chip na topbar.