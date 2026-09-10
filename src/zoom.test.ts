import { describe, expect, it } from "vitest";
import {
  DEFAULT_ZOOM,
  MAX_ZOOM,
  MIN_ZOOM,
  clampZoom,
  isEditableTarget,
  isZoomShortcut,
  nextZoom,
  readZoomPreference,
  saveZoomPreference,
} from "./zoom";

function keyEvent(init: KeyboardEventInit) {
  return new KeyboardEvent("keydown", init);
}

describe("Companion WebView zoom", () => {
  it("clamps the scale to the supported range", () => {
    expect(clampZoom(MIN_ZOOM - 1)).toBe(MIN_ZOOM);
    expect(clampZoom(MAX_ZOOM + 1)).toBe(MAX_ZOOM);
    expect(clampZoom(DEFAULT_ZOOM)).toBe(DEFAULT_ZOOM);
  });

  it("moves by one tenth without floating point drift", () => {
    expect(nextZoom(1, 1)).toBe(1.1);
    expect(nextZoom(1, -1)).toBe(0.9);
  });

  it("recognizes Ctrl/Cmd minus and plus variants", () => {
    expect(isZoomShortcut(keyEvent({ ctrlKey: true, key: "-" }))).toBe(-1);
    expect(isZoomShortcut(keyEvent({ metaKey: true, key: "+" }))).toBe(1);
    expect(isZoomShortcut(keyEvent({ ctrlKey: true, key: "=", code: "Equal" }))).toBe(1);
    expect(isZoomShortcut(keyEvent({ ctrlKey: true, code: "NumpadAdd" }))).toBe(1);
    expect(isZoomShortcut(keyEvent({ ctrlKey: true, code: "NumpadSubtract" }))).toBe(-1);
  });

  it("ignores non-zoom shortcuts", () => {
    expect(isZoomShortcut(keyEvent({ key: "+" }))).toBe(0);
    expect(isZoomShortcut(keyEvent({ ctrlKey: true, key: "0" }))).toBe(0);
  });

  it("does not intercept shortcuts while an editable control has focus", () => {
    expect(isEditableTarget(document.createElement("input"))).toBe(true);
    expect(isEditableTarget(document.createElement("textarea"))).toBe(true);
    expect(isEditableTarget(document.createElement("select"))).toBe(true);
    expect(isEditableTarget(document.createElement("button"))).toBe(false);
  });

  it("persists a clamped preference and rejects an invalid stored value", () => {
    localStorage.clear();
    saveZoomPreference(MAX_ZOOM + 2);
    expect(readZoomPreference()).toBe(MAX_ZOOM);

    localStorage.setItem("ziggs-companion-zoom", "not-a-number");
    expect(readZoomPreference()).toBe(DEFAULT_ZOOM);
  });
});
