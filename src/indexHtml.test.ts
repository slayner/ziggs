import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("initial Companion document", () => {
  it("paints the fixed shell background before the React stylesheet loads", () => {
    const document = readFileSync(resolve(import.meta.dirname, "../index.html"), "utf8");

    expect(document).toMatch(/background(?:-color)?\s*:\s*#0e0f13/i);
  });
});
