# Quarterly forecasts: runbook (ADR-0298, proposed)

Launch order for `index_options.panel_extension` and `index_options.quarterly_forecasts`. Run from `children/index_options` with `PYTHONPATH=<repo>:<repo>/children/index_options`. Every job goes through `compute-run`; never waive the gate. No step shows a forecast, score or 2024+ value: do not open those files.

## Current scope (owner, 2026-11-05 ~19:30)

Nothing with 2024-2025 data for now: skip steps 1-3 and 8, leave `panel_sealed` and `panel_verify` null. `check` and `pin` then require only the 17 open quarters. The WSL disk move is needed only for the skipped panel extension.

## Before anything

- ADR-0298 approved and the skeptic pass at zero Critical and Major.
- HPO selected; the owner's A0008 decision on seeds and average made; `quarterly/recipe.json` written.
- Do not merge or check out this branch in the rerun tree (`/home/russell/wt/full-span-rerun-20261105`) until HPO, zoo and scoring there are done. `_relocated_pins` hashes this tree's dskit files into every fit document, so the rerun tree's frozen documents would refuse. Run this branch from its own worktree only.
- The worktree is committed and clean before the first real run. A real run refuses a dirty tree (receipts and `verify.json` name the commit); a synthetic run is exempt. The journal's `actions.csv` and generated `README.md` do not count as a change.
- WSL disk moved to D:. `plan` refuses while `c_vhdx` is still on C: and the projected writes pass the 3 GB floor.
- `cache_guard.sh` running: its current run expires around 22:08 on 2026-11-05, so restart it. Each sealed quarter hashes the whole panel twice and reads it several times; without the guard the page cache grows.
- No `HALT` file.

## Journal rows land in this worktree

Real score jobs, the panel extension stages and `pin` each append rows to this worktree's `children/index_options/docs/decisioning/actions.csv` (and its generated README). That is on purpose: no config option redirects them. At merge these rows collide with the main line's ids: renumber them by hand (AGENTS.md: a refused ledger merge is an id collision; renumber one side). Commit the worktree before the first real run (above); the later journal rows then leave the tree "clean" by the rule above. Synthetic tests record nothing (pytest).

## Order

1. `python -m index_options.panel_extension plan configs/quarterly-panel-extension.json`
2. `compute-run run <work_root>/jobs-panel.json` (tables, panel, verify). A killed stage resumes. `verify.json` must say `passed: true`; a missing name or a changed through-2023 row stops here.
3. In `configs/quarterly-forecasts-run.json` set `panel_sealed` to the panel in `<work_root>/final.json` and `panel_verify` to `<work_root>/verify.json`, then **commit that file before step 4** (`git add children/index_options/configs/quarterly-forecasts-run.json && git commit`, model identity per the repo CLAUDE.md). The file is tracked, so the edit dirties the tree, and every real job refuses a dirty tree (`CodeTree`); the plan's `run.json` is a copy of the committed file.
4. `python -m index_options.quarterly_forecasts plan configs/quarterly-forecasts-run.json`. Refuses without a passed verify of these exact panels.
5. Parity check, below: mandatory, after the HPO and before any real quarter.
6. `compute-run run <plan_root>/jobs-open.json`: forecasts 2019Q4 and 2020Q1-2023Q4 (17), then each quarter's score job (17, one scorer at a time).
7. Rerun check, below, while lanes are running.
8. `compute-run run <plan_root>/jobs-sealed.json`: forecasts only, 2024Q1-2025-11-28 (8).
9. `python -m index_options.quarterly_forecasts check RUN.json`: exit 3 means a structural failure or a missing quarter of the 25; counts, dates and hashes only.
10. `python -m index_options.quarterly_forecasts table RUN.json`: twCRPS and CRPS gains. The A0005 gate and every level read 2020-2023 only; 2019Q4 is the separate `tuned_year` level.
11. `python -m index_options.quarterly_forecasts pin RUN.json --onboarding-root <root> --source <name>`, once, after all 17 open quarters (it refuses otherwise): writes the 13-point bindings and, beside them, the `-fine` ones. A rerun of this verb resumes.

Planned jobs: 42 (25 forecasts, 17 scores), down from 48 (2019Q1-Q3 dropped, owner 2026-11-05 ~17:55); the panel adds 3. The earlier agent estimate for the 48, 2 to 5 hours (not measured on these jobs), is now an upper bound. Zoo `fl21` scored 5 folds in 37 minutes; zoo fits took 4-12 s for one seed and epoch, so 5 seeds and 10 epochs is at most about 10 minutes a fit. Refine from `<plan_root>/receipts`.

## Folds

2020Q1-2025Q4 are folds 2-25, unchanged. 2019Q4 is fold 29 (MIO's `bridge.fold` must be 1 or more and only fold 1 is free below 2); folds 26-28 are unused. Fold order is not date order; nothing here compares folds.

The HPO tuned on 2019, so 2019Q4 is favored, not independent evidence: its receipt says so in `tuning_disclosure`.

## Parity check, after the HPO (mandatory)

The selection evidence (zoo `fl21`) ran the old `predictive_cdf.py`; this branch changes it (the fit now records `parameters_moved`). The change should not move a weight, and this step proves it on one fit before 25 are trusted. Same settings, same training window (2017-01-03 through 2019-12-31), on the GPU.

1. Write `/home/russell/data/index_options/full-span-rerun-20261105/quarterly/parity-recipe.json` (beside `recipe.json`, outside the repo: a file inside the worktree would be an untracked change and every real job would refuse) with the zoo `fl21` settings: kind `fixed_loc`, learning rate 0.0003, dropout 0.3, weight decay 0.3, hidden 64, width 16, epochs 1, seeds [11], average `linear_pool` (from `zoo/docs/fit-fl21.json`).
2. `python -m index_options.quarterly_forecasts rerun-plan RUN.json --recipe /home/russell/data/index_options/full-span-rerun-20261105/quarterly/parity-recipe.json` (the verb stores the path resolved, so a relative path also works) plans 2020Q1 under `<plan_root>/../parity-check` (own roots and `run_id`, no score job; refuses if the folder exists). Run its printed jobs file through `compute-run`.
3. Compare only hashes, with `Q` the parent of `plan_root` and `Z` the zoo's `fits/fl21/pooled/context_chain/fixed_loc_market_only_mlp64/2/pooled/fit`:

```
sha256sum $Z/model/weights.pt $Q/parity-check/open/fits/*/2020Q1/pooled/context_chain/fixed_loc_market_only_mlp64/2/pooled/fit/model/weights.pt
```

Equal: the old selection evidence and the new code agree; `rm -rf $Q/parity-check` and go on. Different: stop, tell the owner, start no real quarter. Then, from hashes and file names only: confirm the panel hash, the training band in each `fit/metadata.json` and the source hashes match what the zoo recorded; run the same plan twice (the rerun check below) to see whether the GPU repeats itself. The owner then chooses: restore the old behavior in `predictive_cdf.py` (a few lines) and repeat this step, or approve the new code with the difference written into the ADR, which then states that the zoo ranking was run on other code. Do not pick for the owner.

## Rerun check, before final use

```
python -m index_options.quarterly_forecasts rerun-plan RUN.json
```

This plans 2020Q1 again under `<plan_root>/../rerun-check` with its own roots, its own `run_id` (so `compute-run` cannot adopt, refuse on or overwrite the main receipts) and no score job. It refuses if `rerun-check` exists. While another lane is fitting, run the one job it prints, then compare only hashes, with `Q` the parent of `plan_root`:

```
compute-run run $Q/rerun-check/plan/jobs-open.json
sha256sum $Q/open/forecasts-*/2020Q1/forecasts.parquet $Q/rerun-check/open/forecasts-*/2020Q1/forecasts.parquet
sha256sum $Q/open/forecasts-*/2020Q1/forecasts-fine.parquet $Q/rerun-check/open/forecasts-*/2020Q1/forecasts-fine.parquet
```

Equal hashes in each pair prove reproducibility under concurrent lanes. Different hashes: stop, tell the owner, use nothing. Then `rm -rf $Q/rerun-check`.

## When a quarter fails

A fit failure publishes nothing and adds a line to `<plan_root>/fit-failures/<quarter>.jsonl` (for a sealed quarter, the exception type and its words with every number removed); the job then retries or can be launched again. A `skipped` folder cannot come from these documents; if one exists, keep it and ask. A receipt's `owner_name_exceptions` lists any name the owner excused; there are none.

An interrupted scorer leaves its run folder under `<scores_root>/runs/<quarter>/`; the relaunch removes that plan's unfinished run folders (no `result.json` saying `ran`) and starts again. It refuses while a `dskit.pipeline run` of that quarter's score document is alive (stop it first). A run folder belongs to the current plan when its `config.json` scorer params match the current score document; an older plan's folders are left alone and never block. A finished run is never removed: if one of the current plan exists without its published output, the job refuses and the owner decides.
