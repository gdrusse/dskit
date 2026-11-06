# RE-ENTRY - index_options

Updated: 2026-11-05 by Claude Opus 5.5. Full-span rerun: forecasts and scores done through 2023; MIO code reviewed; plain WSL MIO running. Full log: `/home/russell/data/index_options/full-span-rerun-20261105/PLAN.md`.

## Now

- **Model.** Zoo winner `fixed_loc`; recipe `b10d67deebb5`: lr 1e-4, dropout 0.11, weight decay 0.045, hidden 64, width 8, 6 epochs, seeds 11/29/47/53/71, linear pool (`quarterly/recipe.json`). 166 names, list hash 8eae0a07.
- **Forecasts.** 17 quarters (2019Q4, 2020Q1-2023Q4) done, scored and pinned (ADR-0298, merged). `check` passes. 2020Q1 rerun under load gave identical hashes. Scores unopened; `table` not run. Path A0008 now holds this recipe and supersedes M53. No 2024+ work (owner).
- **MIO code.** Merged to main (ADR-0297, A0010). Both final lenses clean on cae384c1 (zero Major or Critical), including all 108 parity state combinations with restarts; tests pass. Never run on the Mac or on real files through the queue.
- **WSL MIO.** `mio-wsl/jobs.json`, branch `claude/mio-wsl-run-20261105` c9eef46b: 4 formulations (w1_010_shock10_lp, w1_003_shock10_lp, w1_003_shock10_mad050_lp, nominal_lp). 2019Q4 and 2020Q1 run to their finish; their scheduler was stopped on purpose, so its receipts stay `running`: judge by `result.json`. 2021Q1 and 2021Q2 never started and go to the queue instead. Peak memory 7.2 GB against an 8 GB placeholder.
- **Results location.** `run_root` is on `/mnt/d/full-span-rerun-20261105/mio-wsl/`. Copy closed results into WSL.

## Next

Owner order, 2026-11-05:

1. Confirm 2019Q4 and 2020Q1 closed (`mio-wsl/registries`, `result.json` under their `run_root`). Copy closed results from D: into WSL. Keep `/home/russell/wt/mio-wsl-run-20261105` until then. A document has no resume found: do not kill one mid-run.
2. **Get more space on C first.** With no jobs running, `wsl --shutdown` and move or compact the WSL disk onto D: (owner approved; verified backup first). The WSL disk file grows on C:, which has 11 GB free.
3. **Solve the remaining jobs on WSL and the Mac:** rebuild the Mac RAM disk (`fullspan-ram` is still attached), short worker check on a 1-2 week packet, then one real parity unit (RUNBOOK). Run 2021Q1 and 2021Q2 through the queue with all 9 formulations. For 2019Q4 and 2020Q1, the remaining 5 need an import (new code, review) or a queue re-solve of the 4 done.
4. Owner reviews the 4 selection quarters, picks 3; run those on 2022-2023 unopened (A0010).
5. Later: `quarterly_forecasts table`; 2024-2025 forecasts when the owner lifts the hold.

## Blocked / owner decisions

- Plain WSL results cannot enter the queue (code pins and the 9-method check); decide import or re-solve, or compare outside the tool.
- ADR-0296 (`auto` device, branch `claude/device-auto-20261105`, uncommitted): owner review and nine questions.
- New-file manifest for ADR-0297 to confirm. WSL disk move to D: approved, after a backup; C: has 11 GB free, which limits queue jobs.

## Read first

- `docs/plans/mio-parallel/RUNBOOK.md`; `docs/plans/quarterly-forecasts-runbook.md` (on the forecast branch).
- [Locked Path](decisioning/path.csv), A0010.
- `/home/russell/data/index_options/full-span-rerun-20261105/mio-review/formulation-review.md`.

## Do not

- Do anything with 2024-2025 data; run MIO on 2024+; view final forecasts; open 2022-2023 MIO economics before the 3 picks.
- Read or grep MIO run logs: one agent's grep of the 2022Q1 rules run.log returned five `weekly_decision` lines (values not seen by the main session).
- Put anything with 2024+ outcomes on D:, or anything on the Mac SSD.
- Waive the compute gate; exceed the WSL 6 GB and Windows 2 GB reserves; let C: fall under 3 GB.
- Trust the commit message of c9eef46b on reuse by file pin: it is wrong (see Blocked).
- Merge the forecast branch into the rerun tree. Journal rows on the branches collide at merge: renumber one side.
