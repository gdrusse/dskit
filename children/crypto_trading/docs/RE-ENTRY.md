# RE-ENTRY — crypto_trading

Updated: 2026-10-10 by Claude Fable 5.1. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/crypto_trading.md` (dskit repo).

## Now

- The child and nine additive dskit packs are on main (ADR-0239..0247; 0241
  withdrawn): `kalshi_history`, `restwindow`, `zipcsv`, `parquet_series`,
  `kinds_run_write`, `vol_estimators`, `binary_pricing`, `binary_scoring`,
  `fee_mechanics`. Child suite 643 passed, 9 skipped.
- The 15-minute kill test ran on the development segment on 2026-10-08 (run
  `crypto-features-15m-2026-10-08-d2cf6aff`, 25,270 scored rows per model): none
  of the three fair-value models beats the Kalshi mid (Brier and log-loss worse
  by 4.5-6.1 SE); buy-at-the-touch loses after fees (-1.20 cents/trade for the
  implied-vol model, about zero for the other two). Development read only;
  held-out was deliberately not enabled; profit figures are an upper bound.

## Next

Follow `docs/plans/2026-10-06-wsl-data-pull-runbook.md` in order: the remaining
stage-A acquisitions, then the hourly document (B6), with the 52 GB 15-minute
trades pull last. Reading the held-out segment (B5) is the owner's call.

## Blocked / owner decisions

- Ratify leads [2, 5, 10], the held-out cut 2026-09-15, margin 0.02, exec lag
  5 s and strike lag 30 s; set the store path `$OB`.
- Whether to read held-out after the development result above.
- Optional ADRs: a date bound for `kalshi_history` pulls, an open-to-close
  duration filter for the hourly document, a memory-budget refusal for hourly
  runs (B6 costs about 9.7 KB per decision row).
- Open minors per ADR are in the dskit `TODO.md` ("crypto_trading ADR-0239..0247").

## Read first

- `docs/plans/2026-10-06-wsl-data-pull-runbook.md`
- `docs/memos/2026-10-08-15m-kill-test-development-run.md`
- `README.md`

## Do not

- Never read the held-out segment without the owner's explicit decision.
- Binance Vision data is under a research licence only.
