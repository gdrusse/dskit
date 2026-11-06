# RE-ENTRY - index_options

Updated: 2026-11-05 by Claude Opus 5.5. Full-span rerun: forecasts and scores done through 2023; MIO code reviewed; plain WSL MIO running. Full log: `/home/russell/data/index_options/full-span-rerun-20261105/PLAN.md`.

## Now

- **Model.** Zoo winner `fixed_loc`; recipe `b10d67deebb5`: lr 1e-4, dropout 0.11, weight decay 0.045, hidden 64, width 8, 6 epochs, seeds 11/29/47/53/71, linear pool (`quarterly/recipe.json`). 166 names, list hash 8eae0a07.
- **Forecasts.** 17 quarters (2019Q4, 2020Q1-2023Q4) done, scored and pinned from `claude/quarterly-forecasts-20261105` (ADR-0298). `check` passes. 2020Q1 rerun under load gave identical hashes. Scores unopened; `table` not run. No 2024+ work (owner).
- **MIO code.** `claude/mio-parallel-on-rerun-20261105` at the wrap commit (ADR-0297, A0010). Review of this commit in progress; not cleared until both lenses are clean. Queue tests pass. Never run on the Mac or on real files through the queue.
- **WSL MIO running.** `mio-wsl/jobs.json`, branch `claude/mio-wsl-run-20261105` c9eef46b: 4 formulations (w1_010_shock10_lp, w1_003_shock10_lp, w1_003_shock10_mad050_lp, nominal_lp) on 2019Q4, 2020Q1, 2021Q1, 2021Q2. Two running at wrap; two wait on the memory gate. Peak so far 4.2 GB against an 8 GB placeholder. Earlier documents took 3-5 h; here `run_root` is on D:, so maybe longer.
- **Results location.** `run_root` is on `/mnt/d/full-span-rerun-20261105/mio-wsl/`. Copy closed results into WSL.

## Next

1. Owner rules on merging (below); keep `/home/russell/wt/mio-wsl-run-20261105` until solves close.
2. Mac: rebuild its RAM disk (`fullspan-ram` is still attached), run the short check on a 1-2 week packet, then one real parity unit (RUNBOOK). Queue route: 5 remaining formulations, or all 9 for a single path.
3. Owner reviews the 4 selection quarters, picks 3; run those on 2022-2023 unopened (A0010).
4. Later: `quarterly_forecasts table`; A0008 wording; 2024-2025 forecasts when the owner lifts the hold.

## Blocked / owner decisions

- **Merge to main.** All branches carry M53 lock commits 82383397 and a8873446, held unmerged by the owner's rerun ruling. A0008 still says epoch 1 while the recipe uses 6 epochs and 5 seeds. Nothing merged or pushed.
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
