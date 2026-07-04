"""Typer sub-app for the `career` noun-group — the role-hunt lane.

Read-rich / execute-gated by doctrine: everything here observes (scan boards, surface comp,
render artifacts); applying/uploading a CV is the user's act, never the tool's. Verbs: `viz` +
`openings` (keyless Greenhouse/Ashby board scan against role-hunt/watchlist.yml).
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from harness.career.config import role_hunt_root
from harness.career.service import CareerService
from harness.errors import ProviderError
from harness.packs import PackGroup
from harness.settings import BaseToolkitSettings

# the shared D3 render engine (harness.viz) — every lane imports it here
from harness.viz import KNOWN_TYPES, VizError, render_diagram

app = typer.Typer(
    cls=PackGroup,  # every verb accepts a trailing `--pack <dir>` (hn career shortlist --pack …)
    add_completion=False,
    help="Career / role-hunt lane (the agentic role-hunter). "
    "Read-only surface: openings scan (Greenhouse/Ashby boards) + D3 renders into role-hunt/.",
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


def _fail(msg: str) -> None:
    console.print(f"[red]error:[/red] {msg}")


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
    as_json: bool = typer.Option(
        False, "--json",
        help="Machine output (per-company scans incl. matches/errors/skips) — the surfaces/"
        "watchman contract",
    ),
) -> None:
    """Scan the watchlist's public ATS boards (keyless, read-only) for matching openings.

    Matching: ANY watchlist title-keyword AND ANY seniority-keyword (see role-hunt/watchlist.yml);
    salary shown as posted (pay-transparency states), unnormalized. Boards that error are surfaced
    loudly — an error is never rendered as "0 openings."
    """
    svc = CareerService(
        get_settings().tracker_path, role_hunt_root=role_hunt_root(get_settings())
    )
    try:
        scans = svc.scan(companies=company or None, grep=grep, unfiltered=unfiltered)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        import json as _json

        console.print_json(_json.dumps([s.model_dump() for s in scans]))
        return
    for s in scans:
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
            t.add_row(o.title, loc, o.salary or "—", o.updated or "—")
        console.print(t)
    hits = sum(len(s.matched) for s in scans)
    console.print(f"\n[bold]{hits}[/bold] matched openings total. URLs in the report (--write).")
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
                    "matrix", data,
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
                    + "*Who's hiring at the altitude, in what shape — counts = matched openings. "
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
    svc = CareerService(
        get_settings().tracker_path, role_hunt_root=role_hunt_root(get_settings())
    )
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
        console.print("[yellow]no shortlist yet — run `hn career openings --write` to persist a "
                      "scan twin the shortlist reads.[/yellow]")
        return
    console.print(f"[bold]{s['total']}[/bold] matched roles · [bold]{s['leadership']}[/bold] "
                  f"leadership · [bold]{s['ic_infra']}[/bold] IC/infra · {s['boards_with_hits']}/"
                  f"{s['scanned_boards']} boards · as of {s['as_of']}")
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
        pipe = load_applications(
            get_settings().tracker_path, root=role_hunt_root(get_settings())
        )
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
    for c in ("Company", "Role", "Stage", "Next step", "Updated"):
        t.add_column(c, max_width=36 if c in ("Role", "Next step") else None)
    for a in pipe.applications:
        t.add_row(a.company, a.role, a.stage, a.next_step or "—", a.updated or "—")
    console.print(t)


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
    svc = CareerService(
        get_settings().tracker_path, role_hunt_root=role_hunt_root(get_settings())
    )
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
    console.print(f"{verb} [bold]{len(profiles)}[/bold] company profiles → role-hunt/companies/ "
                  f"({', '.join(d['slug'] for d in profiles[:6])}{'…' if len(profiles) > 6 else ''})")
    if not write:
        console.print("[dim]dry-run — pass --write to generate. Hand-edited notes are preserved.[/dim]")


@app.command()
def render(
    slug: str = typer.Argument(
        None, help="Application slug under role-hunt/applications/{slug}/ (per-application "
        "pandoc mode). Omit when using --design (master-CV Typst mode)."
    ),
    doc: str = typer.Option("resume", "--doc", help="resume | cover-letter | both (per-app mode)"),
    design: str = typer.Option(
        None, "--design",
        help="Master-CV mode: render docs/career/typst/active/<design>.typ via Typst → PDF. "
        "Use 'list' to enumerate the active design library.",
    ),
    docx: bool = typer.Option(
        False, "--docx",
        help="Master-CV mode: also (or only) emit the ATS-safe Word .docx from the markdown SoT "
        "via pandoc + the named-style reference-doc (design-independent). Pair with --design for "
        "PDF+docx; use alone for docx only.",
    ),
    out: str = typer.Option(
        None, "--out",
        help="Master-CV output path (PDF default docs/career/resume_<design>.pdf; "
        "applies to the .docx when --docx is the sole target)",
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
        _fail("give a slug (per-application mode) or --design <name> (master-CV mode). "
              "`--design list` enumerates the design library.")
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
        _fail("typst not installed — `brew install typst` (the résumé render toolchain). "
              "Install is never automatic — run it yourself.")
        raise typer.Exit(code=1)

    design_file = _resolve_design(design)
    if design_file is None:
        _fail(f"no unique design {design!r} in active/ — known: {', '.join(designs)} "
              "(or `--design list`)")
        raise typer.Exit(code=1)

    root = _typst_root()
    if out_arg:
        out = Path(out_arg).expanduser()
    else:
        out = (get_settings().tracker_path / "docs" / "career" / "renders" / "pdf"
               / f"resume_{design}.pdf")
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
        console.print(f"[yellow]{out.name}: rendered; page count unavailable (mdls indexing lag) — "
                      f"confirm in the eyeball step[/yellow]")
    elif pages > 2:
        console.print(f"[yellow]{out.name}: {pages} pages (master CV — full content library; "
                      f"tailored application renders cut to ≤2)[/yellow]")
    else:
        console.print(f"[green]{out.name}: {pages} page(s) ✓[/green]")
    console.print(f"[dim]  → {out}[/dim]")
    console.print(f"[dim]  self-verify: pdftoppm -png -f 1 -l 1 -r 120 '{out}' /tmp/cv && "
                  f"Read /tmp/cv-1.png  (orphaned headers, spacing, overflow)[/dim]")


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
        _fail("pandoc not installed — `brew install pandoc` (the résumé render toolchain). "
              "Install is never automatic — run it yourself.")
        raise typer.Exit(code=1)

    career = get_settings().tracker_path / "docs" / "career"
    src = career / "resume_v2.md"
    ref = career / "docx-template" / f"reference-{design}.docx"
    if not src.exists():
        _fail(f"master CV markdown not found at {src}")
        raise typer.Exit(code=1)
    if not ref.exists():
        _fail(f"no docx reference-doc for design {design!r} at {ref} — regenerate the set: "
              f"python3 {ref.parent / 'build_reference.py'} (themes track ../typst/active/)")
        raise typer.Exit(code=1)

    out = (Path(out_arg).expanduser() if out_arg
           else career / "renders" / "docx" / f"resume_{design}.docx")
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["pandoc", str(src), f"--reference-doc={ref}", "-o", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, env=_render_env())
    if res.returncode != 0:
        _fail(f"pandoc docx render failed: {res.stderr.strip()[:400]}")
        raise typer.Exit(code=1)

    console.print(f"[green]{out.name}: ATS-safe Word .docx ✓[/green] [dim](design-matched via "
                  f"{ref.name})[/dim]")
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
        _fail("pandoc not installed — `brew install pandoc basictex` (the documented resume "
              "toolchain). Install is never automatic — run it yourself.")
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
            "pandoc", str(src), "-o", str(out), "--pdf-engine=xelatex",
            "-V", "geometry:margin=0.75in", "-V", "mainfont=Helvetica Neue", "-V", "fontsize=11pt",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, env=_render_env())
        if res.returncode != 0:
            _fail(f"pandoc failed for {src.name}: {res.stderr.strip()[:400]}")
            failures += 1
            continue
        pages = _pdf_page_count(out)
        budget = budgets.get(d, 2)
        if pages is None:
            console.print(f"[yellow]{out.name}: rendered; page count unavailable (mdls) — "
                          f"verify in the eyeball step[/yellow]")
        elif pages > budget:
            _fail(f"{out.name}: {pages} pages — OVER the {budget}-page budget. Cut content "
                  f"(relevance-weighted), never compress layout.")
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
        capture_output=True, text=True, timeout=10,
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
