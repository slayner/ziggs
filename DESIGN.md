---
name: Ziggs
description: Central operacional para guildas de Albion Online.
colors:
  steel-shadow: "#0e0f13"
  operational-surface: "#161821"
  raised-surface: "#1d2029"
  technical-border: "#2a2e3a"
  strong-border: "#3a3f4d"
  signal-text: "#e7e9ee"
  muted-text: "#9aa1b0"
  hint-text: "#6b7280"
  instrument-brass: "#d4a338"
  brass-wash: "#2a2417"
  information: "#5b8def"
  information-wash: "#16223d"
  discord: "#5865f2"
  success: "#4ade80"
  danger: "#f87171"
typography:
  headline:
    fontFamily: '"Segoe UI", system-ui, -apple-system, sans-serif'
    fontSize: "22px"
    fontWeight: 700
    lineHeight: 1.1
  title:
    fontFamily: '"Segoe UI", system-ui, -apple-system, sans-serif'
    fontSize: "16px"
    fontWeight: 700
  body:
    fontFamily: '"Segoe UI", system-ui, -apple-system, sans-serif'
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: '"Segoe UI", system-ui, -apple-system, sans-serif'
    fontSize: "11px"
    fontWeight: 700
    letterSpacing: "0.14em"
  technical:
    fontFamily: "ui-monospace, monospace"
    fontSize: "10px"
    fontWeight: 600
rounded:
  square: "0px"
  asset: "4px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-standard:
    backgroundColor: "transparent"
    textColor: "{colors.signal-text}"
    rounded: "{rounded.square}"
    padding: "8px 14px"
  button-primary:
    backgroundColor: "{colors.instrument-brass}"
    textColor: "#1a1406"
    rounded: "{rounded.square}"
    padding: "8px 14px"
  button-discord:
    backgroundColor: "{colors.discord}"
    textColor: "#fff"
    rounded: "{rounded.square}"
    padding: "11px"
  input:
    backgroundColor: "{colors.operational-surface}"
    textColor: "{colors.signal-text}"
    rounded: "{rounded.square}"
    padding: "8px 11px"
  filter-chip-active:
    backgroundColor: "{colors.brass-wash}"
    textColor: "{colors.instrument-brass}"
    rounded: "{rounded.square}"
    padding: "3px 9px"
  card:
    backgroundColor: "{colors.operational-surface}"
    textColor: "{colors.signal-text}"
    rounded: "{rounded.square}"
    padding: "16px 18px"
  panel-header:
    textColor: "{colors.signal-text}"
    typography: "{typography.label}"
    padding: "12px 14px"
---

# Design System: Ziggs

## Overview

**Creative North Star: "Mesa de comando"**

Ziggs é uma interface operacional escura para acompanhar, decidir e agir durante a vida de uma guilda. A referência não é luxo, fantasia ornamental ou uma landing page de conversão: é uma mesa de comando técnica, onde dados de Albion, atividade de membros e controles de administração precisam ficar legíveis no mesmo campo de visão.

A profundidade vem de camadas tonais, linhas estruturais e gradientes discretos sobre uma malha de planta técnica. O latão de instrumento marca seleção, decisão e atividade; azul, verde e vermelho são sinais semânticos. Controles industriais e austeros mantêm a interface seca e precisa, enquanto pequenas respostas de hover dão vida sem competir com a informação.

O Companion traduz a mesma família para um cockpit desktop mais denso; a superfície web é a autoridade deste documento. Em ambas, a interface privilegia contexto, números tabulares e estado operacional em vez de ilustração decorativa.

**Key Characteristics:**
- Painel escuro e técnico, com malha discreta e superfícies em camadas.
- Acento de latão usado como instrumento de prioridade, nunca como acabamento luxuoso.
- Controles compactos, quadrados e legíveis sob alta densidade de dados.
- Estados semânticos explícitos, com cor acompanhada de texto, ícone ou posição.
- Movimento curto, funcional e reduzível para quem prefere menos animação.

## Colors

A paleta separa estrutura tonal de sinal operacional: a maior parte da tela é construída por neutros; o acento e os estados aparecem com intenção.

### Primary

- **Latão de instrumento** (`instrument-brass`): seleção, ação primária, foco de prioridade, títulos de contexto operacional e bordas ativas.
- **Lavagem de latão** (`brass-wash`): fundo contido para seleção, foco e sinalizações do acento.

### Secondary

- **Azul de informação** (`information`): links, foco de campos, seleção informativa e dados que pedem consulta.
- **Lavagem de informação** (`information-wash`): fundo de opções e estados informativos selecionados.
- **Discord de conexão** (`discord`): somente para a relação explícita com Discord e seu login.

### Tertiary

- **Verde de confirmação** (`success`): conclusão, atividade saudável, presença e valores positivos.
- **Vermelho de atenção** (`danger`): erro, remoção, recusa, alerta e valores negativos.

### Neutral

- **Aço em sombra** (`steel-shadow`): fundo global que sustenta a malha técnica.
- **Superfície operacional** (`operational-surface`): cartões, barras e camadas de trabalho.
- **Superfície elevada** (`raised-surface`): hover, menus, campos e separação de subcamadas.
- **Borda técnica** (`technical-border`) e **borda forte** (`strong-border`): divisão de dados, contorno de controles e reforço de estado.
- **Texto de sinal** (`signal-text`), **texto mutado** (`muted-text`) e **texto auxiliar** (`hint-text`): hierarquia de leitura sem introduzir cores decorativas.

**The Latão de Instrumento Rule.** Use o acento para indicar decisão, seleção, prioridade ou atividade; não o distribua como ornamento de fundo nem o deixe competir com sinais de sucesso, informação e perigo.

## Typography

**Display Font:** não há face de display separada; títulos usam a família operacional do sistema.
**Body Font:** Segoe UI com fallbacks de sistema.
**Label/Mono Font:** Segoe UI para rótulos; ui-monospace para índices, auditoria, tempos e números técnicos.

**Character:** a tipografia é utilitária, contemporânea e compacta. A hierarquia vem de peso, caixa, espaçamento e cor — não de famílias concorrentes.

### Hierarchy

- **Headline:** reservado a heróis de gestão e cabeçalhos de área; mantém leitura rápida sem transformar a aplicação em editorial promocional.
- **Title:** identifica workspaces, cartões e agrupamentos de informação.
- **Body:** texto padrão da aplicação, com entrelinha suficiente para explicações e dados contextuais.
- **Label:** rótulos curtos em caixa alta e tracking aberto para painéis, metadados e grupos de controle.
- **Technical:** mono compacto para índices de navegação, consoles de auditoria, horários, latência e valores que precisam alinhar visualmente.

**The Evidência em Primeiro Lugar Rule.** Use a caixa alta espaçada para rotular sistemas e dados; deixe nomes de pessoas, guildas e explicações em caixa normal para preservar escaneabilidade.

## Layout

A estrutura começa com topbar e uma área de trabalho flexível. A raiz operacional usa uma malha de 36px e um halo superior quase imperceptível; ela deve ficar no fundo, nunca reduzir contraste do conteúdo.

A área pública usa container de até 1024px com respiro lateral. Superfícies específicas ampliam somente quando a tarefa exige: gestão chega a 1180px, mercado a 1000px, Companion público a 960px e páginas legais a 820px. Painéis e listas preservam densidade de dashboard com divisores, linhas curtas e números tabulares; não use grandes vazios de landing page em fluxos de operação.

A gestão combina rail editorial de 248px e workspace. Em 900px, a topbar quebra e o rail passa a uma grade de duas colunas; em 600px, ele vira uma coluna. O board de escalação preserva a faixa horizontal de parties: abaixo de 720px o rail se move para cima, mas a faixa continua rolável horizontalmente em vez de ser forçada a uma grade. Detalhes de composição passam a uma coluna abaixo de 719px; a página pública do Companion simplifica sua grade em 640px.

## Elevation & Depth

A interface é plana em repouso e profunda por camadas tonais. Fundos, superfícies, linhas e gradientes verticais estabelecem hierarquia antes de qualquer sombra. Sombras aparecem em menus, dropdowns e overlays para separar conteúdo temporário; não são a linguagem normal dos cartões.

O painel de assinatura combina superfície escura em gradiente, borda fina e dois cantos técnicos opostos. No hover, esses cantos respondem com o acento e um brilho radial muito baixo pode respirar atrás do conteúdo. A resposta é contextual — ela não deve ficar permanentemente ligada.

### Shadow Vocabulary

- **Menu flutuante:** sombra curta e difusa para dropdowns e seletores sobre a superfície operacional.
- **Alerta de status:** sombra mais profunda para o painel de instabilidade, que precisa superar a topbar sem parecer modal.
- **Overlay modal:** véu escuro de alta opacidade, sem elevar cartões comuns fora do contexto de diálogo.

**The Repouso Plano Rule.** Em estado normal, crie hierarquia com superfície, borda e gradiente; reserve sombra e brilho para conteúdo flutuante, hover, foco ou mudança de estado.

## Shapes

A linguagem canônica é esquadrada. Superfícies, botões, campos, menus, trilhos e chips de filtro usam o raio quadrado, reforçado por uma regra global que remove arredondamento das utilidades Tailwind usuais. Bordas finas e cantos de painel transformam o retângulo em instrumento técnico, não em cartão genérico.

Pílulas, avatares, pontos de status, contadores e toggles são exceções funcionais: use a forma circular ou cápsula quando ela expressa identidade, quantidade, continuidade ou um estado compacto. Ícones e renders de item podem usar o pequeno raio de asset para conter bitmap de jogo sem alterar a silhueta dos controles.

**The Interface Quadrada Rule.** Não introduza cartões arredondados por padrão; cada exceção arredondada precisa comunicar uma função que a forma quadrada não comunica.

## Components

### Buttons

- **Shape:** controles retangulares compactos, com borda técnica e tipografia operacional.
- **Primary:** o botão de ação usa o acento de instrumento com texto escuro e peso maior; empregue para a decisão principal do contexto, não para toda ação disponível.
- **Standard / ghost:** fundo transparente, contorno forte e preenchimento tonal somente no hover; é a variante para ações secundárias e utilitárias.
- **Discord:** a variante de integração reserva a cor própria do Discord e largura integral no login.
- **Hover / focus:** hover muda a camada ou o contorno; pressão comprime levemente o controle. Preserve sempre um indicador visível de foco de teclado.

### Chips

- **Style:** filtros operacionais são pequenos, quadrados, com borda e tracking discreto; chips de estado podem ser cápsulas quando a leitura compacta é a função dominante.
- **State:** filtros ativos combinam lavagem e acento de latão; seleção informativa usa azul. Estados de fluxo comunicam conclusão, erro ou etapa futura sem depender apenas da cor.

### Cards / Containers

- **Corner Style:** superfícies de conteúdo seguem a forma quadrada; o painel de assinatura adiciona cantos técnicos opostos, não raio.
- **Background:** cartões comuns usam a superfície operacional; subcamadas, hover e menus usam a superfície elevada.
- **Shadow Strategy:** cartões permanecem planos; menus, popovers e diálogos usam a elevação contextual descrita acima.
- **Border:** uma borda técnica sustenta cada contêiner e pode ganhar força ou acento em hover e seleção.
- **Internal Padding:** preserve a escala compacta de cartões e grupos; densidade não deve virar aperto.

### Inputs / Fields

- **Style:** campo escuro com contorno forte, texto de sinal e forma quadrada.
- **Focus:** foco de campo muda para o sinal de informação; campos compostos usam o mesmo tratamento no contêiner por meio de foco interno.
- **Error / disabled:** perigo aparece em ações destrutivas e mensagens; desabilitado reduz ênfase e remove a aparência de ação disponível sem esconder o contexto.

### Navigation

- **Style:** topbar compacta combina marca, navegação pública, zona de guilda, status e controles de usuário. Itens inativos ficam mutados; hover restaura legibilidade; a rota ativa recebe a superfície elevada.
- **Mobile treatment:** a navegação pública pode rolar horizontalmente após a quebra da topbar, e a zona de guilda ocupa sua própria linha em telas estreitas.

### Painel de comando

- **Character:** é a assinatura visual da Ziggs para dashboards e blocos de decisão.
- **Header:** rótulo em caixa alta, régua horizontal que ocupa o espaço central e ação ou filtro no extremo direito.
- **Behavior:** cantos técnicos e brilho dourado suave aparecem apenas em interação; desative a animação pulsante quando `prefers-reduced-motion` solicitar menos movimento.

## Do's and Don'ts

### Do:

- **Do** construir hierarquia com aço em sombra, superfícies em camadas, bordas e gradientes discretos antes de adicionar sombra.
- **Do** reservar o latão de instrumento para seleção, decisão, prioridade e atividade.
- **Do** manter rótulos de sistema curtos, em caixa alta e com tracking; usar mono para dados técnicos e números tabulares quando houver comparação.
- **Do** preservar a forma quadrada em controles e cartões, usando pílulas apenas como exceção funcional.
- **Do** reduzir ou desligar brilho pulsante e entradas animadas para `prefers-reduced-motion`.
- **Do** garantir contraste e foco de teclado visível em novas superfícies para manter o piso WCAG 2.2 AA.

### Don't:

- **Don't** usar dourado como preenchimento decorativo amplo, gradiente dominante ou substituto de estado semântico.
- **Don't** transformar fluxos operacionais em cartões arredondados, espaçosos ou persuasivos de landing page.
- **Don't** usar sombra grande sob todo painel; a profundidade normal é tonal e estrutural.
- **Don't** remover texto, ícone ou posição ao comunicar sucesso, informação, perigo ou estado de evento.
- **Don't** adicionar animação contínua que concorra com tabelas, alertas e leitura de dados.
