"""The board scan and its reads: openings on the watchlist's public ATS boards, the ranked roles
view, and the local shortlist."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import typer
from rich.table import Table

from harness.career.cli.common import console
from harness.career.config import get_settings, role_hunt_root
from harness.career.service import CareerService
from harness.errors import UsageError
from harness.viz import VizError, render_diagram


def openings(
    company: list[str] = typer.Option(
        None, "--company", "-c", help="Narrow to companies (name substring; repeatable)"
    ),
    grep: str = typer.Option(None, "--grep", help="Extra title-contains filter"),
    unfiltered: bool = typer.Option(
        False, "--all", help="Bypass the watchlist title/seniority keyword filters"
    ),
    write: bool = typer.Option(
        False, "--write", help="Also write role-hunt/discoveries/YYYY-MM-DD-openings.md"
    ),
    new_only: bool = typer.Option(
        False, "--new", help="Only postings no earlier persisted scan carried (delta mode)"
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Machine output (per-company scans incl. matches/errors/skips) — the surfaces/"
        "watchman contract",
    ),
) -> None:
    """Scan the watchlist's public ATS boards (keyless, read-only) for matching openings.

    Matching: ANY watchlist title-keyword AND ANY seniority-keyword (see role-hunt/watchlist.yml);
    salary shown as posted (pay-transparency states), unnormalized. Boards that error are surfaced
    loudly — an error is never rendered as "0 openings."
    """
    svc = CareerService(get_settings().tracker_path, role_hunt_root=role_hunt_root(get_settings()))
    scans = svc.scan(companies=company or None, grep=grep, unfiltered=unfiltered)
    # every opening carries new/first_seen vs the persisted scans before today; --new narrows the
    # PRINTED view only — the --write report + twin always hold the whole scan
    svc.annotate_new(scans, date.today().isoformat())
    shown = scans
    if new_only:
        shown = [s.model_copy(update={"matched": [o for o in s.matched if o.new]}) for s in scans]
    if as_json:
        import json as _json

        console.print_json(_json.dumps([s.model_dump() for s in shown]))
        return
    for s in shown:
        if s.error:
            console.print(f"[red]⚠ {s.company}[/red] — scan error: {s.error}")
            continue
        if s.skipped:
            console.print(f"[dim]– {s.company}: manual-watch ({s.skipped})[/dim]")
            continue
        header = f"[bold]{s.company}[/bold] — {len(s.matched)} matches / {s.total_open} postings"
        console.print(header)
        if not s.matched:
            continue
        t = Table(show_header=True, header_style="bold", pad_edge=False)
        t.add_column("Title", max_width=46)
        t.add_column("Location", max_width=34)
        t.add_column("Salary (as posted)", max_width=26)
        t.add_column("Updated")
        for o in s.matched:
            loc = o.location + (" · remote" if o.remote else "")
            title = ("[green]NEW[/green] " if o.new else "") + o.title
            t.add_row(title, loc, o.salary or "—", o.updated or "—")
        console.print(t)
    hits = sum(len(s.matched) for s in scans)
    fresh = sum(1 for s in scans for o in s.matched if o.new)
    console.print(
        f"\n[bold]{hits}[/bold] matched openings total · [green]{fresh} new[/green] since the earlier "
        "scans. URLs in the report (--write)."
    )
    if write:
        # Pack-aware output: role_hunt_root resolves to <pack>/career when a weight pack is active
        # (else corpus/role-hunt), so `openings --write --pack X` writes into the pack, not the real corpus.
        out = role_hunt_root(get_settings()) / "discoveries" / f"{date.today().isoformat()}-openings.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        md = CareerService.to_markdown(scans, f"Openings scan — {date.today().isoformat()}")
        # lock-step visual: the matrix renders from the SAME scan in the SAME call (doc + diagram
        # cannot drift); embedded at the top of the report.
        data = CareerService.openings_matrix_data(
            scans, f"Openings — company × role shape · {date.today().isoformat()}"
        )
        if data is not None:
            try:
                svg = render_diagram(
                    "matrix",
                    data,
                    out.parent / "visuals" / f"{date.today().isoformat()}-openings-matrix.svg",
                )
                # Logical role-hunt-relative wikilink (the bus-app maps it back to <pack>/career when a
                # pack is active) — relative_to(tracker_path) would error for a pack-output svg.
                rel = Path("role-hunt") / svg.relative_to(role_hunt_root(get_settings()))
                # Anchor the matrix at the BOTTOM (out of immediate field of view on the dashboard,
                # where the live/interactive Hiring Map renders the same data next door — keep it in
                # the doc for out-of-dashboard viewing).
                md = (
                    md.rstrip()
                    + "\n\n---\n\n## Role-shape matrix\n\n"
                    + f"![[{rel}|900]]\n"
                    + "*Who's hiring at the watchlist's seniority band, in what shape — "
                    + "counts = matched openings. "
                    "(The live/interactive version is the CAREER dashboard's Hiring Map.)*\n"
                )
            except VizError as e:
                console.print(f"[yellow]matrix render skipped: {e}[/yellow]")
        out.write_text(md)
        # Machine-readable twin (the "manual sync" pattern) — the dashboard/shortlist read THIS
        # local artifact, never a live board scan on refresh. Doc + twin from the same scan = no drift.
        twin = out.with_suffix(".json")
        twin.write_text(json.dumps([s.model_dump() for s in scans], indent=2))
        console.print(f"wrote {out} (+ matrix visual + {twin.name} twin)")


def roles(
    new_only: bool = typer.Option(False, "--new", help="Only openings no earlier scan carried"),
    location: str = typer.Option(
        "all", "--location", help="all | us (drop outside-US) | home (home area + remote US + unknown)"
    ),
    company: str = typer.Option(None, "--company", help="One company (name or slug); 'auto' = all"),
    limit: int = typer.Option(40, "--limit", help="Rows to print (the JSON carries every row)"),
    as_json: bool = typer.Option(False, "--json", help="The RolesReport contract (the Openings tab)"),
) -> None:
    """The latest scan through the item model: one item per opening, ranked by fit, then new, then
    item level (no network).

    Base = the company's item level from the merged pool (unranked when it has no band row); role
    modifiers (level · shape lane · location · posted range vs the anchor · fresh · new) take their
    values from `role_modifiers:` in role-hunt/reference/pool-enchants.yaml. Fit: on-profile (in-lane
    shape, in-band or leadership level, an eligible location) · adjacent · off-lane (an off-lane shape
    or outside the US). Refresh the data with `openings --write`; this verb never fetches."""
    import json as _json

    from harness.career.roles import roles_payload

    s = get_settings()
    if location not in ("all", "us", "home"):
        raise UsageError(f"--location must be all | us | home, not {location!r}")
    payload = roles_payload(s.tracker_path, role_hunt_root(s), new_only=new_only, location=location,
                            company=company)
    if as_json:
        console.print_json(_json.dumps(payload))
        return
    c = payload["counts"]
    console.print(
        f"[bold]{c['shown']}[/bold] of {c['total']} openings · {c['new']} new · on-profile "
        f"{c['fit']['on-profile']} · adjacent {c['fit']['adjacent']} · off-lane {c['fit']['off-lane']}"
        f" · scan {payload['as_of'] or '—'}"
    )
    t = Table(show_header=True, header_style="bold", pad_edge=False)
    for col, w in (("Fit", 10), ("ilvl", 5), ("Company", 14), ("Title", 52), ("Location", 26)):
        t.add_column(col, max_width=w)
    for r in payload["rows"][:limit]:
        ilvl = "—" if r["final_ilvl"] is None else str(r["final_ilvl"])
        t.add_row(r["fit"], ilvl, r["company"], ("[green]NEW[/green] " if r["new"] else "") + r["title"],
                  r["location"])
    console.print(t)
    if payload["screen_gated"]:
        console.print(f"[dim]{len(payload['screen_gated'])} screen-gated (in --json)[/dim]")
    console.print(f"[dim]{payload['constitution']}[/dim]")


def shortlist(
    limit: int = typer.Option(30, "--limit", help="Max role rows to surface"),
    as_json: bool = typer.Option(
        False, "--json", help="Machine output (summary + ranked roles) — the CAREER-dashboard contract"
    ),
) -> None:
    """High-priority role shortlist from the LATEST persisted scan twin + watchlist tiers (LOCAL,
    no network — refreshing the data is the deliberate `openings --write` action). Summary tiles
    (total / leadership / IC-infra) + tier-ranked role rows. The CAREER
    dashboard's shortlist table + stat tiles read this; nothing here applies anywhere."""
    svc = CareerService(get_settings().tracker_path, role_hunt_root=role_hunt_root(get_settings()))
    data = svc.shortlist(limit=limit)
    if as_json:
        console.print_json(json.dumps(data))
        return
    s = data["summary"]
    if not data["roles"]:
        console.print(
            "[yellow]no shortlist yet — run `hn career openings --write` to persist a "
            "scan twin the shortlist reads.[/yellow]"
        )
        return
    console.print(
        f"[bold]{s['total']}[/bold] matched roles · [bold]{s['leadership']}[/bold] "
        f"leadership · [bold]{s['ic_infra']}[/bold] IC/infra · {s['boards_with_hits']}/"
        f"{s['scanned_boards']} boards · as of {s['as_of']}"
    )
    t = Table(show_header=True, header_style="bold", pad_edge=False)
    for c in ("Company", "Tier", "Shape", "Title", "Salary"):
        t.add_column(c, max_width=40 if c == "Title" else None)
    for r in data["roles"]:
        t.add_row(r["company"], r["tier"] or "—", r["shape"], r["title"], r["salary"])
    console.print(t)
