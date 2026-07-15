---
tags: [bead, finance, tax]
---

# nest-r4 — Confirm no VXUS purchases inside the ±30d wash window

**Status:** open · **Priority:** P1 · **Type:** task

> GENERATED from the beads db — a rendered view; edit via `bd`, never here.

## Details

| field | value |
| --- | --- |
| Created | 2026-07-10 · Jordan Avery |
| Updated | 2026-07-13 |
| Labels | finance, tax |
| Owner | jordan@example.com |

## Description

The dividend-reinvestment flag is the classic trap — an auto-reinvested $40 dividend washes the whole harvest. Audit both accounts' DRIP settings and the last 30 days of fills before nest-r3 sells anything.

## Linked issues

**Parent** (1)
- [[nest-r1]] — 2026 rebalance — drift back to 70/20/10 target weights · open

**Blocks** (1)
- [[nest-r3]] — Harvest the VXUS lot bought at the March high · in_progress
