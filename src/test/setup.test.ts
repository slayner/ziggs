import { describe, expect, it } from "vitest";

describe("frontend test environment", () => {
  it("loads a DOM", () => {
    expect(document.body).toBeTruthy();
  });
});
