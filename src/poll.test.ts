import { describe, expect, it, vi } from "vitest";
import { executePoll, ignoreAsyncFailure } from "./App";

describe("executePoll", () => {
  it("absorbs a transient polling failure", async () => {
    const poll = vi.fn().mockRejectedValue(new Error("IPC indisponível"));

    await expect(executePoll(poll)).resolves.toBeUndefined();
    expect(poll).toHaveBeenCalledOnce();
  });
});

describe("ignoreAsyncFailure", () => {
  it("absorbs an unobserved IPC failure", async () => {
    const operation = vi.fn().mockRejectedValue(new Error("IPC indisponível"));

    expect(() => ignoreAsyncFailure(operation())).not.toThrow();
    await Promise.resolve();
    expect(operation).toHaveBeenCalledOnce();
  });
});
