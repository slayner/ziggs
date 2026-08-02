## ADDED Requirements

### Requirement: Smart sell-city default
The craft calculator SHALL initialize the sell city (`sellCities`) to the selected family's bonus city when the family changes, falling back to "Lymhurst" for families without a bonus city. The user's manual selection SHALL take precedence: once the user manually changes the sell city, it SHALL NOT be overridden by subsequent family changes until the user explicitly resets or the page reloads.

#### Scenario: Family with bonus city
- **WHEN** the user selects "Minério" (bonus city: Thetford)
- **THEN** `sellCities` becomes `["Thetford"]` (if not manually overridden)

#### Scenario: Family without bonus city
- **WHEN** the user selects a family with no `bonusCity` (e.g. journals, pickaxes)
- **THEN** `sellCities` becomes `["Lymhurst"]` (if not manually overridden)

#### Scenario: Manual override preserved
- **WHEN** the user manually changes sell city to "Caerleon" and then switches to another family
- **THEN** the sell city remains "Caerleon" (manual override is preserved)

### Requirement: Smart production-city default
The craft calculator SHALL default the production location city to "Lymhurst" (instead of "Caerleon") for families without a bonus city. Families WITH a bonus city SHALL still default to their bonus city. This applies only to the initial load and when no saved location exists in localStorage.

#### Scenario: First load with no saved location, no-bonus family
- **WHEN** the page loads with no localStorage location and the default family has no bonus city
- **THEN** the production city is "Lymhurst"

#### Scenario: First load with no saved location, bonus family
- **WHEN** the page loads with no localStorage location and the default family has a bonus city (e.g. Thetford for ore)
- **THEN** the production city is the bonus city

#### Scenario: Saved location respected
- **WHEN** localStorage has a saved production location
- **THEN** that location is used regardless of the family's bonus city