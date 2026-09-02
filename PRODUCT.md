# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

A Ziggs atende igualmente três públicos de Albion Online:

- lideranças e administrações de guilda que organizam membros, eventos, composições, recursos e permissões;
- membros ativos que participam de eventos e acompanham a operação da própria guilda;
- jogadores públicos que consultam batalhas, perfis, guildas, rankings e calculadoras.

O sucesso é cada público encontrar e concluir seu fluxo com informação confiável, no contexto certo — operação de guilda, participação ou consulta pública.

## Product Purpose

A Ziggs é uma plataforma para guildas de Albion Online. Ela reúne site, bot de Discord e o aplicativo desktop Ziggs Companion para conectar operação de guilda, participação dos membros e inteligência pública do jogo.

## Positioning

A promessa central é uma central única integrada: o site é a fonte de verdade, o bot de Discord leva a operação ao ambiente social da guilda, e o Companion contribui com funções locais e coleta distribuída que não cabem no navegador.

## Operating Context

Guildas operam no Discord e no site; o bot sincroniza configurações, eventos, mensagens, threads e trabalho pendente com o backend. O Companion para Windows auxilia jogadores com scanner de batalhas, otimização de rota via WireGuard, teste de DNS, medidor de dano e captura de lootlog.

A experiência pública inclui consulta de jogadores, guildas, batalhas, rankings, calculadoras e dados de mercado. A interface tem PT-BR como idioma padrão, com inglês e espanhol disponíveis.

## Capabilities and Constraints

- Autenticação do site somente por Discord OAuth.
- Multi-tenancy por `guild_id` do Discord; dados e permissões de operação pertencem à guilda selecionada.
- Backend FastAPI e Postgres central são a fonte de verdade; o bot ativo é stateless e acessa o backend por HTTP.
- O site é React, TypeScript, Vite e Tailwind; o Companion usa Tauri 2, Rust e React/TypeScript.
- O Companion exige Windows 10/11 64-bit e privilégios administrativos para captura de pacotes e túnel.
- Dados de batalha e preços enviados pelo Companion são validados pelo backend contra a API pública de Albion.

## Brand Commitments

O produto se chama Ziggs e é voltado a Albion Online. Todo texto de usuário, comentário, docstring e detalhe de erro HTTP do produto deve estar em PT-BR; o frontend e o bot também oferecem i18n em inglês e espanhol.

## Evidence on Hand

- Implementações ativas: `frontend/`, `backend/`, `bot-v2/` e `companion/`.
- Recursos de avatar de Albion em `frontend/public/avatars/` e cache de itens em `backend/data/render_cache/`.
- O repositório não fornece depoimentos, clientes, benchmarks ou alegações comerciais verificadas; trabalhos futuros não devem inventá-los.

## Product Principles

1. Centralizar a verdade operacional sem afastar o usuário dos ambientes em que a guilda já trabalha.
2. Tratar informação pública de Albion e dados enviados por clientes como dados que precisam de validação e contexto.
3. Separar nitidamente consulta pública, participação de membro e administração de guilda, preservando a continuidade entre elas.
4. Manter o Discord como extensão operacional do produto, não como fonte concorrente de estado.
5. Respeitar os limites de privacidade e do dispositivo: processamento local quando necessário e nenhuma telemetria ou rastreamento declarados pelo Companion.

## Accessibility & Inclusion

WCAG 2.2 AA é o piso para as superfícies web.
