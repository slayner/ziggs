## Why

The craft page shipped with two profit columns, redundant header text, integer
values showing decimals, and T/H badges that conflict with the row click
target. Refining raw-material city defaults point to the wrong cities, and
item names show in English because `names.json` is EN-only while the refining
catalog already carries PT/ES translations. A round of user feedback surfaced
ten concrete polish issues that hurt readability and trust.

## What Changes

- **Header simplification**: strip redundant parentheticals from primary labels
  ("Materiais (preço unit.)" → "Materiais", "Venda (méd)" → "Venda",
  "Lucro -F"/"Lucro +F" → "Lucro"); drop the "preço unit." subtitle from
  Jornais; center both header lines.
- **Single profit column**: merge the two profit columns (-F / +F) into one
  that follows the Focus toggle; remove the emerald item-name coloring when
  SPF is high.
- **Reorder columns**: move Focus cost to the right of Lucro.
- **Integer focus**: focus values in the cart render as integers (no decimal
  places).
- **No trailing ",0"**: journals total and focus total in the cart summary
  render as integers when whole, not `1.234,0`.
- **Always-visible T/H badges**: render T and H badges in every refining row as
  dim/inactive when not active, bright when active; fix T badge not appearing
  (transmute detection logic).
- **T/H click without row conflict**: stop event propagation on badge click so
  it doesn't trigger `addOrder`; show a popover/tooltip on hover or click.
- **Refining raw-material city defaults**: raw materials (wood, fiber, hide,
  ore, stone) default to all 5 royal cities (Fort Sterling, Lymhurst,
  Bridgewatch, Thetford, Martlock) as price sources, not the bonus city; the
  refined output keeps the bonus city as sell default.
- **Item name translations**: use the refining catalog's `names` map
  (PT/EN/ES) for refining items instead of the EN-only `names.json`.
- **Cart clear button**: replace the square 🗑 emoji with a non-square trash
  icon (Tabler Icons `ti-trash`) and make it more discoverable.

## Capabilities

### New Capabilities
- `craft-page-polish`: UI polish and correctness fixes for the craft/refining
  calculator — header layout, profit column merge, integer formatting, badge
  behavior, refining city defaults, item name localization, and cart clear
  button.

### Modified Capabilities
<!-- None — no existing specs in openspec/specs/. -->

## Impact

- `frontend/src/components/CraftCalculator.tsx`: header labels, profit column
  merge, column order, T/H badge rendering and click handling, cart focus/journal
  formatting, cart clear button, refining `nameOf` lookup, raw-material city
  defaults.
- `frontend/src/i18n/index.ts`: remove redundant subtitle keys no longer used
  (colProfitNoFocusSub, colProfitFocusSub, colJournalsSub if dropped); adjust
  primary labels (colProfitNoFocus/colProfitFocus → single key; colSellAvg;
  colMaterials).
- `frontend/src/lib/format.ts`: no change — integer formatting already exists
  via `silver()`; the fix is using `silver()` instead of `decimal()` for focus
  and journal totals.
- No backend or API changes.