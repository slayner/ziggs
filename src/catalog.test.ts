import { describe, expect, it } from "vitest";
import { itemDisplayName, skillDisplayName } from "./catalog";

describe("catalog display fallbacks", () => {
  it("uses the selected skill locale before English", () => {
    expect(skillDisplayName({ id: 8, name: "Powerful Swing", name_pt: "Golpe Poderoso", name_es: null }, "pt", id => `Skill ${id}`)).toBe("Golpe Poderoso");
    expect(skillDisplayName({ id: 8, name: "Powerful Swing", name_pt: "Golpe Poderoso", name_es: null }, "es", id => `Skill ${id}`)).toBe("Powerful Swing");
  });

  it("keeps a stable skill fallback when catalog data is unavailable", () => {
    expect(skillDisplayName({ id: 77, name: null, name_pt: null, name_es: null }, "en", id => `Skill ${id}`)).toBe("Skill 77");
  });

  it("uses localized item names before the canonical identifier", () => {
    expect(itemDisplayName({ item_id: "T4_BAG", item_name: "Adept's Bag", item_name_pt: "Bolsa do Adepto", item_name_es: "Bolsa de Adepto" }, "pt")).toBe("Bolsa do Adepto");
    expect(itemDisplayName({ item_id: "T4_BAG", item_name: "Adept's Bag", item_name_pt: "", item_name_es: "" }, "es")).toBe("Adept's Bag");
  });

  it("keeps an unresolved packet identifier visible", () => {
    expect(itemDisplayName({ item_id: "IDX_4172", item_name: "IDX_4172", item_name_pt: "IDX_4172", item_name_es: "IDX_4172" }, "pt")).toBe("IDX_4172");
  });
});
