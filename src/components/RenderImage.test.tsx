import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RenderImage } from "./RenderImage";

describe("RenderImage", () => {
  afterEach(cleanup);

  it("tries the next render candidate after the first image fails", () => {
    render(
      <RenderImage
        apiBaseUrl="https://ziggs.xyz"
        kind="spell"
        candidates={["MISSING_ART", "HAMMER_SHOVE"]}
        alt="Golpe Poderoso"
      />,
    );

    const image = screen.getByRole("img", { name: "Golpe Poderoso" });
    expect(image).toHaveAttribute("src", "https://ziggs.xyz/render/spell/MISSING_ART");

    fireEvent.error(image);

    expect(screen.getByRole("img", { name: "Golpe Poderoso" })).toHaveAttribute(
      "src",
      "https://ziggs.xyz/render/spell/HAMMER_SHOVE",
    );
  });

  it("marks the next candidate as retrying after a failed render", () => {
    render(
      <RenderImage
        apiBaseUrl="https://ziggs.xyz"
        kind="spell"
        candidates={["MISSING_ART", "HAMMER_SHOVE"]}
        alt="Golpe Poderoso"
      />,
    );

    fireEvent.error(screen.getByRole("img", { name: "Golpe Poderoso" }));

    expect(screen.getByRole("img", { name: "Golpe Poderoso" })).toHaveAttribute(
      "data-render-status",
      "retrying",
    );
  });

  it("keeps an accessible fixed-size placeholder after every candidate fails", () => {
    render(
      <RenderImage
        apiBaseUrl="https://ziggs.xyz"
        kind="item"
        candidates={["MISSING_ITEM"]}
        alt="Bolsa do Adepto"
        size={48}
      />,
    );

    fireEvent.error(screen.getByRole("img", { name: "Bolsa do Adepto" }));

    const placeholder = screen.getByRole("img", { name: "Bolsa do Adepto: art unavailable" });
    expect(placeholder).toHaveAttribute("data-render-status", "unavailable");
    expect(placeholder).toHaveStyle({ width: "48px", height: "48px" });
  });

  it("retries the same candidate after a transient render failure", async () => {
    vi.useFakeTimers();
    try {
      render(
        <RenderImage
          apiBaseUrl="https://ziggs.xyz"
          kind="item"
          candidates={["T4_BAG"]}
          alt="Bolsa do Adepto"
        />,
      );

      const image = screen.getByRole("img", { name: "Bolsa do Adepto" });
      fireEvent.error(image);
      await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });

      expect(screen.getByRole("img", { name: "Bolsa do Adepto" })).toHaveAttribute(
        "src",
        "https://ziggs.xyz/render/item/T4_BAG?size=64",
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports a loaded render after the browser confirms the image", () => {
    render(
      <RenderImage
        apiBaseUrl="https://ziggs.xyz"
        kind="item"
        candidates={["T4_BAG"]}
        alt="Bolsa do Adepto"
      />,
    );

    const image = screen.getByRole("img", { name: "Bolsa do Adepto" });
    fireEvent.load(image);

    expect(image).toHaveAttribute("data-render-status", "loaded");
  });
});
