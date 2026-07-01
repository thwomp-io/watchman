"""Shared keyless geocoder (Open-Meteo geocoding API).

Lifted out of the weather provider once a second + third sense (air-quality, USGS seismic) needed the
same name→lat/lon step. Generic + keyless; the senses all funnel through here so "Denver" resolves
identically everywhere.

Open-Meteo's `name` search matches place *names* only — it does NOT understand a trailing
region/country qualifier ("San Juan, Puerto Rico" / "Liberia Costa Rica" → 0 results), and a bare
name is population-ranked + ambiguous ("Liberia" → the *country*; "George Town" → Penang, MY). So we
(1) try the literal string first (preserves "Salt Lake City", "Denver"), then (2) split into a
place + qualifier and rank the candidates by how well they match the qualifier (country / admin1 /
country_code, plus a small alias map for null-`country` territories like Puerto Rico / Cayman).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from harness._http import get_with_retry
from harness.travel.models import GeoLocation
from harness.travel.providers.base import ProviderError

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_UA = "harness/0.1 (personal travel-planning harness)"

# Country/territory name → ISO-3166 alpha-2, for qualifier matching where Open-Meteo returns a null
# `country` field (most territories) or the user uses an abbreviation. Full country *names* that ARE
# populated match by text; this bridges the gaps. Extend as new gaps surface.
_COUNTRY_ALIASES: dict[str, str] = {
    "puerto rico": "PR",
    "cayman islands": "KY",
    "us virgin islands": "VI", "u.s. virgin islands": "VI", "usvi": "VI",
    "guam": "GU",
    "united states": "US", "usa": "US", "u.s.": "US", "us": "US", "america": "US",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "england": "GB", "britain": "GB",
    "united arab emirates": "AE", "uae": "AE",
    "south korea": "KR", "north korea": "KP",
    "czech republic": "CZ", "czechia": "CZ",
    "dominican republic": "DO",
}


def _search(
    query: str, *, count: int, client: httpx.Client | None, timeout: float
) -> list[dict[str, Any]]:
    """Raw Open-Meteo name search → list of result dicts (may be empty)."""
    params: dict[str, str | int] = {"name": query, "count": count, "language": "en", "format": "json"}
    try:
        resp = get_with_retry(
            _GEOCODE_URL, params=params, headers={"User-Agent": _UA},
            client=client, timeout=timeout,
        )
        results = resp.json().get("results") or []
    except (httpx.HTTPError, ValueError) as e:
        raise ProviderError(f"geocode failed for {query!r}: {e}") from e
    return list(results)


def _candidate_splits(query: str) -> Iterator[tuple[str, str]]:
    """Yield (place, qualifier) guesses for a 'City, Region' / 'City Region' string.

    Comma form is tried first ("Kihei, Hawaii" → ("Kihei", "Hawaii")); otherwise the split point
    walks right-to-left so the *longest* place name is tried first ("Liberia Costa Rica" →
    ("Liberia Costa", "Rica") then ("Liberia", "Costa Rica")).
    """
    if "," in query:
        place, _, qualifier = query.partition(",")
        place, qualifier = place.strip(), qualifier.strip()
        if place and qualifier:
            yield place, qualifier
        return
    tokens = query.split()
    for i in range(len(tokens) - 1, 0, -1):
        yield " ".join(tokens[:i]), " ".join(tokens[i:])


def _qualifier_score(r: dict[str, Any], qualifier: str) -> int:
    """How well a candidate matches the user's region/country qualifier (higher = better)."""
    q = qualifier.casefold().strip()
    if not q:
        return 0
    fields = [r.get("country"), r.get("admin1"), r.get("name"), r.get("country_code")]
    hay = " ".join(str(f).casefold() for f in fields if f)
    if q in hay:  # full-string match: "hawaii", "costa rica", "colombia", or the code "pr"
        return 2
    code = _COUNTRY_ALIASES.get(q)
    if code and str(r.get("country_code") or "").casefold() == code.casefold():
        return 2  # alias bridge for null-country territories ("cayman islands" → KY)
    if set(q.split()) & set(hay.split()):  # partial token overlap
        return 1
    return 0


def _population(r: dict[str, Any]) -> int:
    pop = r.get("population")
    return int(pop) if isinstance(pop, (int, float)) else 0


def _to_location(r: dict[str, Any]) -> GeoLocation:
    return GeoLocation(
        name=r["name"],
        latitude=float(r["latitude"]),
        longitude=float(r["longitude"]),
        admin1=r.get("admin1"),
        country_code=r.get("country_code"),
        timezone=r.get("timezone"),
    )


def geocode(
    query: str, *, client: httpx.Client | None = None, timeout: float = 30.0
) -> GeoLocation | None:
    """Resolve a place to lat/lon. Tries the literal name, then 'City, Region'/'City Country' forms.

    Returns the best match (qualifier-disambiguated where a region/country is given), or None if
    nothing resolves.
    """
    query = query.strip()
    if not query:
        return None
    # Fast path: the whole string as a literal place name (e.g. "Salt Lake City", "Denver").
    literal = _search(query, count=1, client=client, timeout=timeout)
    if literal:
        return _to_location(literal[0])
    # Qualifier path: split into place + region/country and disambiguate the candidates.
    fallback: dict[str, Any] | None = None
    for place, qualifier in _candidate_splits(query):
        hits = _search(place, count=10, client=client, timeout=timeout)
        if not hits:
            continue
        if fallback is None:
            fallback = max(hits, key=_population)  # best-effort if no qualifier matches anywhere
        matched = [r for r in hits if _qualifier_score(r, qualifier) > 0]
        if matched:
            return _to_location(
                max(matched, key=lambda r: (_qualifier_score(r, qualifier), _population(r)))
            )
    return _to_location(fallback) if fallback else None


def geocode_label(loc: GeoLocation) -> str:
    """Human display label, e.g. 'Denver, Colorado, US'."""
    return ", ".join(p for p in (loc.name, loc.admin1, loc.country_code) if p)
