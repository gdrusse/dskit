# Real-data backtest, zoo and live chain recorder (ADR-0182) closeout

## TL;DR

The index-options child now runs on real data end to end: Cboe SPX/VIX
history -> six scale rungs -> twCRPS zoo -> condor backtest priced by a
smile-calibrated VIX proxy. Every conditional rung beats the naive rung, but
none beats VIX alone, and no rung's strikes beat VIX-implied strikes. A wide
live recorder (20 underlyings, all expiries, 3x per trading day) is
collecting real chains so the proxy can be recalibrated and then replaced.
Findings: `docs/research/real-data-backtest/2026-09-24-zoo-vs-vix.md`.

## Delivered (tier placement per ADR-0182)

- Tier-2 dskit pack `dskit/onboarding/libs/cboe.py`: `index_daily` (Cboe
  history CSVs) and `option_chain` (delayed chain JSON, OCC parsing, ET->UTC,
  `roots`/`max_dte`/`equity_symbols` knobs).
- Tier-1 dskit: `pipeline/option_pricing.py` (`black76`,
  `VolIndexSmileQuotes`), `stats.max_drawdown` / `lower_tail_mean`,
  `kinds_flow.KeyBy` (`keyby`: rows -> Join side table).
- Child wrappers: `IndexCloseRows`, `CondorBacktest`; configs
  `run-real-{distribution,har,lightgbm,vix,har-vix,lightgbm-vix}.json`,
  `run-real-zoo.json`, four Cboe source configs.

## Commands (from `children/index_options`, PYTHONPATH = repo root)

```bash
python -m dskit.pipeline walkforward configs/run-real-har-vix.json --asof <today>
python -m dskit.pipeline staged configs/run-real-zoo.json --asof <today>
```

The zoo's first call is plan-only; paste the printed inventory sha256 and
`approved_by` into the approval node for the run (keep the committed file at
PENDING). Deleting `pipeline_runs/` does NOT reset a staged run: its journal
lives in `docs/decisioning/actions.csv`, so change the document (e.g. the
approval note) to get a fresh identity.

## Data and recorder

- Root `~/data/index_options/ob` (outside Git). Sources: `cboe-index`
  (SPX, VIX), `cboe-index-wide` (27 indices incl. VIX1D/9D/3M/6M/1Y, VVIX,
  SKEW, VXN, RVX, OEX, DJX, RUT), `cboe-chain` (2 early SPXW/XSP
  snapshots, retired from recording), `cboe-chain-wide` (SPX, XSP, NDX, XND,
  RUT, MRUT, DJX, OEX, XEO, VIX, SPY, QQQ, IWM, DIA, TLT, GLD, HYG, EEM,
  XLF, SMH; ~152k contracts, ~175 MB, ~45 s per snapshot).
- Windows task `dskit-index-options-chain-recorder` runs
  `~/data/index_options/record_chain.sh` weekdays 10:30, 15:50, 16:20 ET
  (only while Russell is logged on); log `~/data/index_options/recorder.log`
  (records count + free disk per run). ~130 GB/year.
- NDX/XND index history is not served by Cboe.

## Review and verification

Sonnet lenses: pack (0 Critical; provider zeros + one-cursor caveat
documented), child (0 Critical/Major; outward rounding fixed), smile delta
(Major IV ceiling -> `iv_ceiling`; Minor negativity -> refused in
`problems()`), tier refactor (0 Critical/Major; numbers identical before and
after). Scoped tests: dskit touched modules 2207 passed; child 349 passed
plus 6 `test_integration.py` CLI failures that reproduce on `3204d2b`
(subprocess resolves the stale `~/dskit` install). No full suite.

## Limits and next bounded action

One-day smile calibration; proxy spreads; pre-2016 expiry cadence. Next:
(1) weekly recalibration of the smile from the recorder and SKEW-driven
time-varying skew; (2) rungs with VIX term structure / VVIX / SKEW features
(the only plausible way to beat VIX); (3) after ~6 weeks, rerun the backtest
on recorded quotes for expiries that have settled.
