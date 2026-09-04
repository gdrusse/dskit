# Re-entry

Current wrap: 2026-09-04. `main` is synchronized with `origin/main` at
`fde01b5`. P10, its memo, and the skeleton memo guidance are committed and
pushed. Last verification: 478 passed, 11 skipped; Ruff clean. No pipeline is
running.

## PICK UP HERE: P11 asset-local Gates 1 and 2

Rerun the P10 cohort through Gates 1 and 2 only. Preserve the 2026-02-28
cutoff, exact 25 assets, existing journal, one resumable pipeline JSON, and
one-command-at-a-time execution. META and GROUP remain excluded. Do not run
Gate 3.

Before code, inventory the current seams and write an ADR. Show it to the owner
and wait for approval. The ADR must specify:

- Replace pooled fitting with a standalone model trained only on each asset.
- Test `h=1,2,3,5,10,20,30,60` in order for each asset. Stop at its first
  Gate-1 failure. The last consecutive pass is selected; later horizons are
  neither run nor registered.
- Replace P10's 200-cell correction. Prefer untouched confirmation data: Gate 2
  tests only each asset's selected horizon, then enters that p-value into a
  dependence-aware correction ledger that remains valid as assets arrive over
  time.
- If independent confirmation is infeasible, every null replicate must replay
  the full ordered stopping and selection procedure. Correcting only observed
  survivors on reused data is invalid.

After approval, implement the revised resumable pipeline, run a memory
preflight and focused tests, then run Gates 1 and 2 to completion. Journal every
stage and stop before Gate 3. Report every Gate-1 stop, Gate-2 decision, ledger
entry, and failure without fallback.

## Reference

P10 result:
`pipeline_runs/p10-25-asset-modelability-staged-2026-02-28-b7c8efe9`

P10 memo:
`children/intraday_equities/docs/memos/p10-modelability-pipeline.md`

P10 used pooled 25-asset fits and a study-wide 200-cell max-statistic
correction. Gate 2 retained QQQ at three minutes and NFLX at ten; both later
failed Gate 3's frozen null-spread calibration. P11 changes the estimand and
must not overwrite or reinterpret those artifacts.
