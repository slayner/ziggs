# Análise do Backend + Site — Mapa e Oportunidades

## Arquitetura atual (resumo)

**Stack:** FastAPI + SQLAlchemy + PostgreSQL + 21 background workers async.

**Domínios:**
- **Público (sem login):** Dashboard, Batalhas, Highscores, Craft, Mercado, Perfis (player/guild/aliança), Busca global
- **Guilda (login Discord):** Comps, Eventos (CTA), Regear, Lootlog/Reconcile, Escalacao, Config
- **Bot Discord:** 60+ endpoints pra integração completa (register, events, economy, regear, nodes, battle feed)
- **Companion (desktop):** Scan distribuído, DNS, túnel, lootlog, damage meter, price capture

**21 background workers:** player_tracker, battle_tracker (3 modos), profile_warmer (2), claim_checker, registration_checker, weapon_stats, battle_reprocessor, battle_sweeper, companion_scan, small_battle_discovery, player_count_snapshot, battle_price_reprocessor, silver_dropped, regear_retry, dashboard_cache, highscores_cache, gold_price, market_snapshot, search_index.

---

## O que JÁ FUNCIONA bem

1. **Tracker de batalhas** — 84k batalhas letais, sweeper distribuído, companion scan, probes pra não re-sondar
2. **Sistema de eventos/CTA** — máquina de estados completa, escalacao com autofill, signups, nodes, verification
3. **Comps** — builder complexo com alts, gear_spells, sugestões por função
4. **Mercado** — snapshot contínuo, history agregada, movers com 2 pisos
5. **Craft** — transmutação, heart, focus, journals, refining
6. **Perfis** — cold load assíncrono, cache 5min, warmer, busca fuzzy
7. **Regear** — OCR, screenshot, bank debit, retry
8. **Highscores** — 5 tipos, cache 15min, multi-região
9. **Bot Discord** — integração completa com o backend

---

## O que PRECISA ser feito (priorizado por impacto)

### P0 — Bugs/Gap críticos

| # | Problema | Detalhe |
|---|----------|---------|
| 1 | **Tabela `weapons` não persistida em migrations** | Acabou de quebrar tudo (roles null). Seed é script separado. Solução: migration que popula via SQL inline ou data migration. |
| 2 | **Companion aponta pra `ziggs.xyz` em release** | Não tem deploy ainda. Mudamos pra localhost, mas precisa de estratégia (env var? config override?). |
| 3 | **Página do companion stale** | `DOWNLOAD_URL_WINDOWS = null`, features "coming soon" já estão feitas. |

### P1 — Funcionalidades que faltam e o usuário procura

| # | Feature | Por quê | Como |
|---|---------|---------|------|
| 4 | **Ranking de guilda interno** | O usuário quer ver "quem da minha guilda tá jogando mais/better". Highscores é global; não tem visão "top killers da minha guilda esta semana". | Nova rota `/guilds/{id}/rankings` que filtra `BattleParticipant.guild_id` + agrega por player. Reusa a lógica de highscores mas com escopo de guilda. |
| 5 | **Estatísticas de jogador vs guilda específica** | "Como me saio contra a guilda X?" O endpoint `/players/{id}/versus` existe mas só mostra vs outro jogador. | Estender `versus` pra aceitar `guild_id` como alvo (já tem `victim_guild_id` no `PlayerKillEvent`). |
| 6 | **Histórico de composição da guilda** | "Quais comps a guilda usou nos últimos CTAs?" Os eventos têm `comp_id` mas não há página que mostra histórico de comps usadas. | Nova aba em Management: "Histórico" — lista eventos com comp usada, win rate, attendance. |
| 7 | **Busca de batalhas por jogador** | O usuário busca "batalhas do jogador X" mas a busca global só acha as 6 mais recentes. Não tem filtro "todas as batalhas de X". | O endpoint `/battles` aceita `search` mas não filtra por player_id. Adicionar filtro `player_id` que faz join com `BattleParticipant`. |
| 8 | **Tendência de IP médio** | "A guilda está subindo de IP?" Não há gráfico de evolução de IP ao longo do tempo. | Agregar `BattleParticipant.ip` médio por guilda por semana. Nova rota ou incluir no perfil de guilda. |
| 9 | **Win rate de guilda** | O perfil de guilda mostra batalhas mas não calcula win/loss. | O `battle_sides` tem `score` e `label`. Comparar score dos lados = vitória/derrota. Já parcialmente implementado no perfil (battle history tem win/loss), mas não agregado num número. |
| 10 | **Damage meter no site** | O companion tem damage meter mas o site não mostra. Usuário quer ver "quanto de dano fiz nesta batalha" no perfil. | O `BattleParticipant` já tem `damage_dealt`/`damage_taken`/`healing_done`. Já é mostrado na página de batalha. Mas não no perfil do jogador agregado. |

### P2 — Melhorias de UX/dados

| # | Feature | Detalhe |
|---|---------|---------|
| 11 | **Avatar do usuário no topbar** | `api.me().avatar` é buscado mas nunca renderizado. |
| 12 | **Dashboard: atividade da guilda** | Dashboard é global. Não tem "dashboard da minha guilda" — quantos CTAs esta semana, attendance, regears pagos, loot total. |
| 13 | **Perfil de jogador: aba "Batalhas"** | O perfil mostra kills/deaths/stats mas não lista as batalhas que participou. Tem que ir na busca global. |
| 14 | **Perfil de guilda: top weapons** | Não mostra "armas mais usadas pela guilda". Dado existe em `player_weapon_stats` mas não agregado por guilda. |
| 15 | **Highscores: ranking de IP médio** | Não tem "guildas com maior IP médio em ZvZ". Dado existe em `BattleParticipant.ip`. |
| 16 | **Mercado: alertas de preço** | Não tem "avisar quando item X chegar no preço Y". Seria útil pra crafters. |
| 17 | **Comps: template marketplace** | Cada guilda cria suas comps do zero. Não há templates compartilháveis. |
| 18 | **Eventos: pós-CTA analytics** | Depois do CTA: quantos morreram, regear total, loot total, kill fame total. Hoje é espalhado em abas diferentes. |
| 19 | **Lootlog: silver per hour** | O lootlog calcula silver estimate mas não mostra "X silver/hora" baseado na duração do evento. |
| 20 | **Search: buscar por alliance** | A busca global acha alliances mas não tem página dedicada de "alianças" pra navegar. |

### P3 — Tech debt / limpeza

| # | Item | Detalhe |
|---|------|---------|
| 21 | **4 componentes órfãos no frontend** | `RolesPage`, `RefiningCalculator`, `GuildSetup`, `Login` — dead code. |
| 22 | **`battle_price_reprocessor` é one-shot** | Roda forever mas só faz sentido uma vez. Deveria ser removido do lifespan. |
| 23 | **`item_price_cache` legado** | Tabela `item_price_cache` existe mas `item_prices` + `item_prices_latest` substituíram. |
| 24 | **Cache de weapons em memória** | `_weapon_fn_cache` tem TTL de 60s. Depois do seed, toda primeira request após expirar re-escaneia a tabela inteira (137 rows, ok). |
| 25 | **21 workers sem health check** | Se um worker morre silenciosamente, ninguém sabe. Não há `/health/workers` que mostre quais estão vivos. |
| 26 | **Migrations não populam dados de catálogo** | `weapons` depende de script de seed. Deveria ser migration data. |
| 27 | **Sem rate limit em rotas públicas pesadas** | `/battles/by-code/{id}` faz queries pesadas sem cache. Um bot poderia DoS. |

---

## Recomendação de prioridade

**Fazer agora (impacto alto, esforço baixo):**
1. #1 — Migration que popula `weapons` (acabou de quebrar tudo)
2. #4 — Ranking interno de guilda (dado já existe, só agregar)
3. #13 — Aba "Batalhas" no perfil de jogador (join simples)
4. #11 — Avatar no topbar (1 linha de JSX)
5. #21 — Deletar componentes órfãos

**Fazer depois (impacto alto, esforço médio):**
6. #7 — Busca de batalhas por jogador
7. #8 — Tendência de IP médio
8. #9 — Win rate agregado de guilda
9. #12 — Dashboard da guilda
10. #18 — Pós-CTA analytics

**Fazer eventualmente:**
11. #16 — Alertas de preço
12. #17 — Template marketplace
13. #25 — Health check de workers
14. #26 — Migration de dados de catálogo