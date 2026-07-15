// Mirrors the bus events schema (docs/BUS.md) — lanes/kinds/severities are DATA, not enums:
// the UI renders whatever producers publish (zero finance assumptions; the OSS seam).

export interface BusEvent {
  id: number;
  created_at: string;
  producer: string;
  lane: string;
  kind: string;
  subject: string;
  title: string;
  body: string;
  payload_json: string;
  severity: string; // info | warn | alert (CHECK-constrained db-side)
  read_at: string | null;
  delivered_via: string[];
}

export interface DistinctMeta {
  lanes: string[];
  kinds: string[];
}

// Watchmen health — `hn bus watchmen --json`, rendered as the Inbox watch-floor band.
export interface WatchmanTick {
  at: string; // "HH:MM"
  state: "ran" | "missed" | "pending";
}
export interface AgentHealth {
  id: string;
  label: string;
  state: "green" | "red" | "standby";
  healthy: boolean;
  market_day: boolean;
  last_run: string | null;
  last_run_rel: string | null;
  next_run: string | null;
  runs_today: number;
  expected_by_now: number;
  missed: number;
  cadence: WatchmanTick[];
  last_flags: string | null;
}
export interface WatchmenStatus {
  as_of: string;
  overall: "green" | "red" | "standby";
  agents: AgentHealth[];
}

export interface Producer {
  id: string;
  label: string;
  cmd: string;
  args: string[];
  cwd: string;
}

// A lane information surface: config-registered JSON-emitting read-only command
// the app runs on demand and renders generically.
export interface Surface {
  id: string;
  label: string;
  lane: string;
  cmd: string;
  args: string[];
  cwd: string;
}

export interface AppConfig {
  db_path: string;
  /** What the Inbox actually reads: "remote: <bus_url>" in remote mode, else the local db path. */
  bus_source: string;
  producers: Producer[];
}

export type SurfaceState =
  | { status: "idle" }
  | { status: "running"; startedAt: Date }
  | { status: "ok"; data: unknown; at: Date; tookSecs: number; cached?: boolean; refreshing?: boolean }
  | { status: "error"; message: string; at: Date };

// Vault-discovered viz data entry — shape-sniffed; supported grows per phase.
export interface VizEntry {
  path: string;
  doc: string;
  name: string;
  viz_type: string;
  title: string;
  supported: boolean;
}

// A browsable vault entry — a markdown doc OR a standalone image.
export interface VaultDoc {
  path: string;  // vault-relative (read key)
  area: string;  // first path segment — tree groups by this
  dir: string;   // containing dir, vault-relative — sub-group within area
  name: string;  // file stem
  title: string; // doc: first H1, else stem · image: the filename
  kind: "doc" | "image";
}

// Domain dashboards — config'd widget grids; sources resolved Rust-side except bus.
export type WidgetSource =
  | { type: "command"; cmd: string; args: string[]; cwd: string }
  | { type: "file"; path: string }
  | { type: "bus"; lane?: string; limit?: number };

// Broad-market wire (`hn finance wire --json`) — the Finance ▸ News reader's data.
export interface NewsItem {
  symbol: string;
  title: string;
  url: string;
  source: string;
  published: string;
  summary: string; // RSS body (content:encoded else description), raw/HTML; "" when headline-only
  category: string; // "markets" | "geopolitics" | "thesis" (feeds.yaml) — drives the chips
  holdings_hit: string[]; // held/watchlist symbols named in the title — the relevance badge + "my book" filter
}
export interface WireDigest {
  items: NewsItem[];
  sources_read: string[];
  notes: string[];
}

export interface Widget {
  id: string;
  title: string;
  kind: string; // stat | table | viz | feed | news
  source: WidgetSource;
  refresh: string; // manual | market10m | local60s
  span: number;
  value_path?: string | null;
  prefix?: string | null;
  suffix?: string | null;
  // stat tiles: sign-format (+/pos-neg color). Defaults ON for "%" tiles (the day-change
  // convention); set false for magnitude reads like %-complete where "+" is noise.
  signed?: boolean;
  title_path?: string | null; // dot-path whose value renders accented in the title (e.g. weather city)
  symbols: string[];
  columns?: string[]; // table widgets: explicit column subset/order (else first-8 auto-derived)
  rows?: number; // grid-row span — a tall centerpiece panel (default 1)
  // Dashboard Studio: explicit grid placement in grid units (x = col start,
  // y = row start, w/h = spans). Absent = legacy span/rows dense-flow placement. Mirrors the rust
  // Layout struct — a field here MUST exist in dash.rs serde too (the two-parsers contract).
  layout?: WidgetLayout | null;
}

// The explicit-placement contract (Dashboard Studio). Grid units, not pixels.
export interface WidgetLayout {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Dashboard {
  lane: string;
  title: string;
  group?: string; // nav grouping — dashboards sharing a group render as subtabs under it
  // Dashboard Studio: "default" (seeded, safe to migrate) | "user" (Studio-edited — never reseeded).
  owner?: string;
  widgets: Widget[];
}

// One markdown doc inside a vault dir — the doc-series (take history) list item.
export interface DirDoc {
  path: string;  // vault-relative read key (→ readDoc)
  name: string;  // file stem (timestamped → sorts chronologically)
  title: string; // first H1, else stem
}
