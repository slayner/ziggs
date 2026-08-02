# Plano: Craft, Refino e Transmutacao

Status: Fases 0-6 implementadas e validadas contra planilha de referência (jul/2026). Fase 7 (Hideouts) pendente de validação em jogo. §16.1 (arredondamento de foco) implementado com Math.ceil (conservador); §16.2 (taxa de transmutação) implementado e validado — Base Cost = round(silverCost × 1.156) para enchant upgrade, round(silverCost × 1.1584) para tier upgrade, Usage Fee = round((fee/100) × itemValue × 0.1125) com itemValue float do dump.

Fontes de regras consultadas em 27/07/2026:

- Albion Online Wiki: `Resource return rate`, revisada em 01/06/2026.
- Albion Online Wiki: `Refining` e `City Resources`.
- `ao-data/ao-bin-dumps`, commit `5cf2e8e9b7021f98683181fa5b0e3c64575978e4`.

O dump do jogo e a interface atual do cliente prevalecem sobre paginas antigas da
wiki quando houver divergencia.

## 1. Objetivo

Transformar a pagina publica de Craft em uma central de producao com tres modos:

1. Craft de equipamentos e consumiveis, preservando a calculadora atual.
2. Refino de fibra, couro, minerio, madeira e pedra.
3. Transmutacao de recursos brutos.

Antes de adicionar os novos modos, corrigir a forma como o local de producao e
representado. O usuario deve selecionar a cidade real onde esta produzindo; o
bonus deve ser derivado da receita e da cidade escolhida.

## 2. Fora do escopo inicial

- Automatizar ordens no mercado.
- Inferir automaticamente onde o personagem esta no jogo.
- Usar dados enviados pelo companion como unica fonte de preco.
- Otimizar transporte entre cidades.
- Refazer toda a infraestrutura de precos do backend antes do MVP.
- Suportar hideouts no primeiro corte de Refino.

## 3. Estado atual

`frontend/src/components/CraftCalculator.tsx` concentra interface, estado,
precos, formulas e carrinho em aproximadamente 1.200 linhas.

O local de producao atual e apenas:

```ts
type CraftPlace = "city" | "island" | "hideout";
```

Quando `place === "city"`, a calculadora considera qualquer item com
`bonusCity` como especializado, sem verificar em qual cidade o usuario esta.
Isso concede bonus indevido, por exemplo, a um cajado de gelo produzido em
Thetford.

O catalogo atual ja possui `CatalogFamily.bonusCity`. Nao e necessario criar
uma segunda tabela manual item -> cidade.

## 4. Localizacao de producao

### 4.1 Modelo

Substituir o local generico por uma uniao discriminada:

```ts
type ProductionCity =
  | "Bridgewatch"
  | "Martlock"
  | "Lymhurst"
  | "Fort Sterling"
  | "Thetford"
  | "Caerleon"
  | "Brecilien";

type ProductionLocation =
  | { kind: "city"; city: ProductionCity }
  | { kind: "island"; city: ProductionCity }
  | { kind: "hideout"; quality: number; power: number };
```

`Black Market` nao e local de producao. Continua disponivel somente como
mercado de venda.

### 4.2 Regra de bonus

Para cidades e ilhas:

```ts
specialized = location.city === recipe.bonusCity;
```

Exemplo, cajados de gelo com `bonusCity = "Martlock"`:

| Local real | Bonus especializado |
|---|---:|
| Martlock | sim |
| Ilha de Martlock | sim |
| Thetford | nao |
| Ilha de Thetford | nao |

O valor do bonus depende do modo:

| Operacao | Bonus especializado |
|---|---:|
| Craft | +15 pontos |
| Refino | +40 pontos |

### 4.3 Tres cidades independentes

A pagina deve manter separados:

1. Local de producao: define bonus, retorno e taxa da estacao.
2. Mercado de compra: define o preco de cada material.
3. Mercado de venda: define o valor da saida.

Craftar em Martlock nao implica comprar ou vender em Martlock.

### 4.4 Persistencia

Persistir a ultima localizacao no `localStorage`, inclusive para usuarios
anonimos. Nao criar uma API apenas para esse valor.

Na primeira visita, exigir uma escolha explicita. Escolher automaticamente a
cidade de bonus esconderia o erro que este trabalho pretende resolver.

O carrinho deve guardar um snapshot da localizacao e do retorno no momento em
que a ordem foi adicionada. Alterar a cidade depois nao pode recalcular ordens
antigas silenciosamente.

### 4.5 Interface

O painel `Onde craftar` tera:

```text
Tipo: Cidade | Ilha | Hideout
Cidade: Martlock
Bonus local: Cajados de gelo +15%
```

Sem bonus:

```text
Cidade: Thetford
Sem bonus local
Esta receita recebe bonus em Martlock
```

O cabecalho e cada ordem do carrinho devem mostrar o nome real da cidade, nao
apenas `Cidade`.

## 5. Retorno de recursos

Converter pontos aditivos de bonus em Resource Return Rate:

```text
RRR = pontos / (100 + pontos)
```

Foco adiciona 59 pontos antes da conversao.

### 5.1 Craft em cidade

```text
base da cidade = 18
especializacao da receita = 15
foco = 59
evento = 0, 10 ou 20
```

### 5.2 Refino em cidade

```text
base da cidade = 18
especializacao do recurso = 40
foco = 59
evento = 0, 10 ou 20
```

Resultados canonicos:

| Situacao | Pontos | RRR |
|---|---:|---:|
| Cidade comum | 18 | 15,25% |
| Cidade comum com foco | 77 | 43,50% |
| Cidade de bonus de refino | 58 | 36,71% |
| Cidade de bonus de refino com foco | 117 | 53,92% |

`returnRate.ts` deve expor a conversao basica e resolvers separados para Craft
e Refino. Nao adicionar flags de refino ao resolver atual ate ele ficar
impossivel de entender.

## 6. Receitas de refino

### 6.1 Recursos normais

| Tier | Bruto atual | Refinado anterior | Saida |
|---|---:|---:|---:|
| T2 | 1 | 0 | 1 |
| T3 | 2 | 1 T2 | 1 |
| T4 | 2 | 1 T3 | 1 |
| T5 | 3 | 1 T4 | 1 |
| T6 | 4 | 1 T5 | 1 |
| T7 | 5 | 1 T6 | 1 |
| T8 | 5 | 1 T7 | 1 |

Fibra, couro, minerio e madeira mantem o encantamento entre entrada e saida.
T4 encantado usa o refinado T3 comum como material anterior.

### 6.2 Cidades de bonus de refino

| Familia | Saida | Cidade |
|---|---|---|
| Fibra | Tecido | Lymhurst |
| Couro | Couro trabalhado | Martlock |
| Minerio | Barras | Thetford |
| Madeira | Tabuas | Fort Sterling |
| Pedra | Blocos | Bridgewatch |

### 6.3 Coracoes de faccao

Coracoes, chamados de `City Resources` no dump, participam do refino. Crests
ou emblemas usados em capas nao participam.

| Coracao | Origem | Recurso substituido |
|---|---|---|
| Treeheart | Lymhurst | Madeira |
| Rockheart | Martlock | Pedra |
| Beastheart | Bridgewatch | Couro |
| Mountainheart | Fort Sterling | Minerio |
| Vineheart | Thetford | Fibra |
| Shadowheart | Caerleon | Nenhum diretamente |

O coracao substitui uma unidade do recurso bruto e mantem o material refinado
do tier anterior. Exemplos:

```text
4 minerios T6 + 1 barra T5 -> 1 barra T6
3 minerios T6 + 1 Mountainheart + 1 barra T5 -> 1 barra T6
```

Regras verificadas no dump atual:

- Alternativas com coracao existem de `.0` a `.3`.
- Nao existem alternativas com coracao para `.4`.
- No refino, o coracao e elegivel ao retorno de recursos.
- A elegibilidade ao retorno pertence ao input da receita, nao ao tipo do item.
- Shadowheart pode virar um dos cinco coracoes por Shadowheart + 5.000 prata.
- A conversao de Shadowheart e de mao unica.

O calculo deve comparar receita normal, receita com coracao comprado e coracao
obtido via Shadowheart.

## 7. Excecao da pedra

Pedra encantada nao gera bloco encantado. O encantamento multiplica a
quantidade de blocos comuns:

| Pedra bruta | Blocos anteriores | Blocos comuns produzidos |
|---|---:|---:|
| `.0` | 1x | 1x |
| `.1` | 2x | 2x |
| `.2` | 4x | 4x |
| `.3` | 8x | 8x |

Exemplo T6:

```text
4 T6.0 + 1 bloco T5 -> 1 bloco T6
4 T6.1 + 2 blocos T5 -> 2 blocos T6
4 T6.2 + 4 blocos T5 -> 4 blocos T6
4 T6.3 + 8 blocos T5 -> 8 blocos T6
```

Outras regras:

- Pedra nao possui `.4`.
- Rockheart aparece apenas na receita flat `.0`.
- O item de saida e o mesmo para `.0` a `.3`.
- O foco total da operacao acompanha `amountcrafted`.
- A UI deve dizer `bloco T6 via pedra T6.2`, nao `bloco T6.2`.

O modelo nao deve inferir essas regras por nome. Deve consumir `outputCount` e
as variantes de receita extraidas do dump.

## 8. Especializacao de refino

Cada familia possui specs separadas para T4, T5, T6, T7 e T8.

Para um tier alvo:

```text
eficiencia =
  250 * nivel da spec do tier alvo
  + 30 * soma dos niveis T4..T8 da mesma familia
```

Com todos os niveis em 100:

```text
25.000 da spec propria + 15.000 compartilhados = 40.000
```

Custo efetivo estimado:

```text
foco = foco base * 0,5 ^ (eficiencia / 10.000)
```

Manter float internamente e usar arredondamento conservador ao comparar com um
orcamento de foco. O arredondamento exato do cliente ainda deve ser validado.

As specs podem ser persistidas em `User.craft_settings`, sem migration:

```json
{
  "refining_specs": {
    "fiber": { "4": 100, "5": 80, "6": 50, "7": 0, "8": 0 }
  }
}
```

## 9. Transmutacao

Transmutacao opera apenas sobre recursos brutos:

- Conversao 1:1.
- Sobe exatamente um tier ou um nivel de encantamento por operacao.
- Nao desce tier ou encantamento.
- Nao troca a familia do recurso.
- Nao usa foco.
- Nao tem retorno de recursos.
- Coracoes e crests nao participam.
- Pedra termina em `.3` e usa custos proprios.

O custo em prata deve vir do dump, nao de uma tabela escrita manualmente.

Representar as conversoes como um grafo direcionado aciclico. Para um alvo, um
percurso simples encontra a opcao mais barata entre compra direta e todas as
rotas validas de transmutacao.

Exemplo para T6.2:

```text
comprar T6.2
T5.2 -> T6.2
T6.1 -> T6.2
T5.1 -> T6.1 -> T6.2
```

## 10. Dados gerados

Nao manter receitas manualmente. Criar um gerador, seguindo o padrao dos seeds
existentes, que leia `items.xml` e `achievements.xml` do dump e produza:

```text
frontend/public/data/refining.json
```

Estrutura proposta:

```ts
interface RefiningRecipe {
  key: string;
  family: "fiber" | "hide" | "ore" | "wood" | "stone";
  tier: number;
  enchant: number;
  outputId: string;
  outputCount: number;
  itemValue: number;
  baseFocus: number;
  baseFame: number;
  variants: RefiningVariant[];
}

interface RefiningVariant {
  kind: "normal" | "heart";
  inputs: {
    itemId: string;
    count: number;
    returnable: boolean;
  }[];
}

interface TransmutationRecipe {
  sourceId: string;
  targetId: string;
  silverCost: number;
}
```

O arquivo deve conter a revisao/commit do dump usado. O gerador deve falhar se
as receitas canonicas de controle nao forem encontradas.

## 11. Integracao com itens existentes

Nao adicionar recursos ao `ALBION_ITEMS`, que e voltado para equipamentos,
builds e slots.

Compartilhar por ID oficial:

- Render de imagem via `/render/item/{id}`.
- Cliente de precos AODP.
- Historico de preco.
- Formatacao de tier e encantamento.
- Nomes localizados gerados do dump.
- Carrinho economico.

Evolucao posterior:

- Clicar em uma barra na receita de uma arma abre Refino preselecionado.
- Um material do carrinho pode ser marcado como `comprar` ou `produzir`.
- Transmutacao pode sugerir uma origem mais barata para o recurso do Refino.

## 12. Correcao da camada de precos

Antes de usar precos para arbitragem de Refino e Transmutacao:

1. Separar `sellUpdatedAt` e `buyUpdatedAt` em `PriceQuote`.
2. Nao usar a data recente de um lado para validar o preco velho do outro.
3. Limpar precos de API quando servidor ou cidade mudar e nao houver cotacao.
4. Preservar overrides manuais, mas identifica-los por servidor, cidade e lado.

O MVP continua usando AODP diretamente, como a calculadora atual. Migrar tudo
para o backend exigiria primeiro corrigir o modelo de precos por regiao e lado
do livro; isso nao e pre-requisito para entregar Refino.

## 13. Estrutura de componentes

Manter a rota publica atual e adicionar um seletor de modo no topo.

Arquivos previstos:

```text
frontend/src/components/CraftCalculator.tsx
frontend/src/components/RefiningCalculator.tsx
frontend/src/components/TransmutationCalculator.tsx
frontend/src/lib/craft/location.ts
frontend/src/lib/craft/refining.ts
frontend/src/lib/craft/transmutation.ts
frontend/public/data/refining.json
backend/scripts/seed_refining_data.py
```

Extrair componentes visuais do `CraftCalculator` somente quando houver o
segundo consumidor real. Nao criar um framework generico de calculadoras.

## 14. Fases de entrega

### Fase 0 - Baseline segura

- Concluir a revisao e o commit do worktree atual.
- Excluir caches e imagens geradas do commit.
- Rodar testes e builds existentes.
- Rotacionar tokens Discord expostos durante a auditoria local.

### Fase 1 - Localizacao real no Craft

- Introduzir `ProductionLocation`.
- Exigir cidade para `city` e `island`.
- Derivar bonus por `location.city === family.bonusCity`.
- Persistir a ultima localizacao no navegador.
- Mostrar cidade e status do bonus no painel e carrinho.
- Manter mercados de compra e venda independentes.

### Fase 2 - Dados e formulas de Refino

- Criar o gerador do catalogo.
- Extrair receitas normais, coracoes, pedra, foco, fame e transmutacoes.
- Implementar formulas puras de refino.
- Adicionar checagens canonicas executaveis no gerador.
- Corrigir freshness de buy/sell na camada de precos.

### Fase 3 - Refino MVP

- Adicionar modo `Refino`.
- Selecionar familia, tier, encantamento e quantidade.
- Usar cidade real, taxa, evento e foco.
- Comprar o material refinado do tier anterior no mercado.
- Mostrar lucro, margem, foco e prata por foco.
- Implementar integralmente a excecao da pedra.

### Fase 4 - Specs e coracoes

- Adicionar specs T4-T8 por familia.
- Persistir specs para usuario logado.
- Comparar receita normal e receita com coracao.
- Comparar compra do coracao e conversao de Shadowheart.
- Mostrar a opcao vencedora sem esconder as alternativas.

### Fase 5 - Refino encadeado

- Permitir produzir o refinado do tier anterior.
- Aplicar retorno, taxa e foco em cada etapa.
- Consolidar lista total de materiais.
- Integrar o resultado ao carrinho normal de Craft.

### Fase 6 - Transmutacao

- Adicionar modo `Transmutacao`.
- Montar o grafo a partir do catalogo gerado.
- Comparar compra direta e rotas de conversao.
- Integrar sugestoes ao Refino.

### Fase 7 - Hideouts

- Modelar separadamente Outlands e Roads.
- Implementar as tabelas atuais de qualidade e power level.
- Nao reutilizar silenciosamente a formula atual de Craft.
- Validar os valores no cliente do jogo antes de publicar.

## 15. Criterios de aceite

### Localizacao

- Cajado de gelo em Thetford nao recebe bonus especializado.
- Cajado de gelo em Martlock recebe +15 pontos.
- Ilha vinculada a Martlock segue a mesma especializacao de Martlock.
- Trocar mercado de venda nao altera o retorno.
- Ordem ja adicionada ao carrinho mantem sua cidade e retorno.

### Refino

- T6 barra normal usa 4 minérios T6 e 1 barra T5.
- T6 barra com Mountainheart troca exatamente 1 minerio pelo coracao.
- Mountainheart e elegivel ao retorno nessa receita.
- T6.2 pedra produz 4 blocos T6 e consome 4 vezes os blocos T5.
- Pedra `.4` nao aparece.
- Rockheart nao aparece em pedra `.1`, `.2` ou `.3`.
- Receita `.4` de outro recurso nao oferece coracao.

### Spec e foco

- Spec T6 afeta fortemente T6 e levemente os outros tiers da mesma familia.
- Specs de minerio nao afetam fibra.
- Cinco specs 100 resultam em 40.000 de eficiencia.

### Transmutacao

- Nao cria aresta descendente.
- Nao mistura familias.
- Nao aplica retorno ou foco.
- Compara compra direta com todos os caminhos validos.

## 16. Validacoes pendentes no jogo

- Arredondamento inteiro final do custo de foco.
- Taxa adicional da estacao durante transmutacao, se houver.
- Texto e agrupamento atuais das receitas com coracao no cliente.
- Comportamento visual do retorno probabilistico em lotes pequenos.
- Valores atuais de hideouts apos patches recentes.

Esses pontos devem ter controles calibraveis ou ficar fora do MVP ate serem
medidos. Nao bloquear a localizacao real nem o Refino em cidades por eles.

## 17. Ordem recomendada

Entregar primeiro as Fases 0 a 4. Esse corte resolve o bonus incorreto atual e
entrega Refino util com cidade real, foco, specs, coracoes e pedra. Refino
encadeado, Transmutacao e Hideouts entram depois sem contaminar o primeiro
modelo com complexidade ainda nao validada.
