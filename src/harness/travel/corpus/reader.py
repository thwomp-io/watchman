"""Read the tracker travel corpus.

Parses ONLY the reliably-structured bits (frontmatter, H1, Trip-shape line, Lodging-anchor
bullets, trip candidate-table wikilinks). Screen verdicts + gateway IATAs come from the
config seed (weights.yaml), NOT parsed from the prose (the screen-status table format varies
between clean + calibrated destination docs). Honors the hybrid-config principle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from harness.corpus import load_doc
from harness.corpus import section as _section
from harness.corpus import split_sections as _split_sections
from harness.travel.config.settings import Settings, get_settings
from harness.travel.models import Candidate, ScreenStatus, Trip
from harness.travel.ranking.weights import WeightConfig, load_weights

_BULLET_BOLD = re.compile(r"^\s*[-*]\s*\*\*(.+?)\*\*")
# Capture the trailing slug of a destination wikilink, tolerating (a) any depth of leading `../`
# (a flat trip doc uses `../destinations/...`; a folder-note trip uses `../../destinations/...`) and
# (b) optional archetype path segments (`[[../destinations/beach-coastal/san-diego]]`). Trip docs keep
# the category-free form by convention, so a recategorization never edits them.
_WIKILINK_DEST = re.compile(r"\[\[(?:\.\./)+destinations/(?:[a-z0-9-]+/)*([a-z0-9-]+)")
_DATES = re.compile(r"(\d{1,2})/(\d{1,2}).*?(\d{1,2})/(\d{1,2})")
_YEAR_TAG = re.compile(r"(20\d\d)-(\d{2})")
# Leading date-prefix on a trip/visit filename (e.g. "2025-05-city-weekend", "YYYY-MM-DD-event") — the
# date-of-record when a doc carries no explicit `start` (past trips known only to month precision).
_SLUG_DATE = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?")
_FAR_FUTURE = date(2099, 12, 31)


def _coerce_date(v: object) -> date | None:
    """YAML auto-parses `start: 2026-01-15` to a `date`; tolerate str/datetime too (and junk → None)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v).strip())
    except ValueError:
        return None


def _date_from_slug(stem: str) -> date | None:
    m = _SLUG_DATE.match(stem)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _resolve_folder_note(base: Path, slug: str) -> Path:
    """Resolve a corpus doc that may be a **folder-note** (`{slug}/{slug}.md`, at any depth — flat or
    nested under region/archetype subfolders) OR a legacy **flat file** (`{slug}.md`). Returns the
    folder-note path even when it doesn't exist, so the caller's load surfaces a clear error. Shared by
    destinations + trips (folder-notes are the common shape now — flat is legacy/fixtures)."""
    matches = sorted(base.glob(f"**/{slug}/{slug}.md"))
    if matches:
        return matches[0]
    if (base / f"{slug}.md").exists():
        return base / f"{slug}.md"
    return base / slug / f"{slug}.md"


@dataclass
class DestinationDoc:
    slug: str
    display_name: str
    shape: str = ""
    lodging_anchors: list[str] = field(default_factory=list)


@dataclass
class PreferencesDigest:
    avoid_airline_names: list[str]
    lodging_bar: str  # relative — "best-in-class for the area", not an absolute star floor
    home_airport_preferred: bool
    raw_text: str


@dataclass
class TripPlan:
    slug: str
    window: str
    depart: date
    return_: date | None
    candidates: list[Candidate]


class CorpusReader:
    def __init__(self, settings: Settings | None = None, weights: WeightConfig | None = None):
        self.settings = settings or get_settings()
        self.weights = weights or load_weights()

    @property
    def _root(self) -> Path:
        return self.settings.travel_corpus_path

    # ---- preferences ----
    def read_preferences(self) -> PreferencesDigest:
        text = (self._root / "preferences.md").read_text()
        avoid: list[str] = []
        # budget-carrier names mentioned in the user's preferences prose are picked up as an
        # avoid-list. The vocabulary below is a generic seed of the major US budget/LCC carriers —
        # the parser recognizes any of them named in prose; the machine-readable avoid list proper
        # lives in weights.yaml `airline_avoid_iata` (this prose pass is the drift-canary's input).
        for name in ("Southwest", "Frontier", "Spirit", "Allegiant", "Sun Country", "Avelo"):
            if name in text:
                avoid.append(name)
        return PreferencesDigest(
            avoid_airline_names=avoid,
            lodging_bar="best-in-class for the area",
            home_airport_preferred=bool(self.weights.flight.home_airport)
            and self.weights.flight.home_airport in text,
            raw_text=text,
        )

    # ---- destination ----
    def read_destination(self, slug: str) -> DestinationDoc:
        # Slug-location-agnostic: resolve the folder-note wherever filed (flat or region/archetype-nested),
        # so recategorizing a destination is a pure file move — no code/weights change.
        path = _resolve_folder_note(self._root / "destinations", slug)
        _meta, body = load_doc(path)
        display = next(
            (ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), slug
        )
        sections = _split_sections(body)

        shape_text = _section(sections, "Trip-shape")
        shape = ""
        for ln in shape_text.splitlines():
            if ln.strip():
                shape = ln.strip().lstrip("*").split(".")[0].strip().strip("*").strip()
                break

        anchors: list[str] = []
        for ln in _section(sections, "Lodging anchors").splitlines():
            m = _BULLET_BOLD.match(ln)
            if m:
                anchors.append(m.group(1).strip())

        return DestinationDoc(slug=slug, display_name=display, shape=shape, lodging_anchors=anchors)

    # ---- trip -> candidates ----
    def build_trip_plan(self, trip_slug: str) -> TripPlan:
        # Resolve flat (trips/{slug}.md) OR folder-note (trips/{slug}/{slug}.md) — the common shape now.
        meta, body = load_doc(_resolve_folder_note(self._root / "trips", trip_slug))
        sections = _split_sections(body)

        # Pick the first Window-section line that carries a parseable date-pair — skipping any
        # leading prose/correction-notes (which may contain stray single dates). Falls back to the
        # first non-empty line so a genuinely date-less window still yields a clear parse error.
        window = ""
        first_nonempty = ""
        for ln in _section(sections, "Window").splitlines():
            s = ln.strip().lstrip("-").strip().replace("**", "")
            if not s:
                continue
            first_nonempty = first_nonempty or s
            if _DATES.search(s):
                window = s
                break
        window = window or first_nonempty

        depart, return_ = self._parse_window_dates(window, meta.get("tags", []))

        slugs: list[str] = []
        for m in _WIKILINK_DEST.finditer(body):
            if m.group(1) not in slugs:
                slugs.append(m.group(1))

        candidates: list[Candidate] = []
        for slug in slugs:
            iata = self.weights.destination_airports.get(slug)
            if not iata:
                continue  # unknown gateway — skip in v0 (seed map); v1 reads frontmatter
            doc = self.read_destination(slug)
            screen_cfg = self.weights.destination_screens.get(slug)
            screen = ScreenStatus()
            if screen_cfg:
                screen = ScreenStatus(
                    geological=screen_cfg.geological,  # type: ignore[arg-type]
                    social_crime=screen_cfg.social_crime,  # type: ignore[arg-type]
                    calibration_notes=screen_cfg.calibration_notes,
                )
            fl = self.weights.flight
            origins = fl.query_origins(iata in fl.home_airport_served_iata)
            candidates.append(
                Candidate(
                    slug=slug,
                    display_name=doc.display_name,
                    dest_iata=iata,
                    origins=origins,
                    shape=doc.shape,
                    lodging_anchors=doc.lodging_anchors,
                    screen_status=screen,
                )
            )

        return TripPlan(
            slug=trip_slug, window=window, depart=depart, return_=return_, candidates=candidates
        )

    @staticmethod
    def _parse_window_dates(window: str, tags: list[str] | object) -> tuple[date, date | None]:
        year = 2026
        if isinstance(tags, list):
            for t in tags:
                ym = _YEAR_TAG.match(str(t))
                if ym:
                    year = int(ym.group(1))
                    break
        m = _DATES.search(window)
        if not m:
            raise ValueError(f"could not parse dates from window: {window!r}")
        dm, dd, rm, rd = (int(x) for x in m.groups())
        return date(year, dm, dd), date(year, rm, rd)

    # ---- trip/visit pipeline (the command-center spine) ----
    def scan_trips(self) -> list[Trip]:
        """Read every migrated trip/visit doc (the `trip:` frontmatter block) under
        trips/ + visits/ into Trip models. Docs without a `trip:` block are skipped (not yet
        migrated / not a trip doc). Handles BOTH flat files (`trips/2025-05-city-weekend.md`) and
        folder-notes (`trips/beach-trip/beach-trip.md`)."""
        out: list[Trip] = []
        seen: set[Path] = set()
        for sub in ("trips", "visits"):
            base = self._root / sub
            if not base.is_dir():
                continue
            paths = list(base.glob("*.md"))
            # folder-notes only (slug/slug.md) — skip aux .md so a trip is counted once
            paths += [p for p in base.glob("*/*.md") if p.stem == p.parent.name]
            for path in sorted(paths):
                if path in seen:
                    continue
                seen.add(path)
                trip = self._parse_trip_doc(path, default_kind=sub.rstrip("s"))
                if trip is not None:
                    # tracker-relative path (e.g. "travel/trips/<slug>/<slug>.md") so a dashboard row
                    # can deep-link to its corpus doc; _root is corpus/travel → .parent is the vault root.
                    trip.doc = str(path.relative_to(self._root.parent))
                    out.append(trip)
        return out

    @staticmethod
    def _parse_trip_doc(path: Path, *, default_kind: str) -> Trip | None:
        meta, body = load_doc(path)
        block = meta.get("trip")
        if not isinstance(block, dict):
            return None
        title = next(
            (ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), path.stem
        )
        start = _coerce_date(block.get("start"))
        end = _coerce_date(block.get("end")) or start
        travelers = block.get("travelers") or []
        if not isinstance(travelers, list):
            travelers = [str(travelers)]
        return Trip(
            slug=path.stem,
            kind=str(block.get("kind") or default_kind),
            status=str(block.get("status") or "planning"),
            title=title,
            destination=str(block.get("destination") or ""),
            travelers=[str(t) for t in travelers],
            anchor=str(block.get("anchor") or ""),
            start=start,
            end=end,
            window=str(block.get("window") or ""),
            sort_date=start or _date_from_slug(path.stem) or _FAR_FUTURE,
        )
