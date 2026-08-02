## 1. Multi-city dropdown component (DONE in working tree)

- [x] 1.1 Create `MultiCitySelect` component with checkbox dropdown, city color dots, abbreviated labels
- [x] 1.2 Change state: `sellCity: string` → `sellCities: string[]`, `groupMarket` → `groupBuyCities: Record<string, string[]>`
- [x] 1.3 Update helpers: `cityForGroup` → `citiesForGroup` (returns `string[]`), `cityForMat` → `citiesForMat`
- [x] 1.4 Rewrite `fetchMarket` to query union of all selected cities, pick min (buy) / max (sell), store winning city in meta
- [x] 1.5 Replace `<select>` in SettingsPanel with `<MultiCitySelect>` for sell and per-group buy
- [x] 1.6 Update SettingsPanel prop types and destructuring
- [x] 1.7 Verify `tsc --noEmit` and `npm run build` pass

## 2. Refining catalog loading and variation building (PARTIALLY DONE)

- [x] 2.1 Add `refiningCatalog` state, load via `loadRefiningCatalog()` in mount effect
- [x] 2.2 Add `refiningSpecs` state (persisted via `loadRefiningSpecs`/`saveRefiningSpecs`)
- [x] 2.3 Add `heartPriceByFamily` state
- [x] 2.4 Add useEffect that replaces placeholder variations with real `CatalogVariation[]` when catalog loads
- [x] 2.5 Remove "Refino:" prefix from `REFINING_FAMILY_NAMES`
- [x] 2.6 Set `bonusCity` on synthetic refining families from `REFINING_CITIES`
- [x] 2.7 Remove standalone "Transmutação" picker entry (transmutation becomes purple rows)
- [x] 2.8 Add `heartTokenForFamily` helper (derives heart token ID from catalog at runtime)
- [x] 2.9 Add `isRefiningFamilyCat` helper (detects `category === "refining"`)

## 3. Return rates and focus efficiency overrides

- [x] 3.1 Add `refiningFamKey` derived value from `isRefiningFamilyCat(family)`
- [x] 3.2 Override `rrNoFocus`/`rrFocus` with `refiningReturnRateNoFocus/Focus` when refining (useMemo)
- [x] 3.3 Add `refiningFceByTier` Map (T4-T8) for per-tier focus efficiency
- [x] 3.4 Override `focusMult` with `refiningFocusMultiplier` when refining
- [x] 3.5 Update `compute(v)` to use `refiningFceByTier[v.tier]` when refining
- [x] 3.6 Skip journals: `journalIds = []` when `isRefiningFamilyCat(family)`

## 4. SettingsPanel: heart price + refining specs

- [x] 4.1 Add new props to SettingsPanel: `isRefining`, `refiningSpecs`, `setRefiningSpec`, `heartPrice`, `heartTokenId`, `setHeartPrice`, `heartMarketPrice`
- [x] 4.2 When `isRefining`: hide "Ignorar jornais" panel, show "Coração" panel (heart icon, manual price input, market price hint)
- [x] 4.3 When `isRefining`: hide craft sibling-tree focus panel, show refining T4-T8 specs panel (5 numeric inputs)
- [x] 4.4 When `isRefining`: change bonus indicator text from "+15%" to "+40%"
- [x] 4.5 Pass new props from CraftMode call site (already wired in working tree — verify types match)

## 5. Row rendering: heart variant + transmutation purple rows

- [x] 5.1 Build `heartVariantByOutputId` Map from refining catalog (lookup heart variant per recipe)
- [x] 5.2 Build `transmuteRoutesByTarget` (or compute per-row) using `findRoutes` + `cheapestOption`
- [x] 5.3 In row loop: when refining, compute heart variant profit and compare to normal; show ♥ if heart wins
- [x] 5.4 In row loop: when refining, check transmutation for primary raw material; render purple row above if cheaper
- [x] 5.5 When transmutation is cheaper, override the material's effective price in `compute(v)` with transmute cost; visually mark price field (purple text/badge)
- [x] 5.6 Skip journal column rendering for refining rows (show "—")
- [x] 5.7 Add heart token ID to `fetchMarket`'s `allItems` when family is refining (so market price is pulled)

## 6. Remove standalone branches

- [x] 6.1 Remove the `if (familyKey === TRANSMUTATION_KEY)` early-return branch in CraftMode
- [x] 6.2 Remove the `if (isRefiningFamily(familyKey))` early-return branch that renders `<RefiningCalculator>`
- [x] 6.3 Remove `import RefiningCalculator` and `import TransmutationCalculator` from CraftCalculator.tsx
- [x] 6.4 Delete `TransmutationCalculator.tsx` (transmutation now lives as purple rows)
- [x] 6.5 Keep `RefiningCalculator.tsx` for now (fallback reference); mark for deletion in follow-up

## 7. Verification

- [x] 7.1 `npx tsc --noEmit` passes with zero errors
- [x] 7.2 `npm run build` passes
- [ ] 7.3 Select a refining family in the picker → rows appear in main table with correct materials
- [ ] 7.4 Change production city to bonus city → return rate increases (SettingsPanel shows +40%)
- [ ] 7.5 Set heart price → ♥ icon appears on rows where heart variant is more profitable
- [ ] 7.6 Check transmutation purple row appears when transmute is cheaper than direct buy
- [ ] 7.7 Double-click a refining row → adds to cart with correct qty and no journal cost
- [ ] 7.8 Multi-city: select 2 buy cities → material price is the min across both
- [ ] 7.9 Multi-city: select 2 sell cities → sell price is the max across both
- [ ] 7.10 No "Transmutação" entry in picker; no standalone RefiningCalculator rendered