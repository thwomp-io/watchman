import type { Dashboard, Widget, WidgetSource } from "../types";

interface MockTauri {
  /** Stage a handler invoked with the command's args (for arg-dependent responses). */
  set(cmd: string, handler: (args: Record<string, unknown>) => unknown): void;
  /** Stage a fixed response for a command. */
  setValue(cmd: string, value: unknown): void;
  /** Clear all staged handlers (call in beforeEach). */
  reset(): void;
}

// The IPC bridge stood up in src/test/setup.ts (a setupFile, run before the test module graph). Typed
// accessor so tests stage fixtures ergonomically.
export const mockTauri = (globalThis as unknown as { __mockTauri: MockTauri }).__mockTauri;

// Stage the read-only widget-source commands a Dash render fans out to, so widgets resolve to empty
// instead of throwing — keeps a layout/nav test focused on the tabs, not the widget bodies.
export function stageEmptyWidgetSources(): void {
  mockTauri.setValue("run_widget", "{}");
  mockTauri.setValue("list_events", []);
  mockTauri.setValue("list_vault_dir", []);
  mockTauri.setValue("read_doc", "");
}

const cmd = (...args: string[]): WidgetSource => ({
  type: "command",
  cmd: "uv",
  args: ["run", "hn", ...args],
  cwd: ".",
});

export function statWidget(id: string, title: string): Widget {
  return {
    id,
    title,
    kind: "stat",
    source: cmd("finance", "networth", "--json"),
    refresh: "manual",
    span: 1,
    value_path: "total",
    symbols: [],
  };
}

export function dashboard(lane: string, group: string, title: string): Dashboard {
  return { lane, group, title, widgets: [statWidget(`${lane}-stat`, `${title} stat`)] };
}

// A symbol-parameterized chart widget (the bars / position-chart shape) — carries a symbol pill set.
export function symbolWidget(id: string, symbols: string[]): Widget {
  return {
    id,
    title: `${id} chart`,
    kind: "viz",
    source: cmd("finance", "bars", "--json"),
    refresh: "manual",
    span: 2,
    value_path: null,
    symbols,
  };
}

// A doc-series widget (the openings-scan / market-take shape) — reads a vault dir, newest-first.
export function docSeriesWidget(id: string, path: string): Widget {
  return {
    id,
    title: `${id} series`,
    kind: "doc_series",
    source: { type: "file", path },
    refresh: "manual",
    span: 2,
    value_path: null,
    symbols: [],
  };
}

// A single-dashboard layout carrying one arbitrary widget (for focused widget-behavior tests).
export function oneWidgetDash(lane: string, group: string, title: string, widget: Widget): Dashboard {
  return { lane, group, title, widgets: [widget] };
}

// The maintainer's real console: 5 Finance subtabs (Core + the campaign/observability tabs) + Career + Travel.
export function realFixture(): Dashboard[] {
  return [
    dashboard("finance", "Finance", "Core"),
    dashboard("unwind", "Finance", "Unwind"),
    dashboard("market", "Finance", "Market"),
    dashboard("tickets", "Finance", "Tickets"),
    dashboard("compare", "Finance", "Compare"),
    dashboard("career", "Career", "Board"),
    dashboard("travel", "Travel", "Command-center"),
  ];
}

// A weight pack that DESCRIBES a curated console (v2) — one dashboard per group, dropping the
// maintainer-specific Finance subtabs. Loading it should replace the whole tab-set (full-set override).
export function packFixture(): Dashboard[] {
  return [
    dashboard("finance", "Finance", "Core"),
    dashboard("career", "Career", "Board"),
    dashboard("travel", "Travel", "Command-center"),
  ];
}
