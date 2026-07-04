import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import Dash from "./Dash";
import {
  docSeriesWidget, mockTauri, oneWidgetDash, packFixture, realFixture,
  stageEmptyWidgetSources, symbolWidget,
} from "./test/mockTauri";

describe("Dash — pack-described dashboards", () => {
  beforeEach(() => {
    mockTauri.reset();
    stageEmptyWidgetSources();
    // (the dash widget cache is localStorage-backed but localStorage is absent under jsdom here, so the
    // cache no-ops via its try/catch — no cross-test bleed to clear.)
  });

  it("renders the grouped tab nav from list_dashboards", async () => {
    mockTauri.setValue("list_dashboards", realFixture());
    render(<Dash reloadKey="real" />);

    expect(await screen.findByRole("button", { name: "Finance" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Career" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Travel" })).toBeInTheDocument();
    // Finance is the initial group → its subtabs are exposed.
    expect(screen.getByRole("button", { name: "Unwind" })).toBeInTheDocument();
  });

  // THE REGRESSION (the bug the eyeball caught post-deploy). v2 lets a pack DESCRIBE its dashboards,
  // so a swap changes the LAYOUT, not just the data. Dash must re-fetch list_dashboards on a reloadKey
  // change — refetching only widget data (the pre-fix behavior) left a stale tab-set whose widgets no
  // longer resolved ("unknown widget"). This pins the layout refetch + the preserve-on-swap.
  it("re-fetches the LAYOUT on a pack swap, replacing the tab-set", async () => {
    mockTauri.setValue("list_dashboards", realFixture());
    const { rerender } = render(<Dash reloadKey="real" />);

    // The real console's extra Finance subtabs are present.
    expect(await screen.findByRole("button", { name: "Unwind" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Market" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tickets" })).toBeInTheDocument();

    // Swap to a pack that describes a curated console (Finance has only "Core").
    mockTauri.setValue("list_dashboards", packFixture());
    rerender(<Dash reloadKey="demo-growth" />);

    // Layout refetched: the real console's extra Finance subtabs are gone...
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Unwind" })).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Market" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Tickets" })).not.toBeInTheDocument();
    // ...and the group nav still shows the three groups (we stayed on Finance — preserve-on-swap).
    expect(screen.getByRole("button", { name: "Finance" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Career" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Travel" })).toBeInTheDocument();
  });

  // THE REGRESSION (bug 2): a bars/position widget keeps its selected-symbol
  // STATE across a pack swap (same widget id → same component instance). Without reconciliation it
  // refetches with the prior persona's ticker against the new portfolio → "<sym> not in config". The
  // fix: effSymbol snaps to a valid symbol for the current widget + the refetch tick fires AFTER the new
  // layout commits. This pins both (no error surface + the right pill goes active).
  it("reconciles a bars widget's symbol on a pack swap (no stale-ticker query)", async () => {
    const valid = new Set(["AAPL", "MSFT"]); // pack A's portfolio
    mockTauri.set("run_widget", (args) => {
      const sym = args.symbol as string | null;
      if (sym && !valid.has(sym)) throw new Error(`${sym} not in config`);
      return "{}";
    });
    mockTauri.setValue("list_dashboards",
      [oneWidgetDash("finance", "Finance", "Core", symbolWidget("position_chart", ["AAPL", "MSFT"]))]);
    const { rerender } = render(<Dash reloadKey="real" />);

    // mounted on the first symbol (AAPL); its pill is active, nothing errors.
    await waitFor(() => expect(screen.getByRole("button", { name: "AAPL" })).toHaveClass("active"));
    expect(screen.queryByText(/not in config/)).not.toBeInTheDocument();

    // swap to a persona holding GOOGL/AMZN — AAPL/MSFT are no longer valid tickers.
    valid.clear(); valid.add("GOOGL"); valid.add("AMZN");
    mockTauri.setValue("list_dashboards",
      [oneWidgetDash("finance", "Finance", "Core", symbolWidget("position_chart", ["GOOGL", "AMZN"]))]);
    rerender(<Dash reloadKey="demo-investor" />);

    // the held AAPL selection reconciles to GOOGL → no "<sym> not in config" surfaces, GOOGL pill active.
    await waitFor(() => expect(screen.getByRole("button", { name: "GOOGL" })).toHaveClass("active"));
    expect(screen.queryByText(/not in config/)).not.toBeInTheDocument();
  });

  // THE REGRESSION (bug 1, same eyeball): a doc_series widget (a scan report / market take) never
  // re-listed on a pack swap — its source dir is pack-INVARIANT (resolved pack-aware Rust-side) so
  // neither its dep nor onVaultChanged fired, and it received no refetch tick. It showed the PREVIOUS
  // persona's docs (real data bleeding into a demo) until a manual ⟳. The fix threads forceTick in + re-lists on it.
  it("re-lists a doc_series widget on a pack swap (drops the prior persona's docs)", async () => {
    mockTauri.setValue("list_dashboards",
      [oneWidgetDash("career", "Career", "Board", docSeriesWidget("openings", "career/discoveries"))]);
    mockTauri.set("list_vault_dir",
      () => [{ path: "career/discoveries/a.md", name: "a", title: "Scan A" }]);
    mockTauri.set("read_doc",
      (args) => ((args.path as string).endsWith("a.md") ? "# Scan A\nalpha" : "# Scan B\nbeta"));
    const { rerender } = render(<Dash reloadKey="real" />);

    expect(await screen.findByText("Scan A")).toBeInTheDocument();

    // swap persona: the same (pack-invariant) dir now resolves to a different scan report.
    mockTauri.set("list_vault_dir",
      () => [{ path: "career/discoveries/b.md", name: "b", title: "Scan B" }]);
    rerender(<Dash reloadKey="demo-investor" />);

    await waitFor(() => expect(screen.getByText("Scan B")).toBeInTheDocument());
    expect(screen.queryByText("Scan A")).not.toBeInTheDocument();
  });

  it("falls back gracefully when the active subtab vanishes after a swap", async () => {
    mockTauri.setValue("list_dashboards", realFixture());
    const { rerender } = render(<Dash reloadKey="real" />);
    // Navigate to a Finance subtab that won't exist in the pack.
    fireEvent.click(await screen.findByRole("button", { name: "Tickets" }));

    mockTauri.setValue("list_dashboards", packFixture());
    rerender(<Dash reloadKey="demo-growth" />);

    // No crash; the curated console renders (the vanished lane fell back to the group's first dashboard).
    expect(await screen.findByRole("button", { name: "Finance" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Tickets" })).not.toBeInTheDocument();
  });
});
