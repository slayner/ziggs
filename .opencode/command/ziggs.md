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

1. `traycer_create_agent` — a GUI child. Pick **model by difficulty** (cheapest
   tier that can plausibly finish correctly), per the **Agent Selection Guide**
   tiers. Each tier has a **fallback chain** — if the first provider fails, retry
   with the next (see Step 4.5 — Failover below).
   - **Hard** (try in order): `opencode`/`zai-coding-plan:glm-5.2` → `opencode`/`ollama-cloud:glm-5.2` → `hermes`/`ollama-cloud:glm-5.2` → `codex`/`gpt-5.6-sol`
   - **Medium**: `opencode`/`zai-coding-plan:glm-5-turbo` → `opencode`/`ollama-cloud:kimi-k2.7-code` → `hermes`/`ollama-cloud:kimi-k2.7-code` → `codex`/`gpt-5.6-luna`
   - **Easy**: `opencode`/`ollama-cloud:deepseek-v4-pro` → `hermes`/`ollama-cloud:deepseek-v4-pro` → `opencode`/`zai-coding-plan:glm-4.7` → `codex`/`gpt-5.6-terra`
   - `permissionMode: "auto_accept_edits"` (or `"full_access"` if it needs bash/
     git — e.g. to run typecheck/build). Give it a short name from the task.
   - Only pass `reasoningEffort` for models that list it in
     `traycer_list_harness_models`. When unsure, omit — the creation fails
     cleanly and you retry with the param removed.
   - When in doubt Medium vs Hard, start Medium — the failover + `NEEDS_ESCALATION`
     path lets you promote if the worker gets stuck.
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

Defaults: `permissionMode: auto_accept_edits` (bump to `full_access` if the task
needs shell/git), and model by difficulty per Step 4. Keep the briefing tight —
a worker that re-reads AGENTS.md and gets a crisp ceiling needs little else.

## Step 4.5 — Failover (agent failed)

After delegating, you may receive a signal that the worker **failed** (not
struggled with the task — genuinely failed to run). Detect failure from:

- System message: *"ran into an error before replying"* / *"Payment Required"* /
  *"Insufficient credits"* → provider error.
- Agent goes idle without replying → stuck/lost connection.
- Agent replies but `traycer_get_transcript` shows NO meaningful work (no edits,
  no tool calls) → silent token exhaustion (common with z.ai-coding-plan).

**Do NOT failover** if the agent did real work but hit a legitimate blocker
(scope too tight, missing info) — that's `NEEDS_ESCALATION` (Step 5), not
failover.

### Failover procedure

1. Note the failed `(harness, model)` and which tier it was.
2. Pick the **next** entry in that tier's fallback chain (Step 4).
3. Create a **new** agent (`traycer_create_agent`) with the next provider —
   same workspace, same tier, same permission mode. Do NOT reuse the failed
   agent id.
4. Re-send the **same briefing** via `traycer_send_message` with `expectReply: true`.
5. If **all** providers in the tier fail → escalate to the user (report which
   providers were tried, don't loop).

## Step 5 — Handle the reply

- **Worker reports done + verifies** → review the result, then close the thread
  (reply without `expectReply`). Summarise to the user.
- **`NEEDS_ESCALATION: ...`** → you (the parent) decide, do NOT auto-expand:
  1. **Add context / relax the ceiling** and re-send to the same worker (cheapest), OR
  2. **Ask the user** (if the blocker is a product/scope decision), OR
  3. **Promote** the worker up a model tier (`traycer_configure_agent`: Easy→Medium→Hard)
     and re-send the same task — use this when the worker is genuinely out of its
     depth, not when the ceiling was just too tight (that's option 1).
  Pick the cheapest option that actually unblocks. Never silently widen scope.

## Guardrails

- One worker per task. Don't fan out speculative sub-agents.
- Keep the user informed in short prose — what lane, what was delegated, the
  outcome. No essays.
- This protocol applies to Ziggs work only, not to other projects.
