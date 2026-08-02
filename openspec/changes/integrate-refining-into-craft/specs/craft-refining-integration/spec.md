## ADDED Requirements

### Requirement: Refining families as picker items
The craft calculator picker SHALL include one entry per refining family (fiber, hide, ore, wood, stone). The picker entry name SHALL NOT include a "Refino:" prefix (e.g. "Fibra", not "Refino: Fibra"). The picker entry icon SHALL be the normal material icon (e.g. `T6_CLOTH`) rendered without the excellent-quality (quality 4) border, since refining materials do not have quality tiers.

#### Scenario: Picker entry naming
- **WHEN** the user opens the craft picker
- **THEN** refining family entries appear with their bare material name (e.g. "Minério", "Madeira", "Pedra")
- **AND** no "Refino:" prefix is shown

#### Scenario: Picker icon without excellent border
- **WHEN** the picker renders the icon for a refining family entry
- **THEN** the icon URL does not include a `quality=4` parameter
- **AND** the icon displays as the normal material image

### Requirement: Refining rows in main table
When a refining family is selected, the craft calculator SHALL render the refining recipes as rows in the main CraftMode table (the same table used for craft items), NOT in a separate standalone component. Each refining recipe (tier × enchant) SHALL produce one row. The row SHALL use the "normal" variant's inputs as its material list. The row SHALL show the same columns as craft rows: materials (with per-material price + age indicator), sell price, focus cost, profit -F, profit +F, SPF, and demand. Refining rows SHALL NOT show a journals column value (rendered as "—").

#### Scenario: Selecting a refining family renders rows in main table
- **WHEN** the user selects "Minério" in the picker
- **THEN** the main table renders one row per ore refining recipe (T4.0 through T8.3)
- **AND** each row shows the recipe's inputs as material icons with prices
- **AND** the journals column shows "—" for every refining row

#### Scenario: Double-click adds to cart
- **WHEN** the user double-clicks a refining row
- **THEN** the recipe is added to the shared cart with the current batch quantity, return rate, and place label

### Requirement: Refining return rates
When the selected family is a refining family, the return rates (with and without focus) SHALL be computed using the refining-specific formulas: `refiningReturnRateNoFocus(place, city, family, eventBonus, hoQuality, hoLevel)` and `refiningReturnRateFocus(...)`, which apply a +40% city specialization bonus (vs +15% for craft) and a base of 18 points in royal cities. Rests have a base of 15 and NO refining specialization bonus. The SettingsPanel bonus indicator SHALL show "+40%" (not "+15%") when the refining bonus city matches the selected production city.

#### Scenario: Refining in bonus city
- **WHEN** the user selects "Minério" and sets production city to "Thetford" (the ore bonus city)
- **THEN** the SettingsPanel shows "✓ Thetford +40%"
- **AND** the return rate reflects the refining formula with the +40 specialization bonus

#### Scenario: Refining in non-bonus city
- **WHEN** the user selects "Minério" and sets production city to "Caerleon"
- **THEN** the return rate reflects the base 18 points without the specialization bonus

### Requirement: Per-tier focus efficiency for refining
When the selected family is a refining family, the focus efficiency SHALL be computed per tier using `refiningFocusEfficiency(family, tier, specs)` where `specs` is a `Record<number, number>` of T4-T8 specialization levels (0-100) for that family. Each refining row's focus cost SHALL use its tier-specific focus efficiency, not a single family-wide value. The specialization levels SHALL be persisted in localStorage via `saveRefiningSpecs`/`loadRefiningSpecs`.

#### Scenario: Per-tier focus cost
- **WHEN** the user sets T4 spec to 100 and T8 spec to 0 for "Minério"
- **THEN** the T4 row's focus cost is computed with efficiency = 250×100 + 30×(100+0+0+0+0) = 28000
- **AND** the T8 row's focus cost is computed with efficiency = 250×0 + 30×(100+0+0+0+0) = 3000

### Requirement: Refining specs panel
When the selected family is a refining family, the SettingsPanel SHALL show a refining specialization panel with 5 numeric inputs (T4 through T8, 0-100) instead of the craft focus-efficiency sibling tree. The panel SHALL be labeled appropriately and persist values to localStorage on change.

#### Scenario: Specs panel for refining
- **WHEN** the user selects a refining family
- **THEN** the SettingsPanel shows 5 inputs labeled T4, T5, T6, T7, T8
- **AND** no craft sibling-tree focus panel is shown

### Requirement: No journals for refining
When the selected family is a refining family, the system SHALL NOT include journal item ids in the material list, market fetch, or cart. The "Ignorar jornais" panel SHALL be hidden. The cart SHALL NOT add journal costs for refining orders.

#### Scenario: No journal ids fetched
- **WHEN** the user selects a refining family and clicks "Update prices"
- **THEN** the ADP request does not include any `T*_JOURNAL_*` item ids

### Requirement: Heart price input in SettingsPanel
When the selected family is a refining family, the SettingsPanel SHALL replace the "Ignorar jornais" panel with a "Coração" panel. The panel SHALL display: (a) the heart token item icon and name, (b) a manual price input field, (c) the market-pulled price (if available) as a hint with the source city indicator. The manual price SHALL take precedence over the market price when set. The heart price SHALL be stored per refining family (fiber/hide/ore/wood/stone).

#### Scenario: Heart price from market
- **WHEN** the user selects "Minério" and clicks "Update prices"
- **THEN** the "Coração" panel shows the heart token icon (T1_FACTION_MOUNTAIN_TOKEN_1) and its market price from the cheapest selected buy city
- **AND** a manual input field is available for override

#### Scenario: Manual heart price override
- **WHEN** the user types a manual heart price of 5000
- **THEN** the effective heart price used in profit calculations is 5000, regardless of the market price

### Requirement: Heart variant indicator on rows
For each refining row, the system SHALL compute the profit of the "heart" variant (which uses fewer raw materials + 1 heart token at the configured heart price). When the heart variant's profit (with focus) is greater than the normal variant's profit (with focus) AND the effective heart price is greater than 0, the row SHALL display a ♥ icon. The row's displayed profit SHALL reflect the better of the two variants (heart vs normal).

#### Scenario: Heart worth using
- **WHEN** the heart variant profit (+F) is 1000 and the normal variant profit (+F) is 800, and the heart price is set to 5000
- **THEN** the row displays a ♥ icon
- **AND** the row's profit +F column shows 1000

#### Scenario: Heart not worth using
- **WHEN** the heart variant profit (+F) is 600 and the normal variant profit (+F) is 800
- **THEN** the row does NOT display a ♥ icon
- **AND** the row's profit +F column shows 800

### Requirement: Transmutation as purple row
For each refining row whose primary raw material can be obtained more cheaply via transmutation (buying a lower-tier source item + paying the transmutation silver cost + station fee) than buying it directly, the system SHALL render a purple row above the refining row (same visual style as artifact rows). The purple row SHALL show: the source item icon → target item icon, the transmutation total cost, and the direct buy price for comparison. The transmutation cost SHALL be computed using `findRoutes` and `cheapestOption` from `lib/craft/transmutation.ts`.

#### Scenario: Transmutation cheaper than direct buy
- **WHEN** the direct buy price of T6_ORE is 2000 and the transmutation cost (T5_ORE + silver cost + station fee) is 1800
- **THEN** a purple row appears above the T6.0 refining row
- **AND** the purple row shows the T5_ORE → T6_ORE transmute with cost 1800 vs 2000 direct

#### Scenario: Transmutation not cheaper
- **WHEN** the direct buy price of T6_ORE is 1500 and the transmutation cost is 1800
- **THEN** no purple row appears above the T6.0 refining row

### Requirement: Transmutation affects material price
When a transmutation route is cheaper than the direct buy price for a refining row's primary raw material, the system SHALL use the transmutation cost as the effective material price for that material in the profit calculation, and the material's price field SHALL visually indicate that it is a transmuted price (distinct color or badge).

#### Scenario: Transmuted material price used in calculation
- **WHEN** transmutation is cheaper for T6_ORE
- **THEN** the refining row's profit calculation uses the transmutation cost (1800) as the T6_ORE unit price
- **AND** the material price field shows the transmuted price with a visual indicator (e.g. purple text)

### Requirement: Standalone transmutation picker entry removed
The craft picker SHALL NOT include a standalone "Transmutação" entry. Transmutation is surfaced exclusively as purple rows inside refining family tables. The `TransmutationCalculator` component SHALL be removed from the codebase.

#### Scenario: No transmutation entry in picker
- **WHEN** the user opens the craft picker
- **THEN** there is no "Transmutação" entry
- **AND** transmutation options are only visible as purple rows within refining family tables

### Requirement: Refining catalog loaded in CraftMode
The CraftMode component SHALL load the refining catalog (`public/data/refining.json`) alongside the craft catalog on mount. When the refining catalog loads, the system SHALL replace the placeholder variations of refining families with real `CatalogVariation` objects built from the refining recipes (one variation per tier × enchant, using the "normal" variant's inputs as resources).

#### Scenario: Real variations appear after catalog load
- **WHEN** the page loads and the refining catalog finishes fetching
- **THEN** selecting "Minério" shows rows for T4.0, T5.0, T6.0, T7.0, T8.0, T4.1, T5.1, ... (all tier × enchant combinations)
- **AND** each row's material list matches the refining recipe's normal variant inputs