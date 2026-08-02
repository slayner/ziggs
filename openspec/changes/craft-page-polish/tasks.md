## 1. Header simplification

- [x] 1.1 Update i18n `colMaterials` to "Materiais" (PT/EN/ES) — remove "(preço unit.)" from primary label
- [x] 1.2 Update i18n `colSellAvg` to "Venda" (PT: "Venda", EN: "Sell", ES: "Venta") — remove "(méd)" from primary label
- [x] 1.3 Add i18n key `colProfit` = "Lucro"/"Profit"/"Lucro" (replaces colProfitNoFocus + colProfitFocus)
- [x] 1.4 Remove i18n keys: `colProfitNoFocus`, `colProfitFocus`, `colProfitNoFocusTitle`, `colProfitFocusTitle`, `colProfitNoFocusSub`, `colProfitFocusSub`, `colJournalsSub` (no longer used)
- [x] 1.5 Remove `subtitle={t("colJournalsSub")}` from the Jornais `<Th>` call site
- [x] 1.6 Verify both header lines are centered within their column (check `Th` component `items-end`/default alignment)

## 2. Single profit column

- [x] 2.1 Replace the two profit `<Th>` headers with a single `<Th right>{t("colProfit")}</Th>`
- [x] 2.2 Replace the two profit `<Td>` cells with a single `<Td right value={ok ? (useFocus ? r.profitFocus : r.profitNoFocus) : undefined} sub={ok ? percent(useFocus ? r.marginFocus : r.marginNoFocus) : undefined} highlight>{ok ? silverShort(useFocus ? r.profitFocus : r.profitNoFocus) : "—"}</Td>`
- [x] 2.3 Remove the emerald item-name coloring: change `${isProfit ? "text-emerald-400" : "text-zinc-200"}` to always `text-zinc-200` on the tier label span
- [x] 2.4 Verify: toggle Focus on → Lucro shows profitFocus; toggle off → shows profitNoFocus

## 3. Reorder columns (Focus cost after Lucro)

- [x] 3.1 In the `<thead>`, move the Focus cost `<Th>` from position 3 to position 5 (after Venda/Lucro, before SPF)
- [x] 3.2 In the row `<td>` cells, move the Focus cost `<Td>` to match the new header order
- [x] 3.3 Verify column order: Item, Materiais, Venda, Lucro, Focus cost, SPF, Jornais, Demanda

## 4. Integer focus in cart

- [x] 4.1 In cart summary, change `decimal(Math.round(totalFocus))` to `silver(totalFocus)` (silver() already rounds to integer)
- [x] 4.2 In cart order focus bracket, change `decimal(Math.round(o.focusCostPerItem * o.qty))` to `silver(o.focusCostPerItem * o.qty)`
- [x] 4.3 Verify: focus values show without decimal places (e.g. "1.234" not "1.234,0")

## 5. Integer journal totals

- [x] 5.1 In table Jornais column, change `decimal(journals)` to `silver(journals)` (journals is already Math.ceil'd)
- [x] 5.2 In cart order journal bracket, change `decimal(journals)` to `silver(journals)`
- [x] 5.3 In cart summary journal total display, ensure `journalTotal` shows as integer (it's already Math.ceil'd in the loop — check the `({journalTotal})` display)
- [x] 5.4 Verify: journal counts show without decimal places

## 6. Always-visible T/H badges with click isolation

- [x] 6.1 In the refining row, always render T badge: when `transmuteInfo` is null, render dim (`opacity-30` + no bg); when active, render current bright style
- [x] 6.2 In the refining row, always render H badge: when `!heartWins`, render dim; when active, render current bright style
- [x] 6.3 Add `onClick={(e) => { e.stopPropagation(); }}` to both T and H badge `<span>` elements
- [x] 6.4 Investigate why T badge is not appearing: check `transmuteRoutesByTarget` Map construction and `cheapestOption` logic — fix if routes are not being found for refining families
- [x] 6.5 Verify: T and H badges always visible in refining rows; clicking them does not add to cart; row double-click outside badges still adds to cart

## 7. Refining raw-material city defaults

- [x] 7.1 Change `DEFAULT_GROUP_CITY` type from `Record<string, string>` to `Record<string, string[]>`; convert existing entries to single-element arrays (e.g. `PLANKS: ["Fort Sterling"]`)
- [x] 7.2 Add raw material entries: `WOOD`, `ORE`, `ROCK`, `CLOTH`, `LEATHER` → `["Fort Sterling", "Lymhurst", "Bridgewatch", "Thetford", "Martlock"]`
- [x] 7.3 Update `citiesForGroup` to spread the array: `groupBuyCities[g]?.length ? groupBuyCities[g] : (DEFAULT_GROUP_CITY[g] ?? ["Caerleon"])`
- [x] 7.4 Verify: select wood refining → raw wood price sources show all 5 royal cities; select a craft family using planks → planks still default to Fort Sterling

## 8. Refining item name localization

- [x] 8.1 Add `refiningNames` state: `useState<Record<string, { en: string; pt: string; es: string }>>({})` — populated from `refiningCatalog.names` when catalog loads
- [x] 8.2 Update `nameOf` to check refining names first: `const refName = refiningNames[base]; const langName = refName ? (refName[lang] ?? refName.en) : names[base]; return shortName(langName ?? id);`
- [x] 8.3 Ensure `lang` is available in the scope where `nameOf` is defined (it uses `useLang()` already — verify)
- [x] 8.4 Verify: PT lang → refining items show PT names (e.g. "Troncos de Pinheiro"); EN lang → EN names; non-refining items unaffected

## 9. Cart clear button icon

- [x] 9.1 Replace `🗑` emoji with `<i className="ti ti-trash" />` in the cart clear button
- [x] 9.2 Remove the square `rounded-md border border-zinc-700 px-2 py-1` wrapper; use bare icon button style: `text-zinc-500 hover:text-red-400`
- [x] 9.3 Verify: trash icon is a Tabler icon (non-square), visible when cart has items, hidden when empty

## 10. Verification

- [x] 10.1 `npx tsc --noEmit` passes with zero errors
- [x] 10.2 `npm run build` passes
- [x] 10.3 Table headers: "Materiais" (no parenthetical), "Venda" (no parenthetical), "Lucro" (single column), "Jornais" (no subtitle)
- [x] 10.4 Focus toggle: Lucro column switches between profitFocus/profitNoFocus
- [x] 10.5 Column order: Focus cost is right of Lucro
- [x] 10.6 Cart focus total and bracket show integers (no ",0")
- [x] 10.7 Cart journal total and table journal column show integers
- [x] 10.8 T and H badges always visible in refining rows (dim when inactive)
- [x] 10.9 Clicking T or H badge does not add to cart; row double-click still works
- [x] 10.10 Wood/ore/rock/cloth/leather refining → raw material cities default to 5 royal cities
- [x] 10.11 Refining item names localized (PT/EN/ES)
- [x] 10.12 Item name does not turn emerald for profitable rows
- [x] 10.13 Cart clear button uses Tabler trash icon, non-square