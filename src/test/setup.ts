import "@testing-library/jest-dom/vitest";
import "./tauri";

const storage = new Map<string, string>();
const localStorageMock: Storage = {
  getItem: key => storage.get(key) ?? null,
  setItem: (key, value) => { storage.set(key, String(value)); },
  removeItem: key => { storage.delete(key); },
  clear: () => { storage.clear(); },
  key: index => [...storage.keys()][index] ?? null,
  get length() { return storage.size; },
};

Object.defineProperty(globalThis, "localStorage", { configurable: true, value: localStorageMock });
