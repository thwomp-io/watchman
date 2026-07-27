import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NavContext, type Nav, type Ref } from "./nav";
import { mockTauri } from "./test/mockTauri";
import type { VaultDoc } from "./types";
import VaultZone from "./VaultZone";

// The trip-board wikilink bug: clicking an in-doc wikilink opened the target
// but BACK didn't return to the source doc — because the click called the LOCAL select() (which only
// fires the `report` effect → overwrites nav `current` in place, no history push). The fix routes it
// through nav.navigate(), which stacks the source doc so Back returns. This test pins that.

const TRIP: VaultDoc = {
  path: "travel/trips/summer-trip/summer-trip.md", area: "travel", dir: "travel/trips/summer-trip",
  name: "summer-trip", title: "A trip", kind: "doc",
};
const CITY: VaultDoc = {
  path: "travel/destinations/us-domestic/major-metropolis/chicago/chicago.md",
  area: "travel", dir: "travel/destinations/us-domestic/major-metropolis/chicago",
  name: "chicago", title: "Chicago", kind: "doc",
};

function renderVault(navigate: (r: Ref) => void, target?: string) {
  const nav: Nav = {
    current: { zone: "vault", doc: TRIP.path },
    navigate,
    back: vi.fn(),
    forward: vi.fn(),
    canGoBack: true,
    canGoForward: false,
    report: vi.fn(),
  };
  return render(
    <NavContext.Provider value={nav}>
      <VaultZone target={target} />
    </NavContext.Provider>,
  );
}

describe("VaultZone — in-doc wikilink navigation (regression)", () => {
  beforeEach(() => {
    mockTauri.reset();
    mockTauri.setValue("list_vault_docs", [TRIP, CITY]);
    mockTauri.set("read_doc", (a) =>
      a.path === TRIP.path ? "Go to [[chicago|Chicago]]." : "Chicago body.",
    );
  });

  it("routes a wikilink click through nav.navigate (history push), not local select", async () => {
    const navigate = vi.fn();
    renderVault(navigate, TRIP.path);

    const link = await screen.findByRole("link", { name: "Chicago" });
    fireEvent.click(link);

    // The fix: the click navigates to the target doc via the history primitive (so Back returns to the
    // trip doc). Under the old select()-only code, navigate is never called and this fails.
    expect(navigate).toHaveBeenCalledWith({ zone: "vault", doc: CITY.path });
  });
});

describe("VaultZone — phone master-detail (the 7/21 mobile-unusable field report)", () => {
  beforeEach(() => {
    mockTauri.reset();
    mockTauri.setValue("list_vault_docs", [TRIP, CITY]);
    mockTauri.set("read_doc", () => Promise.resolve("# hello"));
  });

  it("desktop: auto-selects a default doc (no empty stage) and marks the zone doc-open", async () => {
    window.innerWidth = 1024;
    const { container } = renderVault(vi.fn());
    await screen.findByText("A trip");
    expect(container.querySelector(".vault-zone.doc-open")).not.toBeNull();
  });

  it("phone: lands on the TREE (no auto-select), ◀ back returns from a doc", async () => {
    window.innerWidth = 390;
    const ROOT: VaultDoc = {
      path: "travel/notes.md", area: "travel", dir: "travel",
      name: "notes", title: "Root note", kind: "doc",
    };
    mockTauri.setValue("list_vault_docs", [ROOT, TRIP]);
    const { container } = renderVault(vi.fn());
    // the travel folder is open by default → its root-level doc is visible in the TREE
    await screen.findByText("Root note");
    // no doc auto-opened → the tree is the page
    expect(container.querySelector(".vault-zone.doc-open")).toBeNull();
    // open a doc → doc-open (CSS swaps rail→stage); ◀ returns to the tree
    fireEvent.click(screen.getByText("Root note"));
    expect(container.querySelector(".vault-zone.doc-open")).not.toBeNull();
    fireEvent.click(screen.getByText("◀"));
    expect(container.querySelector(".vault-zone.doc-open")).toBeNull();
  });
});
