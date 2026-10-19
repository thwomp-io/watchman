"""Bounded, host-paced fan-out for the news scan.

Why: `pulse` measured 55s wall / 2.3s CPU and `watch` 40s / 2.3s — ~95% serial network wait
across 18 feeds + per-holding Yahoo + Google News + EDGAR, one `get_with_retry` at a time, nine
times a market day and behind every take. The fetches are independent; only the ORDER of the
results and the seen-cache writes need to stay deterministic.

Design:
- `run_parallel` submits every job to one bounded pool and returns results IN SUBMISSION ORDER,
  each either a value or the `ProviderError` it raised — so callers keep the exact "one dead
  source is a note, never a dead run" shape they had serially, and the digest's ordering is
  unchanged. Non-`ProviderError` exceptions propagate (as they did serially).
- Pacing is PER HOST, not per pool: `host_lane(host)` hands out a semaphore sized for that host,
  and providers wrap their request in `paced(url)`. Yahoo stays effectively SERIAL — Yahoo throttles
  per UA+IP bucket and one component's burst starved another surface (Yahoo throttles per UA+IP);
  Google News and SEC get small caps; everything else runs at the pool's width. Parallelism across
  providers is where the wall time goes; per-host discipline is what keeps the 429s away.
- The seen-cache never crosses a thread boundary: fetch in parallel, filter/mark on the caller's
  thread, in order.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlparse

from harness.errors import ProviderError

#: Pool width — enough to overlap the ~30 independent fetches a scan makes, small enough that
#: no host sees more than its lane allows even when every job targets it.
DEFAULT_WORKERS = 8

#: Per-host concurrency caps. Anything not listed gets `_DEFAULT_LANE`. Keys are matched as a
#: suffix of the request host (so `feeds.finance.yahoo.com` and `query2.finance.yahoo.com` share
#: the Yahoo lane — that is exactly the bucket Yahoo throttles on).
HOST_LANES: dict[str, int] = {
    "yahoo.com": 1,  # serial: per-UA+IP throttling; a burst here 429s every Yahoo surface
    "news.google.com": 2,  # the source-scope feeds + per-ticker gnews all land here
    "sec.gov": 2,  # SEC fair-access is ~10 rps; two in flight is polite and plenty
    "nasdaq.com": 2,  # unofficial analyst API; slow (~1-3s/call) and unforgiving of bursts
}
_DEFAULT_LANE = 4

_lanes: dict[str, threading.Semaphore] = {}
_lanes_lock = threading.Lock()


def lane_size(host: str) -> int:
    host = host.lower()
    for suffix, n in HOST_LANES.items():
        if host == suffix or host.endswith("." + suffix):
            return n
    return _DEFAULT_LANE


def host_lane(host: str) -> threading.Semaphore:
    """The semaphore for a host's lane (created on first use, process-wide — the standing pulse
    and an interactive `watch` in the same process share it, which is the point)."""
    key = host.lower()
    for suffix in HOST_LANES:
        if key == suffix or key.endswith("." + suffix):
            key = suffix
            break
    with _lanes_lock:
        sem = _lanes.get(key)
        if sem is None:
            sem = threading.Semaphore(lane_size(key))
            _lanes[key] = sem
        return sem


@contextmanager
def paced(url_or_host: str) -> Iterator[None]:
    """Hold the host's lane for the duration of one request. Pass the URL (the host is parsed)
    or a bare host. No-op cost when the scan is serial — the semaphore is never contended."""
    host = urlparse(url_or_host).netloc if "://" in url_or_host else url_or_host
    with host_lane(host or "default"):
        yield


@dataclass
class JobResult[T]:
    label: str
    value: T | None = None
    error: ProviderError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def run_parallel[T](
    jobs: list[tuple[str, Callable[[], T]]], *, workers: int = DEFAULT_WORKERS
) -> list[JobResult[T]]:
    """Run `(label, fn)` jobs on a bounded pool; results come back in SUBMISSION ORDER with each
    `ProviderError` captured per job. Empty input → empty output without spinning a pool."""
    if not jobs:
        return []
    out: list[JobResult[T]] = []
    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        futures = [(label, pool.submit(fn)) for label, fn in jobs]
        for label, fut in futures:
            try:
                out.append(JobResult(label=label, value=fut.result()))
            except ProviderError as exc:
                out.append(JobResult(label=label, error=exc))
    return out
