---
tags: [bead, finance, tax]
---

# scale-v4 — Map the wash-sale windows around the vest calendar

**Status:** closed · **Priority:** P2 · **Type:** task

> GENERATED from the beads db — a rendered view; edit via `bd`, never here.

## Details

| field | value |
| --- | --- |
| Created | 2026-06-28 · Casey Morgan |
| Updated | 2026-07-13 |
| Closed | 2026-07-13 |
| Labels | finance, tax |
| Owner | casey@example.com |

## Description

A vest is an acquisition — each one poisons a loss sale of the same stock for 30 days on either side. With quarterly vests, the safe harvest windows are narrower than they look; map them once so every loss-lot decision reads off the same calendar.

## Linked issues

**Parent** (1)
- [[scale-v1]] — Vest planning — the 8/15 tranche lands prepared, not improvised · open

**Blocks** (1)
- [[scale-u6]] — Harvest the 2026-02 loss lot before the vest poisons the window · open

## Resolution

Mapped: the 8/15 vest poisons any CRWD loss sale from 7/16 through 9/14; the 11/15 vest repeats the pattern 10/16 through 12/15. Loss harvests must clear by 7/15 or wait for the mid-September gap. Map posted to the unwind dashboard.

## Comments (1)

**quant** · 2026-07-13 11:00

> Cross-checked against the vest_calendar in portfolio.yaml — all three upcoming vests inherit the same ±30-day poison window. scale-u6 is the only task racing the 7/15 cutoff.
