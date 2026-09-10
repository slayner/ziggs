import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import App from "./App";
import { LangProvider } from "./i18n";
import { getVersion, invoke, listen, startDragging } from "./test/tauri";

const config = {
  api_base_url: "https://ziggs.xyz",
  collect_damage_meter: true,
  collect_auto_lootlog: true,
  autostart: false,
  minimize_to_tray: true,
  spell_index_offset: 0,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => {
    resolve = done;
  });
  return { promise, resolve };
}

function renderApp() {
  return render(<LangProvider><App /></LangProvider>);
}

describe("Companion shell", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
    document.documentElement.removeAttribute("lang");
  });

  beforeEach(() => {
    invoke.mockReset();
    getVersion.mockReset();
    listen.mockReset();
    startDragging.mockReset();
    getVersion.mockResolvedValue("0.2.15");
    listen.mockResolvedValue(() => {});
    invoke.mockImplementation((command: string) => {
      if (command === "get_config") return Promise.resolve(config);
      if (command === "get_catalog_status") return Promise.resolve({
        spells: { state: "degraded", source: "cache", count: 3, error: "offline" },
        items: { state: "ready", source: "backend", count: 9, error: null },
      });
      if (command === "get_sniff_stats") return Promise.resolve({
        running: false,
        online: false,
        packets_captured: 0,
        loot_count: 0,
        damage_total: 0,
        player_name: "",
        party_members: [],
        last_map_name: "",
        error: null,
      });
      return Promise.resolve([]);
    });
  });

  it("does not leave a rejection when title bar dragging fails", async () => {
    startDragging.mockRejectedValue(new Error("IPC indisponível"));
    renderApp();

    const bar = await screen.findByRole("banner");
    expect(() => fireEvent.mouseDown(bar, { button: 0 })).not.toThrow();
    await Promise.resolve();
  });

  it("keeps capture switches out of the view navigation", async () => {
    renderApp();

    expect(await screen.findByRole("button", { name: /damage meter/i })).toBeVisible();
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
  });

  it("places the exact persistent capture settings in Settings", async () => {
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /settings/i }));

    expect(screen.getByRole("dialog", { name: /settings/i })).toHaveAttribute("aria-modal", "true");
    const switches = screen.getAllByRole("switch");
    expect(switches).toHaveLength(4);

    fireEvent.click(switches[0]);

    expect(invoke).toHaveBeenCalledWith("set_config", {
      key: "collect_damage_meter",
      value: false,
    });
  });

  it("updates the document language to the selected interface language", async () => {
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /settings/i }));

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "es" } });

    expect(await screen.findByRole("dialog", { name: "Configuración" })).toBeVisible();
    expect(document.documentElement.lang).toBe("es");
  });

  it("focuses the close control when Settings opens", async () => {
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /settings/i }));

    expect(screen.getByRole("button", { name: /close settings/i })).toHaveFocus();
  });

  it("closes Settings with Escape and restores the trigger focus", async () => {
    renderApp();
    const trigger = await screen.findByRole("button", { name: /settings/i });
    fireEvent.click(trigger);

    fireEvent.keyDown(screen.getByRole("dialog", { name: /settings/i }), { key: "Escape" });

    expect(screen.queryByRole("dialog", { name: /settings/i })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("keeps Tab navigation inside Settings", async () => {
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /settings/i }));

    const dialog = screen.getByRole("dialog", { name: /settings/i });
    const close = screen.getByRole("button", { name: /close settings/i });
    const firstSwitch = screen.getAllByRole("switch")[0];
    const lastFocusable = screen.getAllByRole("switch").at(-1)!;

    lastFocusable.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(close).toHaveFocus();

    close.focus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(lastFocusable).toHaveFocus();
  });

  it("reports degraded catalogs without blocking the capture settings", async () => {
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /settings/i }));

    expect(await screen.findByText(/fallback active.*3 entries.*cache/i)).toBeVisible();
    expect(screen.getByText(/9 entries.*backend/i)).toBeVisible();
  });

  it("labels a loading catalog separately from a degraded one", async () => {
    invoke.mockImplementation((command: string) => {
      if (command === "get_config") return Promise.resolve(config);
      if (command === "get_catalog_status") return Promise.resolve({
        spells: { state: "loading", source: "fallback", count: 0, error: null },
        items: { state: "ready", source: "backend", count: 9, error: null },
      });
      if (command === "get_sniff_stats") return Promise.resolve({
        running: false, online: false, packets_captured: 0, loot_count: 0,
        damage_total: 0, player_name: "", party_members: [], last_map_name: "", error: null,
      });
      return Promise.resolve([]);
    });
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /settings/i }));

    expect(await screen.findByText(/loading.*0 entries.*fallback/i)).toBeVisible();
  });

  it("shows a capture failure instead of the offline state", async () => {
    invoke.mockImplementation((command: string) => {
      if (command === "get_config") return Promise.resolve(config);
      if (command === "get_catalog_status") return Promise.resolve({
        spells: { state: "ready", source: "cache", count: 3, error: null },
        items: { state: "ready", source: "cache", count: 9, error: null },
      });
      if (command === "get_sniff_stats") return Promise.resolve({
        running: false, online: false, packets_captured: 0, loot_count: 0,
        damage_total: 0, player_name: "", party_members: [], last_map_name: "",
        error: "WinDivert: driver ausente",
      });
      return Promise.resolve([]);
    });

    renderApp();

    expect(await screen.findByText("WinDivert: driver ausente")).toBeVisible();
    expect(screen.queryByText("Albion closed")).not.toBeInTheDocument();
  });

  it("keeps the displayed configuration unchanged when saving a capture setting fails", async () => {
    const pendingConfig = deferred<typeof config>();
    invoke.mockImplementation((command: string) => {
      if (command === "get_config") return pendingConfig.promise;
      if (command === "set_config") return Promise.reject(new Error("write failed"));
      return Promise.resolve([]);
    });

    renderApp();
    pendingConfig.resolve(config);

    fireEvent.click(await screen.findByRole("button", { name: /settings/i }));
    const damageSwitch = screen.getAllByRole("switch")[0];
    expect(damageSwitch).toHaveAttribute("aria-checked", "true");

    fireEvent.click(damageSwitch);

    expect(await screen.findByText(/could not save/i)).toBeVisible();
    expect(screen.getAllByRole("switch")[0]).toHaveAttribute("aria-checked", "true");
  });

  it("does not leave a rejection when the initial configuration request fails", async () => {
    invoke.mockRejectedValue(new Error("IPC indisponível"));

    expect(() => renderApp()).not.toThrow();
    await Promise.resolve();
  });

  it("does not leave a rejection when update event registration fails", async () => {
    listen.mockRejectedValue(new Error("IPC indisponível"));

    expect(() => renderApp()).not.toThrow();
    await Promise.resolve();
  });
});
