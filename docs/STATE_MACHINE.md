# Máquina de estados do evento (CTA)

Fonte: `backend/app/domain/states.py` + `state_machine.py`. **Nunca** escreva
`events.state` direto — use `state_machine.transition(...)`, que valida a aresta,
roda os guards, grava a transição e o audit log numa transação.

## Estados

| PT | código | terminal? | significado |
|---|---|---|---|
| agendado | `scheduled` | não | criado para começar no futuro |
| andamento | `in_progress` | não | CTA acontecendo (pingado por site ou comando) |
| definição | `definition` | não | deu callout; threads abrem, logística define o tipo |
| verificação | `verification` | não | os passos de conferência |
| espera | `waiting` | não | tudo conferido; **único** estado que libera *finalizar* |
| finalizado | `finalized` | sim | pagamentos/logs gerados |
| cancelado | `cancelled` | sim | abortado antes de pagar |
| excluído | `deleted` | sim | apagado/estornado |

## Transições

```
agendado ──start──▶ andamento ──callout──▶ definição ──tipo definido──▶ verificação
   │                    │                      │                            │
   │                    │                      │                      (passos OK)
   ▼                    ▼                      ▼                            ▼
cancelado/excluído  cancelado/excluído   cancelado/excluído              espera
                                                                           │
                                          ┌──────── voltar p/ corrigir ────┘
                                          ▼                                │
                                    verificação                       finalizar
                                                                           ▼
                                                                       finalizado
                                                                           │
                                                                           ▼
                                                                       excluído
```

- De **agendado/andamento/definição/verificação** dá para ir a `cancelado` ou
  `excluído`.
- **espera** → `verificação` (corrigir), `finalizado`, `cancelado` ou `excluído`.
- **finalizado** e **cancelado** ainda podem virar `excluído` (estorna/limpa), como
  o `/deleteevent` do bot.

## Guards (impedem transição mesmo com aresta válida)

| Aresta | Exige |
|---|---|
| definição → verificação | **tipo definido** (lootsplit / regear / lootsplit+regear) |
| verificação → espera | **todos os passos de verificação concluídos** |
| espera → finalizado | tipo definido **e** verificação completa |

## Passos da verificação

Em `VerificationStep`. Ordem de UI; podem ser marcados em qualquer ordem, mas
todos precisam estar `completed` para liberar `espera`:

1. `participation` — verificar participações (%)
2. `missing_loots` — loots faltantes (desvios) → cobranças
3. `tab_value` — definir o valor da tab
4. `tab_image` — print da tab
5. `battles` — registrar as batalhas do horário do CTA
6. `nodes` — nodes capturados no evento

> A visão citou "7 passos" numerados 1,2,4,5,6,7. Consolidei em 6 sem buraco
> (separando "valor da tab" e "print da tab"). Para ter exatamente 7, basta
> adicionar um membro em `VerificationStep` — o gating não muda.

## Regras econômicas amarradas aos estados

- **lootsplit+regear nunca debita jogador.** Perda só é mostrada/registrada e sai
  do **banco da guilda**, que **pode ficar negativo** (`events.is_loss`).
- Pagamentos só acontecem na transição **espera → finalizado**.
- Split não definido = **0** (distinto de "definido como 0", como no bot).
