import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { LangProvider } from "./i18n";
import { getVersion, invoke, listen, startDragging } from "./test/tauri";

const config = {
  api_base_url: "https://ziggs.xyz",
  collect_damage_meter: true,
  collect_auto_lootlog: true,
  capture_method: "npcap",
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

const idleStats = {
  running: true, online: true, packets_captured: 10, loot_count: 2,
  damage_total: 300, player_name: "Zezinho", party_members: [], last_map_name: "Caerleon", error: null,
};
const skill = (id: number, name: string, total: number) => ({
  id, name, name_pt: null, name_es: null, unique_name: null, icon: null, hits: 3, total, avg: total / 3, max_hit: total, pct: 50,
});
const damageRows = [
  { name: "Fulano", weapon: null, damage: 200, dps: 20, skills: [skill(1, "Rend", 200)], timeline: [] },
  { name: "Zezinho", weapon: null, damage: 100, dps: 10, skills: [skill(2, "Heavy Cleave", 100)], timeline: [] },
];
const lootRow = (ts: string, item_name: string, quantity: number) => ({
  ts, item_id: "T4_BAG", item_name, item_name_pt: item_name, item_name_es: item_name, quantity, looted_by: "Zezinho", looted_from: "Fulano",
});

function mockCommands(overrides: Record<string, (args?: unknown) => unknown>) {
  invoke.mockImplementation((command: string, args?: unknown) => {
    if (overrides[command]) return Promise.resolve().then(() => overrides[command](args));
    if (command === "get_config") return Promise.resolve(config);
    if (command === "get_sniff_stats") return Promise.resolve(idleStats);
    if (command === "get_catalog_status") return Promise.resolve(null);
    return Promise.resolve([]);
  });
}

describe("Companion views", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  beforeEach(() => {
    invoke.mockReset();
    getVersion.mockReset();
    listen.mockReset();
    getVersion.mockResolvedValue("0.2.18");
    listen.mockResolvedValue(() => {});
  });

  it("opens each player's row on its own and closes it again", async () => {
    mockCommands({ get_damage_meter: () => damageRows });
    renderApp();

    const fulano = await screen.findByRole("button", { name: /fulano/i });
    expect(fulano).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Rend")).not.toBeInTheDocument();

    fireEvent.click(fulano);
    expect(await screen.findByText("Rend")).toBeVisible();
    expect(fulano).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByRole("button", { name: /zezinho/i }));
    expect(await screen.findByText("Heavy Cleave")).toBeVisible();
    expect(screen.getByText("Rend")).toBeVisible();

    fireEvent.click(fulano);
    expect(screen.queryByText("Rend")).not.toBeInTheDocument();
    expect(screen.getByText("Heavy Cleave")).toBeVisible();
  });

  it("details each skill: hits, average, biggest hit, damage and share", async () => {
    mockCommands({ get_damage_meter: () => [{
      ...damageRows[0],
      skills: [{ ...skill(1, "Rend", 1_500_000), hits: 12, avg: 125_000, max_hit: 301_000, pct: 62.5 }],
    }] });
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: /fulano/i }));

    const row = (await screen.findByText("Rend")).closest('[role="row"]') as HTMLElement;
    expect(within(row).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["Rend", "12", "125K", "301K", "1.5M", "62.5"]);
    expect(screen.getAllByRole("columnheader").map(cell => cell.textContent)).toEqual(["Skill", "Hits", "Avg", "Max", "Damage", "%"]);
  });

  it("names the auto-attack, which has no skill id", async () => {
    mockCommands({ get_damage_meter: () => [{ ...damageRows[0], weapon: "sword", skills: [skill(-1, "", 200)] }] });
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: /fulano/i }));

    expect(await screen.findByText("Auto-attack")).toBeVisible();
    expect(screen.queryByText(/skill -1/i)).not.toBeInTheDocument();
  });

  it("colors the row by the player's weapon, without writing the weapon after the name", async () => {
    mockCommands({ get_damage_meter: () => [{ ...damageRows[0], weapon: "bow" }, { ...damageRows[1], weapon: "not-a-family" }] });
    renderApp();

    const known = await screen.findByRole("button", { name: /fulano/i });
    expect(known.parentElement).toHaveClass("w-bow");
    expect(known).not.toHaveTextContent(/bow|arco/i);
    expect(screen.getByRole("button", { name: /zezinho/i }).parentElement).not.toHaveClass("w-not-a-family");
  });

  describe("party filter", () => {
    const stranger = { name: "Estranho", weapon: null, damage: 700, dps: 70, skills: [], timeline: [] };
    const withParty = (party_members: string[]) => () => ({ ...idleStats, party_members });

    it("shows only the party and the local player, with bars and shares over them alone", async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
      mockCommands({ get_damage_meter: () => [stranger, ...damageRows], get_sniff_stats: withParty(["Fulano"]) });
      renderApp();
      await screen.findByRole("button", { name: /estranho/i });

      const filter = screen.getByRole("button", { name: "Party Only" });
      expect(filter).toHaveAttribute("aria-pressed", "false");
      fireEvent.click(filter);

      expect(filter).toHaveAttribute("aria-pressed", "true");
      expect(screen.queryByRole("button", { name: /estranho/i })).not.toBeInTheDocument();
      const fulano = screen.getByRole("button", { name: /fulano/i });
      expect(screen.getByRole("button", { name: /zezinho/i })).toBeVisible();
      expect(fulano).toHaveTextContent("66.7");

      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
      await screen.findByRole("button", { name: "Copied" });
      expect(writeText).toHaveBeenCalledWith("1. Fulano — 200 (66.7%)\n2. Zezinho — 100 (33.3%)");
    });

    it("explains an empty result instead of saying there is no combat", async () => {
      mockCommands({ get_damage_meter: () => [stranger], get_sniff_stats: withParty(["Fulano"]) });
      renderApp();
      await screen.findByRole("button", { name: /estranho/i });

      fireEvent.click(screen.getByRole("button", { name: "Party Only" }));

      expect(screen.getByText(/nobody in your party/i)).toBeVisible();
      expect(screen.getByRole("button", { name: "Copy" })).toBeDisabled();
      expect(screen.getByRole("button", { name: /clear/i })).toBeEnabled();
    });

    it("keeps the filters and the open rows after going to the Lootlog and back", async () => {
      mockCommands({ get_damage_meter: () => damageRows, get_sniff_stats: withParty([]) });
      renderApp();
      fireEvent.click(await screen.findByRole("button", { name: /zezinho/i }));
      fireEvent.click(screen.getByRole("button", { name: "Party Only" }));

      fireEvent.click(screen.getByRole("button", { name: /lootlog/i }));
      fireEvent.click(await screen.findByRole("button", { name: /damage meter/i }));

      const zezinho = await screen.findByRole("button", { name: /zezinho/i });
      expect(zezinho).toHaveAttribute("aria-expanded", "true");
      expect(screen.getByRole("button", { name: "Party Only" })).toHaveAttribute("aria-pressed", "true");
      expect(screen.queryByRole("button", { name: /fulano/i })).not.toBeInTheDocument();
    });
  });

  it("fetches the skill icons before any row is opened", async () => {
    const created: Array<{ src: string }> = [];
    vi.stubGlobal("Image", class { src = ""; constructor() { created.push(this); } });
    try {
      const unique = { ...skill(77, "Rare Skill", 10), icon: "ICON_ONLY_IN_THIS_TEST" };
      mockCommands({ get_damage_meter: () => [{ ...damageRows[0], skills: [unique] }] });
      renderApp();

      const row = await screen.findByRole("button", { name: /fulano/i });
      expect(row).toHaveAttribute("aria-expanded", "false");
      await waitFor(() => expect(created.map(image => image.src)).toContain("https://ziggs.xyz/render/spell/ICON_ONLY_IN_THIS_TEST"));
    } finally {
      vi.unstubAllGlobals();
    }
  });

  describe("copy", () => {
    const writeText = vi.fn();
    beforeEach(() => {
      writeText.mockReset();
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    });

    it("copies the ranking as text and confirms it", async () => {
      writeText.mockResolvedValue(undefined);
      mockCommands({ get_damage_meter: () => damageRows });
      renderApp();
      await screen.findByRole("button", { name: /fulano/i });

      fireEvent.click(screen.getByRole("button", { name: "Copy" }));

      expect(await screen.findByRole("button", { name: "Copied" })).toBeVisible();
      expect(writeText).toHaveBeenCalledWith("1. Fulano — 200 (66.7%)\n2. Zezinho — 100 (33.3%)");
    });

    it("says so when the copy fails instead of claiming it worked", async () => {
      writeText.mockRejectedValue(new Error("denied"));
      mockCommands({ get_damage_meter: () => damageRows });
      renderApp();
      await screen.findByRole("button", { name: /fulano/i });

      fireEvent.click(screen.getByRole("button", { name: "Copy" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(/could not copy/i);
      expect(screen.queryByRole("button", { name: "Copied" })).not.toBeInTheDocument();
    });

    it("has nothing to copy without players", async () => {
      mockCommands({ get_damage_meter: () => [] });
      renderApp();

      expect(await screen.findByRole("button", { name: "Copy" })).toBeDisabled();
    });
  });

  it("marks the player's own row in the ranking", async () => {
    mockCommands({ get_damage_meter: () => damageRows });
    renderApp();

    const own = await screen.findByRole("button", { name: /zezinho/i });
    expect(own).toHaveTextContent(/you/i);
    expect(screen.getByRole("button", { name: /fulano/i })).not.toHaveTextContent(/you/i);
  });

  it("lists the newest loot first in a table", async () => {
    mockCommands({
      get_damage_meter: () => [],
      get_captured_loot: () => [lootRow("2026-11-02T01:00:00.000Z", "First Bag", 1), lootRow("2026-11-02T02:30:00.000Z", "Second Bag", 3)],
    });
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /lootlog/i }));

    const rows = await screen.findAllByRole("row");
    expect(rows[1]).toHaveTextContent("02:30");
    expect(rows[1]).toHaveTextContent("Second Bag");
    expect(rows[1]).toHaveTextContent("3");
    expect(rows[2]).toHaveTextContent("First Bag");
  });

  it("says where the CSV was saved", async () => {
    mockCommands({
      get_damage_meter: () => [],
      get_captured_loot: () => [lootRow("2026-11-02T01:00:00.000Z", "First Bag", 1)],
      save_lootlog_csv: () => "C:\\Downloads\\lootlog.csv",
    });
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /lootlog/i }));
    await screen.findByText("First Bag");

    fireEvent.click(screen.getByRole("button", { name: /download \.csv/i }));

    expect(await screen.findByRole("status")).toHaveTextContent("C:\\Downloads\\lootlog.csv");
  });

  it("reports a CSV that could not be saved", async () => {
    mockCommands({
      get_damage_meter: () => [],
      get_captured_loot: () => [lootRow("2026-11-02T01:00:00.000Z", "First Bag", 1)],
      save_lootlog_csv: () => { throw new Error("disk full"); },
    });
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /lootlog/i }));
    await screen.findByText("First Bag");

    fireEvent.click(screen.getByRole("button", { name: /download \.csv/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not save/i);
  });

  it("shows the install guide while Npcap is missing and keeps the saved loot reachable", async () => {
    mockCommands({
      get_sniff_stats: () => ({ ...idleStats, online: false, capture_issue: "npcap_missing" }),
      get_damage_meter: () => [],
      get_captured_loot: () => [lootRow("2026-11-02T01:00:00.000Z", "First Bag", 1)],
    });
    renderApp();

    expect(await screen.findByRole("heading", { name: /install npcap/i })).toBeVisible();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByRole("banner")).toHaveTextContent("Npcap not found");
    expect(screen.queryByText(/no combat data yet/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /lootlog/i }));
    expect(await screen.findByText("First Bag")).toBeVisible();
    expect(screen.getByRole("heading", { name: /install npcap/i })).toBeVisible();
  });

  it("opens the download from the guide", async () => {
    mockCommands({
      get_sniff_stats: () => ({ ...idleStats, capture_issue: "npcap_missing" }),
      get_damage_meter: () => [],
      open_npcap_download: () => undefined,
    });
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: /download npcap/i }));

    expect(invoke).toHaveBeenCalledWith("open_npcap_download");
  });

  it("drops the guide as soon as the user switches to WinDivert", async () => {
    let method = "npcap";
    mockCommands({
      get_config: () => ({ ...config, capture_method: method }),
      get_sniff_stats: () => ({ ...idleStats, capture_issue: method === "npcap" ? "npcap_missing" : null }),
      get_damage_meter: () => [],
      set_config: args => { method = (args as { value: string }).value; },
    });
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: /use windivert/i }));

    expect(invoke).toHaveBeenCalledWith("set_config", { key: "capture_method", value: "windivert" });
    await waitFor(() => expect(screen.queryByRole("heading", { name: /install npcap/i })).not.toBeInTheDocument());
    expect(screen.getByRole("banner")).toHaveTextContent("WinDivert");
  });

  it("says so when switching method from the guide fails", async () => {
    mockCommands({
      get_sniff_stats: () => ({ ...idleStats, capture_issue: "npcap_missing" }),
      get_damage_meter: () => [],
      set_config: () => { throw new Error("write failed"); },
    });
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: /use windivert/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not save/i);
  });

  it("shows why Npcap could not open an adapter, in the guide and not in a second banner", async () => {
    mockCommands({
      get_sniff_stats: () => ({ ...idleStats, online: false, error: "no network adapter with an IPv4 address", capture_issue: "npcap_unavailable" }),
      get_damage_meter: () => [],
    });
    renderApp();

    expect(await screen.findByRole("heading", { name: /could not open/i })).toBeVisible();
    expect(screen.getByText("no network adapter with an IPv4 address")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /download npcap/i })).toBeVisible();
  });

  it("suggests WinDivert when Npcap sees nothing with Albion open, without an install guide", async () => {
    mockCommands({
      get_sniff_stats: () => ({ ...idleStats, online: false, capture_issue: "npcap_no_packets" }),
      get_damage_meter: () => [],
    });
    renderApp();

    expect(await screen.findByText(/exitlag/i)).toBeVisible();
    expect(screen.getByRole("button", { name: /use windivert/i })).toBeVisible();
    expect(screen.queryByRole("button", { name: /download npcap/i })).not.toBeInTheDocument();
    expect(screen.getByText(/no combat data yet/i)).toBeVisible();
    expect(screen.getByRole("banner")).toHaveTextContent("No packets");
  });

  it("offers to reopen as administrator or use Npcap when WinDivert has no permission", async () => {
    mockCommands({
      get_config: () => ({ ...config, capture_method: "windivert" }),
      get_sniff_stats: () => ({ ...idleStats, online: false, capture_issue: "windivert_needs_admin" }),
      get_damage_meter: () => [],
      relaunch_as_admin: () => undefined,
    });
    renderApp();

    expect(await screen.findByRole("heading", { name: /needs administrator/i })).toBeVisible();
    expect(screen.getByRole("banner")).toHaveTextContent("No permission");
    expect(screen.queryByText(/no combat data yet/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /open as administrator/i }));
    expect(invoke).toHaveBeenCalledWith("relaunch_as_admin");

    fireEvent.click(screen.getByRole("button", { name: /use npcap/i }));
    expect(invoke).toHaveBeenCalledWith("set_config", { key: "capture_method", value: "npcap" });
  });

  it("says Windows refused when reopening as administrator is declined", async () => {
    mockCommands({
      get_config: () => ({ ...config, capture_method: "windivert" }),
      get_sniff_stats: () => ({ ...idleStats, online: false, capture_issue: "windivert_needs_admin" }),
      get_damage_meter: () => [],
      relaunch_as_admin: () => { throw "elevation_declined"; },
    });
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: /open as administrator/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/did not authorize/i);
  });

  it("stays on the current method and says why when the switch to WinDivert is declined", async () => {
    mockCommands({
      get_damage_meter: () => [],
      get_npcap_status: () => ({ installed: true, version: "1.79" }),
      set_config: () => { throw "elevation_declined"; },
    });
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /settings/i }));

    fireEvent.click(screen.getByRole("radio", { name: /^windivert/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/did not authorize/i);
    expect(screen.getByRole("radio", { name: /^npcap/i })).toBeChecked();
  });

  it("lets the user choose the capture method in Settings and shows if Npcap was found", async () => {
    mockCommands({
      get_damage_meter: () => [],
      get_npcap_status: () => ({ installed: true, version: "1.79" }),
    });
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /settings/i }));

    const group = screen.getByRole("radiogroup", { name: /capture method/i });
    expect(group).toHaveTextContent("Npcap");
    expect(await screen.findByText("installed · 1.79")).toBeVisible();
    expect(screen.getByRole("radio", { name: /^npcap/i })).toBeChecked();

    fireEvent.click(screen.getByRole("radio", { name: /^windivert/i }));

    expect(invoke).toHaveBeenCalledWith("set_config", { key: "capture_method", value: "windivert" });
  });

  it("tells in Settings when Npcap is not installed", async () => {
    mockCommands({
      get_damage_meter: () => [],
      get_npcap_status: () => ({ installed: false, version: null }),
    });
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /settings/i }));

    expect(await screen.findByText("not found")).toBeVisible();
  });

  it("explains an empty loot session and keeps the actions disabled", async () => {
    mockCommands({ get_damage_meter: () => [], get_captured_loot: () => [] });
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /lootlog/i }));

    expect(await screen.findByText(/nothing captured yet/i)).toBeVisible();
    expect(screen.getByRole("button", { name: /download \.csv/i })).toBeDisabled();
  });
});
