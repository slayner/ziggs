## Why

The craft page has accumulated several UX gaps and regressions from the refining integration work. The user identified nine concrete pain points while dogfooding the page with more focus on crafting:

1. **Sell city default is Black Market** for every family — but most crafted items sell best in their bonus city. The user has to manually change it every time they switch families.
2. **Material price inputs are too narrow** — only 3 characters visible (e.g. "999" fits but "1500" is cut off). Need to see up to 8 characters for realistic silver prices.
3. **Production city default is Caerleon** for items without a bonus city (journals, pickaxes, etc.) — Lymhurst is the better default hub.
4. **Column headers are single-line and terse** — "Materiais", "Lucro −F", "Lucro +F" don't explain enough. A two-line header with subtitles (e.g. "Materiais / preço unitário", "Lucro / sem focus", "Lucro / com focus") would be clearer.
5. **Journal count shows decimals** (e.g. "2.3") — journals are discrete items; should always round up.
6. **Refining transmutation and heart are separate purple rows** — visually noisy and doesn't match the "one row per recipe" principle. Should be inline indicators (T/H badges) on the row itself, with hover/click tooltips showing the details. The row's final prices should already reflect the best option.
7. **No focus toggle** — the `useFocus` was hardcoded to `true` during refining integration, losing the ability to plan non-focus crafts. The toggle needs to come back (next to Premium), profit should turn yellow when focus is active, and the cart order should record whether it uses focus. The cart should also show total focus cost per order and a focus bracket.
8. **Refining orders still show journal brackets in cart** — refining has no journals; the bracket should be suppressed.
9. **Cart clear button is a plain text link** — should be a proper subtle button, only visible when the cart has items.

## What Changes

- **Smart sell-city default**: `sellCities` initializes to `[family.bonusCity ?? "Lymhurst"]` and updates when the family changes (unless the user has manually overridden it).
- **Wider material price inputs**: `PriceField` width increases to fit 8 characters; the materials column and surrounding layout adjust to make room.
- **Smart production-city default**: `productionLocation` default changes from Caerleon to Lymhurst for families without a bonus city.
- **Two-line column headers**: `<Th>` supports an optional subtitle line; column headers gain subtitles (Materiais → "preço unitário", Lucro −F → "sem focus", Lucro +F → "com focus", etc.).
- **Journal count rounding**: `journalsFilled` result is always `Math.ceil`'d at every display site (table, cart).
- **Inline transmute/heart indicators on refining rows**: separate purple rows removed; instead a `T` (purple) and/or `H` (red) badge appears after the item name, with hover/click tooltips showing the transmute source→target+cost or heart variant details. The row's profit already uses the better option.
- **Focus toggle restored**: `useFocus` becomes state (not constant); a Focus toggle button appears next to Premium; profit text turns yellow when focus is active; the spreadsheet (profit filtering, SPF threshold) respects the toggle; each cart order records `useFocus` and its per-item focus cost; cart shows total focus per order as a bracket.
- **No journal brackets for refining in cart**: cart order rendering checks `isRefiningFamilyCat` (or the order's `journalId === null`) and suppresses the journal bracket.
- **Cart clear button**: restyled as a subtle icon button in the cart header, only shown when `cart.length > 0`.

## Capabilities

### New Capabilities
- `craft-smart-defaults`: Sell-city and production-city defaults that adapt to the selected family's bonus city, with Lymhurst as the fallback.
- `craft-focus-toggle`: Focus toggle that controls profit calculation, profit highlighting, and per-order focus cost in the cart.
- `craft-refining-inline-indicators`: Transmute (T) and heart (H) badges on refining rows replacing separate purple rows, with hover/click detail tooltips.
- `craft-table-ux`: Wider price inputs, two-line column headers, journal count rounding, cart clear button polish.

### Modified Capabilities
<!-- No existing specs to modify. -->

## Impact

- **Frontend**: `frontend/src/components/CraftCalculator.tsx` — main changes: `useFocus` state, `sellCities` smart default + family-change effect, `productionLocation` default, `PriceField` width, `Th` subtitle support, row rendering (T/H badges replacing purple rows), cart rendering (focus bracket, journal suppression, clear button), journal count `Math.ceil` at display sites.
- **Frontend**: `frontend/src/lib/craft/engine.ts` — `journalsFilled` stays fractional (it's a pure calc); rounding moves to display sites.
- **Frontend**: `frontend/src/i18n/index.ts` — new keys for subtitles, focus toggle, transmute/heart tooltips, focus bracket.
- **No backend changes**.