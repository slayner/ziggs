## Context

The craft calculator (`CraftCalculator.tsx`, ~1695 lines) renders a table of
craft/refining variations with columns: Item, Materiais, Focus cost, Venda,
Lucro −F, Lucro +F, SPF, Jornais, Demanda. A Focus toggle switches which
profit column is highlighted, but both are always visible. Refining rows show
inline T (transmute) and H (heart) badges conditionally — T only when a
transmute route is cheaper than direct, H only when heart wins. Row
double-click adds to cart; badge clicks bubble up to the row and trigger
add-to-cart unintentionally.

Item names come from `names.json` (EN-only, 5380 entries). The refining
catalog (`/data/refining.json`) already carries `names: Record<id, {en,pt,es}>`
for all refining inputs/outputs, but `nameOf()` only consults `names.json`.

Raw material city defaults use `DEFAULT_GROUP_CITY` which covers refined
materials (PLANKS→Fort Sterling, METALBAR→Thetford, etc.) but has no entries
for raw materials (WOOD, ORE, ROCK, CLOTH, LEATHER). They fall back to
"Caerleon" — wrong for refining, where raw materials trade across all 5 royal
cities.

## Goals / Non-Goals

**Goals:**
- Cleaner table headers with two-line layout, no redundant text
- One profit column that follows the Focus toggle
- T/H badges always visible (dim when inactive) and clickable without
  triggering row actions
- Integer focus and journal totals in the cart (no ",0" suffix)
- Refining raw-material price sources default to all 5 royal cities
- Refining item names localized (PT/EN/ES) from the refining catalog
- More discoverable cart clear button with a non-square icon

**Non-Goals:**
- Changing the cart data model or order interface
- Adding new columns or data fields
- Backend/API changes
- Refactoring RefiningCalculator.tsx (legacy, kept as reference)
- Replacing `names.json` globally (only refining items get localized names)

## Decisions

### 1. Single profit column

**Decision:** Replace the two `<Th>` + two `<Td>` profit columns with one
column. The header reads "Lucro" (no -F/+F suffix). The cell value is
`useFocus ? r.profitFocus : r.profitNoFocus`, highlighted amber always (active
column). Remove `colProfitNoFocus`, `colProfitFocus`, `colProfitNoFocusSub`,
`colProfitFocusSub`, `colProfitNoFocusTitle`, `colProfitFocusTitle` i18n keys.

**Alternative considered:** Keep both columns but hide one — rejected, wastes
horizontal space and the toggle already communicates intent.

### 2. Column order: Focus cost after Lucro

**Decision:** Reorder table headers and cells from
`[Item, Materiais, Focus, Venda, Lucro, SPF, Jornais, Demanda]` to
`[Item, Materiais, Venda, Lucro, Focus, SPF, Jornais, Demanda]`.

**Rationale:** Profit is the primary metric; Focus cost is secondary detail.

### 3. Integer focus and journal totals

**Decision:** Use `silver()` (which rounds to integer, pt-BR thousands
separator) instead of `decimal()` for: cart summary total focus
(`Math.round(totalFocus)` → `silver(totalFocus)`), cart order focus bracket
(`silver(Math.round(o.focusCostPerItem * o.qty))`), and journal total in
cart summary (`silver(journalTotal)` or just `journalTotal` as integer).

The table Jornais column already uses `Math.ceil` + `decimal()` — switch to
`silver()` or a plain integer format so `5` doesn't show as `5,0`.

**Why not change `decimal()` itself:** `decimal()` is used for SPF and other
non-integer values; changing it would break those. The fix is at call sites.

### 4. Always-visible T/H badges with click isolation

**Decision:** In refining rows, always render both T and H `<span>` badges
next to the item name. When inactive (no transmute / heart doesn't win), render
with `opacity-30` and no background. When active, render with the current
bright style (purple bg for T, red bg for H). Add `onClick` with
`e.stopPropagation()` to prevent row double-click trigger. Use native `title`
tooltip for details (no popover component — ponytail).

**T badge not appearing:** The current logic only sets `transmuteInfo` when
`best.kind === "route"`. If `cheapestOption` returns the direct price (no
route is cheaper), `transmuteInfo` stays null. With always-visible badges this
is fine — T shows dim. But if transmute routes exist and are cheaper, T should
brighten. The bug is likely that `transmuteRoutesByTarget` is not populated for
all refining families. Investigate the `transmuteRoutesByTarget` Map build.

### 5. Remove emerald item name for high SPF

**Decision:** Remove the `isProfit ? "text-emerald-400" : "text-zinc-200"`
conditional on the tier label span. Always render `text-zinc-200`.

**Rationale:** The user finds it noisy and misleading — green name doesn't
mean "best profit", just "profitable with current filter".

### 6. Header label simplification

**Decision:** Update i18n values:
- `colMaterials`: "Materiais (preço unit.)" → "Materiais" (subtitle keeps
  "preço unitário")
- `colSellAvg`: "Venda (méd)" → "Venda" (subtitle keeps "médio")
- `colProfitNoFocus`/`colProfitFocus` → replaced by single `colProfit`:
  "Lucro"
- `colJournalsSub`: drop — Jornais header has no subtitle

Center both header lines: `Th` already supports `right` alignment; for
centered columns add a `center` option or use `text-center` on the `<th>`.
Actually the current headers are left-aligned for Item, right-aligned for
numeric columns. "Centralize" here means center the subtitle under the label
within the column — already handled by `items-end`/default. Confirm visually.

### 7. Refining raw-material city defaults

**Decision:** Add raw material groups to `DEFAULT_GROUP_CITY` with all 5 royal
cities as the default, not a single city. This requires changing
`citiesForGroup` to return an array from `DEFAULT_GROUP_CITY` instead of a
single city wrapped in an array.

Change `DEFAULT_GROUP_CITY` from `Record<string, string>` to
`Record<string, string[]>` for raw materials:
```
WOOD: ["Fort Sterling", "Lymhurst", "Bridgewatch", "Thetford", "Martlock"],
ORE:  ["Fort Sterling", "Lymhurst", "Bridgewatch", "Thetford", "Martlock"],
ROCK: ["Fort Sterling", "Lymhurst", "Bridgewatch", "Thetford", "Martlock"],
CLOTH: ["Fort Sterling", "Lymhurst", "Bridgewatch", "Thetford", "Martlock"],
LEATHER: ["Fort Sterling", "Lymhurst", "Bridgewatch", "Thetford", "Martlock"],
```
Keep refined materials as single-city arrays: `PLANKS: ["Fort Sterling"]`,
etc.

`citiesForGroup` becomes:
```ts
groupBuyCities[g]?.length ? groupBuyCities[g] : (DEFAULT_GROUP_CITY[g] ?? ["Caerleon"])
```

**Rationale:** Raw materials (logs, ore, rock, cloth, leather) are gathered
across all biomes and traded in all royal cities. Limiting to one city misses
price arbitrage. Refined materials (planks, bars, cloth, leather, blocks) have
a clear best city (the refining bonus city) where supply concentrates.

### 8. Refining item name localization

**Decision:** Build a merged name map: start with `names.json` entries, then
overlay refining catalog `names` (PT/EN/ES) for any ID that exists in both.
The `nameOf` function currently does `shortName(names[id.split("@")[0]] ?? id)`.
Add a `refiningNames` state from `refiningCatalog.names`, and in `nameOf`:
```ts
const base = id.split("@")[0];
const refName = refiningNames[base];
const langName = refName
  ? refName[lang] ?? refName.en ?? refName.pt
  : names[base];
return shortName(langName ?? id);
```

**Alternative:** Replace `names.json` with the refining catalog globally —
rejected, `names.json` covers 5380 items, refining catalog only covers
refining-related ones.

### 9. Cart clear button icon

**Decision:** Replace `🗑` emoji with `<i className="ti ti-trash" />` (Tabler
Icons, already used across the app — see `GuildConfig.tsx`). Keep the existing
placement (cart header) and gating (`cart.length > 0`). Adjust styling: remove
the square `rounded-md border` wrapper, make it a bare icon button like other
icon-only buttons in the app: `text-zinc-500 hover:text-red-400`.

## Risks / Trade-offs

- **Single profit column loses at-a-glance comparison** → The Focus toggle is
  one click away; users who compare both can toggle back and forth. Acceptable
  trade-off for table width.
- **Always-visible T/H badges add visual noise to every refining row** → Dim
  (opacity-30) inactive state keeps them unobtrusive. Better than invisible —
  users know the feature exists.
- **`DEFAULT_GROUP_CITY` type change from `string` to `string[]`** → Only
  affects `citiesForGroup` consumer; no other callers. Low risk.
- **Refining name overlay could show different names than `names.json`** →
  Refining catalog names are authoritative for refining items; `shortName`
  still strips tier adjectives. If a name differs, the refining catalog wins
  (it's curated).
- **T badge "not appearing" bug** → Root cause investigation needed during
  implementation; the fix may be in `transmuteRoutesByTarget` Map construction
  or the `cheapestOption` comparison logic.