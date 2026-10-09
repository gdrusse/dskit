# Stage A refresh and 15-minute rerun (2026-10-09)

## TL;DR

The Stage A data was refreshed up to the newest files the vendors have published
(Binance through 2026-10-08, Kalshi through 2026-10-09 01:30 UTC), and the
15-minute document was rerun with an unchanged config. The development table is
identical to the 2026-10-08 memo, and held-out was not enabled. The held-out
rows with no usable price data fell from 1,182 to 102 (plus 528 that lack only
the implied-volatility input), and every remaining gap is a day whose vendor file
does not exist yet.

In plain terms: the held-out rows the old run could not price because the data
had not been downloaded are now priced. The development scores did not change,
because the development period did not change.

## Execution contract

- Operation: `python -m dskit.pipeline run configs/run-features-15m.json --asof 2026-10-09`
  (from `children/crypto_trading`), exit code 0, all 16 nodes `ok` (`result.json`).
- Run directory (not tracked):
  `children/crypto_trading/pipeline_runs/crypto-features-15m-2026-10-09-d7e56be5/`.
- Identity: document hash
  `1ade11a68c305b059fecce2117b198e2780c2d31427cf6c00485e8f0f941199b`, the same as
  the 2026-10-08 run, so the config is unchanged; run hash
  `d7e56be5d58fcb2635d547108558f00874b5e992d5f7d4c430831ef22e39d3ce`. An earlier
  intermediate run, `...-2026-10-09-7d7ab8ff` (journal A0051), was taken before the
  2026-10-08 Binance pull and was not published.
- Date: pulls and rerun on 2026-10-09 UTC; the final run is journaled at
  2026-10-09T04:29:53Z (A0074), the table at 04:30:04Z (A0075).
- Worktree: branch `crypto-completion-20261008`, from origin/main `f56a6f3c`.
- Fixed settings (unchanged from the 2026-10-08 memo; `binary_score.json`): store
  root `/home/russell/data/crypto_trading/ob`; leads 2, 5, 10 minutes; held-out cut
  `end_ms` 1789430400000 (2026-09-15 00:00 UTC); take-rule margin 0.02;
  execution lag 5 s; strike-known lag 30 s; standard-error blocks 86,400 s;
  `report_segments` `["development"]`, so held-out is NOT enabled.
- Data cut (`resolved.json`): 13,108 market rows; 98,385 candle rows per asset
  (BTC and ETH); 12 fee-schedule rows; Binance kline and BVOL stream manifests for
  both assets. BVOL is Binance's implied-volatility index.

## Implementation evidence (did it run correctly?)

These show the machinery worked; they say nothing about the models.

- Binance Vision date list (`configs/binance_vision_dates.json`) extended from
  2026-10-05 to 2026-10-08 (new file sha256 `3cb5c067df69...`, old pin
  `9a16650edfe8...`). The four `source-binance-*` configs got the new pin and a
  raised `as_of` of 2026-10-09T02:55:00+00:00. Changing the declaration forces a
  whole-list re-pull of 1,207 days per stream.
- Pull gates (each pull = `acquire --mode backfill` then `validate`):

| stream | gate | records or note | journal |
|---|---|---|---|
| kalshi fee schedules | pass | 6 records | A0035-36 |
| kalshi markets | warn | 1,495,781 records; exactly 10 failing rows on `markets-strike-type-set` (warn severity), the same count as the 2026-10-08 pull; re-pulled 04:10-04:28 UTC | A0037-38, A0068-69 |
| Binance klines BTCUSDT, ETHUSDT | pass | 1,207 records each | A0039-42, redone A0060-63 |
| Binance BVOL BTC, ETH | warn | rule `files-all-ok`: 27 failing files each = the 26 documented vendor-missing days + 2026-10-08 (not published); `max_refused` 31 holds | A0043-46, redone A0064-67 |
| Kalshi 15-minute candles BTC, ETH | pass | incremental (cursor) | A0047-50, A0070-73 |

- `dskit.onboarding verify`: `snapshots_checked` 24, then 25 after publishing,
  `problems: []` both times.
- Final table: 39,324 rows at
  `~/data/crypto_trading/features-15m/decision_features-crypto-features-15m-2026-10-09-d7e56be5.jsonl`,
  sha256 `aa3b301aa5deb42f8a8c99db10100185c822d1a6e2ef655781334eadde9426c5`; latest
  market close 2026-10-09 04:15 UTC. Published as stream
  `decision_features-crypto-features-15m-2026-10-09-d7e56be5` of source
  `features-15m` (acquisition `20261009T043004Z-backfill-b530d550`, A0075). It is a
  new stream name; the old `...-2026-10-08-d2cf6aff` stream is untouched.
- Scoring census (`binary_score.json`): 39,324 rows read; 417 not eligible; 13,637
  in the unreported held-out segment; 25,270 scored per model; zero rows lost to a
  missing forecast, fee or quote in the development segment, for all three models.
- Child tests (`python -m pytest tests -q`, run after the changes): 642 passed,
  9 skipped, 1 failed (the known failure below).
- Resources: 642 GB disk free at start; no pull needed more than about 4 GB of
  memory (17 GB total). Store size is now 11 GB (`du`); the store is write-once,
  so superseded snapshots remain.

## Failures, skips and deliberately unrun work

| item | status |
|---|---|
| First pull attempt (`stageA2.log`) | FAILED at `binance-btcusdt-1m` before any request was made, so nothing was committed. Cause: re-registering a changed declaration creates a second ACTIVE `source_config`, and `acquire` refuses ("2 active source_configs ... retire all but one"). |
| Retire step | The superseded versions were moved active -> retired through the assets registry API (`reg.transition`, origin `crypto-completion-redeclare-20261009`). These retire transitions are NOT journaled in `actions.csv`. |
| Retired-config mix-up | I retired a just-registered BVOL version by assuming registry order. Retirement is one-way, so I bumped `as_of` (03:00, then 03:05, then 02:55; the first two were later than the clock) and registered fresh versions. Register-source rows A0052-A0059 therefore include intermediate versions that were retired unused. Final active versions: `binance-btcusdt-1m` 43936e14..., `binance-ethusdt-1m` 9494d39d..., `binance-btcbvol` e96d7763..., `binance-ethbvol` 06ba65b6.... |
| Vendor gap 1: Binance klines for 2026-10-09 | No file yet (404). Causes the 102 `no_spot` held-out rows, all closing 2026-10-09. |
| Vendor gap 2: Binance BVOL for 2026-10-08 | Not published (404 at 04:30 UTC; the klines file for 10-08 appeared about 02:58 UTC on 10-09). Causes the 528 `no_vol` rows (`fair_bvol` only): 522 closing 2026-10-08, 6 closing 2026-10-09. |
| Failing known test | `tests/test_runbook.py::test_the_sizing_copy_runs_as_pasted_and_is_repeatable_after_a_failed_attempt`: "No module named 'dskit'" inside the test's temporary `bin/python`. Same failure as before this work; not fixed here (item 7 of the plan). |
| 9 skipped tests | Reasons not examined. |
| 26 vendor-missing BVOL days; 10 strike-type rows | Not investigated (carried from the 2026-10-08 memo). Whether the 10 rows are the same markets as the 10 excluded earlier is not proven. |
| `fee_schedules` row count | Reconciled: each pull yields 6 records (one per series); the store now holds two fee pulls (2026-10-08 and 2026-10-09), `scan_stream` keyed on `series_ticker` returns the 6 latest, and the pipeline's fee node reads both versions (12 rows in `resolved.json`) so each decision can use the schedule retrieved before it. |
| Held-out read (B5) | NOT enabled. Held-out rows were built into the table and counted, not scored or reported. |
| Not done by design | No commit, no hourly work, no trades pull, no live order books, Coinbase or Deribit. |
| BVOL 365-day-year assumption | Still unverified. |

## Held-out coverage, old vs new

Held-out = rows with `close_ms` >= 1789430400000. Counts are over all held-out
rows in each table, eligible or not (computed from the two published tables).

| | 2026-10-08 table (`d2cf6aff`) | 2026-10-09 table (`d7e56be5`) |
|---|---|---|
| total rows | 38,724 | 39,324 |
| development rows | 25,584 | 25,584 (unchanged) |
| held-out rows | 13,140 | 13,740 |
| latest close | 2026-10-08 01:15 UTC | 2026-10-09 04:15 UTC |
| `fair_rms` ok / `no_spot` | 11,958 / 1,182 | 13,638 / 102 |
| `fair_ewma` ok / `no_spot` | 11,958 / 1,182 | 13,638 / 102 |
| `fair_bvol` ok / `no_spot` / `no_vol` | 11,958 / 1,182 / 0 | 13,110 / 102 / 528 |
| `no_spot` by close date | 10-06: 570; 10-07: 576; 10-08: 36 | 10-09: 102 |
| scoring census: not eligible / unreported held-out | 410 / 13,044 | 417 / 13,637 |

All 1,182 old `no_spot` rows are now priced. Rows with `no_spot` whose data now
exists: 0. The remaining 102 and 528 rows are the two vendor gaps above. The old
`fair_bvol` split (0 `no_vol`) was checked directly in the old table (held-out: 11,958 ok, 1,182 `no_spot`). Development rows: 25,584 have all
three forecasts, before and after.

## Empirical results (development segment only; unchanged)

The development segment did not change, and `binary_score.md` reproduces the
2026-10-08 memo's headline table. Brier is the average squared miss between a
probability and the outcome (1 if YES, else 0); lower is better. A positive
difference means the model was further from the outcome than the market mid.
n = 25,270 per model; "se" is the standard error clustered on 86,400 s blocks
(45 blocks); "event se" is clustered per event.

| model | Brier model | Brier market | Brier diff (se; event se) | log-loss diff (se; event se) | trades | mean P&L per trade after fee (se; event se) |
|---|---|---|---|---|---|---|
| fair_bvol | 0.1435 | 0.1286 | +0.0149 (0.0024; 0.0007) | +0.0450 (0.0067; 0.0023) | 18,197 | -0.0120 (0.0046; 0.0033) |
| fair_ewma | 0.1317 | 0.1286 | +0.0030 (0.0007; 0.0005) | +0.0137 (0.0028; 0.0020) | 13,159 | -0.0005 (0.0048; 0.0042) |
| fair_rms | 0.1320 | 0.1286 | +0.0034 (0.0007; 0.0005) | +0.0170 (0.0034; 0.0022) | 13,340 | -0.0006 (0.0043; 0.0042) |

Fixed settings behind the table: margin 0.02, execution lag 5 s, strike lag 30 s,
leads 2, 5 and 10 minutes. The interpretation, the by-lead table, the math layers
and the caveats in the 2026-10-08 memo apply unchanged; they are not repeated
here. Held-out scores were not computed or reported.

## Reproducibility and handoff

- Reproduce: the command above against the same store. Expect document hash
  `1ade11a68c30...` (full hash above) and, with the store unchanged, run hash
  `d7e56be5...`. Stream manifest hashes are in `resolved.json`: BTC bvol
  `c694a9ca...`, BTC klines `642caa03...`, ETH bvol `fad633f3...`, ETH klines
  `d16ddbcf...`; markets sha256 `df03fda860b8...`; BTC candles `dbc594048...`;
  ETH candles `d67d7db05...`; fee schedules `1a7e72dc713...`.
- Git state: nothing committed. Modified in the worktree:
  `configs/binance_vision_dates.json`, the four `configs/source-binance-*.json`
  (pin and `as_of`), and `docs/decisioning/{actions.csv,README.md}` (journal rows
  A0036-A0075, the acquires and runs). This memo is new and also uncommitted.
  The pipeline run directory and the data store are ignored artifacts.
- Open items: the two vendor gaps close only when Binance publishes the 2026-10-09
  klines and the 2026-10-08 BVOL files; the failing runbook test; the unjournaled
  retire transitions; the BVOL
  365-day year.
- Held-out status: B5 read not authorized or enabled here. Per runbook B5 it needs
  a recorded edit of `report_segments`, a commit, one run and one read.
- Next authorized action: item 2 (kalshi-history markets).
