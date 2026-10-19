// Dash deep links into a subtab: `follows` resolves the subtab whose widgets follow a picker scope
// and selects `pick` there before it opens (then reports the concrete lane so ◀ comes back to it);
// `dash` restores an exact lane; a subtab click reports itself.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Dash from "./Dash";
import { NavContext, type Nav, type Ref } from "./nav";
import { getSelection, resetSelections } from "./selection";
import { dashboard, mockTauri, oneWidgetDash, stageEmptyWidgetSources, statWidget } from "./test/mockTauri";

function fixture() {
  return [
    dashboard("finance", "Finance", "Core"),
    dashboard("travel", "Travel", "Planning"),
    oneWidgetDash("travel-visits", "Travel", "Visits", { ...statWidget("v", "Visit"), symbols_from: "visit" }),
    oneWidgetDash("travel-trips", "Travel", "Trips", { ...statWidget("t", "Trip"), symbols_from: "trip" }),
  ];
}

function withNav(current: Ref) {
  const report = vi.fn();
  const nav: Nav = {
    current, report, navigate: vi.fn(), back: vi.fn(), forward: vi.fn(), canGoBack: false, canGoForward: false,
  };
  return { nav, report, ui: <NavContext.Provider value={nav}><Dash reloadKey="x" /></NavContext.Provider> };
}

describe("Dash — subtab deep links", () => {
  beforeEach(() => {
    mockTauri.reset();
    stageEmptyWidgetSources();
    mockTauri.setValue("list_dashboards", fixture());
    resetSelections();
    try { localStorage.clear(); } catch { /* jsdom */ }
  });

  it("follows: opens the subtab that follows the scope, with the pick selected", async () => {
    const { ui, report } = withNav({ zone: "dash", follows: "trip", pick: "2027-02-sample-cove" });
    render(ui);
    await waitFor(() => expect(screen.getByRole("button", { name: "Trips" })).toHaveClass("active"));
    expect(screen.getByRole("button", { name: "Travel" })).toHaveClass("active");
    expect(getSelection("travel-trips", "trip")).toBe("2027-02-sample-cove");
    expect(report).toHaveBeenCalledWith({ dash: "travel-trips", follows: undefined, pick: undefined });
  });

  it("dash: restores an exact lane (the ◀ return to Planning)", async () => {
    const { ui } = withNav({ zone: "dash", dash: "travel" });
    render(ui);
    await waitFor(() => expect(screen.getByRole("button", { name: "Planning" })).toHaveClass("active"));
  });

  it("a subtab click reports its lane into the history", async () => {
    const { ui, report } = withNav({ zone: "dash", dash: "travel" });
    render(ui);
    fireEvent.click(await screen.findByRole("button", { name: "Visits" }));
    expect(report).toHaveBeenCalledWith({ dash: "travel-visits" });
  });
});
