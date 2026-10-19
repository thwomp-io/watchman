// VIZ ▸ travel · LIVE: a visit schedule entry renders the Visits tab's themed day-by-day component
// (ScheduleBoard) from the built payload — not the static D3 twin. Synthetic payload.

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import VizZone from "./VizZone";

const { PAYLOAD } = vi.hoisted(() => ({ PAYLOAD: {
  visit: "2030-05-01-sample", title: "Spring visit", subtitle: "", doc: "travel/visits/x/x.md",
  start: "2030-05-01", end: "2030-05-01", day_start: "08:00", day_end: "22:00", evening_start: null,
  availability_groups: [], marker_label: "", marker_cats: [], weather_place: "", weather_note: "",
  precip_unit: "inch", temp_unit: "°F",
  days: [{ date: "2030-05-01", weekday: "Wed", availability: [], markers: [], weather: null,
           items: [{ date: "2030-05-01", start: "10:00", end: "12:00", label: "Museum morning",
                     lane: "history", note: null, penciled: false, bank_id: null, bank_match: null }] }],
  bank: { visit: "v", title: "t", doc: "d", start: null, end: null, outings_title: "", restaurants_title: "",
          notes: [], groups: [], items: [], counts: {}, generated: "" },
} }));

vi.mock("../api", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return {
    ...mod,
    listViz: vi.fn().mockResolvedValue([{
      path: "live:schedule/visit/2030-05-01-sample", doc: "travel · LIVE", name: "Spring visit",
      viz_type: "visit-schedule", title: "Spring visit", supported: true,
    }]),
    readViz: vi.fn().mockResolvedValue(JSON.stringify(PAYLOAD)),
  };
});
vi.mock("../nav", () => ({ useNav: () => ({ navigate: vi.fn(), report: vi.fn(), current: { zone: "viz" } }) }));

describe("VIZ visit schedule", () => {
  it("renders the themed schedule component for a LIVE visit entry", async () => {
    const { container } = render(<VizZone />);
    await waitFor(() => expect(container.querySelector(".viz-visit-schedule .vsched-grid")).not.toBeNull());
    expect(screen.getAllByText("Museum morning").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Spring visit").length).toBeGreaterThan(0);
  });
});
