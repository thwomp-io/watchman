// GENERATED from src/harness/console/contracts.py by scripts/gen-types.mjs — do not edit.

export interface Contracts {
BarsReport?: BarsReport
CampaignReport?: CampaignReport
CorrelationReport?: CorrelationReport
GateItem?: GateItem
GatesBoard?: GatesBoard
GlobalQuote?: GlobalQuote
PlanningCalendar?: PlanningCalendar
PocketSnapshot?: PocketSnapshot
RolesReport?: RolesReport
TripBanks?: TripBanks
VisitBank?: VisitBank
VisitSchedule?: VisitSchedule
}
/**
 * `hn finance bars --json`: daily bars + the clustered levels — the price chart's input.
 */
export interface BarsReport {
as_of: (string | null)
bars: Bar[]
days: number
last_close: (number | null)
levels: SupportLevel[]
notes: string[]
sessions: number
symbol: string
}
/**
 * A single OHLCV bar — the chart-able unit.
 */
export interface Bar {
c: number
h: number
l: number
o: number
t: string
v: number
}
/**
 * A clustered swing-low level (see `harness.finance.levels`) — an observation of past
 * price behavior, never a prediction.
 */
export interface SupportLevel {
distance_pct: number
kind: string
last_touch: string
level: number
touches: number
}
export interface CampaignReport {
as_of: string
calendar: Milestone[]
calendar_notes: string[]
in_flight: InFlight[]
mail: {
[k: string]: unknown
}[]
mail_note: string
stale_days: number
}
export interface Milestone {
approx: boolean
date: string
days: number
kind: string
label: string
ref: string
time: string
}
export interface InFlight {
channel: string
comp_signal: string
company: string
days_since: (number | null)
drift: boolean
last_touch: string
location: string
next_date: (string | null)
next_step: string
next_time: string
ref: string
role: string
screen_line: string
stage: string
stale: boolean
url: string
}
/**
 * Daily-return correlation across holdings — the 'is this name a real diversifier?' hard-data
 * surface. Deterministic FACTS (Pearson correlations / beta / dispersion); the interpretation is the
 * agent's narrative, never computed here (the market.build_overview doctrine).
 */
export interface CorrelationReport {
days: number
divergence_days: (DivergenceDay[] | null)
end: string
factor: (string[] | null)
factor_beta: ({
[k: string]: number
} | null)
factor_corr: ({
[k: string]: number
} | null)
matrix: number[][]
n_obs: number
notes: string[]
start: string
symbols: string[]
vol_annual: {
[k: string]: number
}
}
/**
 * A day the focal name's return diverged most from the factor basket — the 'what moved on its
 * own' evidence. Sign-coded gap (focal − factor): positive = focal up while the factor lagged.
 */
export interface DivergenceDay {
date: string
factor_ret_pct: number
focal: string
focal_ret_pct: number
gap_pct: number
members: {
[k: string]: number
}
}
/**
 * One upcoming binary on the plan's calendar — a held-name print, a macro event, or an RSU
 * vest of the concentrated holding (a vest re-arms the wash window + fires the
 * sell-at-vest pattern, so it ranks on the same countdown).
 *
 * The structured twin of what pulse's print_soon/macro_soon flags carry as prose: the
 * gates board renders these as countdown bars, so every
 * field a bar needs is first-class here instead of parsed out of a message string.
 */
export interface GateItem {
confirmed: boolean
date: (string | null)
days: (number | null)
kind: string
label: string
symbol: (string | null)
}
/**
 * The plan-gates contract — upcoming prints + macro events inside the horizon, sorted
 * imminent-first. `gates` is the viz sniff signature (disjoint from every other shape).
 */
export interface GatesBoard {
as_of: string
gates: GateItem[]
horizon_days: number
notes: string[]
}
/**
 * One rail card: last price + day change vs previous close, with venue state/time.
 *
 * Futures rows also name the DATED contract they quote (see futures_calendar): `symbol` stays
 * the rail's continuous key (`BZ=F` — scalars and dashboards key on it), `contract` is what was
 * actually fetched (`BZX26.NYM`), `contract_month` is the printed month ('Nov 26'), and on a roll
 * day `rolled_from` names the month the rule left. `contract_month` None on a future = the dated
 * symbol was unavailable and the row fell back to Yahoo's continuous alias — the notes say so.
 */
export interface GlobalQuote {
change: (number | null)
change_pct: (number | null)
contract: (string | null)
contract_month: (string | null)
live: boolean
market_state: string
market_time: (number | null)
name: string
price: (number | null)
roll_day: boolean
rolled_from: (string | null)
session_date: (string | null)
session_label: string
session_note: string
symbol: string
}
/**
 * `hn travel upcoming --json`: the Planning tab's top band. `upcoming` = live and dated,
 * soonest first; `someday` = live-but-undated plus parked ideas — listed, never counted down.
 */
export interface PlanningCalendar {
as_of: string
someday: CalendarCard[]
upcoming: CalendarCard[]
}
/**
 * One Planning "on the calendar" card: a live (or parked) trip or visit at a glance.
 */
export interface CalendarCard {
anchor: string
cover: (string | null)
days: (number | null)
decision: boolean
destination: string
doc: string
end: (string | null)
kind: string
name: string
slug: string
start: (string | null)
status: string
title: string
when: string
who: string[]
window: string
}
export interface PocketSnapshot {
entries: PocketEntry[]
live: string
online: boolean
queued: PocketQueued[]
status: PocketStatus
}
export interface PocketEntry {
id: (string | null)
ok: (boolean | null)
role: string
seq: number
text: string
tool: (string | null)
truncated: (boolean | null)
ts: string
}
export interface PocketQueued {
id: string
text: string
ts: string
}
export interface PocketStatus {
actor: (string | null)
branch: (string | null)
claude_version: (string | null)
context_max: (number | null)
context_used: (number | null)
cost_usd: (number | null)
cwd: (string | null)
duration_ms: (number | null)
effort: (string | null)
harness_version: (string | null)
limit_status: (string | null)
limits: {
[k: string]: PocketLimit
}
mcp: {
[k: string]: string
}
mode: (string | null)
model: (string | null)
seal: PocketSeal
session_id: (string | null)
started_at: (string | null)
state: (string | null)
turn_cost_usd: (number | null)
turn_started_at: (string | null)
turns: (number | null)
updated_at: (string | null)
}
export interface PocketLimit {
resets_at: (number | null)
utilization: (number | null)
}
export interface PocketSeal {
checked_at: (string | null)
failures: string[]
ok: boolean
}
/**
 * `hn career roles --json` — the Openings tab.
 */
export interface RolesReport {
anchor: number
as_of: string
bands: {
[k: string]: number
}
companies: RoleCompany[]
constitution: string
counts: RoleCounts
filters: RoleFilters
rows: RoleItem[]
screen_gated: RoleItem[]
}
export interface RoleCompany {
company: string
new: number
on_profile: number
openings: number
slug: string
}
export interface RoleCounts {
fit: {
[k: string]: number
}
new: number
shown: number
total: number
}
export interface RoleFilters {
company: string
location: string
new: boolean
}
export interface RoleItem {
base_ilvl: (number | null)
base_source: string
comp: (CompEstimate | null)
company: string
company_ilvl: (number | null)
company_median_tc: (number | null)
company_mods: number
company_rarity: (string | null)
days_old: (number | null)
doc: (string | null)
final_ilvl: (number | null)
first_seen: string
fit: string
lane: string
level: string
location: string
location_class: string
modifier_sum: number
modifiers: Modifier[]
new: boolean
profile: (string | null)
rarity: string
salary: string
screen_flag: string
screened_out: boolean
shape: string
slug: string
title: string
updated: string
url: string
}
/**
 * The role's likely level on its company's levels.fyi ladder and that rung's median TC, with the
 * neighboring rungs' medians as the low/high edges (levels.fyi publishes medians per level, not
 * percentiles). `basis` says how the rung was chosen; `note` names any fallback.
 */
export interface CompEstimate {
attribution: string
band: string
basis: string
high_level: (string | null)
high_tc: (number | null)
ilvl: (number | null)
level: string
location: string
low_level: (string | null)
low_tc: (number | null)
median_tc: number
note: string
}
export interface Modifier {
family: string
how: string
key: string
name: string
source: string
value: number
}
/**
 * `hn travel banks --json`: the Visits/Trips picker.
 */
export interface TripBanks {
default: (string | null)
entries: TripEntry[]
scope: string
}
/**
 * One picker tile: a visit or trip folder and what it carries.
 */
export interface TripEntry {
cover: (string | null)
date: string
destination: string
doc: string
end: (string | null)
kind: string
live: boolean
outings: number
phase: ("live" | "someday" | "past")
photos: number
restaurants: number
scheduled: number
slug: string
start: (string | null)
status: string
title: string
when: string
}
/**
 * `hn travel bank --json`: the resolved visit and its merged bank.
 */
export interface VisitBank {
doc: string
end: (string | null)
groups: BankGroup[]
items: BankItem[]
notes: string[]
outings_title: string
restaurants_title: string
start: (string | null)
title: string
visit: string
}
export interface BankGroup {
count: number
key: string
kind: ("outing" | "restaurant")
label: string
}
/**
 * One tile: an outing or a restaurant, normalized to a shared shape.
 */
export interface BankItem {
area: (string | null)
confirm: (string | null)
cuisine: (string | null)
drive_min: (number | null)
extra: {
[k: string]: unknown
}
gallery: boolean
gallery_from: (string | null)
group: string
group_label: string
hours: string[]
id: string
image: (string | null)
images: string[]
indoor: (boolean | null)
kind: ("outing" | "restaurant")
label: string
lane: (string | null)
lat: (number | null)
links: {
[k: string]: string
}
lon: (number | null)
meals: string[]
note: (string | null)
phone: (string | null)
place_id: (string | null)
price: (string | null)
rating: (number | null)
reviews: (number | null)
season: (string | null)
status: (string | null)
why: (string | null)
yelp_rating: (number | null)
}
/**
 * `hn travel schedule --json`: the resolved visit, a column per date, and its bank.
 */
export interface VisitSchedule {
availability_groups: AvailabilityGroup[]
bank: VisitBank
day_end: string
day_start: string
days: ScheduleDay[]
doc: string
end: (string | null)
evening_start: (string | null)
marker_cats: MarkerCat[]
marker_label: string
precip_unit: string
start: (string | null)
subtitle: string
temp_unit: string
title: string
visit: string
weather_note: string
weather_place: string
}
export interface AvailabilityGroup {
color: (string | null)
key: string
label: string
}
export interface ScheduleDay {
availability: string[]
date: string
items: ScheduleItem[]
markers: Marker[]
weather: (DayWeather | null)
weekday: string
}
export interface ScheduleItem {
bank_id: (string | null)
bank_match: (("explicit" | "label") | null)
date: string
end: (string | null)
label: string
lane: (string | null)
note: (string | null)
penciled: boolean
start: (string | null)
}
export interface Marker {
cat: (string | null)
date: string
label: string
time: (string | null)
}
export interface DayWeather {
condition: string
high: (number | null)
low: (number | null)
precip_hours: (number | null)
precip_prob: (number | null)
precip_sum: (number | null)
wet: boolean
}
export interface MarkerCat {
color: (string | null)
key: string
label: string
}
