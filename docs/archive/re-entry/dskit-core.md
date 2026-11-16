## 2026-11-15 — before host-storage closeout

## Core (dskit itself)

**Now.** 2026-11-15: reusable device selection (ADR-0296, candidate
`94e785ac`) and saved-fit forecast faults (ADR-0317, `c38e2b28`)
passed two fresh independent final lenses each, with zero Critical/Major.
Explicit-device defaults and frozen studies are unchanged. The stress replay
and fitted-method refusal fix are also integrated; PR #19 is merged.

**Next.** Reconcile remaining reusable candidates individually from the
review-eligibility manifests. Keep canonical data outside new worktrees and
check the storage guard before bulk work. Maintenance receipts distinguish
retired code copies from retained results, dirty work and runtime pins.
The shared .venv retains an editable import path to the preserved
`/home/russell/wt/claude-sonnet-5-5` checkout; verify import locations before
using new main code and do not repoint an environment underneath running jobs.

**Blocked / owner decisions.** Unreviewed experiments retain their gates.
ADR-0284's last recorded Mac-only dispatch review has four path-mapping
Majors; this maintenance did not clear it. Windows C: remains low even after
WSL files were removed; compaction needs a planned interruption.

**Read first.** ADR-0296/0317 in `docs/architecture/decision-log.md`;
`/home/russell/storage-investigation/2026-11-15/worktree-closeout/maintenance-summary.json`;
`/home/russell/storage-investigation/storage-policy.json`.

**Do not.** Rewrite frozen source pins to accept changed code, delete protected
results or unique unfinished work, or treat development review as real-run or
deployment approval. ADR-0284 still needs fresh clean reviews. Earlier notes:
`docs/archive/re-entry/dskit-core.md`.

## Archived 2026-11-15 before maintenance wrap

## Core (dskit itself)

**Now.** 2026-10-29: `tools/session/machine.sh` names the host at session start
(Claude `SessionStart` hook on main, `c1dc38ee`; Codex reads it through a
user-level `~/.codex/hooks.json`, once trusted in `/hooks`). ADR-0284, two-host
fold dispatch over SSH with a path map below identity, was approved and built on
branch `docs/adr-two-host-runner` (head `4e2a338e`, base `db39f872`, Mac clone
only), then parked unmerged: review round 2 left 4 Majors, all path mapping. Only
the ADR is on main. Compute stays on the PC, driven from the Mac by `ssh desktop`.
Mac-only base reds, not regressions: 9 `test_folds.py` and 2 intraday cap tests
(macOS refuses `RLIMIT_AS`), production 133 failed + 23 errors
(`RuntimeFingerprint` libc), no `hypothesis`. `tools/sweep/sweep` crashes when
its git grep times out.

**Next.** PC/WSL clones: pull for the machine hook. Retained work from the
2026-10-26 branch sweep still reconciles one candidate at a time.

**Blocked / owner decisions.** ADR-0284: one redesigned round (inventory every
recorded-path reader and writer first; drop it if review still finds a Major)
or leave it parked.

**Read first.** ADR-0284's "Review checkpoint" in the decision log;
[Branch sweep receipt](memos/2026-10-26-repository-branch-sweep.md).

**Do not.** Merge the ADR-0284 branch without a fresh two-lens review at zero
Critical/Major (rebase it first; main's ADR-0284 text wins). Delete unmerged or
dirty work. Run capped folds on a macOS host. History:
`docs/archive/re-entry/dskit-core.md`.


# RE-ENTRY archive — dskit-core

Previous re-entry notes for this lane, newest first. `/wrap` moves the note it
replaces to the top of this file; nothing is ever edited or removed here. Not
read at session start. Older history: `2026-10-10-consolidated-history.md`.

---

# Core note - 2026-10-26 (replaced 2026-10-29 by the machine hook and ADR-0284 parking)

## Core (dskit itself)

**Now.** 2026-10-26: local main caught up 166 commits to published `8e14281c`.
The completed Fable and scenario work is included. Removed 37 contained local
branches and two contained remote branches; all 88 worktree directories remain.
The 12 unmerged branches, two unmerged detached histories and 20 dirty checkouts
are preserved (overlapping counts). No additional candidate with verified current
two-lens approval was established. No runtime or study changes in this sweep.

**Next.** Reconcile retained work one bounded candidate at a time, then obtain
current required reviews before merging. Dirty checkouts need their owner's
closeout; do not reset or discard them. Prior ADR-0268 performance follow-ups
and known baseline test failures remain historical open work, not retested here.

**Blocked / owner decisions.** No additional merge-ready candidate established;
see the exact retained heads and review gaps in the sweep memo.

**Read first.** [Branch sweep receipt](memos/2026-10-26-repository-branch-sweep.md).

**Do not.** Delete unmerged/dirty work or worktree artifacts; reopen protected
data windows or run studies as part of cleanup. Historical core state is in
`docs/archive/re-entry/dskit-core.md` when needed.

---

# Core note - 2026-10-16 (replaced 2026-10-26 by repository branch sweep)

## Core (dskit itself)

**Now.** 2026-10-16: the ADR-0268 equivalence-preserving performance sweep landed
(`main` fast-forwarded to `471dd655`, from `claude/perf-sweep-20261014`). 34 units:
26 landed, each through its own merge commit carrying the review record (frozen
candidate, two Sonnet skeptic lenses, zero unresolved Critical/Major); 5 dropped
(`pipeline-trust`, `pipeline-kinds`, `child-index-options-a/-b`, `libs-pyomo-rl-nlp`);
3 no change (`production-execution`, `-guards`, `-cli-bundles`). Merged vs main:
core tests and all 8 child suites fail exactly main's known reds and pass 516 more
(the new pins). Record and per-unit outcomes: ADR-0268 in the decision log.
Behaviour change to know: the faster sweep scorer can finish a big commit's
commit-msg check that used to hit the hook's 12 s cap and pass unchecked.
Known reds on main, untouched, each its own bounded task:
`test_purity.py::test_core_never_reaches_outside_the_toolkit_at_any_depth`
(`libs/parquet.py` imports `dskit.onboarding.snapshot`) and, on Python 3.13,
`test_uncertainty_set.py::...::test_replace_without_the_arrays_cannot_recover_them`.

**Next.** Owner, from a machine with push rights: delete the contained remote
branches listed in the archived 2026-10-11 note, plus `claude/perf-sweep-20261014`
if still present. Optional follow-ups, each its own bounded task: the deferred
pins named in the ADR-0268 merge commits; unproven saved patches (open-order index
for `PaperExecutor._march`, money-check fast path) if long paper replays matter.

**Blocked.** Nothing.

---

# Core note — 2026-10-11 (replaced 2026-10-16 by the ADR-0268 performance sweep wrap)

**Now.** 2026-10-10 cleanup: `docs/RE-ENTRY.md` was 469,597 lines (4,431 unique)
after keep-both merges doubled it at every merge; it is now this index plus one
note per lane, history in `docs/archive/re-entry/`. Root `CLAUDE.md`/`AGENTS.md`
shrank to the rules; the standards moved to `docs/standards/`. The branch
`claude/index-options-perf-pilots` is merged. ADR-0256/0257 are on main;
ADR-0258 is built in the index_options lane. Open pack minors: `TODO.md`.
Known reds on main, untouched here, each its own bounded task:
`test_purity.py::test_core_never_reaches_outside_the_toolkit_at_any_depth`
(`libs/parquet.py` imports `dskit.onboarding.snapshot`) and, on Python 3.13,
`test_uncertainty_set.py::...::test_replace_without_the_arrays_cannot_recover_them`
(`dataclasses.replace` now requires the `weights` InitVar).

**Next.** Owner, from a machine with push rights (the cloud proxy refuses ref
deletion): delete the contained remote branches `claude/correlation-arb-research`,
`claude/crypto-trading-child`, `claude/ct-kalshi-history`, `claude/ct-parquet-io`,
`claude/ct-restwindow`, `claude/ct-zipcsv`, `claude/index-options-perf-pilots`,
`claude/repo-cleanup-20261010` (this cleanup, fast-forwarded into main),
`claude/index-options-dispersion-ic` (ADR-0259, fast-forwarded into main);
tag `codex/index-options-iterative-validation`'s tip `80f0e4ee` as
`archive/codex-index-options-iterative-validation`, push the tag, then delete
that branch (superseded; ADR-0215's `CDFEstimatorModel` lives only there).

**Blocked.** Nothing.

---

# Active handoff — 2026-10-06: behavior-identical speedups (merged)

**Merged to main as `2ef373d9` (PR #18, fast-forward from `f9f2f905`).** Pure speedups across
index_options (quote/debit backtests: sorted-quotable-strike bisect, per-entry `_expected`
memo, float fast paths, sliced American charges; cdf_study: groupby-indices instead of
per-row filters, linear settled-event history, positional term slopes, files hashed once;
grid: no redundant deepcopies) and dskit (`number_ok` fast path, precompiled ref regexes,
one document hash per run, twCRPS counter walk, batched LightGBM scale/curve predictions,
numpy-pack column reads, `parse_utc` cache, restamp splice, O(1) duplicate-fill check,
metadata read once). Every rewrite keeps the HEAD path as a fallback for subclasses,
non-float/non-ns inputs and errors.

**Evidence.** Full suite (`tests` + index_options) on base and candidate: every baseline
test has the same outcome (10 pre-existing failures, environment-only); 30 new pinning
tests compare fast paths to the original code. Ruff counts unchanged. Measured: PayoffSelect
walk 4.8x, LightGBM rung 20k rows 53s -> 0.6s, decision-weighted curves 32x, candidate
lookup per row 78ms -> 0.07ms, term slopes ~3x, `_read_inventory` 1.6x.

**Review.** Three independent skeptic passes (correctness, tests/integration, closing
correctness): all Critical/Major fixed (subclass `predict_scale` bypass; float32 inputs;
out-of-range macro dates). Open Minors: index_options fast paths restate `leg_intrinsic`
and `number_ok` for floats (marked "same rule as"); decision-region panel lookups and
`_verified_partition` digest reuse have no randomized old-vs-new pin; batched LightGBM
raises ValueError (not LightGBMError) on ragged rows, unreachable.

**Next.** Remote branches `perf/speedups-2026-10-05`, `claude/zealous-goodall-kdllva` and
`run-steps` are fully in main but still exist: this session's proxy refused the deletes.
Four other branches hold unmerged commits and were kept. `torch.py` untouched (byte-pinned).

