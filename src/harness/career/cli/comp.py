"""Comp sense from levels.fyi: a company's pay ladder and the leaderboard pool.

The modules that do the work import the HTTP stack, so each function imports the one it needs
rather than this file importing them all: every `hn` command loads this file to build the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.table import Table

from harness.career.cli.common import console
from harness.career.config import get_settings, role_hunt_root
from harness.errors import HarnessError, UsageError


def _ladder_feed(
    root: Path,
    *,
    limit: int | None,
    include_pool: bool,
    dry_run: bool,
    notify: bool,
    commit: bool,
    as_json: bool,
) -> None:
    """`comp --refresh-stale`: plan → (dry-run: print it) → run → log → one bus event → commit."""
    from harness.career import ladder_feed as feed

    plan = feed.plan_feed(root, get_settings().tracker_path, limit=limit, include_pool=include_pool)
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
    committed = feed.commit_run(root, get_settings().tracker_path, res, notify=notify) if commit else None
    if as_json:
        console.print_json(json.dumps({
            **res.model_dump(), "summary": res.summary(), "published": published,
            "commit": committed.note if committed else None,
        }))
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
    if committed:
        console.print(f"[dim]{committed.note}[/dim]")


def _show_cached_ladders(root: Path, *, as_json: bool) -> None:
    """`comp --all`: every ladder already in role-hunt/comp/. No network."""
    from harness.career import comp as compmod

    ladders = compmod.cached_ladders(root)
    if as_json:
        console.print_json(json.dumps(compmod.ladders_contract(root, ladders)))
        return
    for lad in ladders:
        console.print(compmod.ladder_markdown(lad), markup=False)
        console.print()
    console.print(f"[dim]{len(ladders)} cached ladder(s) in {compmod.comp_dir(root)}[/dim]")
    return


def _show_pay_report(root: Path, *, year: int | None, refresh: bool, as_json: bool) -> None:
    """`comp --report`: the year-end pay report and the metro and US percentiles, read against the
    user's anchor."""
    from harness.career import comp as compmod
    from harness.career import report as repmod

    anchor = compmod.anchor_tc()
    r = repmod.build_report(root, year=year, anchor=anchor, refresh=refresh)
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


def _show_lca_wages(
    employer: str, *, title: str | None, state: str | None, limit: int, as_json: bool
) -> None:
    """`comp --lca`: the H-1B wages an employer filed, from the local DOL disclosure workbook."""
    from harness.career import comp as compmod
    from harness.career import lca as lcamod

    wb = lcamod.ensure_workbook()
    slim = lcamod.ingest(wb)
    rows = lcamod.query(slim, employer, title=title, state=state, limit=limit)
    src_line = f"Source: DOL OFLC LCA disclosure data — {wb.name} (local copy)"
    if as_json:
        console.print_json(
            json.dumps({"workbook": wb.name, "employer": employer, "rows": rows, "source": src_line})
        )
        return
    if not rows:
        console.print(
            f"[yellow]no LCA rows for employer {employer!r}[/yellow]"
            + (f" title~{title!r}" if title else "")
            + (f" state={state}" if state else "")
            + f" in {wb.name}"
        )
        return
    t = Table(
        show_header=True,
        header_style="bold",
        pad_edge=False,
        title=f"LCA filed wages — {employer} · {wb.name}",
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


def _show_ladder(
    root: Path, company: str, *, family: str, location: str, refresh: bool, as_json: bool
) -> None:
    """`comp <company>`: one company's pay ladder, from the vault copy when it is fresh enough."""
    from harness.career import comp as compmod

    ladder, from_cache = compmod.get_ladder(
        root, company, family=family, location=location, refresh=refresh
    )
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
    commit: bool = typer.Option(
        False,
        "--commit",
        help="--refresh-stale: commit the run's files (the comp directory only) to the corpus repo and "
        "push if it fast-forwards; a commit that fails or can't be pushed is a bus warning",
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
    if source.lower() == "blind":
        from harness.career.comp import blind_refusal

        raise HarnessError(blind_refusal())
    if source.lower() != "levels":
        raise UsageError(f"unknown --source {source!r}: levels | blind")
    root = role_hunt_root(get_settings())
    if refresh_stale:
        _ladder_feed(
            root, limit=limit, include_pool=pool, dry_run=dry_run, notify=notify, commit=commit,
            as_json=as_json,
        )
        return
    if all_cached:
        _show_cached_ladders(root, as_json=as_json)
    elif report is not None or (company and company.lower() == "report"):
        _show_pay_report(root, year=report, refresh=refresh, as_json=as_json)
    elif lca:
        _show_lca_wages(lca, title=title, state=state, limit=25 if limit is None else limit, as_json=as_json)
    elif company:
        _show_ladder(root, company, family=family, location=location, refresh=refresh, as_json=as_json)
    else:
        raise UsageError("give a company (`hn career comp example-co`) or `--lca <employer>`")


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
        if single:
            saved = poolmod.latest_survey(root)
        else:
            from harness.career.roles import attach_openings

            tracker = get_settings().tracker_path
            saved = attach_openings(tracker, root, poolmod.merge_pool(tracker, root))
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
    survey = poolmod.build_pool(
        get_settings().tracker_path, root, family=family, level=level, location=location, refresh=refresh
    )
    prior = poolmod.prior_survey(root, survey["cut"]["key"], before=survey["as_of"])
    diff = poolmod.diff_surveys(survey, prior)
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
