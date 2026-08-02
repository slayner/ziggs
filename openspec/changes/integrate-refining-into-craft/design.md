## Context

The craft calculator (`CraftCalculator.tsx`, ~1550 lines) is the central hub for crafting economics. It has a picker (item families), a shared SettingsPanel (location/bonus, market, specialization, journals), a main table (one row per variation), and a cart. Refining and transmutation currently live in two separate components (`RefiningCalculator.tsx`, `TransmutationCalculator.tsx`) that duplicate the SettingsPanel's controls and don't share the cart.

The refining catalog (`public/data/refining.json`, 130 recipes, 191 transmutations, 5 heart conversions) and the formulas (`lib/craft/refining.ts`, `lib/craft/transmutation.ts`) are already implemented and validated against a reference spreadsheet. The ADP price API (`fetchAdpPrices`) already accepts multi-city `locations` arrays. The gap is purely in the UI integration layer.

Multi-city selection and the refining integration have been partially implemented in the working tree (the `MultiCitySelect` component, `sellCities`/`groupBuyCities` state, and the refining catalog loading are already in place). This design document captures the full plan including the remaining work.

## Goals / Non-Goals

**Goals:**
- Refining families render as first-class rows in the CraftMode table, sharing the SettingsPanel and Cart.
- Multi-city buy/sell selection with min (buy) / max (sell) price aggregation.
- Refining-specific return rates, per-tier focus efficiency, and no journals.
- Heart variant comparison with a ♥ indicator on rows.
- Transmutation as purple rows (like artifact rows) inside refining tables.
- Heart price input in SettingsPanel (replacing "Ignorar jornais" for refining).

**Non-Goals:**
- Backend changes (the ADP API already supports multi-city; no backend work needed).
- Refining chain expansion (the user explicitly said chain is not needed — each recipe is shown as-is).
- Hideout refining options (the user said NO hideout for refining; only city/island).
- Revalidating formulas (already validated against the reference spreadsheet).
- Deleting `RefiningCalculator.tsx` immediately (kept as fallback until integration is verified in-browser; can be removed in a follow-up).

## Decisions

### D1: Refining recipes → CatalogVariation at load time
**Decision:** When the refining catalog loads, replace the placeholder variations of synthetic refining families with real `CatalogVariation[]` built from the recipes (normal variant only). Heart variant info is stored in a parallel lookup (Map<outputId, RefiningVariant>) for row-time comparison.

**Rationale:** Reusing the existing `CatalogVariation` type and the `computeCraft` engine avoids a parallel rendering path. The normal variant's inputs map directly to `CatalogResource` (`uniqueName`, `count`, `noReturn`). The heart variant is not a separate row — it's a comparison that flips the row's effective inputs when it wins.

**Alternative considered:** A separate rendering path for refining that bypasses `CatalogVariation`. Rejected because it would duplicate the table rendering, cart integration, and market fetching logic.

### D2: Per-tier focus efficiency via a Map
**Decision:** Compute a `refiningFceByTier: Record<number, number>` (T4-T8) for the selected refining family, and in the `compute(v)` function, use `refiningFceByTier[v.tier]` when the family is refining, falling back to the single `focusFce` for craft.

**Rationale:** Craft's `focusFce` is a single number per family (weapon spec + sibling tree). Refining's efficiency is per-tier (250×own_tier_spec + 30×sum_of_all_specs). A Map avoids changing the `computeCraft` signature while giving per-row precision.

### D3: Return rate override via useMemo
**Decision:** When `refiningFamKey` is non-null, compute `rrNoFocus`/`rrFocus` via `refiningReturnRateNoFocus/Focus(place, craftCity, family, eventBonus, hoQuality, hoLevel)`. Otherwise use the existing `returnRateNoFocus/Focus(location)`. Both are wrapped in `useMemo` keyed on their inputs.

**Rationale:** The refining formula uses a different bonus structure (+40% city spec, not +15%) and a different function signature. A branch at the call site is the smallest diff that covers all downstream consumers (rows, cart, SettingsPanel display).

### D4: Multi-city via arrays + min/max aggregation in fetchMarket
**Decision:** Change `sellCity: string` → `sellCities: string[]` and `groupMarket: Record<string, string>` → `groupBuyCities: Record<string, string[]>`. In `fetchMarket`, pass the union of all selected cities as `locations`. For materials, iterate all buy cities per group and pick the min price. For sell, iterate all sell cities and pick the max per-city average. Store the winning city in `matMeta`/`sellMeta` for the price-source indicator.

**Rationale:** The ADP API already returns per-city rows. The aggregation is O(cities × items) which is negligible for typical selections (2-5 cities). This avoids multiple API calls and keeps the price-source attribution accurate.

**Alternative considered:** Query each city separately. Rejected because it multiplies API calls and the ADP API explicitly supports comma-separated locations.

### D5: Heart variant comparison at row-render time
**Decision:** For each refining row, after computing the normal variant's `CraftResult`, compute the heart variant's result by temporarily swapping the materials to the heart variant's inputs (with the heart token priced at the effective heart price). If heart profit+F > normal profit+F and heart price > 0, display a ♥ icon and use the heart result's profit values. The material list shown is the winning variant's.

**Rationale:** The user wants a single row per tier×enchant with a ♥ indicator when heart wins. Computing both variants per render is cheap (the `computeCraft` function is pure arithmetic). Storing the heart variant lookup as a `Map<outputId, RefiningVariant>` avoids re-scanning the catalog per row.

### D6: Transmutation as purple row (same pattern as artifacts)
**Decision:** Before each refining row, check if the primary raw material can be obtained more cheaply via transmutation. If so, render a purple `<tr>` (same class as artifact rows: `border-t border-zinc-700 bg-purple-900/10`) showing source→target, transmute cost, and direct buy price. The transmutation cost uses `findRoutes` + `cheapestOption` from `lib/craft/transmutation.ts`.

**Rationale:** The user explicitly said "use a cor roxa na linha igual fazemos com a linha de mostrar o artefato do item". Reusing the artifact-row pattern (a full-width purple row above the item row) is visually consistent and requires no new CSS.

### D7: Transmuted material price used in calculation
**Decision:** When transmutation is cheaper for a material, override that material's effective price in the `compute(v)` call with the transmutation cost, and visually mark the price field (purple text or badge) to indicate it's transmuted.

**Rationale:** The user said "o preço do material bruto deve mudar e mostrar meio que o material bruto que ele precisa comprar e então mostra o resultado da transmutação mostrando o preço final e com uma cor diferente". The price changes and the color signals the transmutation.

### D8: SettingsPanel prop extension
**Decision:** Extend SettingsPanel with optional props: `isRefining`, `refiningSpecs`, `setRefiningSpec`, `heartPrice`, `heartTokenId`, `setHeartPrice`, `heartMarketPrice`. When `isRefining` is true: hide the "Ignorar jornais" panel, show the "Coração" panel; hide the craft sibling-tree focus panel, show the refining T4-T8 specs panel. The bonus indicator text changes from "+15%" to "+40%".

**Rationale:** Props are already passed conditionally from CraftMode. The SettingsPanel branches on `isRefining` to swap panels. This avoids creating a separate RefiningSettingsPanel component.

## Risks / Trade-offs

- **[Risk] `computeCraft` focus cost differs from refining by `Math.ceil` per craft** → Mitigation: The difference is at most 1 focus point per craft, negligible for batches of 30+. If precision matters, `refiningFocusCost` can be called separately and the `focusCost` field overridden in the result. Documented with a `ponytail:` comment.

- **[Risk] Heart token price not fetched when no buy city is selected for the heart's group** → Mitigation: The heart token is added to `allPriceIds` in `fetchMarket` when the family is refining, so it's fetched alongside other materials using the same buy-city settings. The manual input is the fallback.

- **[Risk] Transmutation routes require station fee which may not be set** → Mitigation: `stationFeePer100` is already a shared setting. The transmutation station fee uses the same value. If 0, transmutation cost = source price + silver cost, which is still a valid comparison.

- **[Risk] `RefiningCalculator.tsx` left as dead code** → Mitigation: It's not imported by CraftMode after integration. Can be deleted in a follow-up commit. Keeping it during development provides a reference for expected behavior.

- **[Trade-off] Per-tier FCE Map adds a useMemo per family switch** → Acceptable: the map is 5 entries, recomputed only when specs or family change.

- **[Trade-off] Heart variant computed on every render even when heart price is 0** → Acceptable: the computation is a few multiplications; skipping it when `heartPrice === 0` is a micro-optimization that adds a branch.