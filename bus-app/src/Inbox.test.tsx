// The Inbox: watchman chips in the status bar (a red watchman is visible without opening anything;
// the popover carries the vitals), story rows, a catalyst opening as an article (publisher, Open
// original to the resolved link, mark-read on open), the filtered footer, j/k.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import Inbox from "./Inbox";
import { mockTauri } from "./test/mockTauri";
import type { BusEvent, WatchmenStatus } from "./types";

function ev(id: number, subject: string, title: string, payload: Record<string, unknown>,
            over: Partial<BusEvent> = {}): BusEvent {
  return {
    id, created_at: "2026-10-05T16:33:27+00:00", producer: "finance.pulse", lane: "finance", kind: "catalyst",
    subject, title: `${subject} — ${title}`, body: "", payload_json: JSON.stringify(payload), severity: "info",
    read_at: null, delivered_via: [], ...over,
  };
}

const EVENTS: BusEvent[] = [
  ev(1, "ACME", "Acme wins a contract", {
    url: "https://news.google.com/rss/articles/a", resolved_url: "https://sampledaily.example/acme",
    publisher: "Sample Daily", published: "2026-10-05 14:00", story: "acme wins a contract",
    summary: "The contract runs five years.",
  }),
  ev(2, "BOLT", "Acme wins a contract", { url: "u2", story: "acme wins a contract", published: "2026-10-05 14:00" }),
  ev(3, "ACME", "ACME Oct 2026 300.000 call", { url: "u3", story: "acme oct", filtered: "option-contract" }),
  ev(4, "ACME", "Second story", { url: "https://news.google.com/rss/articles/b", story: "second", published: "2026-10-05 13:00" }),
];

function wm(state: "green" | "red"): WatchmenStatus {
  return {
    as_of: "x", overall: state, agents: [{
      id: "pulse", label: "Finance pulse", state, healthy: state === "green", market_day: true, last_run: "x",
      last_run_rel: "8m ago", next_run: "10:33a Mon", runs_today: 4, expected_by_now: state === "red" ? 5 : 4,
      missed: state === "red" ? 1 : 0, cadence: [], last_flags: null,
    }],
  };
}

function stage(state: "green" | "red" = "green") {
  mockTauri.setValue("list_events", EVENTS);
  mockTauri.setValue("distinct_meta", { lanes: ["finance"], kinds: ["catalyst"] });
  mockTauri.set("run_surface", (a) => (a.id === "system.watchmen" ? JSON.stringify(wm(state)) : "{}"));
  mockTauri.setValue("ack_events", 1);
  mockTauri.setValue("article_card", { url: "x", image: "", error: "" });
}

describe("Inbox", () => {
  beforeEach(() => { mockTauri.reset(); });

  it("puts each watchman in the status bar; a degraded one is red at a glance", async () => {
    stage("red");
    render(<Inbox onUnread={() => {}} />);
    const chip = await screen.findByRole("button", { name: /Finance pulse/ });
    expect(chip).toHaveClass("wm-red");
    expect(chip).toHaveTextContent("1 MISSED");
    fireEvent.click(chip);
    expect(screen.getByRole("dialog", { name: /Finance pulse vitals/ })).toHaveTextContent("10:33a Mon");
    expect(document.querySelector("section.watchmen")).toBeNull(); // no standalone panel
  });

  it("collapses a story across tickers and hides filtered rows behind the footer", async () => {
    stage();
    render(<Inbox onUnread={() => {}} />);
    const row = (await screen.findAllByText("Acme wins a contract"))[0].closest("li")!;
    expect(row).toHaveTextContent("ACME");
    expect(row).toHaveTextContent("BOLT");
    expect(screen.queryByText("ACME Oct 2026 300.000 call")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /1 filtered/ }));
    expect(screen.getByText("ACME Oct 2026 300.000 call")).toBeInTheDocument();
  });

  it("opens a catalyst like an article and marks the whole story read", async () => {
    stage();
    render(<Inbox onUnread={() => {}} />);
    fireEvent.click((await screen.findAllByText("Acme wins a contract"))[0]);
    const open = await screen.findByRole("link", { name: /Open original/ });
    expect(open).toHaveAttribute("href", "https://sampledaily.example/acme");
    expect(screen.getByText("The contract runs five years.")).toBeInTheDocument();
    expect(screen.getAllByText("Sample Daily").length).toBeGreaterThan(0);
    await waitFor(() => expect(mockTauri.calls().some(([c, a]) => c === "ack_events"
      && JSON.stringify(a.ids) === JSON.stringify([1, 2]))).toBe(true));
  });

  it("labels an unresolved Google link and j/k walks the rows", async () => {
    stage();
    render(<Inbox onUnread={() => {}} />);
    await screen.findAllByText("Acme wins a contract");
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "j" });
    const open = await screen.findByRole("link", { name: /Open original/ });
    expect(open).toHaveAttribute("href", "https://news.google.com/rss/articles/b");
    expect(open).toHaveTextContent("via Google News");
  });
});
