import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useWebviewZoom } from "./useWebviewZoom";
import { setZoom } from "./test/tauri";

function ZoomHarness() {
  useWebviewZoom();
  return null;
}

function deferred<T>() {
  let reject!: (reason?: unknown) => void;
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

describe("WebView zoom shortcut", () => {
  beforeEach(() => {
    localStorage.clear();
    setZoom.mockReset();
    setZoom.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    document.body.replaceChildren();
  });

  it("changes only the WebView scale and persists it for Ctrl plus", async () => {
    render(<ZoomHarness />);

    const event = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ctrlKey: true, key: "+" });
    document.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    await waitFor(() => {
      expect(setZoom).toHaveBeenCalledWith(1.1);
      expect(localStorage.getItem("ziggs-companion-zoom")).toBe("1.1");
    });
  });

  it("leaves a focused input shortcut untouched", () => {
    render(<><input /><ZoomHarness /></>);
    const input = document.querySelector("input")!;
    input.focus();

    const event = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ctrlKey: true, key: "+" });
    input.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
    expect(setZoom).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("ziggs-companion-zoom")).toBeNull();
  });

  it("keeps the last applied preference when changing the WebView scale fails", async () => {
    render(<ZoomHarness />);
    await waitFor(() => {
      expect(setZoom).toHaveBeenCalledWith(1);
    });
    setZoom.mockRejectedValueOnce(new Error("WebView indisponível"));

    document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ctrlKey: true, key: "+" }));

    await waitFor(() => {
      expect(setZoom).toHaveBeenLastCalledWith(1.1);
    });
    expect(localStorage.getItem("ziggs-companion-zoom")).toBeNull();
  });

  it("accumulates rapid shortcuts from the requested WebView scale", async () => {
    render(<ZoomHarness />);
    await waitFor(() => {
      expect(setZoom).toHaveBeenCalledWith(1);
    });

    document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ctrlKey: true, key: "+" }));
    document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ctrlKey: true, key: "+" }));

    await waitFor(() => {
      expect(setZoom).toHaveBeenLastCalledWith(1.2);
      expect(localStorage.getItem("ziggs-companion-zoom")).toBe("1.2");
    });
  });

  it("serializes pending shortcuts so a stale completion cannot reset the scale", async () => {
    const firstShortcut = deferred<void>();
    const secondShortcut = deferred<void>();
    render(<ZoomHarness />);
    await waitFor(() => {
      expect(setZoom).toHaveBeenCalledWith(1);
    });
    setZoom.mockImplementationOnce(() => firstShortcut.promise)
      .mockImplementationOnce(() => secondShortcut.promise);

    document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ctrlKey: true, key: "+" }));
    document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ctrlKey: true, key: "+" }));

    expect(setZoom).toHaveBeenCalledTimes(1);
    firstShortcut.resolve();
    await waitFor(() => {
      expect(setZoom).toHaveBeenCalledWith(1.1);
    });
    await waitFor(() => {
      expect(setZoom).toHaveBeenLastCalledWith(1.2);
    });
    secondShortcut.resolve();
    await waitFor(() => {
      expect(localStorage.getItem("ziggs-companion-zoom")).toBe("1.2");
    });
  });

  it("persists the last applied zoom when the final queued shortcut fails", async () => {
    const firstShortcut = deferred<void>();
    const failedShortcut = deferred<void>();
    render(<ZoomHarness />);
    await waitFor(() => {
      expect(setZoom).toHaveBeenCalledWith(1);
    });
    setZoom.mockImplementationOnce(() => firstShortcut.promise)
      .mockImplementationOnce(() => failedShortcut.promise);

    document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ctrlKey: true, key: "+" }));
    document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ctrlKey: true, key: "+" }));

    firstShortcut.resolve();
    await waitFor(() => {
      expect(setZoom).toHaveBeenLastCalledWith(1.2);
    });
    failedShortcut.reject(new Error("WebView indisponível"));

    await waitFor(() => {
      expect(localStorage.getItem("ziggs-companion-zoom")).toBe("1.1");
    });
  });

  it("removes the global shortcut when the shell unmounts", () => {
    const shell = render(<ZoomHarness />);
    shell.unmount();
    const callsBeforeShortcut = setZoom.mock.calls.length;

    document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ctrlKey: true, key: "+" }));

    expect(setZoom).toHaveBeenCalledTimes(callsBeforeShortcut);
  });
});
