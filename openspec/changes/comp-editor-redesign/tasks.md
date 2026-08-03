## 1. Types: DraftSlot.roles[] → DraftSlot.role (single)

- [ ] 1.1 In `comp/types.ts`: change `DraftSlot` from `{ fn: string | null; roles: DraftRole[] }` to `{ fn: string | null; role: DraftRole }`
- [ ] 1.2 Remove `DraftRole.flex_of` field from `DraftRole`
- [ ] 1.3 In `comp/types.ts`: change `CompCode` slot structure from `roles: [...]` to `role: {...}` (keep backward-compat in decode)
- [ ] 1.4 `npx tsc --noEmit` — expect ~30+ errors (every `slot.roles[...]` access breaks). Do NOT fix yet; this is the cascade signal.

## 2. Helpers: serialização (compToDraft, roleToPayload, encodeCompCode)

- [ ] 2.1 `compToDraft`: `s.roles.map(apiRoleToDraft)` → `apiRoleToDraft(s.roles[0])` (take first role from backend; drop rest silently)
- [ ] 2.2 `roleToPayload`: no change needed (operates on a single DraftRole already)
- [ ] 2.3 `encodeCompCode`: change slot from `roles: [...]` to `role: {...}`. Remove `flex_of` from encoded role.
- [ ] 2.4 `decodeCompCode`: handle both formats — if `slot.roles` is array, take `roles[0]`; if `slot.role` is object, use directly. Map to new `DraftSlot.role`.
- [ ] 2.5 `npx tsc --noEmit` — fewer errors now (helpers fixed, CompEditor still broken).

## 3. CompEditor: remove flex state and UI

- [ ] 3.1 Remove state: `editRi`, `flexMenu`, `addSlotMenu`, `addCopyMenu` (all flex-related)
- [ ] 3.2 Remove functions: `getPickableRoles`, `ensurePickableRolesLoaded`, `setFlexMenu` usage, flex picker render block
- [ ] 3.3 Delete `comp/BuildTabs.tsx` (no longer used)
- [ ] 3.4 Remove import of `BuildTabs` from CompEditor
- [ ] 3.5 `toggleCard`: no longer needs editRi reset (single role)
- [ ] 3.6 `addSlot(pi)`: creates `{ fn: null, role: emptyRole() }` (single role, not array)
- [ ] 3.7 `removeSlot`, `updSlot`: adapt to `slot.role` instead of `slot.roles[editRi]`
- [ ] 3.8 `updRole(pi, si, ri, fn)`: remove `ri` param — always role index 0 (the only role). Simplify to `updRole(pi, si, fn)`.
- [ ] 3.9 `updRoleQuiet`: same simplification
- [ ] 3.10 `save()`: `s.roles.map(r => r.catalog_id)` → `[s.role.catalog_id]`
- [ ] 3.11 `renderCardDetail(pi, si)`: remove `editRi` references, use `slot.role` directly

## 4. CompEditor: card header — name as title, fn-dot as selector

- [ ] 4.1 Card header (`rc-head`): render role name as the title. When expanded, name is an `<input>` (inline edit). When collapsed, name is `<span>`.
- [ ] 4.2 Fn-dot: make it clickable. On click, open a small dropdown (`fn-dropdown`) with fn-type colored buttons. Selecting sets `slot.fn`.
- [ ] 4.3 If `slot.fn` is null, fn-dot is gray/translucent. Click opens same dropdown.
- [ ] 4.4 Remove the old fn-type-picker panel (the big block with ColorPicker/emoji/label/ordenar/deletar rows). That config stays in the toolbar "Funções" button.
- [ ] 4.5 Remove the `detail-section` for "Identidade" — name is in the header now, fn is the dot.

## 5. CompEditor: compact 3-column layout

- [ ] 5.1 Expanded card body: 3-column grid for top row — (1) Weapon + skills, (2) Consumables, (3) Notes
- [ ] 5.2 Column 1 (Weapon): `ItemPicker(weapon)` + Q/W/Passive `SpellPicker`s below it
- [ ] 5.3 Column 2 (Consumables): food + potion ItemPickers with qty inputs, side by side or stacked
- [ ] 5.4 Column 3 (Notes): `obs` textarea + `play_style` input (compact)
- [ ] 5.5 Equipment grid row (below the 3-column row): 5-column grid for offhand/helmet/armor/boots/cape
- [ ] 5.6 Each gear piece: ItemPicker + alt ItemPickers inline below + gear_spells inline beside
- [ ] 5.7 Responsive: < 720px → stack to 1 column

## 6. CompEditor: gear spells inline with gear piece

- [ ] 6.1 For each gear slot (helmet/armor/boots): if the item has spells, show SpellPickers directly below the ItemPicker (not in a separate section)
- [ ] 6.2 For alt items with spells: show SpellPickers beside the alt ItemPicker
- [ ] 6.3 Remove the old "Habilidades" section (Q/W/Passive are now under weapon)

## 7. CSS: compact layout, fn-dropdown, 3-column grid

- [ ] 7.1 `.rc-card-detail`: padding 16px, `background: var(--surface-2)`, border-top separator
- [ ] 7.2 `.rc-detail-top-row`: `display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px;` (responsive: 1col < 720px)
- [ ] 7.3 `.rc-gear-grid`: `display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px;` (responsive: 3 cols < 720px, 2 cols < 500px)
- [ ] 7.4 `.fn-dropdown`: absolute positioned small menu below fn-dot, with colored buttons per fn-type
- [ ] 7.5 `.detail-section-title`: keep editorial style (uppercase, 11px, gold, border-bottom) for sections that remain (gear grid header, notes header if needed)
- [ ] 7.6 `.role-card`: border-left 6px solid var(--chip-color); `.rc-open` highlight (border gold, bg surface-2)
- [ ] 7.7 `.rc-fn-dot`: 10×10px, cursor pointer, hover ring, transition

## 8. CompBuilder: remove startEditing

- [ ] 8.1 `CompBuilder.tsx`: `active.startEditing` always true (or remove the prop from CompEditor since always edit)
- [ ] 8.2 Remove `initialEditing` from CompEditor props (ignored, always edit)

## 9. i18n cleanup

- [ ] 9.1 Remove flex-related keys: `flexOfPrefix`, `newBuildBtn`, `cbAddBuildTab`, `cbBuildsCountTitle`, `noOtherBuildInComp`, `flexAlternativesLabel`, `mainTabLabel`
- [ ] 9.2 Remove fn-type-picker keys if no longer used: `selectFnTypeHint` (the big picker panel is gone)
- [ ] 9.3 Keep `identityLabel` if still used, or remove if the "Identidade" section is fully replaced by header

## 10. Verification

- [ ] 10.1 `npx tsc --noEmit` passes with zero errors
- [ ] 10.2 `npm run build` passes
- [ ] 10.3 Open a comp in the editor — card expands inline, shows 3-column top row + gear grid
- [ ] 10.4 Click fn-dot → dropdown opens → select fn → dot color changes
- [ ] 10.5 Edit role name in the card title — name updates
- [ ] 10.6 Add alt item to a gear piece → alt ItemPicker appears inline
- [ ] 10.7 Save comp → comp loads correctly on refresh (role_ids: [single])
- [ ] 10.8 Import old comp code (with `roles: [...]`) → takes roles[0], loads correctly
- [ ] 10.9 Export comp code → new format (`role: {...}`)
- [ ] 10.10 No BuildTabs visible — no flex picker, no build tabs, no editRi