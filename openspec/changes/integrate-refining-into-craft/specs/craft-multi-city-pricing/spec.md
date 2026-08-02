## ADDED Requirements

### Requirement: Multi-city buy selection
The craft calculator SettingsPanel SHALL allow the user to select zero or more cities per material market group as the buy source ("Comprar em"). When more than one city is selected, the system SHALL query prices for all selected cities in a single request and use the minimum available price (sell order `sell_price_min` for sell-order mode, or `buy_price_max` for buy-order mode) across the selected cities as the effective material price. The city that produced the chosen price SHALL be recorded and displayed as the price's source.

#### Scenario: Single city selected (backward compatible)
- **WHEN** the user selects exactly one buy city for a material group
- **THEN** the effective material price equals the price in that city, matching the pre-change single-city behavior

#### Scenario: Multiple cities selected
- **WHEN** the user selects two or more buy cities for a material group (e.g. Thetford and Caerleon)
- **THEN** the system fetches prices for all selected cities in one ADP request
- **AND** the effective material price is the minimum non-zero price across the selected cities
- **AND** the price's source indicator shows the city abbreviation of the city that produced the minimum price

#### Scenario: No cities selected
- **WHEN** the user deselects all buy cities for a material group
- **THEN** the system falls back to the default city for that group (as defined by `DEFAULT_GROUP_CITY`)
- **AND** the effective material price is the price in the default city

### Requirement: Multi-city sell selection
The craft calculator SettingsPanel SHALL allow the user to select zero or more cities as the sell destination ("Vender em"). When more than one city is selected, the system SHALL query prices for all selected sell cities in a single request and use the maximum average price (averaged across qualities 1-4 per city, as in the pre-change logic) across the selected cities as the effective sell price. The city that produced the chosen price SHALL be recorded and displayed as the price's source. Black Market is a valid sell city and uses `buy_price_max` instead of `sell_price_min`.

#### Scenario: Black Market selected as sell city
- **WHEN** "Black Market" is among the selected sell cities
- **THEN** for Black Market quotes the system uses `buy_price_max` (instant-sell) rather than `sell_price_min`
- **AND** the effective sell price is the maximum across all selected sell cities (including non-Black-Market cities using `sell_price_min`)

#### Scenario: Multiple sell cities selected
- **WHEN** the user selects "Black Market" and "Caerleon" as sell cities
- **THEN** the system fetches prices for both cities in one ADP request
- **AND** computes the per-city average across qualities 1-4
- **AND** the effective sell price is the maximum of the two per-city averages
- **AND** the price's source indicator shows the winning city

### Requirement: Multi-city dropdown UI
The craft calculator SHALL render a multi-city dropdown component for both buy (per material group) and sell selections. The component SHALL display the currently selected cities as abbreviated labels (e.g. "BM, CN, TF"), open a checkbox list on click, close on outside-click, and allow toggling individual cities.

#### Scenario: Dropdown opens and closes
- **WHEN** the user clicks the multi-city dropdown button
- **THEN** a checkbox list of all eligible cities appears
- **WHEN** the user clicks outside the dropdown
- **THEN** the dropdown closes without losing the current selection

#### Scenario: City color dot in dropdown
- **WHEN** the dropdown is open
- **THEN** each city row displays a colored dot matching the city's established color (from `CITY_COLOR`)

### Requirement: ADP multi-city query
The price fetch function (`fetchAdpPrices`) already accepts a `locations: string[]` parameter and joins them with commas in the ADP API URL. The `fetchMarket` function SHALL pass the union of all selected sell cities and all selected buy cities (across material groups) as the `locations` array, so a single ADP request per item-id chunk retrieves prices for all relevant cities. The response rows include a `city` field that the frontend SHALL use to attribute each quote to its city.

#### Scenario: Single request covers all selected cities
- **WHEN** the user has selected 3 sell cities and 2 buy cities per material group
- **THEN** `fetchMarket` issues ADP requests with `locations=` containing the union of all 5 cities
- **AND** each returned quote is keyed by `itemId|city|quality` for per-city lookup