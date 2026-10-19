"""The ladder feed — the standing, PACED refresh of the comp cache.

`hn career comp --refresh-stale [--limit N] [--pool] [--dry-run] [--notify]`. The Ladders tab only
holds what `hn career comp <company>` has fetched; without this verb that is a manual fetch. This makes it
a standing feed with a hard per-run cap, so the cache grows and stays fresh on its own while
levels.fyi is never burst. The cap's default is 40, sized from a clean paced run at the provider's
2s pacing; pacing + stop-on-throttle are the real guards, the cap is the ceiling.

The plan, in order (the first `limit` are this run's; the rest wait for the next):
  1. STALE   — cached ladders past the TTL for the feed's `{family}/{location}` key, oldest first.
  2. WATCHLIST — watchlist companies with no ladder for that key, in watchlist order (the curated
     top-down list IS the priority); a name the slug index cannot resolve exactly is SKIPPED with
     the `hn career slugs` pointer, never guessed into a paced 404.
  3. POOL (`--pool`) — companies in the newest persisted pool survey per cut (in-play rows only —
     the screen-gated pane gets no fetch budget) with no ladder. Survey rows only; the watchlist is
     never written.
One company per fetch through `comp.get_ladder` (the provider's own 2s min-interval paces the run;
the feed adds no second throttle). The run STOPS on the first throttle-shaped refusal — a 429, a
403, or the site's empty-page answer — logs it by name and never retries inside the same run; a
404 (a slug the site does not know) is logged and the run continues.

Every candidate writes one line to `role-hunt/comp/feed-log.jsonl` (`ts · slug · action
fetched|skipped|error · reason`) — the did-it-run audit, next to the cache it feeds. `--notify`
publishes ONE bus `info` event with the counts ("ladders: 3 refreshed, 2 new, 33 fresh"); a run with
nothing to do publishes nothing (the pacing budget is the only thing that costs; silence is fine).
A weekly scheduled run (e.g. a launchd timer) runs it with
the default cap; launchd does not catch up after a dark week, by design.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from harness.career import comp as compmod
from harness.career.pool import pool_dir
from harness.career.watchlist import load_watchlist
from harness.errors import ProviderError
from harness.settings import overlay_get

LANE = "career"
PRODUCER = "ladders"
KIND = "ladder_feed"
LOG_NAME = "feed-log.jsonl"
DEFAULT_LIMIT = 40

Reason = Literal["stale", "watchlist", "pool"]
Action = Literal["fetched", "skipped", "error"]


class Candidate(BaseModel):
    slug: str
    company: str
    reason: Reason
    detail: str = ""  # "fetched 2026-09-01 (8d)" / "tier: core" / "staff · US · #12"


class Skipped(BaseModel):
    """A name the plan could not turn into a fetch — reported, never guessed."""

    company: str
    reason: str


class FeedPlan(BaseModel):
    family: str
    location: str
    limit: int
    ttl_days: int
    candidates: list[Candidate] = Field(default_factory=list)  # every candidate, in feed order
    unresolved: list[Skipped] = Field(default_factory=list)
    fresh: int = 0  # cached ladders inside the TTL for this key (the "N fresh" of the summary)

    @property
    def selected(self) -> list[Candidate]:
        return self.candidates[: self.limit]

    @property
    def deferred(self) -> list[Candidate]:
        return self.candidates[self.limit :]


class FeedEntry(BaseModel):
    ts: str
    slug: str
    action: Action
    reason: str


class FeedResult(BaseModel):
    plan: FeedPlan
    entries: list[FeedEntry] = Field(default_factory=list)
    stopped: str | None = None  # the throttle-shaped refusal that ended the run, verbatim

    @property
    def refreshed(self) -> int:
        return sum(1 for e in self.entries if e.action == "fetched" and e.reason == "stale")

    @property
    def new(self) -> int:
        return sum(1 for e in self.entries if e.action == "fetched" and e.reason != "stale")

    @property
    def errors(self) -> int:
        return sum(1 for e in self.entries if e.action == "error")

    def summary(self) -> str:
        s = f"ladders: {self.refreshed} refreshed, {self.new} new, {self.plan.fresh} fresh"
        if self.errors:
            s += f", {self.errors} error{'s' if self.errors != 1 else ''}"
        if self.stopped:
            s += " · stopped on a throttle"
        return s


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def feed_limit() -> int:
    """The per-run cap: `career.levels.feed_limit` in the operator's overlay, else 40 (the proven batch)."""
    raw = overlay_get("career", "levels", "feed_limit", default=DEFAULT_LIMIT)
    try:
        n = int(str(raw))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return n if n > 0 else DEFAULT_LIMIT


def log_path(role_hunt: Path) -> Path:
    return compmod.comp_dir(role_hunt) / LOG_NAME


def _age_days(stamp: str, now: datetime) -> int | None:
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (now - then).days


def _cached_for_key(role_hunt: Path, key: str) -> dict[str, dict[str, Any]]:
    """slug → the cached ladder entry for `key` (every cache file, readable ones only)."""
    out: dict[str, dict[str, Any]] = {}
    d = compmod.comp_dir(role_hunt)
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        entry = (raw.get("ladders") or {}).get(key)
        if isinstance(entry, dict):
            out[str(raw.get("slug") or p.stem)] = entry
    return out


def _newest_survey_rows(role_hunt: Path) -> list[tuple[str, str, str]]:
    """(slug, company, detail) for the in-play rows of the newest survey per cut key — the same
    newest-per-cut rule `pool --survey` merges by."""
    d = pool_dir(role_hunt)
    if not d.exists():
        return []
    newest: dict[str, Path] = {}
    for p in sorted(d.glob("*.json")):
        newest[p.stem.split("-", 3)[-1]] = p
    rows: list[tuple[str, str, str]] = []
    for p in newest.values():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        cut = data.get("cut") or {}
        level = str(cut.get("level", "")).lower().split("-")[0] or "all"
        geo = str(cut.get("geo", "")).replace("-", " ")
        for r in data.get("rows", []):
            slug, company = str(r.get("slug", "")), str(r.get("company", ""))
            if slug:
                rows.append((slug, company or slug, f"{level} · {geo} · #{r.get('rank', '?')}"))
    return rows


def _index_candidates(role_hunt: Path, company: str) -> list[str]:
    """The slug index's nearest candidates for a name (empty without an index)."""
    from harness.career.levels_index import load_index, resolve_slug_in_index

    try:
        index = load_index(role_hunt)
    except ProviderError:
        return []
    if index is None:
        return []
    hit, candidates = resolve_slug_in_index(index, company)
    return [hit] if hit else list(candidates)


def plan_feed(
    role_hunt: Path,
    tracker_path: Path,
    *,
    limit: int | None = None,
    include_pool: bool = False,
    family: str = compmod.DEFAULT_FAMILY,
    location: str = compmod.DEFAULT_LOCATION,
    ttl_days: int = compmod.TTL_DAYS,
    now: datetime | None = None,
) -> FeedPlan:
    """The ordered candidate list (stale-oldest → watchlist-without-ladder → pool rows) + the fresh
    count. Pure over the disk (cache files, watchlist, surveys, the slug index); no network."""
    now = now or _now()
    key = f"{family}/{location}"
    plan = FeedPlan(family=family, location=location, limit=limit or feed_limit(), ttl_days=ttl_days)
    cached = _cached_for_key(role_hunt, key)
    seen: set[str] = set()

    # (1) stale, oldest first
    stale: list[tuple[int, Candidate]] = []
    for slug, entry in cached.items():
        stamp = str(entry.get("fetched_at", ""))
        age = _age_days(stamp, now)
        if age is None or age >= ttl_days:
            detail = f"fetched {stamp[:10] or 'unknown'} ({age}d)" if age is not None else "unstamped"
            company = str(entry.get("company") or slug)
            cand = Candidate(slug=slug, company=company, reason="stale", detail=detail)
            stale.append((age if age is not None else 10**6, cand))
        else:
            plan.fresh += 1
    for _, cand in sorted(stale, key=lambda t: -t[0]):
        plan.candidates.append(cand)
        seen.add(cand.slug)

    # (2) watchlist companies with no ladder for this key
    try:
        wl = load_watchlist(tracker_path, root=role_hunt)
    except ProviderError as e:
        plan.unresolved.append(Skipped(company="(watchlist)", reason=str(e)))
        wl = None
    if wl is not None:
        for wc in wl.companies:
            try:
                slug = compmod.resolve_slug(role_hunt, wc.name)
            except ProviderError as e:
                # The index has no exact hit — but a manual fetch may have cached this company by its
                # site slug already (e.g. Example Labs → `example`, Acme Robotics → `acme-robotics`): a cached
                # nearest candidate means COVERED, not unresolved.
                covered = next((c for c in _index_candidates(role_hunt, wc.name) if c in cached), None)
                if covered is None:
                    plan.unresolved.append(Skipped(company=wc.name, reason=str(e)))
                continue
            if slug in seen or slug in cached:
                continue
            seen.add(slug)
            plan.candidates.append(
                Candidate(slug=slug, company=wc.name, reason="watchlist", detail=f"tier: {wc.tier or '—'}")
            )

    # (3) the newest survey per cut, in-play rows only
    if include_pool:
        for slug, company, detail in _newest_survey_rows(role_hunt):
            if slug in seen or slug in cached:
                continue
            seen.add(slug)
            plan.candidates.append(Candidate(slug=slug, company=company, reason="pool", detail=detail))
    return plan


_THROTTLE_TELLS = ("429", "403", "empty page", "too many requests", "rate limit")


def is_throttle(err: BaseException) -> bool:
    """A refusal that means 'the site is saying stop' — the run ends on it, never retries."""
    msg = str(err).lower()
    return any(t in msg for t in _THROTTLE_TELLS)


def _append_log(path: Path, entry: FeedEntry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry.model_dump()) + "\n")


def run_feed(
    role_hunt: Path,
    plan: FeedPlan,
    *,
    fetch: Callable[[str], str] | None = None,
    log: Path | None = None,
    now: datetime | None = None,
) -> FeedResult:
    """Fetch the plan's selected candidates in order, one paced page each, logging every outcome.
    `fetch` resolves at call time inside `get_ladder` (a test replaces the provider; the real
    provider's min-interval is the pacing). Stops on the first throttle-shaped refusal."""
    now = now or _now()
    log = log or log_path(role_hunt)
    res = FeedResult(plan=plan)
    stopped = False
    for cand in plan.selected:
        ts = now.isoformat()
        if stopped:
            entry = FeedEntry(ts=ts, slug=cand.slug, action="skipped", reason="stopped after a throttle")
        else:
            try:
                compmod.get_ladder(
                    role_hunt,
                    cand.company,
                    family=plan.family,
                    location=plan.location,
                    refresh=True,
                    fetch=fetch,
                    slug=cand.slug,
                )
                entry = FeedEntry(ts=ts, slug=cand.slug, action="fetched", reason=cand.reason)
            except ProviderError as e:
                entry = FeedEntry(ts=ts, slug=cand.slug, action="error", reason=str(e)[:400])
                if is_throttle(e):
                    res.stopped = str(e)[:400]
                    stopped = True
        res.entries.append(entry)
        _append_log(log, entry)
    return res


def publish_summary(res: FeedResult, *, stamp: str | None = None) -> bool:
    """ONE bus `info` event per run that did something (a fetch, an error, or a stop); a run whose
    plan was empty publishes nothing. Returns whether an event was published."""
    if not res.entries:
        return False
    from harness.bus.models import EventDraft
    from harness.bus.service import BusService

    stamp = stamp or _now().strftime("%Y%m%dT%H%M%SZ")
    fetched = [e.slug for e in res.entries if e.action == "fetched"]
    errored = [f"{e.slug}: {e.reason}" for e in res.entries if e.action == "error"]
    body_lines = []
    if fetched:
        body_lines.append("fetched: " + ", ".join(fetched))
    if errored:
        body_lines.append("errors: " + " · ".join(errored))
    if res.stopped:
        body_lines.append(f"stopped: {res.stopped}")
    deferred = len(res.plan.deferred)
    if deferred:
        body_lines.append(f"{deferred} more wait for the next run (cap {res.plan.limit})")
    BusService().publish(
        EventDraft(
            producer=PRODUCER,
            lane=LANE,
            kind=KIND,
            subject="ladders",
            title=res.summary(),
            body="\n".join(body_lines),
            payload={
                "refreshed": res.refreshed,
                "new": res.new,
                "fresh": res.plan.fresh,
                "errors": res.errors,
                "deferred": deferred,
                "stopped": res.stopped,
                "fetched": fetched,
            },
            severity="info",
            idempotency_key=f"{PRODUCER}:{KIND}:{stamp}",
        )
    )
    return True
