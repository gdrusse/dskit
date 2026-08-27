# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

## This session (2026-08-26): ADR-0037 — the observations read seam

**Branch:** `feat/obs-read-seam-adr-0037`, **merged to `main`** after a
clean adversarial pass · **Tests:** 2440 passed, 108 optional-lib
skips · **ruff:** clean.

- **Generic-first (owner ruling):** the intraday_poc bars-node OOM
  (14.3 GB on 2M bars) graduated into
  `dskit/onboarding/observations.py`: `scan_stream` (codec-aware,
  bitemporal instant-adjudicated dedup, canonical key identity,
  segment-safe params, single-copy memory — 650 vs 1547 B/row) +
  `stream_digest` (incremental, byte-parity with the frozen dump
  recipe). `BarsFromStore` is a thin wrapper; score kinds accept
  `"cal"`; both prior RE-ENTRY action items closed.
- **The skeptic loop ran to a clean pass:** ten rounds, two fresh
  adversarial reviewers per round (20 reviews). Rounds 1–9 surfaced
  ~18 real defects — every one fixed red-first with the skeptics'
  reproductions pinned as tests, every round recorded as an amendment
  block inside ADR-0037. Round 10: both lenses zero
  blocker/major/correctness. Deferred nits are declared in the ADR
  (tenth block).
- The walk-forward backtest runs end to end on synthetic stores
  (3/3 folds, plain and gzip byte-parity; the first cal-band document
  also ran, composing ADR-0034/0036/0037).

## Open

- **Real-data backtest re-run:** the 2M-bar Alpaca `ob/` store is not
  on this machine — re-acquire (needs the child's `.env` keys) or run
  where it lives, then `walkforward run-backtest.json`.
- pmquant ratification/P0 stays on the owner's word; §13 gaps
  5/6/7/9/11/12 in `TODO.md` (10 is half-landed: the function seam
  exists, the reader KIND awaits a second child).
