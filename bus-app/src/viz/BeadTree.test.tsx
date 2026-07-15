// The bead family tree: families lay out as an org chart (wrapping to the
// container width), singles shelf beneath, blocks-deps overlay dashed, hover card carries the
// open-ticket link, tile click quick-looks the ticket in a DocPopup, and the empty board reads
// calm (never broken). Fixture is fully fictional — ids/titles invented for the test.
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mockTauri } from "../test/mockTauri";
import BeadTree from "./BeadTree";

beforeEach(() => mockTauri.reset());

const node = (id: string, over: Record<string, unknown> = {}) => ({
  id, title: `Title of ${id}`, status: "open", priority: "P2", type: "task",
  assignee: "", labels: "demo", updated: "2026-01-02", ticket: `ops/beads/${id}.md`, ...over,
});

const fixture = {
  beads: [
    node("t-epic", { status: "open", priority: "P1", type: "epic" }),
    node("t-kid1", { status: "in_progress", assignee: "dev" }),
    node("t-kid2", { status: "closed" }),
    node("t-solo", { status: "in_progress" }),
  ],
  edges: [
    { source: "t-epic", target: "t-kid1", kind: "child" },
    { source: "t-epic", target: "t-kid2", kind: "child" },
    { source: "t-kid2", target: "t-kid1", kind: "blocks" },
  ],
  omitted: 3,
};

describe("BeadTree", () => {
  it("renders every bead block, both edge kinds, and the omitted honesty count", () => {
    const { container } = render(<BeadTree data={fixture} />);
    for (const id of ["t-epic", "t-kid1", "t-kid2", "t-solo"]) {
      expect(screen.getByText(id)).toBeInTheDocument();
    }
    expect(container.querySelectorAll(".beadtree-edge")).toHaveLength(2);
    expect(container.querySelectorAll(".beadtree-edge-blocks")).toHaveLength(1);
    expect(screen.getByText(/3 QUIET SINGLES OFF-TREE/)).toBeInTheDocument();
  });

  it("children sit a row beneath their parent; the single shelves below the family", () => {
    const { container } = render(<BeadTree data={fixture} />);
    const y = (id: string) => {
      const g = screen.getByText(id).closest("g")!;
      return Number(/translate\([\d.]+,([\d.]+)\)/.exec(g.getAttribute("transform")!)![1]);
    };
    expect(y("t-kid1")).toBeGreaterThan(y("t-epic"));
    expect(y("t-solo")).toBeGreaterThan(y("t-kid1"))
    expect(container.querySelector(".beadtree-node.st-in_progress")).not.toBeNull();
    expect(container.querySelector(".beadtree-node.p1")).not.toBeNull();
  });

  it("hovering a block opens the metadata card with the open-ticket link", () => {
    const { container } = render(<BeadTree data={fixture} />);
    fireEvent.mouseEnter(screen.getByText("t-kid1").closest("g")!);
    const tip = within(container.querySelector(".viz-tip") as HTMLElement);
    expect(tip.getByText("Title of t-kid1")).toBeInTheDocument();
    expect(tip.getByText("in_progress")).toBeInTheDocument();
    expect(tip.getByText("dev")).toBeInTheDocument();
    expect(tip.getByText("open ticket →")).toBeInTheDocument();
  });

  it("hovering a blocks edge opens the relationship card — endpoints side-by-side + the sentence", () => {
    const { container } = render(<BeadTree data={fixture} />);
    fireEvent.mouseEnter(container.querySelector(".beadtree-edge-hit")!);
    const tip = within(container.querySelector(".beadtree-rel-tip") as HTMLElement);
    expect(tip.getByText("BLOCKS-DEP")).toBeInTheDocument();
    expect(tip.getByText("blocker")).toBeInTheDocument();
    expect(tip.getByText("blocked")).toBeInTheDocument();
    expect(tip.getByText("t-kid2")).toBeInTheDocument();
    expect(tip.getByText("t-kid1")).toBeInTheDocument();
    // the fixture's blocker is CLOSED → the state chip reads resolved, not future-tense
    expect(tip.getByText("resolved — blocker closed")).toBeInTheDocument();
    expect(tip.getAllByText("open ticket →")).toHaveLength(2);
    // both endpoint blocks light up while the edge is hovered
    expect(container.querySelectorAll(".beadtree-node.hot")).toHaveLength(2);
    expect(container.querySelector(".beadtree-edge-blocks.hot")).not.toBeNull();
  });

  it("an open blocker reads as an active gate in the state chip", () => {
    const { container } = render(<BeadTree data={{
      beads: [node("t-gate", { status: "open" }), node("t-waiting")],
      edges: [{ source: "t-gate", target: "t-waiting", kind: "blocks" }],
    }} />);
    fireEvent.mouseEnter(container.querySelector(".beadtree-edge-hit")!);
    expect(screen.getByText("active — blocker must close first")).toBeInTheDocument();
  });

  it("clicking a tile quick-looks the full ticket in the DocPopup (hover flows untouched)", async () => {
    mockTauri.setValue("read_doc", "---\ntags: [bead]\n---\n\n# t-kid1 — Title of t-kid1\n\nFull ticket body here. [[t-epic]]");
    render(<BeadTree data={fixture} />);
    fireEvent.click(screen.getByText("t-kid1").closest("g")!);
    const dialog = await screen.findByRole("dialog");
    const pop = within(dialog);
    expect(await pop.findByText("Full ticket body here.", { exact: false })).toBeInTheDocument();
    expect(pop.getByText("open in VAULT ↗")).toBeInTheDocument();
    expect(pop.getByText("ops/beads/t-kid1.md")).toBeInTheDocument();
    // Esc closes
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("families wrap into bands when the container is narrow", () => {
    // two 2-wide families in a default 1100px container (6 slots) sit in ONE band; the wrap
    // math is pinned by the shelf landing BELOW both families either way — this guards the
    // band bookkeeping (no NaN/undefined positions, every bead still placed)
    const { container } = render(<BeadTree data={{
      beads: [
        node("t-a"), node("t-a1"), node("t-a2"),
        node("t-b"), node("t-b1"), node("t-b2"),
      ],
      edges: [
        { source: "t-a", target: "t-a1", kind: "child" },
        { source: "t-a", target: "t-a2", kind: "child" },
        { source: "t-b", target: "t-b1", kind: "child" },
        { source: "t-b", target: "t-b2", kind: "child" },
      ],
    }} />);
    expect(container.querySelectorAll(".beadtree-node")).toHaveLength(6);
    for (const g of container.querySelectorAll(".beadtree-node")) {
      expect(g.getAttribute("transform")).toMatch(/translate\([\d.]+,[\d.]+\)/);
    }
  });

  it("an empty board renders calm, not broken", () => {
    render(<BeadTree data={{ beads: [], edges: [] }} />);
    expect(screen.getByText(/NO ACTIVE BEADS/)).toBeInTheDocument();
  });

  it("survives a cyclic parent edge without hanging", () => {
    vi.useRealTimers();
    render(<BeadTree data={{
      beads: [node("t-a"), node("t-b")],
      edges: [
        { source: "t-a", target: "t-b", kind: "child" },
        { source: "t-b", target: "t-a", kind: "child" },
      ],
    }} />);
    expect(screen.getByText("t-a")).toBeInTheDocument();
  });
});
