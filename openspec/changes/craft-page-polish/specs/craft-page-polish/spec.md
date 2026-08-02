## ADDED Requirements

### Requirement: Simplified column headers
The table headers SHALL strip redundant parenthetical text from primary labels
and rely on the subtitle line for secondary detail. "Materiais (preço unit.)"
SHALL become "Materiais", "Venda (méd)" SHALL become "Venda", and the two
profit columns ("Lucro -F" / "Lucro +F") SHALL become a single "Lucro" column.
The Jornais header SHALL NOT have a subtitle. Both header lines SHALL be
visually centered within their column.

#### Scenario: Header labels are clean
- **WHEN** the craft table renders
- **THEN** the Materiais column header shows "Materiais" with subtitle "preço unitário"
- **AND** the Venda column header shows "Venda" with subtitle "médio"
- **AND** the Lucro column header shows "Lucro" with no -F/+F suffix
- **AND** the Jornais column header shows "Jornais" with no subtitle

### Requirement: Single profit column follows Focus toggle
The table SHALL render one profit column instead of two. The displayed value
SHALL be `profitFocus` when Focus is toggled on and `profitNoFocus` when Focus
is toggled off. The column SHALL be highlighted (amber) when the displayed
profit is the active mode.

#### Scenario: Focus on shows profit with focus
- **WHEN** the Focus toggle is on
- **THEN** the Lucro column displays `profitFocus` for each row
- **AND** the column values are amber-highlighted

#### Scenario: Focus off shows profit without focus
- **WHEN** the Focus toggle is off
- **THEN** the Lucro column displays `profitNoFocus` for each row
- **AND** the column values are amber-highlighted

### Requirement: Focus cost column positioned after Lucro
The Focus cost column SHALL appear immediately to the right of the Lucro
column in the table. The column order SHALL be: Item, Materiais, Venda,
Lucro, Focus cost, SPF, Jornais, Demanda.

#### Scenario: Column order
- **WHEN** the table renders
- **THEN** Focus cost is to the right of Lucro and to the left of SPF

### Requirement: Integer focus values in cart
Focus values in the cart SHALL render as integers with no decimal places. The
cart summary total focus and per-order focus bracket SHALL use integer
formatting.

#### Scenario: Cart summary total focus is integer
- **WHEN** the cart has orders with focus and total focus is a whole number
- **THEN** the total focus displays without decimal places (e.g. "1.234" not "1.234,0")

#### Scenario: Cart order focus bracket is integer
- **WHEN** an order in the cart has focus enabled
- **THEN** the focus bracket shows an integer value (e.g. "👁 480" not "👁 480,0")

### Requirement: Integer journal totals in cart
Journal count in the cart summary SHALL render as an integer with no decimal
places when the value is whole. The table Jornais column SHALL also show
integers.

#### Scenario: Cart journal total is integer
- **WHEN** the cart has orders with journals and journal total is a whole number
- **THEN** the journal total displays without decimal places (e.g. "12" not "12,0")

#### Scenario: Table journal column is integer
- **WHEN** the table renders a row with journals
- **THEN** the Jornais column shows an integer (e.g. "5" not "5,0")

### Requirement: Always-visible T and H badges in refining rows
Refining rows SHALL always render both T and H badges next to the item name.
When the feature is inactive (no transmute route / heart doesn't win), the
badge SHALL render in a dim/inactive style. When active, the badge SHALL
render in its bright style (purple for T, red for H).

#### Scenario: T badge dim when no transmute
- **WHEN** a refining row has no cheaper transmute route
- **THEN** the T badge is visible but dim (low opacity, no background)

#### Scenario: T badge bright when transmute is cheaper
- **WHEN** a refining row has a transmute route cheaper than direct
- **THEN** the T badge is bright (purple background, full opacity)

#### Scenario: H badge dim when heart doesn't win
- **WHEN** a refining row's heart variant profit is not higher than normal
- **THEN** the H badge is visible but dim

#### Scenario: H badge bright when heart wins
- **WHEN** a refining row's heart variant profit is higher than normal
- **THEN** the H badge is bright (red background, full opacity)

### Requirement: T and H badge click isolation
Clicking a T or H badge SHALL NOT trigger the row's add-to-cart action. Badge
clicks SHALL stop event propagation. Hovering over a badge SHALL show a
tooltip with details.

#### Scenario: Badge click does not add to cart
- **WHEN** the user clicks the T or H badge
- **THEN** no order is added to the cart
- **AND** the badge tooltip/detail is shown

#### Scenario: Row double-click still adds to cart
- **WHEN** the user double-clicks the row outside the badges
- **THEN** the order is added to the cart as before

### Requirement: Refining raw-material city defaults
Raw materials (wood, fiber, hide, ore, stone) SHALL default to all 5 royal
cities (Fort Sterling, Lymhurst, Bridgewatch, Thetford, Martlock) as price
source cities. Refined materials (planks, metal bars, cloth, leather, stone
blocks) SHALL keep their existing single-city defaults. The refined output
sell city SHALL default to the refining bonus city.

#### Scenario: Raw wood price sources
- **WHEN** the user selects a wood refining family
- **THEN** the raw wood material price sources default to all 5 royal cities

#### Scenario: Raw ore price sources
- **WHEN** the user selects an ore refining family
- **THEN** the raw ore material price sources default to all 5 royal cities

#### Scenario: Refined planks keep single-city default
- **WHEN** the user selects a non-refining family that uses planks
- **THEN** planks price source defaults to Fort Sterling (unchanged)

### Requirement: Refining item name localization
Refining item names (inputs and outputs) SHALL be displayed in the user's
selected language (PT/EN/ES) using the refining catalog's `names` map. When
a name is not found in the refining catalog, the system SHALL fall back to
`names.json`.

#### Scenario: Portuguese refining names
- **WHEN** the user's language is PT and a refining row renders
- **THEN** item names show in Portuguese (e.g. "Troncos de Pinheiro" not "Pine Logs")

#### Scenario: Spanish refining names
- **WHEN** the user's language is ES and a refining row renders
- **THEN** item names show in Spanish (e.g. "Troncos de pino" not "Pine Logs")

#### Scenario: Fallback to names.json for non-refining items
- **WHEN** a non-refining item is rendered
- **THEN** the name comes from `names.json` as before

### Requirement: Remove emerald item name for high SPF
The item tier label SHALL always render in neutral color (`text-zinc-200`).
It SHALL NOT turn emerald based on SPF or profitability.

#### Scenario: Profitable row name is neutral
- **WHEN** a row is profitable and SPF is above the minimum
- **THEN** the tier label is `text-zinc-200`, not emerald

### Requirement: Cart clear button with non-square icon
The cart clear button SHALL use a Tabler Icons trash icon (`ti-trash`)
instead of the 🗑 emoji. The button SHALL be a bare icon button without a
square border wrapper. It SHALL be visible only when the cart has items.

#### Scenario: Clear button visible with items
- **WHEN** the cart has one or more items
- **THEN** a trash icon button is visible in the cart header

#### Scenario: Clear button hidden when empty
- **WHEN** the cart is empty
- **THEN** no clear button is shown

#### Scenario: Clear button uses Tabler icon
- **WHEN** the clear button renders
- **THEN** it uses `<i className="ti ti-trash" />`, not the 🗑 emoji