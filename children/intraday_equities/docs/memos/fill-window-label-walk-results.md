# Fill-window label: what a next-bar fill can earn

## TL;DR

Relabelling the locked lean pooled LightGBM walk from close[t] -> close[t+h] to
close[t+1] -> close[t+1+h] (the window a decision made after bar t can actually
trade) cuts the 20-fold mean path score from `0.00493` to `0.000151`, a 33x
drop. The remaining rank IC is real but tiny: `+0.009` to `+0.018` at leads 1-5
(fold t 2.5-4.3), zero at leads 6-10. The top yhat decile earns about
0.03-0.06 sigma gross, well under 1 bp against 1.8-7.2 bp round-trip costs.
The 1-minute-row twin is weaker still: IC 0.002-0.005, path score negative
(see "1-minute-row result").

## Execution contract

- Command: `python -m dskit.pipeline walkforward
  configs/run-lean-pooled-h10-fill-label-walk.json --asof 2026-02-28
  --adapter intraday_equities`, run 2026-10-11/12 in worktree
  `claude/fill-label-20261011`. Document hash `f829c90e897513e5...`.
- The document is the P16 lean pooled lattice walk (fold doc `b611e037`,
  baseline walk hash `14aa945c`) with ONE change: `label_entry_offset_bars: 1`
  on every scan node (h01..h10). Same 25-name cohort plus SPY, same inner HPO
  (4 trials, 63-day inner validation), same 20 development folds (first cutoff
  2022-05-06, step 63 days, 730-day train, 5-day embargo, 63-day validation).
- Rows every 5 minutes, scored on the 30-minute lattice; labels in 1-minute
  tape bars; y = (log P[t+1+h]/P[t+1] - beta_t log SPY[t+1+h]/SPY[t+1]) /
  (sigma_t sqrt(h)). sigma_t (390-bar rolling std) and beta_t (3900-bar) stay
  at the decision bar t. A row whose entry bar is more than 2 x period_ms after
  t (a session gap) is refused (NaN label), mirroring `_bar_returns`.
- No data after 2026-02-27 exists on disk; the lockbox (from 2026-03-01) is
  untouched. Development evidence only; nothing here is deployment eligible.

## Implementation evidence

- `intraday_equities/nodes.py`: `label_entry_offset_bars` (int >= 0, default 0)
  joins LABEL_PARAMS; `_LeadLabel._window` shifts entry and exit by the
  offset and refuses off-tape or cross-gap windows. Default 0 leaves every
  shipped document's identity hash and label values unchanged (pinned by
  `test_entry_offset_zero_is_the_adr_0059_label_unchanged`).
- Five new tests in `tests/test_nodes.py`: window placement, sigma/beta at t,
  gap and tape-end refusal, validation of the knob, default identity. Suite:
  28 passed, 1 skipped; ruff clean. Commit `499ae25a`.
- One defect found and fixed before the walk ran: the first gap rule used
  `== offset x period_ms` with period_ms = 300000 (row cadence) against
  1-minute tapes and refused every row; the `<= 2 x period_ms` rule fixed it.

## Empirical results (5-minute rows, 20 folds, 901,784 scored rows)

Path score per fold is in the walk report
(`pipeline_runs/lean-pooled-h10-fill-label-walkforward-2026-02-28-f829c90e/report.md`):
mean `0.000151217`, std `0.000499744`, 12/20 folds positive. The same walk with
offset 0 scored mean `0.00493172`, std `0.00181338`, 20/20 positive.

Pooled Spearman rank IC between yhat and the fill-window label, with the fold
mean and its t statistic (mean / std x sqrt(20)):

| lead | rows | pooled IC | fold mean | t |
|---|---:|---:|---:|---:|
| 1 | 253,894 | +0.0095 | +0.0094 | 2.9 |
| 2 | 161,193 | +0.0179 | +0.0176 | 4.3 |
| 3 | 150,927 | +0.0106 | +0.0115 | 2.5 |
| 4 | 120,190 | +0.0157 | +0.0178 | 3.8 |
| 5 | 120,190 | +0.0142 | +0.0160 | 3.7 |
| 6-10 | 19,078 each | -0.008 to +0.008 | -0.007 to +0.012 | -0.9 to 1.8 |

Leads 6-10 are LRCX and MSTR only (their certified leads). Per name, pooled
over folds, mean IC across leads 1-5: SMH 0.030, IWM 0.028, TER 0.025, LITE
0.022, CIEN 0.021, LRCX 0.015; ADBE, MSTR, NOW, NRG, XLE, XLF, BAC and PANW
are within +-0.006 of zero. Top-decile mean label by lead: h1 +0.030 sigma,
h2 +0.056, h3 +0.029, h4 +0.031, h5 +0.014 (all rows: +0.001 to +0.004).
With a 1-minute sigma of a few bp for these names that is a fraction of a bp
gross, consistent with the 2026-10-11 edge study's +0.1 to +0.5 bp top-decile
fill proxy against 1.8 bp (config) to 7.2 bp (full) round-trip cost.

Conclusion: the ADR-0059 label's IC of about 0.09 is roughly 95 percent the
one bar [t, t+1] that a next-bar fill cannot reach. What survives is a
statistically real but economically negligible signal at leads 1-5.

## Deliberately unrun / in flight

- The 1-minute-row twin `configs/run-lean-pooled-h5-fill-label-1m-walk.json`
  (hash `67c51411dbccefe2...`; leads 1-5; frozen estimator params from the
  retrain simulation's `lean-pooled-h10-minute-walk.json`; no inner HPO; path
  heads clipped to lead 5) reads new full-window 1-minute feature caches at
  `pipeline_cache/p13-features-f32-kronos-v1-full-1m-5862b7f0/{a,c,d,e}`
  (row window 2020-05-01 to 2026-02-28, built 2026-10-12 with
  `modelability_study.cache_build_document`, peak 9.1 GB each). Its first
  launch failed at the path node (inputs for leads 6-10 required by LRCX and
  MSTR's certified lead 10); the clip fixed it and the walk was relaunched at
  01:25 ET on 2026-10-12, log `pipeline_runs/fill-label-1m-walk.log`.
  Fold 0 trains on 4.39 M rows; a fold takes about 5 minutes.
- Not run: any trading simulation on the new label, any cost-aware cell
  search on the 1-minute rows, and the 1-minute offset-0 baseline.

## 1-minute-row result (added 2026-10-12 11:30 ET)

`configs/run-lean-pooled-h5-fill-label-1m-walk.json` (hash `67c51411dbccefe2...`)
ran 20/20 folds with no errors (summary
`pipeline_runs/lean-pooled-h5-fill-label-1m-walkforward-2026-02-28-67c51411/`,
log `pipeline_runs/fill-label-1m-walk.log`). Every 1-minute row is scored
(24,527,741 rows; fold 0 trained on 4.39 M rows), frozen lean estimator
params, no inner HPO. Mean path score `-0.000434337`, std `0.00130555`.

| lead | rows | pooled IC | fold mean | t | folds > 0 |
|---|---:|---:|---:|---:|---:|
| 1 | 7,749,005 | +0.0016 | +0.0019 | 3.8 | 16/20 |
| 2 | 4,901,430 | +0.0015 | +0.0028 | 2.6 | 15/20 |
| 3 | 4,587,216 | +0.0006 | +0.0034 | 3.0 | 15/20 |
| 4 | 3,645,045 | +0.0013 | +0.0046 | 3.1 | 14/20 |
| 5 | 3,645,045 | +0.0013 | +0.0049 | 2.8 | 16/20 |

Per name (mean IC over its leads): IWM +0.0075, MSTR +0.0057, XBI +0.0052;
nine names are negative (LRCX -0.0056, BIDU -0.0043, SMH -0.0034, ADBE
-0.0033, XLK -0.0027, TER -0.0017). Top-decile mean label: h1 +0.007 sigma
to h4 +0.018 sigma, under 0.1 bp gross.

Conclusion: at the 1-minute row the next-bar-fill signal is a tenth of the
5-minute lattice's (IC 0.002-0.005 versus 0.01-0.02), the same sign-mixed
per-name picture, and the path score turns negative. The forecast layer has
no edge a fill can reach at either row cadence. Not run: a 1-minute offset-0
baseline and any cost-aware search on these rows.

## Reproducibility and handoff

Both documents validate (`python -m dskit.pipeline validate <doc> --adapter
intraday_equities`). The caches are gitignored run artefacts; rebuild them
from the four `full-1m-cache-{a,c,d,e}.json` documents (universe override
`period_ms: 60000`, `row_window` as above, `include_klines: false`). The IC
tables above come from the per-fold `artifacts/scan_hXX/predictions.parquet`
files (columns ts, series, fold, horizon, yhat, y, mu). The walk-forward
summary directories listed above are the durable artefacts. Next authorized
action: read the 1-minute walk's report and predictions the same way and
record them here.
