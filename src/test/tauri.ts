import { vi } from "vitest";

export const invoke = vi.fn();
export const getVersion = vi.fn();
export const listen = vi.fn();
export const startDragging = vi.fn();
export const minimize = vi.fn().mockResolvedValue(undefined);
export const close = vi.fn().mockResolvedValue(undefined);
export const setZoom = vi.fn().mockResolvedValue(undefined);

vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("@tauri-apps/api/app", () => ({ getVersion }));
vi.mock("@tauri-apps/api/event", () => ({ listen }));
vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({ startDragging, minimize, close }),
}));
vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => ({ setZoom }),
}));
