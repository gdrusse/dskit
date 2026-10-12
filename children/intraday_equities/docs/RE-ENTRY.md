# RE-ENTRY — intraday_equities

Updated: 2026-10-12 by Claude Fable 5.1. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/intraday_equities.md` (dskit repo).

## Now

- 2026-10-11/12 edge study: the development simulation's P&L is noise and the
  locked label's IC (~0.09) is ~95% the bar [t, t+1], which a decision made
  after bar t closes cannot trade (bid-ask bounce of the last print).
- Landed: `label_entry_offset_bars` (ADR-0059 family, default 0 keeps every
  shipped identity) and `configs/run-lean-pooled-h10-fill-label-walk.json`.
  Its 20-fold result: mean path score 0.000151 vs 0.00493 at offset 0; IC
  +0.009..+0.018 at leads 1-5 (t 2.5-4.3), zero at 6-10; top decile gross
  under 1 bp vs 1.8-7.2 bp cost. Memo:
  `docs/memos/fill-window-label-walk-results.md`.
- In flight at wrap: the 1-minute-row twin
  `configs/run-lean-pooled-h5-fill-label-1m-walk.json` (hash 67c51411…),
  relaunched 2026-10-12 01:25 ET in worktree `/home/russell/wt/fill-label-20261011`,
  log `pipeline_runs/fill-label-1m-walk.log`, ~5 min per fold, 20 folds. Its
  full-window 1-minute caches live under
  `pipeline_cache/p13-features-f32-kronos-v1-full-1m-5862b7f0/` (gitignored).
- The joint-MIO state of 2026-09-29 (speed-ups landed, folds 2-19 run waiting
  on rulings F1-F5) is unchanged; see the archive.

## Next

Read the 1-minute walk's report and `artifacts/scan_hXX/predictions.parquet`
(Spearman IC per lead and name, fold t), append to the memo, and bring the
owner a go/no-go on the forecast layer: the signal that survives a next-bar
fill is an order of magnitude below cost on every name at every lead.

## Blocked / owner decisions

- Whether to keep investing in the forecast layer (population change, graph
  model, pretrained TSFM) given the fill-window result, or stop.
- Rulings on F1-F5 before any policy change; EQ = 0.25 stays a proxy.
- WSL memory is capped at 18 GB by `.wslconfig` on a 32 GB host; a 1-minute
  cache build peaks at 9.1 GB.

## Read first

- `docs/memos/fill-window-label-walk-results.md`
- `docs/decisioning/framework.md` (the training lock)
- `docs/memos/` (newest first)

## Do not

- Do not read a label IC at offset 0 as tradeable edge; measure at offset 1.
- Any simulation shortcut against the plan is raised with the owner explicitly,
  never only disclosed in an ADR (owner ruling 2026-09-26).
- Do not delete the worktree while the 1-minute walk runs in it.
