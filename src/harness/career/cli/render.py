"""Résumé and application renders: the master CV through Typst (PDF) or a design-matched docx, and
an application folder's documents through pandoc, each with deterministic checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from harness._cli import report
from harness.career.cli.common import console
from harness.career.config import get_settings
from harness.errors import HarnessError, UsageError
from harness.settings import overlay_get


def _resume_output_stem() -> str:
    """Render-output name stem — overlay-driven (career.global_settings.resume.output_stem),
    generic "resume" default: the engine never hardcodes a person's filename (generalize-first)."""
    return str(overlay_get("career", "resume", "output_stem", default="resume"))


def _resume_source_md() -> str:
    """The master-CV markdown SoT filename under docs/career/ — overlay-driven
    (career.global_settings.resume.source_md), generic "resume.md" default."""
    return str(overlay_get("career", "resume", "source_md", default="resume.md"))


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
        raise UsageError(
            "give a slug (per-application mode) or --design <name> (master-CV mode). "
            "`--design list` enumerates the design library."
        )
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
        raise HarnessError(f"no designs in {_typst_root() / 'active'} — is the Typst workspace present?")
    console.print("[bold]active design library[/bold] (docs/career/typst/active/):")
    for d in designs:
        star = " ⭐ default" if d == "mono-accent" else ""
        console.print(f"  • {d}{star}")


def _render_master_pdf(design: str, out_arg: str | None) -> None:
    """Master-CV Typst render: active/<design>.typ → PDF, --root the typst workspace."""
    import shutil

    designs = _active_designs()
    if not designs:
        raise HarnessError(f"no designs in {_typst_root() / 'active'} — is the Typst workspace present?")

    if shutil.which("typst") is None and not Path("/opt/homebrew/bin/typst").exists():
        raise HarnessError(
            "typst not installed — `brew install typst` (the résumé render toolchain). "
            "Install is never automatic — run it yourself."
        )

    design_file = _resolve_design(design)
    if design_file is None:
        raise HarnessError(
            f"no unique design {design!r} in active/ (known: {', '.join(designs)})",
            hint="`--design list` shows every design.",
        )

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
        raise HarnessError(f"typst compile failed for {design_file.name}: {res.stderr.strip()[:400]}")

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

    if shutil.which("pandoc") is None and not Path("/opt/homebrew/bin/pandoc").exists():
        raise HarnessError(
            "pandoc not installed — `brew install pandoc` (the résumé render toolchain). "
            "Install is never automatic — run it yourself."
        )

    career = get_settings().tracker_path / "docs" / "career"
    src = career / _resume_source_md()
    ref = career / "docx-template" / f"reference-{design}.docx"
    if not src.exists():
        raise HarnessError(f"master CV markdown not found at {src}")
    if not ref.exists():
        raise HarnessError(
            f"no docx reference-doc for design {design!r} at {ref} — regenerate the set: "
            f"python3 {ref.parent / 'build_reference.py'} (themes track ../typst/active/)"
        )

    out = (
        Path(out_arg).expanduser()
        if out_arg
        else career / "renders" / "docx" / design / f"{_resume_output_stem()}.docx"
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["pandoc", str(src), f"--reference-doc={ref}", "-o", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, env=_render_env())
    if res.returncode != 0:
        raise HarnessError(f"pandoc docx render failed: {res.stderr.strip()[:400]}")

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

    if shutil.which("pandoc") is None:
        raise HarnessError(
            "pandoc not installed — `brew install pandoc basictex` (the documented resume "
            "toolchain). Install is never automatic — run it yourself."
        )

    base = get_settings().tracker_path / "role-hunt" / "applications" / slug
    if not base.is_dir():
        raise HarnessError(f"no application folder at {base} — create role-hunt/applications/{slug}/ first")

    docs = ["resume", "cover-letter"] if doc == "both" else [doc]
    budgets = {"resume": 2, "cover-letter": 1}
    failures = 0
    for d in docs:
        src = base / f"{d}.md"
        if not src.exists():
            report(f"{src.name} not found in {base}")
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
            report(f"pandoc failed for {src.name}: {res.stderr.strip()[:400]}")
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
            report(
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

    res = subprocess.run(
        ["mdls", "-name", "kMDItemNumberOfPages", "-raw", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    out = res.stdout.strip()
    return int(out) if out.isdigit() else None
