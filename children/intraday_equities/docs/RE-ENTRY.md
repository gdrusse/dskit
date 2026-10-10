# RE-ENTRY — intraday_equities

Updated: 2026-10-10 by Claude Fable 5.1. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/intraday_equities.md` (dskit repo).

## Now

- Last recorded state is 2026-09-29. The joint-MIO speed-ups landed on main
  (`cbe6152`): 604 -> 332 ms per simulated minute on the synthetic 12-name/S=128
  replay; fixes 1-3 are bit-exact and the tangent windows keep the same optimum.
- The full retrain backtest (`run-retrain-simulation.json`) was stopped by the
  owner on 2026-09-26: it solved one MIO per lead group, and the ruling is one
  joint solve per minute across all admitted names and horizons. Gates admitted
  12 of 25 units. Fold 2 fees (-$0.098/fill) roughly equal the modelled fees,
  so the gross edge is likely about zero.
- Audit findings F1-F5 (noisy-head tranche split, near risk-neutral gamma,
  SPY-residual scenarios, unconditional robust CI, MIP gap) are recorded and
  NOT acted on: the owner limited that work to speed.

## Next

In WSL, from a fresh worktree of main: confirm Fold 2's fee sum against its
-$2,055, then run `configs/run-joint-simulation.json` folds 2-19 through
`tools/run_joint_simulation.sh` (tracked, not detached) and report with the
in-document evaluator.

## Blocked / owner decisions

- Rulings on F1-F5 before any policy change.
- EQ = 0.25 is a proxy until Schwab publishes its Rule 605 figure.

## Read first

- `docs/decisioning/framework.md` (the training lock)
- `docs/plans/2026-09-08-final-model-replay-and-monitoring.md`
- `docs/memos/` (newest first)

## Do not

- Any simulation shortcut against the plan is raised with the owner explicitly,
  never only disclosed in an ADR (owner ruling 2026-09-26).
