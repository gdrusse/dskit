# Re-entry

Current wrap: 2026-09-04. `main` is synchronized with `origin/main`. PR #7
(the pmquant child) is merged; the staged-study ADR that had also taken the
number 0075 is now ADR-0081. Last verification: see the end of this file. No
pipeline is running.

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

## Landed this wrap: pmquant child (PR #7)

- `children/pmquant/` — prediction-market ladders (Kalshi, Polymarket) as
  thin tier-3 kinds + JSON over dskit seams. `configs/run-e2e.json` is the
  proof document (22 nodes; `tests/test_e2e.py` runs it on the synthetic
  world). `run-kalshi-ladders.json` is its real-data twin.
- dskit generic, ADR-0075…0080: onboarding packs `kalshi`, `polymarket`,
  `predexon` + `leads.py`; the `localtables` connector; the `observations`
  pipeline kind; the public clause DSL; `acquired_at` is the commit instant;
  one backoff ceiling (`connector.MAX_BACKOFF_S`); Polymarket `closedTime`.
- **Waiting on the owner:** `PREDEXON_API_KEY` in the environment before
  `configs/source-predexon.json` can pull; the twin's real-data run on a
  machine holding `~/pmquant_data`; the rulings listed in `TODO.md` under
  "Found by the pmquant child build".
- Also merged: `chore/quote-pull-budget` — the Alpaca quotes backfill
  `budget_seconds` 3000 → 570, so an interrupted pull loses under ten minutes.
- `fix/hstar-min-split-gain` is the pre-rewrite lineage (no common ancestor
  with `main`); every file it carries is already in `main`. Safe to delete.

## Reference

P10 result:
`pipeline_runs/p10-25-asset-modelability-staged-2026-02-28-b7c8efe9`

P10 memo:
`children/intraday_equities/docs/memos/p10-modelability-pipeline.md`

P10 used pooled 25-asset fits and a study-wide 200-cell max-statistic
correction. Gate 2 retained QQQ at three minutes and NFLX at ten; both later
failed Gate 3's frozen null-spread calibration. P11 changes the estimand and
must not overwrite or reinterpret those artifacts.
