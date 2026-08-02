## ADDED Requirements

### Requirement: Inline transmute indicator (T badge)
For refining rows where the primary raw material can be obtained more cheaply via transmutation, the system SHALL display a "T" badge (purple) after the item name in the Item column, instead of a separate purple row above. The row's material prices and profit SHALL already reflect the transmuted cost (the cheaper option). Hovering or clicking the T badge SHALL show a tooltip/popover with the transmute details: source item icon → target item icon, transmute total cost, and direct buy price for comparison.

#### Scenario: Transmute available — T badge shown
- **WHEN** the transmutation cost for the primary raw material is cheaper than the direct buy price
- **THEN** a purple "T" badge appears after the item tier label in the Item column
- **AND** the row's material price for that material uses the transmuted cost
- **AND** hovering/clicking the T badge shows the transmute source → target + cost vs direct price

#### Scenario: Transmute not cheaper — no T badge
- **WHEN** the transmutation cost is NOT cheaper than the direct buy price (or no route exists)
- **THEN** no T badge appears on the row

### Requirement: Inline heart indicator (H badge)
For refining rows where the heart variant is more profitable than the normal variant (and the effective heart price > 0), the system SHALL display an "H" badge (red) after the item name in the Item column, instead of a separate ♥ icon. The row's profit SHALL already reflect the heart variant's result (the better option). Hovering or clicking the H badge SHALL show a tooltip/popover with the heart variant details: heart token icon, heart price used, and the profit difference vs normal.

#### Scenario: Heart worth using — H badge shown
- **WHEN** the heart variant profit (+F) is greater than the normal variant profit (+F) and heart price > 0
- **THEN** a red "H" badge appears after the item tier label in the Item column
- **AND** the row's profit columns reflect the heart variant's result
- **AND** hovering/clicking the H badge shows the heart token + price + profit comparison

#### Scenario: Heart not worth using — no H badge
- **WHEN** the heart variant is not more profitable or heart price is 0
- **THEN** no H badge appears on the row

### Requirement: Both badges can coexist
When both transmute and heart are active for the same row, both T and H badges SHALL appear (T first, then H). The row's profit SHALL reflect the best combination (transmuted material price + heart variant inputs, if both are better).

#### Scenario: Both T and H active
- **WHEN** both transmute is cheaper AND heart variant is more profitable
- **THEN** both "T" (purple) and "H" (red) badges appear after the item name, in that order

### Requirement: Remove separate purple transmute rows
The system SHALL NOT render separate purple `<tr>` rows for transmutation above refining rows. The transmute information is exclusively surfaced via the inline T badge and its tooltip.

#### Scenario: No separate purple rows
- **WHEN** a refining family is selected
- **THEN** no purple rows appear above the refining rows (only artifact purple rows for craft families with artifacts remain)