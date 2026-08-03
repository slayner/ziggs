## ADDED Requirements

### Requirement: One role per slot
The comp editor SHALL enforce exactly one role per slot. The `DraftSlot` type MUST have a single `role: DraftRole` field, not an array. Flex builds (multiple roles per slot) are removed.

#### Scenario: Existing comp with flex builds loads
- **WHEN** a comp with slots containing multiple roles is loaded via `compToDraft`
- **THEN** the editor takes `roles[0]` as the single role and silently drops the rest

#### Scenario: Saving a comp
- **WHEN** the user saves a comp
- **THEN** each slot sends `role_ids: [single_catalog_id]` to the backend

### Requirement: Role name is the card title
The slot card title SHALL display the role name. The user MUST be able to edit the name inline by clicking the title when the card is expanded. There SHALL be no separate "Identidade" section with a name input.

#### Scenario: Editing role name
- **WHEN** the card is expanded and the user clicks the title text
- **THEN** an input appears in place of the text, pre-filled with the current name
- **WHEN** the user types and blurs the input
- **THEN** the role name is updated in the draft

### Requirement: Function selector via colored square
The function (fn) of a slot SHALL be indicated by a colored square (fn-dot) next to the weapon render in the card header. Clicking the fn-dot SHALL open a dropdown listing all available fn-types as colored buttons. Selecting a button sets the slot's function. There SHALL be no separate fn-type-picker panel or select dropdown.

#### Scenario: Slot has no function
- **WHEN** a slot has `fn: null`
- **THEN** the fn-dot is gray and semi-transparent
- **WHEN** the user clicks the fn-dot
- **THEN** the dropdown opens with all fn-types
- **WHEN** the user selects a fn-type
- **THEN** the slot's `fn` is set and the fn-dot takes the fn-type's color

#### Scenario: Slot has a function
- **WHEN** a slot has `fn: "tank"`
- **THEN** the fn-dot is blue (the tank color)
- **WHEN** the user clicks the fn-dot and selects a different fn-type
- **THEN** the slot's `fn` is updated and the fn-dot color changes

### Requirement: Weapon skills below weapon picker
The Q, W, and Passive spell pickers SHALL appear directly below the weapon ItemPicker, in the same column. There SHALL be no separate "Habilidades" section. Gear spells (helmet_Q, armor_W, etc.) SHALL remain inline with their respective gear piece in the equipment grid.

#### Scenario: Weapon has spells
- **WHEN** a weapon is selected and has spells in the cache
- **THEN** Q, W, and Passive SpellPickers appear below the weapon ItemPicker

#### Scenario: Weapon has no spells
- **WHEN** no weapon is selected or the weapon has no spells
- **THEN** no spell pickers appear below the weapon picker

### Requirement: Compact 3-column layout for small sections
When a card is expanded, the top row SHALL display three columns: (1) Weapon + skills, (2) Consumables (food/potion), (3) Notes (obs/play_style). The equipment grid (offhand/helmet/armor/boots/cape) SHALL be a separate row below, with up to 5 columns. This minimizes vertical space.

#### Scenario: Card expanded on wide screen
- **WHEN** the card is expanded and viewport width >= 720px
- **THEN** the top row shows 3 columns (weapon, consumables, notes) and the gear grid shows up to 5 columns

#### Scenario: Card expanded on narrow screen
- **WHEN** the card is expanded and viewport width < 720px
- **THEN** columns stack vertically (1 column)

### Requirement: Swaps remain as inline alt items
Alt items (offhand_alt, helmet_alt, armor_alt, boots_alt, cape_alt) SHALL remain as inline ItemPickers below their respective gear piece in the equipment grid. Each alt picker has a small "+" button to add up to 2 alts. Gear spells for alts SHALL appear beside the alt picker.

#### Scenario: Adding an alt item
- **WHEN** a gear piece has an item and the user clicks the "+" button
- **THEN** a new empty ItemPicker appears below the main item for an alt

#### Scenario: Alt item with gear spells
- **WHEN** an alt helmet is selected and has spells
- **THEN** the gear spell pickers for that alt appear beside the alt ItemPicker

### Requirement: Accordion inline expansion
The slot card SHALL expand inline when clicked, showing the build editor within the card. Only one card per comp SHALL be expanded at a time. Clicking the card again collapses it.

#### Scenario: Expanding a card
- **WHEN** the user clicks a collapsed card
- **THEN** the card expands showing weapon, consumables, notes, and gear grid inline
- **AND** any previously expanded card collapses

#### Scenario: Collapsing a card
- **WHEN** the user clicks an expanded card
- **THEN** the card collapses showing only the header (weapon icon, name, equip strip, fn-dot)

### Requirement: Comp code backward compatibility
`decodeCompCode` SHALL handle both old format (`roles: DraftRole[]`) and new format (`role: DraftRole`). Old codes with multiple roles take `roles[0]` as the single role.

#### Scenario: Importing old comp code
- **WHEN** a comp code in the old format (`roles: [r0, r1]`) is decoded
- **THEN** `r0` becomes the slot's single role, `r1` is dropped

#### Scenario: Exporting new comp code
- **WHEN** a comp is exported via `encodeCompCode`
- **THEN** each slot has `role: DraftRole` (single object, not array)