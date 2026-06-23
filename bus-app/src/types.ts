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
  state: "green" | "red";
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
  overall: "green" | "red";
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

export interface Widget {
  id: string;
  title: string;
  kind: string; // stat | table | viz | feed
  source: WidgetSource;
  refresh: string; // manual | market10m | local60s
  span: number;
  value_path?: string | null;
  prefix?: string | null;
  suffix?: string | null;
  title_path?: string | null; // dot-path whose value renders accented in the title (e.g. weather city)
  symbols: string[];
  columns?: string[]; // table widgets: explicit column subset/order (else first-8 auto-derived)
  rows?: number; // grid-row span — a tall centerpiece panel (default 1)
}

export interface Dashboard {
  lane: string;
  title: string;
  group?: string; // nav grouping — dashboards sharing a group render as subtabs under it
  widgets: Widget[];
}

// One markdown doc inside a vault dir — the doc-series (take history) list item.
export interface DirDoc {
  path: string;  // vault-relative read key (→ readDoc)
  name: string;  // file stem (timestamped → sorts chronologically)
  title: string; // first H1, else stem
}
