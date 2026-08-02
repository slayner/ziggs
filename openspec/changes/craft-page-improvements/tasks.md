## 1. Smart defaults (sell city + production city)

- [x] 1.1 Add `sellCitiesUserOverride: boolean` state (default false); set true when user calls `setSellCities` manually
- [x] 1.2 Add `useEffect` on `family`: when `!sellCitiesUserOverride`, set `sellCities` to `[family?.bonusCity ?? "Lymhurst"]`
- [x] 1.3 Change `productionLocation` initial fallback from `"Caerleon"` to `"Lymhurst"` (line 291)
- [x] 1.4 Verify: select a bonus-city family → sell city auto-sets to bonus city; manually change → switch family → sell city preserved

## 2. Focus toggle

- [x] 2.1 Change `const useFocus = true` to `const [useFocus, setUseFocus] = useState(true)`
- [x] 2.2 Add `<ToggleBtn active={useFocus} on="Focus" off="Focus" onClick={() => setUseFocus((f) => !f)} />` next to Premium in the production controls bar
- [x] 2.3 In the row rendering: when `useFocus` is true, add `className="text-amber-300"` to the "Lucro +F" `<Td>`; when false, add it to "Lucro −F"
- [x] 2.4 Add `focusCostPerItem: number` to the `Order` interface
- [x] 2.5 In `addOrder`, compute `focusCostPerItem: v.focus * focusMult` and include in the order object
- [x] 2.6 In the cart order card: when `o.useFocus`, show a focus bracket `👁 {silver(o.focusCostPerItem * o.qty)} focus`
- [x] 2.7 In the cart summary: compute `totalFocus = Σ(o.focusCostPerItem * o.qty for o in cart if o.useFocus)` and display it
- [x] 2.8 Add i18n keys: `focusToggle`, `focusBracketLabel`, `totalFocusLabel` (PT/EN/ES)

## 3. Inline T/H badges on refining rows

- [x] 3.1 Remove the separate purple `<tr>` block for transmutation (the `transmuteInfo` row push, lines ~866-884)
- [x] 3.2 In the Item column cell, after the tier label, add a purple "T" `<span>` when `transmuteInfo` is non-null, with `title` showing source→target + cost vs direct
- [x] 3.3 Add `onClick` to the T badge that toggles a popover showing transmute details (source icon → target icon, cost, direct price) — used native `title` tooltip instead (ponytail: sufficient detail, no extra state)
- [x] 3.4 Replace the `♥` icon with a red "H" `<span>` when `heartWins`, with `title` showing heart token + price + profit comparison
- [x] 3.5 Add `onClick` to the H badge that toggles a popover showing heart variant details — used native `title` tooltip (same rationale)
- [x] 3.6 Ensure both badges can coexist (T then H) when both are active
- [x] 3.7 Add i18n keys for tooltip text: `transmuteTooltip`, `heartTooltip` (PT/EN/ES) — used existing `heartHeader` key + inline text in `title`

## 4. Wider material price inputs

- [x] 4.1 Change `PriceField` width in the Materials column from `w-16` to `w-28` (or equivalent that fits 8 chars)
- [x] 4.2 Verify the table layout adjusts (overflow-x-auto handles it; check 1280px viewport doesn't break)
- [x] 4.3 If needed, adjust the grid breakpoint from `min-[1500px]` to `min-[1600px]` to give the middle column more room — not needed, overflow-x-auto handles it

## 5. Two-line column headers

- [x] 5.1 Extend `<Th>` component to accept optional `subtitle?: string` prop
- [x] 5.2 When `subtitle` is present, render it as a smaller muted `<span className="block text-[9px] font-normal normal-case text-zinc-600">` below the primary label
- [x] 5.3 Update all `<Th>` call sites with subtitles: Item (none), Materiais ("preço unitário"), Focus cost ("por craft"), Venda ("médio"), Lucro −F ("sem focus"), Lucro +F ("com focus"), SPF ("prata/focus"), Jornais ("preço unit."), Demanda ("vendas/dia")
- [x] 5.4 Add i18n keys for all subtitles: `colMaterialsSub`, `colFocusCostSub`, `colSellAvgSub`, `colProfitNoFocusSub`, `colProfitFocusSub`, `colSpfSub`, `colJournalsSub`, `colDemandSub` (PT/EN/ES)

## 6. Journal count rounding

- [x] 6.1 In the table Jornais column: wrap `journals` with `Math.ceil(journals)` (or `Math.ceil(journalsFilled(...))`)
- [x] 6.2 In the cart order bracket: wrap `journals` with `Math.ceil(journals)`
- [x] 6.3 Verify the cart summary (which sums fractional then ceilings) still works correctly — do NOT change that logic

## 7. No journal brackets for refining in cart

- [x] 7.1 In the cart order card rendering, gate the journal bracket with `{o.journalId !== null && ...}` (journalId is already null for refining)
- [x] 7.2 Verify: add a refining order to cart → no journal bracket shown; add a craft order → bracket shown

## 8. Cart clear button

- [x] 8.1 Remove the old plain-text clear button at line 1365 (`<button onClick={onClear} ...>{t("clearAllBtn")}</button>`)
- [x] 8.2 Add a subtle clear button in the cart header (next to the title `<h2>`), gated with `{cart.length > 0 && (...)}`
- [x] 8.3 Style: small icon button with trash icon or "Limpar" text, `text-zinc-500 hover:text-red-400`, subtle
- [x] 8.4 Verify: cart empty → no button; cart has items → button visible; click → cart clears

## 9. Verification

- [x] 9.1 `npx tsc --noEmit` passes with zero errors
- [x] 9.2 `npm run build` passes
- [ ] 9.3 Select a bonus-city family → sell city auto-sets to bonus city; production city stays as saved
- [ ] 9.4 Manually change sell city → switch family → sell city preserved
- [ ] 9.5 Toggle Focus on → profit+F column turns yellow; toggle off → profit−F column turns yellow
- [ ] 9.6 Add order to cart with focus on → focus bracket shown; toggle off, add order → no bracket
- [ ] 9.7 Refining row with transmute → T badge shown, no purple row; hover/click shows details
- [ ] 9.8 Refining row with heart → H badge shown; hover/click shows details
- [ ] 9.9 Material price "12345678" visible without truncation
- [ ] 9.10 Column headers show two lines (label + subtitle)
- [ ] 9.11 Journal count in table and cart shows integer (ceiling)
- [ ] 9.12 Refining order in cart → no journal bracket
- [ ] 9.13 Cart empty → no clear button; cart has items → clear button visible; click → empties
- [ ] 9.14 Cart summary shows total focus cost