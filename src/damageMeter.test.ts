import { describe, expect, it } from "vitest";
import { meterText, partyRows, shareOf, shortNumber, skillIconCandidates } from "./damageMeter";

describe("shortNumber", () => {
  it.each([
    [0, "0"],
    [842.4, "842"],
    [999.4, "999"],
    [999.5, "1K"],
    [1000, "1K"],
    [1234, "1.23K"],
    [12_345, "12.3K"],
    [123_456, "123K"],
    [999_499, "999K"],
    [999_500, "1M"],
    [1_842_310, "1.84M"],
    [15_209_770, "15.2M"],
    [999_500_000, "1B"],
    [2_340_000_000, "2.34B"],
  ])("%d reads %s", (value, expected) => {
    expect(shortNumber(value)).toBe(expected);
  });
});

describe("meterText", () => {
  it("lists the ranking with the share of each player", () => {
    expect(meterText([
      { name: "Mordred", damage: 1_842_310 },
      { name: "Beltrana", damage: 460_577 },
    ])).toBe("1. Mordred — 1.84M (80.0%)\n2. Beltrana — 461K (20.0%)");
  });

  it("never writes a hash, which the Albion chat reads as a command", () => {
    expect(meterText([{ name: "Mordred", damage: 10 }])).not.toContain("#");
  });

  it("is empty without players", () => {
    expect(meterText([])).toBe("");
  });
});

describe("shareOf", () => {
  it("does not divide by zero", () => {
    expect(shareOf(0, 0)).toBe("0.0");
  });
});

describe("skillIconCandidates", () => {
  const skill = { icon: "ICON", unique_name: "UNIQUE", name: "Name" };

  it("uses the skill's own art", () => {
    expect(skillIconCandidates({ id: 7, ...skill }, "sword")).toEqual(["ICON", "UNIQUE", "Name"]);
  });

  it("gives the auto-attack an icon of the attack type of the weapon", () => {
    expect(skillIconCandidates({ id: -1, ...skill }, "sword")).toEqual(["PASSIVE_KNOCKBACKCHANCE"]);
    expect(skillIconCandidates({ id: -1, ...skill }, "bow")).toEqual(["SPEEDSHOT2"]);
    expect(skillIconCandidates({ id: -1, ...skill }, "firestaff")).toEqual(["PASSIVE_ATTACKBUFF_ARCANESTAFF"]);
  });

  it("has no icon for the auto-attack when the weapon is unknown", () => {
    expect(skillIconCandidates({ id: -1, ...skill }, null)).toEqual([]);
  });
});

describe("partyRows", () => {
  const rows = [{ name: "Fulano" }, { name: "Zezinho" }, { name: "Estranho" }];

  it("keeps the party and the local player, in the same order", () => {
    expect(partyRows(rows, "Zezinho", ["Fulano"])).toEqual([{ name: "Fulano" }, { name: "Zezinho" }]);
  });

  it("is only the local player without a party", () => {
    expect(partyRows(rows, "Zezinho", [])).toEqual([{ name: "Zezinho" }]);
  });

  it("does not need the local player to be known", () => {
    expect(partyRows(rows, "", ["Fulano"])).toEqual([{ name: "Fulano" }]);
    expect(partyRows(rows, null, null)).toEqual([]);
  });
});
