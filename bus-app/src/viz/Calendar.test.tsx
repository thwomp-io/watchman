// The interactive calendar twin: month roster from the window bounds (empty months
// render — a quiet month is information), item days carry kind dots + the centerpiece ring,
// hover shows the glance card, click opens the full-day popup with the purchase link.
// Fictional fixture — shipped fixtures never carry real personal dates (the release scan's spirit).
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Calendar from "./Calendar";

const fixture = {
  title: "Calendar — almanac + ticketed events",
  subtitle: "Rivertown · 2025-05-06 → 2025-06-27",
  from: "2025-05-06",
  to: "2025-06-27",
  days: [
    {
      date: "2025-05-09",
      items: [
        { label: "Otters vs Herons", kind: "sports", segment: "Sports", tier: "centerpiece",
          venue: "Rivertown Field", time: "19:00", url: "https://tickets.example/otters", source: "ticketmaster" },
        { label: "Harvest Day — Sat–Mon", kind: "holiday", segment: "Holiday", tier: "perk",
          venue: "", time: null, url: "", source: "reference" },
      ],
    },
  ],
  months: [],
};

describe("Calendar", () => {
  it("renders every month the window touches, empty ones included", () => {
    render(<Calendar data={fixture} />);
    expect(screen.getByText("May 2025")).toBeInTheDocument();
    expect(screen.getByText("June 2025")).toBeInTheDocument(); // quiet month still renders
  });

  it("marks the item day (dots + centerpiece ring) and opens the full-day popup on click", () => {
    const { container } = render(<Calendar data={fixture} />);
    const day = container.querySelector('[data-tier="centerpiece"]');
    expect(day).not.toBeNull();
    fireEvent.click(day!);
    expect(screen.getByText("Otters vs Herons")).toBeInTheDocument();
    expect(screen.getByText(/Sports · Rivertown Field · 19:00/)).toBeInTheDocument();
    expect(screen.getByText("CENTERPIECE")).toBeInTheDocument();
    const link = screen.getByText("post ↗") as HTMLAnchorElement;
    expect(link.href).toContain("tickets.example");
    // almanac items say so
    expect(screen.getByText(/almanac/)).toBeInTheDocument();
  });

  it("shows the glance card on hover", () => {
    const { container } = render(<Calendar data={fixture} />);
    const day = container.querySelector(".cal-cell.has-items");
    fireEvent.mouseEnter(day!);
    expect(screen.getByText("2025-05-09")).toBeInTheDocument();
    expect(screen.getByText("Otters vs Herons")).toBeInTheDocument();
    fireEvent.mouseLeave(day!);
    expect(screen.queryByText("2025-05-09")).not.toBeInTheDocument();
  });

  it("degrades calm on a windowless payload", () => {
    render(<Calendar data={{ from: "", to: "", days: [] }} />);
    expect(screen.getByText(/NO CALENDAR WINDOW/)).toBeInTheDocument();
  });
});


describe("Calendar — the big board (variant=big)", () => {
  const big = { ...fixture, variant: "big" };

  it("renders one month with nav + labels IN the cells", () => {
    render(<Calendar data={big} />);
    // opens on today's month when in-roster, else the first — fixture window is May-Jun 2025,
    // so it clamps to September (findIndex miss → 0)
    expect(screen.getByText("May 2025")).toBeInTheDocument();
    expect(screen.queryByText("June 2025")).not.toBeInTheDocument(); // one month at a time
    expect(screen.getByText(/Otters vs Herons/)).toBeInTheDocument();   // label in the cell
    expect(screen.getByText("SUN")).toBeInTheDocument();                // full weekday header
  });

  it("navigates months and clamps at the window edges", () => {
    render(<Calendar data={big} />);
    const [prev, next] = [screen.getByText("◀"), screen.getByText("▶")];
    expect((prev as HTMLButtonElement).disabled).toBe(true);  // at the first month
    fireEvent.click(next);
    expect(screen.getByText("June 2025")).toBeInTheDocument();
    expect((screen.getByText("▶") as HTMLButtonElement).disabled).toBe(true); // at the last
    fireEvent.click(screen.getByText("TODAY"));
    expect(screen.getByText("May 2025")).toBeInTheDocument();
  });

  it("cell click opens the shared full-day popup", () => {
    const { container } = render(<Calendar data={big} />);
    fireEvent.click(container.querySelector('.calbig-cell[data-tier="centerpiece"]')!);
    expect(screen.getByText("CENTERPIECE")).toBeInTheDocument();
    expect((screen.getByText("post ↗") as HTMLAnchorElement).href).toContain("tickets.example");
  });
});
