// The interactive schedule twin's v2 pass: availability bands render behind the
// grid, the schedule-bank options panel renders as grouped chips, and both item blocks and bank
// chips carry the glance tip + the linked full record. Fictional fixture — shipped fixtures
// never carry real trips.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Schedule from "./Schedule";

const fixture = {
  title: "Rivertown weekend",
  start: "2025-05-09",
  end: "2025-05-11",
  dayStart: "08:00",
  dayEnd: "22:00",
  availability: {
    groups: [
      { key: "coast", label: "coast base", color: "#fde68a" },
      { key: "city", label: "city base", color: "#bfdbfe" },
    ],
    weekday: [{ until: "15:00", group: "coast" }, { group: "city" }],
    weekend: [{ group: "city" }],
    // per-date override: Saturday is the base-flip day — day-of-week rules can't express it
    dates: { "2025-05-10": [{ until: "12:00", group: "coast" }, { group: "city" }] },
  },
  markers: [{ date: "2025-05-10", time: "9a", label: "harbor market" }],
  items: [
    {
      date: "2025-05-09", start: "18:30", end: "20:30", label: "Herons Nest dinner", lane: "dining",
      note: "window table held", links: [{ label: "Yelp", url: "https://yelp.example/herons-nest" }],
    },
  ],
  bankTitle: "Idea bank",
  bank: [
    {
      group: "Coast", lane: "explore", label: "Lighthouse walk", note: "sunset slot",
      links: [{ label: "trail map", url: "https://trails.example/lighthouse" }],
    },
    { group: "City", lane: "dining", label: "Pasta Cellar", note: "the casual alt" },
  ],
};

describe("Schedule v2", () => {
  it("renders day columns, the marker rail, and availability bands + legend", () => {
    const { container } = render(<Schedule data={fixture} />);
    expect(screen.getByText(/harbor market/)).toBeInTheDocument();
    // Fri (weekday) 2 segments · Sat (per-date OVERRIDE — the base-flip day) 2 · Sun (weekend) 1 → 5
    const bands = container.querySelectorAll('rect[fill-opacity="0.13"]');
    expect(bands.length).toBe(5);
    expect(screen.getByText("coast base")).toBeInTheDocument();
  });

  it("renders the schedule-bank options panel as grouped chips", () => {
    render(<Schedule data={fixture} />);
    expect(screen.getByText("Idea bank")).toBeInTheDocument();
    expect(screen.getByText("Coast")).toBeInTheDocument();
    expect(screen.getByText("Lighthouse walk")).toBeInTheDocument();
  });

  it("item hover shows the glance tip with note + source link", () => {
    const { container } = render(<Schedule data={fixture} />);
    fireEvent.mouseMove(container.querySelector(".sched-item")!);
    const tip = container.querySelector(".viz-tip")!;
    expect(tip.textContent).toContain("window table held");
    expect((tip.querySelector("a.viz-tip-link") as HTMLAnchorElement).href)
      .toBe("https://yelp.example/herons-nest");
  });

  it("bank chip click opens the full record with its links row", () => {
    const { container } = render(<Schedule data={fixture} />);
    fireEvent.click(screen.getByText("Lighthouse walk").closest("button")!);
    const detail = container.querySelector(".viz-detail")!;
    expect(detail.textContent).toContain("sunset slot");
    const a = detail.querySelector(".viz-detail-links a") as HTMLAnchorElement;
    expect(a.href).toBe("https://trails.example/lighthouse");
    expect(a.getAttribute("target")).toBe("_blank");
  });
});
