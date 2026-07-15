---
tags: [bead, finance, tax]
---

# scale-v2 — Model the withholding shortfall on the 8/15 vest

**Status:** closed · **Priority:** P2 · **Type:** task

> GENERATED from the beads db — a rendered view; edit via `bd`, never here.

## Details

| field | value |
| --- | --- |
| Created | 2026-06-20 · Casey Morgan |
| Updated | 2026-07-12 |
| Closed | 2026-07-12 |
| Labels | finance, tax |
| Owner | casey@example.com |

## Description

RSU vests withhold at the flat supplemental rate, which runs well under the actual marginal rate — the gap compounds quietly across four vests a year and surfaces as an April surprise.

## Linked issues

**Parent** (1)
- [[scale-v1]] — Vest planning — the 8/15 tranche lands prepared, not improvised · open

## Resolution

Modeled: the default 22% supplemental withholding runs ~11 points under the marginal rate — roughly a $1.9k gap on a 45-unit vest at current prices. Plan: cover it with the 9/15 1040-ES Q3 payment rather than touching payroll elections.
