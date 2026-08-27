# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

## This session (2026-08-26): ADR-0037 — the observations read seam

**Branch:** `feat/obs-read-seam-adr-0037` (**NOT merged** — see below) ·
**Tests:** 2425 passed, 108 optional-lib skips · **ruff:** clean.

- **Owner ruling applied:** generic dskit first, children are wrappers.
  The intraday_poc bars-node OOM (14.3 GB on 2M bars) graduated into
  `dskit/onboarding/observations.py`: `scan_stream` (codec-aware,
  bitemporally deduplicating, single-copy memory discipline — 650 vs
  1547 B/row measured) + `stream_digest` (incremental, byte-parity
  with the frozen dump recipe). `BarsFromStore` is now a thin wrapper.
- Both RE-ENTRY action items closed: score kinds accept `"cal"`
  (ADR-0034); the reader is codec-aware (ADR-0036).
- The blocked walk-forward backtest runs end to end on synthetic
  stores (3 folds, plain and gzip byte-parity, 2.6 GB RSS). The
  REAL-data re-run still needs the 2M-bar `ob/` store re-acquired —
  it is not on this machine.

## Why the branch is NOT merged — owner decision needed

A skeptic-review loop ran (2 fresh adversarial reviewers per round,
distinct lenses). **Three consecutive rounds each found real
correctness defects** — round 1 in the original code, rounds 2 and 3
in the previous round's fixes (tie adjudication: running-max vs final
winner; coercing `==` identity; NaN key ordering; heterogeneous-key
crash; lexicographic vs instant `acquired_at`). All are fixed
red-first with the skeptics' own reproductions as tests, and each
round's findings are recorded as amendment blocks inside ADR-0037.
Per the loop's escalation rule the grind stopped there instead of
self-declaring a marginal pass.

**Decision for the owner:** run one more fresh two-skeptic round on
`6889c82` and merge on a clean pass (recommended), or merge as-is.
The four commits: `3e40b10` (seam), `b330e1a` (round-1 fixes),
`1578c56` (round-2 fixes), `6889c82` (round-3 fixes).

**Next after that:** re-acquire the Alpaca store and re-run
`run-backtest.json` on real data; pmquant P0 remains on the owner's
word; §13 gaps 5/6/7/9/11/12 in `TODO.md` (10 is half-landed).
