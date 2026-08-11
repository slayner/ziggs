# Awakened weapon valuation

## Conclusion

`LegendarySoul.attunementSpent` is neither silver nor attunement in normal
units. The value is in fixed-point `x10000`, same as the monetary prices in
the Albion protocol. The estimated silver spent on tuning is:

```text
tuning_silver = round(attunementSpent * 33 / 10000)
```

The full awakened surcharge used in Juicy Kills is:

```text
6,000,000 awakening
+ tuning_silver
+ 1,000,000 per reattunement after the first era
```

The market price of the `.4` weapon is calculated separately and added to
the surcharge. A `.4` weapon without a `LegendarySoul` gets no awakened
surcharge.

## Evidence

1. The game dump at `referencia/ao-bin-dumps-master/legendaryitems.xml:4-31`
   defines `silver="6000000"`, `attunementtosilverfactor="33"`, base cost
   `addreplace="10000"` and the per-tier factors.
2. The monetary protocol uses fixed-point `x10000`. The validated parser in
   the companion divides `UnitPriceSilver` by 10,000 at
   `companion/src-tauri/src/photon_parser.rs:1343-1414`.
3. The smallest `attunementSpent` from real souls matches the dump's base
   cost multiplied by 10,000:

| Tier | `10000 * tier_scaling * 10000` | Smallest soul observed |
|------|---------------------------------|-----------------------|
| 4    | 2,075,569                       | 2,080,000             |
| 7    | 33,430,091                      | 33,430,000            |

This match across tiers does not happen if the field is read as direct
silver. It shows the Albion REST API exposes the internal raw fixed-point.

## Empirical validation

Across 20,000 recent kill events, 673 unique souls with traits were found.
For T4 (`n=427`), raw `attunementSpent` had median 475,482,679, p90
2,020,515,533 and max 17,083,776,514. Correctly converted, that is roughly
1.57M, 6.67M and 56.38M silver in tuning, respectively.

Real case `Aethra`, T4.4:

```text
raw attunementSpent = 530,733,598
tuning              = 530,733,598 * 33 / 10,000 = 1,751,421
awakened surcharge  = 6,000,000 + 1,751,421 = 7,751,421
```

Previous interpretations produced 536.7M (raw read as silver) and 17.52B
(raw multiplied again by 33); both ignored the fixed-point scale.

## Limitations

- The 100 Siphoned Energy and 100 Avalonian Energy required for awakening
  appear in the dump, but are not added to the surcharge until there is a
  reliable regional price for both. The result is conservative by that
  amount.
- `attunementSpent` records accumulated cost, but does not tell whether the
  user paid the focus alternative. The 33 conversion represents the silver
  equivalent defined by the game.
- Current traits do not reveal how many attempts failed. The accumulated
  field is an upper bound; reconstructing cost only from visible rolls
  would be lower.

## Sources

- SBI game dump: `referencia/ao-bin-dumps-master/legendaryitems.xml`.
- Albion gameinfo API: `https://gameinfo-*.albiononline.com/api/gameinfo/events/{id}`,
  `Equipment.*.LegendarySoul` fields observed in real events.
- Monetary protocol format: `companion/src-tauri/src/photon_parser.rs`.