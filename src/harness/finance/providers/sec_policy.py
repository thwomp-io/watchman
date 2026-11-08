"""Name the cause when SEC refuses a request.

SEC answers a refused request with HTTP 403 and an HTML page whose title says why. Reading it turns
"HTTP 403" into a cause the operator can act on:
- "Your Request Originates from an Undeclared Automated Tool" — the User-Agent was refused (fair-access
  policy). data.sec.gov accepts the bare `harness/<version>`; it refuses a UA that carries a URL
  (verified 2026-10-27). www.sec.gov archives also want an email contact (`HARNESS_SEC_CONTACT`).
- "Request Rate Threshold Exceeded" — over SEC's 10 requests/second. (The title SEC documents for
  this case; not reproduced live, since that means exceeding the limit.)
Anything else is reported with whatever title the page carried.
"""

from __future__ import annotations

import html
import re

import httpx

_TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def refusal_cause(resp: httpx.Response) -> str:
    """A one-line cause for a non-200 SEC response ('' when there is nothing to add)."""
    if resp.status_code != 403:
        return ""
    m = _TITLE.search(resp.text or "")
    title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""
    if "Undeclared Automated Tool" in title:
        return "SEC refused the User-Agent (fair-access; data.sec.gov wants a bare harness/<version>)"
    if "Rate Threshold" in title:
        return "SEC rate limit (over 10 requests/second): back off and retry"
    return f"SEC refused the request ({title or 'no reason page'})"
