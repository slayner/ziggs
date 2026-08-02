---
description: Entry point for any Ziggs task. Triages the request and routes it (inline / intake / delegate-to-worker).
agent: build
---

# Ziggs task dispatcher

You are the **porta de entrada** for every Ziggs-related request. Before editing
or delegating, classify the request and route it. Do not create agents just to
fulfil ritual — resolve trivial work directly.

Task: $ARGUMENTS

## Step 1 — Triage

Read the request and the relevant parts of `AGENTS.md` + `HISTORICO-SESSOES.md`,
then place it in exactly one lane:

| Lane | When | Action |
|------|------|--------|
| **A. Trivial** | One-shot, obvious, < ~3 tool calls, no design choice | Do it inline, now. No agent. |
| **B. Vague / mature area** | Unclear intent, OR touches a mature/-sensitive area (prices, companion, auth, migrations, lootlog) without a crisp spec | Run **`/ziggs-intake`** to clarify before any edit. |
| **C. Clear + substantial** | Concrete goal + known scope, but non-trivial (many files, a feature, a migration) | **Delegate to a worker** (below). |

If unsure between A and C, lean A — do it inline and escalate only if it grows.

## Step 2 — Lane A (trivial)

Just do the work. Skip the rest.

## Step 3 — Lane B (vague / mature)

Hand off to the intake: invoke **`/ziggs-intake`** with the raw request. Wait for
the clarified spec to come back, then re-triage the clarified version (usually → C).

## Step 4 — Lane C (delegate to worker)

Delegate via the **Traycer child-agent** mechanism (not opencode native
subagents — replies must flow over `traycer_send_message` so they survive the
thread). Create one worker per task:

1. `traycer_create_agent` — a GUI child, `permissionMode: "auto_accept_edits"`
   (or `"full_access"` if it needs bash/git). Give it a short name from the task.
2. `traycer_send_message` to the new agent id with `expectReply: true`, sending
   the **worker briefing** below filled in. This is the single source of truth
   for budget + escalation — copy it verbatim into the message.

### Worker briefing (send this)

```
TASK: <one-line goal>
CONTEXT: <files/areas to touch, decisions already made, links to AGENTS.md sections>
SCOPE CEILING: <explicit boundary — what is IN and what is OUT>
  - Stay inside: <...>
  - Do NOT touch: <...>
  - If the real fix is outside this ceiling, STOP and escalate (below).
BUDGET:
  - Attempts: 2 retries on the same sub-task. A 3rd failure on the same thing
    = stop, do not loop.
  - Complexity: if the work grows beyond the SCOPE CEILING (new modules the user
    didn't sanction, a second subsystem dragged in), STOP.
DONE MEANS: <verifiable criterion — test command, typecheck, etc.>

WORKFLOW:
- Read AGENTS.md first (it carries hard-won doctrine; ignoring it = known bugs).
- Make the minimum change that satisfies DONE MEANS.
- Verify with the project's lint/typecheck/test command before reporting done.

ESCALATION:
- If you hit the budget or the ceiling, do NOT expand scope and do NOT keep
  trying silently. Reply on THIS thread via traycer_send_message with a message
  that STARTS WITH the literal token `NEEDS_ESCALATION` followed by:
    1. what you tried (commands/diffs),
    2. where you're blocked (the exact error / the scope expansion needed),
    3. the smallest next step you'd recommend.
- Otherwise, reply with your final result + the verification command output.
```

Defaults: `permissionMode: auto_accept_edits`, model inherited. Bump to
`full_access` only if the task needs shell/git. Keep the briefing tight — a
worker that re-reads AGENTS.md and gets a crisp ceiling needs little else.

## Step 5 — Handle the reply

- **Worker reports done + verifies** → review the result, then close the thread
  (reply without `expectReply`). Summarise to the user.
- **`NEEDS_ESCALATION: ...`** → you (the parent) decide, do NOT auto-expand:
  1. **Add context** and re-send to the same worker (cheapest), OR
  2. **Ask the user** (if the blocker is a product/scope decision), OR
  3. **Promote** the worker to a more capable model (`traycer_configure_agent`)
     and re-send the same task.
  Pick the cheapest option that actually unblocks. Never silently widen scope.

## Guardrails

- One worker per task. Don't fan out speculative sub-agents.
- Keep the user informed in short prose — what lane, what was delegated, the
  outcome. No essays.
- This protocol applies to Ziggs work only, not to other projects.
