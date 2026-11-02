import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const BASE = "https://ziggs.xyz";
const spellUrl = (key: string) => `${BASE}/render/spell/${key}`;

class FakeImage {
  static created: FakeImage[] = [];
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  src = "";
  constructor() {
    FakeImage.created.push(this);
  }
}

describe("preloadSkillIcon", () => {
  let preloadSkillIcon: typeof import("./preload").preloadSkillIcon;

  beforeEach(async () => {
    FakeImage.created = [];
    vi.stubGlobal("Image", FakeImage);
    vi.resetModules();
    ({ preloadSkillIcon } = await import("./preload"));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const urls = () => FakeImage.created.map(image => image.src);

  it("asks for the same url the table uses, once per skill", () => {
    preloadSkillIcon(BASE, ["RENDINGSTRIKE"]);
    preloadSkillIcon(BASE, ["RENDINGSTRIKE"]);

    expect(urls()).toEqual([spellUrl("RENDINGSTRIKE")]);
  });

  it("has nothing to do for a skill without art", () => {
    preloadSkillIcon(BASE, []);

    expect(urls()).toEqual([]);
  });

  it("tries the next candidate when one fails, and stops at the first that loads", () => {
    preloadSkillIcon(BASE, ["FIRST", "SECOND", "THIRD"]);

    FakeImage.created[0].onerror?.();
    expect(urls()).toEqual([spellUrl("FIRST"), spellUrl("SECOND")]);

    FakeImage.created[1].onload?.();
    expect(urls()).toEqual([spellUrl("FIRST"), spellUrl("SECOND")]);
  });

  it("goes a few at a time and moves on as each one finishes", () => {
    for (const key of ["A", "B", "C", "D", "E", "F"]) preloadSkillIcon(BASE, [key]);

    expect(urls()).toEqual(["A", "B", "C", "D"].map(spellUrl));

    FakeImage.created[0].onload?.();
    expect(urls()).toEqual(["A", "B", "C", "D", "E"].map(spellUrl));

    FakeImage.created[1].onerror?.();
    expect(urls()).toEqual(["A", "B", "C", "D", "E", "F"].map(spellUrl));
  });
});
