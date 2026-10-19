"""Role shapes — the deterministic classifier the hiring map, the shortlist and the role items share.

A matched opening gets one shape bucket and one LANE (in · neutral · off), from its title first and
its department/team second. The buckets are ordered so the strongest signal wins:

1. reliability and security words are unambiguous in-lane signals and win outright;
2. the off-lane buckets (data-center construction/facilities, business/GTM, hardware design) win
   over the generic infra words they often share ("Capacity Deployment Lead - Data Center
   Operations" is a facilities role, not capacity engineering);
3. a research/ML/data role noun (scientist, ML engineer…) beats infra words in the same title; then
   platform/infra vocabulary (capacity, compute, storage, networking, caching, inference, release,
   database internals…) — the breadth of the infrastructure lane;
4. a title with none of those but a domain word of its own (search, ML, research, gameplay…) is a
   software/ML role whatever its org is called; a title with no signal at all reads the DEPARTMENT
   through the same buckets (a plain "Senior Software Engineer" on an Infrastructure team is infra);
5. leadership and general software/ML titles are neutral; "Other" is the residue.

Off-lane roles stay in the scan (the watchlist's `title_none` is noise control, not a fit filter);
the role items' shape debuff sorts them down. Matching is whole-word (a trailing plural allowed), so
"ai" never matches "air" and "infra" never matches inside another word.
"""

from __future__ import annotations

import re
from functools import cache

IN, NEUTRAL, OFF = "in", "neutral", "off"

# (label, lane, tokens) — evaluated in this order. Token = whole word or phrase, plural "s" allowed.
# Phrases that look in-lane but are facilities work — checked before everything else.
_PRE_OFF: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Data center / Facilities", OFF, ("physical security", "security officer", "security guard")),
)
_STRONG: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("SRE / Reliability", IN, (
        "sre", "site reliability", "reliability", "observability", "production engineer",
        "production engineering", "incident", "resilience",
    )),
    ("Security", IN, ("security", "appsec", "infosec")),
)
_OFF: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Data center / Facilities", OFF, (
        "data center", "datacenter", "critical facility", "facility", "facilities", "commissioning",
        "construction", "electrical", "mechanical", "site operation", "technical deployment",
        "deployment lead", "manufacturing", "supply chain", "procurement", "energy", "dig",
    )),
    ("Business / GTM", OFF, (
        "business development", "sales", "account executive", "account manager", "solution architect",
        "solutions architect", "solution engineer", "solutions engineer", "field engineer",
        "partnership", "partner manager", "customer success", "customer experience", "marketing",
        "go-to-market", "gtm", "recruiter", "recruiting", "transaction", "deal", "field application",
        "commercial", "consulting", "consultant",
    )),
    ("Hardware", OFF, (
        "asic", "silicon", "semiconductor", "hardware", "circuit", "verification engineer",
        "physical design", "rtl", "fpga", "analog", "pcb", "thermal", "signal integrity", "soc",
        "chip",
    )),
)
# A title whose ROLE NOUN is research/ML/data work stays that, whatever infra word rides along
# ("Principal Data Scientist - Cloud Gaming" is a scientist, not a cloud engineer).
_ROLE_NOUN: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Software / ML", NEUTRAL, (
        "scientist", "researcher", "machine learning engineer", "ml engineer", "data engineer",
        "data analyst", "applied scientist",
    )),
)
_INFRA: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Platform / Infra", IN, (
        "platform", "infrastructure", "infra", "kubernetes", "k8s", "cloud", "devops",
        "systems engineer", "systems software", "system software", "capacity", "compute", "storage",
        "storage engine", "network", "networking", "sdn", "distributed system", "caching", "cache",
        "inference", "serving", "performance", "hpc", "release", "build", "ci/cd", "deployment",
        "developer productivity", "tooling", "automation", "database", "internals", "kernel", "linux",
        "runtime", "orchestration", "fleet", "provisioning", "cdn", "edge", "query engine",
        "systems performance", "virtualization", "control plane", "data plane",
    )),
)
# A title's own DOMAIN word beats its department's (a vector-search engineer in an org named
# "Platform" is a search engineer); generic words ("software engineer") defer to the department.
_DOMAIN: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Software / ML", NEUTRAL, (
        "machine learning", "ml", "ai", "research", "scientist", "perception", "model", "search",
        "frontend", "full stack", "fullstack", "mobile", "game", "gameplay", "graphics",
    )),
)
_SOFT: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Leadership", NEUTRAL, ("director", "manager", "head of", "vp", "vice president", "chief")),
    ("Software / ML", NEUTRAL, (
        "software", "engineer", "developer", "backend", "data", "java", "architect",
    )),
)

OTHER = "Other"
# The hiring map's axes, in reading order (in-lane first, off-lane last, the residue at the end).
AXES: tuple[str, ...] = (
    "SRE / Reliability", "Platform / Infra", "Security", "Leadership", "Software / ML",
    "Data center / Facilities", "Business / GTM", "Hardware", OTHER,
)
LANE_OF: dict[str, str] = {
    label: lane for label, lane, _ in (*_STRONG, *_OFF, *_ROLE_NOUN, *_INFRA, *_DOMAIN, *_SOFT)
} | {OTHER: NEUTRAL}


@cache
def _rx(tokens: tuple[str, ...]) -> re.Pattern[str]:
    alts = "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alts})s?(?![a-z0-9])")


def _first(text: str, groups: tuple[tuple[str, str, tuple[str, ...]], ...]) -> str | None:
    for label, _lane, tokens in groups:
        if _rx(tokens).search(text):
            return label
    return None


def shape_of(title: str, department: str = "") -> str:
    """The opening's shape bucket (one of AXES)."""
    t, d = title.lower(), (department or "").lower()
    hard = (*_PRE_OFF, *_STRONG, *_OFF, *_INFRA)
    return (_first(t, (*_PRE_OFF, *_STRONG, *_OFF, *_ROLE_NOUN, *_INFRA)) or _first(t, _DOMAIN)
            or _first(d, hard) or _first(t, _SOFT) or OTHER)


def lane_of(shape: str) -> str:
    """in | neutral | off for a bucket label."""
    return LANE_OF.get(shape, NEUTRAL)
