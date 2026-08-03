## Context

The comp editor (`CompEditor.tsx`, ~1200 lines) has been patched incrementally across many sessions. Each patch added a concept (flex builds, alt items, gear spells, fn-types, accordion, 1-quadrante) without revisiting the whole. The result is a component that works but is hard to use and hard to improve.

**Current data flow:**
- `Draft → DraftParty → DraftSlot → DraftRole[]` (roles is an array = flex builds)
- `DraftSlot.fn` = function type (tank/healer/dps/...)
- `DraftRole.equip` = weapon + gear + alts (swaps)
- `DraftRole.gear_spells` = `{helmet_Q: "SPELL_ID", ...}`
- Serialização: `roleToPayload` → `build_items[]` no backend (GameRole)

**Backend is agnostic to flex** — `comp_slot_roles` is N:1 with `comp_slots`, but if frontend always sends `role_ids: [id]`, it works with 1 row per slot. No migration needed.

## Goals / Non-Goals

**Goals:**
- 1 slot = 1 role. Remove flex builds entirely from frontend.
- Comp editor is compact: most of a build visible without scrolling when expanded.
- Function selector is the colored square next to the weapon — click to change.
- Role name is the card title — edit inline, no separate field.
- Weapon skills (Q/W/Passive) appear directly under the weapon picker.
- 3-column grid for small sections (weapon, consumables, notes).
- Swaps (alt items) remain, inline under their gear piece.

**Non-Goals:**
- Backend schema changes (no migration, no new endpoints).
- CompList redesign (already done, works well).
- Removing swaps — swaps stay.
- Changing the catalog/role API.

## Decisions

### D1: `DraftSlot.roles: DraftRole[]` → `DraftSlot.role: DraftRole`

**Why:** Flex builds added `BuildTabs`, `flexMenu`, `flex_of`, `getPickableRoles`, `editRi`, flex picker — 6+ concepts that complicate every mutation. Removing them simplifies `updSlot`, `updRole`, `toggleCard`, `save`, `compToDraft`, `roleToPayload`, `encodeCompCode`.

**Alternative considered:** Keep `roles: DraftRole[]` but force length 1. Rejected — the array indexing (`roles[editRi]`, `roles[0]`) would still leak into every code path, and `BuildTabs` would still render (1 tab).

**Impact on serialização:**
- `compToDraft`: `s.roles.map(apiRoleToDraft)` → `apiRoleToDraft(s.roles[0])` (take first; if backend has N from old data, take first and drop rest).
- `save()`: `s.roles.map(r => r.catalog_id)` → `[s.role.catalog_id]` (still sends `role_ids: [id]` to backend).
- `encodeCompCode`: slot goes from `roles: [...]` to `role: {...}`. Old codes with `roles: [r0, r1]` → import takes `r0`.

### D2: Card layout — title = role name, function = colored square

**Card recolhido:**
```
┌──┬──────────────────────────────────────────────────────┐
│▓▓│ [weapon-icon] Role Name (editable)        [equip-strip] [×] │
└──┴──────────────────────────────────────────────────────┘
 ^-- 6px border-left (fn color) + 8px fn-dot
```

**Card expandido:**
```
┌──┬──────────────────────────────────────────────────────┐
│▓▓│ [weapon-icon] Role Name (input)          [equip-strip] [×] │
│  ├──────────────────────────────────────────────────────┤
│  │ [col-1: weapon]      [col-2: consumables]  [col-3: notes] │
│  │  ItemPicker(weapon)   food / potion         obs / play_style│
│  │  Q / W / Passive                                     │
│  ├──────────────────────────────────────────────────────┤
│  │ [gear grid: 5 columns]                                │
│  │  offhand  helmet  armor  boots  cape                  │
│  │  (each with alts inline + gear_spells inline)         │
│  └──────────────────────────────────────────────────────┘
```

**Why 3-column for top row:** Weapon (with skills) is the tallest, consumables and notes are short. They fit side by side, saving vertical space.

**Why 5-column for gear:** Each gear piece is just an ItemPicker + optional alts + gear_spells. 5 across is compact.

### D3: Function selector = click fn-dot → dropdown

The fn-dot (8×8 colored square) next to the weapon icon is clickable. Click opens a small dropdown with fn-type buttons (colored). Selecting one sets `slot.fn`. No separate fn-type-picker panel.

**If slot has no fn:** dot is gray/empty. Click opens the same dropdown.

### D4: Weapon skills under weapon picker

Q/W/Passive `SpellPicker`s appear directly below the weapon `ItemPicker`, in the same column. No separate "Habilidades" section. Gear spells (helmet_Q, armor_W) stay inline with their gear piece in the gear grid.

### D5: Remove `BuildTabs.tsx`, `flex_of`, `editRi`, `flexMenu`, `getPickableRoles`, `addCopyMenu`

All flex-related state and UI is deleted. `DraftRole.flex_of` field is removed from `types.ts`. `encodeCompCode`/`decodeCompCode` drop `flex_of`.

### D6: `comp_slots.fn` already exists in backend

No migration. The frontend was already sending `fn` in `SlotIn`. The backend `_build_parties` already reads `s.fn`. The only change is how the frontend SETS `fn` (dot click vs select dropdown).

## Risks / Trade-offs

- **[Risk] Existing comps with flex builds** → They have N `comp_slot_roles` per slot. `compToDraft` will take `roles[0]` and silently drop the rest. The extra roles remain in the DB but are invisible. **Mitigation:** This is acceptable — the user explicitly wants flex gone. If needed later, a cleanup script can delete orphaned `comp_slot_roles` with `position > 0`.
- **[Risk] Event escalation validates against `flex_ids`** → `event_escalation.py:226` checks `game_role_id in flex_ids` (set of all `csr.game_role_id` for the slot). With 1 role, the set has 1 element. Assignment still works — the only valid role is the one in the slot. **Mitigation:** No change needed.
- **[Risk] Comp code backward compatibility** → Old codes with `roles: [r0, r1, r2]` need to import. `decodeCompCode` will take `roles[0]` if `roles` is an array (backward compat), or `role` if it's the new single-object format. **Mitigation:** Handle both formats in decode.
- **[Trade-off] Less flexibility** → Users can't have 2 builds for the same slot. **Mitigation:** Swaps (alt gear) cover the "same role, different gear" use case. If a user truly needs 2 different builds, they create 2 slots.