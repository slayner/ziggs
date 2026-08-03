## Why

The comp editor grew incrementally over many sessions and became a patchwork: 2 quadrantes vira 1, badge vira borda, seções se acumulam sem hierarquia. The core problem is conceptual, not cosmetic: **flex builds** (múltiplas roles por slot) added a dimension of complexity (BuildTabs, flex picker, flex_of metadata, `roles: DraftRole[]`) that makes the editor hard to use and hard to improve. The user wants a comp editor that is **fast, illustrative, and exploratory** — pick a weapon, see the build, swap gear, done. No multi-build management inside a single slot.

## What Changes

- **BREAKING: Remove flex builds** — 1 slot = 1 role. `DraftSlot.roles` goes from `DraftRole[]` to `DraftRole` (single). `comp_slot_roles` keeps N rows in the DB (no migration), but the frontend always sends `role_ids: [single_id]`. `BuildTabs`, `flexMenu`, `flex_of`, `getPickableRoles`, flex picker — all removed.
- **Swaps stay** — alt items (offhand_alt, helmet_alt, etc.) remain inside the single role. This is the only form of "alternative" gear.
- **Role name in the bracket title** — the card title IS the role name, editable inline. No separate "Identidade" section with a name field.
- **Function selector = colored square next to weapon render** — clicking the colored square (fn-dot) opens a dropdown to change function. No separate fn-type-picker panel.
- **Weapon skills below weapon picker** — Q/W/Passive appear directly under the weapon ItemPicker, not in a separate "Habilidades" section. Gear spells (helmet_Q, armor_W) stay inline with their gear piece.
- **Compact layout: 3-column grid for small sections** — Consumables (food/potion), Notes (obs/play_style), and Weapon occupy one row of 3 columns. Equipment (offhand/helmet/armor/boots/cape) occupies its own row with a 5-column grid (or 3+2). No section is wider than it needs to be.
- **Accordion stays** — click a slot to expand inline. The expanded card is compact enough to show most of the build without scrolling.
- **Alt items: inline under their gear piece, compact** — each alt item is a small ItemPicker below the main one, with a "+" button. Gear spells for alts appear beside the alt picker, not in a separate column.

## Capabilities

### New Capabilities
- `comp-editor-v2`: Redesigned comp editor — single-role-per-slot, inline accordion, compact 3-column layout, function selector via colored square, weapon skills under weapon picker, swaps as the only alt gear mechanism.

### Modified Capabilities
<!-- No existing specs in openspec/specs/ — this is a greenfield spec. -->

## Impact

- **Frontend**: `frontend/src/components/comp/CompEditor.tsx` — major rewrite (remove flex, reorganize sections, compact layout). `comp/types.ts` — `DraftSlot.roles` becomes `DraftRole` (single). `comp/helpers.ts` — `compToDraft`, `roleToPayload`, `encodeCompCode` updated for single-role. `comp/BuildTabs.tsx` — deleted. `comp/CompBuilder.tsx` — `startEditing` prop removed (always edit).
- **Backend**: NO schema changes. `comp_slot_roles` keeps N rows but frontend sends 1. `services/comps.py` `_build_parties` already handles `role_ids: [id]`. `event_escalation.py` flex validation still works (set of 1). No migration needed.
- **i18n**: Remove flex-related keys (`flexOfPrefix`, `newBuildBtn`, `cbAddBuildTab`, `cbBuildsCountTitle`, `noOtherBuildInComp`, `flexAlternativesLabel`, `mainTabLabel`). Add keys for new layout if needed.
- **Comp code (import/export)**: `encodeCompCode`/`decodeCompCode` updated — `CompCode` slot structure goes from `roles: []` to single role. Old codes with `roles: [r0, r1]` keep `r0` (first) on import.