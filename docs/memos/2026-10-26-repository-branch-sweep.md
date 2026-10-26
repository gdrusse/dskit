# Repository merge and branch sweep - 2026-10-26

Date: 2026-10-26. Author: GPT-6. Repository: `gdrusse/dskit`.

## TL;DR

The completed Fable work is on main. The local main checkout was brought forward
166 commits to published main `8e14281c5fcc9f34aab6734fcbc79bc53b5b24af`;
37 contained local branches and two contained remote branches were deleted.
Twelve unmerged local branches, two unmerged detached histories, and 20 dirty
checkouts were preserved; these categories overlap. All 88 worktree directories
and their study artifacts remain.

## Execution contract

The owner asked for main to be current, reviewed work merged, and merged branches
purged. Scope was repository maintenance in WSL Ubuntu-24.04. A branch was eligible
for deletion only when its exact head was an ancestor of published main and its
checkout was clean and had no detected process using it. Each remote deletion
used an exact expected-head lease. Worktrees were detached at the same commit
before their local branch names were removed.

A new merge requires the current whole-slice review closure described in
[implementation workflow](../skills/implementation-workflow.md) and
[skeptic review](../skills/skeptic-review.md). A commit saying "review fixes" is
not that closure. Dirty files, current owner Path decisions, active workloads,
and unmerged history were preserved. No runtime code, strategy, data window,
solver configuration or study output was changed. No market data was read and no
backtest, fit, solver run or full test suite was launched.

## Completed operations and limits

- Published main already contained the Fable branch at `1fd8d7ac` and the scenario
  stress work at `64f95755`. Their integrated result is `8e14281c`; the two clean
  integration reviews and focused checks are recorded in the
  [publication addendum](../../children/index_options/docs/memos/2026-10-26-shock-rows-2020-test.md).
- Local main was clean at `1357e9a6c708ce2bbc59af4dcdb214efdc026459` and was
  fast-forwarded to `8e14281c`. The installed merge hooks ran; it remained clean.
- Local branches fell from 64 to 27: main, 12 unmerged branches, and 14 branches
  already contained in main whose dirty checkouts were deliberately preserved.
- Remote branches fell from four to two: main and
  `codex/index-options-iterative-validation`. The latter remains at
  `80f0e4eef1110657716e7302423d985c1f2c8a18`; its local branch has two newer commits
  and uncommitted files. Its saved "review fixes" checkpoint is not evidence of
  final approval for the later local head.
- No additional runtime branch was merged during this sweep. Inspection covered
  unique commits, changed files, added review/ADR text, current main changes,
  relevant saved memos and available session evidence. No additional candidate
  with a verified current two-lens approval was established. This is not a claim
  that every historical review transcript has been authenticated.
- Two distinct process groups were using crypto checkouts during the inventory;
  both locations were already dirty and were left untouched. Process guards
  checked working directories and explicit checkout paths in command arguments.

## Deleted remote branches

- `claude/index-options-mio-fable-20261026` at
  `1fd8d7aceeaf789e76653c6b11970b513e68b63b`.
- `claude/strike-fast-scan-20261018` at
  `031065694782f7644d23f466c6068eb7814473f6`.

Both deletes succeeded in one atomic push with separate expected-head leases.
Their commits remain reachable from main and their local worktree files remain.

## Deleted local branches

Each entry gives the removed branch name and its retained commit. The complete
before-state receipt contains full hashes and checkout paths.
- `claude/condor-production-realism-20261009` - `c1989321bdf4`.
- `claude/crypto-minute-features-20261009` - `68ae207786d1`.
- `claude/crypto-select-20261009` - `e147c56dca9e`.
- `claude/dskit-row-stride-20261010` - `a0eddfdfe2ea`.
- `claude/dskit-zoo-slice-20261009` - `30fd0be729e2`.
- `claude/dskit-zs-array-panel` - `549ca6e067f2`.
- `claude/dskit-zs-cdf-public-seams` - `abf11effde25`.
- `claude/dskit-zs-e2e-docs` - `3d97fa8a4e2e`.
- `claude/dskit-zs-horizon-cdf` - `ef8f3c6e9034`.
- `claude/dskit-zs-panel-score` - `a1a2c401565a`.
- `claude/dskit-zs-panel-torch` - `cf14c964b805`.
- `claude/dskit-zs-torch-ts-encoders` - `eec53ffbea19`.
- `claude/httpblobs-dates-20261012` - `b5df79fe295f`.
- `claude/index-options-mio-fable-20261026` - `1fd8d7aceeaf`.
- `claude/index-options-t2-refusal-20261010` - `bd7c6612d356`.
- `claude/kalshi-trades-bound-20261012` - `ade21c64265e`.
- `claude/option-universe-300` - `f9f2f9050cfb`.
- `claude/sonnet-5-5-session` - `26236ab8c405`.
- `claude/stock-lane` - `fbb111c92d14`.
- `claude/strike-fast-scan-20261018` - `031065694782`.
- `claude/theta-census-features-20261012` - `a3e642ed33ca`.
- `claude/theta-hypothesis-docs-20261013` - `89bbb1d04f7c`.
- `claude/theta-ledger-sort-20261012` - `f09ac492eaac`.
- `claude/theta-parquet-export-20261012` - `465e94f303b5`.
- `claude/theta-parquet-readers-20261012` - `a998c4f19816`.
- `codex/cdf-feature-forward-selection` - `6bd273194776`.
- `codex/cdf-risk-neutral-suite` - `0364cc060da9`.
- `codex/cdf-tail-calibration` - `b140720d6eef`.
- `codex/cdf-tail-data` - `b9a6920f88a2`.
- `codex/condor-build-projection-20261008` - `43eabca54bde`.
- `codex/condor-robustification` - `71e0511d7b12`.
- `codex/condor-terra-calibration-20261008` - `9c281b56c0a3`.
- `codex/stat-arb-prediction-20261020` - `32550b6867fb`.
- `codex/strike-tuning-20261022` - `d64ff3029444`.
- `codex/theta-market-training-20261016` - `834f2c47e195`.
- `crypto-killtest-20261007` - `f56a6f3c02d4`.
- `run-steps` - `a58ff850629b`.

## Preserved unmerged branches and detached histories

These are recovery and review candidates, not approved additions to main. Their
recorded heads remain available. At least one path in every candidate has also
changed on main since divergence, so reconciliation must consider the current
owner and contracts rather than blindly replaying the old patch.

- `claude/crypto-project-orientation-20261009` at `c22a2c2edcf91b5ed5d297320be76c68bdcdab7d`: Not merged: current final independent correctness and integration approval was not verified; preserve unique history.
- `claude/crypto-windows-20261009` at `29e4ba64995b017647091fda61c0bfeda6925a7e`: Not merged: historical owner Path and window decisions overlap changed main documents; no current reconciliation/review closure verified.
- `claude/crypto-zoo-20261009` at `846448bfabfdd03b2a6df92a2b0d573cf1b26d2c`: Not merged: branch explicitly labels TapeRows an unapproved interim holding and SeriesRows proposal awaiting approval; no current final review closure verified.
- `claude/theta-event-detector-20261012` at `d59d7687c4a74919047d0c4364aeb589838cff35`: Not merged: current final independent correctness and integration approval was not verified; preserve unique history.
- `claude/torch-ts-quantile-head` at `c804e9cbecf11c15cd8e4b885dd315bedfad13be`: Not merged: current final independent correctness and integration approval was not verified; preserve unique history.
- `codex/condor-build-dual-20261008` at `dbe580b24d4274a267b21c30c9ce598c7b8b5538`: Not merged: current final independent correctness and integration approval was not verified; preserve unique history.
- `codex/condor-build-integration-20261008` at `818a25c5dc7389f63978d9d09dba602cfb3fa78c`: Not merged: current final independent correctness and integration approval was not verified; preserve unique history.
- `codex/condor-rho-final-20261008` at `4b93bd4dd8cf533e90da931f0b809eb1d678edea`: Not merged: current final independent correctness and integration approval was not verified; preserve unique history.
- `codex/condor-terra-inference-20261008` at `27178ad1d9667cb0677b540358a14c16e516d5be`: Not merged: current final independent correctness and integration approval was not verified; preserve unique history.
- `codex/condor-terra-intake-20261008` at `9f954d308523b0b25572210b3a5b9337b983bbe8`: Not merged: current final independent correctness and integration approval was not verified; preserve unique history.
- `codex/index-options-iterative-validation` at `bd697abb8c64afa3cf98685a04b5628c32735817`: Not merged: dirty checkout; two local fixes follow the remote review-fix checkpoint. Final whole-slice approval at the current head was not verified.
- `codex/stat-arb-zoo-20261021` at `f0f9da18aa44c535f0b056f37de2de4472af3848`: Not merged: ADR-0276 diagnostics and scoring corrections are saved, but the memo and inspected review records do not establish two final clean independent reviews at f0f9da18.
- `/home/russell/wt/crypto-minutefeatures-columns-20261009` at `791317265edacc98f90add4b9cf0ca77abc45f13`: Not merged: current final independent correctness and integration approval was not verified; preserve unique history.
- `/home/russell/wt/index-options-recorder` at `9013eb10bcd786decd875a6c8d967dc08f745c52`: Not merged: dirty detached checkout with three unique timestamp-zone commits; final current review closure not verified.

## Preserved dirty checkouts

There were 20 dirty worktrees: 15 attached to branches and five detached. Fourteen
of the attached branch heads are already contained in main; the remaining one
is iterative-validation. The recorder is both dirty and an unmerged detached
history. These counts are deliberately not added together as distinct work.

- `/home/russell/dskit-crypto-prew4-oos` - codex/crypto-prew4-oos, 62 status entries. Active processes detected; left running.
- `/home/russell/wt/correlation-arb-20261019` - detached, 2 status entries.
- `/home/russell/wt/correlation-arb-options-json` - correlation-arb-options-json, 1 status entries.
- `/home/russell/wt/crypto-completion-20261008` - crypto-completion-20261008, 2 status entries.
- `/home/russell/wt/crypto-hpo-20261010` - claude/crypto-hpo-20261010, 2 status entries.
- `/home/russell/wt/crypto-hpo-wide-20261011` - claude/crypto-hpo-wide-20261011, 2 status entries.
- `/home/russell/wt/crypto-pull` - detached, 2 status entries. Active processes detected; left running.
- `/home/russell/wt/crypto-w4-leads-20261012` - claude/crypto-w4-leads-20261012, 2 status entries.
- `/home/russell/wt/crypto-w4-take-20261011` - claude/crypto-w4-take-20261011, 2 status entries.
- `/home/russell/wt/crypto-zoo-wiring-20261009` - claude/crypto-zoo-wiring-20261009, 2 status entries.
- `/home/russell/wt/dskit-speed-20261010` - claude/dskit-train-speed-20261010, 2 status entries.
- `/home/russell/wt/family-availability` - claude/family-availability, 10 status entries.
- `/home/russell/wt/fill-label-20261011` - claude/fill-label-20261011, 1 status entries.
- `/home/russell/wt/index-options-iterative-validation` - codex/index-options-iterative-validation, 4 status entries.
- `/home/russell/wt/index-options-recorder` - detached, 2 status entries.
- `/home/russell/wt/intraday-robust-joint` - codex/intraday-robust-joint, 2 status entries.
- `/home/russell/wt/joint-run-20260929` - detached, 2 status entries.
- `/home/russell/wt/kalshi-json-reader-20261019` - kalshi-json-reader-20261019, 1 status entries.
- `/home/russell/wt/model-zoo-417` - claude/model-zoo-417, 3 status entries.
- `/home/russell/wt/training-validation-audit` - detached, 3 status entries.

## Verification

A second complete worktree inventory confirmed 88 directories, 20 dirty
checkouts, and unchanged commit heads and porcelain status records in every
checkout except the intentionally updated local main. This check compares Git
status metadata; it is not a checksum of every ignored artifact. All 37 detached
checkouts kept their original commit and remained clean. Remote refs were read
back after deletion and matched the two expected surviving branches.

Documentation checks: `python -B -m pytest -q tests/docs/test_budgets.py`
passed all four checks in 0.12 seconds. The reported counts were checked against
the saved receipts; changed relative links resolve and `git diff --check` passes.
This change touches only this memo, the core re-entry note and its archive; all
runtime, test, configuration and contract files match `8e14281c`. It is an
editorial receipt under the existing review policy, with no fresh code review or
full-suite run claimed. The command and output are saved in `docs-checks.json`.

## Reproducibility and handoff

The WSL receipt directory is
`/home/russell/.local/state/dskit-branch-sweep-20261026/`:

- `before.json`: all refs, hashes, worktrees, dirty entries and detected processes.
- `main-update.json`: the fast-forward command outcome and hook output.
- `operations.jsonl`: exact detach/delete operations, heads and return codes.
- `cleanup-summary.json`: deleted names and preserved contained branches.
- `unmerged-evidence.json`: unique commits, paths, added review text and decisions.
- `after-cleanup.json`: verified refs and every worktree's resulting status.

These local receipts preserve the deleted names and full commit hashes. Any
removed branch can be recreated from its recorded commit, which remains on main.
No worktree directory was removed and no unmerged history was pruned.

Next: reconcile a retained candidate in an isolated checkout and obtain the
required current reviews before merging it. Dirty work needs its owning session's
bounded closeout. The detached recorder also needs its timestamp-zone changes
reconciled and reviewed. Existing statistical-arbitrage results on an unmerged
branch are saved evidence, not a newly accepted model or new test authorization.
The index-options study memos, Fable session artifacts and sealed test windows
remain in their existing locations.
