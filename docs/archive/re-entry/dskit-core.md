# RE-ENTRY archive — dskit-core

Previous re-entry notes for this lane, newest first. `/wrap` moves the note it
replaces to the top of this file; nothing is ever edited or removed here. Not
read at session start. Older history: `2026-10-10-consolidated-history.md`.

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

