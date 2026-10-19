"""Typer sub-app for the `career` noun-group — the role-hunt lane.

Read-rich / execute-gated by doctrine: everything here observes (scan boards, surface comp,
capture forms, render artifacts, assemble packets). The ONE execute-side act is `apply --submit
--confirm` (the user's deliberate escalation): one packet per run, nothing without
the flag, a receipt recorded and the pipeline row advanced — the tool assembles, the human
greenlights.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from functools import lru_cache
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from harness._cli import fail as _fail
from harness.career.config import role_hunt_root
from harness.career.service import CareerService
from harness.errors import ProviderError
from harness.packs import PackGroup
from harness.settings import BaseToolkitSettings, overlay_get

# the shared D3 render engine (harness.viz) — every lane imports it here
from harness.viz import KNOWN_TYPES, VizError, render_diagram


def _resume_output_stem() -> str:
    """Render-output name stem — overlay-driven (career.global_settings.resume.output_stem),
    generic "resume" default: the engine never hardcodes a person's filename (generalize-first)."""
    return str(overlay_get("career", "resume", "output_stem", default="resume"))


def _resume_source_md() -> str:
    """The master-CV markdown SoT filename under docs/career/ — overlay-driven
    (career.global_settings.resume.source_md), generic "resume.md" default."""
    return str(overlay_get("career", "resume", "source_md", default="resume.md"))


app = typer.Typer(
    cls=PackGroup,  # every verb accepts a trailing `--pack <dir>` (hn career shortlist --pack …)
    add_completion=False,
    help="Career / role-hunt lane — keyless ATS board scan, posting + form capture, local shortlist "
    "+ application pipeline, résumé renders, packet assembly, D3 diagrams into role-hunt/. "
    "Execute-gated: the only act is `apply --submit --confirm`, one packet per run.",
)
console = Console()


@app.callback()
def _lane() -> None:
    """Career / role-hunt lane. (Explicit group callback: keeps `career viz …` a named
    subcommand even while it's the lane's only verb — Typer otherwise collapses
    single-command apps, which would silently re-shape the CLI when verb #2 lands.)"""


@lru_cache(maxsize=1)
def get_settings() -> BaseToolkitSettings:
    """Lane settings — the shared base only (tracker_path + .env). A career-specific
    Settings subclass lands when the lane grows provider keys (scanners, comp data)."""
    return BaseToolkitSettings()




@app.command()
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
    try:
        scans = svc.scan(companies=company or None, grep=grep, unfiltered=unfiltered)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
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


@app.command()
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
        _fail(f"--location must be all | us | home, not {location!r}")
        raise typer.Exit(code=1)
    try:
        payload = roles_payload(s.tracker_path, role_hunt_root(s), new_only=new_only, location=location,
                                company=company)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
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


@app.command()
def campaign(
    as_json: bool = typer.Option(False, "--json", help="The CampaignReport contract (the Campaign tab)"),
    mail_limit: int = typer.Option(12, "--mail-limit", help="Recruiter-mail rows"),
) -> None:
    """Where the search stands: the pipeline rows in flight (next step + date, last touch, channel,
    the screen line; stale = no touch in `career.campaign.stale_days` and no dated next step), inbound
    notes with no pipeline row (drift), the career calendar from today, and recruiter mail (humans
    only). Read-only — stage edits stay in applications.yaml."""
    from harness.career.campaign import build_campaign

    s = get_settings()
    try:
        rep = build_campaign(s.tracker_path, role_hunt_root(s), mail_limit=mail_limit)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(rep.model_dump_json())
        return
    t = Table(title=f"in flight · as of {rep.as_of}", show_header=True, header_style="bold", pad_edge=False)
    for col in ("Company", "Stage", "Next", "Last touch", "Channel", "Screen", ""):
        t.add_column(col)
    for c in rep.in_flight:
        nxt = f"{c.next_date} {c.next_time}".strip() if c.next_date else "—"
        flag = "DRIFT" if c.drift else ("stale — decide or archive" if c.stale else "")
        touch = f"{c.last_touch} ({c.days_since}d)" if c.last_touch else "—"
        t.add_row(c.company, c.stage, nxt, touch, c.channel, c.screen_line, flag)
    console.print(t)
    for m in rep.calendar:
        console.print(f"  {m.date}{' ' + m.time if m.time else ''} · in {m.days}d · {m.label}")
    if rep.mail_note:
        console.print(f"[dim]mail: {rep.mail_note}[/dim]")
    for row in rep.mail:
        when, sender, subj = str(row["date"])[:10], str(row["from"])[:28], str(row["subject"])[:60]
        console.print(f"  [dim]{when}[/dim] {row['class']:<9} {sender:<28} {subj}")


@app.command()
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
    try:
        data = svc.shortlist(limit=limit)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
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


@app.command()
def applications(
    as_json: bool = typer.Option(
        False, "--json", help="Machine output (pipeline rows) — the CAREER-dashboard contract"
    ),
) -> None:
    """The application/opportunity pipeline (`role-hunt/applications.yaml`, hand-edited
    corpus). A missing file = empty pipeline. The CAREER dashboard's PIPELINE table reads this;
    advancing a stage is the user's edit (read-rich / execute-gated)."""
    from harness.career.applications import load_applications

    try:
        pipe = load_applications(get_settings().tracker_path, root=role_hunt_root(get_settings()))
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(pipe.model_dump_json())
        return
    if not pipe.applications:
        console.print("[dim]pipeline empty — seed role-hunt/applications.yaml as opportunities land.[/dim]")
        return
    t = Table(show_header=True, header_style="bold", pad_edge=False)
    for c in ("Company", "Role", "Stage", "Slug", "Next step", "Updated"):
        t.add_column(c, max_width=36 if c in ("Role", "Next step") else None)
    for a in pipe.applications:
        t.add_row(a.company, a.role, a.stage, a.slug or "—", a.next_step or "—", a.updated or "—")
    console.print(t)


@app.command()
def posting(
    url: str = typer.Argument(..., help="The posting URL (Greenhouse hosted / ?gh_jid= embed / Ashby)"),
    slug: str = typer.Option(None, "--slug", help="Application folder name (default {company}-{title})"),
    company: str = typer.Option(
        None,
        "--company",
        "-c",
        help="Watchlist company name — required for a Greenhouse embed URL (?gh_jid=), which "
        "carries no board token; otherwise the name comes from the watchlist row whose token matches",
    ),
    force: bool = typer.Option(False, "--force", help="Re-capture over an existing posting.md"),
    as_json: bool = typer.Option(False, "--json", help="Machine output (posting + form schema)"),
) -> None:
    """Capture a posting + its ATS application form into role-hunt/applications/{slug}/ —
    posting.md · form.json (the ATS's own schema: fields, types, required, options, EEO blocks,
    AI-usage attestation) · answers.yaml (the authoring skeleton). Keyless, read-only: Greenhouse
    via the Job Board API, Ashby via the hosted page's form query. Never writes applications.yaml."""
    from harness.career.posting import (
        default_slug,
        normalize_ashby,
        normalize_greenhouse,
        resolve_posting_url,
        sync_answers,
        write_posting_folder,
    )
    from harness.career.providers import (
        fetch_ashby_posting,
        fetch_greenhouse_degrees,
        fetch_greenhouse_posting,
    )

    settings = get_settings()
    root = role_hunt_root(settings)
    svc = CareerService(settings.tracker_path, role_hunt_root=root)
    token = None
    name = company
    companies = []
    try:
        companies = svc.load().companies
    except ProviderError:
        pass  # no watchlist: fine for hosted URLs; the embed form needs one and says so
    if company:
        row = next((c for c in companies if company.lower() in c.name.lower()), None)
        if row is not None:
            token, name = (row.token or None), row.name
    try:
        ref = resolve_posting_url(url, board_token=token)
        if not name:
            row = next((c for c in companies if c.ats == ref.ats and c.token == ref.board), None)
            name = row.name if row else ref.board.replace("-", " ").title()
        if ref.ats == "greenhouse":
            raw = fetch_greenhouse_posting(ref.board, ref.job_id)
            raw["_board"] = ref.board
            # the Education block's degree list rides a second keyless call, only when asked for
            degrees = fetch_greenhouse_degrees(ref.board) if str(raw.get("education") or "") else []
            post, form = normalize_greenhouse(raw, name, degrees=degrees)
        else:
            job, aform = fetch_ashby_posting(ref.board, ref.job_id)
            job["_board"] = ref.board
            post, form = normalize_ashby(job, aform, name)
        key = slug or default_slug(post.company, post.title)
        existed = (root / "applications" / key / "answers.yaml").exists()
        folder = write_posting_folder(root, key, post, form, force=force)
        # a re-capture re-syncs the authored answers to the new form
        added, retired = sync_answers(folder, key, post, form) if existed else ([], [])
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(
            json.dumps(
                {"slug": key, "folder": str(folder), "posting": post.model_dump(), "form": form.model_dump()}
            )
        )
        return
    req = sum(1 for f in form.fields if f.required and f.kind != "compliance")
    custom = sum(1 for f in form.fields if f.kind == "custom")
    console.print(
        f"[bold]{post.company} — {post.title}[/bold]  ({post.location or '—'} · "
        f"{post.salary or 'salary not posted'})"
    )
    console.print(f"captured → {folder}")
    console.print(
        f"  form: {len(form.fields)} fields · {req} required · {custom} custom questions"
        + (
            f" · [yellow]AI-usage attestation present[/yellow] ({form.ai_policy.url})"
            if form.ai_policy.present
            else ""
        )
    )
    if existed:
        console.print(
            f"  answers.yaml re-synced: [bold]{len(added)}[/bold] added"
            + (f" ({', '.join(added)})" if added else "")
            + f" · [bold]{len(retired)}[/bold] retired"
            + (f" ({', '.join(retired)} → retired_answers)" if retired else "")
            + " · authored values kept"
        )
    console.print(f"  next: tailor resume.md → answer answers.yaml → `hn career apply {key}`")


@app.command()
def schools(
    term: str = typer.Argument(..., help="Search text, e.g. 'University of Maryland'"),
    board: str = typer.Option(
        None, "--board", help="Greenhouse board token (default: the board of --slug's posting)"
    ),
    slug: str = typer.Option(None, "--slug", help="Take the board from this application folder"),
) -> None:
    """Search a Greenhouse board's school list (keyless) — the exact names the form's School
    typeahead accepts. Author `school--0` in answers.yaml from this; the browser fill never guesses."""
    from harness.career.posting import load_form
    from harness.career.providers import fetch_greenhouse_schools

    if not board and slug:
        try:
            board = load_form(role_hunt_root(get_settings()) / "applications" / slug).board
        except ProviderError as e:
            _fail(str(e))
            raise typer.Exit(code=1) from e
    if not board:
        _fail("give --board <token> or --slug <application> (a Greenhouse posting)")
        raise typer.Exit(code=1)
    try:
        names = fetch_greenhouse_schools(board, term)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if not names:
        console.print(f"[yellow]no school matches {term!r} on board {board!r}[/yellow] — try fewer words")
        return
    for n in names:
        console.print(f"  {n}")


@app.command()
def apply(
    slug: str = typer.Argument(..., help="Application folder under role-hunt/applications/"),
    submit: bool = typer.Option(False, "--submit", help="The gate: record ONE submission (needs --confirm)"),
    confirm: bool = typer.Option(False, "--confirm", help="Actually perform --submit (one slug per run)"),
    via: str = typer.Option(
        "auto", "--via", help="auto | api | manual — auto tries the ATS API seam, then manual"
    ),
    receipt: str = typer.Option(
        None,
        "--receipt",
        help="manual transport: what came back (confirmation id / page text / email subject / time)",
    ),
    fill: bool = typer.Option(
        False,
        "--fill",
        help="After assembling: open the hosted form in a headed browser (dedicated profile), fill "
        "every packet field + upload the résumé, screenshot, and STOP before Submit",
    ),
    no_wait: bool = typer.Option(
        False, "--no-wait", help="--fill: close the window right after the screenshot (a dry run)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine output (the packet)"),
) -> None:
    """Assemble the application packet and STOP — or, with --submit --confirm, record ONE
    submission and advance applications.yaml to `applied`.

    Assemble: answers.yaml + the folder's files validated against form.json (required coverage ·
    option validity · files resolve · identity from the harness.yaml `career.applicant` block ·
    AI-policy provenance when the form carries an attestation) → packet.json + packet.md (the fill
    sheet). No network, no pipeline write; every failure is named.

    Submit: prints the envelope; without --confirm nothing happens. With it: the API seam (no ATS
    on the watchlist has a candidate-side API today — it fails loud with the browser path), else
    `manual` with a required --receipt → receipt.json + the pipeline row → applied. An existing
    receipt refuses a second run."""
    from harness.career.packet import Applicant, PacketError, assemble, write_packet
    from harness.career.posting import load_form
    from harness.career.submit import (
        SubmitNotAvailable,
        advance_pipeline,
        api_submit,
        existing_receipt,
        record_submission,
    )

    settings = get_settings()
    root = role_hunt_root(settings)
    folder = root / "applications" / slug
    if not folder.is_dir():
        _fail(f"no application folder at {folder} — `hn career posting <url> --slug {slug}` first")
        raise typer.Exit(code=1)
    # a stale render: resume.md newer than resume.pdf → re-render through the existing render path
    md, pdf = folder / "resume.md", folder / "resume.pdf"
    if md.exists() and (not pdf.exists() or md.stat().st_mtime > pdf.stat().st_mtime):
        console.print("[dim]resume.md is newer than resume.pdf — rendering[/dim]")
        _render_application(slug, "resume")
    applicant = Applicant.model_validate(overlay_get("career", "applicant", default={}) or {})
    try:
        packet = assemble(folder, applicant)
    except PacketError as e:
        console.print(f"[red]packet NOT assembled[/red] — {len(e.failures)} check(s) failed:")
        for f in e.failures:
            console.print(f"  ✗ {f}")
        raise typer.Exit(code=1) from e
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    from harness.career.comp import benchmark_note

    pj, pm = write_packet(folder, packet, benchmark_note(role_hunt_root(get_settings()), packet.company))
    if as_json and not submit:
        console.print_json(json.dumps(packet.model_dump()))
        return
    console.print(
        f"[green]packet assembled[/green] — {len(packet.fields)} fields · "
        f"{len(packet.files)} file(s) · checks: {', '.join(packet.checks)}"
    )
    console.print(f"  fill sheet → {pm}")
    if fill:
        if submit:
            _fail("--fill and --submit are separate acts: fill first, click Submit yourself, then record")
            raise typer.Exit(code=1)
        _browser_fill(folder, pj, no_wait=no_wait)
        return
    if not submit:
        console.print(
            f'[dim]  review it, then: hn career apply {slug} --submit --confirm --receipt "…"[/dim]'
        )
        return

    # ----- the gate -----
    prior = existing_receipt(folder)
    console.print(f"\n[bold]SUBMIT[/bold] {packet.company} — {packet.title}")
    console.print(f"  apply URL: {packet.apply_url}")
    console.print(f"  {len(packet.fields)} answers · {len(packet.files)} file(s) · transport: {via}")
    if prior is not None:
        _fail(
            f"already recorded as submitted {prior.submitted_at} via {prior.via} "
            f"({prior.receipt[:60]}) — a re-submission is a deliberate human act; "
            "remove receipt.json first"
        )
        raise typer.Exit(code=1)
    if not confirm:
        console.print("[yellow]not submitted[/yellow] — re-run with --confirm to record exactly this packet")
        return
    form = load_form(folder)
    rec = None
    try:
        if via in ("auto", "api"):
            try:
                rec = api_submit(form, packet)
            except SubmitNotAvailable as e:
                if via == "api" or not receipt:
                    _fail(str(e))
                    raise typer.Exit(code=1) from e
                via = "manual"
        if rec is None:
            rec = record_submission(folder, packet, via="manual", receipt_text=receipt or "")
        action = advance_pipeline(
            root / "applications.yaml",
            slug=slug,
            company=packet.company,
            role=packet.title,
            url=packet.apply_url,
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    console.print(f"[green]recorded[/green] {rec.submitted_at} via {rec.via} → {folder / 'receipt.json'}")
    console.print(f"  applications.yaml: row {action} → stage applied")


# repo root: src/harness/career/cli.py → parents[3] (the viz engine resolves render.js the same way)
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _git_main_checkout(root: Path) -> Path | None:
    """The shared worktree → main-checkout hop (`harness.paths.git_main_checkout`)."""
    from harness.paths import git_main_checkout

    return git_main_checkout(root)


def _bus_app_candidates() -> list[Path]:
    """Where `bus-app/` (the fill script + its node_modules) may live, in order: an explicit
    `HARNESS_BUS_APP_DIR`, this checkout, then the main checkout when this is a worktree — the
    shared node-tree resolver (`harness.paths`), which the viz renderer uses too.

    Why the chain: when run from a worktree without node_modules, the fill script and its deps
    are only on the main checkout, so this checkout alone would refuse."""
    from harness.paths import node_tree_candidates

    return node_tree_candidates("bus-app", "HARNESS_BUS_APP_DIR", repo_root=_REPO_ROOT)


def _resolve_bus_app() -> Path | None:
    """The first candidate that has Playwright installed, else None."""
    for c in _bus_app_candidates():
        if (c / "node_modules" / "playwright" / "package.json").exists() and (
            c / "scripts" / "apply-fill.mjs"
        ).exists():
            return c
    return None


def _browser_profile_dir() -> Path:
    """The dedicated, empty browser profile the fill runs under — cookies/history isolated from
    the operator's daily browser (the compartment posture). Created on first use, nothing else."""
    from harness.paths import harness_config_dir

    d = harness_config_dir() / "browser-profile"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_node(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """The one spawn seam (tests replace it). stderr streams through (the script's progress lines);
    stdout is captured for the RESULT line."""
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=False, stdout=subprocess.PIPE, text=True, env=_render_env()
    )


def _browser_fill(folder: Path, packet_json: Path, *, no_wait: bool = False) -> None:
    """Spawn the headed browser fill (bus-app/scripts/apply-fill.mjs) for an assembled packet.

    Zero new dependencies by design: Playwright ships in bus-app's devDependencies and its Chromium
    is already installed for the agent's eye. Everything is fail-loud + never auto-installed."""
    import shutil

    bus_app = _resolve_bus_app()
    if bus_app is None:
        looked = " · ".join(str(c) for c in _bus_app_candidates())
        _fail(
            "Playwright is not installed in any bus-app checkout — looked in: "
            f"{looked}. Run `cd <main checkout>/bus-app && npm ci` (never automatic), or set "
            "HARNESS_BUS_APP_DIR to a bus-app dir that has node_modules."
        )
        raise typer.Exit(code=1)
    fill_script = bus_app / "scripts" / "apply-fill.mjs"
    brew_node = Path("/opt/homebrew/bin/node")
    node = shutil.which("node") or (str(brew_node) if brew_node.exists() else None)
    if node is None:
        _fail("`node` not found on PATH — install Node (brew install node)")
        raise typer.Exit(code=1)
    shot = folder / "fill-shot.png"
    after = folder / "post-submit.png"
    cmd = [
        node,
        str(fill_script),
        "--packet",
        str(packet_json),
        "--profile",
        str(_browser_profile_dir()),
        "--shot",
        str(shot),
        "--after",
        str(after),
    ]
    if no_wait:
        cmd.append("--no-wait")
    console.print(
        "[bold]browser fill[/bold] — headed Chromium on the dedicated profile; the script "
        f"stops before Submit. Watch the window. [dim](runs from {bus_app})[/dim]"
    )
    res = _run_node(cmd, bus_app)
    line = next((ln for ln in (res.stdout or "").splitlines() if ln.startswith("RESULT ")), "")
    if not line:
        _fail(f"the fill script exited {res.returncode} without a result — see its output above")
        raise typer.Exit(code=1)
    result = json.loads(line[len("RESULT ") :])
    if not result.get("ok"):
        _fail(str(result.get("error")))
        raise typer.Exit(code=1)
    counts = result.get("counts", {})
    console.print(
        f"  filled {counts.get('filled', 0)} · skipped {counts.get('skipped', 0)} · "
        f"unresolved {counts.get('unresolved', 0)} → {shot}"
    )
    for r in result.get("report", []):
        if r["status"] == "unresolved":
            console.print(f"  [yellow]✗ {r['label']}[/yellow]: {r['note']}")
    if result.get("submitted"):
        console.print(
            f"[green]the page moved on[/green] → {result.get('final_url')}"
            + (f"  (post-submit shot: {result.get('after')})" if result.get("after") else "")
        )
        console.print(
            f"  now record it: hn career apply {folder.name} --submit --confirm "
            f'--receipt "{result.get("final_url")}"'
        )
    elif not no_wait:
        console.print("[dim]window closed without the page moving on — nothing recorded[/dim]")


_NOTES_SENTINEL = "<!-- ⬇ HAND-EDITED NOTES BELOW — PRESERVED ON REGEN ⬇ -->"


@app.command()
def company_profiles(
    write: bool = typer.Option(
        False, "--write", help="Write role-hunt/companies/{slug}.md (else dry-run summary)"
    ),
) -> None:
    """Generate per-company profile docs for the matrix-present companies (the Hiring-Map deep-link
    targets) — assembled from the corpus (watchlist tier/ATS + target-map axes/comp +
    fit-profiles excerpt + the latest scan's openings). The auto block is regenerated each run; a
    hand-edited notes section below the sentinel is PRESERVED (non-destructive). Read-only research."""
    svc = CareerService(get_settings().tracker_path, role_hunt_root=role_hunt_root(get_settings()))
    try:
        profiles = svc.company_profiles()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if not profiles:
        console.print("[yellow]no scan twin yet — run `hn career openings --write` first.[/yellow]")
        return
    base = role_hunt_root(get_settings()) / "companies"
    for d in profiles:
        auto = CareerService.company_profile_md(d)
        path = base / f"{d['slug']}.md"
        notes = ""
        if path.exists():
            old = path.read_text()
            if _NOTES_SENTINEL in old:
                notes = old.split(_NOTES_SENTINEL, 1)[1]
        if not notes.strip():
            notes = "\n\n## Notes\n\n_TBD — hand-edit; preserved across regens._\n"
        content = auto + "\n" + _NOTES_SENTINEL + notes
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    verb = "wrote" if write else "would write"
    console.print(
        f"{verb} [bold]{len(profiles)}[/bold] company profiles → role-hunt/companies/ "
        f"({', '.join(d['slug'] for d in profiles[:6])}{'…' if len(profiles) > 6 else ''})"
    )
    if not write:
        console.print("[dim]dry-run — pass --write to generate. Hand-edited notes are preserved.[/dim]")


@app.command()
def render(
    slug: str = typer.Argument(
        None,
        help="Application slug under role-hunt/applications/{slug}/ (per-application "
        "pandoc mode). Omit when using --design (master-CV Typst mode).",
    ),
    doc: str = typer.Option("resume", "--doc", help="resume | cover-letter | both (per-app mode)"),
    design: str = typer.Option(
        None,
        "--design",
        help="Master-CV mode: render docs/career/typst/active/<design>.typ via Typst → PDF. "
        "Use 'list' to enumerate the active design library.",
    ),
    docx: bool = typer.Option(
        False,
        "--docx",
        help="Master-CV mode: also (or only) emit the ATS-safe Word .docx from the markdown SoT "
        "via pandoc + the named-style reference-doc (design-independent). Pair with --design for "
        "PDF+docx; use alone for docx only.",
    ),
    out: str = typer.Option(
        None,
        "--out",
        help="Master-CV output path (PDF default docs/career/renders/pdf/<design>/<stem>.pdf — one "
        "folder per design, the file inside ready to send as-is; stem from the overlay "
        "career.resume.output_stem, default 'resume'; applies to the .docx when --docx is the sole target)",
    ),
) -> None:
    """Render a résumé/application artifact with deterministic checks.

    TWO modes:

    • Master-CV (--design / --docx): render the approved master from the content/template split
      (docs/career/). `--design` → Typst PDF (`typst compile --root <typst> active/<design>.typ`),
      the durable per-company/industry presentation layer (gorgeous, ATS-safe). `--docx` → the
      ATS-safe Word .docx (pandoc + a named-style reference-doc; design-independent — the single
      canonical Word fallback some portals require). Combine for both.

    • Per-application (slug): tailored markdown → PDF via pandoc/xelatex (the apply pipeline step).

    Either way the deterministic half runs here (page-budget ≤2 resume / ≤1 cover-letter via macOS
    mdls + toolchain/existence guards); the mandatory rasterize-and-Read eyeball is the agent's
    follow-up (the self-verifying render loop)."""
    if design is not None or docx:
        _render_master(design, out, docx)
        return
    if slug is None:
        _fail(
            "give a slug (per-application mode) or --design <name> (master-CV mode). "
            "`--design list` enumerates the design library."
        )
        raise typer.Exit(code=1)
    _render_application(slug, doc)


def _typst_root() -> Path:
    """The Typst résumé workspace (content/template split lives here; --root for compiles)."""
    return get_settings().tracker_path / "docs" / "career" / "typst"


def _resolve_design(name: str) -> Path | None:
    """Map a short design name (e.g. 'mono-accent') to its active/design-N-<name>.typ file.

    Designs are named active/design-<N>-<slug>.typ; match on the <slug> tail so the caller never
    needs the numeric prefix. Returns None if no unique match (caller lists the library)."""
    active = _typst_root() / "active"
    if not active.is_dir():
        return None
    # exact stem, then <slug>-tail match (design-10-mono-accent.typ ← 'mono-accent')
    cands = sorted(active.glob("*.typ"))
    for f in cands:
        if f.stem == name:
            return f
    tail = [f for f in cands if f.stem.split("-", 2)[-1] == name]
    return tail[0] if len(tail) == 1 else None


def _active_designs() -> list[str]:
    """Short names of the active design library (the <slug> tail of each design-N-<slug>.typ)."""
    active = _typst_root() / "active"
    if not active.is_dir():
        return []
    return sorted(f.stem.split("-", 2)[-1] for f in active.glob("design-*.typ"))


def _render_master(design: str | None, out_arg: str | None, docx: bool = False) -> None:
    """Master-CV render orchestrator: Typst PDF (--design) and/or the ATS-safe Word .docx (--docx).

    The two targets are independent: the PDF is per-design (the gorgeous human-facing artifact); the
    docx is design-independent (one canonical ATS-safe Word fallback). `--out` controls the PDF when
    a design is given, otherwise the docx."""
    if design == "list":
        _list_designs()
        return
    if design is not None:
        _render_master_pdf(design, out_arg)
    if docx:
        # docx mirrors a design too; default to the mono-accent default when --docx is used alone.
        _render_master_docx(design or "mono-accent", out_arg if design is None else None)


def _list_designs() -> None:
    designs = _active_designs()
    if not designs:
        _fail(f"no designs in {_typst_root() / 'active'} — is the Typst workspace present?")
        raise typer.Exit(code=1)
    console.print("[bold]active design library[/bold] (docs/career/typst/active/):")
    for d in designs:
        star = " ⭐ default" if d == "mono-accent" else ""
        console.print(f"  • {d}{star}")


def _render_master_pdf(design: str, out_arg: str | None) -> None:
    """Master-CV Typst render: active/<design>.typ → PDF, --root the typst workspace."""
    import shutil
    import subprocess

    designs = _active_designs()
    if not designs:
        _fail(f"no designs in {_typst_root() / 'active'} — is the Typst workspace present?")
        raise typer.Exit(code=1)

    if shutil.which("typst") is None and not Path("/opt/homebrew/bin/typst").exists():
        _fail(
            "typst not installed — `brew install typst` (the résumé render toolchain). "
            "Install is never automatic — run it yourself."
        )
        raise typer.Exit(code=1)

    design_file = _resolve_design(design)
    if design_file is None:
        _fail(f"no unique design {design!r} in active/ — known: {', '.join(designs)} (or `--design list`)")
        raise typer.Exit(code=1)

    root = _typst_root()
    if out_arg:
        out = Path(out_arg).expanduser()
    else:
        # one folder per design; the file name carries no theme, so the copy in the folder is the one
        # to send as-is
        out = (
            get_settings().tracker_path
            / "docs"
            / "career"
            / "renders"
            / "pdf"
            / design
            / f"{_resume_output_stem()}.pdf"
        )
    out.parent.mkdir(parents=True, exist_ok=True)

    # Typst's per-file sandbox blocks the parent ../resume-content.typ import → --root the workspace.
    cmd = ["typst", "compile", "--root", str(root), str(design_file), str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, env=_render_env())
    if res.returncode != 0:
        _fail(f"typst compile failed for {design_file.name}: {res.stderr.strip()[:400]}")
        raise typer.Exit(code=1)

    # Master-CV mode is INFORMATIONAL on page count, not gated: the master is the full content
    # library you tailor DOWN from — it may legitimately run >2 pages. The ≤2 relevance-cut is a
    # per-application discipline (enforced in _render_application), not a master constraint.
    pages = _pdf_page_count(out)
    if pages is None:
        console.print(
            f"[yellow]{out.name}: rendered; page count unavailable (mdls indexing lag) — "
            f"confirm in the eyeball step[/yellow]"
        )
    elif pages > 2:
        console.print(
            f"[yellow]{out.name}: {pages} pages (master CV — full content library; "
            f"tailored application renders cut to ≤2)[/yellow]"
        )
    else:
        console.print(f"[green]{out.name}: {pages} page(s) ✓[/green]")
    console.print(f"[dim]  → {out}[/dim]")
    console.print(
        f"[dim]  self-verify: pdftoppm -png -f 1 -l 1 -r 120 '{out}' /tmp/cv && "
        f"Read /tmp/cv-1.png  (orphaned headers, spacing, overflow)[/dim]"
    )


def _render_master_docx(design: str, out_arg: str | None) -> None:
    """Design-matched ATS-safe Word .docx from the markdown SoT via pandoc + a per-design reference-doc.

    `pandoc md -> docx --reference-doc=X` inherits X's NAMED styles, so each design's reference-doc
    (docs/career/docx-template/reference-<design>.docx) DEFINES the look pandoc's output adopts —
    themed to MIRROR the matching Typst PDF design (Heading1 name font/align/color · Heading2 section
    font/case/accent + rule · compact body + tight bullets · margins). The docx is the same design
    FAMILY as its PDF, font-substituted (Arial←Helvetica, Georgia←Libertinus, Courier New←Menlo) —
    not pixel-identical (different engines; ATS strips styling anyway). Regenerate the reference-docs
    via docs/career/docx-template/build_reference.py when a design's look changes."""
    import shutil
    import subprocess

    if shutil.which("pandoc") is None and not Path("/opt/homebrew/bin/pandoc").exists():
        _fail(
            "pandoc not installed — `brew install pandoc` (the résumé render toolchain). "
            "Install is never automatic — run it yourself."
        )
        raise typer.Exit(code=1)

    career = get_settings().tracker_path / "docs" / "career"
    src = career / _resume_source_md()
    ref = career / "docx-template" / f"reference-{design}.docx"
    if not src.exists():
        _fail(f"master CV markdown not found at {src}")
        raise typer.Exit(code=1)
    if not ref.exists():
        _fail(
            f"no docx reference-doc for design {design!r} at {ref} — regenerate the set: "
            f"python3 {ref.parent / 'build_reference.py'} (themes track ../typst/active/)"
        )
        raise typer.Exit(code=1)

    out = (
        Path(out_arg).expanduser()
        if out_arg
        else career / "renders" / "docx" / design / f"{_resume_output_stem()}.docx"
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["pandoc", str(src), f"--reference-doc={ref}", "-o", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, env=_render_env())
    if res.returncode != 0:
        _fail(f"pandoc docx render failed: {res.stderr.strip()[:400]}")
        raise typer.Exit(code=1)

    console.print(
        f"[green]{out.name}: ATS-safe Word .docx ✓[/green] [dim](design-matched via {ref.name})[/dim]"
    )
    console.print(f"[dim]  → {out}[/dim]")
    # Self-verify (no Word needed): LibreOffice headless → PDF → rasterize → Read the PNG.
    soffice = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if Path(soffice).exists():
        console.print(
            f"[dim]  self-verify: '{soffice}' --headless --convert-to pdf --outdir /tmp '{out}' "
            f"&& pdftoppm -png -f 1 -l 1 -r 130 '/tmp/{out.stem}.pdf' /tmp/cvdocx "
            f"&& Read /tmp/cvdocx-1.png[/dim]"
        )


def _render_application(slug: str, doc: str) -> None:
    """Per-application pandoc/xelatex render (the apply-pipeline step)."""
    import shutil
    import subprocess

    if shutil.which("pandoc") is None:
        _fail(
            "pandoc not installed — `brew install pandoc basictex` (the documented resume "
            "toolchain). Install is never automatic — run it yourself."
        )
        raise typer.Exit(code=1)

    base = get_settings().tracker_path / "role-hunt" / "applications" / slug
    if not base.is_dir():
        _fail(f"no application folder at {base} — create role-hunt/applications/{slug}/ first")
        raise typer.Exit(code=1)

    docs = ["resume", "cover-letter"] if doc == "both" else [doc]
    budgets = {"resume": 2, "cover-letter": 1}
    failures = 0
    for d in docs:
        src = base / f"{d}.md"
        if not src.exists():
            _fail(f"{src.name} not found in {base}")
            failures += 1
            continue
        out = base / f"{d}.pdf"
        cmd = [
            "pandoc",
            str(src),
            "-o",
            str(out),
            "--pdf-engine=xelatex",
            "-V",
            "geometry:margin=0.75in",
            "-V",
            "mainfont=Helvetica Neue",
            "-V",
            "fontsize=11pt",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, env=_render_env())
        if res.returncode != 0:
            _fail(f"pandoc failed for {src.name}: {res.stderr.strip()[:400]}")
            failures += 1
            continue
        pages = _pdf_page_count(out)
        budget = budgets.get(d, 2)
        if pages is None:
            console.print(
                f"[yellow]{out.name}: rendered; page count unavailable (mdls) — "
                f"verify in the eyeball step[/yellow]"
            )
        elif pages > budget:
            _fail(
                f"{out.name}: {pages} pages — OVER the {budget}-page budget. Cut content "
                f"(relevance-weighted), never compress layout."
            )
            failures += 1
        else:
            console.print(f"[green]{out.name}: {pages} page(s) — within budget ✓[/green]")
        console.print(f"[dim]  → {out}  (now: agent eyeball — orphaned headers, spacing)[/dim]")
    if failures:
        raise typer.Exit(code=1)


def _render_env() -> dict[str, str]:
    """Render subprocess env with the toolchain dirs resolved explicitly (the launch-context
    doctrine — GUI/launchd/global-install launches inherit a minimal PATH). MacTeX → texbin;
    Homebrew (typst/pandoc/poppler) → /opt/homebrew/bin. Existence-checked, dedup'd."""
    import os

    env = dict(os.environ)
    path = env.get("PATH", "")
    for tooldir in ("/opt/homebrew/bin", "/Library/TeX/texbin"):
        if Path(tooldir).is_dir() and tooldir not in path:
            path = path + os.pathsep + tooldir if path else tooldir
    env["PATH"] = path
    return env


def _pdf_page_count(path: Path) -> int | None:
    """macOS-native page count via Spotlight metadata (no extra dependency)."""
    import subprocess

    res = subprocess.run(
        ["mdls", "-name", "kMDItemNumberOfPages", "-raw", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    out = res.stdout.strip()
    return int(out) if out.isdigit() else None


@app.command()
def viz(
    diagram: str = typer.Argument(..., help="Diagram type (matrix | compare | rank-bar | …)"),
    dest: str = typer.Option(
        ...,
        "--dest",
        help="Vault path under tracker/ — SVG written to {dest}/visuals/ (e.g. 'role-hunt')",
    ),
    data_file: str = typer.Option(..., "--data", help="Path to the diagram's JSON data"),
    name: str = typer.Option(..., "--name", help="Output file stem"),
    theme: str = typer.Option(
        "light", "--theme", help="Render theme: light (default) | instrument (the bus-app console palette)"
    ),
) -> None:
    """Render a D3 diagram into the career corpus ({dest}/visuals/{name}.svg) + print the embed.

    Reuses the shared render engine — all harness diagram types (the target-map heatmap is
    `matrix`; finalist radars are `compare`). Read-only artifact; nothing here applies anywhere.
    """
    if diagram not in KNOWN_TYPES:
        _fail(f"unknown diagram type {diagram!r}; known: {', '.join(KNOWN_TYPES)}")
        raise typer.Exit(code=1)
    try:
        data = json.loads(Path(data_file).read_text())
    except (OSError, ValueError) as e:
        _fail(f"could not read --data {data_file!r}: {e}")
        raise typer.Exit(code=1) from e
    tracker = get_settings().tracker_path
    out = tracker / dest / "visuals" / f"{name}.svg"
    try:
        written = render_diagram(diagram, data, out, theme=theme)
    except VizError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    rel = written.resolve().relative_to(tracker.resolve())
    console.print(f"wrote {written}")
    console.print("\nObsidian embed (paste into the doc):")
    console.print(f"![[{rel.as_posix()}|640]]", markup=False)  # markup=False: keep [[...]] literal


# ————— the comp sense + the levels.fyi index —————


def _ladder_feed(
    root: Path, *, limit: int | None, include_pool: bool, dry_run: bool, notify: bool, as_json: bool
) -> None:
    """`comp --refresh-stale`: plan → (dry-run: print it) → run → log → one bus event."""
    from harness.career import ladder_feed as feed

    try:
        plan = feed.plan_feed(root, get_settings().tracker_path, limit=limit, include_pool=include_pool)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if dry_run:
        if as_json:
            console.print_json(json.dumps(plan.model_dump()))
            return
        console.print(
            f"[bold]ladder feed — plan[/bold] · {plan.family} · {plan.location} · cap {plan.limit} "
            f"· {plan.fresh} fresh · {len(plan.candidates)} candidate(s)"
        )
        for i, c in enumerate(plan.candidates, 1):
            tag = "[green]this run[/green]" if i <= plan.limit else "[dim]next run[/dim]"
            console.print(f"  {i:>3}. {c.company} ({c.slug}) — {c.reason} · {c.detail} · {tag}")
        for s in plan.unresolved:
            console.print(f"  [yellow]skip[/yellow] {s.company}: {s.reason}")
        if not plan.candidates:
            console.print("[dim]nothing to do — every ladder is fresh[/dim]")
        return
    res = feed.run_feed(root, plan)
    published = feed.publish_summary(res) if notify else False
    if as_json:
        console.print_json(json.dumps({**res.model_dump(), "summary": res.summary(), "published": published}))
        return
    for ent in res.entries:
        color = {"fetched": "green", "error": "red", "skipped": "dim"}[ent.action]
        console.print(f"  [{color}]{ent.action:<7}[/{color}] {ent.slug} — {ent.reason}")
    for s in plan.unresolved:
        console.print(f"  [yellow]skip[/yellow]    {s.company}: {s.reason}")
    console.print(f"[bold]{res.summary()}[/bold]")
    if plan.deferred:
        console.print(f"[dim]{len(plan.deferred)} more wait for the next run (cap {plan.limit})[/dim]")
    if not res.entries:
        console.print("[dim]nothing to do — every ladder is fresh; no event published[/dim]")
    elif notify:
        state = "published" if published else "nothing published"
        console.print(f"[dim]bus: {state} · log {feed.log_path(root)}[/dim]")


@app.command()
def comp(
    company: str = typer.Argument(
        None, help="Company name or levels.fyi slug (e.g. 'Example Co' / example-co)"
    ),
    family: str = typer.Option(
        "software-engineer",
        "--family",
        "-f",
        help="levels.fyi job-family slug (`hn career families` lists them; SRE is a focus, not a family)",
    ),
    location: str = typer.Option(
        "united-states",
        "--location",
        "-l",
        help="Geo pin (levels.fyi location slug: united-states, san-francisco-bay-area, …). Mandatory — "
        "the unpinned page localizes on the caller's IP; a non-USD page is refused, never printed",
    ),
    refresh: bool = typer.Option(
        False, "--refresh", help="Re-fetch even if the vault copy is inside the TTL"
    ),
    lca: str = typer.Option(
        None,
        "--lca",
        help="Rail 2 — filed H-1B wages from the DOL LCA disclosure workbook for this EMPLOYER "
        "(name substring)",
    ),
    title: str = typer.Option(None, "--title", help="--lca: job-title substring filter"),
    state: str = typer.Option(None, "--state", help="--lca: worksite state (WA, CA…)"),
    limit: int = typer.Option(
        None,
        "--limit",
        help="--lca: rows to print (0 = all; default 25) · --refresh-stale: the fetch cap per run "
        "(default `career.levels.feed_limit` in the overlay, else 40)",
    ),
    source: str = typer.Option("levels", "--source", help="levels (default) | blind (declined — says why)"),
    refresh_stale: bool = typer.Option(
        False,
        "--refresh-stale",
        help="The standing ladder feed: refresh the stalest cached ladders + fetch ladders for "
        "watchlist companies without one, at most --limit per run, paced; stops on a throttle",
    ),
    pool: bool = typer.Option(
        False,
        "--pool",
        help="--refresh-stale: also the newest pool surveys' in-play companies without a ladder",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="--refresh-stale: print the plan (who, why, in order) and fetch nothing"
    ),
    notify: bool = typer.Option(
        False, "--notify", help="--refresh-stale: publish one bus info event with the run's counts"
    ),
    report: int = typer.Option(
        None,
        "--report",
        help="The reports rail: the year-end pay report + the metro/US percentiles → the anchor read",
    ),
    all_cached: bool = typer.Option(
        False, "--all", help="Every cached ladder in role-hunt/comp/ (no network; the Ladders tab)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine output"),
) -> None:
    """A company's engineering pay ladder from levels.fyi's sanctioned keyless markdown route
    (level → median TC, YOE, the median base/stock/bonus mix), geo-PINNED, cached in
    role-hunt/comp/<slug>.json for a week, attribution on every render. `--lca <employer>` reads
    the DOL LCA disclosure workbook (filed H-1B wages by title/worksite) from the local drop.
    Read-only; nothing here applies anywhere."""
    from harness.career import comp as compmod
    from harness.career import lca as lcamod

    if source.lower() == "blind":
        _fail(compmod.blind_refusal())
        raise typer.Exit(code=1)
    if source.lower() != "levels":
        _fail(f"unknown --source {source!r}: levels | blind")
        raise typer.Exit(code=1)
    root = role_hunt_root(get_settings())
    if refresh_stale:
        _ladder_feed(root, limit=limit, include_pool=pool, dry_run=dry_run, notify=notify, as_json=as_json)
        return
    if limit is None:
        limit = 25
    if all_cached:
        ladders = compmod.cached_ladders(root)
        if as_json:
            console.print_json(json.dumps(compmod.ladders_contract(root, ladders)))
            return
        for lad in ladders:
            console.print(compmod.ladder_markdown(lad), markup=False)
            console.print()
        console.print(f"[dim]{len(ladders)} cached ladder(s) in {compmod.comp_dir(root)}[/dim]")
        return
    if report is not None or (company and company.lower() == "report"):
        from harness.career import report as repmod

        try:
            anchor = compmod.anchor_tc()
            r = repmod.build_report(root, year=report, anchor=anchor, refresh=refresh)
        except ProviderError as e:
            _fail(str(e))
            raise typer.Exit(code=1) from e
        if as_json:
            console.print_json(json.dumps(r))
            return
        how = "vault copy" if r.get("from_cache") else "fetched now"
        console.print(f"[bold]levels.fyi pay report {r['year']}[/bold] · {how} · anchor ${r['anchor']:,}")
        for ln in r["anchor_read"]:
            console.print(f"  • {ln}")
        met, us, rep = r["metro"], r["us"], r["report"]
        console.print(
            f"\n{met.get('location') or r['metro']}: median {compmod.fmt_money(met.get('median'))} · "
            f"p25 {compmod.fmt_money(met.get('p25'))} · p75 {compmod.fmt_money(met.get('p75'))} · "
            f"p90 {compmod.fmt_money(met.get('p90'))}"
        )
        console.print(
            f"United States: median {compmod.fmt_money(us.get('median'))} · "
            f"p25 {compmod.fmt_money(us.get('p25'))} · p75 {compmod.fmt_money(us.get('p75'))} · "
            f"p90 {compmod.fmt_money(us.get('p90'))}"
        )
        if rep.get("levels"):
            t = Table(show_header=True, header_style="bold", pad_edge=False, title="Median TC by level (SWE)")
            yrs = rep.get("level_years") or ["prior", "current"]
            for col in ("Level", str(yrs[0]), str(yrs[1]), "Change"):
                t.add_column(col)
            for k, v in rep["levels"].items():
                t.add_row(k, compmod.fmt_money(v["prior"]), compmod.fmt_money(v["current"]), v["change"])
            console.print(t)
        for name, rows in rep.get("top_by_level", {}).items():
            if rows and ("Staff" in name or "Principal" in name):
                tops = " · ".join(f"{c['company']} ${c['total']:,}" for c in rows)
                console.print(f"[bold]{name}[/bold]: {tops}")
        console.print(f"[dim]wrote role-hunt/reference/pay-report-{r['year']}.md · {r['attribution']}[/dim]")
        return
    if lca:
        try:
            wb = lcamod.ensure_workbook()
            slim = lcamod.ingest(wb)
            rows = lcamod.query(slim, lca, title=title, state=state, limit=limit)
        except ProviderError as e:
            _fail(str(e))
            raise typer.Exit(code=1) from e
        src_line = f"Source: DOL OFLC LCA disclosure data — {wb.name} (local copy)"
        if as_json:
            console.print_json(
                json.dumps({"workbook": wb.name, "employer": lca, "rows": rows, "source": src_line})
            )
            return
        if not rows:
            console.print(
                f"[yellow]no LCA rows for employer {lca!r}[/yellow]"
                + (f" title~{title!r}" if title else "")
                + (f" state={state}" if state else "")
                + f" in {wb.name}"
            )
            return
        t = Table(
            show_header=True,
            header_style="bold",
            pad_edge=False,
            title=f"LCA filed wages — {lca} · {wb.name}",
        )
        for col in ("Employer", "Title", "SOC", "Worksite", "Wage", "Unit", "Annual", "Status", "Decided"):
            t.add_column(col)
        for r in rows:
            t.add_row(
                r.get("EMPLOYER_NAME", "")[:34],
                r.get("JOB_TITLE", "")[:40],
                r.get("SOC_CODE", ""),
                f"{r.get('WORKSITE_CITY', '')}, {r.get('WORKSITE_STATE', '')}",
                r.get("WAGE_RATE_OF_PAY_FROM", ""),
                r.get("WAGE_UNIT_OF_PAY", ""),
                compmod.fmt_money(r.get("annual")),
                r.get("CASE_STATUS", ""),
                r.get("DECISION_DATE", ""),
            )
        console.print(t)
        console.print(
            f"[dim]{src_line} · wage = the filed rate-of-pay FROM figure; annual = ×2080 for hourly[/dim]"
        )
        return
    if not company:
        _fail("give a company (`hn career comp example-co`) or `--lca <employer>`")
        raise typer.Exit(code=1)
    root = role_hunt_root(get_settings())
    try:
        ladder, from_cache = compmod.get_ladder(
            root, company, family=family, location=location, refresh=refresh
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(json.dumps({**ladder.model_dump(), "from_cache": from_cache}))
        return
    fam = ladder.family.replace("-", " ").title()
    loc = ladder.location.replace("-", " ").title()
    how = f"vault copy, fetched {ladder.fetched_at[:10]}" if from_cache else "fetched now"
    console.print(
        f"[bold]{ladder.company}[/bold] ({ladder.slug}) — {fam} · {loc} · {ladder.currency} · {how}"
    )
    line = f"Median TC [bold]{compmod.fmt_money(ladder.median_tc)}[/bold]"
    if ladder.high_tc:
        line += f" · high {compmod.fmt_money(ladder.high_tc)}"
    if ladder.last_updated:
        line += f" · site updated {ladder.last_updated}"
    console.print(line)
    if ladder.mix:
        m = ladder.mix
        console.print(
            f"Median mix: base {compmod.fmt_money(m.get('base'))} · stock {compmod.fmt_money(m.get('stock'))}"
            f" · bonus {compmod.fmt_money(m.get('bonus'))}"
        )
    if ladder.levels:
        t = Table(show_header=True, header_style="bold", pad_edge=False)
        t.add_column("Level")
        t.add_column("Median TC", justify="right")
        t.add_column("YOE")
        t.add_column("Title (site-stated)")
        for lv in ladder.levels:
            t.add_row(lv.level, compmod.fmt_money(lv.median_tc), lv.yoe or "—", lv.title or "—")
        console.print(t)
    else:
        console.print(f"[yellow]{ladder.note}[/yellow]")
    console.print(f"[dim]{ladder.attribution} · {ladder.url}[/dim]")


@app.command("levels-index")
def levels_index(
    refresh: bool = typer.Option(
        False, "--refresh", help="(Re)build from the sitemaps — ~104 files, minutes"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine output (counts + stamps)"),
) -> None:
    """The levels.fyi slug / job-family / industry / leaderboard index, built from the sitemaps
    (streamed, one file per request, paced) into role-hunt/reference/levels-index.json. Without
    --refresh: the current index's status. `families` / `slugs` / `industries` query it."""
    from harness.career import levels_index as lix

    root = role_hunt_root(get_settings())
    try:
        if refresh:
            console.print("[dim]streaming the levels.fyi sitemaps (paced; ~2.5 GB over the wire)…[/dim]")
            index = lix.build_index(root, progress=lambda u: console.print(f"[dim]  {u}[/dim]"))
        else:
            loaded = lix.load_index(root)
            if loaded is None:
                _fail(f"no index at {lix.index_path(root)} — run `hn career levels-index --refresh`")
                raise typer.Exit(code=1)
            index = loaded
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    status = {
        "path": str(lix.index_path(root)),
        "fetched_at": index.get("fetched_at"),
        "sitemap_lastmod": index.get("sitemap_lastmod"),
        "counts": index.get("counts"),
    }
    if as_json:
        console.print_json(json.dumps(status))
        return
    c = index.get("counts", {})
    console.print(
        f"levels index · fetched {index.get('fetched_at', '?')[:10]} · sitemap lastmod "
        f"{index.get('sitemap_lastmod', '?')[:10]} · {c.get('companies', 0):,} companies · "
        f"{c.get('families', 0)} families · {c.get('industries', 0)} industries · "
        f"{c.get('files', 0)} files / {c.get('urls', 0):,} urls"
    )
    console.print(f"[dim]{status['path']}[/dim]")


def _load_index_or_fail() -> dict[str, object]:
    from harness.career import levels_index as lix

    root = role_hunt_root(get_settings())
    try:
        index = lix.load_index(root)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if index is None:
        _fail("no levels index yet — run `hn career levels-index --refresh` once")
        raise typer.Exit(code=1)
    return index


@app.command()
def families(
    grep: str = typer.Option(None, "--grep", help="Substring filter on the family slug"),
    as_json: bool = typer.Option(False, "--json", help="Machine output"),
) -> None:
    """levels.fyi job-family slugs from the index, with the sub-path forms each carries
    (locations/ geo slugs, focus/ areas). `--grep engineer` narrows. The family slug is what
    `comp --family` and the ladder route take; a focus is a filter on the site, not a route."""
    from harness.career import levels_index as lix

    index = _load_index_or_fail()
    rows = lix.families(index, grep)
    if as_json:
        console.print_json(json.dumps(dict(rows)))
        return
    if not rows:
        console.print(f"[yellow]no family matches {grep!r}[/yellow]")
        return
    t = Table(show_header=True, header_style="bold", pad_edge=False)
    t.add_column("Family slug")
    t.add_column("Locations", justify="right")
    t.add_column("Focus areas")
    t.add_column("Example location")
    for slug, v in rows:
        locs = v.get("locations", [])
        focus = v.get("focus", [])
        t.add_row(
            slug,
            str(len(locs)),
            ", ".join(focus[:8]) + (" …" if len(focus) > 8 else ""),
            locs[0] if locs else "—",
        )
    console.print(t)
    console.print(
        f"[dim]{len(rows)} families · route: /t/<family>[.md] · /t/<family>/locations/<geo>[.md][/dim]"
    )


@app.command()
def slugs(
    name: str = typer.Argument(None, help="Company name to resolve (e.g. 'Example Co')"),
    watchlist: bool = typer.Option(False, "--watchlist", help="Resolve every watchlist company"),
    as_json: bool = typer.Option(False, "--json", help="Machine output"),
) -> None:
    """Company name → levels.fyi slug from the sitemap index (exact slugified hit, else the
    nearest candidates). `--watchlist` resolves every role-hunt/watchlist.yml company and says
    'no levels page' where nothing matches."""
    from harness.career import levels_index as lix

    index = _load_index_or_fail()
    names: list[str] = []
    if watchlist:
        svc = CareerService(get_settings().tracker_path, role_hunt_root=role_hunt_root(get_settings()))
        try:
            names = [c.name for c in svc.load().companies]
        except ProviderError as e:
            _fail(str(e))
            raise typer.Exit(code=1) from e
    elif name:
        names = [name]
    else:
        _fail("give a company name or --watchlist")
        raise typer.Exit(code=1)
    out = []
    for n in names:
        hit, cands = lix.resolve_slug_in_index(index, n)
        out.append({"name": n, "slug": hit, "candidates": cands})
    if as_json:
        console.print_json(json.dumps(out))
        return
    for r in out:
        if r["slug"]:
            console.print(f"  {r['name']} → [bold]{r['slug']}[/bold]")
        elif r["candidates"]:
            near = ", ".join(r["candidates"][:6])
            console.print(f"  {r['name']} → [yellow]no exact slug[/yellow]; nearest: {near}")
        else:
            console.print(f"  {r['name']} → [red]no levels page[/red]")


@app.command()
def industries(
    as_json: bool = typer.Option(False, "--json", help="Machine output"),
) -> None:
    """levels.fyi industry pages from the index — the pond indices by sector (gaming, fintech,
    semiconductors, …) with their geo sub-pages. Browse: https://www.levels.fyi/industry/<slug>."""
    from harness.career import levels_index as lix
    from harness.career.providers.levels import BASE

    index = _load_index_or_fail()
    rows = lix.industries(index)
    if as_json:
        console.print_json(
            json.dumps(
                [
                    {"slug": s, "url": f"{BASE}/industry/{s}", "locations": v.get("locations", [])}
                    for s, v in rows
                ]
            )
        )
        return
    for s, v in rows:
        console.print(f"  {s:<28} {BASE}/industry/{s}   [dim]{len(v.get('locations', []))} geo pages[/dim]")
    console.print(f"[dim]{len(rows)} industries[/dim]")


@app.command()
def pool(
    level: str = typer.Option("staff", "--level", help="all | entry | mid | senior | staff | principal"),
    location: str = typer.Option(
        "us",
        "--location",
        "-l",
        help="us | an alias from career.comp.locations | '<kind>/<Geo>' (region/San-Francisco-Bay-Area) "
        "| an index geo name",
    ),
    family: str = typer.Option("software-engineer", "--family", "-f", help="Leaderboard family slug"),
    write: bool = typer.Option(False, "--write", help="Persist role-hunt/pool/<date>-<cut>.{md,json} + diff"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-fetch the cut (else a 24h cached page)"),
    from_survey: bool = typer.Option(
        False,
        "--survey",
        help="No network: the WHOLE pool — every persisted survey + every cached ladder merged, one item "
        "per company (the Pool tab's source); --single = the newest persisted cut alone",
    ),
    single: bool = typer.Option(
        False, "--single", help="With --survey: the newest single cut, not the merge"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine output (rows + modifiers + panes)"),
) -> None:
    """The pool — one levels.fyi leaderboard cut read through the item model: base ilvl
    = median TC vs the comp anchor, named modifiers from the registry (machine / agent / operator tiers),
    set bonuses, the one-tier cap, rarity engine-side; joined to the watchlist, the finance bench and
    the screen pre-flags. A survey — never writes the watchlist."""
    from harness.career import pool as poolmod

    root = role_hunt_root(get_settings())
    if from_survey:
        try:
            if single:
                saved = poolmod.latest_survey(root)
            else:
                from harness.career.roles import attach_openings

                tracker = get_settings().tracker_path
                saved = attach_openings(tracker, root, poolmod.merge_pool(tracker, root))
        except ProviderError as e:
            _fail(str(e))
            raise typer.Exit(code=1) from e
        if as_json:
            console.print_json(json.dumps(saved))
        else:
            what = (
                f"merged: {len(saved['sources']['surveys'])} surveys + {saved['sources']['ladders']} ladders"
                if saved.get("merged")
                else saved.get("survey_file")
            )
            console.print(
                f"[bold]Pool[/bold] {what} · {len(saved.get('rows', []))} rows · "
                f"{len(saved.get('screen_gated', []))} screen-gated · pass --json for the contract"
            )
        return
    try:
        survey = poolmod.build_pool(
            get_settings().tracker_path, root, family=family, level=level, location=location, refresh=refresh
        )
        prior = poolmod.prior_survey(root, survey["cut"]["key"], before=survey["as_of"])
        diff = poolmod.diff_surveys(survey, prior)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    survey["diff"] = diff
    if as_json:
        console.print_json(json.dumps(survey))
        if write:
            poolmod.write_survey(root, survey, diff)
        return
    c = survey["cut"]
    console.print(
        f"[bold]Pool[/bold] — {c['family']} · {c['level']} · {c['geo']} · {survey['fetched']} · "
        f"anchor ${survey['anchor']:,}"
    )
    t = Table(show_header=True, header_style="bold", pad_edge=False)
    for col, just in (
        ("#", "right"),
        ("Company", "left"),
        ("Level", "left"),
        ("Median TC", "right"),
        ("Base", "right"),
        ("Mods", "right"),
        ("Final", "right"),
        ("Rarity", "left"),
        ("WL", "left"),
        ("Bench", "left"),
        ("Flag", "left"),
    ):
        t.add_column(col, justify=just)  # type: ignore[arg-type]
    colors = {"legendary": "orange1", "epic": "magenta", "rare": "blue", "common": "green", "poor": "dim"}
    for r in survey["rows"]:
        cap = " ⌈" if r["capped"] else ""
        t.add_row(
            str(r["rank"]),
            r["company"],
            r["label"] or "—",
            f"${r['median_tc']:,}",
            f"{r['base_ilvl']:+d}",
            f"{r['modifier_sum']:+d}",
            f"{r['final_ilvl']:+d}{cap}",
            f"[{colors.get(r['rarity'], '')}]{r['rarity']}[/]",
            "yes" if r["on_watchlist"] else "—",
            r["ticker"] if r["bench"] else "—",
            r["screen_flag"],
        )
    console.print(t)
    for r in survey["rows"]:
        if r["modifiers"] or r["set_bonuses"]:
            parts = [f"{m['name']} {m['value']:+d} ({m['how']})" for m in r["modifiers"]]
            parts += [f"{b['name']} ({b['have']}/{b['count']}) +{b['bonus']}" for b in r["set_bonuses"]]
            console.print(
                f"  [dim]{r['company']}:[/dim] "
                + " · ".join(parts)
                + (f" · [yellow]capped at {r['capped_at']}[/yellow]" if r["capped"] else "")
            )
    if survey["screen_gated"]:
        console.print("\n[bold]Screen-gated[/bold] (visible, marked, no arithmetic):")
        for r in survey["screen_gated"]:
            console.print(f"  {r['rank']}. {r['company']} ${r['median_tc']:,} — {r['screen_flag']}")
    if diff:
        new_names = ", ".join(diff["new_entrants"]) or "—"
        console.print(
            f"\n[bold]Delta vs {diff['prior_as_of']}[/bold]: new {new_names} · "
            f"dropped {', '.join(diff['dropped']) or '—'} · "
            f"rank moves {len(diff['rank_moves'])} · rarity changes {len(diff['rarity_changes'])}"
        )
    console.print(f"[dim]Agency: {poolmod.AGENCY_LINE}. {survey['constitution']}[/dim]")
    console.print(f"[dim]{survey['attribution']} · {c['url']}[/dim]")
    if write:
        mp, jp = poolmod.write_survey(root, survey, diff)
        console.print(f"wrote {mp} (+ {jp.name})")
