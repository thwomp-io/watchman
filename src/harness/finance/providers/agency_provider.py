"""The statistical agencies' own published numbers, and the two market-holiday schedules — keyless.

The calendar's schedule and consensus come from one aggregator; a release's ACTUAL is read here,
from whoever publishes it, so a late or missing aggregator row does not leave the number blank.

Sources (each verified live 2026-11-10; one request per read, no retries — a refusal is the answer):

- **BLS public API v1** (`api.bls.gov/publicAPI/v1/timeseries/data/`, POST, JSON): CPI, PPI, payrolls,
  the unemployment rate, hourly earnings, as index levels and counts; the changes are computed here.
  Limits: 25 queries a day and 25 series a query, so the caller batches and counts. A month the
  agency did not collect comes back as the value `"-"` and is read as "not published". Only the API
  host answers a tool client; `www.bls.gov` refuses one.
- **BEA** (`www.bea.gov`, HTML): the GDP data page carries the headline rate and the release date in
  marked-up fields. The PCE data page carries only the 12-month rate, so the monthly and core rates
  are read from the release it links to ("Current Release"), where they are sentences of prose.
  A data page can trail its release by some minutes ("Page being updated").
- **Census** (`www.census.gov/retail/sales.html`, HTML): the advance retail sales headline, month over
  month and year over year, as one sentence. Its time-series API requires a key and is not used.
- **SIFMA** (`www.sifma.org/resources/general/holiday-schedule`): the bond market's recommended
  closes and 2:00 p.m. early closes. The page lists the U.S., the U.K. and Japan in one document;
  only the U.S. section is read.
- **NYSE** (`www.nyse.com/trade/hours-calendars`): the equity holidays as a table of three years, the
  1:00 p.m. early closes as footnote sentences. The page repeats its content inside a script payload,
  so dates are de-duplicated.

Not sources:

- **Weekly jobless claims.** The Labor Department's queryable dataset
  (`oui.doleta.gov/unemploy/wkclaims/report.asp`) answers, and runs about four weeks behind the
  Thursday release; the release itself (`www.dol.gov/ui/data.pdf`) refuses a tool client. Claims stay
  with the aggregator.
- **FRED's CSV download** (`fred.stlouisfed.org/graph/fredgraph.csv`) does not answer: the stream is
  reset over HTTP/2 and times out over HTTP/1.1, with the tool User-Agent and with a browser one alike
  (verified 2026-11-11), so the User-Agent is not what it keys on. Its API needs a key.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from harness._http import USER_AGENT
from harness.errors import ProviderError
from harness.textutil import html_to_line

_BLS = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
_BEA = "https://www.bea.gov"
_BEA_GDP = f"{_BEA}/data/gdp/gross-domestic-product"
_BEA_PCE = f"{_BEA}/data/personal-consumption-expenditures-price-index"
_CENSUS_RETAIL = "https://www.census.gov/retail/sales.html"
_SIFMA = "https://www.sifma.org/resources/general/holiday-schedule"
_NYSE = "https://www.nyse.com/trade/hours-calendars"

_LONG_DATE = r"[A-Z][a-z]+ \d{1,2}, \d{4}"
_CHANGE = r"((?:increased|decreased) [\d.]+ percent|was unchanged)"


# ————— transport —————


def _send(client: httpx.Client, method: str, url: str, **kw: Any) -> httpx.Response:
    """One request, no retry. Any error status or transport failure is a ProviderError naming the
    host; a throttle, a server error or a timeout is transient."""
    host = httpx.URL(url).host
    try:
        resp = client.request(method, url, headers={"User-Agent": USER_AGENT}, **kw)
    except httpx.HTTPError as e:
        raise ProviderError(f"{host}: {type(e).__name__}", transient=True) from e
    if resp.status_code >= 400:
        raise ProviderError(
            f"{host}: HTTP {resp.status_code}", transient=resp.status_code == 429 or resp.status_code >= 500
        )
    return resp


_text = html_to_line  # a page as one line of running text


def _long_date(s: str) -> date:
    return datetime.strptime(s.strip(), "%B %d, %Y").date()


# ————— BLS —————


@dataclass
class Point:
    year: int
    month: int
    value: float | None  # None: the agency published no figure for the month


def parse_bls(body: Any) -> dict[str, list[Point]]:
    """Pure: series id → its monthly points, newest first. Annual and quarterly rows are skipped."""
    if not isinstance(body, dict) or body.get("status") != "REQUEST_SUCCEEDED":
        msg = "; ".join(str(m) for m in (body.get("message") or [])) if isinstance(body, dict) else ""
        raise ProviderError(f"api.bls.gov: request not accepted{': ' + msg if msg else ''}", transient=True)
    out: dict[str, list[Point]] = {}
    for s in (body.get("Results") or {}).get("series") or []:
        points = []
        for r in s.get("data") or []:
            m = re.fullmatch(r"M(0[1-9]|1[0-2])", str(r.get("period") or ""))
            if not m:
                continue
            try:
                value: float | None = float(str(r.get("value")).replace(",", ""))
            except ValueError:
                value = None
            points.append(Point(int(r["year"]), int(m.group(1)), value))
        points.sort(key=lambda p: (p.year, p.month), reverse=True)
        out[str(s.get("seriesID"))] = points
    return out


def fetch_bls(
    client: httpx.Client, series: list[str], start_year: int, end_year: int
) -> dict[str, list[Point]]:
    """One query for up to 25 series."""
    resp = _send(
        client,
        "POST",
        _BLS,
        json={
            "seriesid": series[:25],
            "startyear": str(start_year),
            "endyear": str(end_year),
        },
    )
    try:
        return parse_bls(resp.json())
    except ValueError as e:
        raise ProviderError("api.bls.gov: the reply is not JSON", transient=True) from e


# ————— BEA —————


@dataclass
class GdpRelease:
    released: date
    period: str  # "Q3 2026 (Adv)"
    rate: float  # annualized quarterly change, percent
    previous: float | None


@dataclass
class PceRelease:
    released: date
    month: str
    mm: float | None
    core_mm: float | None
    yy: float | None
    core_yy: float | None
    prev_mm: float | None
    prev_core_mm: float | None


def _bea_released(page: str) -> date:
    m = re.search(rf"Current release:\s*</strong>(?:&nbsp;|\s)*({_LONG_DATE})", page)
    if not m:
        raise ProviderError("www.bea.gov: no 'Current release' date on the page (its layout changed)")
    return _long_date(m.group(1))


def parse_bea_gdp(page: str) -> GdpRelease:
    """Pure: the headline rate, its label and the prior quarter's rate from the GDP data page."""
    facts = re.findall(
        r"field--name-title[^>]*>([^<]+)</div>\s*</td>\s*<td>\s*<div[^>]*field--name-field-value[^>]*>"
        r"\s*([+\-−]?[\d.]+)%",
        page,
    )
    if not facts:
        raise ProviderError("www.bea.gov: no GDP headline on the page (its layout changed)")
    rates = [float(v.replace("−", "-")) for _, v in facts]
    return GdpRelease(
        _bea_released(page), facts[0][0].strip(), rates[0], rates[1] if len(rates) > 1 else None
    )


def parse_bea_pce_page(page: str) -> tuple[date, str]:
    """Pure: the PCE data page's release date and the path of the release it links to."""
    m = re.search(
        r'href="(/news/\d{4}/personal-income-and-outlays[a-z0-9\-]*)"[^>]*>\s*Current Release', page
    )
    if not m:
        raise ProviderError("www.bea.gov: no 'Current Release' link on the PCE page (its layout changed)")
    return _bea_released(page), m.group(1)


def _signed(phrase: str | None) -> float | None:
    """'increased 0.5 percent' → 0.5 · 'decreased 0.1 percent' → -0.1 · 'was unchanged' → 0.0."""
    if not phrase:
        return None
    if "unchanged" in phrase:
        return 0.0
    n = float(re.search(r"[\d.]+", phrase).group(0))  # type: ignore[union-attr]
    return -n if phrase.startswith("decreased") else n


def parse_bea_outlays(page: str, released: date) -> PceRelease:
    """Pure: the PCE price index's monthly and 12-month changes, headline and core, from the
    Personal Income and Outlays release. A sentence the release does not carry leaves its figure None."""
    text = _text(page)
    mm = re.search(
        rf"From the preceding month, the PCE price index for (\w+) {_CHANGE}\s*\.\s*"
        rf"Excluding food and energy, the PCE price index {_CHANGE}",
        text,
    )
    yy = re.search(
        rf"From the same month one year ago, the PCE price index for \w+ {_CHANGE}\s*\.\s*"
        rf"Excluding food and energy, the PCE price index {_CHANGE}",
        text,
    )
    table = re.search(
        r"PCE price index (-?[\d.]+) -?[\d.]+ PCE price index excluding food and energy (-?[\d.]+) -?[\d.]+",
        text,
    )
    if not mm and not yy:
        raise ProviderError("www.bea.gov: no PCE price sentences in the release (its wording changed)")
    return PceRelease(
        released=released,
        month=mm.group(1) if mm else "",
        mm=_signed(mm.group(2)) if mm else None,
        core_mm=_signed(mm.group(3)) if mm else None,
        yy=_signed(yy.group(1)) if yy else None,
        core_yy=_signed(yy.group(2)) if yy else None,
        prev_mm=float(table.group(1)) if table else None,
        prev_core_mm=float(table.group(2)) if table else None,
    )


def fetch_gdp(client: httpx.Client) -> GdpRelease:
    return parse_bea_gdp(_send(client, "GET", _BEA_GDP).text)


def fetch_pce_released(client: httpx.Client) -> tuple[date, str]:
    return parse_bea_pce_page(_send(client, "GET", _BEA_PCE).text)


def fetch_pce(client: httpx.Client, released: date, path: str) -> PceRelease:
    return parse_bea_outlays(_send(client, "GET", _BEA + path).text, released)


# ————— Census —————


@dataclass
class RetailRelease:
    released: date
    month: str  # "September 2026"
    mm: float | None
    yy: float | None
    prev_mm: float | None  # the prior month's change as revised in this release


def _up_down(direction: str | None, number: str | None) -> float | None:
    if not number:
        return 0.0 if direction and "unchanged" in direction else None
    return -float(number) if direction == "down" else float(number)


def parse_census_retail(page: str) -> RetailRelease:
    """Pure: the advance retail and food services headline."""
    pub = re.search(rf'itemprop="datePublished"[^>]*>\s*({_LONG_DATE})', page)
    text = _text(page)
    move = r"(?:(up|down) ([\d.]+) percent|(virtually unchanged))"
    m = re.search(
        rf"retail and food services sales for (\w+ \d{{4}}),.*?were \$[\d.,]+ \w+, {move}.*?"
        rf"from the previous month, and {move}",
        text,
    )
    if not pub or not m:
        raise ProviderError("www.census.gov: no advance retail headline on the page (its layout changed)")
    rev = re.search(r"percent change was revised from .*? to (up|down) ([\d.]+) percent", text)
    return RetailRelease(
        released=_long_date(pub.group(1)),
        month=m.group(1),
        mm=_up_down(m.group(2) or m.group(4), m.group(3)),
        yy=_up_down(m.group(5) or m.group(7), m.group(6)),
        prev_mm=_up_down(rev.group(1), rev.group(2)) if rev else None,
    )


def fetch_retail(client: httpx.Client) -> RetailRelease:
    return parse_census_retail(_send(client, "GET", _CENSUS_RETAIL).text)


# ————— market holidays —————


@dataclass
class MarketDay:
    day: str  # ISO
    market: str  # bonds | equities
    kind: str  # closed | early
    name: str  # the holiday ("Veterans Day"); for an early close, the holiday it precedes or follows
    close_et: str | None = None  # an early close's time, 24-hour


def _weekday_date(s: str) -> date:
    """'Wednesday, November 11, 2026' → the date."""
    return _long_date(s.split(",", 1)[1])


def _pm(hhmm: str) -> str:
    h, m = hhmm.split(":")
    return f"{int(h) % 12 + 12:02d}:{m}"


def parse_sifma(page: str) -> list[MarketDay]:
    """Pure: the U.S. recommendations only (the page also lists the U.K. and Japan)."""
    start = page.find("U.S. Holiday Recommendations")
    end = page.find("U.K. Holiday Recommendations", start)
    if start < 0 or end < 0:
        raise ProviderError("www.sifma.org: no U.S. section on the holiday page (its layout changed)")
    out: list[MarketDay] = []
    for name, day, note in re.findall(
        r"<h3[^>]*>([^<]+)</h3>\s*<span[^>]*>([^<]+)</span>\s*<p[^>]*>([^<]*)</p>", page[start:end]
    ):
        name = re.sub(r"\s+\d{4}/\d{4}$", "", _html.unescape(name).strip())  # "New Year's Day 2026/2027"
        try:
            out.append(MarketDay(_weekday_date(day).isoformat(), "bonds", "closed", name))
            early = re.search(
                rf"Early Close \((\d{{1,2}}:\d{{2}}) p\.m\.[^)]*\):\s*(\w+, {_LONG_DATE})", note
            )
            if early:
                out.append(
                    MarketDay(
                        _weekday_date(early.group(2)).isoformat(), "bonds", "early", name, _pm(early.group(1))
                    )
                )
        except ValueError:
            continue
    if not out:
        raise ProviderError("www.sifma.org: the U.S. section lists no dates (its layout changed)")
    return out


def parse_nyse(page: str) -> list[MarketDay]:
    """Pure: the holiday table (one column per year) and the early closes named in its footnotes."""
    table = re.search(r"<table.*?</table>", page, flags=re.S)
    years = [int(y) for y in re.findall(r"<th>(\d{4})</th>", table.group(0))] if table else []
    if not table or not years:
        raise ProviderError("www.nyse.com: no holiday table on the page (its layout changed)")
    out: dict[tuple[str, str], MarketDay] = {}
    for name, cells in re.findall(r"<tr><th>([^<]+)</th>(.*?)</tr>", table.group(0), flags=re.S):
        name = _html.unescape(name).strip()
        for year, cell in zip(years, re.findall(r"<td>(.*?)</td>", cells, flags=re.S), strict=False):
            m = re.search(r"\w+day, ([A-Z][a-z]+ \d{1,2})", cell)
            if not m:
                continue  # "—": no holiday observed that year
            day = _long_date(f"{m.group(1)}, {year}").isoformat()
            out[(day, "closed")] = MarketDay(day, "equities", "closed", name)
    text = _text(page)
    for m in re.finditer(r"close early at (\d{1,2}:\d{2}) p\.m\.", text):
        sentence = re.split(r"NYSE American|All times", text[m.end() : m.end() + 400])[0]
        for d in re.findall(rf"\w+day, {_LONG_DATE}", sentence):
            day = _weekday_date(d).isoformat()
            out[(day, "early")] = MarketDay(day, "equities", "early", "", _pm(m.group(1)))
    return sorted(out.values(), key=lambda d: (d.day, d.kind))


def fetch_sifma(client: httpx.Client) -> list[MarketDay]:
    return parse_sifma(_send(client, "GET", _SIFMA).text)


def fetch_nyse(client: httpx.Client) -> list[MarketDay]:
    return parse_nyse(_send(client, "GET", _NYSE).text)
