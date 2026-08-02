## ADDED Requirements

### Requirement: Wider material price inputs
The material price input fields (`PriceField` used in the Materials column) SHALL be wide enough to display at least 8 characters of the price value. The current width (`w-16` = 4rem) only shows ~3 characters. The new width SHALL accommodate 8 characters without truncation. The table layout SHALL adjust to make room for the wider inputs (e.g. the Materials column may need more width, or the table may need horizontal scroll earlier).

#### Scenario: 8-character price visible
- **WHEN** a material price is "12345678" (8 characters)
- **THEN** the full number is visible in the price field without truncation or scrolling

#### Scenario: Layout adjusts
- **WHEN** the wider price fields are rendered
- **THEN** the table does not overflow horizontally on standard desktop widths (1280px+); the Materials column and surrounding columns adjust to accommodate

### Requirement: Two-line column headers
The table header (`<thead>`) SHALL use two lines per column. The first line is the primary label (e.g. "Materiais", "Lucro"); the second line is a subtitle (e.g. "preço unitário", "sem focus", "com focus"). The `<Th>` component SHALL support an optional `subtitle` prop. Columns: Item (no subtitle), Materiais ("preço unitário"), Focus cost ("por craft"), Venda ("médio"), Lucro −F ("sem focus"), Lucro +F ("com focus"), SPF ("prata/focus"), Jornais ("preço unit."), Demanda ("vendas/dia").

#### Scenario: Two-line header renders
- **WHEN** the table header renders
- **THEN** each column header shows a primary label on the first line and a subtitle on the second line
- **AND** the subtitle is smaller/muted text below the primary label

### Requirement: Journal count always rounded up
Every display site that shows a journal count SHALL render `Math.ceil(journalsFilled(...))` — an integer, never a decimal. This applies to: the table's Jornais column, and the cart's journal bracket per order.

#### Scenario: Table journal column shows integer
- **WHEN** `journalsFilled` returns 2.3 for a row
- **THEN** the Jornais column shows "3" (not "2.3")

#### Scenario: Cart journal bracket shows integer
- **WHEN** a cart order's `journalsFilled` returns 1.1
- **THEN** the journal bracket shows "2" (not "1.1")

### Requirement: No journal brackets for refining in cart
Cart orders for refining families (where `journalId === null` or the order's family is refining) SHALL NOT display a journal bracket. The journal bracket is only shown for craft orders that actually use journals.

#### Scenario: Refining order — no journal bracket
- **WHEN** a refining order is in the cart
- **THEN** no journal count bracket is displayed on the order card

#### Scenario: Craft order — journal bracket shown
- **WHEN** a craft order with journals is in the cart
- **THEN** the journal count bracket is displayed as usual

### Requirement: Cart clear button
The cart SHALL display a clear button (to empty the cart) in the cart header area. The button SHALL be a subtle icon button (e.g. a trash icon or "Limpar"), only visible when `cart.length > 0`. When the cart is empty, the button SHALL NOT be rendered.

#### Scenario: Clear button visible with items
- **WHEN** the cart has at least one order
- **THEN** a clear button is visible in the cart header

#### Scenario: Clear button hidden when empty
- **WHEN** the cart is empty
- **THEN** no clear button is rendered

#### Scenario: Clicking clear empties the cart
- **WHEN** the user clicks the clear button
- **THEN** all orders are removed from the cart