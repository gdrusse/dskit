# RE-ENTRY archive — intraday_equities

Previous re-entry notes for this lane, newest first. `/wrap` moves the note it
replaces to the top of this file; nothing is ever edited or removed here. Not
read at session start. Older history: `2026-10-10-consolidated-history.md`.

---

## Joint-MIO speed-ups landed; folds 2-19 run waits on owner rulings (note of 2026-10-10)

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

---

## Joint-MIO speed-ups landed; full folds 2-19 run is next (2026-09-29)

- **Landed on main** (`cbe6152`, remote containment verified), on top of the
  merged Codex robust/persistent joint work: `13e340c` SolveRecord slacks off
  HiGHS arrays; `a3ce8ae` compiled bit-exact replacement for appsi
  `update_params`; `ce07c6c` exact per-scenario tangent windows (live tangent
  rows 4096 -> ~818); `d3e67ce` ledger snapshot payload rendered once (same
  bytes). No config, policy or identity-hash change.
- **Measured** (synthetic 12-name/S=128 replay, one job at a time): 604 -> 332
  ms/minute (0.55x); decide 551 -> 291 ms (HiGHS 286 -> 173, SolveRecord 101 ->
  2, update 93 -> 44); checkpoint at history cap 142 -> 65 ms. Projected Fold 2
  ~1h40m vs 3h00m (projection; real instances not timed here).
- **Equivalence:** fixes 1-3 exact (bit/byte-identical). Windows keep the same
  optimum (32/32 equal within 1e-9 at tight tolerance; robust lower edge holds
  at optima only, by dominance). Default HiGHS abs gap 1e-6 already made answers
  tolerance-driven, so same-state minutes differ in ~2% by 1-4 shares (each
  within ~$0.03 expected utility; no bias, no cascade). A rerun of Fold 2 will
  not reproduce $10,444.51 to the cent.
- **Review:** owner-directed Sonnet author + ONE Haiku lens per fix (C0/M0/m0/n0
  each), not the usual two-lens close; orchestrator re-derived the window
  bounds by hand. Tests: pyomo/production focused 735 passed/8 skipped; child
  nodes_capital+replay 584; simulation Joint/Solve 35; ruff clean. 25
  pre-existing `cryptography` `_cffi_backend` failures in the cloud container
  only (tests/production ADR-0147/captured-authorization files).
- **Audit findings NOT acted on (owner: speed only, no policy change):** code
  implements its math correctly, but (F1, critical) the free tranche split
  values each name at the best of up to ~10 noisy heads -> buys on noise
  (P(buy|zero edge) 0.73 at K=10 vs 0 at K=1, ~4.8x turnover); (F2) gamma 2 is
  near risk-neutral at minute scale, CVaR $500 never binds -> 100% cash in 1-4
  names; (F3) scenarios are SPY-residuals, no market factor; (F4) robust CI
  measures an unconditional bias, Gamma=1 spreads into more names; (F5)
  mip_abs_gap 1e-6 ~ 0.02-0.16% of objective. Fold 2 -$0.098/fill ~ modelled
  fees $0.096/fill -> likely gross ~0. Owner rulings needed before any change.
- **Not built:** direct highspy backend (~60-80 ms/min more; needs ADR); lean
  form (no measured gain); per-tick checkpoint cadence (production semantics).
- **Next:** in WSL, fresh worktree of main, confirm Fold 2's fee sum vs its
  -$2,055, then run `configs/run-joint-simulation.json` folds 2-19 via
  `tools/run_joint_simulation.sh` (tracked, not detached) and report with the
  in-document evaluator.

