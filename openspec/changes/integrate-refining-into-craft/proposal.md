## Why

Refining and transmutation currently live in two standalone components (`RefiningCalculator`, `TransmutationCalculator`) that duplicate the craft page's controls (location, market, batch, premium, fee) and don't share the cart or SettingsPanel. The user wants refining to be a first-class item family inside the craft page: same table, same shared controls, same cart, plus multi-city buy/sell selection. This unifies the experience, removes duplicated UI, and lets a refiner compare refining rows against craft rows using the same market data and settings.

## What Changes

- **Refining families become item types in the craft picker** (no "Refino:" prefix; image is the normal material icon without the excellent-quality border).
- **Refining rows render inside the main CraftMode table** (not a standalone component), reusing the shared SettingsPanel (location/bonus, market, specialization) and the Cart.
- **Multi-city buy/sell**: "Comprar em" and "Vender em" selectors become multi-city dropdowns (checkboxes); the fetched price is the min across buy cities for materials, and the max across sell cities for the output.
- **Refining return rates and focus efficiency** use the refining formulas (`refiningReturnRateNoFocus`/`Focus` with +40% city bonus, per-tier FCE from `refiningFocusEfficiency`) instead of the craft formulas, when the selected family is a refining family.
- **Refining ignores journals entirely** (no journal ids, no journal column, no journal cost in cart).
- **SettingsPanel swaps "Ignorar jornais" → "Coração"** for refining families: a manual heart-token price input (per family), with the market-pulled price shown alongside as a hint.
- **Heart variant**: when the heart variant is more profitable than the normal variant (using the configured heart price), a ♥ icon shows on the row and the row's profit is computed using the heart variant's inputs (fewer raw + 1 heart token at the configured price).
- **Transmutation as a purple row** (like artifact rows): when buying the raw material via transmutation (source item + silver cost + station fee) is cheaper than buying it directly, a purple row appears above the refining row showing the transmute source → target with the effective cost, in a distinct color.
- **BREAKING**: the standalone `TransmutationCalculator` picker entry and component are removed; transmutation is now surfaced as purple rows inside refining family tables.

## Capabilities

### New Capabilities
- `craft-multi-city-pricing`: Multi-city selection for buy (materials) and sell (output) in the craft calculator, fetching prices across all selected cities and aggregating to min (buy) / max (sell).
- `craft-refining-integration`: Refining families rendered as first-class rows in the craft page table, with refining-specific return rates, per-tier focus efficiency, no journals, heart-variant comparison, and transmutation purple rows.

### Modified Capabilities
<!-- No existing specs to modify. -->

## Impact

- **Frontend**: `frontend/src/components/CraftCalculator.tsx` — major: new state (refining catalog, refining specs, heart prices, multi-city sell/buy), SettingsPanel props and rendering, row rendering for refining, transmutation purple rows, removal of standalone branches.
- **Frontend**: `frontend/src/components/RefiningCalculator.tsx` — becomes unused by CraftMode; kept for now (can be deleted once integration is verified).
- **Frontend**: `frontend/src/components/TransmutationCalculator.tsx` — removed (transmutation now lives as purple rows).
- **Frontend libs**: `lib/craft/refining.ts`, `lib/craft/transmutation.ts`, `lib/craft/refiningCatalog.ts`, `lib/craft/location.ts` — reused as-is (already validated against reference spreadsheet).
- **Data**: `public/data/refining.json` — no changes (already has float `itemValue` for transmutations).
- **No backend changes** — the AODP API already supports multi-city queries via comma-separated `locations`; the existing `fetchAdpPrices` already passes a locations array.