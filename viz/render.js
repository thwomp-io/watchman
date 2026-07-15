// Server-side D3 → static SVG renderer for the toolkit.
// Python shells out:  node render.js <type> <data.json> <out.svg>
// Output is a standalone SVG written to {doc}/visuals/, embedded in the Obsidian corpus.
//
// Diagram types: `timeline` (date-window day-grid), `schedule`/`schedule-bank` (day×time planner),
// `food-bank` (grouped restaurant-card bank — the dining sibling to schedule-bank),
// `radial` (drive-time map), `compare` (radar), `matrix` (destination-characteristics heatmap),
// `weather-strip` (daily hi/lo + precip), `map` (d3-geo: destinations pinned + great-circle arcs
// from a home origin), `rank-bar` (component-stacked ranking), `calendar` (12-month year grid +
// almanac items). reach/scores are curated, NOT computed/routed.
// Add a renderer to RENDERERS to add a type.

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { JSDOM } from "jsdom";
import * as d3 from "d3";
import { feature } from "topojson-client";
import { sankey as d3sankey, sankeyLinkHorizontal, sankeyJustify } from "d3-sankey";

const [,, type, dataPath, outPath, themeName = "light"] = process.argv;
if (!type || !dataPath || !outPath) {
  console.error("usage: node render.js <type> <data.json> <out.svg> [theme]");
  process.exit(2);
}

// ————— design tokens ————————————————————————————————————————————————————————
// `light` keeps the original Apple-light values EXACTLY (default output is byte-stable).
// `instrument` mirrors bus-app/src/App.css — the watchman's-console palette, so vault SVGs and
// the app's interactive viz read as one design system. Tokens, not literals: themes are data.
const THEMES = {
  light: {
    font: "ui-sans-serif, -apple-system, 'Segoe UI', Roboto, sans-serif",
    bg: "#fbfbfd", panel: "#ffffff", panelAlt: "#f2f2f7", cellWeekend: "#eef2ff",
    ink: "#1d1d1f", inkDim: "#6e6e73", inkFaint: "#8e8e93", inkStrong: "#3a3a3c",
    stroke: "#e5e5ea", strokeDim: "#d2d2d7",
    knockout: "#ffffff",      // text on saturated fills
    halo: "#ffffff",          // text/marker halos over imagery & maps
    tintTarget: "#ffffff",    // interpolateRgb target for pastel tints (≈ paper)
    tileMode: "solid",        // treemap tiles: solid color blocks (the original look)
    ribbonOpacity: 0.38,      // sankey link stroke-opacity
    linkLabelHalo: "#f5f5f7", // sankey value-label halo (readability over crossing ribbons)
    categorical: d3.schemeTableau10,
  },
  // `noir` — the PUBLIC / hero-diagram theme (higher polish bar than the internal SVGs): near-black
  // canvas + a bright neon categorical palette for high-contrast "glowing block" components. Tuned for
  // README architecture diagrams that must read on both GitHub light- and dark-mode (the SVG carries its
  // own black bg). Renderers that support glow/gradient (e.g. `flow`) light up under this theme.
  noir: {
    font: "ui-sans-serif, -apple-system, 'Segoe UI', Roboto, sans-serif",
    bg: "#08090c", panel: "#10131a", panelAlt: "#161a23", cellWeekend: "#12161f",
    ink: "#f4f6fb", inkDim: "#aab2c2", inkFaint: "#6b7382", inkStrong: "#ffffff",
    stroke: "#272d39", strokeDim: "#323a48",
    knockout: "#08090c",
    halo: "#08090c",
    tintTarget: "#08090c",
    tileMode: "phosphor",
    ribbonOpacity: 0.55,
    linkLabelHalo: "#08090c",
    glow: true,               // renderers may add outer-glow filters under this theme
    categorical: ["#37e0d8", "#7c8cff", "#9bff6a", "#ffd13d", "#ff6fa5", "#5ad1ff",
                  "#c08bff", "#ffa14a", "#74f0c2", "#ff8a7a"],
  },
  instrument: {
    font: "ui-monospace, 'SF Mono', Menlo, monospace",
    bg: "#0b0d0e", panel: "#121517", panelAlt: "#191d20", cellWeekend: "#161a21",
    ink: "#d7dcd9", inkDim: "#79827f", inkFaint: "#5a625f", inkStrong: "#d7dcd9",
    stroke: "#23282b", strokeDim: "#1d2225",
    knockout: "#0b0d0e",
    halo: "#0b0d0e",
    tintTarget: "#0b0d0e",    // tints sink toward the chassis instead of paper
    tileMode: "phosphor",     // translucent fill + colored stroke — the bus-app tile idiom
    ribbonOpacity: 0.5,       // dimmed color over dark mud below this
    linkLabelHalo: "#0b0d0e",
    categorical: ["#e8a33d", "#59b9ff", "#7dd6a0", "#ff8a7a", "#b48ead", "#6fc7c0",
                  "#d98fb6", "#c9d65e", "#ffb454", "#8fbcbb"],
  },
};
const T = THEMES[themeName];
if (!T) {
  console.error(`unknown theme '${themeName}' (known: ${Object.keys(THEMES).join(", ")})`);
  process.exit(2);
}

const data = JSON.parse(readFileSync(dataPath, "utf8"));
const RENDERERS = {
  timeline: renderTimeline,
  schedule: renderSchedule,
  "schedule-bank": renderScheduleBank,
  "food-bank": renderFoodBank,
  radial: renderRadial,
  compare: renderCompare,
  matrix: renderMatrix,
  "weather-strip": renderWeatherStrip,
  map: renderMap,
  "rank-bar": renderRankBar,
  calendar: renderCalendar,
  pie: renderPie,
  treemap: renderTreemap,
  sankey: renderSankey,
  flow: renderFlow,
  line: renderLine,
  scatter: renderScatter,
  "map-annotate": renderMapAnnotate,
};
const render = RENDERERS[type];
if (!render) {
  console.error(`unknown type '${type}'. known: ${Object.keys(RENDERERS).join(", ")}`);
  process.exit(2);
}

const dom = new JSDOM("<!DOCTYPE html><body></body>");
const body = d3.select(dom.window.document.body);
render(body, data);

const svgNode = body.select("svg").node();
const xml = `<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n${svgNode.outerHTML}\n`;
mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, xml);
console.error(`wrote ${outPath} (${xml.length} bytes, ${type})`);

// function declarations (hoisted — safe to call from the top-level render() above)
function parseDay(s) {
  return new Date(`${s}T12:00:00`); // noon-local: no TZ rollover
}
function isWeekend(dt) {
  return dt.getDay() === 0 || dt.getDay() === 6;
}
function truncate(s, n) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
// Group bank items into ordered {name, items} sections: by explicit `group` if any item carries one
// (first-seen order), else by lane in the schedule's lane order (color-stable). Bank-only.
function groupBank(bank, lanes) {
  if (!bank.length) return [];
  if (bank.some((b) => b.group)) {
    const order = [];
    const m = new Map();
    bank.forEach((b) => {
      const k = b.group || "Other";
      if (!m.has(k)) {
        m.set(k, []);
        order.push(k);
      }
      m.get(k).push(b);
    });
    return order.map((k) => ({ name: k, items: m.get(k) }));
  }
  return lanes
    .filter((l) => bank.some((b) => (b.lane || "plan") === l))
    .map((l) => ({ name: l, items: bank.filter((b) => (b.lane || "plan") === l) }));
}

function renderTimeline(root, d) {
  // Floor to local midnight FIRST: timeDay.range() ceils its start to the next day boundary, so a
  // noon-parsed start would silently drop day 0. parseDay uses noon to avoid TZ rollover; floor here.
  const start = d3.timeDay.floor(parseDay(d.start));
  const end = d3.timeDay.floor(parseDay(d.end));
  const days = d3.timeDay.range(start, d3.timeDay.offset(end, 1)); // inclusive of `end`
  const items = d.items || [];

  const keyf = d3.timeFormat("%Y-%m-%d");
  const dayIndex = new Map(days.map((dt, i) => [keyf(dt), i]));
  const byDay = new Map(days.map((_, i) => [i, []]));
  for (const it of items) {
    const i = dayIndex.get(it.date);
    if (i !== undefined) byDay.get(i).push(it);
  }
  const maxItems = d3.max([1, ...Array.from(byDay.values(), (a) => a.length)]);

  const M = { top: 66, right: 20, bottom: 44, left: 20 };
  const colW = 150, headerH = 46, chipH = 26, chipGap = 6;
  const bodyH = maxItems * (chipH + chipGap) + chipGap;
  const width = M.left + M.right + days.length * colW;
  const height = M.top + headerH + bodyH + M.bottom;

  const lanes = Array.from(new Set(items.map((i) => i.lane || "event")));
  const color = d3.scaleOrdinal().domain(lanes).range(T.categorical);

  const svg = root
    .append("svg")
    .attr("xmlns", "http://www.w3.org/2000/svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("width", width)
    .attr("height", height)
    .attr("font-family", T.font);

  svg.append("rect").attr("width", width).attr("height", height).attr("fill", T.bg);

  svg.append("text").attr("x", M.left).attr("y", 28)
    .attr("font-size", 20).attr("font-weight", 700).attr("fill", T.ink)
    .text(d.title || "Timeline");
  if (d.subtitle) {
    svg.append("text").attr("x", M.left).attr("y", 49)
      .attr("font-size", 13).attr("fill", T.inkDim).text(d.subtitle);
  }

  const wkday = d3.timeFormat("%a");
  const datef = d3.timeFormat("%b %-d");

  const cols = svg.selectAll("g.day").data(days).join("g")
    .attr("class", "day")
    .attr("transform", (_, i) => `translate(${M.left + i * colW},${M.top})`);

  cols.append("rect")
    .attr("width", colW - 4).attr("height", headerH + bodyH).attr("rx", 8)
    .attr("fill", (dt) => (isWeekend(dt) ? T.cellWeekend : T.panel))
    .attr("stroke", T.stroke);

  cols.append("text").attr("x", (colW - 4) / 2).attr("y", 20).attr("text-anchor", "middle")
    .attr("font-size", 13).attr("font-weight", 600)
    .attr("fill", (dt) => (isWeekend(dt) ? "#3730a3" : T.ink))
    .text((dt) => wkday(dt));
  cols.append("text").attr("x", (colW - 4) / 2).attr("y", 38).attr("text-anchor", "middle")
    .attr("font-size", 12).attr("fill", T.inkDim).text((dt) => datef(dt));

  cols.each(function (_, i) {
    const g = d3.select(this);
    byDay.get(i).forEach((it, k) => {
      const y = headerH + chipGap + k * (chipH + chipGap);
      const chip = g.append("g").attr("transform", `translate(6,${y})`);
      chip.append("rect").attr("width", colW - 16).attr("height", chipH).attr("rx", 6)
        .attr("fill", color(it.lane || "event")).attr("fill-opacity", 0.16)
        .attr("stroke", color(it.lane || "event")).attr("stroke-opacity", 0.55);
      chip.append("text").attr("x", 8).attr("y", chipH / 2 + 4).attr("font-size", 11)
        .attr("fill", T.ink)
        .text(`${it.time ? it.time + "  " : ""}${truncate(it.label, 20)}`);
    });
  });

  const lg = svg.append("g").attr("transform", `translate(${M.left},${height - 14})`);
  let x = 0;
  for (const l of lanes) {
    const g = lg.append("g").attr("transform", `translate(${x},0)`);
    g.append("rect").attr("width", 11).attr("height", 11).attr("y", -10).attr("rx", 2).attr("fill", color(l));
    g.append("text").attr("x", 16).attr("y", -1).attr("font-size", 11).attr("fill", T.inkDim).text(l);
    x += 16 + l.length * 7 + 18;
  }
}

function fmtClock(hhmm) {
  const [h, m] = String(hhmm).split(":").map(Number);
  const ap = h < 12 ? "a" : "p";
  const h12 = h % 12 || 12;
  return m ? `${h12}:${String(m).padStart(2, "0")}${ap}` : `${h12}${ap}`;
}

// Availability bands are DATA-DRIVEN so the schedule family is reusable for trips, not just one visit.
// `d.availability` = { groups:[{key,label,color}], weekday:[{until?,group}], weekend:[{until?,group}] }.
// Each segment runs from the prior boundary (or dayStart) to its `until` (or dayEnd). Omitted →
// the default weekday/weekend availability pattern (weekday = limited-until-evening, then full; weekend = all-day).
function buildAvail(d) {
  const a = d.availability;
  if (a && Array.isArray(a.groups) && a.groups.length) {
    const byKey = new Map(a.groups.map((g) => [g.key, g.color]));
    const fallback = a.groups[0].key;
    return {
      groups: a.groups,
      weekday: a.weekday && a.weekday.length ? a.weekday : [{ group: fallback }],
      weekend: a.weekend && a.weekend.length ? a.weekend : [{ group: fallback }],
      colorOf: (k) => byKey.get(k) || T.strokeDim,
    };
  }
  const eve = d.eveningStart || "17:30";
  const dflt = { all: "#86efac", solo: "#c7d2fe" }; // everyone-available green · partial indigo
  return {
    groups: [
      { key: "all", label: "everyone available", color: dflt.all },
      { key: "solo", label: "partial availability", color: dflt.solo },
    ],
    weekday: [{ until: eve, group: "solo" }, { group: "all" }],
    weekend: [{ group: "all" }],
    colorOf: (k) => dflt[k] || T.strokeDim,
  };
}

// Shared layout for the schedule family: parses days/times, lanes+color scale (item lanes first so
// their colors stay stable; bank lanes appended when `withBank`), data-driven availability, adaptive
// text caps, and — when a bank is present — the options-panel geometry + total height. Both the
// compact `schedule` and the expanded `schedule-bank` build off this one object.
function scheduleLayout(d, withBank) {
  const start = d3.timeDay.floor(parseDay(d.start));
  const end = d3.timeDay.floor(parseDay(d.end));
  const days = d3.timeDay.range(start, d3.timeDay.offset(end, 1));
  const items = d.items || [];
  const bank = withBank ? d.bank || [] : [];

  const toMin = (s) => {
    const [h, m] = String(s).split(":").map(Number);
    return h * 60 + (m || 0);
  };
  const dayStartMin = toMin(d.dayStart || "08:00");
  const dayEndMin = toMin(d.dayEnd || "23:00");

  const M = { top: 72, right: 22, bottom: 56, left: 50 };
  // Day columns auto-widen on 1-2 day plans: the canvas floors at 460 anyway, so a lone 158px column
  // wastes the floor while its labels/notes ellipsize into empty gutter — the truncation-into-
  // whitespace bug (viz doctrine rule 1). Multi-day grids keep the compact 158.
  const colW = Math.max(158, Math.min(380, Math.floor((460 - 50 - 22) / days.length)));
  const headerH = 46, pxPerMin = 0.72;
  const colsRight = M.left + days.length * colW; // right edge of the day columns
  // Floor the canvas so the left-anchored title/subtitle/legend don't clip on 1–2 day plans (a single
  // 158px column can't hold the legend). No-op for multi-day grids (already wider). Gridlines clip to
  // colsRight, not `width`, so they don't trail into the empty right gutter on narrow plans.
  const width = Math.max(460, colsRight + M.right);

  const keyf = d3.timeFormat("%Y-%m-%d");
  const dayIndex = new Map(days.map((dt, i) => [keyf(dt), i]));
  const colX = (i) => M.left + i * colW;

  // Ambient per-day "what's on" markers (e.g. a notable event on a given day): an AWARENESS rail between the
  // day header and the plot — deliberately NOT committed schedule blocks. railH shifts the grid down to
  // make room; absent → railH 0 (unchanged layout). Data: markers:[{date,label,time?}], markerLabel?.
  const markers = d.markers || [];
  const markersByDate = d3.group(markers, (m) => m.date);
  const markerRowH = 17;
  const maxMarkers = d3.max([0, ...Array.from(markersByDate.values(), (a) => a.length)]) || 0;
  const railH = markers.length ? maxMarkers * markerRowH + 6 : 0;
  const railTop = M.top + headerH;
  const plotTop = railTop + railH;
  const plotH = (dayEndMin - dayStartMin) * pxPerMin;
  const yAt = (min) => plotTop + (min - dayStartMin) * pxPerMin;

  const lanes = Array.from(
    new Set([...items.map((i) => i.lane || "plan"), ...bank.map((b) => b.lane || "plan")]),
  );
  const color = d3.scaleOrdinal().domain(lanes).range(T.categorical);
  const avail = buildAvail(d);

  // Adaptive text caps derived from column width (supersedes the old fixed 17/22 — roomier when wide).
  const labelMax = Math.max(12, Math.floor((colW - 38) / 6));
  const noteMax = Math.max(10, Math.floor((colW - 26) / 5.4));

  const gridBottom = plotTop + plotH + M.bottom; // legend sits 16px inside this band
  const legendY = gridBottom - 16;

  const bankGroups = groupBank(bank, lanes);
  const bankGeom = { titleBand: 32, header: 22, rowH: 18, pad: 18 };
  const maxBankRows = d3.max([0, ...bankGroups.map((g) => g.items.length)]) || 0;
  const bankTop = gridBottom + 10;
  const bankH = bank.length
    ? bankGeom.titleBand + bankGeom.header + maxBankRows * bankGeom.rowH + bankGeom.pad
    : 0;
  const height = bank.length ? bankTop + bankH : gridBottom;

  return {
    days, items, bank, toMin, dayStartMin, dayEndMin, M, colW, plotTop, plotH, width, colsRight,
    keyf, dayIndex, colX, yAt, lanes, color, avail, labelMax, noteMax,
    markers, markersByDate, railTop, markerRowH,
    gridBottom, legendY, bankGroups, bankGeom, bankTop, height,
  };
}

function makeSvg(root, width, height) {
  const svg = root
    .append("svg")
    .attr("xmlns", "http://www.w3.org/2000/svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("width", width)
    .attr("height", height)
    .attr("font-family", T.font);
  svg.append("rect").attr("width", width).attr("height", height).attr("fill", T.bg);
  return svg;
}

// Paint the day×time grid (title, day columns, data-driven availability bands, hour gridlines, item
// blocks, legend) into a pre-sized svg. Shared by `schedule` + `schedule-bank`.
function paintGrid(svg, d, L) {
  const { days, items, M, colW, plotTop, plotH, width, colsRight, dayStartMin, dayEndMin, colX, yAt,
    dayIndex, toMin, color, avail, labelMax, noteMax, legendY, lanes, keyf, markers, markersByDate,
    railTop, markerRowH } = L;

  // Marker categories: each marker may carry a `cat` key mapped via top-level `markerCats`
  // ({cat: {label, color}}) to its own chip color — lets one awareness rail carry multiple
  // categories (e.g. two different event series) distinguishably. Back-compat: no `cat`/
  // `markerCats` → the single amber `markerLabel` rail (unchanged).
  const markerCats = d.markerCats || {};
  const dfltCat = { label: d.markerLabel || "what's on (on TV / in town)", color: "#f59e0b" };
  const catOf = (mk) => (mk.cat && markerCats[mk.cat]) || dfltCat;
  const tintFill = (c) => d3.interpolateRgb(c, T.tintTarget)(0.9);
  const shadeText = (c) => d3.interpolateRgb(c, "#000000")(0.45);

  svg.append("text").attr("x", M.left).attr("y", 28).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Schedule");
  if (d.subtitle)
    svg.append("text").attr("x", M.left).attr("y", 49).attr("font-size", 13).attr("fill", T.inkDim)
      .text(d.subtitle);

  const wkday = d3.timeFormat("%a");
  const datef = d3.timeFormat("%b %-d");

  days.forEach((dt, i) => {
    const x = colX(i);
    const weekend = isWeekend(dt);
    svg.append("rect").attr("x", x + 2).attr("y", plotTop).attr("width", colW - 6)
      .attr("height", plotH).attr("fill", T.panel).attr("stroke", T.stroke).attr("rx", 6);
    let segStart = dayStartMin;
    (weekend ? avail.weekend : avail.weekday).forEach((seg) => {
      const segEnd = seg.until ? toMin(seg.until) : dayEndMin;
      svg.append("rect").attr("x", x + 3).attr("y", yAt(segStart)).attr("width", colW - 8)
        .attr("height", yAt(segEnd) - yAt(segStart)).attr("fill", avail.colorOf(seg.group))
        .attr("fill-opacity", 0.4);
      segStart = segEnd;
    });
    svg.append("text").attr("x", x + (colW - 4) / 2).attr("y", M.top + 18).attr("text-anchor", "middle")
      .attr("font-size", 13).attr("font-weight", 600)
      .attr("fill", weekend ? "#15803d" : T.ink).text(wkday(dt));
    svg.append("text").attr("x", x + (colW - 4) / 2).attr("y", M.top + 36).attr("text-anchor", "middle")
      .attr("font-size", 12).attr("fill", T.inkDim).text(datef(dt));
    (markersByDate.get(keyf(dt)) || []).forEach((mk, k) => {
      const my = railTop + 2 + k * markerRowH;
      const cw = colW - 10;
      const cat = catOf(mk);
      const gm = svg.append("g").attr("transform", `translate(${x + 5},${my})`);
      gm.append("rect").attr("width", cw).attr("height", markerRowH - 3).attr("rx", 4)
        .attr("fill", tintFill(cat.color)).attr("stroke", cat.color).attr("stroke-opacity", 0.8);
      gm.append("text").attr("x", 7).attr("y", markerRowH - 7).attr("font-size", 9.5).attr("fill", shadeText(cat.color))
        .text(truncate(`${mk.time ? mk.time + " " : ""}${mk.label}`, Math.floor((cw - 12) / 5.3)));
    });
  });

  for (let m = dayStartMin; m <= dayEndMin; m += 60) {
    const yy = yAt(m);
    svg.append("line").attr("x1", M.left).attr("x2", colsRight).attr("y1", yy).attr("y2", yy)
      .attr("stroke", T.stroke).attr("stroke-opacity", 0.7);
    const h = m / 60;
    svg.append("text").attr("x", M.left - 6).attr("y", yy + 4).attr("text-anchor", "end")
      .attr("font-size", 10).attr("fill", T.inkFaint).text(`${h % 12 || 12}${h < 12 ? "a" : "p"}`);
  }

  items.forEach((it) => {
    const i = dayIndex.get(it.date);
    if (i === undefined) return;
    const s = toMin(it.start || "12:00");
    const e = it.end ? toMin(it.end) : s + 120;
    const top = yAt(s);
    const h = Math.max(26, yAt(e) - yAt(s));
    const c = color(it.lane || "plan");
    const g = svg.append("g").attr("transform", `translate(${colX(i) + 6},${top})`);
    g.append("rect").attr("width", colW - 18).attr("height", h).attr("rx", 6)
      .attr("fill", c).attr("fill-opacity", 0.18).attr("stroke", c).attr("stroke-opacity", 0.6);
    g.append("rect").attr("width", 4).attr("height", h).attr("rx", 2).attr("fill", c);
    g.append("text").attr("x", 11).attr("y", 15).attr("font-size", 11).attr("font-weight", 600)
      .attr("fill", T.ink)
      .text(`${it.start ? fmtClock(it.start) + " " : ""}${truncate(it.label, labelMax)}`);
    if (it.note && h >= 38)
      g.append("text").attr("x", 11).attr("y", 29).attr("font-size", 9.5).attr("fill", T.inkDim)
        .text(truncate(it.note, noteMax));
  });

  const lg = svg.append("g").attr("transform", `translate(${M.left},${legendY})`);
  let lx = 0;
  const chip = (fill, label, op) => {
    const g = lg.append("g").attr("transform", `translate(${lx},0)`);
    g.append("rect").attr("width", 11).attr("height", 11).attr("y", -10).attr("rx", 2)
      .attr("fill", fill).attr("fill-opacity", op ?? 1);
    g.append("text").attr("x", 16).attr("y", -1).attr("font-size", 11).attr("fill", T.inkDim).text(label);
    lx += 16 + label.length * 6.6 + 18;
  };
  avail.groups.forEach((grp) => chip(grp.color, grp.label, 0.7));
  lanes.forEach((l) => chip(color(l), l));
  if (markers.length) {
    const catKeys = Object.keys(markerCats);
    if (catKeys.length) {
      const present = new Set(markers.map((m) => (m.cat && markerCats[m.cat] ? m.cat : "_dflt")));
      catKeys.forEach((k) => { if (present.has(k)) chip(markerCats[k].color, markerCats[k].label, 0.7); });
      if (present.has("_dflt")) chip(dfltCat.color, dfltCat.label, 0.7);
    } else {
      chip(dfltCat.color, dfltCat.label, 0.7);
    }
  }
}

// Paint the options-bank panel below the grid: the unscheduled swap-ins, grouped (by explicit `group`,
// else lane) into columns, each item a lane-colored dot + label (+ dim note). Color matches the grid.
function paintBank(svg, d, L) {
  const { bank, bankGroups, bankGeom, bankTop, color, M, width } = L;
  if (!bank.length) return;
  const plotLeft = M.left;
  const plotW = width - M.left - M.right;
  svg.append("line").attr("x1", plotLeft).attr("x2", plotLeft + plotW).attr("y1", bankTop)
    .attr("y2", bankTop).attr("stroke", T.strokeDim);
  svg.append("text").attr("x", plotLeft).attr("y", bankTop + 21).attr("font-size", 13)
    .attr("font-weight", 700).attr("fill", T.ink)
    .text(d.bankTitle || "Idea bank — swap-ins (not yet scheduled)");
  const gColW = plotW / bankGroups.length;
  const groupsTop = bankTop + bankGeom.titleBand;
  const labelMax = Math.max(8, Math.floor((gColW - 28) / 6.2));
  bankGroups.forEach((grp, gi) => {
    const cx = plotLeft + gi * gColW;
    svg.append("text").attr("x", cx).attr("y", groupsTop).attr("font-size", 11.5)
      .attr("font-weight", 600).attr("fill", T.inkStrong).text(`${grp.name} (${grp.items.length})`);
    grp.items.forEach((it, r) => {
      const y = groupsTop + bankGeom.header + r * bankGeom.rowH;
      const c = color(it.lane || "plan");
      svg.append("rect").attr("x", cx).attr("y", y - 8).attr("width", 9).attr("height", 9)
        .attr("rx", 2).attr("fill", c);
      const t = svg.append("text").attr("x", cx + 15).attr("y", y).attr("font-size", 11);
      t.append("tspan").attr("fill", T.ink).text(truncate(it.label, labelMax));
      if (it.note) {
        const noteMax = Math.floor((gColW - 28 - it.label.length * 6.0) / 5.4);
        if (noteMax >= 6)
          t.append("tspan").attr("fill", T.inkFaint).attr("font-size", 10)
            .text(`  ·  ${truncate(it.note, noteMax)}`);
      }
    });
  });
}

// `schedule` — the COMPACT planner: day columns × time-of-day axis, data-driven availability bands,
// items as time-positioned blocks, and an optional ambient "what's on" marker rail above the grid
// (awareness, not commitments — see scheduleLayout). Data: {title,subtitle,start,end,dayStart,dayEnd,
// eveningStart, availability?, markers?:[{date,label,time?}], markerLabel?,
// items:[{date,start,end?,label,lane,note?}]}.
function renderSchedule(root, d) {
  const L = scheduleLayout(d, false);
  paintGrid(makeSvg(root, L.width, L.height), d, L);
}

// `schedule-bank` — the EXPANDED planner: the same grid PLUS an options-bank panel below it listing
// the unscheduled swap-ins the schedule is picking from (grouped + color-dotted by lane to MATCH the
// grid). Reads the same data + {bankTitle?, bank:[{label,lane,note?,group?}]}; renders alongside
// `schedule` in a doc's visuals/.
function renderScheduleBank(root, d) {
  const L = scheduleLayout(d, true);
  const svg = makeSvg(root, L.width, L.height);
  paintGrid(svg, d, L);
  paintBank(svg, d, L);
}

// word-wrap `s` into at most `maxLines` lines of ~`maxChars` each; ellipsize the last line if content
// overflows. Used for the rich note text on food-bank cards.
function wrapLines(s, maxChars, maxLines) {
  const words = String(s || "").split(/\s+/).filter(Boolean);
  const lines = [];
  let cur = "";
  let idx = 0;
  while (idx < words.length && lines.length < maxLines) {
    const w = words[idx];
    const cand = cur ? `${cur} ${w}` : w;
    if (cand.length <= maxChars || !cur) {
      cur = cand.length <= maxChars ? cand : truncate(w, maxChars); // force a too-long word onto an empty line
      idx++;
    } else {
      lines.push(cur);
      cur = "";
    }
  }
  if (cur && lines.length < maxLines) cur = (lines.push(cur), "");
  if (idx < words.length && lines.length) lines[lines.length - 1] = truncate(`${lines[lines.length - 1]} …`, maxChars);
  return lines;
}

// `food-bank` — the restaurant companion to `schedule-bank`: a grouped card-bank of every dining option
// gathered for a visit/trip (+ extrapolations), sectioned by role/meal. Each card carries the spot's
// name, a meta line (area · cuisine · price), a fit/why line (2-line wrap), a meal tag, and a STATUS-
// colored left-accent + dot (booked / banked / new — what's locked vs still a candidate, the same
// "what's placed + what I'm choosing from" read the schedule-bank gives). Consumes restaurants.json
// directly: {title,subtitle, groups:[{key,label}], restaurants:[{name,group,area,cuisine,meals:[],
// price,hostfit?,note?,status?}]}.
function renderFoodBank(root, d) {
  // Status dimension is data-driven: `statusCats` ({key:{color,label}}) overrides the default
  // booked/banked/new (the default semantics) so the same grouped-card engine serves any option
  // pool (e.g. a day-trip's ⭐top-pick / solid / casual). Cards key off `r.status`.
  const STATUS = d.statusCats || {
    booked: { color: "#16a34a", label: "on the schedule" },
    banked: { color: T.inkFaint, label: "banked earlier" },
    new: { color: "#0ea5e9", label: "new this pass" },
  };
  const statusKeys = Object.keys(STATUS);
  const allR = d.restaurants || [];
  const groups = (d.groups && d.groups.length)
    ? d.groups
    : Array.from(new Set(allR.map((r) => r.group || "Other"))).map((k) => ({ key: k, label: k }));
  const byGroup = new Map(groups.map((g) => [g.key, []]));
  const fallback = groups[0] ? groups[0].key : "Other";
  if (!byGroup.has(fallback)) byGroup.set(fallback, []);
  allR.forEach((r) => (byGroup.get(byGroup.has(r.group) ? r.group : fallback)).push(r));
  const sections = groups.filter((g) => (byGroup.get(g.key) || []).length);

  const cols = 3;
  const M = { top: 70, right: 22, bottom: 40, left: 22 };
  const width = 1020;
  const innerW = width - M.left - M.right;
  const gap = 14;
  const cardW = (innerW - (cols - 1) * gap) / cols;
  const cardH = 86, rowGap = 12, headerH = 32, sectionGap = 14;

  const groupColor = d3.scaleOrdinal().domain(groups.map((g) => g.key)).range(T.categorical);

  let height = M.top;
  sections.forEach((g) => {
    const rows = Math.ceil((byGroup.get(g.key) || []).length / cols);
    height += headerH + rows * cardH + (rows - 1) * rowGap + sectionGap;
  });
  height += M.bottom;

  const svg = makeSvg(root, width, height);
  svg.append("text").attr("x", M.left).attr("y", 28).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Restaurant bank");
  if (d.subtitle)
    svg.append("text").attr("x", M.left).attr("y", 49).attr("font-size", 12.5).attr("fill", T.inkDim)
      .text(truncate(d.subtitle, Math.floor(innerW / 6.4)));

  // status legend (top-right of the header band)
  const lg = svg.append("g").attr("transform", `translate(${M.left},${M.top - 12})`);
  let lx = 0;
  statusKeys.filter((k) => allR.some((r) => r.status === k)).forEach((k) => {
    const s = STATUS[k];
    const g = lg.append("g").attr("transform", `translate(${lx},0)`);
    g.append("circle").attr("r", 5).attr("cy", -4).attr("fill", s.color);
    g.append("text").attr("x", 11).attr("y", -1).attr("font-size", 11).attr("fill", T.inkDim).text(s.label);
    lx += 11 + s.label.length * 6.6 + 22;
  });

  let y = M.top + 14;
  sections.forEach((g) => {
    const items = byGroup.get(g.key) || [];
    const gc = groupColor(g.key);
    svg.append("rect").attr("x", M.left).attr("y", y).attr("width", innerW).attr("height", 23).attr("rx", 5)
      .attr("fill", d3.interpolateRgb(gc, T.tintTarget)(0.82));
    svg.append("rect").attr("x", M.left).attr("y", y).attr("width", 4).attr("height", 23).attr("rx", 2).attr("fill", gc);
    svg.append("text").attr("x", M.left + 13).attr("y", y + 16).attr("font-size", 12.5).attr("font-weight", 700)
      .attr("fill", d3.interpolateRgb(gc, "#000000")(0.55)).text(`${g.label}  (${items.length})`);
    y += headerH;

    items.forEach((r, i) => {
      const col = i % cols, row = Math.floor(i / cols);
      const cx = M.left + col * (cardW + gap);
      const cy = y + row * (cardH + rowGap);
      const st = STATUS[r.status] || STATUS[statusKeys[1]] || STATUS[statusKeys[0]] || { color: T.inkFaint, label: "" };
      svg.append("rect").attr("x", cx).attr("y", cy).attr("width", cardW).attr("height", cardH).attr("rx", 8)
        .attr("fill", T.panel).attr("stroke", T.stroke);
      svg.append("rect").attr("x", cx).attr("y", cy).attr("width", 4).attr("height", cardH).attr("rx", 2).attr("fill", st.color);
      svg.append("circle").attr("cx", cx + cardW - 13).attr("cy", cy + 14).attr("r", 4).attr("fill", st.color);

      svg.append("text").attr("x", cx + 13).attr("y", cy + 19).attr("font-size", 12).attr("font-weight", 700)
        .attr("fill", T.ink).text(truncate(r.name || "", Math.floor((cardW - 36) / 6.6)));
      const meta = [r.area, r.cuisine, r.price].filter(Boolean).join("  ·  ");
      svg.append("text").attr("x", cx + 13).attr("y", cy + 35).attr("font-size", 10).attr("fill", T.inkDim)
        .text(truncate(meta, Math.floor((cardW - 26) / 5.4)));
      wrapLines(r.hostfit || r.note || "", Math.floor((cardW - 26) / 5.2), 2).forEach((ln, li) =>
        svg.append("text").attr("x", cx + 13).attr("y", cy + 51 + li * 13).attr("font-size", 9.5)
          .attr("fill", T.inkFaint).text(ln));
      const meals = Array.isArray(r.meals) ? r.meals.join("/") : r.meals;
      if (meals)
        svg.append("text").attr("x", cx + cardW - 11).attr("y", cy + cardH - 9).attr("text-anchor", "end")
          .attr("font-size", 9).attr("fill", "#b0b0b8").text(truncate(meals, 18));
    });
    const rows = Math.ceil(items.length / cols);
    y += rows * cardH + (rows - 1) * rowGap + sectionGap;
  });
}

// `pie` — allocation donut(s): one or more donuts in a row (e.g. current vs target) with a SHARED color
// map by slice label + shared legend, % labels on the larger slices, the pie's name in the hole, and a
// caption below. Data: {title, subtitle, pies:[{label, caption?, slices:[{label, value}]}]} (or a single
// {slices:[...]}). The finance allocation-workshop view.
function renderPie(root, d) {
  const pies = (d.pies && d.pies.length) ? d.pies : [{ label: d.centerLabel || "", slices: d.slices || [] }];
  const labels = [];
  pies.forEach((p) => (p.slices || []).forEach((s) => { if (!labels.includes(s.label)) labels.push(s.label); }));
  const palette = T.categorical.concat(d3.schemeSet2 || []);
  const color = d3.scaleOrdinal().domain(labels).range(palette);

  const R = 118, inner = R * 0.6, gap = 70, M = { top: 66, right: 30, bottom: 14, left: 30 };
  const plotW = pies.length * (2 * R) + (pies.length - 1) * gap;
  const width = Math.max(460, M.left + M.right + plotW);
  const legPerRow = Math.max(1, Math.floor((width - M.left - M.right) / 210));
  const legRows = Math.ceil(labels.length / legPerRow);
  const legendTop = M.top + 2 * R + 24 + 16;
  const height = legendTop + legRows * 18 + 14;

  const svg = makeSvg(root, width, height);
  svg.append("text").attr("x", M.left).attr("y", 28).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Allocation");
  if (d.subtitle)
    svg.append("text").attr("x", M.left).attr("y", 49).attr("font-size", 12.5).attr("fill", T.inkDim)
      .text(truncate(d.subtitle, Math.floor((width - M.left - M.right) / 6.4)));

  const pieGen = d3.pie().value((s) => s.value).sort(null);
  const arcGen = d3.arc().innerRadius(inner).outerRadius(R);
  const startX = M.left + (width - M.left - M.right - plotW) / 2;
  pies.forEach((p, i) => {
    const cx = startX + R + i * (2 * R + gap);
    const cy = M.top + R;
    const total = (p.slices || []).reduce((a, s) => a + (s.value || 0), 0) || 1;
    const g = svg.append("g").attr("transform", `translate(${cx},${cy})`);
    pieGen(p.slices || []).forEach((a) => {
      g.append("path").attr("d", arcGen(a)).attr("fill", color(a.data.label))
        .attr("stroke", T.bg).attr("stroke-width", 1.5);
      const pct = (a.data.value / total) * 100;
      if (pct >= 6) {
        const [lx, ly] = arcGen.centroid(a);
        g.append("text").attr("x", lx).attr("y", ly).attr("text-anchor", "middle").attr("dy", "0.35em")
          .attr("font-size", 10.5).attr("font-weight", 600).attr("fill", T.knockout).text(`${Math.round(pct)}%`);
      }
    });
    g.append("text").attr("y", 4).attr("text-anchor", "middle").attr("font-size", 14).attr("font-weight", 700)
      .attr("fill", T.ink).text(p.label || "");
    if (p.caption)
      svg.append("text").attr("x", cx).attr("y", cy + R + 22).attr("text-anchor", "middle")
        .attr("font-size", 11.5).attr("fill", T.inkDim).text(p.caption);
  });

  const colW = (width - M.left - M.right) / legPerRow;
  const lg = svg.append("g").attr("transform", `translate(${M.left},${legendTop})`);
  labels.forEach((lab, i) => {
    const gg = lg.append("g").attr("transform", `translate(${(i % legPerRow) * colW},${Math.floor(i / legPerRow) * 18})`);
    gg.append("rect").attr("width", 11).attr("height", 11).attr("y", -9).attr("rx", 2).attr("fill", color(lab));
    gg.append("text").attr("x", 16).attr("font-size", 11).attr("fill", T.inkDim).text(truncate(lab, 28));
  });
}

// `treemap` — concentration view: holdings sized by $, colored by group, labels + %-of-total in-cell.
// Data: {title, subtitle, nodes:[{label, value, group, display?}], groups?:[{key, label}]}. Value in $;
// `display` overrides the auto "$Xk" label. The "where's the concentration" view (a few names dominate).
function renderTreemap(root, d) {
  const nodes = (d.nodes || []).filter((n) => (n.value || 0) > 0);
  const groupKeys = d.groups ? d.groups.map((g) => g.key)
    : Array.from(new Set(nodes.map((n) => n.group || "")));
  const groupLabel = new Map((d.groups || []).map((g) => [g.key, g.label]));
  const color = d3.scaleOrdinal().domain(groupKeys).range(T.categorical);
  const M = { top: 64, right: 16, bottom: 28, left: 16 };
  // Canvas is data-overridable: dense books (a full fund look-through runs many
  // dozens of tiles) starve at 900×460 — optional `width`/`plotH` in the data JSON size the
  // canvas; defaults unchanged.
  const width = d.width || 900, plotH = d.plotH || 460;
  const legRows = Math.ceil(groupKeys.length / 4);
  const height = M.top + plotH + 20 + legRows * 18 + M.bottom;
  const plotW = width - M.left - M.right;

  const hroot = d3.hierarchy({ children: nodes }).sum((n) => n.value || 0).sort((a, b) => b.value - a.value);
  d3.treemap().size([plotW, plotH]).paddingInner(3).round(true)(hroot);
  const total = hroot.value || 1;
  const fmt = (v) => `$${Math.round(v / 1000)}k`;

  const svg = makeSvg(root, width, height);
  svg.append("text").attr("x", M.left).attr("y", 28).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Concentration");
  if (d.subtitle)
    svg.append("text").attr("x", M.left).attr("y", 49).attr("font-size", 12.5).attr("fill", T.inkDim)
      .text(truncate(d.subtitle, Math.floor(plotW / 6.4)));

  hroot.leaves().forEach((leaf) => {
    const x = M.left + leaf.x0, y = M.top + leaf.y0, w = leaf.x1 - leaf.x0, h = leaf.y1 - leaf.y0;
    const c = color(leaf.data.group || "");
    const phosphor = T.tileMode === "phosphor";
    svg.append("rect").attr("x", x).attr("y", y).attr("width", w).attr("height", h).attr("rx", 4)
      .attr("fill", c).attr("fill-opacity", phosphor ? 0.24 : 0.85)
      .attr("stroke", phosphor ? c : T.bg)
      .attr("stroke-opacity", phosphor ? 0.6 : null).attr("stroke-width", 1.5);
    const pct = Math.round((leaf.data.value / total) * 100);
    if (w > 56 && h > 32) {
      svg.append("text").attr("x", x + 7).attr("y", y + 18).attr("font-size", 12).attr("font-weight", 700)
        .attr("fill", phosphor ? T.ink : T.knockout)
        .text(truncate(leaf.data.label, Math.floor((w - 12) / 7)));
      svg.append("text").attr("x", x + 7).attr("y", y + 33).attr("font-size", 10.5)
        .attr("fill", phosphor ? c : T.panelAlt)
        .text(`${leaf.data.display || fmt(leaf.data.value)} · ${pct}%`);
    } else if (w > 32 && h > 15) {
      svg.append("text").attr("x", x + 5).attr("y", y + 13).attr("font-size", 9.5)
        .attr("fill", phosphor ? T.ink : T.panel)
        .text(truncate(leaf.data.label, Math.floor((w - 8) / 6)));
    }
  });

  const lg = svg.append("g").attr("transform", `translate(${M.left},${M.top + plotH + 20})`);
  const lcolW = plotW / Math.max(1, Math.min(4, groupKeys.length));
  groupKeys.forEach((k, i) => {
    const gg = lg.append("g").attr("transform", `translate(${(i % 4) * lcolW},${Math.floor(i / 4) * 18})`);
    gg.append("rect").attr("width", 11).attr("height", 11).attr("y", -9).attr("rx", 2).attr("fill", color(k));
    gg.append("text").attr("x", 16).attr("font-size", 11).attr("fill", T.inkDim).text(truncate(groupLabel.get(k) || k, 32));
  });
}

// `sankey` — flow diagram (d3-sankey): left→right value flows. Data: {title, subtitle, nodes:[{name,
// group?}], links:[{source, target, value, label?}], unitPrefix?, unitSuffix?} (source/target = node
// name or index). Each link ribbon gets a value label at its midpoint — `label` overrides; otherwise
// unitPrefix + value + unitSuffix (e.g. "$" + 3.5 + "k" → "$3.5k"). The position-unwind flow view
// (position → proceeds → destinations).
function renderSankey(root, d) {
  const rawNodes = (d.nodes || []).map((n) => ({ ...n }));
  const idx = new Map(rawNodes.map((n, i) => [n.name, i]));
  const links = (d.links || []).map((l) => ({
    source: typeof l.source === "number" ? l.source : idx.get(l.source),
    target: typeof l.target === "number" ? l.target : idx.get(l.target),
    value: l.value || 0,
    label: l.label,
  })).filter((l) => l.source != null && l.target != null && l.value > 0);
  // Left gutter auto-sizes to the longest SOURCE-side label (nodes never appearing as a target) —
  // an earlier fixed 84px gutter clipped long source-side labels (doctrine rule 1: never truncate
  // into whitespace — the canvas had room). Right gutter stays 215 (target labels render rightward).
  const targetIdx = new Set(links.map((l) => l.target));
  const srcLabelMax = Math.max(0, ...rawNodes.filter((_, i) => !targetIdx.has(i)).map((n) => n.name.length));
  const M = { top: 66, right: 215, bottom: 18, left: Math.max(84, Math.min(260, srcLabelMax * 6.1 + 14)) };
  const width = 980, height = 70 + Math.max(240, rawNodes.length * 30);
  const color = d3.scaleOrdinal(T.categorical);

  const sk = d3sankey().nodeWidth(15).nodePadding(20).nodeAlign(sankeyJustify)
    .extent([[M.left, M.top], [width - M.right, height - M.bottom]]);
  const graph = sk({ nodes: rawNodes.map((n) => ({ ...n })), links });

  const svg = makeSvg(root, width, height);
  svg.append("text").attr("x", M.left).attr("y", 28).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Flow");
  if (d.subtitle)
    svg.append("text").attr("x", M.left).attr("y", 49).attr("font-size", 12.5).attr("fill", T.inkDim)
      .text(truncate(d.subtitle, Math.floor((width - M.left - M.right) / 6.4)));

  const fmtVal = (v) => (v % 1 === 0 ? String(v) : v.toFixed(1));
  const linkLabel = (l) =>
    l.label != null ? String(l.label) : `${d.unitPrefix || ""}${fmtVal(l.value)}${d.unitSuffix || ""}`;
  graph.links.forEach((l) => {
    svg.append("path").attr("d", sankeyLinkHorizontal()(l)).attr("fill", "none")
      .attr("stroke", color(l.source.group || l.source.name)).attr("stroke-opacity", T.ribbonOpacity)
      .attr("stroke-width", Math.max(1, l.width));
    // value label at the ribbon midpoint — white halo for readability over crossing ribbons
    const lx = (l.source.x1 + l.target.x0) / 2, ly = (l.y0 + l.y1) / 2;
    const t = svg.append("text").attr("x", lx).attr("y", ly).attr("dy", "0.35em")
      .attr("text-anchor", "middle").attr("font-size", 10.5).attr("font-weight", 600)
      .attr("fill", T.inkStrong).text(linkLabel(l));
    t.attr("stroke", T.linkLabelHalo).attr("stroke-width", 3).attr("paint-order", "stroke");
  });
  graph.nodes.forEach((n) => {
    svg.append("rect").attr("x", n.x0).attr("y", n.y0).attr("width", n.x1 - n.x0)
      .attr("height", Math.max(1, n.y1 - n.y0)).attr("rx", 2).attr("fill", color(n.group || n.name));
    const leftCol = n.x0 <= M.left + 2;
    svg.append("text").attr("x", leftCol ? n.x0 - 6 : n.x1 + 6).attr("y", (n.y0 + n.y1) / 2).attr("dy", "0.35em")
      .attr("text-anchor", leftCol ? "end" : "start").attr("font-size", 11).attr("fill", T.ink)
      .text(truncate(n.name, 36));
  });
}

// `flow` — a layered architecture / data-flow diagram: nodes placed in left→right columns, connected by
// labeled arrows, with optional curved `feedback` links arcing back underneath. This is the
// documentation/architecture-diagram type (a conceptual picture, NOT a data-driven research viz) — e.g.
// the README's corpus → harness → watchman → (back to corpus) loop. Data:
//   {title?, subtitle?, nodes:[{id, col, label, sublabel?, accent?}], links:[{source, target, label?, feedback?}]}
// `col` is the 0-based column index (multiple nodes can share a column → they stack); `sublabel` may carry
// "\n" for multiple lines; `accent` overrides the node's top-bar color (else cycles the theme palette).
function renderFlow(root, d) {
  const nodes = d.nodes || [];
  const links = d.links || [];
  const palette = T.categorical;

  const cols = Array.from(new Set(nodes.map((n) => n.col))).sort((a, b) => a - b);
  const colNodes = new Map(cols.map((c) => [c, nodes.filter((n) => n.col === c)]));
  const maxRows = d3.max(cols, (c) => colNodes.get(c).length) || 1;

  const subLines = (n) => (n.sublabel ? String(n.sublabel).split("\n") : []);
  const maxSub = d3.max(nodes, (n) => subLines(n).length) || 0;
  const NW = 200, NH = 50 + maxSub * 15;
  // Column gap auto-sizes to the longest ADJACENT-column link label (the auto-size doctrine: a fixed
  // gap that clips real labels is a defect — dense wire diagrams carry file/route names on their
  // arrows). Labels sit centered in the gap at ~6.15px/char (font 11); skip-column links are excluded
  // (their midpoints land over another column's band, so authors keep those labels short). Clamped so
  // a label can widen the canvas but never explode it.
  const colOf = new Map(nodes.map((n) => [n.id, n.col]));
  const adjLabelPx = links
    .filter((l) => !l.feedback && l.label && Math.abs(colOf.get(l.target) - colOf.get(l.source)) === 1)
    .map((l) => String(l.label).length * 6.15 + 28);
  const COLGAP = Math.min(260, Math.max(124, ...adjLabelPx, 0));
  const ROWGAP = 28;
  const hasFeedback = links.some((l) => l.feedback);
  const M = { top: d.title ? 78 : 30, right: 30, bottom: hasFeedback ? 96 : 36, left: 30 };

  const width = M.left + cols.length * NW + (cols.length - 1) * COLGAP + M.right;
  const bandH = maxRows * NH + (maxRows - 1) * ROWGAP;
  const height = M.top + bandH + M.bottom;

  const svg = makeSvg(root, width, height);
  if (d.title)
    svg.append("text").attr("x", M.left).attr("y", 32).attr("font-size", 20).attr("font-weight", 700)
      .attr("fill", T.ink).text(d.title);
  if (d.subtitle)
    svg.append("text").attr("x", M.left).attr("y", 54).attr("font-size", 12.5).attr("fill", T.inkDim)
      .text(truncate(d.subtitle, Math.floor((width - M.left - M.right) / 6.4)));

  const defs = svg.append("defs");
  const mk = (id, fill) =>
    defs.append("marker").attr("id", id).attr("viewBox", "0 0 10 10").attr("refX", 8.5).attr("refY", 5)
      .attr("markerWidth", 7).attr("markerHeight", 7).attr("orient", "auto-start-reverse")
      .append("path").attr("d", "M0,0 L10,5 L0,10 z").attr("fill", fill);
  mk("flow-arrow", T.inkDim);
  mk("flow-arrow-fb", T.inkFaint);

  // per-accent arrowheads, created on demand + deduped — an accented link needs its arrowhead to match
  // its stroke (markers carry a fixed fill, so each distinct accent gets its own marker def).
  const accentMarkers = new Map();
  const arrowFor = (accent) => {
    if (!accent) return "url(#flow-arrow)";
    if (!accentMarkers.has(accent)) {
      const id = `flow-arrow-a${accentMarkers.size}`;
      mk(id, accent);
      accentMarkers.set(accent, id);
    }
    return `url(#${accentMarkers.get(accent)})`;
  };

  // haloed text — readable wherever a label crosses an arrow or the panel edge
  const label = (x, y, text, opts = {}) => {
    const common = (sel) => sel.attr("x", x).attr("y", y).attr("text-anchor", "middle")
      .attr("font-size", opts.size || 11).attr("font-weight", opts.weight || 600)
      .attr("font-style", opts.italic ? "italic" : "normal").text(text);
    common(svg.append("text")).attr("stroke", T.bg).attr("stroke-width", 3.5).attr("fill", "none");
    common(svg.append("text")).attr("fill", opts.fill || T.inkStrong);
  };

  // positions (each column centered vertically within the band)
  const pos = new Map();
  cols.forEach((c, ci) => {
    const ns = colNodes.get(c);
    const colBandH = ns.length * NH + (ns.length - 1) * ROWGAP;
    const y0 = M.top + (bandH - colBandH) / 2;
    const x = M.left + ci * (NW + COLGAP);
    ns.forEach((n, ri) => pos.set(n.id, { x, y: y0 + ri * (NH + ROWGAP), w: NW, h: NH }));
  });

  // forward links (drawn under the boxes). Optional per-link fields (both backward-compatible —
  // absent fields render exactly the classic neutral link):
  //   accent: stroke + matching arrowhead + label color — lets a multi-path routing diagram carry a
  //           color per lane (e.g. one color per connection method), the "which path is this?" signal
  //   dash:   dashed stroke for planned/in-flight paths (roadmap semantics, vs solid = shipped)
  links.filter((l) => !l.feedback).forEach((l) => {
    const s = pos.get(l.source), t = pos.get(l.target);
    if (!s || !t) return;
    const sy = s.y + s.h / 2, ty = t.y + t.h / 2;
    const line = svg.append("line").attr("x1", s.x + s.w).attr("y1", sy).attr("x2", t.x - 3).attr("y2", ty)
      .attr("stroke", l.accent || T.inkDim).attr("stroke-width", 2).attr("marker-end", arrowFor(l.accent));
    if (l.dash) line.attr("stroke-dasharray", "6 5");
    if (l.label) label((s.x + s.w + t.x) / 2, (sy + ty) / 2 - 9, l.label, l.accent ? { fill: l.accent } : {});
  });

  // feedback links — dashed curve arcing back beneath everything
  links.filter((l) => l.feedback).forEach((l) => {
    const s = pos.get(l.source), t = pos.get(l.target);
    if (!s || !t) return;
    const sx = s.x + s.w / 2, tx = t.x + t.w / 2;
    const dip = M.top + bandH + 56;
    const path = `M${sx},${s.y + s.h} C${sx},${dip} ${tx},${dip} ${tx},${t.y + t.h + 3}`;
    svg.append("path").attr("d", path).attr("fill", "none").attr("stroke", T.inkFaint)
      .attr("stroke-width", 1.8).attr("stroke-dasharray", "5 4").attr("marker-end", "url(#flow-arrow-fb)");
    if (l.label) label((sx + tx) / 2, dip + 5, l.label, { fill: T.inkDim, weight: 500, italic: true });
  });

  // nodes (on top) — glowing neon cards: accent border + outer glow + a subtle accent-glass gradient
  // fill, with an accent-bright title. Under a non-glow theme it degrades to a clean bordered card.
  const useGlow = !!T.glow;
  nodes.forEach((n, i) => {
    const p = pos.get(n.id);
    if (!p) return;
    const accent = n.accent || palette[i % palette.length];
    const gid = `flow-grad-${i}`, fid = `flow-glow-${i}`;
    const grad = defs.append("linearGradient").attr("id", gid)
      .attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 1);
    grad.append("stop").attr("offset", "0%").attr("stop-color", accent).attr("stop-opacity", useGlow ? 0.20 : 0.10);
    grad.append("stop").attr("offset", "100%").attr("stop-color", accent).attr("stop-opacity", 0.03);
    if (useGlow)
      defs.append("filter").attr("id", fid).attr("x", "-45%").attr("y", "-45%").attr("width", "190%").attr("height", "190%")
        .append("feDropShadow").attr("dx", 0).attr("dy", 0).attr("stdDeviation", 5)
        .attr("flood-color", accent).attr("flood-opacity", 0.6);

    const g = svg.append("g").attr("transform", `translate(${p.x},${p.y})`);
    const box = g.append("rect").attr("width", p.w).attr("height", p.h).attr("rx", 13)
      .attr("fill", T.panel).attr("stroke", accent).attr("stroke-width", 1.8);
    if (useGlow) box.attr("filter", `url(#${fid})`);
    g.append("rect").attr("width", p.w).attr("height", p.h).attr("rx", 13).attr("fill", `url(#${gid})`);
    g.append("text").attr("x", p.w / 2).attr("y", 30).attr("text-anchor", "middle")
      .attr("font-size", 15.5).attr("font-weight", 800).attr("letter-spacing", 0.2)
      .attr("fill", accent).text(n.label);
    subLines(n).forEach((line, li) =>
      g.append("text").attr("x", p.w / 2).attr("y", 48 + li * 15).attr("text-anchor", "middle")
        .attr("font-size", 11).attr("fill", T.inkDim).text(line));
  });
}

// `line` — timeseries line chart: one or more series over a shared date x-axis + value y-axis, with
// gridlines, per-point dots, an end-of-line value label, and a legend (when >1 series). Data:
// {title, subtitle, yPrefix?, series:[{label, points:[{x, y}]}]} — x = ISO date string. The
// net-worth-trend / metric-over-time view (y-domain is min..max-padded, NOT forced to 0, so small
// trends are visible — it's a trend chart, not a magnitude comparison).
function renderLine(root, d) {
  const series = (d.series || []).filter((s) => (s.points || []).length);
  const color = d3.scaleOrdinal().domain(series.map((s) => s.label)).range(T.categorical);
  const M = { top: 64, right: 60, bottom: 48, left: 70 };
  const width = 860, height = 420;
  const plotW = width - M.left - M.right, plotH = height - M.top - M.bottom;

  const toDate = (x) => new Date(`${x}T12:00:00`);
  const pts = series.flatMap((s) => s.points.map((p) => ({ d: toDate(p.x), y: p.y })));
  const dates = pts.map((p) => p.d.getTime()), ys = pts.map((p) => p.y);
  const yLo = Math.min(...ys), yHi = Math.max(...ys), pad = (yHi - yLo) * 0.12 || yHi * 0.05 || 1;
  const x = d3.scaleTime().domain([new Date(Math.min(...dates)), new Date(Math.max(...dates))])
    .range([M.left, M.left + plotW]);
  const y = d3.scaleLinear().domain([yLo - pad, yHi + pad]).nice().range([M.top + plotH, M.top]);
  const yPrefix = d.yPrefix || "";
  const smallRange = yHi - yLo < 10000; // sub-$10k spread → show one decimal so $k ticks don't collide
  const fmtY = (v) => (yPrefix === "$" ? `$${(v / 1000).toFixed(smallRange ? 1 : 0)}k` : `${yPrefix}${v}`);

  const svg = makeSvg(root, width, height);
  svg.append("text").attr("x", 20).attr("y", 28).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Trend");
  if (d.subtitle)
    svg.append("text").attr("x", 20).attr("y", 49).attr("font-size", 12.5).attr("fill", T.inkDim)
      .text(truncate(d.subtitle, Math.floor((width - 40) / 6.4)));

  y.ticks(5).forEach((t) => {
    svg.append("line").attr("x1", M.left).attr("x2", M.left + plotW).attr("y1", y(t)).attr("y2", y(t))
      .attr("stroke", T.stroke).attr("stroke-opacity", 0.7);
    svg.append("text").attr("x", M.left - 8).attr("y", y(t) + 4).attr("text-anchor", "end")
      .attr("font-size", 10).attr("fill", T.inkFaint).text(fmtY(t));
  });
  const uniq = Array.from(new Set(dates)).sort((a, b) => a - b).map((t) => new Date(t));
  const xticks = uniq.length <= 8 ? uniq : x.ticks(6);
  const xfmt = d3.timeFormat("%b %-d");
  xticks.forEach((t) => {
    svg.append("text").attr("x", x(t)).attr("y", M.top + plotH + 18).attr("text-anchor", "middle")
      .attr("font-size", 10).attr("fill", T.inkFaint).text(xfmt(t));
  });

  const lineGen = d3.line().x((p) => x(p.d)).y((p) => y(p.y));
  series.forEach((s) => {
    const sp = s.points.map((p) => ({ d: toDate(p.x), y: p.y })).sort((a, b) => a.d - b.d);
    const c = color(s.label);
    svg.append("path").attr("d", lineGen(sp)).attr("fill", "none").attr("stroke", c).attr("stroke-width", 2.5);
    sp.forEach((p) => svg.append("circle").attr("cx", x(p.d)).attr("cy", y(p.y)).attr("r", 3.5).attr("fill", c));
    const last = sp[sp.length - 1];
    svg.append("text").attr("x", x(last.d) + 7).attr("y", y(last.y) + 4).attr("font-size", 11)
      .attr("font-weight", 600).attr("fill", c).text(fmtY(last.y));
  });

  if (series.length > 1) {
    const lg = svg.append("g").attr("transform", `translate(${M.left},${height - 10})`);
    let lx = 0;
    series.forEach((s) => {
      const g = lg.append("g").attr("transform", `translate(${lx},0)`);
      g.append("rect").attr("width", 11).attr("height", 11).attr("y", -9).attr("rx", 2).attr("fill", color(s.label));
      g.append("text").attr("x", 16).attr("font-size", 11).attr("fill", T.inkDim).text(s.label);
      lx += 16 + s.label.length * 6.6 + 20;
    });
  }
}

// Positioning scatter: each point placed at (x,y) on two continuous axes, colored by a categorical
// `group`, labeled with its short `label`. Built for bench positioning (valuation × quality), but
// generic. x supports log scaling (`xLog:true`) for wide-spread axes like P/S multiples. Dashed
// median crosshairs split the plane into quadrants so the "good corner" reads at a glance. Scores are
// CURATED/agent-judged (say so in the subtitle), not computed. data: {title, subtitle, xLabel, yLabel,
// xLog?, points:[{label, x, y, group, r?}]}.
function renderScatter(root, d) {
  const pts = (d.points || []).filter((p) => p.x > 0 && p.y != null);
  const groups = Array.from(new Set(pts.map((p) => p.group || "—")));
  const color = d3.scaleOrdinal().domain(groups).range(T.categorical);
  const M = { top: 74, right: 72, bottom: 80, left: 64 };
  const width = 900, height = 600;
  const plotW = width - M.left - M.right, plotH = height - M.top - M.bottom;

  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const xLo = Math.min(...xs), xHi = Math.max(...xs);
  const yLo = Math.min(...ys), yHi = Math.max(...ys);
  const x = (d.xLog ? d3.scaleLog() : d3.scaleLinear())
    .domain(d.xLog ? [xLo * 0.8, xHi * 1.15] : [xLo - (xHi - xLo) * 0.08, xHi + (xHi - xLo) * 0.08])
    .range([M.left, M.left + plotW]);
  const yPad = (yHi - yLo) * 0.18 || 1;
  const y = d3.scaleLinear().domain([yLo - yPad, yHi + yPad]).nice().range([M.top + plotH, M.top]);

  const svg = makeSvg(root, width, height);
  svg.append("text").attr("x", 20).attr("y", 30).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Positioning");
  if (d.subtitle)
    svg.append("text").attr("x", 20).attr("y", 51).attr("font-size", 12.5).attr("fill", T.inkDim)
      .text(truncate(d.subtitle, Math.floor((width - 40) / 6.4)));

  // y gridlines + ticks
  y.ticks(6).forEach((t) => {
    svg.append("line").attr("x1", M.left).attr("x2", M.left + plotW).attr("y1", y(t)).attr("y2", y(t))
      .attr("stroke", T.stroke).attr("stroke-opacity", 0.7);
    svg.append("text").attr("x", M.left - 8).attr("y", y(t) + 4).attr("text-anchor", "end")
      .attr("font-size", 10).attr("fill", T.inkFaint).text(t);
  });
  // x gridlines + ticks (log-aware)
  const candidateTicks = [1, 1.5, 2, 3, 5, 8, 12, 20, 30, 50, 80];
  const xticks = (d.xLog ? candidateTicks.filter((v) => v >= x.domain()[0] && v <= x.domain()[1]) : x.ticks(7));
  xticks.forEach((t) => {
    svg.append("line").attr("x1", x(t)).attr("x2", x(t)).attr("y1", M.top).attr("y2", M.top + plotH)
      .attr("stroke", T.stroke).attr("stroke-opacity", 0.5);
    svg.append("text").attr("x", x(t)).attr("y", M.top + plotH + 16).attr("text-anchor", "middle")
      .attr("font-size", 10).attr("fill", T.inkFaint).text(d.xLog ? `${t}×` : t);
  });

  // median crosshairs → quadrant guide
  const median = (a) => { const s = [...a].sort((m, n) => m - n); const h = Math.floor(s.length / 2); return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2; };
  const mx = median(xs), my = median(ys);
  svg.append("line").attr("x1", x(mx)).attr("x2", x(mx)).attr("y1", M.top).attr("y2", M.top + plotH)
    .attr("stroke", T.inkFaint).attr("stroke-dasharray", "4 4").attr("stroke-opacity", 0.55);
  svg.append("line").attr("x1", M.left).attr("x2", M.left + plotW).attr("y1", y(my)).attr("y2", y(my))
    .attr("stroke", T.inkFaint).attr("stroke-dasharray", "4 4").attr("stroke-opacity", 0.55);
  svg.append("text").attr("x", M.left + 6).attr("y", M.top + 15).attr("font-size", 10).attr("font-weight", 700)
    .attr("fill", T.inkFaint).attr("letter-spacing", 0.5).text("CHEAP + HIGH-QUALITY");

  // axis labels
  svg.append("text").attr("x", M.left + plotW / 2).attr("y", height - 30).attr("text-anchor", "middle")
    .attr("font-size", 12).attr("fill", T.inkDim).text(d.xLabel || "Valuation (cheap → rich)");
  svg.append("text").attr("transform", `translate(17,${M.top + plotH / 2}) rotate(-90)`)
    .attr("text-anchor", "middle").attr("font-size", 12).attr("fill", T.inkDim).text(d.yLabel || "Quality →");

  // markers first (at their true positions)
  pts.forEach((p) => {
    svg.append("circle").attr("cx", x(p.x)).attr("cy", y(p.y)).attr("r", p.r || 6)
      .attr("fill", color(p.group || "—")).attr("fill-opacity", 0.85)
      .attr("stroke", T.knockout).attr("stroke-width", 1);
  });
  // value-labels with greedy vertical de-clutter (banded quality scores → horizontal collisions;
  // nudge colliders down and draw a faint leader when a label travels off its marker).
  // Seed the index with marker footprints so labels dodge ALL points, not just each other.
  const placed = pts.map((p) => { const r = p.r || 6; return { x1: x(p.x) - r, x2: x(p.x) + r, y1: y(p.y) - r, y2: y(p.y) + r }; });
  const hits = (a, b) => !(a.x2 < b.x1 || a.x1 > b.x2 || a.y2 < b.y1 || a.y1 > b.y2);
  pts.map((p) => ({ p, px: x(p.x), py: y(p.y) })).sort((a, b) => a.px - b.px).forEach(({ p, px, py }) => {
    const r = p.r || 6, w = p.label.length * 6.2 + 4;
    const rightEdge = px > M.left + plotW - 56;       // flip label to the left near the right margin
    const lx = px + (rightEdge ? -(r + 3) - w : r + 3);
    let ly = py + 3.5, tries = 0;
    let box = { x1: lx, x2: lx + w, y1: ly - 9, y2: ly + 3 };
    while (placed.some((q) => hits(box, q)) && tries < 14) { ly += 12; box = { x1: lx, x2: lx + w, y1: ly - 9, y2: ly + 3 }; tries++; }
    placed.push(box);
    if (ly - (py + 3.5) > 6)
      svg.append("line").attr("x1", px + (rightEdge ? -r : r)).attr("y1", py)
        .attr("x2", rightEdge ? lx + w : lx).attr("y2", ly - 3)
        .attr("stroke", T.inkFaint).attr("stroke-opacity", 0.4).attr("stroke-width", 0.75);
    svg.append("text").attr("x", lx).attr("y", ly).attr("text-anchor", "start")
      .attr("font-size", 10).attr("font-weight", 600).attr("fill", T.inkStrong).text(p.label);
  });

  // legend (bottom)
  const lg = svg.append("g").attr("transform", `translate(${M.left}, ${height - 12})`);
  let lx = 0;
  groups.forEach((g) => {
    const gg = lg.append("g").attr("transform", `translate(${lx},0)`);
    gg.append("circle").attr("r", 5).attr("cx", 5).attr("cy", -4).attr("fill", color(g));
    gg.append("text").attr("x", 15).attr("font-size", 11).attr("fill", T.inkDim).text(g);
    lx += 15 + g.length * 6.6 + 22;
  });
}

// Drive-time map: home at center, concentric (sqrt-spaced) rings = travel time, each option placed by
// compass bearing (0°=N, clockwise) at a radius scaled to its drive-time, colored by indoor/outdoor
// (the weather-fit / rain-hedge encoding). Geography is the can't-hold-it-in-text case. Data:
// {title,subtitle,origin, rings:[{minutes,label?}], points:[{label,bearing_deg,minutes,indoor?}]}.
function renderRadial(root, d) {
  const points = d.points || [];
  const rings = d.rings && d.rings.length ? d.rings : [{ minutes: 15 }, { minutes: 30 }, { minutes: 60 }, { minutes: 120 }];
  const domainMax = Math.max(...rings.map((r) => r.minutes), ...points.map((p) => p.minutes || 0), 1);

  const size = 760;
  const M = { top: 72, right: 20, bottom: 40, left: 20 };
  const cx = size / 2;
  const cy = M.top + (size - M.top - M.bottom) / 2;
  const maxR = Math.min(size - M.left - M.right, size - M.top - M.bottom) / 2 - 10;
  const rScale = d3.scaleSqrt().domain([0, domainMax]).range([0, maxR]); // sqrt: spreads the near cluster

  const width = size, height = size;
  const INDOOR = "#0ea5e9", OUTDOOR = "#f59e0b"; // rain-safe blue · outdoor amber

  const svg = root
    .append("svg")
    .attr("xmlns", "http://www.w3.org/2000/svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("width", width)
    .attr("height", height)
    .attr("font-family", T.font);
  svg.append("rect").attr("width", width).attr("height", height).attr("fill", T.bg);

  svg.append("text").attr("x", M.left).attr("y", 30).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Drive-time map");
  if (d.subtitle)
    svg.append("text").attr("x", M.left).attr("y", 51).attr("font-size", 13).attr("fill", T.inkDim)
      .text(d.subtitle);

  // concentric drive-time rings + minute labels (along the top)
  rings.forEach((r) => {
    const rr = rScale(r.minutes);
    svg.append("circle").attr("cx", cx).attr("cy", cy).attr("r", rr)
      .attr("fill", "none").attr("stroke", "#d9d9e0").attr("stroke-dasharray", "3 4");
    svg.append("text").attr("x", cx + 4).attr("y", cy - rr - 3).attr("text-anchor", "start")
      .attr("font-size", 10).attr("fill", T.inkFaint).text(r.label || `${r.minutes} min`);
  });

  // compass ticks
  [["N", 0], ["E", 90], ["S", 180], ["W", 270]].forEach(([lab, b]) => {
    const rad = (b * Math.PI) / 180;
    const x = cx + (maxR + 14) * Math.sin(rad);
    const y = cy - (maxR + 14) * Math.cos(rad);
    svg.append("text").attr("x", x).attr("y", y + 4).attr("text-anchor", "middle")
      .attr("font-size", 11).attr("font-weight", 600).attr("fill", "#b0b0b8").text(lab);
  });

  // home origin (center)
  svg.append("circle").attr("cx", cx).attr("cy", cy).attr("r", 6).attr("fill", T.ink);
  svg.append("text").attr("x", cx).attr("y", cy - 11).attr("text-anchor", "middle")
    .attr("font-size", 11).attr("font-weight", 700).attr("fill", T.ink).text(d.origin || "Home");

  // options
  points.forEach((p) => {
    const rad = ((p.bearing_deg || 0) * Math.PI) / 180;
    const rr = rScale(p.minutes || 0);
    const x = cx + rr * Math.sin(rad);
    const y = cy - rr * Math.cos(rad);
    const c = p.indoor ? INDOOR : OUTDOOR;
    svg.append("circle").attr("cx", x).attr("cy", y).attr("r", 6)
      .attr("fill", c).attr("fill-opacity", 0.9).attr("stroke", T.panel).attr("stroke-width", 1.5);
    const right = x >= cx; // label points away from center (left of center → anchor-end)
    svg.append("text").attr("x", x + (right ? 10 : -10)).attr("y", y + 4)
      .attr("text-anchor", right ? "start" : "end")
      .attr("font-size", 11).attr("fill", T.ink)
      .text(`${truncate(p.label, 22)}  ${p.minutes}m`);
  });

  // legend
  const lg = svg.append("g").attr("transform", `translate(${M.left},${height - 16})`);
  let lx = 0;
  const chip = (fill, label) => {
    const g = lg.append("g").attr("transform", `translate(${lx},0)`);
    g.append("circle").attr("r", 6).attr("cy", -4).attr("fill", fill);
    g.append("text").attr("x", 12).attr("y", -1).attr("font-size", 11).attr("fill", T.inkDim).text(label);
    lx += 12 + label.length * 6.6 + 22;
  };
  chip(INDOOR, "indoor / rain-safe");
  chip(OUTDOOR, "outdoor (best on a dry day)");
}

// Radar / spider — candidates × qualitative axes (the diverge-pool decision view). Each axis radiates
// from center; each candidate is a translucent polygon connecting its per-axis values, so relative
// strengths/weaknesses read as a *shape* at a glance. Data: {title,subtitle, axes:[str], max?,
// candidates:[{label, values:[number]}]}. values are on a 0..max scale (default max = data peak).
function renderCompare(root, d) {
  const axes = d.axes || [];
  const cands = (d.candidates || []).filter((c) => Array.isArray(c.values));
  const N = axes.length;
  const maxV = d.max || d3.max([1, ...cands.flatMap((c) => c.values)]) || 1;

  const M = { top: 88, right: 30, bottom: 64, left: 30 }; // top clears title+subtitle above the apex label
  const ringR = 220; // radar radius
  const labelPad = 132; // room for axis labels around the perimeter
  const cx = M.left + labelPad + ringR;
  const cy = M.top + ringR;
  const width = cx + ringR + labelPad + M.right;
  // legend wraps to multiple rows when labels overflow the canvas width; pre-measure the row
  // count here so the SVG height grows to contain it (height is fixed before the legend is drawn).
  const legendItemW = (c) => 12 + String(c.label).length * 6.8 + 24;
  const legendAvailW = width - M.left - M.right;
  const legendRowH = 18;
  let legendRows = 1;
  let _measureX = 0;
  cands.forEach((c) => {
    const w = legendItemW(c);
    if (_measureX > 0 && _measureX + w > legendAvailW) {
      legendRows += 1;
      _measureX = 0;
    }
    _measureX += w;
  });
  const height = cy + ringR + M.bottom + (legendRows - 1) * legendRowH;

  const color = d3.scaleOrdinal().domain(cands.map((c) => c.label)).range(T.categorical);
  // angle: start at top (−90°), go clockwise
  const ang = (i) => (i / N) * 2 * Math.PI - Math.PI / 2;
  const rad = (v) => (v / maxV) * ringR;
  const pt = (i, v) => [cx + rad(v) * Math.cos(ang(i)), cy + rad(v) * Math.sin(ang(i))];
  const poly = (vals) => vals.map((v, i) => pt(i, v).join(",")).join(" ");

  const svg = makeSvg(root, width, height);
  svg.append("text").attr("x", M.left).attr("y", 30).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Comparison");
  if (d.subtitle)
    svg.append("text").attr("x", M.left).attr("y", 51).attr("font-size", 13).attr("fill", T.inkDim)
      .text(d.subtitle);

  // concentric grid rings (one per integer level) + faint level numbers up the top spoke
  for (let lvl = 1; lvl <= maxV; lvl++) {
    svg.append("polygon").attr("points", poly(axes.map(() => lvl)))
      .attr("fill", lvl === maxV ? T.bg : "none").attr("stroke", T.stroke).attr("stroke-width", 1);
    svg.append("text").attr("x", cx + 4).attr("y", cy - rad(lvl) + 3).attr("font-size", 9)
      .attr("fill", "#c7c7cc").text(lvl);
  }

  // axis spokes + perimeter labels (anchored by angle so they don't collide with the radar)
  axes.forEach((label, i) => {
    const [ex, ey] = pt(i, maxV);
    svg.append("line").attr("x1", cx).attr("y1", cy).attr("x2", ex).attr("y2", ey)
      .attr("stroke", T.stroke).attr("stroke-width", 1);
    const ca = Math.cos(ang(i)), sa = Math.sin(ang(i));
    const lx = cx + (ringR + 14) * ca, ly = cy + (ringR + 14) * sa;
    const anchor = Math.abs(ca) < 0.25 ? "middle" : ca > 0 ? "start" : "end";
    const dy = sa < -0.25 ? -2 : sa > 0.25 ? 11 : 4;
    svg.append("text").attr("x", lx).attr("y", ly + dy).attr("text-anchor", anchor)
      .attr("font-size", 11.5).attr("font-weight", 600).attr("fill", T.inkStrong).text(label);
  });

  // candidate polygons (translucent fill + colored stroke + vertex dots)
  cands.forEach((c) => {
    const col = color(c.label);
    svg.append("polygon").attr("points", poly(c.values))
      .attr("fill", col).attr("fill-opacity", 0.12).attr("stroke", col).attr("stroke-width", 2)
      .attr("stroke-opacity", 0.85);
    c.values.forEach((v, i) => {
      const [x, y] = pt(i, v);
      svg.append("circle").attr("cx", x).attr("cy", y).attr("r", 3).attr("fill", col);
    });
  });

  // legend (wraps across rows using the same packing as the pre-measure above; first row sits at the
  // original bottom offset, later rows stack downward into the height we grew for them)
  const legendTop = height - 18 - (legendRows - 1) * legendRowH;
  let lx = 0;
  let lrow = 0;
  cands.forEach((c) => {
    const w = legendItemW(c);
    if (lx > 0 && lx + w > legendAvailW) {
      lrow += 1;
      lx = 0;
    }
    const g = svg.append("g")
      .attr("transform", `translate(${M.left + lx},${legendTop + lrow * legendRowH})`);
    g.append("circle").attr("r", 6).attr("cy", -4).attr("fill", color(c.label));
    g.append("text").attr("x", 12).attr("y", -1).attr("font-size", 11).attr("fill", T.inkDim)
      .text(c.label);
    lx += w;
  });
}

// matrix: destination-characteristics heatmap. rows × axes, cell = score (more = more of that
// quality, taste-neutral — NOT a goodness ranking; weight per trip). Optional `group` bands + a
// neutral `reach` column (effort, not a demerit). data: {title, subtitle?, axes:[str], max?,
//   reachLabel?, note?, rows:[{label, group?, values:[number], reach?}]}
function renderMatrix(root, d) {
  const axes = d.axes || [];
  const rowsIn = (d.rows || []).filter((r) => Array.isArray(r.values));
  const maxV = d.max || d3.max([1, ...rowsIn.flatMap((r) => r.values)]) || 1;
  const hasReach = rowsIn.some((r) => r.reach);
  const hasDetail = rowsIn.some((r) => r.detail);

  const groups = [];
  const gmap = new Map();
  rowsIn.forEach((r) => {
    const k = r.group || "";
    if (!gmap.has(k)) {
      gmap.set(k, []);
      groups.push(k);
    }
    gmap.get(k).push(r);
  });

  const cell = 30; // per-axis column width
  const rowH = 21;
  const labelW = 190; // left destination-name column
  // descriptor columns size themselves to the longest string (capped) — no truncating into whitespace
  const reachChars = hasReach
    ? Math.min(78, Math.max(20, ...rowsIn.map((r) => (r.reach || "").length)))
    : 0;
  const reachW = hasReach ? Math.ceil(reachChars * 6.2) : 0;
  // detail: a single free-text string OR structured sub-columns via `detailCols`:
  // detailCols:[{key,label}] + per-row detail:{key:value}. Aligned sub-columns with
  // mini-headers + faint separators — alignment is the delineation.
  const detailColsDef = Array.isArray(d.detailCols) && d.detailCols.length ? d.detailCols : null;
  let detailSpec = [];
  let detailChars = 0;
  let detailW = 0;
  if (hasDetail && detailColsDef) {
    detailSpec = detailColsDef.map((c) => {
      const vals = rowsIn.map((r) => String((r.detail || {})[c.key] ?? ""));
      const chars = Math.min(80, Math.max(String(c.label || "").length, 3, ...vals.map((v) => v.length)));
      return { key: c.key, label: String(c.label || c.key), chars, w: Math.ceil(chars * 6.2) };
    });
    detailW = detailSpec.reduce((a, c) => a + c.w, 0) + (detailSpec.length - 1) * 18;
  } else if (hasDetail) {
    detailChars = Math.min(78, Math.max(20, ...rowsIn.map((r) => String(r.detail || "").length)));
    detailW = Math.ceil(detailChars * 6.2);
  }
  const headerH = 92; // rotated axis headers
  const groupH = 22;
  const M = { top: 60, right: 22, bottom: 40, left: 22 };

  const gridW = axes.length * cell;
  const gridX = M.left + labelW;
  const rowsW = labelW + gridW + (reachW ? 14 + reachW : 0) + (detailW ? 14 + detailW : 0);
  const reachHdr = d.reachLabel || "Reach (effort, not a demerit)";
  const detailHdr = d.detailLabel || "Detail";
  const detailX = gridX + gridW + (reachW ? 14 + reachW : 0) + 14;
  // grow the canvas so a long title / subtitle / reach-header isn't clipped (text > grid width)
  const width = Math.max(
    M.left + rowsW + M.right,
    M.left + (d.title || "Destination characteristics").length * 11 + M.right,
    d.subtitle ? M.left + d.subtitle.length * 6.9 + M.right : 0,
    hasReach ? gridX + gridW + 14 + reachHdr.length * 6.6 + M.right : 0,
    hasDetail ? detailX + detailHdr.length * 6.6 + M.right : 0
  );
  const bodyTop = M.top + headerH;
  const nGroupBands = groups.filter((g) => g).length;
  const height = bodyTop + nGroupBands * groupH + rowsIn.length * rowH + M.bottom;

  const color = d3.scaleSequential(d3.interpolateBlues).domain([0, maxV * 1.08]);
  const textColor = (v) => (v / maxV > 0.6 ? T.knockout : T.inkStrong);

  const svg = makeSvg(root, width, height);
  svg.append("text").attr("x", M.left).attr("y", 27).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Destination characteristics");
  if (d.subtitle)
    svg.append("text").attr("x", M.left).attr("y", 48).attr("font-size", 12.5).attr("fill", T.inkDim)
      .text(d.subtitle);

  axes.forEach((a, i) => {
    const x = gridX + i * cell + cell / 2;
    svg.append("text").attr("x", x).attr("y", bodyTop - 8)
      .attr("transform", `rotate(-45 ${x} ${bodyTop - 8})`).attr("text-anchor", "start")
      .attr("font-size", 11.5).attr("font-weight", 600).attr("fill", T.inkStrong).text(a);
  });
  if (hasReach)
    svg.append("text").attr("x", gridX + gridW + 14).attr("y", bodyTop - 8)
      .attr("font-size", 10.5).attr("font-weight", 600).attr("fill", T.inkFaint)
      .text(reachHdr);
  if (hasDetail && detailSpec.length) {
    let dx = detailX;
    detailSpec.forEach((c) => {
      c.x = dx;
      svg.append("text").attr("x", dx).attr("y", bodyTop - 8)
        .attr("font-size", 10.5).attr("font-weight", 600).attr("fill", T.inkFaint).text(c.label);
      dx += c.w + 18;
    });
  } else if (hasDetail) {
    svg.append("text").attr("x", detailX).attr("y", bodyTop - 8)
      .attr("font-size", 10.5).attr("font-weight", 600).attr("fill", T.inkFaint)
      .text(detailHdr);
  }

  let y = bodyTop;
  groups.forEach((g) => {
    if (g) {
      svg.append("rect").attr("x", M.left).attr("y", y).attr("width", rowsW).attr("height", groupH)
        .attr("fill", T.panelAlt);
      svg.append("text").attr("x", M.left + 6).attr("y", y + groupH - 7).attr("font-size", 10.5)
        .attr("font-weight", 700).attr("fill", T.inkDim).attr("letter-spacing", "0.04em")
        .text(g.toUpperCase());
      y += groupH;
    }
    gmap.get(g).forEach((r) => {
      svg.append("text").attr("x", M.left + 4).attr("y", y + rowH - 7).attr("font-size", 12)
        .attr("fill", T.ink).text(truncate(r.label, 27));
      r.values.forEach((v, i) => {
        const x = gridX + i * cell;
        svg.append("rect").attr("x", x + 1).attr("y", y + 1).attr("width", cell - 2)
          .attr("height", rowH - 2).attr("rx", 3).attr("fill", v ? color(v) : T.panelAlt);
        if (v)
          svg.append("text").attr("x", x + cell / 2).attr("y", y + rowH - 6).attr("text-anchor", "middle")
            .attr("font-size", 10.5).attr("font-weight", 600).attr("fill", textColor(v)).text(v);
      });
      if (hasReach && r.reach)
        svg.append("text").attr("x", gridX + gridW + 14).attr("y", y + rowH - 6).attr("font-size", 10.5)
          .attr("fill", T.inkFaint).text(truncate(r.reach, reachChars));
      if (hasDetail && detailSpec.length && r.detail) {
        detailSpec.forEach((c) => {
          const v = String((r.detail || {})[c.key] ?? "");
          if (!v) return;
          const fill = v.startsWith("+") ? "#2e7d32" : /^[−-]/.test(v) ? "#c62828" : T.inkStrong;
          svg.append("text").attr("x", c.x).attr("y", y + rowH - 6).attr("font-size", 10.5)
            .attr("fill", fill).text(truncate(v, c.chars));
        });
      } else if (hasDetail && r.detail)
        svg.append("text").attr("x", detailX).attr("y", y + rowH - 6).attr("font-size", 10.5)
          .attr("fill", T.inkStrong).text(truncate(String(r.detail), detailChars));
      y += rowH;
    });
  });

  // faint vertical separators delineating the descriptor columns (structured mode)
  if (hasDetail && detailSpec.length) {
    const sepTop = bodyTop - 20, sepBot = y - 4;
    const sepXs = [detailX - 9, ...detailSpec.slice(1).map((c) => c.x - 9)];
    sepXs.forEach((sx) => {
      svg.append("line").attr("x1", sx).attr("x2", sx).attr("y1", sepTop).attr("y2", sepBot)
        .attr("stroke", T.stroke).attr("stroke-width", 1);
    });
  }

  const lg = svg.append("g").attr("transform", `translate(${M.left},${height - 16})`);
  // neutral default legend; domain framing belongs in the data (`note`) —
  lg.append("text").attr("x", 0).attr("y", 0).attr("font-size", 10.5).attr("fill", T.inkDim)
    .text(d.note || "more of this quality →  (a characteristics scale, not a goodness score)");
  for (let v = 1; v <= maxV; v++) {
    const bx = gridX + gridW - maxV * 26 + (v - 1) * 26;
    lg.append("rect").attr("x", bx).attr("y", -11).attr("width", 22).attr("height", 13).attr("rx", 3)
      .attr("fill", color(v));
    lg.append("text").attr("x", bx + 11).attr("y", -1).attr("text-anchor", "middle").attr("font-size", 9.5)
      .attr("fill", textColor(v)).text(v);
  }
}

// weather-strip: daily hi/lo + precip across a window, for one or many places (stacked lanes share
// the day x-axis). data: {title, subtitle?, unit?, note?, days?:[...], label?,
//   series?:[{label, days:[{date, hi, lo, precip?, cond?}]}]}
function renderWeatherStrip(root, d) {
  const series = d.series || (d.days ? [{ label: d.label || "", days: d.days }] : []);
  const allDays = series[0] ? series[0].days : [];
  const nDays = allDays.length;
  const colW = 56;
  const laneH = 96;
  const labelW = series.length > 1 || (series[0] && series[0].label) ? 124 : 8;
  // top clears title (y27) + subtitle (y48) before the day-header row (M.top-22 / -9)
  const M = { top: 76, right: 22, bottom: 34, left: 22 };
  const gridX = M.left + labelW;
  // grow to fit a long title/subtitle when a short window makes the grid narrow (else they clip)
  const width = Math.max(
    gridX + nDays * colW + M.right,
    M.left + (d.title || "Weather").length * 11 + M.right,
    d.subtitle ? M.left + d.subtitle.length * 6.9 + M.right : 0
  );
  const height = M.top + series.length * laneH + M.bottom;
  const unit = d.unit || "F";

  const allHi = series.flatMap((s) => s.days.map((x) => x.hi));
  const allLo = series.flatMap((s) => s.days.map((x) => x.lo));
  const tMin = d3.min(allLo);
  const tMax = d3.max(allHi);
  const tColor = d3.scaleSequential(d3.interpolateRdYlBu).domain([tMax == null ? 90 : tMax, tMin == null ? 40 : tMin]);
  const ppColor = (p) => (p >= 50 ? "#0a84ff" : p >= 20 ? "#5ac8fa" : "#cfe9ff");

  const svg = makeSvg(root, width, height);
  svg.append("text").attr("x", M.left).attr("y", 27).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Weather");
  if (d.subtitle)
    svg.append("text").attr("x", M.left).attr("y", 48).attr("font-size", 12.5).attr("fill", T.inkDim)
      .text(d.subtitle);

  allDays.forEach((day, i) => {
    const x = gridX + i * colW + colW / 2;
    const dt = parseDay(day.date);
    const dow = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][dt.getDay()];
    const md = `${dt.getMonth() + 1}/${dt.getDate()}`;
    svg.append("text").attr("x", x).attr("y", M.top - 22).attr("text-anchor", "middle").attr("font-size", 11)
      .attr("font-weight", 700).attr("fill", isWeekend(dt) ? T.ink : T.inkDim).text(dow);
    svg.append("text").attr("x", x).attr("y", M.top - 9).attr("text-anchor", "middle").attr("font-size", 10)
      .attr("fill", T.inkFaint).text(md);
  });

  series.forEach((s, si) => {
    const top = M.top + si * laneH;
    if (labelW > 8)
      svg.append("text").attr("x", M.left).attr("y", top + laneH / 2).attr("font-size", 12.5)
        .attr("font-weight", 600).attr("fill", T.ink).text(truncate(s.label, 17));
    s.days.forEach((day, i) => {
      const x = gridX + i * colW;
      svg.append("rect").attr("x", x + 3).attr("y", top + 6).attr("width", colW - 6).attr("height", 36)
        .attr("rx", 6).attr("fill", tColor((day.hi + day.lo) / 2)).attr("fill-opacity", 0.9);
      svg.append("text").attr("x", x + colW / 2).attr("y", top + 22).attr("text-anchor", "middle")
        .attr("font-size", 13).attr("font-weight", 700).attr("fill", T.ink).text(`${Math.round(day.hi)}°`);
      svg.append("text").attr("x", x + colW / 2).attr("y", top + 37).attr("text-anchor", "middle")
        .attr("font-size", 10).attr("fill", T.inkStrong).text(`${Math.round(day.lo)}°`);
      const pp = Math.max(0, Math.min(100, day.precip == null ? 0 : day.precip));
      const barMax = 30;
      const bh = (pp / 100) * barMax;
      svg.append("rect").attr("x", x + colW / 2 - 9).attr("y", top + 48 + (barMax - bh)).attr("width", 18)
        .attr("height", bh).attr("rx", 2).attr("fill", ppColor(pp));
      svg.append("text").attr("x", x + colW / 2).attr("y", top + 48 + barMax + 12).attr("text-anchor", "middle")
        .attr("font-size", 9.5).attr("fill", pp >= 50 ? "#0a84ff" : T.inkFaint).text(`${pp}%`);
    });
  });

  svg.append("text").attr("x", M.left).attr("y", height - 12).attr("font-size", 10.5).attr("fill", T.inkFaint)
    .text(d.note || `°${unit} hi/lo · blue bar = precip chance · warm→cool tinted by mean temp`);
}

// map: d3-geo. Destinations pinned on a world basemap + great-circle arcs from a home origin; the
// projection auto-fits to origin+points (regional sets zoom in, world sets zoom out). Reach labels
// are CURATED (passed in), not routed/computed — d3-geo only projects + draws. data: {title,
//   subtitle?, note?, origin:{label,lat,lon}, points:[{label, lat, lon, reach?, group?}]}
function renderMap(root, d) {
  const origin = d.origin || { label: "Home", lat: 39.83, lon: -98.58 };  // neutral placeholder (US geographic center) when no origin supplied
  const points = (d.points || []).filter((p) => p.lat != null && p.lon != null);
  const W = 920;
  const H = 560;
  const pad = 60;

  // basemap: lazy-load the world TopoJSON only for this type (don't pay it on every render)
  const world = JSON.parse(readFileSync(new URL(import.meta.resolve("world-atlas/countries-110m.json"))));
  const land = feature(world, world.objects.countries);

  const coords = [[origin.lon, origin.lat], ...points.map((p) => [p.lon, p.lat])];
  const projection = d3
    .geoMercator()
    .fitExtent([[pad, pad + 30], [W - pad, H - pad]], { type: "MultiPoint", coordinates: coords });
  const path = d3.geoPath(projection);

  const groups = Array.from(new Set(points.map((p) => p.group || "")));
  const color = d3.scaleOrdinal().domain(groups).range(T.categorical);

  const svg = makeSvg(root, W, H);
  svg.append("rect").attr("width", W).attr("height", H).attr("fill", "#eaf2fb"); // ocean
  svg.append("path").attr("d", path(land)).attr("fill", T.panelAlt).attr("stroke", T.panel).attr("stroke-width", 0.6);

  svg.append("text").attr("x", pad).attr("y", 30).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Map");
  if (d.subtitle)
    svg.append("text").attr("x", pad).attr("y", 50).attr("font-size", 12.5).attr("fill", T.inkStrong)
      .text(d.subtitle);

  // great-circle arcs origin → each point (sampled so they curve, not straight projected lines)
  points.forEach((p) => {
    const interp = d3.geoInterpolate([origin.lon, origin.lat], [p.lon, p.lat]);
    const arc = { type: "LineString", coordinates: d3.range(0, 1.0001, 0.02).map(interp) };
    svg.append("path").attr("d", path(arc)).attr("fill", "none")
      .attr("stroke", color(p.group || "")).attr("stroke-width", 1.3).attr("stroke-opacity", 0.5);
  });

  // destination pins + labels
  points.forEach((p) => {
    const [x, y] = projection([p.lon, p.lat]);
    svg.append("circle").attr("cx", x).attr("cy", y).attr("r", 4.5).attr("fill", color(p.group || ""))
      .attr("stroke", T.panel).attr("stroke-width", 1);
    svg.append("text").attr("x", x + 7).attr("y", y - 2).attr("font-size", 10.5).attr("font-weight", 600)
      .attr("fill", T.ink).text(truncate(p.label, 22));
    if (p.reach)
      svg.append("text").attr("x", x + 7).attr("y", y + 9).attr("font-size", 8.8).attr("fill", T.inkDim)
        .text(truncate(p.reach, 24));
  });

  // origin marker (drawn last so it sits on top)
  const [ox, oy] = projection([origin.lon, origin.lat]);
  svg.append("circle").attr("cx", ox).attr("cy", oy).attr("r", 11).attr("fill", "none")
    .attr("stroke", T.ink).attr("stroke-width", 1.5);
  svg.append("circle").attr("cx", ox).attr("cy", oy).attr("r", 5).attr("fill", T.ink);
  svg.append("text").attr("x", ox).attr("y", oy - 16).attr("text-anchor", "middle").attr("font-size", 11.5)
    .attr("font-weight", 700).attr("fill", T.ink).text(origin.label);

  // group legend
  const named = groups.filter((g) => g);
  if (named.length) {
    const lg = svg.append("g").attr("transform", `translate(${pad},${H - 16})`);
    let lx = 0;
    named.forEach((g) => {
      const gp = lg.append("g").attr("transform", `translate(${lx},0)`);
      gp.append("circle").attr("r", 5).attr("cy", -4).attr("fill", color(g));
      gp.append("text").attr("x", 10).attr("y", -1).attr("font-size", 10).attr("fill", T.inkDim).text(g);
      lx += 10 + g.length * 6.2 + 20;
    });
  }
  if (d.note)
    svg.append("text").attr("x", W - pad).attr("y", H - 16).attr("text-anchor", "end").attr("font-size", 9.5)
      .attr("fill", T.inkFaint).text(d.note);
}

// rank-bar: a ranked, component-stacked horizontal bar — makes an explainable score (e.g. travel
// rank = price + home-airport bonus + screen) visible. data: {title, subtitle?, note?, max?,
//   segments?:[{key,label}], rows:[{label, parts:[{key,value}], total?}]}
function renderRankBar(root, d) {
  const rows = (d.rows || []).map((r) => ({
    ...r,
    total: r.total != null ? r.total : (r.parts || []).reduce((s, p) => s + (p.value || 0), 0),
  }));
  rows.sort((a, b) => b.total - a.total);
  const maxTotal = d.max || d3.max([1, ...rows.map((r) => r.total)]);
  const segKeys = d.segments
    ? d.segments.map((s) => s.key)
    : Array.from(new Set(rows.flatMap((r) => (r.parts || []).map((p) => p.key))));
  const segLabel = new Map((d.segments || []).map((s) => [s.key, s.label]));
  const color = d3.scaleOrdinal().domain(segKeys).range(T.categorical);

  const labelW = 170;
  const barMax = 540;
  const rowH = 26;
  const gap = 7;
  const M = { top: 64, right: 70, bottom: 40, left: 22 };
  const width = M.left + labelW + barMax + M.right;
  const height = M.top + rows.length * (rowH + gap) + M.bottom;
  const x = d3.scaleLinear().domain([0, maxTotal]).range([0, barMax]);
  const bx = M.left + labelW;

  const svg = makeSvg(root, width, height);
  svg.append("text").attr("x", M.left).attr("y", 30).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Ranking");
  if (d.subtitle)
    svg.append("text").attr("x", M.left).attr("y", 51).attr("font-size", 12.5).attr("fill", T.inkDim)
      .text(d.subtitle);

  rows.forEach((r, i) => {
    const y = M.top + i * (rowH + gap);
    svg.append("text").attr("x", bx - 8).attr("y", y + rowH / 2 + 4).attr("text-anchor", "end")
      .attr("font-size", 11.5).attr("font-weight", 600).attr("fill", T.ink).text(truncate(r.label, 25));
    let cx = bx;
    (r.parts || []).forEach((p) => {
      const w = x(Math.max(0, p.value || 0));
      svg.append("rect").attr("x", cx).attr("y", y).attr("width", w).attr("height", rowH).attr("rx", 2)
        .attr("fill", color(p.key)).attr("fill-opacity", 0.9);
      cx += w;
    });
    svg.append("text").attr("x", cx + 6).attr("y", y + rowH / 2 + 4).attr("font-size", 11)
      .attr("font-weight", 700).attr("fill", T.inkStrong).text(Math.round(r.total * 10) / 10);
  });

  const lg = svg.append("g").attr("transform", `translate(${bx},${height - 14})`);
  let lx = 0;
  segKeys.forEach((k) => {
    const t = segLabel.get(k) || k;
    const g = lg.append("g").attr("transform", `translate(${lx},0)`);
    g.append("rect").attr("width", 11).attr("height", 11).attr("y", -10).attr("rx", 2).attr("fill", color(k));
    g.append("text").attr("x", 15).attr("y", -1).attr("font-size", 10.5).attr("fill", T.inkDim).text(t);
    lx += 15 + t.length * 6.4 + 20;
  });
  if (d.note)
    svg.append("text").attr("x", width - M.right).attr("y", height - 14).attr("text-anchor", "end")
      .attr("font-size", 9.5).attr("fill", T.inkFaint).text(d.note);
}

// calendar: a 12-cell year grid — each month density-colored (opportunity count) + listing the
// almanac items (long-weekends / games / dates). The "when to travel" planning view (pairs with the
// reference almanac). data: {title, subtitle?, note?, months:[{name, items:[{label, kind?}], score?}]}
function renderCalendar(root, d) {
  const months = d.months || [];
  const cols = 4;
  const nrows = Math.ceil((months.length || 12) / cols);
  const cellW = 204;
  const cellH = 134;
  const gapx = 12;
  const gapy = 12;
  const M = { top: 64, right: 22, bottom: 38, left: 22 };
  const width = M.left + cols * cellW + (cols - 1) * gapx + M.right;
  const height = M.top + nrows * cellH + (nrows - 1) * gapy + M.bottom;
  const scoreOf = (m) => (m.score != null ? m.score : (m.items || []).length);
  const maxScore = d3.max([1, ...months.map(scoreOf)]);
  const heat = d3.scaleSequential(d3.interpolateBlues).domain([0, maxScore * 1.3]);
  const kindColor = { holiday: "#34c759", sports: "#1f5bc4", personal: "#af52de", "mega-event": "#ff9500" };

  const svg = makeSvg(root, width, height);
  svg.append("text").attr("x", M.left).attr("y", 30).attr("font-size", 20).attr("font-weight", 700)
    .attr("fill", T.ink).text(d.title || "Year calendar");
  if (d.subtitle)
    svg.append("text").attr("x", M.left).attr("y", 51).attr("font-size", 12.5).attr("fill", T.inkDim)
      .text(d.subtitle);

  months.forEach((m, i) => {
    const x = M.left + (i % cols) * (cellW + gapx);
    const y = M.top + Math.floor(i / cols) * (cellH + gapy);
    const score = scoreOf(m);
    svg.append("rect").attr("x", x).attr("y", y).attr("width", cellW).attr("height", cellH).attr("rx", 8)
      .attr("fill", T.panel).attr("stroke", T.stroke);
    svg.append("text").attr("x", x + 9).attr("y", y + 18).attr("font-size", 12.5).attr("font-weight", 700)
      .attr("fill", T.ink).text(m.name);
    // density chip
    svg.append("rect").attr("x", x + cellW - 34).attr("y", y + 6).attr("width", 26).attr("height", 16).attr("rx", 4)
      .attr("fill", score ? heat(score) : T.panelAlt);
    svg.append("text").attr("x", x + cellW - 21).attr("y", y + 18).attr("text-anchor", "middle")
      .attr("font-size", 10).attr("font-weight", 700).attr("fill", score / maxScore > 0.6 ? T.knockout : T.inkStrong)
      .text(score);
    (m.items || []).slice(0, 6).forEach((it, k) => {
      const iy = y + 36 + k * 15;
      svg.append("circle").attr("cx", x + 10).attr("cy", iy - 3).attr("r", 3).attr("fill", kindColor[it.kind] || T.inkFaint);
      svg.append("text").attr("x", x + 18).attr("y", iy).attr("font-size", 9.3).attr("fill", T.inkStrong)
        .text(truncate(it.label, 29));
    });
    if ((m.items || []).length > 6)
      svg.append("text").attr("x", x + 18).attr("y", y + 36 + 6 * 15).attr("font-size", 9).attr("fill", T.inkFaint)
        .text(`+${m.items.length - 6} more`);
  });

  const lg = svg.append("g").attr("transform", `translate(${M.left},${height - 14})`);
  let lx = 0;
  Object.entries(kindColor).forEach(([k, c]) => {
    const g = lg.append("g").attr("transform", `translate(${lx},0)`);
    g.append("circle").attr("r", 4).attr("cy", -4).attr("fill", c);
    g.append("text").attr("x", 9).attr("y", -1).attr("font-size", 9.5).attr("fill", T.inkDim).text(k);
    lx += 9 + k.length * 6 + 16;
  });
}

// map-annotate: a base map screenshot (base64 data-URI, prepped Python-side) + fractional-coord
// overlays — numbered pins (auto-numbered in order), a route polyline, unnumbered notes, a legend
// panel auto-built from the pins, a solid title band, and an optional 0.1 coordinate grid (`grid:true`)
// to read off fractional coords. All coords are fractions (0–1) of the image w/h → resolution-stable.
function renderMapAnnotate(root, d) {
  const W = d.width, H = d.height;
  const sx = (f) => f * W, sy = (f) => f * H;
  const accent = d.accent || "#e23b3b", routeC = d.routeColor || "#1f6feb";
  const u = W / 2013; // scale factor so styling matches the calibrated reference (2013px-wide)

  const svg = root.append("svg")
    .attr("xmlns", "http://www.w3.org/2000/svg")
    .attr("viewBox", `0 0 ${W} ${H}`).attr("width", W).attr("height", H)
    .attr("font-family", "Helvetica, Arial, sans-serif");
  svg.append("image").attr("href", d.imageDataUri).attr("x", 0).attr("y", 0)
    .attr("width", W).attr("height", H);

  // halo'd text — for labels placed directly over the busy map (NOT for text on a solid band)
  const halo = (x, y, s, size, { anchor = "start", weight = "600", fill = "#11161c" } = {}) =>
    svg.append("text").attr("x", x).attr("y", y).attr("font-size", size).attr("font-weight", weight)
      .attr("text-anchor", anchor).attr("stroke", T.halo).attr("stroke-width", size * 0.18)
      .attr("paint-order", "stroke").attr("fill", fill).text(s);

  // optional coordinate grid (the coord-picker affordance: render once with grid → read off coords)
  if (d.grid) {
    for (let i = 1; i < 10; i++) {
      const gx = sx(i / 10), gy = sy(i / 10);
      svg.append("line").attr("x1", gx).attr("y1", 0).attr("x2", gx).attr("y2", H)
        .attr("stroke", "#ff00aa").attr("stroke-width", 1).attr("opacity", 0.45);
      svg.append("line").attr("x1", 0).attr("y1", gy).attr("x2", W).attr("y2", gy)
        .attr("stroke", "#ff00aa").attr("stroke-width", 1).attr("opacity", 0.45);
      halo(gx + 3, 22 * u, (i / 10).toFixed(1), 22 * u, { fill: "#b3007a" });
      halo(3, gy - 3, (i / 10).toFixed(1), 22 * u, { fill: "#b3007a" });
    }
  }

  // route — double stroke (white under, colored dashed over) for legibility on any map
  if (d.route && d.route.length) {
    const pts = d.route.map(([x, y]) => `${sx(x)},${sy(y)}`).join(" ");
    svg.append("polyline").attr("points", pts).attr("fill", "none")
      .attr("stroke", T.halo).attr("stroke-width", 11 * u).attr("stroke-linecap", "round");
    svg.append("polyline").attr("points", pts).attr("fill", "none").attr("stroke", routeC)
      .attr("stroke-width", 6 * u).attr("stroke-dasharray", `${2 * u} ${16 * u}`).attr("stroke-linecap", "round");
  }

  // title band — SOLID (≥0.9 opacity) + crisp white title, NO halo (the v2 washed-out lesson)
  const titleSize = 46 * u, subSize = 28 * u;
  const bandH = d.subtitle ? 126 * u : 78 * u;
  svg.append("rect").attr("x", 0).attr("y", 0).attr("width", W).attr("height", bandH)
    .attr("fill", "#11161c").attr("opacity", 0.93);
  if (d.title)
    svg.append("text").attr("x", 24 * u).attr("y", 56 * u).attr("font-size", titleSize)
      .attr("font-weight", 700).attr("fill", T.knockout).text(d.title);
  if (d.subtitle)
    svg.append("text").attr("x", 26 * u).attr("y", 102 * u).attr("font-size", subSize)
      .attr("fill", "#cdd5df").text(d.subtitle);

  // notes — unnumbered halo text on the map (off-map arrows, asides)
  (d.notes || []).forEach((n) => halo(sx(n.xf), sy(n.yf), n.text, 30 * u, n.fill ? { fill: n.fill } : {}));

  // pins — auto-numbered in array order; `star:true` adds a ring (the centerpiece)
  const pins = d.pins || [];
  const pinR = 27 * u, numSize = 34 * u;
  pins.forEach((p, i) => {
    const x = sx(p.xf), y = sy(p.yf);
    if (p.star)
      svg.append("circle").attr("cx", x).attr("cy", y).attr("r", pinR * 1.7).attr("fill", "none")
        .attr("stroke", accent).attr("stroke-width", 5 * u).attr("opacity", 0.9);
    svg.append("circle").attr("cx", x).attr("cy", y).attr("r", pinR)
      .attr("fill", accent).attr("stroke", T.halo).attr("stroke-width", 5 * u);
    svg.append("text").attr("x", x).attr("y", y + numSize * 0.35).attr("font-size", numSize)
      .attr("font-weight", 700).attr("text-anchor", "middle").attr("fill", T.knockout).text(i + 1);
  });

  // legend panel (bottom-left) — auto-built from pins (number → caption); the precise teaching
  const withCap = pins.filter((p) => p.caption);
  if (withCap.length) {
    const ls = 27 * u, rowH = 38 * u;
    const panelW = (d.legendWidth || 0.51) * W;
    const rows = withCap.length + (d.legendTitle ? 1 : 0);
    const top = H - 30 * u - rowH * rows;
    svg.append("rect").attr("x", 20 * u).attr("y", top - 34 * u).attr("width", panelW)
      .attr("height", rowH * rows + 30 * u).attr("rx", 14 * u).attr("fill", T.panel).attr("opacity", 0.9);
    let ry = top;
    if (d.legendTitle) {
      halo(40 * u, ry, d.legendTitle, ls, { fill: "#11161c", weight: "700" });
      ry += rowH;
    }
    pins.forEach((p, i) => {
      if (!p.caption) return;
      halo(40 * u, ry, `${i + 1}.  ${p.caption}`, ls, { fill: "#11161c", weight: "500" });
      ry += rowH;
    });
  }
}
