---
tags: [bead, finance, withdrawal]
---

# glide-w2 — Recompute trailing-12-month spend from the account exports

**Status:** closed · **Priority:** P2 · **Type:** task

> GENERATED from the beads db — a rendered view; edit via `bd`, never here.

## Details

| field | value |
| --- | --- |
| Created | 2026-06-08 · Marion Reyes |
| Updated | 2026-07-11 |
| Closed | 2026-07-11 |
| Labels | finance, withdrawal |
| Owner | marion@example.com |

## Description

Pull the card and checking exports, categorize, and post the trailing-12 number. The spend figure is the denominator of the whole review — nothing downstream moves until it lands.

## Linked issues

**Parent** (1)
- [[glide-w1]] — Annual safe-withdrawal-rate review — re-underwrite the 3.6% plan · open

**Blocks** (1)
- [[glide-w3]] — Model the withdrawal rate against the cash-runway refill rule · in_progress

## Resolution

Trailing-12-month spend posted at $86,400 — roughly 3.5% of the June 30 portfolio value, inside the 3.6% line with a little margin. Category detail filed with the review note.

## Comments (1)

**steward** · 2026-07-10 11:00

> Caught one anomaly before posting: the cruise deposit appeared in the travel category twice (card and checking sides of the same payment). Deduplicated; the corrected total is what shipped.
