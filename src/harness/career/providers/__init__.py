"""ATS board providers — keyless public JSON APIs only (read-only by doctrine).

Greenhouse + Ashby (board-token APIs) + Workday CXS + Eightfold (per-tenant config, the JSON
endpoints behind the JS portals — verified against representative tenants). Lever stays a known
drop-in seam. The residual `ats: none` set (static pages, vendor-proprietary APIs, true anti-bot)
is covered by a planned Playwright-based fetcher.
"""

from harness.career.providers.ashby import fetch_ashby
from harness.career.providers.eightfold import fetch_eightfold
from harness.career.providers.greenhouse import fetch_greenhouse
from harness.career.providers.workday import fetch_workday

__all__ = ["fetch_ashby", "fetch_eightfold", "fetch_greenhouse", "fetch_workday"]
