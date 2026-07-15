import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import Dash, { migrateLegacyLayouts, resolveOverlaps } from "./Dash";
import type { Dashboard, Widget } from "./types";
import {
  docSeriesWidget, mockTauri, oneWidgetDash, packFixture, realFixture,
  stageEmptyWidgetSources, statWidget, studioFixture, symbolWidget,
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

    // The full fixture's extra Finance subtabs are present.
    expect(await screen.findByRole("button", { name: "Unwind" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Market" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tickets" })).toBeInTheDocument();

    // Swap to a pack that describes a curated console (Finance has only "Core").
    mockTauri.setValue("list_dashboards", packFixture());
    rerender(<Dash reloadKey="demo-growth" />);

    // Layout refetched: the full fixture's extra Finance subtabs are gone...
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

describe("Dash — Dashboard Studio render-path split", () => {
  beforeEach(() => {
    mockTauri.reset();
    stageEmptyWidgetSources();
  });

  it("a layout-bearing dashboard renders through the studio grid", async () => {
    mockTauri.setValue("list_dashboards", studioFixture());
    const { container } = render(<Dash reloadKey="studio" />);
    await screen.findByText("Studio A");
    expect(container.querySelector(".studio-grid")).not.toBeNull();
    expect(container.querySelector(".dash-grid")).toBeNull();
    // both widgets render inside RGL cells
    expect(container.querySelectorAll(".studio-cell").length).toBe(2);
  });

  it("a legacy (layout-less) dashboard keeps the dense-flow grid, untouched", async () => {
    mockTauri.setValue("list_dashboards", realFixture());
    const { container } = render(<Dash reloadKey="real" />);
    await screen.findByText("Core stat");
    expect(container.querySelector(".dash-grid")).not.toBeNull();
    expect(container.querySelector(".studio-grid")).toBeNull();
  });
});

describe("Dashboard Studio — unlock, migration, save (checkpoint C)", () => {
  beforeEach(() => {
    mockTauri.reset();
    stageEmptyWidgetSources();
  });

  it("migrateLegacyLayouts synthesizes first-fit row-dense placements", () => {
    const d: Dashboard = {
      lane: "x", title: "X", widgets: [
        { ...statWidget("a", "A"), span: 2 },              // stat -> 2x1 at 0,0
        { ...statWidget("b", "B"), span: 2 },              // stat -> 2x1 at 2,0
        { ...statWidget("c", "C"), kind: "table", span: 4, rows: 1 }, // -> 4x3 at 0,1
        { ...statWidget("d", "D"), span: 2 },              // stat -> 2x1... first fit = 0,4
      ],
    };
    const m = migrateLegacyLayouts(d, 4);
    expect(m.owner).toBe("user");
    expect(m.widgets[0].layout).toEqual({ x: 0, y: 0, w: 2, h: 1 });
    expect(m.widgets[1].layout).toEqual({ x: 2, y: 0, w: 2, h: 1 });
    expect(m.widgets[2].layout).toEqual({ x: 0, y: 1, w: 4, h: 3 });
    expect(m.widgets[3].layout).toEqual({ x: 0, y: 4, w: 2, h: 1 });
  });

  it("unlocking a legacy dashboard migrates it and persists via save_dashboard", async () => {
    mockTauri.setValue("list_dashboards", realFixture());
    mockTauri.setValue("save_dashboard", null);
    render(<Dash reloadKey="" />);
    await screen.findByText("Core stat");

    fireEvent.click(screen.getByRole("button", { name: /LOCKED/ }));
    // the migration saved immediately: the dashboard went studio-managed + user-owned
    await waitFor(() => {
      const call = mockTauri.calls().find(([cmd]) => cmd === "save_dashboard");
      expect(call).toBeTruthy();
      const saved = (call![1] as { dashboard: Dashboard }).dashboard;
      expect(saved.owner).toBe("user");
      expect(saved.widgets.every((w: Widget) => !!w.layout)).toBe(true);
    });
    // and the toggle reads EDITING
    expect(screen.getByRole("button", { name: /EDITING/ })).toBeInTheDocument();
  });

  it("the unlock toggle is hidden while a pack is active (transient dashboards)", async () => {
    mockTauri.setValue("list_dashboards", packFixture());
    render(<Dash reloadKey="demo-growth" />);
    await screen.findByRole("button", { name: "Finance" });
    expect(screen.queryByRole("button", { name: /LOCKED|EDITING/ })).toBeNull();
  });
});

describe("Dashboard Studio — the no-widget-loss invariant (drag-collision regression)", () => {
  // The originating field bug, reconstructed: on a 6-col dashboard the user dragged a 1x1
  // tile onto an occupied cell; RGL pushed the occupant down a row — directly UNDER a 3x3
  // panel — and it vanished from view. The resolver must relocate the PUSHED widget (never the
  // user's drag) to the drag's vacated cell. Swap semantics.
  const lay = (x: number, y: number, w = 1, h = 1) => ({ x, y, w, h });
  function coreLike(): Dashboard {
    const w = (id: string, l: { x: number; y: number; w: number; h: number }): Widget =>
      ({ ...statWidget(id, id), layout: l });
    return {
      lane: "finance", title: "Core", owner: "user",
      widgets: [
        w("networth", lay(0, 0)), w("proxy", lay(1, 0)), w("spy", lay(2, 0)),
        w("rsp", lay(3, 0)), w("breadth", lay(4, 0)), w("mag7", lay(5, 0)),
        w("trend", lay(3, 1, 3, 3)),
      ],
    };
  }

  it("relocates the RGL-pushed victim to the vacated cell — never hides, never drops", () => {
    const prev = coreLike();
    // what RGL hands the commit after the incident's drag:
    const cells = [
      { i: "networth", ...lay(0, 0) }, { i: "proxy", ...lay(1, 0) }, { i: "spy", ...lay(2, 0) },
      { i: "rsp", ...lay(3, 1) },      // RGL's push — lands under trend
      { i: "breadth", ...lay(3, 0) },  // the user's drag
      { i: "mag7", ...lay(5, 0) },
      { i: "trend", ...lay(3, 1, 3, 3) },
    ];
    const out = resolveOverlaps(prev, cells, 6, "breadth");
    const at = (id: string) => out.find((c) => c.i === id)!;
    expect(out.length).toBe(cells.length);                    // count preserved
    expect(at("breadth")).toMatchObject(lay(3, 0));           // the drag wins its cell
    expect(at("rsp")).toMatchObject(lay(4, 0));               // the victim swaps into the vacated slot
    expect(at("trend")).toMatchObject(lay(3, 1, 3, 3));       // stayers never move
    // and nothing overlaps anything
    const seen = new Set<string>();
    for (const c of out) {
      for (let r = c.y; r < c.y + c.h; r++) for (let cc = c.x; cc < c.x + c.w; cc++) {
        const k = `${cc},${r}`;
        expect(seen.has(k), `overlap at ${k}`).toBe(false);
        seen.add(k);
      }
    }
  });

  it("without an activeId every mover still lands on a free cell (count preserved)", () => {
    const prev = coreLike();
    const cells = [
      { i: "networth", ...lay(0, 0) }, { i: "proxy", ...lay(1, 0) }, { i: "spy", ...lay(2, 0) },
      { i: "rsp", ...lay(3, 1) }, { i: "breadth", ...lay(3, 0) }, { i: "mag7", ...lay(5, 0) },
      { i: "trend", ...lay(3, 1, 3, 3) },
    ];
    const out = resolveOverlaps(prev, cells, 6, null);
    expect(out.length).toBe(cells.length);
    const seen = new Set<string>();
    for (const c of out) {
      for (let r = c.y; r < c.y + c.h; r++) for (let cc = c.x; cc < c.x + c.w; cc++) {
        const k = `${cc},${r}`;
        expect(seen.has(k)).toBe(false);
        seen.add(k);
      }
    }
  });
});

describe("Dashboard Studio — return to default (two-click confirm)", () => {
  beforeEach(() => {
    mockTauri.reset();
    stageEmptyWidgetSources();
  });

  it("arm-then-confirm resets, replaces state, and re-locks", async () => {
    mockTauri.setValue("list_dashboards", realFixture());
    mockTauri.setValue("save_dashboard", null);
    const fresh = { lane: "finance", group: "Finance", title: "Core", owner: "default",
                    widgets: [statWidget("restored", "Restored stat")] };
    mockTauri.setValue("reset_dashboard", fresh);
    render(<Dash reloadKey="" />);
    await screen.findByText("Core stat");

    fireEvent.click(screen.getByRole("button", { name: /LOCKED/ })); // unlock (migrates)
    const reset = await screen.findByRole("button", { name: "↺ DEFAULT" });
    fireEvent.click(reset); // arm
    expect(screen.getByRole("button", { name: /CONFIRM RESET/ })).toBeInTheDocument();
    expect(mockTauri.calls().some(([c]) => c === "reset_dashboard")).toBe(false); // armed ≠ fired
    fireEvent.click(screen.getByRole("button", { name: /CONFIRM RESET/ })); // confirm
    await waitFor(() => {
      expect(mockTauri.calls().some(([c]) => c === "reset_dashboard")).toBe(true);
      expect(screen.getByText("Restored stat")).toBeInTheDocument(); // state swapped in place
    });
    // re-locked: the toggle reads LOCKED again and the reset button is gone
    expect(screen.getByRole("button", { name: /LOCKED/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /DEFAULT|CONFIRM/ })).toBeNull();
  });

  it("the reset button only exists in edit mode", async () => {
    mockTauri.setValue("list_dashboards", realFixture());
    render(<Dash reloadKey="" />);
    await screen.findByText("Core stat");
    expect(screen.queryByRole("button", { name: /DEFAULT/ })).toBeNull();
  });
});
