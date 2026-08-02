---
description: Intake for vague or mature-area Ziggs requests. Clarifies intent, scope, and constraints before any work.
agent: build
---

# Ziggs change intake

Use when a Ziggs request is **vague, incomplete, or touches a mature/sensitive
area** (prices, companion, auth, migrations, lootlog, the AGENTS.md doctrine
sections). Goal: turn it into a crisp spec that can be re-dispatched via
`/ziggs` (lane C), without editing anything yet.

Raw request: $ARGUMENTS

## Step 1 — Find the gaps

Read the request against `AGENTS.md` (especially the doctrine sections for the
area it touches) and `HISTORICO-SESSOES.md`. List what's genuinely unknown or
risky. Common gaps:

- **Intent**: what's the user-visible outcome? (not the implementation)
- **Scope boundary**: what is explicitly IN and OUT? which subsystems?
- **Mature-area trap**: does it conflict with a documented decision? (search
  AGENTS.md for the area — e.g. "lootlog", "companion", "city-markers")
- **Success criterion**: how do we know it's done? (a command, a behaviour)
- **Non-goals**: what should NOT change?

If the request is actually already clear, say so in one line and return it
unchanged — don't invent questions to justify the ritual.

## Step 2 — Ask

Use the **question tool** to ask only the gaps that actually block safe
implementation. Batch them into one call (multiple questions), keep options
concrete and grounded in the codebase. Prefer 2–4 questions; more than ~5 means
the request is under-shaped and you should say so.

Each question:
- one clear dimension (intent / scope / success / non-goals),
- options grounded in real code paths or documented decisions,
- a recommended option first when one exists.

## Step 3 — Emit the spec

Once the answers resolve the gaps, write a tight spec to the thread:

```
SPEC:
  Goal: <one line, user-visible>
  Scope: <IN: ... / OUT: ...>
  Touches: <files/areas>
  Done when: <verifiable>
  Non-goals: <...>
  Decisions locked: <...>
```

Then tell the user to re-run **`/ziggs <spec>`** (or just continue — the
dispatcher will now route it to lane C as clear+substantial).

## Guardrails

- Never edit code in intake. Intake produces a spec, not a change.
- Don't re-ask things AGENTS.md already settles — read it first.
- If the user's answer reveals the request is actually two tasks, split them and
  say so; one worker per task downstream.
