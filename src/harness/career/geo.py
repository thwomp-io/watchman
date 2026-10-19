"""Posting geography — where an opening sits relative to the applicant: the home area (overlay
`career.roles.home_area`, default the city of `career.applicant.location`), remote in the US, elsewhere
in the US (relocation), outside the US, or unknown (a board that says "3 Locations"). The place lists
here are generic geography; the applicant's own places come only from the overlay."""

from __future__ import annotations

import re

from harness.career.models import Opening

# Generic geography (not the user's): words that put a posting outside the US, and the US markers.
_NON_US = (
    "canada", "quebec", "québec", "ontario", "toronto", "montreal", "montréal", "british columbia",
    "united kingdom", "uk", "england", "london", "scotland", "ireland", "dublin", "india",
    "bangalore", "bengaluru", "karnataka", "hyderabad", "gurugram", "pune", "germany", "berlin",
    "munich", "france", "paris", "netherlands", "amsterdam", "spain", "madrid", "barcelona",
    "poland", "warsaw", "portugal", "lisbon", "italy", "milan", "switzerland", "zurich", "sweden",
    "stockholm", "denmark", "copenhagen", "finland", "norway", "israel", "tel aviv", "japan",
    "tokyo", "singapore", "australia", "sydney", "melbourne", "new zealand", "anz", "korea",
    "seoul", "taiwan", "taipei", "china", "shanghai", "beijing", "shenzhen", "hong kong",
    "mexico", "brazil", "são paulo", "sao paulo", "argentina", "colombia", "chile", "emea",
    "apac", "latam", "europe", "indonesia", "vietnam", "philippines", "malaysia", "thailand",
    "czech", "prague", "romania", "serbia", "ukraine", "estonia", "lithuania", "greece", "turkey",
    "uae", "dubai", "south africa", "egypt", "nigeria", "kenya", "costa rica",
)
_US_WORDS = (
    "united states", "usa", "u.s.", "us-", "us -", "- us", "(us)", "us remote", "amer", "north america",
)
_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
_STATE_RX = re.compile(r"(?:,|\s)\s*([A-Z]{2})\b")


def word_in(text: str, words: tuple[str, ...] | list[str]) -> bool:
    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text) for w in words)


def home_area() -> list[str]:
    """The applicant's home-area place names (lowercased): `career.roles.home_area` in the
    overlay, else the city of `career.applicant.location`. Empty = no home seat is ever read."""
    from harness.settings import overlay_get

    raw = overlay_get("career", "roles", "home_area", default=None)
    if isinstance(raw, list) and raw:
        return [str(x).strip().lower() for x in raw if str(x).strip()]
    loc = overlay_get("career", "applicant", "location", default="") or ""
    city = str(loc).split(",")[0].strip().lower()
    return [city] if city else []


def location_class(o: Opening, home: list[str]) -> str:
    """home · remote_us · relocation · outside_us · unknown."""
    raw = o.location or ""
    loc = raw.lower()
    if home and word_in(loc, home):
        return "home"
    us = word_in(loc, _US_WORDS) or any(m in _US_STATES for m in _STATE_RX.findall(raw))
    non_us = word_in(loc, _NON_US)
    remote = o.remote is True or "remote" in loc
    if remote and (us or not non_us):
        return "remote_us"
    if us:
        return "relocation"
    if non_us:
        return "outside_us"
    return "unknown"
