"""The application pipeline: where the search stands, the tracked applications, posting + form
capture, the school-name lookup, and packet assembly (with the browser fill and the one gated
submit record)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import typer
from rich.table import Table

from harness.career.cli.common import console
from harness.career.cli.render import _render_application, _render_env
from harness.career.config import get_settings, role_hunt_root
from harness.career.service import CareerService
from harness.errors import HarnessError, ProviderError, UsageError
from harness.paths import REPO_ROOT
from harness.settings import overlay_get


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
    rep = build_campaign(s.tracker_path, role_hunt_root(s), mail_limit=mail_limit)
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


def applications(
    as_json: bool = typer.Option(
        False, "--json", help="Machine output (pipeline rows) — the CAREER-dashboard contract"
    ),
) -> None:
    """The application/opportunity pipeline (`role-hunt/applications.yaml`, hand-edited
    corpus). A missing file = empty pipeline. The CAREER dashboard's PIPELINE table reads this;
    advancing a stage is the user's edit (read-rich / execute-gated)."""
    from harness.career.applications import load_applications

    pipe = load_applications(get_settings().tracker_path, root=role_hunt_root(get_settings()))
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
        board = load_form(role_hunt_root(get_settings()) / "applications" / slug).board
    if not board:
        raise UsageError("give --board <token> or --slug <application> (a Greenhouse posting)")
    names = fetch_greenhouse_schools(board, term)
    if not names:
        console.print(f"[yellow]no school matches {term!r} on board {board!r}[/yellow] — try fewer words")
        return
    for n in names:
        console.print(f"  {n}")


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
    allow_stale_browser: bool = typer.Option(
        False, "--allow-stale-browser",
        help="--fill: open the ATS page even when the browser is behind Chrome stable's security fixes "
        "(the sandbox stays on). Without it a stale browser is refused; installed Google Chrome is "
        "preferred because it updates itself.",
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
        raise HarnessError(
            f"no application folder at {folder}",
            hint=f"Create it with `hn career posting <url> --slug {slug}`.",
        )
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
        raise HarnessError(str(e)) from e
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
            raise UsageError(
                "--fill and --submit are separate acts",
                hint="Fill first, click Submit yourself, then record.",
            )
        _browser_fill(folder, pj, no_wait=no_wait, allow_stale_browser=allow_stale_browser)
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
        raise HarnessError(
            f"already recorded as submitted {prior.submitted_at} via {prior.via} "
            f"({prior.receipt[:60]}) — a re-submission is a deliberate human act; "
            "remove receipt.json first"
        )
    if not confirm:
        console.print("[yellow]not submitted[/yellow] — re-run with --confirm to record exactly this packet")
        return
    form = load_form(folder)
    rec = None
    if via in ("auto", "api"):
        try:
            rec = api_submit(form, packet)
        except SubmitNotAvailable as e:
            if via == "api" or not receipt:
                raise HarnessError(str(e)) from e
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
    console.print(f"[green]recorded[/green] {rec.submitted_at} via {rec.via} → {folder / 'receipt.json'}")
    console.print(f"  applications.yaml: row {action} → stage applied")


def _bus_app_candidates() -> list[Path]:
    """Where `bus-app/` (the fill script + its node_modules) may live, in order: an explicit
    `HARNESS_BUS_APP_DIR`, this checkout, then the main checkout when this is a worktree — the
    shared node-tree resolver (`harness.paths`), which the viz renderer uses too.

    Why the chain: when run from a worktree without node_modules, the fill script and its deps
    are only on the main checkout, so this checkout alone would refuse."""
    from harness.paths import node_tree_candidates

    return node_tree_candidates("bus-app", "HARNESS_BUS_APP_DIR", repo_root=REPO_ROOT)


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


def _browser_fill(
    folder: Path, packet_json: Path, *, no_wait: bool = False, allow_stale_browser: bool = False
) -> None:
    """Spawn the headed browser fill (bus-app/scripts/apply-fill.mjs) for an assembled packet.

    Zero new dependencies by design: Playwright ships in bus-app's devDependencies and its Chromium
    is already installed for the agent's eye. Everything is fail-loud + never auto-installed."""
    import shutil

    bus_app = _resolve_bus_app()
    if bus_app is None:
        looked = " · ".join(str(c) for c in _bus_app_candidates())
        raise HarnessError(
            "Playwright is not installed in any bus-app checkout — looked in: "
            f"{looked}. Run `cd <main checkout>/bus-app && npm ci` (never automatic), or set "
            "HARNESS_BUS_APP_DIR to a bus-app dir that has node_modules."
        )
    fill_script = bus_app / "scripts" / "apply-fill.mjs"
    brew_node = Path("/opt/homebrew/bin/node")
    node = shutil.which("node") or (str(brew_node) if brew_node.exists() else None)
    if node is None:
        raise HarnessError("`node` not found on PATH — install Node (brew install node)")
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
    if allow_stale_browser:
        cmd.append("--allow-stale-browser")
    console.print(
        "[bold]browser fill[/bold] — headed, sandboxed, current browser on the dedicated profile; the script "
        f"stops before Submit. Watch the window. [dim](runs from {bus_app})[/dim]"
    )
    res = _run_node(cmd, bus_app)
    line = next((ln for ln in (res.stdout or "").splitlines() if ln.startswith("RESULT ")), "")
    if not line:
        raise HarnessError(f"the fill script exited {res.returncode} without a result — see its output above")
    result = json.loads(line[len("RESULT ") :])
    if not result.get("ok"):
        raise HarnessError(str(result.get("error")))
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
