import { describe, expect, it } from "vitest";
import { buildRenderUrl, itemRenderCandidates, skillRenderCandidates } from "./render";

describe("Companion render proxy helpers", () => {
  it("builds encoded spell proxy URLs without a duplicate slash", () => {
    expect(buildRenderUrl("https://ziggs.xyz/", "spell", "Powerful Swing")).toBe("https://ziggs.xyz/render/spell/Powerful%20Swing");
  });

  it("builds encoded item proxy URLs with image parameters", () => {
    expect(buildRenderUrl("https://ziggs.xyz", "item", "T8_HEAD_PLATE_SET1@2", { quality: 4, size: 64 })).toBe("https://ziggs.xyz/render/item/T8_HEAD_PLATE_SET1%402?quality=4&size=64");
  });

  it("asks for the icon before the skill id, which may have no art of its own", () => {
    expect(skillRenderCandidates({ icon: "PASSIVE_X", unique_name: "PASSIVE_X_CONDITION", name: "Passive" })).toEqual(["PASSIVE_X", "PASSIVE_X_CONDITION", "Passive"]);
  });

  it("uses each skill render candidate once in stable order", () => {
    expect(skillRenderCandidates({ icon: "Powerful Swing", unique_name: "HAMMER_SHOVE", name: "Powerful Swing" })).toEqual(["Powerful Swing", "HAMMER_SHOVE"]);
  });

  it("never requests art for unresolved loot indexes", () => {
    expect(itemRenderCandidates({ item_id: "IDX_4172", item_name: "IDX_4172" })).toEqual([]);
  });
});
