## Context

The craft calculator (`CraftCalculator.tsx`, ~1664 lines) is the central hub for crafting economics. It was recently extended to integrate refining families as first-class rows (change `integrate-refining-into-craft`). This change addresses nine UX gaps and regressions identified while dogfooding the page with more focus on crafting.

Key current state:
- `useFocus` is hardcoded to `true` (line 287) — the toggle was removed during refining integration.
- `sellCities` defaults to `["Black Market"]` (line 312) — no smart default per family.
- `productionLocation` defaults to `{ kind: "city", city: "Caerleon" }` (line 291) — Lymhurst is better for no-bonus families.
- `PriceField` width is `w-16` (4rem) in the Materials column — only ~3 chars visible.
- Column headers are single-line via `<Th>` (line 1557) — no subtitle support.
- `journalsFilled` returns a float; display sites use `decimal(journals)` which shows decimals.
- Transmutation is rendered as a separate purple `<tr>` above the refining row (lines 866-884).
- Heart variant shows a `♥` icon (line 914).
- Cart has a plain-text clear button (line 1365) that's always visible.
- Cart shows journal brackets for all orders including refining (line 1392).

## Goals / Non-Goals

**Goals:**
- Smart defaults for sell city and production city based on the selected family.
- Wider price inputs that show 8 characters.
- Two-line column headers with subtitles.
- Journal count always rounded up at display sites.
- Inline T/H badges on refining rows replacing separate purple rows, with hover/click tooltips.
- Focus toggle restored, with profit highlighting, filtering, and per-order focus cost in cart.
- No journal brackets for refining orders in cart.
- Polished cart clear button, only visible when cart has items.

**Non-Goals:**
- Changing the `journalsFilled` function itself (it stays fractional; rounding is at display).
- Changing the ADP API or backend.
- Adding new i18n locales (reuse existing PT/EN/ES; add new keys to all three).
- Redesigning the cart layout beyond the focus bracket and clear button.

## Decisions

### D1: Smart sell-city default via "userOverride" flag
**Decision:** Track a `sellCitiesUserOverride: boolean` state. When the family changes and `sellCitiesUserOverride` is false, set `sellCities` to `[family.bonusCity ?? "Lymhurst"]`. When the user manually changes `sellCities` (via `setSellCities`), set `sellCitiesUserOverride = true`. This preserves the user's choice across family switches without resetting.

**Rationale:** A useEffect on `family` that unconditionally sets `sellCities` would override the user's manual selection every time they switch families. The flag is the simplest way to distinguish "user chose this" from "default needs updating."

**Alternative considered:** Compare current `sellCities` to the previous family's bonus city and only reset if they match. Rejected because it's fragile (what if the user picks the bonus city manually, then switches families? It would incorrectly preserve it).

### D2: Production-city default via loadLocation fallback
**Decision:** Change the `loadLocation()` fallback from `{ kind: "city", city: "Caerleon" }` to `{ kind: "city", city: "Lymhurst" }`. This only affects first load with no saved location. The family-change smart-default for production city is NOT automatic (the user explicitly chooses the production city in the SettingsPanel; auto-changing it would be surprising). The spec only requires the initial default to be Lymhurst.

**Rationale:** The production city is a deliberate user choice (it affects return rates, bonus, etc.). Auto-changing it on family switch would be confusing. Changing only the initial fallback is the minimal fix.

### D3: PriceField width via w-28 + layout adjustment
**Decision:** Change the Materials column `PriceField` from `w-16` (4rem) to `w-28` (7rem ≈ 8 chars at current font size). To make room, the Materials column already uses `flex-nowrap`; the table's `overflow-x-auto` wrapper handles overflow. The overall table grid (`min-[1500px]:grid-cols-[300px_1fr_300px]`) gives the middle column enough room; if needed, the grid breakpoint can shift to `min-[1600px]`.

**Rationale:** `w-28` is the smallest Tailwind width that fits 8 numeric characters at `text-sm`. The table already scrolls horizontally on narrow viewports; making the inputs wider just shifts the scroll threshold slightly.

### D4: Two-line Th via subtitle prop
**Decision:** Extend `<Th>` to accept an optional `subtitle` prop. When present, the `<th>` renders the primary label in a `<span>` and the subtitle in a smaller `<span className="block text-[9px] ...">` below it. The `<th>` gets `whitespace-nowrap` to prevent wrapping.

**Rationale:** One-line change to the component; all call sites pass `subtitle` where relevant. No CSS restructuring needed — the subtitle is just a second span in the existing `<th>`.

### D5: Journal count Math.ceil at display sites
**Decision:** Wrap every `journalsFilled(...)` display with `Math.ceil(...)`. There are two display sites: the table Jornais column (line 858) and the cart order bracket (line 1380). The `journalsFilled` function itself stays fractional (it's used in intermediate calculations like the cart summary's `journalCount` Map, which already does `Math.ceil` at line 1371).

**Rationale:** Rounding at display is the minimal diff. Rounding in the function would change the cart summary logic (which sums fractional counts before ceiling — correct, because ceiling then summing would over-count).

### D6: Inline T/H badges with hover tooltip
**Decision:** Replace the separate purple `<tr>` for transmutation and the `♥` icon with inline badges: a purple "T" and/or red "H" `<span>` after the item tier label. Each badge has `title` (native HTML tooltip) for hover, and `onClick` that toggles a small popover (reuse the `ItemIcon` popover pattern or a simple `useState` + absolute div). The popover shows: for T — source icon → target icon, transmute cost vs direct price; for H — heart token icon, heart price, profit comparison. The row's prices already use the best option (transmuted cost for materials, heart variant profit).

**Rationale:** The user explicitly asked for this: "a linha roxa deve ser a própria linha do refino, e não uma linha separada... colocamos a linha roxa e então adicionamos caracteres como H (coração) T(transmute) cada um com uma cor talvez após o nome do ITEM." One row per recipe, with inline indicators + tooltips. Native `title` is the simplest hover; `onClick` popover is for richer detail.

**Alternative considered:** Pure CSS tooltip on hover (no click). Rejected because the detail (icons + prices) is too rich for a native tooltip, and click gives mobile parity.

### D7: Focus toggle — state + highlighting + cart
**Decision:** Change `const useFocus = true` to `const [useFocus, setUseFocus] = useState(true)`. Add a `<ToggleBtn>` next to Premium. For profit highlighting: when `useFocus` is true, the "Lucro +F" `<Td>` gets `className="text-amber-300"`; when false, the "Lucro −F" `<Td>` gets it. The `isProfit` filter already uses `useFocus` (line 857) — it's correct. For the cart: add `focusCostPerItem: number` to the `Order` interface, computed as `v.focus * focusMult` at add time. The cart order card shows a focus bracket `👁 {focusCostPerItem * qty} focus` when `o.useFocus` is true. The cart summary shows total focus = Σ for `useFocus` orders.

**Rationale:** `useFocus` was a constant; making it state is the minimal change. The `Order` interface already has `useFocus`; adding `focusCostPerItem` is one field. The bracket follows the same pattern as the existing place/journal/total brackets.

### D8: No journal bracket for refining in cart
**Decision:** In the cart order rendering, check `o.journalId === null` (which is already set for refining orders — `journalId: prof ? journalId(v.tier, prof) : null` at line 602, and `prof` is null for refining). When `journalId === null`, suppress the journal bracket.

**Rationale:** `journalId` is already null for refining. The display just needs a conditional check. No state changes needed.

### D9: Cart clear button — subtle, conditional
**Decision:** Move the clear button to the cart header (next to the title), restyle as a subtle icon button (trash icon `🗑` or text "Limpar" with hover-red), and gate it with `{cart.length > 0 && (...)}`. Remove the old plain-text clear button at line 1365.

**Rationale:** The old button was always visible even when empty. Moving it to the header and gating on `cart.length > 0` is the simplest fix. An icon button matches the existing `IconButton` pattern.

## Risks / Trade-offs

- **[Risk] Smart sell-city reset may surprise users** → Mitigation: the `sellCitiesUserOverride` flag ensures manual selections are preserved. First load always uses the default; subsequent family switches only update if the user hasn't overridden.
- **[Risk] Wider price inputs may cause table overflow on narrow screens** → Mitigation: the table already has `overflow-x-auto`; the scroll threshold shifts slightly. Acceptable trade-off for readability.
- **[Risk] Focus toggle changes profit filtering** → Mitigation: the `isProfit` logic already branches on `useFocus` (line 857); it was just always-true before. Making it state doesn't change the logic, just makes it controllable.
- **[Trade-off] Inline T/H badges lose the visual prominence of a full-width purple row** → Acceptable: the user explicitly asked for this. The badges + tooltips provide the same information in less space.
- **[Trade-off] `journalsFilled` rounding at display means the table and cart show ceiling, but the cart summary sums fractional then ceilings** → Correct: the summary should sum fractional counts per journal type, THEN ceiling (to avoid over-counting). The per-order bracket and table column ceiling individually.