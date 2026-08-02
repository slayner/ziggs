## ADDED Requirements

### Requirement: Focus toggle button
The craft calculator SHALL display a Focus toggle button next to the Premium toggle in the production controls bar. The toggle SHALL be a boolean state (`useFocus`) that controls whether profit calculations use focus return rates and focus costs. When focus is active, the toggle SHALL be visually highlighted (same style as Premium when active).

#### Scenario: Focus toggle visible
- **WHEN** the craft page renders
- **THEN** a "Focus" toggle button appears to the right of the "Premium" toggle

#### Scenario: Toggle focus on
- **WHEN** the user clicks the Focus toggle (currently off)
- **THEN** `useFocus` becomes true and profit columns recompute with focus return rates

#### Scenario: Toggle focus off
- **WHEN** the user clicks the Focus toggle (currently on)
- **THEN** `useFocus` becomes false and profit columns recompute without focus

### Requirement: Profit highlighting follows focus toggle
When `useFocus` is true, the profit column that reflects the active mode (profit +F) SHALL be highlighted in yellow/amber text. When `useFocus` is false, the profit −F column SHALL be highlighted instead. The inactive profit column SHALL use the default muted text color.

#### Scenario: Focus on — profit+F yellow
- **WHEN** `useFocus` is true
- **THEN** the "Lucro +F" column values render in amber/yellow text
- **AND** the "Lucro −F" column values render in default muted text

#### Scenario: Focus off — profit−F yellow
- **WHEN** `useFocus` is false
- **THEN** the "Lucro −F" column values render in amber/yellow text
- **AND** the "Lucro +F" column values render in default muted text

### Requirement: Profit filtering respects focus toggle
The profit filter that determines whether a row is "profitable" SHALL use the active focus mode. When `useFocus` is true, a row is profitable if `profitFocus > 0 && silverPerFocus >= minSpf`. When false, a row is profitable if `profitNoFocus > 0`.

#### Scenario: Focus on — SPF filter applies
- **WHEN** `useFocus` is true
- **THEN** rows with `silverPerFocus < minSpf` are not marked as profitable

#### Scenario: Focus off — no SPF filter
- **WHEN** `useFocus` is false
- **THEN** rows are profitable if `profitNoFocus > 0`, regardless of SPF

### Requirement: Cart order records focus state and per-item focus cost
Each order added to the cart SHALL record the `useFocus` state at the time of adding (already exists as `o.useFocus`). Additionally, each order SHALL record the per-item focus cost (`focusCostPerItem: number`) computed as `v.focus * focusMult` at the time of adding. The cart SHALL display a focus bracket on each order showing the total focus cost for that order (`focusCostPerItem * qty`).

#### Scenario: Add order with focus on
- **WHEN** the user double-clicks a row with `useFocus = true`
- **THEN** the cart order records `useFocus: true` and `focusCostPerItem` = the row's per-item focus cost
- **AND** the order card shows a focus bracket with the total focus (e.g. "👁 12,450 focus")

#### Scenario: Add order with focus off
- **WHEN** the user double-clicks a row with `useFocus = false`
- **THEN** the cart order records `useFocus: false` and `focusCostPerItem` is still recorded (for reference)
- **AND** the order card does NOT show the focus bracket

### Requirement: Cart total focus cost
The cart SHALL display the total focus cost across all orders (only counting orders where `useFocus` is true) in the cart summary section.

#### Scenario: Total focus shown
- **WHEN** the cart has orders with focus enabled
- **THEN** the cart summary shows "Total focus: X" where X = Σ(order.focusCostPerItem * order.qty) for orders with `useFocus = true`