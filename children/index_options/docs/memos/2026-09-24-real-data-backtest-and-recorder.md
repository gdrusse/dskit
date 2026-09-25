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

## Addendum 2026-09-25: recorder outage and chain data quality

**What the store actually holds** (`~/data/index_options/ob`, audited
2026-09-25). Index history is complete: SPX 1975 and VIX 1990 through
2026-09-24, plus 27 wide series. Chains are thinner than "collecting"
above implied. Only **three distinct chain states** exist:

| `cboe-chain-wide` snapshot | Served state |
|---|---|
| 20260924T020407Z, T020505Z, T023753Z, T143037Z | One Cboe state, stamped 2026-09-23 overnight (last trades 2026-09-22), served unchanged by Cboe's CDN through 2026-09-24 10:30 ET. Rows are identical, so dedup keeps one. |
| 20260925T191624Z (`f7fae4ff`) | Fresh, **except XND** (see below). |
| 20260925T192402Z (`28df744e`) | Fresh and correct. |

The two `cboe-chain` (SPXW/XSP) snapshots hold the same 2026-09-23 state.

**Outage.** Cboe switched the chain `timestamp` from New York to UTC
without notice between 2026-09-24 10:30 and 15:50 ET. The pack read it
as New York, which put `quote_time` about 4 h after `acquired_at`, and
the validator refused every pull from 15:50 ET 2026-09-24 through 10:30
ET 2026-09-25. Three scheduled snapshots were lost. Fixed on main
(`ef46c75`, `8d1d802`, `36722e0`): the zone is decided per stamp by the
fetch window, a stale chain takes the zone the same pull decided, and a
pull that no chain decides is refused. So a fully stale CDN state like
the one on 2026-09-24 is now refused rather than stored again.

**Known-bad rows (no retraction mechanism exists).** Snapshot
`cboe-chain-wide-20260925T191624Z-live-f7fae4ff` holds 4,348 XND rows (its only mis-zoned underlying) stamped
`quote_time` 2026-09-25T07:00:47Z. The true instant is 03:00:47Z (UTC
stamp, 16 h stale). They were written by the interim fix `ef46c75`, which
kept the New York reading of stale stamps. The same XND state is stored
correctly in `28df744e`. The mislabel is 4 h *late*, so there is no
look-ahead. Readers of chain history must drop
`underlying == "XND" and quote_time == "2026-09-25T07:00:47+00:00"`.
The zone of the 2026-09-23 state (overnight stamps, fetched 22 h or
more later) cannot be decided from the stamps; treat its instants as
+/- 4 h.
Retracting rows from a content-hashed snapshot is a generic onboarding
gap and needs an upstream proposal, not local plumbing.

**Recorder log.** The log's grep kept only the `error:` line and hid the
message on the next line. `record_chain.sh` (outside Git) now keeps the
three message lines after any error.
